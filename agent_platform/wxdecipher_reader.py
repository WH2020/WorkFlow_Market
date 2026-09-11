"""Bounded, read-only WeChat 4 SQLite -> existing structured-message contract.

No directory discovery, media fetching, model calls, or fallback guessing of
unknown schemas. WXDecipher's upstream schema examples are not verified schemas.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .wechat_store import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, MAX_MESSAGE_CHARS, WechatStoreError
from .wxdecipher_crypto import open_readonly


MAX_TABLES = 2048
MAX_NAMES = 200_000
MAX_DECODED_BYTES = 1024 * 1024
HASH_TABLE = re.compile(r"msg_([0-9a-f]{32})\Z", re.IGNORECASE)
MESSAGE_COLUMNS = {"local_id", "local_type", "sort_seq", "create_time", "message_content", "real_sender_id"}
NAME_TABLES = {"name2id", "sendername2id", "contact", "contacts", "session", "sessiontable"}


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _column(columns: dict[str, str], *aliases: str) -> str:
    return next((columns[name.casefold()] for name in aliases if name.casefold() in columns), "")


def _schema(connection) -> list[tuple[str, dict[str, str]]]:
    tables = connection.execute(
        "SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT ?",
        (MAX_TABLES + 1,),
    ).fetchall()
    if len(tables) > MAX_TABLES:
        raise WechatStoreError("SCHEMA_LIMIT", "数据库表数量超出本地处理上限")
    result = []
    for row in tables:
        name = str(row["name"])
        # Only real ordinary tables: never invoke an uploaded virtual-table module.
        if not re.match(r"\s*CREATE\s+TABLE\b", str(row["sql"] or ""), re.IGNORECASE):
            continue
        columns = {str(item["name"]).casefold(): str(item["name"]) for item in connection.execute(
            f"PRAGMA table_info({_identifier(name)})",
        )}
        result.append((name, columns))
    return result


def _text(value: Any, maximum: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).replace("\x00", "").strip()[:maximum]


def _integer(value: Any, *, default: int = 0) -> int:
    try:
        return int(value) if value is not None and value != "" else default
    except (ValueError, TypeError, OverflowError):
        return default


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_DECODED_BYTES:
            raise WechatStoreError("CONTENT_TOO_LARGE", "单条消息正文超出安全解码上限；未导入")
        return value
    if not isinstance(value, bytes):
        return str(value)
    if len(value) > MAX_DECODED_BYTES:
        raise WechatStoreError("CONTENT_TOO_LARGE", "单条消息压缩内容过大；未导入")
    if value.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard
        except ImportError as error:
            raise WechatStoreError("DEPENDENCY_MISSING", "消息使用 Zstandard 压缩，请安装 requirements-wxdecipher.txt 后重试") from error
        try:
            with zstandard.ZstdDecompressor(max_window_size=8192).stream_reader(io.BytesIO(value)) as reader:
                value = reader.read(MAX_DECODED_BYTES + 1)
            if len(value) > MAX_DECODED_BYTES:
                raise WechatStoreError("CONTENT_TOO_LARGE", "消息解压后超出安全上限；未导入")
        except zstandard.ZstdError as error:
            raise WechatStoreError("DECOMPRESSION_FAILED", "消息压缩内容损坏或窗口过大；未导入") from error
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WechatStoreError("ENCODING_UNSUPPORTED", "消息正文不是支持的 UTF-8 / Zstandard 格式；未导入") from error


def _message_content(body: str, type_number: int, subtype: int) -> tuple[str, str]:
    kind = {1: "text", 3: "image", 34: "voice", 43: "video", 47: "emoji", 49: "link",
            10000: "system", 10002: "system"}.get(type_number, "unknown")
    if kind in {"image", "voice", "video", "emoji"}:
        return kind, {"image": "[图片]", "voice": "[语音]", "video": "[视频]", "emoji": "[表情]"}[kind]
    if type_number == 49:
        if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
            raise WechatStoreError("INVALID_MESSAGE", "卡片含不支持的 XML 声明；未导入")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return "link", "[卡片内容无法解析]"
        app = root if root.tag == "appmsg" else root.find(".//appmsg")
        if app is None:
            return "link", "[卡片内容无法解析]"
        subtype = subtype or _integer(app.findtext("type"))
        parts = [(app.findtext(name) or "").strip() for name in ("title", "des", "url")]
        kind = "file" if subtype == 6 else "link"
        return kind, "\n".join(part for part in parts if part) or ("[文件]" if kind == "file" else "[卡片]")
    return kind, body or f"[类型 {type_number}]"


def _load_names(databases: list[tuple[str, Path]]) -> tuple[dict[str, str], dict[str, str], dict[str, dict[int, str]]]:
    display_names: dict[str, str] = {}
    conversation_hashes: dict[str, str] = {}
    source_ids: dict[str, dict[int, str]] = {}
    resource_ids: dict[int, str] = {}
    total = 0
    for source_name, database in databases:
        local_ids: dict[int, str] = {}
        source_ids[source_name] = local_ids
        with open_readonly(database) as connection:
            for table, columns in _schema(connection):
                if table.casefold() not in NAME_TABLES:
                    continue
                username = _column(columns, "username", "user_name", "strUsrName", "userName")
                if not username:
                    continue
                nickname = _column(columns, "nick_name", "nickname", "nickName")
                remark = _column(columns, "remark", "conRemark", "remark_name")
                primary_id = _column(columns, "id", "user_id") or "rowid"
                selections = [f"{_identifier(username)} AS username"]
                selections += [f"{_identifier(col)} AS {alias}" for col, alias in ((nickname, "nickname"), (remark, "remark")) if col]
                if table.casefold() in {"name2id", "sendername2id"}:
                    selections.append(f"{_identifier(primary_id)} AS source_id")
                sql = f"SELECT {','.join(selections)} FROM {_identifier(table)} LIMIT ?"
                for row in connection.execute(sql, (MAX_NAMES + 1,)):
                    total += 1
                    if total > MAX_NAMES:
                        raise WechatStoreError("ROW_LIMIT", "联系人/会话映射超出本次处理上限")
                    name = _text(row["username"])
                    if not name:
                        continue
                    digest = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()
                    conversation_hashes[digest] = name
                    display = (_text(row["remark"]) if remark else "") or (_text(row["nickname"]) if nickname else "")
                    # Reference-only Name2Id/session rows must not erase contact names.
                    if display:
                        display_names[name] = display
                    else:
                        display_names.setdefault(name, name)
                    if table.casefold() in {"name2id", "sendername2id"}:
                        mapping = resource_ids if table.casefold() == "sendername2id" else local_ids
                        identity = _integer(row["source_id"], default=-1)
                        if identity in mapping and mapping[identity] != name:
                            raise WechatStoreError("MAPPING_CONFLICT", "发送者映射不一致，请勿混用不同账号或不同时间的数据库副本")
                        mapping[identity] = name
    # Some Windows 4.1 snapshots keep sender IDs in message_resource.db.
    for mapping in source_ids.values():
        if not mapping:
            mapping.update(resource_ids)
    return display_names, conversation_hashes, source_ids


def convert_databases(databases: list[tuple[str, Path]], output: Path, *, self_username: str = "") -> dict:
    """Write a single JSONL transaction source, bounded and deterministic.

    Inputs contain caller-selected file names and application-owned decrypted
    paths. A missing mapping or unsupported message table fails the whole batch.
    """
    if not databases or output.exists():
        raise WechatStoreError("INVALID_INPUT", "请选择数据库，且使用新的结构化输出文件")
    display, hashes, source_ids = _load_names(databases)
    count = byte_count = message_tables = media_count = unknown_senders = 0
    source_reports = []
    created = False
    try:
        from .wechat_privacy import create_private_file
        create_private_file(output)
        created = True
        with output.open("w", encoding="utf-8", newline="\n") as writer:
            for source_name, database in sorted(databases, key=lambda item: item[0]):
                source_count = 0
                with open_readonly(database, timeout_seconds=90) as connection:
                    for table, columns in _schema(connection):
                        table_hash = HASH_TABLE.fullmatch(table)
                        if not table_hash:
                            if table.casefold().startswith("msg_"):
                                raise WechatStoreError("SCHEMA_MISMATCH", "消息表命名与支持的微信 4 分表格式不符；未导入")
                            continue
                        if not MESSAGE_COLUMNS.issubset(columns):
                            raise WechatStoreError("SCHEMA_MISMATCH", "消息表字段与支持的微信 4 结构不符；请提供脱敏 schema 补充适配，不猜测字段含义")
                        fields = {
                            "id": _column(columns, "local_id", "localId", "msg_id", "id"),
                            "sort": _column(columns, "sort_seq"),
                            "server_id": _column(columns, "server_id", "serverId", "msgSvrId"),
                            "conversation": _column(columns, "talker", "username", "conversation", "conversation_id", "strTalker"),
                            "time": _column(columns, "create_time", "createTime", "timestamp", "time"),
                            "content": _column(columns, "message_content", "content", "strContent"),
                            "compressed": _column(columns, "compress_content", "compressContent"),
                            "type": _column(columns, "local_type", "msg_type", "type"),
                            "subtype": _column(columns, "sub_type", "subType"),
                            "sender": _column(columns, "sender", "sender_wxid", "sender_id", "real_sender_id"),
                            "is_self": _column(columns, "is_sender", "isSender", "is_self"),
                        }
                        if not fields["id"] or not fields["time"] or not fields["content"] or not fields["type"]:
                            raise WechatStoreError("SCHEMA_MISMATCH", "消息表字段与支持的微信 4 结构不匹配；未导入。请提供脱敏 schema 以补充适配")
                        fixed_conversation = hashes.get(table_hash.group(1).lower(), "") if table_hash else ""
                        if not fields["conversation"] and not fixed_conversation:
                            raise WechatStoreError("MISSING_CONTACTS", "无法将消息表对应到会话；请一并选择同一账号的 contact.db、session.db 或包含 Name2Id 的消息库")
                        message_tables += 1
                        selected = ",".join(f"{_identifier(col)} AS {_identifier(alias)}" for alias, col in fields.items() if col)
                        order = ",".join(_identifier(col) for col in (fields["sort"], fields["id"]) if col)
                        sql = f"SELECT {selected} FROM {_identifier(table)} ORDER BY {order} LIMIT ?"
                        for raw in connection.execute(sql, (MAX_IMPORT_ROWS + 1,)):
                            count += 1
                            source_count += 1
                            if count > MAX_IMPORT_ROWS:
                                raise WechatStoreError("ROW_LIMIT", "单次数据库转换最多 200000 条消息；请分批准备副本")
                            row = dict(raw)
                            conversation = _text(row.get("conversation")) or fixed_conversation
                            if not conversation:
                                raise WechatStoreError("MISSING_CONTACTS", "消息缺少会话标识；未导入")
                            combined_type = _integer(row.get("type"), default=1)
                            msg_type = combined_type & 0xFFFFFFFF
                            subtype = _integer(row.get("subtype")) or (combined_type >> 32)
                            # V4 message_content itself can be Zstandard. The
                            # compress_content column can hold unrelated extras.
                            primary_body = row.get("content")
                            body = _decode(primary_body if primary_body not in (None, b"", "") else row.get("compressed"))
                            original_body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                            sender_value = row.get("sender")
                            numeric_sender = isinstance(sender_value, (int, float)) or (isinstance(sender_value, str) and sender_value.isdigit())
                            sender = source_ids[source_name].get(_integer(sender_value), "") if numeric_sender else _text(sender_value)
                            self_flag = _integer(row.get("is_self")) == 1 if fields["is_self"] else bool(self_username and sender == self_username)
                            if conversation.endswith("@chatroom") and not self_flag and ":\n" in body[:160]:
                                head, _, rest = body.partition(":\n")
                                if re.fullmatch(r"[A-Za-z0-9_@.\-]{1,128}", head):
                                    sender, body = head, rest
                            if self_flag:
                                sender = self_username or sender
                            elif not sender and fields["is_self"] and not conversation.endswith("@chatroom"):
                                sender = conversation
                            kind, content = _message_content(body, msg_type, subtype)
                            if len(content) > MAX_MESSAGE_CHARS:
                                raise WechatStoreError("CONTENT_TOO_LARGE", "单条消息超过 20000 字符；请缩小源数据范围后重试")
                            if kind in {"image", "voice", "video", "emoji"}:
                                media_count += 1
                            if not sender and not self_flag:
                                unknown_senders += 1
                            server_id = _text(row.get("server_id"))
                            # Optional sender/contact maps must not change identity
                            # when a later import supplies better metadata.
                            fallback = json.dumps([conversation, row["id"], row.get("sort"), row["time"],
                                                   combined_type, original_body_hash], ensure_ascii=False)
                            identity = ("server:" + server_id if server_id and server_id != "0" else
                                        "local:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest())
                            message = {
                                "conversation": conversation,
                                "conversation_name": display.get(conversation, conversation),
                                "message_id": identity,
                                "sender_id": sender,
                                "sender_name": "我" if self_flag else display.get(sender, sender) or "发送者未识别",
                                "is_self": self_flag,
                                "time": row["time"],
                                "kind": kind,
                                "content": content,
                            }
                            encoded = json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n"
                            byte_count += len(encoded.encode("utf-8"))
                            if byte_count > MAX_IMPORT_BYTES:
                                raise WechatStoreError("FILE_TOO_LARGE", "转换后超过 64 兆字节；请分批准备消息库")
                            writer.write(encoded)
                source_reports.append({"source_name": source_name, "messages": source_count})
        if not message_tables or not count:
            raise WechatStoreError("NO_MESSAGES", "未找到可导入的消息；请包含 message_0.db 等消息库，而不只是联系人或会话库")
        warnings = ["仅处理本次所选数据库及已确认重放的日志范围；未选择的文件和未提交内容不在结果中。",
                    "账号归属按你提供的本人微信 ID 绑定；消息库不总含可验证的所属账号字段，无法独立证明所有文件同属一个账号。"]
        if media_count:
            warnings.append(f"{media_count} 条媒体消息以类型占位显示；可在媒体副本恢复面板单独处理所选文件，不会自动关联到会话。")
        if unknown_senders:
            warnings.append(f"{unknown_senders} 条消息的发送者未识别，已明确标记，未推断为本人或对方。")
        if not self_username:
            warnings.append("未填写本人微信 ID；没有 is_sender 字段的消息不会推断为本人发送。")
        return {"messages": count, "message_tables": message_tables, "bytes": byte_count,
                "sources": source_reports, "warnings": warnings}
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
