"""Local project lookup and explicit customer context; no file bodies or WeChat."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone

from .business_library import DossierError


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def aliases(value: str) -> list[str]:
    return list(dict.fromkeys(term.strip() for term in re.split(r"[,，;；\n]", value) if term.strip()))


def _active(view: dict, kind: str = "project") -> list[dict]:
    return [e for e in view["entities"] if e["type"] == kind and (kind == "customer" or e.get("workspace_id"))
            and not e.get("archived") and not e.get("source_unavailable")]


def card(item: dict, entities: dict) -> dict:
    return {"entity_id": item["id"], "entity_type": item["type"], "workspace_id": item.get("workspace_id", ""), "name": item["name"],
            "customer": entities.get(item.get("customer"), {}).get("name", ""),
            "owner": entities.get(item.get("owner"), {}).get("name", ""), "stage": item.get("stage", "")}


def resolve(view: dict, payload: dict) -> dict:
    if (not isinstance(payload, dict) or set(payload) - {"message", "current_entity_id", "selected_entity_id"}
            or not isinstance(payload.get("message"), str) or not 1 <= len(payload["message"].strip()) <= 8000):
        raise DossierError("INVALID_INPUT", "项目检索需要 1–8000 字的问题")
    for field in ("current_entity_id", "selected_entity_id"):
        if not isinstance(payload.get(field, ""), str) or len(payload.get(field, "")) > 128:
            raise DossierError("INVALID_INPUT", "项目标识无效")
    entities = {e["id"]: e for e in view["entities"]}
    projects = _active(view)
    available = {e["id"]: e for e in projects}
    selected = payload.get("selected_entity_id")
    if selected and selected not in available:
        raise DossierError("NOT_FOUND", "项目已归档或不可用，请重新选择")
    query = normalized(payload["message"])
    strong, weak = [], []
    def mentions(term):
        term = normalized(term)
        if len(term) < 2 or term in {"项目", "客户", "默认项目", "日常工作", "试点"}:
            return False
        pattern = re.escape(term)
        if term.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        return bool(re.search(pattern, query))
    for item in projects:
        terms = [item["name"], *aliases(item.get("aliases", ""))]
        if any(mentions(term) for term in terms):
            strong.append(item)
        else:
            stem = re.sub(r"项目$", "", item["name"])
            customer = entities.get(item.get("customer"), {}).get("name", "")
            owner = entities.get(item.get("owner"), {}).get("name", "")
            if (len(stem) >= 3 and mentions(stem)) or mentions(customer) or mentions(owner):
                weak.append(item)
    candidates = strong or weak
    match = available.get(selected) if selected else strong[0] if len(strong) == 1 else None
    # A follow-up may reuse the current project, but an unknown named project
    # must not silently fall back to the previous project's facts.
    if not match and not candidates and payload.get("current_entity_id"):
        if re.search(r"(?:这个|该|本|当前|上面|上述)项目|^(?:那|那么)?它", query):
            match = available.get(payload["current_entity_id"])
    return {"status": "matched" if match else "ambiguous" if candidates else "not_found",
            "revision": view["revision"], "project": card(match, entities) if match else None,
            "candidates": [card(e, entities) for e in candidates[:12]], "candidate_count": len(candidates)}


def resolve_customer(view: dict, payload: dict) -> dict:
    """Only an explicitly selected customer; never expand to related projects."""
    if (not isinstance(payload, dict) or set(payload) != {"entity_id"}
            or not isinstance(payload.get("entity_id"), str) or not 1 <= len(payload["entity_id"]) <= 128):
        raise DossierError("INVALID_INPUT", "请选择一个客户档案")
    item = next((e for e in _active(view, "customer") if e["id"] == payload["entity_id"]), None)
    if not item:
        raise DossierError("NOT_FOUND", "客户已归档或不可用，请重新选择")
    return {"status": "matched", "revision": view["revision"],
            "customer": card(item, {e["id"]: e for e in view["entities"]})}


def context(view: dict, selection: dict, *, kind: str = "project") -> dict:
    if kind not in {"project", "customer"}:
        raise DossierError("INVALID_INPUT", "不支持此档案类型")
    if (not isinstance(selection, dict) or set(selection) != {"entity_id", "revision"}
            or not all(isinstance(selection[k], str) for k in selection)):
        raise DossierError("INVALID_INPUT", "只能提交档案编号和版本，不能提交自定义背景")
    if selection["revision"] != view["revision"]:
        raise DossierError("CONFLICT", "档案资料已更新，请重新发送以检索最新资料")
    entities = {e["id"]: e for e in view["entities"]}
    item = next((e for e in _active(view, kind) if e["id"] == selection["entity_id"]), None)
    if not item:
        raise DossierError("NOT_FOUND", "档案已归档或不可用，请重新选择")
    def text(value, limit=500):
        return str(value or "")[:limit]
    records = sorted(item.get("records", []), key=lambda r: r["created_at"], reverse=True)
    facts = {"名称": text(item["name"]), "简称": text(item.get("aliases")),
             "客户": text(entities.get(item.get("customer"), {}).get("name")),
             "负责人": text(entities.get(item.get("owner"), {}).get("name")),
             "阶段": text(item.get("stage")), "说明": text(item.get("description"), 1000)}
    if kind == "customer":
        facts.pop("简称")
        facts["客户"] = text(item["name"])
        facts["行业"] = text(item.get("industry"))
    refs = [ref for ref, ids in view["links"].items() if ref.startswith("file:") and item["id"] in ids]
    source = {"来源": "本地客户档案" if kind == "customer" else "本地项目档案", "档案类型": kind,
              "档案编号": item["id"], "项目空间": item.get("workspace_id", ""),
              "原始来源编号": item.get("source_ref", ""), "档案更新时间": item.get("updated_at", "未记录"),
              "检索时间": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "来源变化待核对": bool(item.get("source_changed")), "基本信息": facts,
              "缺失信息": [k for k, v in facts.items() if not v or v == "待补充"],
              "最近记录": [{"类型": r["kind"], "内容": text(r["text"], 400), "完成": r["done"],
                            "截止": r["due"], "记录时间": r["created_at"], "来源": text(r["source"]),
                            "负责人": text(entities.get(r["owner"], {}).get("name"))} for r in records[:8]],
              "记录总数": len(records), "展示记录数": min(8, len(records)), "长文本可能截断": True,
              "关联文件数": len(refs), "读取边界": "未读取文件正文、微信群或聊天原文；未修改任何业务资料。"}
    body = json.dumps(source, ensure_ascii=False)
    if len(body.encode("utf-8")) > 24 * 1024:
        raise DossierError("LIMIT", "项目背景超过对话容量，请缩小范围")
    return {"entity_id": item["id"], "name": text(item["name"]), "revision": view["revision"], "text": body}
