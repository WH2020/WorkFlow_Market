from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_ROWS = 200_000
MAX_MESSAGE_CHARS = 20_000
MAX_SCOPE_CONVERSATIONS = 50
MAX_SCOPE_MESSAGES = 600
MAX_SCOPE_TEXT_BYTES = 750_000
ALLOWED_IMPORT_SUFFIXES = {".json", ".jsonl", ".csv"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class WechatStoreError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _root(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise WechatStoreError("INVALID_ROOT", "应用目录不存在")
    return root


def _wechat_root(project_root: Path | str) -> Path:
    root = _root(project_root)
    data = root / "data"
    if data.exists() and (data.is_symlink() or not data.is_dir()):
        raise WechatStoreError("UNSAFE_PATH", "应用数据目录必须是普通目录")
    candidate = data / "wechat"
    if candidate.exists() and not candidate.resolve().is_relative_to(root):
        raise WechatStoreError("UNSAFE_PATH", "微信资料目录越出应用范围")
    return candidate


def database_path(project_root: Path | str) -> Path:
    return _wechat_root(project_root) / "chat-index.sqlite3"


def _safe_text(value: Any, maximum: int, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = unicodedata.normalize("NFKC", str(value)).replace("\x00", "").strip()
    return text[:maximum]


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _first(row: Mapping[str, Any], names: Iterable[str], default: Any = "") -> Any:
    lowered = {str(key).casefold(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value is not None and str(value).strip() != "":
            return value
    return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "是", "我", "self"}


def _timestamp(value: Any) -> tuple[int, str]:
    if value is None or value == "":
        return 0, ""
    try:
        number = float(value)
        if number > 10_000_000_000_000:
            number /= 1_000_000
        elif number > 10_000_000_000:
            number /= 1_000
        moment = datetime.fromtimestamp(number, timezone.utc)
        if 2000 <= moment.year <= 2100:
            return int(number), _iso(moment)
    except (TypeError, ValueError, OSError, OverflowError):
        pass
    text = _safe_text(value, 80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        parsed = parsed.astimezone(timezone.utc)
        if 2000 <= parsed.year <= 2100:
            return int(parsed.timestamp()), _iso(parsed)
    except ValueError:
        pass
    return 0, text


def _stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:length]
    return f"{prefix}-{digest}"


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    if (
        not name
        or len(name) > 120
        or name in {".", ".."}
        or name[-1] in {".", " "}
        or re.search(r'[<>:"/\\|?*]', name)
        or any(ord(character) < 32 for character in name)
    ):
        raise WechatStoreError("INVALID_FILE", "微信导出文件名无效")
    if Path(name).suffix.casefold() not in ALLOWED_IMPORT_SUFFIXES:
        raise WechatStoreError("UNSUPPORTED_FORMAT", "仅支持 JSON、JSONL 和 CSV 格式的微信结构化导出")
    return name


def _connect(project_root: Path | str, *, writable: bool) -> sqlite3.Connection:
    root = _wechat_root(project_root)
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise WechatStoreError("UNSAFE_PATH", "微信资料目录必须是应用内的普通目录")
    if writable:
        root.mkdir(parents=True, exist_ok=True)
    path = database_path(project_root)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise WechatStoreError("UNSAFE_PATH", "微信会话索引必须是普通文件")
    if not path.exists() and not writable:
        raise WechatStoreError("NOT_CONFIGURED", "尚未导入微信会话")
    try:
        if writable:
            connection = sqlite3.connect(path, timeout=5)
        else:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if writable:
            connection.execute("PRAGMA journal_mode=WAL")
            _initialize(connection)
        elif connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise WechatStoreError("SCHEMA_MISMATCH", "微信会话索引版本不受支持")
        return connection
    except sqlite3.Error as error:
        raise WechatStoreError("STORE_ERROR", f"无法打开微信会话索引：{error}") from error


def _initialize(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in {0, SCHEMA_VERSION}:
        raise WechatStoreError("SCHEMA_MISMATCH", "微信会话索引版本不受支持")
    if version == SCHEMA_VERSION:
        return
    connection.executescript(
        """
        CREATE TABLE import_batches (
          batch_id TEXT PRIMARY KEY,
          source_name TEXT NOT NULL,
          source_format TEXT NOT NULL,
          file_sha256 TEXT NOT NULL,
          account_label TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          auto_cleanup INTEGER NOT NULL CHECK (auto_cleanup IN (0,1)),
          raw_path TEXT NOT NULL,
          raw_status TEXT NOT NULL CHECK (raw_status IN ('retained','purged')),
          parsed_rows INTEGER NOT NULL,
          imported_messages INTEGER NOT NULL,
          duplicate_messages INTEGER NOT NULL,
          conversation_count INTEGER NOT NULL
        ) STRICT;
        CREATE INDEX import_batches_hash_idx ON import_batches(file_sha256, raw_status, imported_at DESC);

        CREATE TABLE conversations (
          conversation_id TEXT PRIMARY KEY,
          source_username TEXT NOT NULL,
          display_name TEXT NOT NULL,
          chat_type TEXT NOT NULL CHECK (chat_type IN ('direct','group','official','unknown')),
          retained_messages INTEGER NOT NULL DEFAULT 0,
          total_imported_messages INTEGER NOT NULL DEFAULT 0,
          first_epoch INTEGER NOT NULL DEFAULT 0,
          last_epoch INTEGER NOT NULL DEFAULT 0,
          last_sent_at TEXT NOT NULL DEFAULT '',
          last_preview TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE messages (
          message_id TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE RESTRICT,
          source_batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE RESTRICT,
          source_message_id TEXT NOT NULL,
          sender_id TEXT NOT NULL,
          sender_name TEXT NOT NULL,
          is_self INTEGER NOT NULL CHECK (is_self IN (0,1)),
          sent_at TEXT NOT NULL,
          epoch INTEGER NOT NULL,
          kind TEXT NOT NULL,
          content TEXT NOT NULL,
          source_locator TEXT NOT NULL,
          content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64),
          imported_at TEXT NOT NULL,
          purge_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX messages_conversation_time_idx ON messages(conversation_id, epoch DESC, message_id DESC);
        CREATE INDEX messages_purge_idx ON messages(purge_at, message_id);

        CREATE TABLE message_imports (
          batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE RESTRICT,
          message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
          row_number INTEGER NOT NULL,
          PRIMARY KEY(batch_id, message_id)
        ) STRICT;

        CREATE TABLE review_scopes (
          scope_id TEXT PRIMARY KEY,
          project_id TEXT NOT NULL,
          title TEXT NOT NULL,
          date_from TEXT NOT NULL,
          date_to TEXT NOT NULL,
          query TEXT NOT NULL,
          conversation_ids_json TEXT NOT NULL CHECK (json_valid(conversation_ids_json)),
          message_count INTEGER NOT NULL,
          selection_sha256 TEXT NOT NULL CHECK (length(selection_sha256)=64),
          model_sharing_confirmed INTEGER NOT NULL CHECK (model_sharing_confirmed=1),
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('active','expired'))
        ) STRICT;
        PRAGMA user_version=1;
        """
    )
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE message_fts USING fts5(message_id UNINDEXED, conversation_id UNINDEXED, sender_name, content, tokenize='unicode61')"
        )
    except sqlite3.OperationalError:
        connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL) STRICT")
        connection.execute("INSERT INTO settings VALUES('fts','unavailable')")
    connection.commit()


def _iter_json_messages(value: Any, inherited: Mapping[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    inherited = dict(inherited or {})
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield {**inherited, **item}
        return
    if not isinstance(value, dict):
        return
    for key in ("messages", "rows", "data", "items"):
        child = value.get(key)
        if isinstance(child, list):
            defaults = {
                "conversation": _first(value, ("conversation", "conversation_id", "username", "talker")),
                "conversation_name": _first(value, ("conversation_name", "display_name", "name")),
            }
            yield from _iter_json_messages(child, {**inherited, **{k: v for k, v in defaults.items() if v}})
            return
        if isinstance(child, dict):
            yield from _iter_json_messages(child, inherited)
            return
    conversations = value.get("conversations")
    if isinstance(conversations, list):
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            defaults = {
                "conversation": _first(conversation, ("conversation", "conversation_id", "username", "talker")),
                "conversation_name": _first(conversation, ("conversation_name", "display_name", "name")),
                "is_group": conversation.get("is_group"),
            }
            yield from _iter_json_messages(conversation.get("messages", []), {**inherited, **defaults})
        return
    yield {**inherited, **value}


def _iter_rows(path: Path) -> Iterator[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(MAX_IMPORT_BYTES)
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row:
                        yield dict(row)
        except (csv.Error, UnicodeDecodeError) as error:
            raise WechatStoreError("INVALID_EXPORT", f"微信 CSV 导出结构无效：{error}") from error
        finally:
            csv.field_size_limit(previous_limit)
        return
    if suffix == ".jsonl":
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise WechatStoreError("INVALID_EXPORT", f"JSONL 第 {line_number} 行不是有效 JSON") from error
                    yield from _iter_json_messages(value)
        except UnicodeDecodeError as error:
            raise WechatStoreError("INVALID_EXPORT", "微信 JSONL 导出不是有效 UTF-8") from error
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WechatStoreError("INVALID_EXPORT", "微信 JSON 导出结构无效") from error
    yield from _iter_json_messages(value)


def _normalize_message(row: Mapping[str, Any], account_label: str, row_number: int) -> dict[str, Any] | None:
    conversation = _safe_text(_first(row, (
        "conversation", "conversation_id", "talker", "username", "session_id", "chat_id", "room_id",
    )), 500)
    conversation_name = _safe_text(_first(row, (
        "conversation_name", "talker_name", "session_name", "chat_name", "room_name",
    )), 300)
    if not conversation:
        conversation = conversation_name
    if not conversation:
        return None
    display_name = conversation_name or conversation
    sender_id = _safe_text(_first(row, ("sender_wxid", "sender_id", "from_id", "from_user")), 300)
    sender_name = _safe_text(_first(row, ("sender_name", "sender", "from_name", "author")), 300)
    is_self = _bool(_first(row, ("is_self", "is_sender", "from_me", "mine"), False))
    if not sender_name:
        sender_name = "我" if is_self else "对方"
    epoch, sent_at = _timestamp(_first(row, ("epoch", "timestamp", "create_time", "sent_at", "time", "date")))
    kind = _safe_text(_first(row, ("kind", "type_label", "message_type", "msg_type", "type"), "text"), 80, default="text")
    content = _safe_text(_first(row, ("content", "body", "text", "message", "structured_description")), MAX_MESSAGE_CHARS)
    structured_title = _safe_text(_first(row, ("structured_title", "title")), 1000)
    structured_url = _safe_text(_first(row, ("structured_url", "url")), 2048)
    if not content and (structured_title or structured_url):
        content = " ".join(value for value in (structured_title, structured_url) if value)
    if not content:
        content = f"[{kind or '非文字消息'}]"
    source_message_id = _safe_text(_first(row, ("message_id", "msg_id", "local_id", "id")), 300)
    table = _safe_text(_first(row, ("table", "source_table")), 200)
    if table and source_message_id:
        source_message_id = f"{table}:{source_message_id}"
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    conversation_id = _stable_id("wxconv", account_label, conversation)
    identity = source_message_id or f"{epoch}:{sender_id or sender_name}:{kind}:{content_sha256}"
    message_id = _stable_id("wxmsg", conversation_id, identity)
    is_group = _bool(row.get("is_group")) or conversation.endswith("@chatroom")
    chat_type = "group" if is_group else "official" if conversation.startswith("gh_") else "direct"
    return {
        "message_id": message_id,
        "conversation_id": conversation_id,
        "source_username": conversation,
        "display_name": display_name,
        "chat_type": chat_type,
        "source_message_id": source_message_id or f"row-{row_number}",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "is_self": int(is_self),
        "sent_at": sent_at,
        "epoch": epoch,
        "kind": kind or "unknown",
        "content": content,
        "content_sha256": content_sha256,
    }


def _copy_raw(project_root: Path, source: Path, batch_id: str, filename: str) -> str:
    imports_root = _wechat_root(project_root) / "imports"
    if imports_root.exists() and (imports_root.is_symlink() or not imports_root.is_dir()):
        raise WechatStoreError("UNSAFE_PATH", "微信导入目录必须是普通目录")
    target_root = imports_root / batch_id
    target_root.mkdir(parents=True, exist_ok=False)
    target = target_root / filename
    descriptor, temporary_name = tempfile.mkstemp(prefix=".wechat-import-", suffix=".tmp", dir=target_root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target.relative_to(project_root).as_posix()


def import_export(
    project_root: Path | str,
    source_path: Path | str,
    *,
    source_name: str,
    account_label: str = "本机微信",
    retention_days: int = 7,
    auto_cleanup: bool = True,
    ownership_confirmed: bool,
) -> dict[str, Any]:
    if not ownership_confirmed:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请先确认这是本人账号且有权处理的聊天记录")
    root = _root(project_root)
    selected_source = Path(source_path)
    if selected_source.is_symlink() or not selected_source.is_file():
        raise WechatStoreError("INVALID_FILE", "请选择普通的微信结构化导出文件")
    source = selected_source.resolve()
    filename = _safe_filename(source_name)
    if not source.is_file():
        raise WechatStoreError("INVALID_FILE", "请选择普通的微信结构化导出文件")
    size = source.stat().st_size
    if size <= 0 or size > MAX_IMPORT_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "微信导出文件必须为 1 字节至 64 兆字节")
    if source.suffix.casefold() != Path(filename).suffix.casefold():
        raise WechatStoreError("INVALID_FILE", "上传文件类型与文件名不一致")
    label = _safe_text(account_label, 120, default="本机微信") or "本机微信"
    if int(retention_days) != 7 or not auto_cleanup:
        raise WechatStoreError("INVALID_RETENTION", "微信导入副本和消息原文固定保留 7 天并自动清理")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    file_sha256 = digest.hexdigest()
    imported_at = _now()
    expires_at = imported_at + timedelta(days=int(retention_days))

    connection = _connect(root, writable=True)
    raw_relative = ""
    try:
        duplicate = connection.execute(
            "SELECT * FROM import_batches WHERE file_sha256=? AND raw_status='retained' ORDER BY imported_at DESC LIMIT 1",
            (file_sha256,),
        ).fetchone()
        if duplicate:
            return {
                "batch_id": duplicate["batch_id"],
                "duplicate_file": True,
                "message_count": duplicate["imported_messages"],
                "conversation_count": duplicate["conversation_count"],
                "expires_at": duplicate["expires_at"],
                "message": "该导出文件已经导入，未重复保存或建立重复消息。",
            }
        batch_id = f"wechat-batch-{uuid.uuid4().hex[:20]}"
        raw_relative = _copy_raw(root, source, batch_id, filename)
        imported_copy = (root / raw_relative).resolve()
        copied_digest = hashlib.sha256(imported_copy.read_bytes()).hexdigest()
        if copied_digest != file_sha256:
            raise WechatStoreError("FILE_CHANGED", "微信导出文件在导入期间发生变化，请重新选择")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO import_batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id, filename, source.suffix.casefold().lstrip("."), file_sha256, label,
                _iso(imported_at), _iso(expires_at), int(auto_cleanup), raw_relative, "retained",
                0, 0, 0, 0,
            ),
        )
        parsed_rows = imported = duplicates = 0
        conversation_ids: set[str] = set()
        for row_number, row in enumerate(_iter_rows(imported_copy), 1):
            if row_number > MAX_IMPORT_ROWS:
                raise WechatStoreError("ROW_LIMIT", f"单次最多导入 {MAX_IMPORT_ROWS} 条消息")
            parsed_rows += 1
            normalized = _normalize_message(row, label, row_number)
            if normalized is None:
                continue
            conversation_ids.add(normalized["conversation_id"])
            timestamp = _iso(imported_at)
            connection.execute(
                """INSERT INTO conversations(
                     conversation_id,source_username,display_name,chat_type,retained_messages,total_imported_messages,
                     first_epoch,last_epoch,last_sent_at,last_preview,updated_at
                   ) VALUES(?,?,?,?,0,0,0,0,'','',?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                     display_name=CASE WHEN excluded.display_name<>excluded.source_username THEN excluded.display_name ELSE conversations.display_name END,
                     chat_type=excluded.chat_type,updated_at=excluded.updated_at""",
                (
                    normalized["conversation_id"], normalized["source_username"], normalized["display_name"],
                    normalized["chat_type"], timestamp,
                ),
            )
            existing = connection.execute(
                "SELECT purge_at,conversation_id,content_sha256 FROM messages WHERE message_id=?", (normalized["message_id"],)
            ).fetchone()
            if existing:
                if (
                    existing["conversation_id"] != normalized["conversation_id"]
                    or existing["content_sha256"] != normalized["content_sha256"]
                ):
                    raise WechatStoreError("MESSAGE_CONFLICT", "相同消息编号对应了不同会话或正文，已停止导入")
                duplicates += 1
                if existing["purge_at"] < _iso(expires_at):
                    connection.execute(
                        "UPDATE messages SET purge_at=? WHERE message_id=?",
                        (_iso(expires_at), normalized["message_id"]),
                    )
            else:
                locator = f"wechat://message/{normalized['message_id']}"
                connection.execute(
                    """INSERT INTO messages(
                         message_id,conversation_id,source_batch_id,source_message_id,sender_id,sender_name,is_self,
                         sent_at,epoch,kind,content,source_locator,content_sha256,imported_at,purge_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        normalized["message_id"], normalized["conversation_id"], batch_id,
                        normalized["source_message_id"], normalized["sender_id"], normalized["sender_name"],
                        normalized["is_self"], normalized["sent_at"], normalized["epoch"], normalized["kind"],
                        normalized["content"], locator, normalized["content_sha256"], timestamp, _iso(expires_at),
                    ),
                )
                try:
                    connection.execute(
                        "INSERT INTO message_fts(message_id,conversation_id,sender_name,content) VALUES(?,?,?,?)",
                        (normalized["message_id"], normalized["conversation_id"], normalized["sender_name"], normalized["content"]),
                    )
                except sqlite3.OperationalError:
                    pass
                imported += 1
            connection.execute(
                "INSERT OR IGNORE INTO message_imports(batch_id,message_id,row_number) VALUES(?,?,?)",
                (batch_id, normalized["message_id"], row_number),
            )
        if parsed_rows == 0 or not conversation_ids:
            raise WechatStoreError(
                "NO_MESSAGES",
                "没有识别到可整理的消息；请导出包含 conversation、sender、time、content 等字段的 JSON、JSONL 或 CSV",
            )
        for conversation_id in conversation_ids:
            _refresh_conversation(connection, conversation_id)
        connection.execute(
            """UPDATE import_batches SET parsed_rows=?,imported_messages=?,duplicate_messages=?,conversation_count=?
               WHERE batch_id=?""",
            (parsed_rows, imported, duplicates, len(conversation_ids), batch_id),
        )
        connection.commit()
        return {
            "batch_id": batch_id,
            "duplicate_file": False,
            "parsed_rows": parsed_rows,
            "message_count": imported,
            "duplicate_messages": duplicates,
            "conversation_count": len(conversation_ids),
            "expires_at": _iso(expires_at),
            "auto_cleanup": bool(auto_cleanup),
            "message": (
                f"已在本机整理 {len(conversation_ids)} 个会话、导入 {imported} 条新消息"
                f"{f'，跳过 {duplicates} 条重复消息' if duplicates else ''}。"
            ),
        }
    except Exception:
        connection.rollback()
        if raw_relative:
            raw = root / raw_relative
            raw.unlink(missing_ok=True)
            try:
                raw.parent.rmdir()
            except OSError:
                pass
        raise
    finally:
        connection.close()


def _refresh_conversation(connection: sqlite3.Connection, conversation_id: str) -> None:
    summary = connection.execute(
        """SELECT count(*) AS retained,min(epoch) AS first_epoch,max(epoch) AS last_epoch
           FROM messages WHERE conversation_id=?""",
        (conversation_id,),
    ).fetchone()
    latest = connection.execute(
        "SELECT sent_at,content FROM messages WHERE conversation_id=? ORDER BY epoch DESC,message_id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    total = connection.execute(
        "SELECT count(DISTINCT message_id) FROM message_imports WHERE message_id IN (SELECT message_id FROM messages WHERE conversation_id=?)",
        (conversation_id,),
    ).fetchone()[0]
    connection.execute(
        """UPDATE conversations SET retained_messages=?,total_imported_messages=max(total_imported_messages,?),
           first_epoch=?,last_epoch=?,last_sent_at=?,last_preview=?,updated_at=? WHERE conversation_id=?""",
        (
            int(summary["retained"] or 0), int(total or 0), int(summary["first_epoch"] or 0),
            int(summary["last_epoch"] or 0), latest["sent_at"] if latest else "",
            _safe_text(latest["content"] if latest else "", 160), _iso(_now()), conversation_id,
        ),
    )


def _date(value: Any, label: str) -> str:
    text = _safe_text(value, 10)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as error:
        raise WechatStoreError("INVALID_DATE", f"{label}必须使用 YYYY-MM-DD") from error
    return text


def list_conversations(
    project_root: Path | str,
    *,
    query: str = "",
    chat_type: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    limit = int(limit)
    offset = int(offset)
    if not 1 <= limit <= 200 or not 0 <= offset <= 100_000:
        raise WechatStoreError("INVALID_PAGING", "会话分页参数无效")
    if chat_type and chat_type not in {"direct", "group", "official", "unknown"}:
        raise WechatStoreError("INVALID_FILTER", "会话类型无效")
    start = _date(date_from, "开始日期") if date_from else ""
    end = _date(date_to, "结束日期") if date_to else ""
    if start and end and start > end:
        raise WechatStoreError("INVALID_DATE", "开始日期不能晚于结束日期")
    search = _safe_text(query, 100)
    connection = _connect(project_root, writable=False)
    try:
        clauses = ["c.retained_messages>0"]
        values: list[Any] = []
        if chat_type:
            clauses.append("c.chat_type=?")
            values.append(chat_type)
        message_date = "(CASE WHEN sm.epoch>0 THEN date(sm.epoch,'unixepoch','localtime') ELSE substr(sm.sent_at,1,10) END)"
        if search:
            escaped = _like(search)
            message_clauses = [
                "sm.conversation_id=c.conversation_id",
                "(sm.content LIKE ? ESCAPE '\\' OR sm.sender_name LIKE ? ESCAPE '\\')",
            ]
            message_values: list[Any] = [escaped, escaped]
            if start:
                message_clauses.append(f"{message_date}>=?")
                message_values.append(start)
            if end:
                message_clauses.append(f"{message_date}<=?")
                message_values.append(end)
            clauses.append(
                "(c.display_name LIKE ? ESCAPE '\\' OR c.source_username LIKE ? ESCAPE '\\' "
                "OR EXISTS(SELECT 1 FROM messages sm WHERE "
                + " AND ".join(message_clauses) + "))"
            )
            values.extend([escaped, escaped, *message_values])
        if start or end:
            period_clauses = ["sm.conversation_id=c.conversation_id"]
            period_values: list[Any] = []
            if start:
                period_clauses.append(f"{message_date}>=?")
                period_values.append(start)
            if end:
                period_clauses.append(f"{message_date}<=?")
                period_values.append(end)
            clauses.append("EXISTS(SELECT 1 FROM messages sm WHERE " + " AND ".join(period_clauses) + ")")
            values.extend(period_values)
        where = " AND ".join(clauses)
        total = int(connection.execute(f"SELECT count(*) FROM conversations c WHERE {where}", values).fetchone()[0])
        rows = [dict(row) for row in connection.execute(
            f"""SELECT conversation_id,source_username,display_name,chat_type,retained_messages,
                       total_imported_messages,first_epoch,last_epoch,last_sent_at,last_preview,updated_at
                FROM conversations c WHERE {where}
                ORDER BY last_epoch DESC,display_name COLLATE NOCASE,conversation_id LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        )]
        return {"total": total, "returned": len(rows), "offset": offset, "rows": rows}
    finally:
        connection.close()


def read_messages(
    project_root: Path | str,
    conversation_id: str,
    *,
    query: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 100,
    before_epoch: int | None = None,
) -> dict[str, Any]:
    if not ID_RE.fullmatch(conversation_id):
        raise WechatStoreError("INVALID_ID", "会话编号无效")
    limit = int(limit)
    if not 1 <= limit <= 200:
        raise WechatStoreError("INVALID_PAGING", "单次消息读取数量必须为 1–200")
    start = _date(date_from, "开始日期") if date_from else ""
    end = _date(date_to, "结束日期") if date_to else ""
    search = _safe_text(query, 100)
    connection = _connect(project_root, writable=False)
    try:
        conversation = connection.execute(
            "SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        if not conversation:
            raise WechatStoreError("NOT_FOUND", "会话不存在")
        clauses = ["conversation_id=?"]
        values: list[Any] = [conversation_id]
        if search:
            clauses.append("(content LIKE ? ESCAPE '\\' OR sender_name LIKE ? ESCAPE '\\')")
            escaped = _like(search)
            values.extend([escaped, escaped])
        if start:
            clauses.append("(CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)>=?")
            values.append(start)
        if end:
            clauses.append("(CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)<=?")
            values.append(end)
        if before_epoch is not None:
            clauses.append("epoch<?")
            values.append(int(before_epoch))
        where = " AND ".join(clauses)
        selected = [dict(row) for row in connection.execute(
            f"""SELECT message_id,sender_id,sender_name,is_self,sent_at,epoch,kind,content,source_locator,content_sha256
                FROM messages WHERE {where} ORDER BY epoch DESC,message_id DESC LIMIT ?""",
            [*values, limit + 1],
        )]
        has_more = len(selected) > limit
        selected = selected[:limit]
        selected.reverse()
        return {
            "conversation": dict(conversation),
            "rows": selected,
            "returned": len(selected),
            "has_more": has_more,
            "next_before_epoch": min((int(row["epoch"]) for row in selected), default=None) if has_more else None,
        }
    finally:
        connection.close()


def create_review_scope(project_root: Path | str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("model_sharing_confirmed") is not True:
        raise WechatStoreError(
            "MODEL_SHARING_REQUIRED",
            "请确认允许将所选会话文字交给当前模型处理；若模型在云端，内容会离开本机",
        )
    project_id = _safe_text(payload.get("project_id"), 128, default="project-default") or "project-default"
    if not ID_RE.fullmatch(project_id):
        raise WechatStoreError("INVALID_PROJECT", "项目编号无效")
    date_from = _date(payload.get("date_from"), "开始日期")
    date_to = _date(payload.get("date_to"), "结束日期")
    if date_from > date_to:
        raise WechatStoreError("INVALID_DATE", "开始日期不能晚于结束日期")
    query = _safe_text(payload.get("query"), 100)
    supplied_ids = payload.get("conversation_ids")
    if not isinstance(supplied_ids, list) or len(supplied_ids) > MAX_SCOPE_CONVERSATIONS:
        raise WechatStoreError("INVALID_SCOPE", f"一次最多选择 {MAX_SCOPE_CONVERSATIONS} 个会话")
    conversation_ids = []
    for value in supplied_ids:
        identity = _safe_text(value, 128)
        if not ID_RE.fullmatch(identity):
            raise WechatStoreError("INVALID_ID", "所选会话编号无效")
        if identity not in conversation_ids:
            conversation_ids.append(identity)
    connection = _connect(project_root, writable=True)
    try:
        if not conversation_ids and payload.get("all_in_period") is True:
            conversation_ids = [row[0] for row in connection.execute(
                """SELECT DISTINCT conversation_id FROM messages
                   WHERE (CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)>=?
                     AND (CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)<=?
                   ORDER BY conversation_id LIMIT ?""",
                (date_from, date_to, MAX_SCOPE_CONVERSATIONS + 1),
            )]
            if len(conversation_ids) > MAX_SCOPE_CONVERSATIONS:
                raise WechatStoreError("SCOPE_TOO_LARGE", f"该时间段超过 {MAX_SCOPE_CONVERSATIONS} 个会话，请先筛选或手动选择")
        if not conversation_ids:
            raise WechatStoreError("INVALID_SCOPE", "请至少选择一个会话")
        placeholders = ",".join("?" for _ in conversation_ids)
        clauses = [
            f"conversation_id IN ({placeholders})",
            "(CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)>=?",
            "(CASE WHEN epoch>0 THEN date(epoch,'unixepoch','localtime') ELSE substr(sent_at,1,10) END)<=?",
        ]
        values: list[Any] = [*conversation_ids, date_from, date_to]
        if query:
            clauses.append("(content LIKE ? ESCAPE '\\' OR sender_name LIKE ? ESCAPE '\\')")
            escaped = _like(query)
            values.extend([escaped, escaped])
        where = " AND ".join(clauses)
        selected_content = connection.execute(
            f"""SELECT message_id,conversation_id,sender_name,is_self,sent_at,epoch,kind,content,
                       source_locator,content_sha256
                  FROM messages WHERE {where} ORDER BY epoch,message_id""",
            values,
        ).fetchall()
        message_count = len(selected_content)
        if message_count <= 0:
            raise WechatStoreError("EMPTY_SCOPE", "所选日期和会话范围内没有可整理的消息")
        if message_count > MAX_SCOPE_MESSAGES:
            raise WechatStoreError("SCOPE_TOO_LARGE", f"一次最多整理 {MAX_SCOPE_MESSAGES} 条消息，请缩短日期或减少会话")
        text_bytes = sum(len(str(row["content"]).encode("utf-8")) for row in selected_content)
        if text_bytes > MAX_SCOPE_TEXT_BYTES:
            raise WechatStoreError(
                "SCOPE_TOO_LARGE",
                "所选会话原文超过单次整理安全上限，请缩短日期、输入关键词或减少会话",
            )
        selected_conversations = connection.execute(
            f"""SELECT conversation_id,display_name,chat_type
                  FROM conversations WHERE conversation_id IN ({placeholders})
                  ORDER BY conversation_id""",
            conversation_ids,
        ).fetchall()
        if len(selected_conversations) != len(conversation_ids):
            raise WechatStoreError("NOT_FOUND", "所选范围中有会话不存在或已被清理")
        names = sorted(str(row["display_name"]) for row in selected_conversations)
        title = _safe_text(payload.get("title"), 160) or (
            f"{names[0]} 等 {len(names)} 个会话" if len(names) > 1 else names[0]
        )
        scope_id = f"wechat-scope-{uuid.uuid4().hex[:20]}"
        canonical = json.dumps({
            "conversation_ids": sorted(conversation_ids),
            "conversations": [
                {
                    "chat_type": row["chat_type"],
                    "conversation_id": row["conversation_id"],
                    "display_name": row["display_name"],
                }
                for row in selected_conversations
            ],
            "date_from": date_from,
            "date_to": date_to,
            "messages": [
                {
                    "content_sha256": row["content_sha256"],
                    "conversation_id": row["conversation_id"],
                    "epoch": row["epoch"],
                    "is_self": row["is_self"],
                    "kind": row["kind"],
                    "message_id": row["message_id"],
                    "sent_at": row["sent_at"],
                    "sender_name": row["sender_name"],
                    "source_locator": row["source_locator"],
                }
                for row in selected_content
            ],
            "query": query,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        created_at = _now()
        expires_at = created_at + timedelta(days=7)
        connection.execute(
            "INSERT INTO review_scopes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                scope_id, project_id, title, date_from, date_to, query,
                json.dumps(conversation_ids, ensure_ascii=False), message_count,
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(), 1,
                _iso(created_at), _iso(expires_at), "active",
            ),
        )
        connection.commit()
        return {
            "scope_id": scope_id,
            "project_id": project_id,
            "title": title,
            "date_from": date_from,
            "date_to": date_to,
            "conversation_count": len(conversation_ids),
            "message_count": message_count,
            "text_bytes": text_bytes,
            "expires_at": _iso(expires_at),
            "selection_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
    finally:
        connection.close()


def review_scope_summary(project_root: Path | str, scope_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"wechat-scope-[a-f0-9]{20}", _safe_text(scope_id, 64)):
        raise WechatStoreError("INVALID_ID", "微信会话授权范围编号无效")
    connection = _connect(project_root, writable=False)
    try:
        row = connection.execute(
            """SELECT scope_id,project_id,title,date_from,date_to,query,message_count,
                      selection_sha256,created_at,expires_at,status
                 FROM review_scopes WHERE scope_id=?""",
            (scope_id,),
        ).fetchone()
        if not row:
            raise WechatStoreError("NOT_FOUND", "微信会话授权范围不存在")
        summary = dict(row)
        try:
            expires_at = datetime.fromisoformat(str(summary["expires_at"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise WechatStoreError("STORE_ERROR", "微信会话授权范围时间无效") from error
        if summary["status"] != "active" or expires_at <= _now():
            raise WechatStoreError("SCOPE_EXPIRED", "微信会话授权范围已到期，请重新选择会话")
        return summary
    finally:
        connection.close()


def cleanup_expired(project_root: Path | str, *, reference: datetime | None = None) -> dict[str, Any]:
    path = database_path(project_root)
    if not path.is_file():
        return {"purged_batches": 0, "purged_messages": 0, "purged_files": 0}
    root = _root(project_root)
    instant = reference or _now()
    cutoff = _iso(instant)
    connection = _connect(root, writable=True)
    purged_files = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            "SELECT batch_id,raw_path FROM import_batches WHERE auto_cleanup=1 AND raw_status='retained' AND expires_at<=?",
            (cutoff,),
        ).fetchall()
        for batch in batches:
            raw = (root / batch["raw_path"]).resolve()
            allowed = (_wechat_root(root) / "imports").resolve()
            if raw.is_relative_to(allowed) and raw.is_file() and not raw.is_symlink():
                raw.unlink()
                purged_files += 1
                try:
                    raw.parent.rmdir()
                except OSError:
                    pass
            connection.execute("UPDATE import_batches SET raw_status='purged' WHERE batch_id=?", (batch["batch_id"],))
        expired_ids = [row[0] for row in connection.execute("SELECT message_id FROM messages WHERE purge_at<=?", (cutoff,))]
        if expired_ids:
            try:
                connection.executemany("DELETE FROM message_fts WHERE message_id=?", ((value,) for value in expired_ids))
            except sqlite3.OperationalError:
                pass
            connection.executemany("DELETE FROM messages WHERE message_id=?", ((value,) for value in expired_ids))
        connection.execute("UPDATE review_scopes SET status='expired' WHERE status='active' AND expires_at<=?", (cutoff,))
        conversation_ids = [row[0] for row in connection.execute("SELECT conversation_id FROM conversations")]
        for conversation_id in conversation_ids:
            _refresh_conversation(connection, conversation_id)
        connection.commit()
        return {"purged_batches": len(batches), "purged_messages": len(expired_ids), "purged_files": purged_files}
    except sqlite3.Error as error:
        connection.rollback()
        raise WechatStoreError("STORE_ERROR", f"清理微信导入副本失败：{error}") from error
    finally:
        connection.close()


def dashboard(project_root: Path | str) -> dict[str, Any]:
    path = database_path(project_root)
    if not path.is_file():
        return {
            "configured": False,
            "status": "empty",
            "conversation_count": 0,
            "message_count": 0,
            "batch_count": 0,
            "batches": [],
            "revision": "empty",
            "supported_formats": ["json", "jsonl", "csv"],
            "upstream_status": "design_specification_only",
        }
    connection = _connect(project_root, writable=False)
    try:
        conversation_count = int(connection.execute("SELECT count(*) FROM conversations WHERE retained_messages>0").fetchone()[0])
        message_count = int(connection.execute("SELECT count(*) FROM messages").fetchone()[0])
        batch_count = int(connection.execute("SELECT count(*) FROM import_batches").fetchone()[0])
        batches = [dict(row) for row in connection.execute(
            """SELECT batch_id,source_name,account_label,imported_at,expires_at,auto_cleanup,raw_status,
                      parsed_rows,imported_messages,duplicate_messages,conversation_count
               FROM import_batches ORDER BY imported_at DESC,batch_id DESC LIMIT 12"""
        )]
        last_change = connection.execute(
            "SELECT max(value) FROM (SELECT max(imported_at) value FROM import_batches UNION ALL SELECT max(updated_at) FROM conversations)"
        ).fetchone()[0] or ""
        revision = hashlib.sha256(f"{conversation_count}:{message_count}:{batch_count}:{last_change}".encode()).hexdigest()[:20]
        return {
            "configured": True,
            "status": "ready" if message_count else "raw_expired",
            "conversation_count": conversation_count,
            "message_count": message_count,
            "batch_count": batch_count,
            "batches": batches,
            "revision": revision,
            "supported_formats": ["json", "jsonl", "csv"],
            "upstream_status": "design_specification_only",
        }
    finally:
        connection.close()
