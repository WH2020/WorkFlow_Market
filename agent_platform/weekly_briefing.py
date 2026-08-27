"""Deterministic personalized sales briefing projection for the local workbench.

This module only reads the selected local business backend.  It never writes,
guesses customer ownership, or invents asset links.  The governed Pi workflow
creates the authoritative, hash-bound snapshot used for the final deck.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .business_backend import (
    BusinessBackendError,
    _complete_business_csv_rows,
    _controlled_regular_file,
    _sqlite_connection,
    resolve_business_backend,
)


MAX_ROWS = 1000
MAX_ROSTER_BYTES = 1024 * 1024
CLOSED = {"completed", "closed", "resolved", "rejected", "cancelled", "canceled", "done", "已完成", "已关闭", "已拒绝", "已取消"}
ACTIVE_ASSETS = {"active", "approved", "published", "verified", "ready", "有效", "已发布", "已核验", "可用", "已批准"}
AUTHORIZED_ASSETS = {"approved", "authorized", "permitted", "已授权", "已批准", "可使用"}


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _valid_id(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _period(start: str, end: str) -> tuple[date, date]:
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as error:
        raise BusinessBackendError("INVALID_INPUT", "销售简报周期必须使用 YYYY-MM-DD") from error
    if first > last:
        raise BusinessBackendError("INVALID_INPUT", "销售简报开始日期不能晚于结束日期")
    if last - first >= timedelta(days=31):
        raise BusinessBackendError("INVALID_INPUT", "销售简报周期不能超过 31 天")
    return first, last


def _within(row: Mapping[str, Any], fields: Iterable[str], first: date, last: date) -> bool:
    return any((candidate := _date_value(row.get(field))) is not None and first <= candidate <= last for field in fields)


def _read_roster(root: Path) -> list[dict[str, Any]]:
    path = root / "data" / "sales" / "salespeople.json"
    if not path.exists() and not path.is_symlink():
        return []
    path = _controlled_regular_file(root, path, "销售人员名单", MAX_ROSTER_BYTES)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BusinessBackendError("ROSTER_INVALID", "销售人员名单不是有效的 UTF-8 JSON") from error
    rows = payload.get("salespeople") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise BusinessBackendError("ROSTER_INVALID", "销售人员名单格式无效或数量超过上限")
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("salesperson_id"), str) or not isinstance(row.get("name"), str) or not isinstance(row.get("active"), bool):
            raise BusinessBackendError("ROSTER_INVALID", "销售人员名单存在无效记录")
        result.append(dict(row))
    return result


def _load_rows(project_root: Path | str, first: date, last: date) -> dict[str, list[dict[str, Any]]]:
    backend = resolve_business_backend(project_root)
    roster = _read_roster(backend.root)
    if backend.backend == "csv":
        activities = [row for row in _complete_business_csv_rows(backend.root, "data/sales/activities.csv") if _within(row, ("occurred_at", "created_at", "next_action_due"), first, last)]
        resources = [row for row in _complete_business_csv_rows(backend.root, "data/sales/resource-requests.csv") if _within(row, ("requested_at", "updated_at", "deadline"), first, last)]
        related = {_text(row, "customer_id") for row in activities + resources if _text(row, "customer_id")}
        customers = [row for row in _complete_business_csv_rows(backend.root, "data/sales/customers.csv") if _text(row, "customer_id") in related or _within(row, ("updated_at", "last_evidence_date", "next_action_due"), first, last)]
        return {
            "salespeople": roster,
            "customers": customers[:MAX_ROWS], "activities": activities[:MAX_ROWS],
            "resource_requests": resources[:MAX_ROWS],
            "sales_assets": _complete_business_csv_rows(backend.root, "data/sales/sales-assets.csv")[:MAX_ROWS],
            "actions": [], "risks": [],
        }

    start_iso = f"{first.isoformat()}T00:00:00+08:00"
    end_iso = f"{(last + timedelta(days=1)).isoformat()}T00:00:00+08:00"
    with _sqlite_connection(backend) as connection:
        activities = [dict(row) for row in connection.execute(
            "SELECT * FROM activities WHERE deleted_at IS NULL AND julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<julianday(?) ORDER BY occurred_at DESC,activity_id LIMIT ?",
            (start_iso, end_iso, MAX_ROWS),
        ).fetchall()]
        resources = [dict(row) for row in connection.execute(
            """SELECT * FROM resource_requests WHERE deleted_at IS NULL AND (
                 (requested_at IS NOT NULL AND julianday(requested_at)>=julianday(?) AND julianday(requested_at)<julianday(?)) OR
                 (updated_at IS NOT NULL AND julianday(updated_at)>=julianday(?) AND julianday(updated_at)<julianday(?)) OR
                 (deadline IS NOT NULL AND julianday(deadline)>=julianday(?) AND julianday(deadline)<julianday(?)))
               ORDER BY updated_at DESC,request_id LIMIT ?""",
            (start_iso, end_iso, start_iso, end_iso, start_iso, end_iso, MAX_ROWS),
        ).fetchall()]
        related = sorted({_text(row, "account_id") for row in activities + resources if _text(row, "account_id")})
        account_conditions = ["(julianday(updated_at)>=julianday(?) AND julianday(updated_at)<julianday(?))"]
        parameters: list[Any] = [start_iso, end_iso]
        if related:
            account_conditions.append(f"account_id IN ({','.join('?' for _ in related)})")
            parameters.extend(related)
        parameters.append(MAX_ROWS)
        customers = [dict(row) for row in connection.execute(
            f"SELECT * FROM accounts WHERE deleted_at IS NULL AND ({' OR '.join(account_conditions)}) ORDER BY updated_at DESC,account_id LIMIT ?",
            parameters,
        ).fetchall()]
        actions = [dict(row) for row in connection.execute(
            "SELECT * FROM actions WHERE deleted_at IS NULL AND lower(coalesce(status,'')) NOT IN ('completed','cancelled','canceled','closed') ORDER BY coalesce(due_at,'9999-12-31'),action_id LIMIT ?",
            (MAX_ROWS,),
        ).fetchall()]
        risks = [dict(row) for row in connection.execute(
            "SELECT * FROM risks WHERE deleted_at IS NULL AND lower(coalesce(status,'')) NOT IN ('closed','resolved','cancelled','canceled') ORDER BY updated_at DESC,risk_id LIMIT ?",
            (MAX_ROWS,),
        ).fetchall()]
        assets = [dict(row) for row in connection.execute(
            "SELECT * FROM sales_assets WHERE deleted_at IS NULL ORDER BY updated_at DESC,asset_id LIMIT ?",
            (MAX_ROWS,),
        ).fetchall()]
    return {
        "salespeople": roster, "customers": customers, "activities": activities,
        "resource_requests": resources, "sales_assets": assets, "actions": actions, "risks": risks,
    }


def build_weekly_briefing(project_root: Path | str, start: str, end: str) -> dict[str, Any]:
    """Return a natural-language-ready preview; the Pi snapshot remains authoritative."""
    first, last = _period(start, end)
    rows = _load_rows(project_root, first, last)
    sellers: dict[str, dict[str, str]] = {}
    aliases: dict[str, str] = {}
    roster_names: dict[str, str] = {}

    def add_seller(salesperson_id: str, name: str, source: str) -> str | None:
        identity = salesperson_id.strip()
        if not _valid_id(identity):
            return None
        sellers.setdefault(identity, {"salesperson_id": identity, "name": name.strip() or identity, "seller_source": source})
        aliases[_normalized(identity)] = identity
        aliases[_normalized(sellers[identity]["name"])] = identity
        return identity

    for row in rows["salespeople"]:
        roster_id = _text(row, "salesperson_id")
        if _valid_id(roster_id):
            roster_names[roster_id] = _text(row, "name") or roster_id
        if row.get("active") is True:
            add_seller(roster_id, _text(row, "name"), "roster")
    for row in rows["activities"] + rows["resource_requests"]:
        identity = _text(row, "salesperson_id")
        if identity and _normalized(identity) not in aliases:
            add_seller(identity, roster_names.get(identity, identity), "record")

    customers = rows["customers"]
    customer_by_id = {_text(row, "customer_id", "account_id"): row for row in customers if _text(row, "customer_id", "account_id")}

    def account_name(account_id: str) -> str:
        return _text(customer_by_id.get(account_id, {}), "customer_name", "name") or account_id or "未关联客户"

    def resolve(candidate: str) -> str | None:
        return aliases.get(_normalized(candidate))

    seller_accounts = {identity: set() for identity in sellers}
    account_sellers: dict[str, set[str]] = {}

    def assign(seller_id: str | None, account_id: str) -> None:
        if not seller_id or seller_id not in sellers or not account_id:
            return
        seller_accounts[seller_id].add(account_id)
        account_sellers.setdefault(account_id, set()).add(seller_id)

    for row in customers:
        assign(resolve(_text(row, "owner")), _text(row, "customer_id", "account_id"))
    for row in rows["activities"] + rows["resource_requests"]:
        assign(resolve(_text(row, "salesperson_id")) or resolve(_text(row, "owner")), _text(row, "customer_id", "account_id"))
    for row in rows["actions"]:
        assign(resolve(_text(row, "owner")), _text(row, "customer_id", "account_id"))

    def row_seller(row: Mapping[str, Any]) -> str | None:
        explicit = resolve(_text(row, "salesperson_id", "owner"))
        if explicit:
            return explicit
        linked = account_sellers.get(_text(row, "customer_id", "account_id"), set())
        return next(iter(linked)) if len(linked) == 1 else None

    actions_by_seller: dict[str, list[dict[str, Any]]] = {identity: [] for identity in sellers}

    def add_action(seller_id: str | None, item: dict[str, Any]) -> None:
        if seller_id in actions_by_seller and item.get("text"):
            actions_by_seller[seller_id].append(item)

    for row in rows["resource_requests"]:
        if _normalized(_text(row, "status")) in CLOSED:
            continue
        account_id, record_id = _text(row, "customer_id", "account_id"), _text(row, "request_id")
        if record_id:
            add_action(row_seller(row), {"kind": "resource", "text": f"推动资源申请：{_text(row, 'request_summary') or '未命名申请'}", "account_id": account_id, "account_name": account_name(account_id), "due_at": _text(row, "deadline"), "evidence": {"type": "resource_request", "id": record_id}})
    for row in rows["actions"]:
        if _normalized(_text(row, "status")) in CLOSED:
            continue
        account_id, record_id = _text(row, "customer_id", "account_id"), _text(row, "action_id")
        if record_id:
            add_action(row_seller(row), {"kind": "action", "text": _text(row, "action_text"), "account_id": account_id, "account_name": account_name(account_id), "due_at": _text(row, "due_at"), "evidence": {"type": "action", "id": record_id}})
    for row in customers:
        account_id, next_action = _text(row, "customer_id", "account_id"), _text(row, "next_action")
        if next_action:
            for seller_id in account_sellers.get(account_id, set()):
                add_action(seller_id, {"kind": "customer_action", "text": next_action, "account_id": account_id, "account_name": account_name(account_id), "due_at": _text(row, "next_action_due"), "evidence": {"type": "account", "id": account_id}})

    assets_by_seller: dict[str, list[dict[str, Any]]] = {identity: [] for identity in sellers}
    filtered_assets = unassigned_assets = 0
    for row in rows["sales_assets"]:
        asset_id, source_path = _text(row, "asset_id"), _text(row, "source_path")
        source_status = _normalized(_text(row, "source_status"))
        eligible = bool(asset_id and source_path) and _normalized(_text(row, "status")) in ACTIVE_ASSETS and _normalized(_text(row, "authorization_status")) in AUTHORIZED_ASSETS and (not source_status or source_status in {"verified", "已核验"})
        if not eligible:
            filtered_assets += 1
            continue
        account_id = _text(row, "customer_id", "account_id")
        targets = sorted(account_sellers.get(account_id, set())) if account_id else []
        explicit_owner = resolve(_text(row, "owner"))
        if not targets and explicit_owner:
            targets = [explicit_owner]
        if not targets:
            unassigned_assets += 1
            continue
        for seller_id in targets:
            assets_by_seller[seller_id].append({"asset_id": asset_id, "title": _text(row, "title") or asset_id, "asset_type": _text(row, "asset_type"), "account_id": account_id, "account_name": account_name(account_id) if account_id else "通用资料", "source_path": source_path, "evidence": {"type": "sales_asset", "id": asset_id}})

    seller_briefs = []
    for seller in sorted(sellers.values(), key=lambda item: (item["name"], item["salesperson_id"])):
        seller_id = seller["salesperson_id"]
        account_ids = sorted(seller_accounts[seller_id])
        updates = []
        for row in rows["activities"]:
            if row_seller(row) != seller_id:
                continue
            activity_id, account_id, summary = _text(row, "activity_id"), _text(row, "customer_id", "account_id"), _text(row, "summary")
            if activity_id and summary:
                updates.append({"activity_id": activity_id, "account_id": account_id, "account_name": account_name(account_id), "occurred_at": _text(row, "occurred_at"), "channel": _text(row, "channel"), "activity_type": _text(row, "activity_type"), "summary": summary, "evidence": {"type": "activity", "id": activity_id}})
        resource_needs = []
        for row in rows["resource_requests"]:
            if row_seller(row) != seller_id or _normalized(_text(row, "status")) in CLOSED:
                continue
            request_id, account_id = _text(row, "request_id"), _text(row, "customer_id", "account_id")
            if request_id:
                resource_needs.append({"request_id": request_id, "account_id": account_id, "account_name": account_name(account_id), "summary": _text(row, "request_summary"), "resource_type": _text(row, "resource_type"), "deadline": _text(row, "deadline"), "status": _text(row, "status") or "待处理", "evidence": {"type": "resource_request", "id": request_id}})
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in actions_by_seller[seller_id]:
            key = f"{item['evidence']['type']}:{item['evidence']['id']}:{item['text']}"
            deduplicated.setdefault(key, item)
        ranked = sorted(deduplicated.values(), key=lambda item: ({"resource": 0, "action": 1}.get(item["kind"], 2), item.get("due_at") or "9999-12-31", item["text"]))[:3]
        brief: dict[str, Any] = {**seller, "account_count": len(account_ids), "account_ids": account_ids, "top_actions": ranked, "account_updates": updates[:12], "resource_needs": resource_needs[:12], "recommended_assets": assets_by_seller[seller_id][:6]}
        if not account_ids:
            brief["welcome_note"] = "本周期尚未找到可确认归属的客户；请先在销售人员名单、客户负责人或跟进记录中补充对应关系。"
        seller_briefs.append(brief)

    unmatched = [row for row in customers if not account_sellers.get(_text(row, "customer_id", "account_id"))]
    open_resources = [row for row in rows["resource_requests"] if _normalized(_text(row, "status")) not in CLOSED]
    needs_attention: list[dict[str, Any]] = []
    for row in open_resources[:10]:
        needs_attention.append({"kind": "resource", "text": f"资源申请待推进：{_text(row, 'request_summary') or '未命名申请'}", "account_id": _text(row, "customer_id", "account_id"), "due_at": _text(row, "deadline"), "evidence": {"type": "resource_request", "id": _text(row, "request_id")}})
    for row in rows["risks"][:10]:
        needs_attention.append({"kind": "risk", "text": _text(row, "risk_text"), "account_id": _text(row, "customer_id", "account_id"), "evidence": {"type": "risk", "id": _text(row, "risk_id")}})
    for row in [item for item in customers if _text(item, "risks")][:10]:
        needs_attention.append({"kind": "risk", "text": _text(row, "risks"), "account_id": _text(row, "customer_id", "account_id"), "evidence": {"type": "account", "id": _text(row, "customer_id", "account_id")}})

    messages = []
    if not seller_briefs:
        messages.append("没有找到启用的销售人员，也没有从本周期跟进或资源申请中识别到销售人员编号。请先维护销售人员名单。")
    if unmatched:
        messages.append(f"{len(unmatched)} 个客户无法确认销售归属，已保留在总监视图的“待分配”中，没有自动猜测负责人。")
    if filtered_assets:
        messages.append(f"{filtered_assets} 份销售资料因未授权、状态无效、未核验或缺少真实文件路径，未进入个人推荐。")
    if unassigned_assets:
        messages.append(f"{unassigned_assets} 份有效资料没有明确客户或销售负责人，仅保留在资料库中，未自动分发。")
    if not rows["activities"]:
        messages.append("本周期没有已记录的销售跟进；个人简报将只显示现有客户动作和资源事项。")
    if not messages:
        messages.append("销售归属、跟进记录和可推荐资料均通过确定性校验，可以生成个人简报与总监汇总。")
    status = "empty" if not seller_briefs and not customers and not rows["activities"] and not rows["resource_requests"] else ("limited" if unmatched or filtered_assets or not seller_briefs else "ready")
    touched = {_text(row, "customer_id", "account_id") for row in rows["activities"] if _text(row, "customer_id", "account_id")}
    return {
        "schema_version": "1.0", "mode": "personalized-sales-action-brief", "period": {"start": start, "end": end},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manager_rollup": {
            "seller_count": len(seller_briefs), "account_count": len(customers), "touched_account_count": len(touched), "open_resource_count": len(open_resources),
            "seller_summaries": [{"salesperson_id": brief["salesperson_id"], "name": brief["name"], "account_count": brief["account_count"], "action_count": len(brief["top_actions"]), "update_count": len(brief["account_updates"]), "resource_count": len(brief["resource_needs"])} for brief in seller_briefs],
            "unassigned_accounts": [{"account_id": _text(row, "customer_id", "account_id"), "name": _text(row, "customer_name", "name"), "owner": _text(row, "owner")} for row in unmatched],
            "needs_attention": needs_attention[:20],
        },
        "seller_briefs": seller_briefs,
        "validation": {"status": status, "filtered_asset_count": filtered_assets, "unassigned_asset_count": unassigned_assets, "unmapped_account_count": len(unmatched), "messages": messages},
        "source": {"backend": resolve_business_backend(project_root).backend, "note": "此页面为本地只读预览；正式任务会冻结同周期证据快照并记录 SHA-256。"},
    }
