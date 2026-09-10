"""Workbench-owned upload/decode sessions for WXDecipher's local workflow.

All database and plaintext staging is isolated from model tools and removed on
completion/failure. Interrupted sessions expire after 30 minutes, at next access.
Keys are request-local only; media and WAL never enter model tools.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .wechat_store import WechatStoreError, import_export
from .wxdecipher_crypto import CIPHER_MODES, MAX_DATABASE_BYTES, decrypt_database
from .wxdecipher_reader import convert_databases
from .wxdecipher_wal import replay_wal
from . import wxdecipher_capture


MAX_FILES = 16
MAX_UPLOADS = MAX_FILES * 2
MAX_SESSION_BYTES = 1024 * 1024 * 1024
SESSION_TTL = 30 * 60
CAPTURE_CONSENT_TTL = 60
SESSION_ID = re.compile(r"wxdecipher-[0-9a-f]{32}\Z")
STORED_NAME = re.compile(r"db-[0-9a-f]{32}\.db\Z")
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_LOCK = threading.Lock()


def capabilities() -> dict:
    crypto_available = importlib.util.find_spec("Crypto") is not None
    return {
        "available": True, "decrypt_available": crypto_available,
        "zstandard_available": importlib.util.find_spec("zstandard") is not None,
        "max_file_bytes": MAX_DATABASE_BYTES, "max_files": MAX_FILES,
        "cipher_modes": list(CIPHER_MODES), "key_capture": wxdecipher_capture.available(), "key_storage": False,
        "key_capture_experimental": True,
        "media_restore": importlib.util.find_spec("PIL") is not None, "wal_replay": True,
        "max_uploads": MAX_UPLOADS, "max_media_bytes": 16 * 1024 * 1024,
    }


def _root(project_root: Path | str, *, create: bool = False) -> Path:
    from .wechat_privacy import ensure_private_directory, verify_private_directory
    project = Path(project_root).resolve()
    if not project.is_dir():
        raise WechatStoreError("INVALID_ROOT", "应用目录不存在")
    current = project
    for part in ("data", "wechat", "decipher"):
        current = current / part
        if current.is_symlink() or (current.exists() and (not current.is_dir() or current.resolve() != current.absolute())):
            raise WechatStoreError("UNSAFE_PATH", "数据库暂存目录必须是应用内的普通目录")
        if part == "data":
            if create:
                current.mkdir(exist_ok=True)
        elif current.exists():
            verify_private_directory(current)
        elif create:
            ensure_private_directory(current)
    return current


def _session_path(project_root: Path | str, session_id: str) -> Path:
    from .wechat_privacy import verify_private_directory
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        raise WechatStoreError("INVALID_ID", "数据库导入会话编号无效")
    parent = _root(project_root)
    selected = parent / session_id
    if selected.is_symlink() or (selected.exists() and (not selected.is_dir() or selected.resolve().parent != parent.resolve())):
        raise WechatStoreError("UNSAFE_PATH", "数据库导入会话路径无效")
    if selected.exists():
        verify_private_directory(selected)
    return selected


def _ordinary_file(path: Path) -> bool:
    from .wechat_privacy import verify_private_file
    ordinary = path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
    if ordinary:
        verify_private_file(path)
    return ordinary


def _read_session(path: Path) -> dict:
    manifest = path / "session.json"
    if not _ordinary_file(manifest):
        raise WechatStoreError("NOT_FOUND", "数据库导入会话已结束或到期，请重新选择文件")
    try:
        if manifest.stat().st_size > 32768:
            raise ValueError()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data["created_at"], (int, float)):
            raise ValueError()
        files = data["files"]
        if not isinstance(files, list) or len(files) > MAX_UPLOADS:
            raise ValueError()
        for entry in files:
            if (not isinstance(entry, dict) or not STORED_NAME.fullmatch(entry["stored_name"])
                    or not isinstance(entry["bytes"], int) or not 0 <= entry["bytes"] <= MAX_DATABASE_BYTES
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ValueError()
            _source_name(entry["source_name"])
        if not all(isinstance(data.get(key), str) for key in ("account_label", "self_username")):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError) as error:
        raise WechatStoreError("INVALID_SESSION", "数据库导入会话记录损坏，请清除暂存后重新导入") from error


def _save_session(path: Path, data: dict) -> None:
    temporary = path / ("manifest-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path / "session.json")
    finally:
        temporary.unlink(missing_ok=True)


def _remove_session(path: Path) -> None:
    # This function only deletes a freshly validated, UUID-named app session.
    # Refuse nested links/junctions instead of traversing arbitrary targets.
    if not path.exists():
        return
    if not SESSION_ID.fullmatch(path.name) or path.is_symlink() or path.resolve() != path.absolute():
        raise WechatStoreError("UNSAFE_PATH", "不能清理越界的暂存目录")
    for child in path.iterdir():
        if not _ordinary_file(child):
            raise WechatStoreError("UNSAFE_PATH", "暂存目录含非普通文件，已停止清理")
    shutil.rmtree(path)


def _cleanup_unlocked(project_root: Path | str) -> int:
    parent = _root(project_root)
    if not parent.exists():
        return 0
    removed = 0
    now = time.time()
    for path in parent.iterdir():
        if not SESSION_ID.fullmatch(path.name):
            continue
        path = _session_path(project_root, path.name)
        try:
            created = _read_session(path)["created_at"]
        except WechatStoreError:
            created = path.stat().st_mtime
        if now - created >= SESSION_TTL:
            _remove_session(path)
            removed += 1
    return removed


def cleanup_sessions(project_root: Path | str) -> int:
    if not _LOCK.acquire(blocking=False):
        return 0
    try:
        return _cleanup_unlocked(project_root)
    finally:
        _LOCK.release()


@contextmanager
def _locked() -> Iterator[None]:
    if not _LOCK.acquire(blocking=False):
        raise WechatStoreError("DECIPHER_BUSY", "另一个数据库导入正在处理，请稍后重试")
    try:
        yield
    finally:
        _LOCK.release()


def _source_name(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 120
            or value in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value[-1] in {".", " "}):
        raise WechatStoreError("INVALID_FILE", "数据库文件名无效；不能包含目录")
    database_name = value[:-4] if value.lower().endswith("-wal") else value
    if Path(database_name).suffix.lower() not in DATABASE_SUFFIXES:
        raise WechatStoreError("UNSUPPORTED_FORMAT", "请选择 .db、.sqlite、.sqlite3 副本或同名 -wal；不支持 SHM/回滚日志")
    return value


def create_session(project_root: Path | str, payload: dict) -> dict:
    from .wechat_privacy import ensure_private_directory
    if payload.get("ownership_confirmed") is not True or payload.get("snapshot_confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请确认本人账号、有权处理，且所选文件为同一账号的完整静态数据库副本")
    account = str(payload.get("account_label") or "本机微信").strip()
    self_username = str(payload.get("self_username") or "").strip()
    if not 1 <= len(account) <= 120 or not re.fullmatch(r"[\w.@-]{1,128}", self_username):
        raise WechatStoreError("INVALID_INPUT", "账号标记或本人微信 ID 格式无效")
    with _locked():
        parent = _root(project_root, create=True)
        _cleanup_unlocked(project_root)
        if sum(1 for path in parent.iterdir() if SESSION_ID.fullmatch(path.name)) >= 2:
            raise WechatStoreError("SESSION_LIMIT", "已有未结束的数据库导入；请取消或等待暂存到期后重试")
        session_id = "wxdecipher-" + uuid.uuid4().hex
        path = parent / session_id
        ensure_private_directory(path)
        try:
            _save_session(path, {"created_at": time.time(), "account_label": account,
                                 "self_username": self_username, "files": []})
        except BaseException:
            _remove_session(path)
            raise
    return {"session_id": session_id, "expires_in_seconds": SESSION_TTL}


@contextmanager
def structured_upload_directory(project_root: Path | str) -> Iterator[Path]:
    """Give the legacy structured-upload endpoint the same private staging.

    No conversation data goes to the global OS temp directory. Interrupted
    structured uploads use the existing 30-minute session cleanup rules.
    """
    from .wechat_privacy import ensure_private_directory
    with _locked():
        parent = _root(project_root, create=True)
        _cleanup_unlocked(project_root)
        if sum(1 for path in parent.iterdir() if SESSION_ID.fullmatch(path.name)) >= 2:
            raise WechatStoreError("SESSION_LIMIT", "已有未结束的微信导入，请先结束或等待暂存到期")
        path = parent / ("wxdecipher-" + uuid.uuid4().hex)
        ensure_private_directory(path)
        try:
            _save_session(path, {"created_at": time.time(), "account_label": "", "self_username": "", "files": []})
            yield path
        finally:
            _remove_session(path)


def upload_database(project_root: Path | str, session_id: str, filename: str, stream: BinaryIO, length: int) -> dict:
    filename = _source_name(filename)
    minimum = 0 if filename.lower().endswith("-wal") else 512
    if not minimum <= length <= MAX_DATABASE_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "主库最少 512 字节；单个 DB/WAL 不得超过 256 兆字节")
    with _locked():
        _cleanup_unlocked(project_root)
        path = _session_path(project_root, session_id)
        data = _read_session(path)
        if len(data["files"]) >= MAX_UPLOADS or sum(entry["bytes"] for entry in data["files"]) + length > MAX_SESSION_BYTES:
            raise WechatStoreError("FILE_TOO_LARGE", "每次最多 16 个主库及其 16 个 WAL，合计不超过 1 吉字节")
        if any(entry["source_name"].casefold() == filename.casefold() for entry in data["files"]):
            raise WechatStoreError("DUPLICATE_FILE", "本批次已有同名数据库，请勿重复选择或混合多个账号")
        stored_name = "db-" + uuid.uuid4().hex + ".db"
        target = path / stored_name
        digest = hashlib.sha256()
        received = 0
        try:
            with target.open("xb") as writer:
                while received < length:
                    block = stream.read(min(1024 * 1024, length - received))
                    if not block:
                        raise WechatStoreError("INVALID_FILE", "数据库上传提前中断；未导入")
                    if len(block) > length - received:
                        raise WechatStoreError("INVALID_FILE", "数据库上传长度不一致")
                    writer.write(block)
                    digest.update(block)
                    received += len(block)
                writer.flush()
                os.fsync(writer.fileno())
            data["files"].append({"source_name": filename, "stored_name": stored_name,
                                  "bytes": received, "sha256": digest.hexdigest()})
            _save_session(path, data)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return {"source_name": filename, "bytes": received, "file_count": len(data["files"])}


def discard_session(project_root: Path | str, session_id: str) -> dict:
    with _locked():
        _remove_session(_session_path(project_root, session_id))
    return {"message": "已移除本次数据库暂存副本；原始文件未改动"}


def _capture_file_binding(data: dict) -> str:
    value = [(entry["source_name"], entry["bytes"], entry["sha256"]) for entry in data["files"]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


def issue_capture_consent(project_root: Path | str, payload: dict) -> dict:
    """Mint a short-lived ticket only AFTER the user's selected files upload.

    HTTP authentication is mandatory at the route boundary. This ticket binds
    that confirmation to one session, exact inputs and process instance; it is
    not a replacement for caller authentication and contains no database key.
    """
    if payload.get("confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请明确确认本次自动取钥")
    pid, created = wxdecipher_capture._validate_identity(payload.get("process_id"), payload.get("created_at"))
    with _locked():
        _cleanup_unlocked(project_root)
        path = _session_path(project_root, payload.get("session_id", ""))
        data = _read_session(path)
        if not data["files"]:
            raise WechatStoreError("NO_DATABASES", "请先上传所选数据库，再确认本次取钥")
        ticket = secrets.token_urlsafe(32)
        data["capture_consent"] = {"sha256": hashlib.sha256(ticket.encode("ascii")).hexdigest(),
                                   "process_id": pid, "created_at": created, "issued_at": time.time(),
                                   "files_sha256": _capture_file_binding(data)}
        _save_session(path, data)
        return {"consent_token": ticket, "expires_in_seconds": CAPTURE_CONSENT_TTL}


def _consume_capture_consent(path: Path, data: dict, payload: object) -> None:
    # Caller holds the session lock. Consume before any process is opened,
    # including invalid attempts; the whole session is removed on run exit.
    consent = data.pop("capture_consent", None)
    _save_session(path, data)
    try:
        if not isinstance(payload, dict) or payload.get("confirmed") is not True or not isinstance(consent, dict):
            raise ValueError()
        ticket = payload.get("consent_token")
        if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket):
            raise ValueError()
        pid, created = wxdecipher_capture._validate_identity(payload.get("process_id"), payload.get("created_at"))
        age = time.time() - consent["issued_at"]
        if (not 0 <= age <= CAPTURE_CONSENT_TTL or pid != consent["process_id"] or created != consent["created_at"]
                or consent["files_sha256"] != _capture_file_binding(data)
                or not secrets.compare_digest(hashlib.sha256(ticket.encode("ascii")).hexdigest(), consent["sha256"])):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise WechatStoreError("CAPTURE_CONSENT_REQUIRED", "本次取钥确认无效、已使用或超过 60 秒，请重新选择文件和进程并确认") from None


def run_session(project_root: Path | str, payload: dict) -> dict:
    mode = payload.get("cipher_mode", "auto")
    if mode not in CIPHER_MODES:
        raise WechatStoreError("INVALID_CIPHER", "解密模式无效")
    key = payload.get("key", "")
    if not isinstance(key, str) or len(key) > 128:
        raise WechatStoreError("KEY_FORMAT", "密钥格式无效")
    file_keys = payload.get("file_keys", {})
    if (not isinstance(file_keys, dict) or len(file_keys) > MAX_FILES
            or not all(isinstance(name, str) and isinstance(value, str) and len(value) <= 128
                       for name, value in file_keys.items())):
        raise WechatStoreError("KEY_FORMAT", "逐库密钥格式无效")
    with _locked():
        _cleanup_unlocked(project_root)
        path = _session_path(project_root, payload.get("session_id", ""))
        data = _read_session(path)
        try:
            if not data["files"]:
                raise WechatStoreError("NO_DATABASES", "尚未选择数据库")
            databases = [entry for entry in data["files"] if not entry["source_name"].lower().endswith("-wal")]
            wals = {entry["source_name"][:-4].casefold(): entry for entry in data["files"] if entry["source_name"].lower().endswith("-wal")}
            if not databases or len(databases) > MAX_FILES:
                raise WechatStoreError("NO_DATABASES", "每次需选择 1–16 个主数据库副本")
            if set(wals) - {entry["source_name"].casefold() for entry in databases}:
                raise WechatStoreError("WAL_MISMATCH", "每个 WAL 都须有同批次同名主库（例如 message_0.db 与 message_0.db-wal）")
            if wals and payload.get("wal_replay_confirmed") is not True:
                raise WechatStoreError("AUTHORIZATION_REQUIRED", "请确认 DB/WAL 为同一时点的配对静态副本，再启用本地 WAL 重放")
            if set(file_keys) - {entry["source_name"] for entry in databases}:
                raise WechatStoreError("KEY_FORMAT", "逐库密钥必须对应本次选中的文件")
            for entry in data["files"]:
                source = path / entry["stored_name"]
                if not _ordinary_file(source) or source.stat().st_size != entry["bytes"]:
                    raise WechatStoreError("INVALID_FILE", "数据库暂存副本已变化；请重新导入")
                with source.open("rb") as reader:
                    if hashlib.file_digest(reader, "sha256").hexdigest() != entry["sha256"]:
                        raise WechatStoreError("FILE_CHANGED", "数据库暂存副本已变化；请重新导入")
            plaintext = []
            reports = []
            wal_warnings = []
            captured_keys = {}
            capture_report = None
            if payload.get("auto_capture") is not None:
                _consume_capture_consent(path, data, payload["auto_capture"])
                missing_keys = {entry["source_name"]: path / entry["stored_name"] for entry in databases
                                if not (file_keys.get(entry["source_name"]) or key)}
                captured_keys, capture_report = wxdecipher_capture.capture_keys(missing_keys, payload["auto_capture"])
            for index, entry in enumerate(databases):
                source = path / entry["stored_name"]
                wal_report = None
                if entry["source_name"].casefold() in wals:
                    merged = path / f"merged-{index}.db"
                    wal_report = replay_wal(source, path / wals[entry["source_name"].casefold()]["stored_name"], merged)
                    wal_warnings.extend(f"{entry['source_name']}：{warning}" for warning in wal_report["warnings"])
                    source = merged
                destination = path / f"plain-{index}.db"
                selected_key = file_keys.get(entry["source_name"]) or key or captured_keys.get(entry["source_name"], "")
                selected_mode = "sqlcipher4-raw" if entry["source_name"] in captured_keys else mode
                report = decrypt_database(source, destination, selected_key, selected_mode)
                if wal_report is not None:
                    report["wal"] = {**wal_report, "verified": True}
                reports.append({"source_name": entry["source_name"], **report})
                plaintext.append((entry["source_name"], destination))
            structured = path / "messages.jsonl"
            conversion = convert_databases(plaintext, structured, self_username=data["self_username"])
            conversion["warnings"] = wal_warnings + conversion.get("warnings", [])
            result = import_export(
                project_root, structured, source_name="WXDecipher-messages.jsonl",
                account_label=data["account_label"], ownership_confirmed=True,
                account_id=data["self_username"],
                retention_days=7, auto_cleanup=True,
            )
            return {**result, "decipher": {"databases": reports, "key_capture": capture_report, **conversion},
                    "message": result["message"] + " 数据库副本已全部校验，密钥和解密暂存不保留。"}
        finally:
            # Do not retain bad-key retries or plaintext after schema/import errors.
            _remove_session(path)
