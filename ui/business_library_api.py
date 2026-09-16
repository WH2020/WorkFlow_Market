"""Read-only source projections for the workbench's extended dossiers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agent_platform.business_backend import search_accounts
from agent_platform.weekly_briefing import _read_roster
from agent_platform.business_library import DossierError, source_entity, is_linklike


def frozen_file(root: Path, entry: dict, project_id: str) -> dict:
    if entry.get("origin_project_id") and entry["origin_project_id"] != project_id:
        raise ValueError("所选文件原始归属为其他项目，资料标签不能改变原始归属")
    if entry.get("project_id") and entry["project_id"] != project_id:
        raise ValueError("所选文件属于其他项目，请切换项目或重新选择资料")
    result = {k: str(entry.get(k, "")) for k in ("library_id", "title", "path", "url", "version")}
    if any(len(result[k].encode("utf-16-le")) > limit * 2 or any(ord(c) < 32 for c in result[k]) for k, limit in (("library_id", 128), ("title", 1000), ("path", 4096), ("url", 4096), ("version", 256))):
        raise ValueError("资料元数据超出任务范围，请更新资料后重试")
    result["sha256"] = ""
    if not result["path"]:
        return result
    root = root.resolve(strict=True)
    relative = Path(result["path"])
    if (relative.parts[:2] == ("inputs", "projects") or relative.parts[:2] == ("outputs", "reimbursements")) and len(relative.parts) >= 3 and relative.parts[2] != project_id:
        raise ValueError("所选文件原始路径属于其他项目")
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] not in {"inputs", "outputs", "data"}:
        raise ValueError("资料路径不属于受控目录")
    if relative.parts[0] == "data" and relative.parts[:2] != ("data", "inbox"):
        raise ValueError("资料路径不属于受控输入目录")
    node = root
    for part in relative.parts:
        node /= part
        if is_linklike(node):
            raise ValueError("不能将链接文件作为任务资料")
    path = node.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("任务资料不是受控普通文件")
    # Freeze the bytes, not a catalog version counter. The reader rechecks the
    # digest against the exact buffer it parses (including after a restart).
    before = path.stat()
    if before.st_size > 32 * 1024 * 1024:
        raise ValueError("单份任务资料不能超过 32 MiB")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("资料在选择期间被替换，请重试")
        content = handle.read(32 * 1024 * 1024 + 1)
        after = os.fstat(handle.fileno())
    if len(content) > 32 * 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or path.stat() != after:
        raise ValueError("资料在选择期间发生变化，请重试")
    result.update(path=path.relative_to(root).as_posix(), sha256=hashlib.sha256(content).hexdigest())
    return result


def source_records(root: Path, projects: list[dict]) -> list[dict]:
    result = []
    roster = _read_roster(root)
    for person in roster:
        result.append(source_entity("sales", "roster:" + person["salesperson_id"], person["name"],
            stage="在职" if person["active"] else "暂停负责", team=person.get("team", ""),
            source_version=hashlib.sha256(json.dumps(person, sort_keys=True).encode()).hexdigest()))
    cursor = None
    while True:
        page = search_accounts(root, cursor=cursor, limit=100)
        for account in page["rows"]:
            # Owner labels are not stable identities. Never match people by name.
            result.append(source_entity("customer", f"account:{page['binding_id']}:{account['account_id']}", account["name"],
                account_id=account["account_id"], industry=account.get("sector", ""),
                stage=account.get("lifecycle_stage", ""), original_owner=account.get("owner", ""),
                description=account.get("summary", ""), source_version=str(account["version"])))
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if len(result) >= 10000:
            raise DossierError("LIMIT", "历史台账超过档案视图容量，请在历史客户经营中筛选查看")
    for project in projects:
        result.append(source_entity("project", "workspace:" + project["project_id"], project["name"],
            workspace_id=project["project_id"], description=project.get("description", ""),
            archived=project["status"] == "archived", source_version=hashlib.sha256(json.dumps(
                {k: project.get(k, "") for k in ("project_id", "name", "description", "status")}, sort_keys=True).encode()).hexdigest()))
    return result
