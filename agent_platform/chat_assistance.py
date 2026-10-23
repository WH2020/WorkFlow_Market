"""Bounded conversation UI and explicit preferences; no background profiling or model calls.

Only enum preferences are remembered. Business notes use the existing dossier
transaction and require a completed, server-owned turn plus a user confirmation.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import business_library as dossiers

PREFERENCES = {
    "length": {"label": "回答详略", "choices": {"brief": "简短回答", "balanced": "适中详略", "detailed": "详细解释"}},
    "structure": {"label": "回答结构", "choices": {"conclusion": "先结论后依据", "steps": "按步骤说明"}},
    "format": {"label": "呈现方式", "choices": {"paragraphs": "以自然段为主", "bullets": "优先分点说明", "tables": "比较信息时优先用表格"}},
    "language": {"label": "回答语言", "choices": {"zh": "中文", "en": "英文"}},
}


def _read(connection):
    exists = connection and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_preferences'").fetchone()
    row = connection.execute("SELECT payload FROM chat_preferences WHERE id=1").fetchone() if exists else None
    if row is None:
        return {"schema": 1, "version": 0, "values": {}}
    try:
        state = json.loads(row[0])
        if (set(state) != {"schema", "version", "values"} or state["schema"] != 1
                or type(state["version"]) is not int or not 0 <= state["version"] < 2**53
                or not isinstance(state["values"], dict) or set(state["values"]) - PREFERENCES.keys()):
            raise ValueError()
        for key, item in state["values"].items():
            if (not isinstance(item, dict) or set(item) != {"value", "updated_at"}
                    or item["value"] not in PREFERENCES[key]["choices"]
                    or not isinstance(item["updated_at"], str) or len(item["updated_at"]) > 64):
                raise ValueError()
        return state
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise dossiers.DossierError("INVALID_STORE", "回答偏好存储损坏或版本不支持；未覆盖原文件") from error


def _view(state):
    return {"revision": hashlib.sha256(dossiers._json(state).encode()).hexdigest(),
            "values": state["values"], "catalog": PREFERENCES}


def preferences(root):
    with dossiers._connection(root) as connection:
        return _view(_read(connection))


def change_preference(root, payload):
    if not isinstance(payload, dict) or set(payload) - {"revision", "key", "value", "confirmed"}:
        raise dossiers.DossierError("INVALID_INPUT", "回答偏好参数无效")
    if payload.get("confirmed") is not True:
        raise dossiers.DossierError("CONFIRMATION_REQUIRED", "请确认保存或忘记这项回答偏好")
    key, value = payload.get("key"), payload.get("value")
    if (not isinstance(key, str) or key not in PREFERENCES or not isinstance(value, str)
            or value and value not in PREFERENCES[key]["choices"]):
        raise dossiers.DossierError("INVALID_INPUT", "仅支持界面列出的回答偏好；不能保存任意画像或指令")
    with dossiers._connection(root, True) as connection:
        state = _read(connection)
        if payload.get("revision") != _view(state)["revision"]:
            raise dossiers.DossierError("CONFLICT", "回答偏好已变化，请刷新偏好后重新核对；未覆盖修改")
        if value:
            state["values"][key] = {"value": value, "updated_at": dossiers._now()}
        else:
            state["values"].pop(key, None)
        state["version"] += 1
        connection.execute("CREATE TABLE IF NOT EXISTS chat_preferences (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        connection.execute("INSERT INTO chat_preferences(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (dossiers._json(state),))
        return _view(state)


def preference_context(view):
    values = {PREFERENCES[key]["label"]: PREFERENCES[key]["choices"][item["value"]]
              for key, item in view["values"].items()}
    return {"revision": view["revision"], "text": json.dumps(values, ensure_ascii=False)} if values else None


def suggestions(message, view):
    """Conservative explicit-command detection, never infer personality from activity."""
    if (not re.match(r"^(?:请|麻烦)?(?:记住|以后|回答时|回复时|我(?:更)?(?:喜欢|希望|偏好))", message.strip())
            or re.search(r"[?？\"“”]|不要|不喜欢|不是|别|假设|例如|他说|她说", message)):
        return []
    if not re.search(r"回答|回复|表达|偏好|先(?:给|说|讲)?(?:出)?结论|按步骤|分步骤", message):
        return []
    patterns = {
        "length": {"brief": r"简短|简洁|精简", "detailed": r"详细|详尽", "balanced": r"详略适中"},
        "structure": {"conclusion": r"先(?:给|说|讲)?(?:出)?结论|结论在前", "steps": r"按步骤|分步骤"},
        "format": {"paragraphs": r"自然段", "bullets": r"分点|条目", "tables": r"表格"},
        "language": {"zh": r"中文", "en": r"英文|英语"},
    }
    result = []
    for key, choices in patterns.items():
        matches = [value for value, pattern in choices.items() if re.search(pattern, message)]
        if len(matches) == 1 and view["values"].get(key, {}).get("value") != matches[0]:
            old = view["values"].get(key, {}).get("value", "")
            result.append({"type": "preference_suggestion", "key": key, "value": matches[0],
                           "label": PREFERENCES[key]["label"], "text": PREFERENCES[key]["choices"][matches[0]],
                           "choices": PREFERENCES[key]["choices"],
                           "previous": PREFERENCES[key]["choices"].get(old, "尚未记住"), "revision": view["revision"]})
    return result


def project_card(context):
    data = json.loads(context["text"])
    return {"type": "project", "entity_id": context["entity_id"], "revision": context["revision"],
            "name": context["name"], "entity_type": data.get("档案类型", "project"), "facts": data["基本信息"], "missing": data["缺失信息"],
            "records": data["最近记录"], "record_count": data["记录总数"],
            "source_changed": data["来源变化待核对"], "retrieved_at": data["检索时间"],
            "source": data["来源"], "boundary": data["读取边界"]}


def context_cards(context, view):
    cards = [project_card(context)] if context else []
    if view["values"]:
        cards.append({"type": "preferences_used", "items": [
            f"{PREFERENCES[k]['label']}：{PREFERENCES[k]['choices'][v['value']]}" for k, v in view["values"].items()]})
    return cards


def completion_cards(turn, view, *, use_preferences=True):
    cards = suggestions(turn.text, view) if use_preferences else []
    if turn.project_id:
        # This is the user's text, not an LLM assertion. Questions remain editable
        # source material; they are never silently promoted to business facts.
        context = turn.transport["project_context"]
        cards.append({"type": "project_note", "entity_id": turn.project_id, "name": context["name"],
                      "entity_type": json.loads(context["text"]).get("档案类型", "project"),
                      "revision": context["revision"], "session_id": turn.session.session_id,
                      "request_id": turn.request_id, "text": turn.text if len(turn.text) <= 2000 else "",
                      "long_source": len(turn.text) > 2000})
    return cards
