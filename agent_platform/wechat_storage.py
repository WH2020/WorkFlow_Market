"""Explicit, per-project enrollment of a current-user private WeChat store.

No source databases, existing stores, parent ACLs or elevation are touched.
The fixed-location private marker is the sole authority for opting in.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time

from . import wechat_privacy as privacy

_LOCK = threading.RLock()
_PLANS: dict[str, dict] = {}
_TTL = 120


def _error(code: str, message: str) -> Exception:
    from .wechat_store import WechatStoreError
    return WechatStoreError(code, message)


def _private_base() -> Path:
    return Path.home() / "Agent4Market-PrivateData"


def _locations(project_root: Path | str) -> tuple[Path, Path, str]:
    project = Path(project_root).resolve(strict=True)
    identity = hashlib.sha256(os.path.normcase(str(project)).encode("utf-8")).hexdigest()
    base = _private_base()
    return base, base / identity, identity


def _verify_existing(path: Path) -> None:
    # Unlike ensure_private_directory, this must never repair an existing ACL.
    privacy.verify_private_directory(path)


def _selected_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise _error("INVALID_DIRECTORY", "请填写本机专用数据目录的完整路径")
    raw = value.strip().strip('"')
    try:
        selected = privacy._normalise_path(Path(raw).expanduser())
    except (RuntimeError, ValueError, OSError):
        raise _error("INVALID_DIRECTORY", "请选择本机绝对路径，不支持盘符根目录、网络路径或路径别名") from None
    if selected.is_symlink() or selected.resolve() != selected.absolute():
        raise _error("UNSAFE_PATH", "专用数据目录不能包含链接或重定向路径")
    if not selected.parent.is_dir():
        raise _error("INVALID_DIRECTORY", "上级文件夹不存在，请先选择已有的上级文件夹，再填写专用子目录名称")
    return selected


def _require_empty(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _verify_existing(path)
        if next(path.iterdir(), None) is not None:
            raise _error("STORAGE_MIGRATION_REQUIRED", "目录已有数据或未结束的导入，不能自动切换或覆盖；请选择空的专用目录，并另行处理旧数据")


def _require_inactive_store(path: Path) -> None:
    _verify_existing(path)
    # Failed/finished sessions can leave these empty scaffolding directories.
    # Keep them in place, but do not mistake them for retained user data.
    for child in path.iterdir():
        if child.name not in {"decipher", "imports"}:
            raise _error("STORAGE_MIGRATION_REQUIRED", "当前专用目录已有数据，不能自动切换；本次未迁移或删除数据")
        _require_empty(child)


def enrolled_root(project_root: Path | str) -> Path | None:
    base, target, identity = _locations(project_root)
    with _LOCK:
        if not base.exists() and not base.is_symlink():
            return None
        if not target.exists() and not target.is_symlink():
            return None
        # The container holds no records itself. Verify the private project
        # directory and its entire safe ancestor chain, not an owner-only
        # policy on the shared container (read-only traversal is harmless).
        _verify_existing(target)
        marker = target / "storage.json"
        if not marker.exists() and not marker.is_symlink():
            return None
        with privacy.hold_private_file(marker):
            if marker.stat().st_size > 32768:
                raise _error("UNSAFE_PATH", "私有数据目录授权记录无效")
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise _error("UNSAFE_PATH", "私有数据目录授权记录无效") from None
        if value == {"schema_version": 1, "project_id": identity}:
            selected = target / "wechat"
        elif (isinstance(value, dict) and set(value) == {"schema_version", "project_id", "directory"}
              and value["schema_version"] == 2 and value["project_id"] == identity):
            selected = _selected_path(value["directory"])
        else:
            raise _error("UNSAFE_PATH", "私有数据目录授权记录不匹配")
        _verify_existing(selected)
        return selected


def _check_legacy_empty(project_root: Path | str) -> None:
    data = Path(project_root).resolve() / "data"
    legacy = data / "wechat"
    for path in (data, legacy):
        if path.is_symlink() or (path.exists() and path.resolve() != path.absolute()):
            raise _error("STORAGE_MIGRATION_REQUIRED", "原数据位置包含链接，需先人工核对，不能自动切换")
    try:
        if legacy.exists() and (not legacy.is_dir() or next(legacy.iterdir(), None) is not None):
            raise _error("STORAGE_MIGRATION_REQUIRED", "原目录已有微信数据或未结束的导入，请先处理原数据后再切换；本次未迁移或删除数据")
    except OSError:
        raise _error("STORAGE_MIGRATION_REQUIRED", "无法确认原微信数据目录是否为空，请先检查原目录权限") from None


def permission_plan(project_root: Path | str, payload: dict | None = None) -> dict:
    with _LOCK:
        active = enrolled_root(project_root)
        base, target, identity = _locations(project_root)
        directory = (payload or {}).get("directory", "")
        if not isinstance(directory, str):
            raise _error("INVALID_DIRECTORY", "专用数据目录必须是完整路径文本")
        selected = _selected_path(directory) if directory else active or target / "wechat"
        if active is not None and selected == active:
            return {"configured": True, "directory": str(active), "message": "当前用户专用数据目录已启用"}
        _check_legacy_empty(project_root)
        if active is not None:
            _require_inactive_store(active)
        if selected != target / "wechat" and (selected == target or selected in target.parents or target in selected.parents):
            raise _error("INVALID_DIRECTORY", "请选择授权配置目录之外的专用数据目录")
        _require_empty(selected)
        # No filesystem writes until the user confirms this exact plan.
        if target.exists() or target.is_symlink():
            _verify_existing(target)
        now = time.monotonic()
        for token, plan in list(_PLANS.items()):
            if now - plan["created"] >= _TTL:
                del _PLANS[token]
        if len(_PLANS) >= 16:
            del _PLANS[next(iter(_PLANS))]
        token = secrets.token_urlsafe(32)
        _PLANS[token] = {"identity": identity, "target": str(target), "directory": str(selected),
                         "previous": str(active) if active else None, "created": now}
        return {"configured": False, "confirmation_token": token, "expires_in_seconds": _TTL,
                "directory": str(selected),
                "message": "将在所选位置创建私有数据目录。先复制数据库到该目录，再校验、解密和解析副本；聊天索引和结构化导入副本也保存在此。不会修改微信原库或其他文件夹的权限；授权配置仍保存在用户目录中。"}


def grant_permission(project_root: Path | str, payload: dict) -> dict:
    if payload.get("confirmed") is not True:
        raise _error("AUTHORIZATION_REQUIRED", "请先确认创建当前用户专用的数据目录")
    token = payload.get("confirmation_token")
    if not isinstance(token, str):
        raise _error("STORAGE_CONSENT_EXPIRED", "目录授权无效或已过期，请重新申请")
    with _LOCK:
        plan = _PLANS.pop(token, None)
        base, target, identity = _locations(project_root)
        if (not plan or plan["identity"] != identity or plan["target"] != str(target)
                or time.monotonic() - plan["created"] >= _TTL):
            raise _error("STORAGE_CONSENT_EXPIRED", "目录授权无效或已过期，请重新申请")
        active = enrolled_root(project_root)
        if (str(active) if active else None) != plan["previous"]:
            raise _error("STORAGE_CONSENT_EXPIRED", "当前数据位置已变化，请重新申请")
        _check_legacy_empty(project_root)
        selected = Path(plan["directory"])
        if active is not None:
            _require_inactive_store(active)
        _require_empty(selected)
        if not base.exists() and not base.is_symlink():
            privacy.ensure_private_directory(base)
        # Creating/verifying target validates base as an ancestor: links and
        # replacement/ACL takeover rights still fail without changing its ACL.
        for directory in (target, selected):
            if directory.exists() or directory.is_symlink():
                _verify_existing(directory)
            else:
                privacy.ensure_private_directory(directory)
        # Publish the marker only after a create/write/delete permission probe.
        probe = selected / ("probe-" + secrets.token_hex(16))
        privacy.create_private_file(probe)
        try:
            with probe.open("wb") as stream:
                stream.write(b"Agent4Market storage probe")
                stream.flush()
                os.fsync(stream.fileno())
            privacy.verify_private_file(probe)
        finally:
            probe.unlink(missing_ok=True)
        marker = target / "storage.json"
        temporary = target / ("storage-" + secrets.token_hex(16) + ".tmp")
        privacy.create_private_file(temporary)
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"schema_version": 2, "project_id": identity, "directory": str(selected)}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            _verify_existing(selected)
            if enrolled_root(project_root) != active:
                raise _error("UNSAFE_PATH", "目录授权记录已变化，请重新检查")
            # Atomic marker replacement is the commit point. All fallible
            # validation precedes it; subsequent accesses revalidate normally.
            temporary.replace(marker)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"configured": True, "directory": str(selected), "message": "专用数据目录已创建并通过权限检查。请重新点击导入；如有加密密钥，请重新填写。"}
