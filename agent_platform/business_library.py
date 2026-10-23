"""Local, user-edited dossiers. Never writes the legacy sales ledger or chat text.

Legacy objects are read-only projections with explicitly separate dossier overrides.
SQLite serializes writers; a snapshot revision prevents lost edits across windows.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import stat
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

TYPES = {"sales", "customer", "project"}
FIELDS = {"name", "aliases", "owner", "customer", "team", "stage", "industry", "description", "project_type"}
MAX_ENTITIES = 10000


class DossierError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any, label: str, maximum: int = 500, required: bool = False) -> str:
    if not isinstance(value, str):
        raise DossierError("INVALID_INPUT", f"{label}必须是文字")
    value = unicodedata.normalize("NFC", value).strip()
    if (required and not value) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise DossierError("INVALID_INPUT", f"{label}不能为空、含控制字符或超过 {maximum} 字")
    return value


def source_entity(kind: str, source_id: str, name: str, **fields: Any) -> dict[str, Any]:
    identity = "source-" + hashlib.sha256(f"{kind}:{source_id}".encode()).hexdigest()[:24]
    return {"id": identity, "type": kind, "name": name, "source_ref": source_id, **fields}


def is_linklike(path: Path) -> bool:
    # Python 3.11 has no Path.is_junction; Windows reparse attributes still
    # detect directory junctions without following them.
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _path(root: Path) -> Path:
    root = root.resolve(strict=True)
    path = root / "data" / "library" / "dossiers.sqlite3"
    for node in [path, path.parent, path.parent.parent]:
        if is_linklike(node):
            raise DossierError("UNSAFE_PATH", "档案目录不能使用链接或目录联接")
    if path.exists() and (not path.is_file() or path.stat().st_size > 64 * 1024 * 1024):
        raise DossierError("INVALID_STORE", "档案存储无效或超过容量")
    return path


@contextmanager
def _connection(root: Path, writable: bool = False):
    path = _path(root)
    if not writable and not path.exists():
        yield None
        return
    if writable:
        path.parent.mkdir(parents=True, exist_ok=True)
        _path(root)
    connection = None
    try:
        connection = sqlite3.connect(path if writable else path.as_uri() + "?mode=ro", uri=not writable, timeout=5)
        if writable:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS dossiers (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        yield connection
        if writable:
            connection.commit()
    except sqlite3.Error as error:
        if writable and connection:
            connection.rollback()
        busy = getattr(error, "sqlite_errorcode", 0) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        raise DossierError("STORE_BUSY" if busy else "INVALID_STORE",
                           "档案正在被其他操作占用，请稍后重试" if busy else "档案存储无法读取；未覆盖原文件") from error
    except Exception:
        if writable and connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()


def _read(connection) -> dict[str, Any]:
    has_table = connection and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dossiers'").fetchone()
    row = connection.execute("SELECT payload FROM dossiers WHERE id=1").fetchone() if has_table else None
    if row is None:
        return {"schema": 1, "entities": {}, "links": {}, "groups": {}, "version": 0}
    try:
        value = json.loads(row[0])
        _validate_state(value)
        return value
    except (ValueError, TypeError, AttributeError, RecursionError) as error:
        raise DossierError("INVALID_STORE", "档案存储损坏或版本不支持；未修改原文件") from error


def _validate_state(value: Any) -> None:
    def check(condition):
        if not condition:
            raise ValueError("Invalid dossier state")
    def text(v, limit=4096):
        return isinstance(v, str) and len(v) <= limit and not any(ord(c) < 32 and c not in "\n\t" for c in v)
    check(isinstance(value, dict) and value.get("schema") == 1)
    check(type(value.get("version")) is int and 0 <= value["version"] <= 2**53)
    check(all(isinstance(value.get(k), dict) for k in ("entities", "links", "groups")))
    check(len(value["entities"]) <= MAX_ENTITIES and len(value["links"]) <= 100000 and len(value["groups"]) <= 100000)
    for identity, entry in value["entities"].items():
        check(text(identity, 128) and isinstance(entry, dict) and entry.get("id") == identity and entry.get("type") in TYPES)
        if "source_snapshot" in entry:
            base, overrides = entry["source_snapshot"], entry.get("overrides")
            check(isinstance(base, dict) and base.get("id") == identity and base.get("type") == entry["type"])
            check(text(base.get("name"), 1000) and bool(base["name"]) and len(base) <= 40)
            check(all((type(v) is bool if k == "archived" else text(v, 1000000)) for k, v in base.items()))
            check(isinstance(overrides, dict) and not set(overrides) - FIELDS)
            check(all(text(v, 1000 if k == "description" else 128) for k, v in overrides.items()))
        else:
            check(text(entry.get("name"), 128) and bool(entry["name"]))
        for key in FIELDS | {"source_ref", "source_version", "workspace_id", "created_at", "updated_at"}:
            if key in entry:
                check(text(entry[key], 1000 if key == "description" else 1024))
        for key, maximum in (("contacts", 200), ("records", 5000), ("history", 10000)):
            rows = entry.get(key)
            check(isinstance(rows, list) and len(rows) <= maximum and all(isinstance(r, dict) for r in rows))
            for row in rows:
                if key == "contacts":
                    check(all(text(row.get(k), 200) for k in ("id", "name", "role", "note")))
                elif key == "records":
                    check(row.get("kind") in {"action", "followup", "milestone"} and type(row.get("done")) is bool)
                    check(all(text(row.get(k), 2000) for k in ("id", "text", "owner", "due", "task_id", "source", "created_at")))
                else:
                    check(text(row.get("at")) and text(row.get("operation")) and all(isinstance(row.get(k), dict) for k in ("before", "after")))
                    check(all(len(row[k]) <= 20 and all(text(v, 1000000) for v in row[k].values()) for k in ("before", "after")))
        check(type(entry.get("archived")) is bool)
    for ref, ids in value["links"].items():
        check(text(ref, 256) and ref.startswith(("file:", "wechat:")) and isinstance(ids, list) and len(ids) <= MAX_ENTITIES)
        check(all(text(i, 128) for i in ids))
    check(all(text(ref, 256) and ref.startswith("wechat:") and category in {"sales", "customer", "other"} for ref, category in value["groups"].items()))


def _save_entity(state: dict, item: dict, old: dict | None, sources: list[dict]) -> None:
    identity = item["id"]
    source = next((s for s in sources if s["id"] == identity), None)
    prior = state["entities"].get(identity, {})
    if not source:
        if "source_snapshot" in prior:
            state["entities"][identity] = {**copy.deepcopy(prior), **{
                k: copy.deepcopy(item[k]) for k in ("archived", "contacts", "records", "history", "updated_at")}}
        else:
            state["entities"][identity] = item
        return
    # Keep only explicit local changes. Inherited fields always reflect the source.
    overrides = copy.deepcopy(prior.get("overrides", {}))
    if prior and "source_snapshot" not in prior:
        # Older local snapshots cannot prove which fields were inherited: retain
        # differences, with the original source-version warning, never silently merge.
        overrides.update({k: prior[k] for k in FIELDS if k in prior and prior[k] != source.get(k, "")})
    overrides.update({k: item[k] for k in FIELDS if k in item and (old or {}).get(k, "") != item[k]})
    state["entities"][identity] = {
        **{k: copy.deepcopy(item[k]) for k in ("id", "type", "archived", "contacts", "records", "history", "updated_at")},
        "source_ref": source["source_ref"], "source_version": prior.get("source_version", source.get("source_version", "")),
        "source_snapshot": copy.deepcopy(prior.get("source_snapshot", source)), "overrides": overrides,
    }


def _snapshot(state: dict, sources: list[dict]) -> dict:
    entities = {}
    source_map = {s["id"]: s for s in sources}
    for source in sources:
        entity = {"archived": False, "owner": "", "customer": "", "description": "", "stage": "待补充",
                  "contacts": [], "records": [], "history": [], **copy.deepcopy(source)}
        entities[entity["id"]] = entity
    for identity, overlay in state["entities"].items():
        fallback = {"owner": "", "customer": "", "description": "", "stage": "待补充", **copy.deepcopy(overlay.get("source_snapshot", {}))}
        merged = entities.get(identity, {"id": identity, **fallback})
        merged.update(copy.deepcopy({k: v for k, v in overlay.items() if k not in {"overrides", "source_snapshot"}}))
        merged.update(copy.deepcopy(overlay.get("overrides", {})))
        merged.pop("source_unavailable", None)
        merged.pop("source_changed", None)
        if overlay.get("source_ref") and identity not in source_map:
            merged["source_unavailable"] = True
        if identity in source_map:
            merged["source_changed"] = overlay.get("source_version") != source_map[identity].get("source_version")
            if merged["type"] == "project":
                merged["archived"] = source_map[identity]["archived"]
        entities[identity] = merged
    material = {"state": state, "sources": sources}
    return {"revision": hashlib.sha256(_json(material).encode()).hexdigest(), "entities": list(entities.values()),
            "links": copy.deepcopy(state["links"]), "groups": copy.deepcopy(state["groups"])}


def snapshot(root: Path, sources: list[dict] | None = None) -> dict:
    with _connection(root) as connection:
        return _snapshot(_read(connection), sources or [])


def _require_entity(entities: dict, identity: str, kind: str | None = None, active: bool = False) -> dict:
    if not isinstance(identity, str):
        raise DossierError("INVALID_INPUT", "档案编号无效")
    item = entities.get(identity)
    if not item or (kind and item["type"] != kind):
        raise DossierError("NOT_FOUND", "关联档案不存在或类型不匹配")
    if active and (item.get("archived") or item.get("source_unavailable")):
        raise DossierError("ARCHIVED", "档案已归档或原始来源不可用，请刷新或恢复后操作")
    return item


def mutate(root: Path, payload: dict, *, sources: list[dict] | None = None,
           valid_sources: set[str] | None = None, task: dict | None = None, conversation: dict | None = None) -> dict:
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise DossierError("CONFIRMATION_REQUIRED", "请确认保存本次档案修改")
    with _connection(root, True) as connection:
        state = _read(connection)
        view = _snapshot(state, sources or [])
        chat_record_id = ""
        if conversation is not None:
            if (payload.get("operation") != "record" or payload.get("id") != conversation["project_id"]
                    or payload.get("task_id") or payload.get("owner")):
                raise DossierError("INVALID_INPUT", "对话只能向本轮档案追加经用户确认的记录")
            chat_record_id = "chat-" + hashlib.sha256(
                (conversation["session_id"] + ":" + conversation["request_id"]).encode()).hexdigest()[:32]
            existing = next((r for e in view["entities"] if e["id"] == payload["id"]
                             for r in e.get("records", []) if r["id"] == chat_record_id), None)
            if existing:
                if (any(existing[k] != payload.get(k, "") for k in ("text", "kind", "due"))
                        or ("\n用户原话" in existing["source"]) != bool(conversation.get("include_source_quote"))):
                    raise DossierError("DUPLICATE", "本轮记录已保存；不能用重复请求覆盖已保存内容")
                return {**view, "saved_id": payload["id"], "message": "本轮记录已保存，没有重复添加。"}
        if payload.get("revision") != view["revision"]:
            raise DossierError("CONFLICT", "档案已由其他窗口或来源更新，请刷新后重试；未覆盖修改")
        entities = {item["id"]: item for item in view["entities"]}
        operation, identity = payload.get("operation"), payload.get("id")
        if not isinstance(operation, str) or (identity is not None and not isinstance(identity, str)):
            raise DossierError("INVALID_INPUT", "档案操作或编号无效")
        result_id = identity
        if operation == "save":
            new = not identity
            kind = payload.get("type")
            if kind not in TYPES:
                raise DossierError("INVALID_INPUT", "档案类型无效")
            old = None if new else _require_entity(entities, identity, kind, active=True)
            if kind == "project" and (new or not old.get("workspace_id")):
                raise DossierError("INVALID_INPUT", "请先创建项目空间，再保存项目档案")
            fields = payload.get("fields")
            if not isinstance(fields, dict) or set(fields) - FIELDS:
                raise DossierError("INVALID_INPUT", "档案字段无效")
            values = {key: _text(value, key, 1000 if key == "description" else 128, key == "name") for key, value in fields.items()}
            name = values.get("name", (old or {}).get("name", ""))
            if not name:
                raise DossierError("INVALID_INPUT", "请填写名称")
            if any(e["id"] != identity and e["type"] == kind and e["name"].casefold() == name.casefold() for e in entities.values()):
                raise DossierError("DUPLICATE", "已有同名档案，请核对；不会按名称自动合并")
            if new and len(entities) >= MAX_ENTITIES:
                raise DossierError("LIMIT", "档案容量已达上限")
            for key, target_kind in (("owner", "sales"), ("customer", "customer")):
                target = values.get(key, (old or {}).get(key, ""))
                if target:
                    _require_entity(entities, target, target_kind, active=True)
            if kind != "project" and values.get("customer"):
                raise DossierError("INVALID_INPUT", "只有项目可以设置所属客户")
            identity = identity or "entity-" + uuid.uuid4().hex
            item = copy.deepcopy(old) if old else {"id": identity, "type": kind, "owner": "", "customer": "", "stage": "待补充",
                "description": "", "archived": False, "contacts": [], "records": [], "history": [], "created_at": _now()}
            item.update(values)
            changed = [key for key in values if not old or old.get(key, "") != values[key]]
            _history(item, "建立档案" if new else "更新档案", {k: (old or {}).get(k, "") for k in changed}, {k: item[k] for k in changed})
            _save_entity(state, item, old, sources or [])
            result_id = identity
        elif operation in {"archive", "restore", "contact", "record", "toggle"}:
            item = copy.deepcopy(_require_entity(entities, identity, active=operation not in {"archive", "restore"}))
            if item["type"] == "project" and operation in {"archive", "restore"}:
                raise DossierError("INVALID_INPUT", "请使用项目空间归档或恢复，以保持定时任务状态一致")
            if operation in {"archive", "restore"}:
                if operation == "restore" and item.get("source_unavailable"):
                    raise DossierError("NOT_FOUND", "原始档案来源不可用，无法恢复")
                item["archived"] = operation == "archive"
                _history(item, "归档（保留资料与记录）" if item["archived"] else "恢复档案")
            elif operation == "contact":
                if item["type"] != "customer":
                    raise DossierError("INVALID_INPUT", "联系人仅可加入客户档案")
                if len(item["contacts"]) >= 200:
                    raise DossierError("LIMIT", "联系人数量已达上限")
                item["contacts"].append({"id": uuid.uuid4().hex, **{k: _text(payload.get(k, ""), k, 200, k == "name") for k in ("name", "role", "note")}})
                _history(item, "新增联系人")
            elif operation == "record":
                kind = payload.get("kind")
                if kind not in {"followup", "action", "milestone"}:
                    raise DossierError("INVALID_INPUT", "记录类型无效")
                due = _text(payload.get("due", ""), "截止日期", 10)
                if due:
                    try:
                        if date.fromisoformat(due).isoformat() != due:
                            raise ValueError()
                    except ValueError as error:
                        raise DossierError("INVALID_INPUT", "截止日期无效") from error
                owner = _text(payload.get("owner", ""), "负责人", 128)
                if owner:
                    _require_entity(entities, owner, "sales", active=True)
                task_id = _text(payload.get("task_id", ""), "来源任务", 128)
                if task_id:
                    if not task or task.get("task_id") != task_id or task.get("status") != "completed" or task.get("library_context", {}).get("entity_id") != identity:
                        raise DossierError("INVALID_TASK", "只能从本档案已完成的工作保存记录")
                    if any(r.get("task_id") == task_id for r in item["records"]):
                        raise DossierError("DUPLICATE", "该工作建议已保存，请勿重复提交")
                if len(item["records"]) >= 5000:
                    raise DossierError("LIMIT", "记录数量已达上限")
                source = "用户核对工作结果后保存" if task_id else "手工录入"
                if conversation:
                    source = f"用户确认的对话记录（非独立核验） · {conversation['created_at']} · {chat_record_id}"
                    if conversation.get("include_source_quote"):
                        excerpt = conversation["text"][:1200]
                        source += f"\n用户原话{'（截取前 1200 字）' if len(conversation['text']) > 1200 else ''}：{excerpt}"
                item["records"].insert(0, {"id": chat_record_id or uuid.uuid4().hex, "kind": kind, "text": _text(payload.get("text"), "记录内容", 2000, True),
                    "owner": owner, "due": due, "done": False, "created_at": _now(), "task_id": task_id,
                    "source": source})
                _history(item, "新增记录", {}, {"record_id": item["records"][0]["id"]})
            else:
                record = next((r for r in item["records"] if r["id"] == payload.get("record_id")), None)
                if not record or record["kind"] == "followup" or not isinstance(payload.get("done"), bool):
                    raise DossierError("INVALID_INPUT", "行动记录或目标状态无效")
                record["done"] = payload["done"]
                _history(item, "完成行动" if record["done"] else "重新打开行动", {}, {"record_id": record["id"]})
            _save_entity(state, item, entities[identity], sources or [])
        elif operation in {"link", "classify", "entity-links"}:
            refs = payload.get("sources")
            if not isinstance(refs, list) or not 1 <= len(refs) <= 200 or any(not isinstance(r, str) or r not in (valid_sources or set()) for r in refs):
                raise DossierError("INVALID_SOURCE", "资料不存在、已过期或当前平台不允许访问")
            if operation == "entity-links":
                _require_entity(entities, identity, active=True)
                selected = payload.get("selected")
                if not isinstance(selected, list) or any(not isinstance(r, str) or r not in refs for r in selected):
                    raise DossierError("INVALID_SOURCE", "资料选择无效")
                for ref in refs:
                    ids = [i for i in state["links"].get(ref, []) if i != identity]
                    state["links"][ref] = ids + ([identity] if ref in selected else [])
            elif operation == "classify":
                category = payload.get("category")
                if category not in {"customer", "sales", "other"} or any(not ref.startswith("wechat:") for ref in refs):
                    raise DossierError("INVALID_INPUT", "群聊分类无效")
                for ref in refs:
                    state["groups"][ref] = category
            else:
                ids = payload.get("entity_ids")
                if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) for i in ids):
                    raise DossierError("INVALID_INPUT", "关联对象无效")
                for eid in ids:
                    _require_entity(entities, eid)
                for ref in refs:
                    state["links"][ref] = sorted(set(state["links"].get(ref, []) + ids if payload.get("append") is True else ids))
        else:
            raise DossierError("INVALID_INPUT", "档案操作无效")
        state["version"] += 1
        try:
            _validate_state(state)
        except (ValueError, TypeError, AttributeError) as error:
            raise DossierError("INVALID_INPUT", "本次档案内容无效或超过容量，未保存") from error
        encoded = _json(state)
        if len(encoded.encode()) > 32 * 1024 * 1024:
            raise DossierError("LIMIT", "档案容量已达上限，未保存本次修改")
        connection.execute("INSERT INTO dossiers(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (encoded,))
        return {**_snapshot(state, sources or []), "saved_id": result_id, "message": "档案已保存；原始销售台账、文件和聊天记录未修改。"}


def _history(item: dict, operation: str, before: dict | None = None, after: dict | None = None) -> None:
    if len(item["history"]) >= 10000:
        raise DossierError("LIMIT", "历史记录容量已达上限，未修改档案")
    item["updated_at"] = _now()
    item["history"].insert(0, {"at": item["updated_at"], "operation": operation, "before": before or {}, "after": after or {}})


def task_context(view: dict, identity: str, revision: str) -> dict:
    if revision != view["revision"]:
        raise DossierError("CONFLICT", "档案已更新，请刷新并重新确认任务背景")
    entities = {e["id"]: e for e in view["entities"]}
    item = _require_entity(entities, identity, active=True)
    scope = list(dict.fromkeys(i for i in [identity, item.get("owner"), item.get("customer")] if i))
    def bounded(value, maximum=1000):
        text = "".join(c for c in str(value) if ord(c) >= 32 or c in "\n\t")
        return text.encode("utf-16-le", errors="ignore")[:maximum * 2].decode("utf-16-le", errors="ignore")
    return {"entity_id": identity, "revision": revision, "scope_ids": scope,
            "workspace_id": item.get("workspace_id", ""),
            "source_ref": bounded(item.get("source_ref", "")), "source_version": bounded(item.get("source_version", "")),
            "snapshot": {key: bounded(item.get(key, "")) for key in ("type", "name", "stage", "description")},
            "owner_name": bounded(entities.get(item.get("owner"), {}).get("name", "")),
            "customer_name": bounded(entities.get(item.get("customer"), {}).get("name", ""))}
