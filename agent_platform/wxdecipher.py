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
import sys
import threading
import time
import uuid
from collections import deque
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
DISCOVERY_TTL = 10 * 60
DISCOVERY_MAX_DIRECTORIES = 4_000
DISCOVERY_MAX_ENTRIES = 20_000
DISCOVERY_MAX_DEPTH = 8
DISCOVERY_MAX_GROUPS = 8
SESSION_ID = re.compile(r"wxdecipher-[0-9a-f]{32}\Z")
STORED_NAME = re.compile(r"db-[0-9a-f]{32}\.db\Z")
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
DISCOVERY_DATABASE_NAME = re.compile(
    r"(?:message(?:_[0-9]+)?|message_resource|contact|session)\.(?:db|sqlite|sqlite3)(?:-wal)?\Z",
    re.IGNORECASE,
)
_LOCK = threading.Lock()
_DISCOVERY_LOCK = threading.Lock()
_DISCOVERY_TOKENS: dict[str, dict] = {}


def _discovery_platform(platform_name: str | None = None) -> tuple[str, str] | None:
    selected = platform_name or sys.platform
    if selected == "win32":
        return "windows", "Windows"
    if selected == "darwin":
        return "macos", "macOS"
    return None


def _discovery_roots(platform_key: str, home: Path, environ: dict[str, str]) -> list[Path]:
    candidates: list[Path] = []
    if platform_key == "windows":
        user = Path(environ.get("USERPROFILE") or home)
        documents = [user / "Documents"]
        if environ.get("OneDrive"):
            documents.append(Path(environ["OneDrive"]) / "Documents")
        for base in documents:
            candidates.extend((base / "xwechat_files", base / "WeChat Files", base / "Weixin Files"))
        for variable in ("APPDATA", "LOCALAPPDATA"):
            if environ.get(variable):
                base = Path(environ[variable]) / "Tencent"
                candidates.extend((base / "WeChat", base / "xwechat", base / "Weixin"))
    elif platform_key == "macos":
        containers = home / "Library" / "Containers"
        for bundle in ("com.tencent.xinWeChat", "com.tencent.WeChat"):
            data = containers / bundle / "Data"
            candidates.extend((data / "Documents", data / "Library" / "Application Support" / bundle))
        candidates.extend((
            home / "Library" / "Application Support" / "com.tencent.xinWeChat",
            home / "Library" / "Application Support" / "WeChat",
        ))

    roots: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_dir() or resolved.is_symlink() or any(resolved == root or resolved.is_relative_to(root) for root in roots):
            continue
        roots.append(resolved)
    return roots


def _discovery_group(path: Path, root: Path) -> tuple[str, str]:
    relative = path.relative_to(root)
    folders = relative.parts[:-1]
    account = root.name if root.name.casefold().startswith("wxid_") else next((part for part in folders if part.casefold().startswith("wxid_")), "")
    if not account and folders:
        root_name = root.name.casefold()
        if root_name in {"xwechat_files", "wechat files", "weixin files"}:
            account = folders[0]
        elif folders[0].casefold() == "db_storage":
            account = root.name
        elif root.name.casefold() == "db_storage":
            account = root.parent.name
        else:
            lowered = [part.casefold() for part in folders]
            account = folders[lowered.index("db_storage") - 1] if "db_storage" in lowered and lowered.index("db_storage") > 0 else folders[0]
    account = account or "默认账号目录"
    visible = account if len(account) <= 18 else account[:10] + "…" + account[-4:]
    return f"{root}|{account.casefold()}", visible


def _scan_discovery_roots(roots: list[Path]) -> tuple[dict[str, dict], bool]:
    groups: dict[str, dict] = {}
    directories = entries = 0
    truncated = False
    for root in roots:
        pending: deque[tuple[Path, int]] = deque([(root, 0)])
        while pending:
            directory, depth = pending.popleft()
            try:
                if directory.resolve(strict=True) != directory or not directory.is_relative_to(root):
                    continue
            except (OSError, RuntimeError):
                continue
            directories += 1
            if directories > DISCOVERY_MAX_DIRECTORIES:
                return groups, True
            try:
                with os.scandir(directory) as children:
                    for child in children:
                        entries += 1
                        if entries > DISCOVERY_MAX_ENTRIES:
                            return groups, True
                        try:
                            if child.is_symlink():
                                continue
                            if child.is_dir(follow_symlinks=False):
                                if depth < DISCOVERY_MAX_DEPTH:
                                    pending.append((Path(child.path), depth + 1))
                                continue
                            if not child.is_file(follow_symlinks=False) or not DISCOVERY_DATABASE_NAME.fullmatch(child.name):
                                continue
                        except OSError:
                            continue
                        minimum = 0 if child.name.casefold().endswith("-wal") else 512
                        try:
                            path = Path(child.path).resolve(strict=True)
                            stat = path.stat()
                        except (OSError, RuntimeError):
                            continue
                        if not path.is_relative_to(root) or stat.st_nlink > 1 or not minimum <= stat.st_size <= MAX_DATABASE_BYTES:
                            continue
                        key, account = _discovery_group(path, root)
                        group = groups.setdefault(key, {"account": account, "root": root, "files": {}})
                        previous = group["files"].get(child.name.casefold())
                        record = {"path": path, "root": root, "name": child.name, "bytes": stat.st_size,
                                  "modified_ns": stat.st_mtime_ns, "device": stat.st_dev, "inode": stat.st_ino}
                        if previous is None or record["modified_ns"] > previous["modified_ns"]:
                            group["files"][child.name.casefold()] = record
            except OSError:
                continue
    return groups, truncated


def _discovery_file_rank(record: dict) -> tuple[int, int, str]:
    name = record["name"].casefold()
    base = name[:-4] if name.endswith("-wal") else name
    stem = Path(base).stem
    if stem in {"contact", "session", "message_resource"}:
        return 0, 0, name
    match = re.fullmatch(r"message(?:_([0-9]+))?", stem)
    return 1, int(match.group(1) or 0) if match else 0, name


def _bounded_discovery_files(files: dict[str, dict]) -> tuple[list[dict], bool]:
    main = sorted((record for name, record in files.items() if not name.endswith("-wal")), key=_discovery_file_rank)
    selected: list[dict] = []
    total = 0
    truncated = len(main) > MAX_FILES
    for record in main[:MAX_FILES]:
        wal = files.get(record["name"].casefold() + "-wal")
        pair = [record, *([wal] if wal else [])]
        if total + sum(item["bytes"] for item in pair) > MAX_SESSION_BYTES:
            truncated = True
            continue
        selected.extend(pair)
        total += sum(item["bytes"] for item in pair)
    return selected, truncated


def _purge_discovery_tokens(now_value: float) -> None:
    for token, record in list(_DISCOVERY_TOKENS.items()):
        if now_value - record["created_at"] >= DISCOVERY_TTL:
            _DISCOVERY_TOKENS.pop(token, None)


def _manual_discovery_root(value: object) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(char) < 32 for char in value):
        raise WechatStoreError("INVALID_DIRECTORY", "请输入微信账号文件夹的完整路径")
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    # Do not turn a pasted path into shell code or expand environment variables.
    if value.startswith(("\\\\", "//")):
        raise WechatStoreError("INVALID_DIRECTORY", "请选择本机微信账号文件夹，不支持网络或设备路径")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WechatStoreError("INVALID_DIRECTORY", "请输入当前系统下的完整目录路径")
    try:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise WechatStoreError("INVALID_DIRECTORY", "请选择实际文件夹，不支持链接目录")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise WechatStoreError("INVALID_DIRECTORY", "请填写文件夹位置，而不是数据库文件")
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise WechatStoreError("INVALID_DIRECTORY", "请指定微信账号文件夹，而不是磁盘或用户根目录")
        with os.scandir(resolved):
            pass
    except (OSError, RuntimeError):
        raise WechatStoreError("INVALID_DIRECTORY", "目录不存在或无法访问，请检查路径和目录权限") from None
    return resolved


def discover_databases(payload: dict, *, platform_name: str | None = None, home: Path | None = None,
                       environ: dict[str, str] | None = None) -> dict:
    if payload.get("ownership_confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请先确认这是本人账号且有权检索本机微信数据库")
    platform = _discovery_platform(platform_name)
    if platform is None:
        raise WechatStoreError("DISCOVERY_UNSUPPORTED", "当前平台暂不支持自动检索，请使用手动检索")
    platform_key, platform_label = platform
    manual = "directory" in payload
    roots = ([_manual_discovery_root(payload["directory"])] if manual else
             _discovery_roots(platform_key, Path(home or Path.home()), dict(os.environ if environ is None else environ)))
    groups, scan_truncated = _scan_discovery_roots(roots)
    prepared: list[tuple[dict, list[dict], bool]] = []
    for group in groups.values():
        selected, files_truncated = _bounded_discovery_files(group["files"])
        if selected:
            prepared.append((group, selected, files_truncated))
    prepared.sort(key=lambda item: max(record["modified_ns"] for record in item[1]), reverse=True)
    if len(prepared) > DISCOVERY_MAX_GROUPS:
        scan_truncated = True
        prepared = prepared[:DISCOVERY_MAX_GROUPS]

    response_groups: list[dict] = []
    now_value = time.time()
    with _DISCOVERY_LOCK:
        _purge_discovery_tokens(now_value)
        for group, records, files_truncated in prepared:
            group_id = secrets.token_urlsafe(18)
            visible_files: list[dict] = []
            for record in records:
                token = secrets.token_urlsafe(24)
                _DISCOVERY_TOKENS[token] = {**record, "created_at": now_value, "group_id": group_id, "in_use": False}
                visible_files.append({"candidate_id": token, "name": record["name"], "bytes": record["bytes"],
                                      "modified_at": record["modified_ns"] // 1_000_000})
            response_groups.append({
                "group_id": group_id,
                "label": f"{platform_label} · {group['account']}",
                "files": visible_files,
                "truncated": files_truncated,
            })
    location = "指定目录及其子目录" if manual else f"{platform_label} 常见微信目录"
    message = (f"已在{location}中找到 {len(response_groups)} 组数据库，请选择后添加。"
               if response_groups else f"未在{location}中找到兼容数据库，请检查目录或直接选择数据库文件。")
    return {"platform": platform_key, "platform_label": platform_label, "groups": response_groups,
            "scan_truncated": scan_truncated, "message": message, "expires_in_seconds": DISCOVERY_TTL}


def import_discovered_databases(project_root: Path | str, payload: dict) -> dict:
    if payload.get("ownership_confirmed") is not True or payload.get("snapshot_confirmed") is not True:
        raise WechatStoreError("AUTHORIZATION_REQUIRED", "请确认本人账号、有权处理，且微信已退出或数据库文件在复制期间保持静止")
    candidate_ids = payload.get("candidate_ids")
    if (not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= MAX_UPLOADS
            or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{32}", item) for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)):
        raise WechatStoreError("INVALID_INPUT", "自动检索结果无效，请重新检索")
    now_value = time.time()
    with _DISCOVERY_LOCK:
        _purge_discovery_tokens(now_value)
        try:
            records = [_DISCOVERY_TOKENS[token] for token in candidate_ids]
        except KeyError:
            raise WechatStoreError("DISCOVERY_EXPIRED", "自动检索结果已过期，请重新检索") from None
        if len({record["group_id"] for record in records}) != 1 or any(record["in_use"] for record in records):
            raise WechatStoreError("INVALID_INPUT", "请选择同一微信账号目录中的数据库并重新检索")
        for record in records:
            record["in_use"] = True

    session_id = payload.get("session_id", "")
    imported: list[dict] = []
    try:
        for record in records:
            path: Path = record["path"]
            root: Path = record["root"]
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise WechatStoreError("SOURCE_CHANGED", "自动检索到的数据库位置已变化，请重新检索")
            with path.open("rb") as reader:
                before = os.fstat(reader.fileno())
                expected = (record["bytes"], record["modified_ns"], record["device"], record["inode"])
                actual = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
                if actual != expected or before.st_nlink > 1:
                    raise WechatStoreError("SOURCE_CHANGED", "自动检索到的数据库已变化，请退出微信后重新检索")
                imported.append(upload_database(project_root, session_id, record["name"], reader, before.st_size))
                after = os.fstat(reader.fileno())
                if (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino) != expected:
                    raise WechatStoreError("SOURCE_CHANGED", "数据库在复制期间发生变化，请退出微信后重新检索")
    except BaseException:
        try:
            _discard_incomplete_session(project_root, session_id)
        except WechatStoreError:
            pass
        with _DISCOVERY_LOCK:
            for token in candidate_ids:
                if token in _DISCOVERY_TOKENS:
                    _DISCOVERY_TOKENS[token]["in_use"] = False
        raise
    with _DISCOVERY_LOCK:
        for token in candidate_ids:
            if token in _DISCOVERY_TOKENS:
                _DISCOVERY_TOKENS[token]["in_use"] = False
    return {"file_count": len(imported), "bytes": sum(item["bytes"] for item in imported),
            "message": "已将自动检索的数据库复制到本次私有暂存；原文件未修改"}


def capabilities() -> dict:
    crypto_available = importlib.util.find_spec("Crypto") is not None
    discovery_platform = _discovery_platform()
    return {
        "available": True, "decrypt_available": crypto_available,
        "zstandard_available": importlib.util.find_spec("zstandard") is not None,
        "max_file_bytes": MAX_DATABASE_BYTES, "max_files": MAX_FILES,
        "cipher_modes": list(CIPHER_MODES), "key_capture": wxdecipher_capture.available(), "key_storage": False,
        "key_capture_experimental": True,
        "media_restore": importlib.util.find_spec("PIL") is not None, "wal_replay": True,
        "max_uploads": MAX_UPLOADS, "max_media_bytes": 16 * 1024 * 1024,
        "database_discovery": {
            "available": discovery_platform is not None,
            "platform": discovery_platform[0] if discovery_platform else "unsupported",
            "platform_label": discovery_platform[1] if discovery_platform else "当前平台",
            "manual_available": True,
        },
    }


def _root(project_root: Path | str, *, create: bool = False) -> Path:
    from .wechat_privacy import ensure_private_directory, verify_private_directory
    from .wechat_storage import enrolled_root
    project = Path(project_root).resolve()
    if not project.is_dir():
        raise WechatStoreError("INVALID_ROOT", "应用目录不存在")
    enrolled = enrolled_root(project)
    current = enrolled if enrolled is not None else project
    for part in (("decipher",) if enrolled is not None else ("data", "wechat", "decipher")):
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
        if any(not isinstance(data.get(key, False), bool) for key in ("retain_copies", "copy_ready", "cleanup_pending")):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError) as error:
        raise WechatStoreError("INVALID_SESSION", "数据库导入会话记录损坏，请清除暂存后重新导入") from error


def _save_session(path: Path, data: dict) -> None:
    from .wechat_privacy import create_private_file

    temporary = path / ("manifest-" + uuid.uuid4().hex + ".tmp")
    created = False
    try:
        create_private_file(temporary)
        created = True
        with temporary.open("w", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path / "session.json")
        created = False
    finally:
        if created:
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
            data = _read_session(path)
            created = data["created_at"]
            if now - created < SESSION_TTL and data.get("cleanup_pending"):
                try:
                    _clear_parse_outputs(path, data)
                except WechatStoreError:
                    pass  # Retry on the next access; parsing remains gated.
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
                                 "self_username": self_username, "files": [],
                                 "retain_copies": payload.get("retain_copies") is True, "copy_ready": False})
        except BaseException:
            _remove_session(path)
            raise
    return {"session_id": session_id, "expires_in_seconds": SESSION_TTL, "working_directory": str(path)}


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
    from .wechat_privacy import create_private_file

    filename = _source_name(filename)
    minimum = 0 if filename.lower().endswith("-wal") else 512
    if not minimum <= length <= MAX_DATABASE_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "主库最少 512 字节；单个 DB/WAL 不得超过 256 兆字节")
    with _locked():
        _cleanup_unlocked(project_root)
        path = _session_path(project_root, session_id)
        data = _read_session(path)
        if data.get("copy_ready"):
            raise WechatStoreError("COPY_SEALED", "本批副本已封存，不能追加文件；请清除后重新复制")
        if len(data["files"]) >= MAX_UPLOADS or sum(entry["bytes"] for entry in data["files"]) + length > MAX_SESSION_BYTES:
            raise WechatStoreError("FILE_TOO_LARGE", "每次最多 16 个主库及其 16 个 WAL，合计不超过 1 吉字节")
        if any(entry["source_name"].casefold() == filename.casefold() for entry in data["files"]):
            raise WechatStoreError("DUPLICATE_FILE", "本批次已有同名数据库，请勿重复选择或混合多个账号")
        stored_name = "db-" + uuid.uuid4().hex + ".db"
        target = path / stored_name
        digest = hashlib.sha256()
        received = 0
        created = False
        try:
            create_private_file(target)
            created = True
            with target.open("wb") as writer:
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
            if created:
                target.unlink(missing_ok=True)
            raise
    return {"source_name": filename, "bytes": received, "file_count": len(data["files"])}


def discard_session(project_root: Path | str, session_id: str) -> dict:
    with _locked():
        _remove_session(_session_path(project_root, session_id))
    return {"message": "已移除本次数据库暂存副本；原始文件未改动"}


def _discard_incomplete_session(project_root: Path | str, session_id: str) -> None:
    # A failed/replayed upload must never erase a sealed retryable batch.
    with _locked():
        path = _session_path(project_root, session_id)
        data = _read_session(path)
        if not data.get("copy_ready"):
            _remove_session(path)


def finish_copy(project_root: Path | str, payload: dict) -> dict:
    """Seal a complete copied batch; parsing can now retry without its sources."""
    with _locked():
        _cleanup_unlocked(project_root)
        path = _session_path(project_root, payload.get("session_id", ""))
        data = _read_session(path)
        if not data.get("retain_copies"):
            raise WechatStoreError("INVALID_SESSION", "本会话不是独立复制批次")
        files = data["files"]
        if not files or type(payload.get("file_count")) is not int or payload["file_count"] != len(files):
            raise WechatStoreError("COPY_INCOMPLETE", "本批文件尚未全部复制完成，不能解析")
        names = {entry["source_name"].casefold() for entry in files}
        main = {name for name in names if not name.endswith("-wal")}
        if not main or len(main) > MAX_FILES or any(name[:-4] not in main for name in names if name.endswith("-wal")):
            raise WechatStoreError("WAL_MISMATCH", "主库与 WAL 未正确配对，不能完成复制")
        for entry in files:
            source = path / entry["stored_name"]
            if not _ordinary_file(source) or source.stat().st_size != entry["bytes"]:
                raise WechatStoreError("FILE_CHANGED", "数据库副本已变化，请重新复制")
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise WechatStoreError("FILE_CHANGED", "数据库副本校验失败，请重新复制")
        data["copy_ready"] = True
        _save_session(path, data)
        return {"session_id": path.name, "working_directory": str(path), "file_count": len(files),
                "expires_in_seconds": max(0, int(SESSION_TTL - (time.time() - data["created_at"]))),
                "message": "数据库复制完成。可以单独解析；本批副本固定保留至复制会话创建后 30 分钟。"}


def _clear_parse_outputs(path: Path, data: dict, *, clear_consent: bool = True) -> None:
    """Retain only copied DB/WAL inputs, never keys or derived plaintext."""
    keep = {"session.json", *(entry["stored_name"] for entry in data["files"])}
    failed = False
    try:
        unwanted = [child for child in path.iterdir() if child.name not in keep]
    except OSError:
        unwanted = []
        failed = True
    for child in unwanted:
        try:
            if not _ordinary_file(child):
                failed = True
                continue
            child.unlink()
        except (OSError, WechatStoreError):
            failed = True
    if clear_consent:
        data.pop("capture_consent", None)
        data["cleanup_pending"] = failed
        try:
            _save_session(path, data)
        except (OSError, WechatStoreError):
            failed = True
    if failed:
        raise WechatStoreError("CLEANUP_PENDING", "部分解析输出尚未清理，已暂停解析；下次访问会重试清理，也可主动清除整批副本")


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
        if data.get("retain_copies") and not data.get("copy_ready"):
            raise WechatStoreError("COPY_INCOMPLETE", "请先完成整批数据库复制，再申请取钥")
        ticket = secrets.token_urlsafe(32)
        data["capture_consent"] = {"sha256": hashlib.sha256(ticket.encode("ascii")).hexdigest(),
                                   "process_id": pid, "created_at": created, "issued_at": time.time(),
                                   "files_sha256": _capture_file_binding(data)}
        _save_session(path, data)
        return {"consent_token": ticket, "expires_in_seconds": CAPTURE_CONSENT_TTL}


def _consume_capture_consent(path: Path, data: dict, payload: object) -> None:
    # Caller holds the session lock. Consume before any process is opened,
    # including invalid attempts; no consent survives a completed parse attempt.
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
        if data.get("retain_copies") and not data.get("copy_ready"):
            raise WechatStoreError("COPY_INCOMPLETE", "请先完成数据库复制，再单独解析")
        response = None
        parse_error = None
        try:
            if data.get("retain_copies"):
                _clear_parse_outputs(path, data, clear_consent=False)
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
            response = {**result, "decipher": {"databases": reports, "key_capture": capture_report, **conversion},
                    "message": result["message"] + " 密钥和解密输出不保留。" +
                    ("本批复制副本保留至到期，也可主动清除。" if data.get("retain_copies") else "数据库暂存已清除。")}
            return response
        except BaseException as error:
            parse_error = error
            raise
        finally:
            if data.get("retain_copies"):
                try:
                    _clear_parse_outputs(path, data)
                except WechatStoreError as cleanup_error:
                    if response is not None:
                        response["cleanup_pending"] = True
                        response["message"] = result["message"] + " " + str(cleanup_error)
                    elif isinstance(parse_error, WechatStoreError):
                        parse_error.args = (str(parse_error) + " " + str(cleanup_error),)
                    elif parse_error is None:
                        raise
            else:
                _remove_session(path)
