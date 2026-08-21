"""Read-only business data routing for the local workbench.

The selected backend is determined exclusively by data/storage-backend.json.
An absent pointer means the legacy CSV backend; an invalid pointer fails closed.
This module never creates, migrates, writes, or switches a business database.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
MAX_POINTER_BYTES = 64 * 1024
MAX_CSV_BYTES = 16 * 1024 * 1024
MAX_LIMIT = 100
MAX_360_SECTION_ROWS = 1000
MAX_TIMELINE_SCAN_ROWS = 5000
MAX_BUSINESS_CSV_SCAN_ROWS = 250_000
MAX_TIMELINE_CSV_SCAN_ROWS = MAX_BUSINESS_CSV_SCAN_ROWS
FOCUS_DUE_DAYS = 7
FOCUS_STALE_DAYS = 30


class BusinessBackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BusinessBackend:
    backend: str
    binding_id: str
    root: Path
    database_path: Path | None = None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _controlled_regular_file(root: Path, candidate: Path, label: str, maximum: int | None = None) -> Path:
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise BusinessBackendError("UNSAFE_PATH", f"{label}必须位于项目目录内") from error
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BusinessBackendError("UNSAFE_PATH", f"{label}路径不能包含符号链接")
    if not candidate.is_file():
        raise BusinessBackendError("STORE_MISSING", f"{label}不存在或不是普通文件")
    if maximum is not None and candidate.stat().st_size > maximum:
        raise BusinessBackendError("FILE_TOO_LARGE", f"{label}超过安全大小限制")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise BusinessBackendError("UNSAFE_PATH", f"{label}越出项目目录")
    return resolved


def resolve_business_backend(project_root: Path | str) -> BusinessBackend:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise BusinessBackendError("PROJECT_MISSING", "项目目录不存在")
    pointer = root / "data" / "storage-backend.json"
    if not pointer.exists() and not pointer.is_symlink():
        return BusinessBackend("csv", "csv:pointer-absent", root)
    pointer = _controlled_regular_file(root, pointer, "存储指针", MAX_POINTER_BYTES)
    raw = pointer.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BusinessBackendError("POINTER_INVALID", "存储指针不是有效的 UTF-8 JSON") from error
    if not isinstance(payload, dict) or payload.get("backend") != "sqlite":
        raise BusinessBackendError("BACKEND_UNSUPPORTED", "存储指针声明了不支持的后端")
    if payload.get("schema_version") != SCHEMA_VERSION or isinstance(payload.get("schema_version"), bool):
        raise BusinessBackendError("SCHEMA_UNSUPPORTED", f"只支持业务数据库 schema v{SCHEMA_VERSION}")
    relative_text = payload.get("database_relative_path")
    if not isinstance(relative_text, str) or not relative_text or "\\" in relative_text or "\0" in relative_text:
        raise BusinessBackendError("POINTER_INVALID", "存储指针缺少受控数据库相对路径")
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise BusinessBackendError("UNSAFE_PATH", "业务数据库路径必须是项目内规范相对路径")
    database = _controlled_regular_file(root, root.joinpath(*relative.parts), "业务数据库")
    binding_id = f"sqlite:v{SCHEMA_VERSION}:{_sha256(raw)}"
    backend = BusinessBackend("sqlite", binding_id, root, database)
    with _sqlite_connection(backend) as connection:
        row = connection.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        if row is None or row["version"] != SCHEMA_VERSION:
            raise BusinessBackendError("SCHEMA_UNSUPPORTED", "业务数据库 schema 与存储指针不一致")
    return backend


@contextmanager
def _sqlite_connection(backend: BusinessBackend) -> Iterator[sqlite3.Connection]:
    if backend.backend != "sqlite" or backend.database_path is None:
        raise BusinessBackendError("BACKEND_MISMATCH", "当前后端不是 SQLite")
    try:
        connection = sqlite3.connect(
            f"{backend.database_path.as_uri()}?mode=ro",
            timeout=5,
            isolation_level=None,
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as error:
        raise BusinessBackendError("SQLITE_ERROR", f"无法只读打开业务数据库：{error}") from error
    try:
        yield connection
    except sqlite3.Error as error:
        message = str(error)
        code = "STORE_BUSY" if "locked" in message.lower() or "busy" in message.lower() else "SQLITE_ERROR"
        raise BusinessBackendError(code, f"业务数据库查询失败：{message}") from error
    finally:
        connection.close()


def _csv_rows(root: Path, relative: str, limit: int = 5000) -> list[dict[str, str]]:
    path = root / relative
    if not path.exists() and not path.is_symlink():
        return []
    path = _controlled_regular_file(root, path, relative, MAX_CSV_BYTES)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for _, row in zip(range(limit), csv.DictReader(handle))]
    except (OSError, UnicodeError, csv.Error) as error:
        raise BusinessBackendError("CSV_INVALID", f"无法读取 {relative}：{error}") from error


def _complete_business_csv_rows(root: Path, relative: str) -> list[dict[str, str]]:
    rows = _csv_rows(root, relative, MAX_BUSINESS_CSV_SCAN_ROWS + 1)
    if len(rows) > MAX_BUSINESS_CSV_SCAN_ROWS:
        raise BusinessBackendError("SCAN_LIMIT", f"{relative} 超过受控扫描上限，无法返回完整结果")
    return rows


def _row_version(row: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(sorted(row.items())), ensure_ascii=False, separators=(",", ":"))
    return _sha256(payload.encode("utf-8"))


def _cursor_scope(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(raw)


def _encode_cursor(kind: str, values: Sequence[str], scope: str) -> str:
    raw = json.dumps({"kind": kind, "values": list(values), "scope": scope}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, kind: str, width: int, scope: str) -> tuple[str, ...] | None:
    if cursor is None or cursor == "":
        return None
    if not isinstance(cursor, str) or len(cursor) > 1024:
        raise BusinessBackendError("INVALID_CURSOR", "分页游标无效")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BusinessBackendError("INVALID_CURSOR", "分页游标无效") from error
    values = (
        payload.get("values")
        if isinstance(payload, dict) and payload.get("kind") == kind and payload.get("scope") == scope
        else None
    )
    if not isinstance(values, list) or len(values) != width or any(not isinstance(value, str) for value in values):
        raise BusinessBackendError("INVALID_CURSOR", "分页游标与当前查询不匹配")
    return tuple(values)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > MAX_LIMIT:
        raise BusinessBackendError("INVALID_INPUT", f"limit 必须为 1-{MAX_LIMIT}")
    return value


def _normalize_query(value: str) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise BusinessBackendError("INVALID_INPUT", "查询词最多 500 个字符")
    return " ".join(value.casefold().split())


def _parse_iso8601(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise BusinessBackendError("INVALID_INPUT", f"{label} 无效")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise BusinessBackendError("INVALID_INPUT", f"{label} 必须是有效的 ISO 8601 日期或时间") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_iso(value: str, label: str) -> str:
    return _parse_iso8601(value, label).isoformat(timespec="microseconds").replace("+00:00", "Z")


ACCOUNT_FILTERS = {"owner", "region", "sector", "lifecycle_stage", "health", "project_id"}


def search_accounts(
    project_root: Path | str,
    *,
    query: str = "",
    filters: Mapping[str, str] | None = None,
    updated_since: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    backend = resolve_business_backend(project_root)
    normalized = _normalize_query(query)
    limit = _limit(limit)
    filters = dict(filters or {})
    if any(key not in ACCOUNT_FILTERS or not isinstance(value, str) or len(value) > 500 for key, value in filters.items()):
        raise BusinessBackendError("INVALID_INPUT", "客户筛选字段或值无效")
    updated_since_value = _canonical_iso(updated_since, "updated_since") if updated_since is not None else None
    updated_since_moment = _parse_iso8601(updated_since_value, "updated_since") if updated_since_value else None
    scope = _cursor_scope({
        "kind": "accounts", "query": normalized, "filters": sorted(filters.items()),
        "updated_since": updated_since_value,
    })
    position = _decode_cursor(cursor, "accounts", 2, scope)
    rows: list[dict[str, Any]]
    if backend.backend == "csv":
        projected = []
        for row in _complete_business_csv_rows(backend.root, "data/sales/customers.csv"):
            item = {
                "account_id": row.get("customer_id", ""), "name": row.get("customer_name", ""),
                "region": row.get("region", ""), "sector": row.get("sector", ""), "owner": row.get("owner", ""),
                "lifecycle_stage": row.get("stage", ""), "health": row.get("health", ""),
                "summary": "", "project_id": "", "updated_at": row.get("updated_at", ""),
                "version": _row_version(row), "last_activity_at": row.get("last_evidence_date", ""),
                "open_actions": 1 if row.get("next_action", "").strip() else 0,
                "open_risks": 1 if row.get("risks", "").strip() else 0,
            }
            haystack = "\n".join(str(item.get(key, "")) for key in ("account_id", "name", "region", "sector", "owner", "summary")).casefold()
            if normalized and normalized not in haystack:
                continue
            if any(str(item.get(key, "")) != value for key, value in filters.items()):
                continue
            if updated_since_moment is not None:
                try:
                    if _parse_iso8601(str(item.get("updated_at", "")), "客户更新时间") < updated_since_moment:
                        continue
                except BusinessBackendError:
                    continue
            projected.append(item)
        projected.sort(key=lambda item: (str(item["updated_at"]), str(item["account_id"])), reverse=True)
        if position:
            projected = [item for item in projected if (str(item["updated_at"]), str(item["account_id"])) < position]
        rows = projected[: limit + 1]
    else:
        conditions = ["a.deleted_at IS NULL"]
        parameters: list[Any] = []
        if normalized:
            escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append("(lower(a.name) LIKE ? ESCAPE '\\' OR lower(a.normalized_name) LIKE ? ESCAPE '\\' OR lower(coalesce(a.summary,'')) LIKE ? ESCAPE '\\' OR lower(coalesce(a.region,'')) LIKE ? ESCAPE '\\' OR lower(coalesce(a.sector,'')) LIKE ? ESCAPE '\\' OR lower(coalesce(a.owner,'')) LIKE ? ESCAPE '\\')")
            parameters.extend([f"%{escaped}%"] * 6)
        for key, value in filters.items():
            conditions.append(f"a.{key} = ?")
            parameters.append(value)
        if updated_since_value is not None:
            conditions.append("julianday(a.updated_at) >= julianday(?)")
            parameters.append(updated_since_value)
        if position:
            conditions.append("(a.updated_at < ? OR (a.updated_at = ? AND a.account_id < ?))")
            parameters.extend([position[0], position[0], position[1]])
        sql = f"""
            SELECT a.account_id, a.name, a.region, a.sector, a.owner, a.lifecycle_stage, a.health,
                   a.summary, a.project_id, a.version, a.updated_at,
                   (SELECT max(occurred_at) FROM activities x WHERE x.account_id=a.account_id AND x.deleted_at IS NULL) AS last_activity_at,
                   (SELECT count(*) FROM actions x WHERE x.account_id=a.account_id AND x.deleted_at IS NULL AND x.status NOT IN ('completed','cancelled')) AS open_actions,
                   (SELECT count(*) FROM risks x WHERE x.account_id=a.account_id AND x.deleted_at IS NULL AND x.status NOT IN ('closed','resolved','cancelled')) AS open_risks
            FROM accounts a WHERE {' AND '.join(conditions)}
            ORDER BY a.updated_at DESC, a.account_id DESC LIMIT ?
        """
        parameters.append(limit + 1)
        with _sqlite_connection(backend) as connection:
            rows = [dict(row) for row in connection.execute(sql, parameters).fetchall()]
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor("accounts", [str(rows[-1]["updated_at"]), str(rows[-1]["account_id"])], scope) if has_more and rows else None
    return {"backend": backend.backend, "binding_id": backend.binding_id, "rows": rows, "next_cursor": next_cursor, "has_more": has_more}


SECTIONS = (
    "contacts", "opportunities", "activities", "commitments", "risks", "signals", "actions",
    "resource_requests", "sales_assets", "evidence_refs", "task_links", "artifacts",
)


def read_account_360(
    project_root: Path | str,
    account_id: str,
    *,
    sections: Sequence[str] | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    if not isinstance(account_id, str) or not account_id or len(account_id) > 128 or any(char in account_id for char in "\0\r\n"):
        raise BusinessBackendError("INVALID_INPUT", "客户 ID 无效")
    selected = tuple(sections or SECTIONS)
    if len(set(selected)) != len(selected) or any(section not in SECTIONS for section in selected):
        raise BusinessBackendError("INVALID_INPUT", "客户 360 分区无效")
    if since is not None:
        if not isinstance(since, str) or not since or len(since) > 64:
            raise BusinessBackendError("INVALID_INPUT", "since 无效")
        try:
            datetime.fromisoformat(since[:-1] + "+00:00" if since.endswith("Z") else since)
        except ValueError as error:
            raise BusinessBackendError("INVALID_INPUT", "since 必须是有效的 ISO 8601 日期或时间") from error
    backend = resolve_business_backend(project_root)
    if backend.backend == "csv":
        customer_rows = _complete_business_csv_rows(backend.root, "data/sales/customers.csv")
        customer = next((row for row in customer_rows if row.get("customer_id") == account_id), None)
        if customer is None:
            raise BusinessBackendError("NOT_FOUND", "客户不存在")
        account = {
            "account_id": account_id, "name": customer.get("customer_name", ""), "region": customer.get("region", ""),
            "sector": customer.get("sector", ""), "owner": customer.get("owner", ""),
            "lifecycle_stage": customer.get("stage", ""), "health": customer.get("health", ""),
            "budget_path": customer.get("budget_path", ""), "summary": "", "project_id": "",
            "version": _row_version(customer), "updated_at": customer.get("updated_at", ""),
        }
        result: dict[str, Any] = {"backend": "csv", "binding_id": backend.binding_id, "account": account, "sections": {}}
        contacts = []
        for index, (field, role) in enumerate((("key_contact", "key_contact"), ("decision_maker", "decision_maker"))):
            if customer.get(field, "").strip():
                contacts.append({"contact_id": f"legacy-{account_id}-{index}", "display_name": customer[field], "role": role, "identity_status": "legacy_text", "version": account["version"]})
        legacy: dict[str, list[dict[str, Any]]] = {
            "contacts": contacts,
            "opportunities": [],
            "activities": [dict(row, version=_row_version(row)) for row in (_complete_business_csv_rows(backend.root, "data/sales/activities.csv") if "activities" in selected else []) if row.get("customer_id") == account_id and (not since or row.get("occurred_at", "") >= since)],
            "commitments": [],
            "risks": ([{"risk_id": f"legacy-risk-{account_id}", "account_id": account_id, "risk_text": customer.get("risks", ""), "status": "open", "version": account["version"]}] if customer.get("risks", "").strip() else []),
            "signals": [],
            "actions": ([{"action_id": f"legacy-action-{account_id}", "account_id": account_id, "action_text": customer.get("next_action", ""), "due_at": customer.get("next_action_due", ""), "status": "open", "origin": "imported", "version": account["version"]}] if customer.get("next_action", "").strip() else []),
            "resource_requests": [dict(row, version=_row_version(row)) for row in (_complete_business_csv_rows(backend.root, "data/sales/resource-requests.csv") if "resource_requests" in selected else []) if row.get("customer_id") == account_id],
            "sales_assets": [dict(row, version=_row_version(row)) for row in (_complete_business_csv_rows(backend.root, "data/sales/sales-assets.csv") if "sales_assets" in selected else []) if row.get("customer_id") == account_id],
            "evidence_refs": [], "task_links": [], "artifacts": [],
        }
        truncated_sections: list[str] = []
        for section in selected:
            values = legacy[section]
            if section == "activities":
                values.sort(key=lambda item: (str(item.get("occurred_at", "")), str(item.get("activity_id", ""))), reverse=True)
            if len(values) > MAX_360_SECTION_ROWS:
                truncated_sections.append(section)
            result["sections"][section] = values[:MAX_360_SECTION_ROWS]
        result["truncated_sections"] = truncated_sections
        return result

    queries = {
        "contacts": "SELECT c.*, ac.account_contact_id, ac.role, ac.influence_level, ac.decision_role, ac.relationship_status, ac.is_primary, ac.version AS relationship_version FROM account_contacts ac JOIN contacts c ON c.contact_id=ac.contact_id WHERE ac.account_id=? AND ac.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY ac.is_primary DESC, c.display_name, c.contact_id LIMIT ?",
        "opportunities": "SELECT * FROM opportunities WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, opportunity_id LIMIT ?",
        "activities": "SELECT * FROM activities WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR occurred_at>=?) ORDER BY occurred_at DESC, activity_id DESC LIMIT ?",
        "commitments": "SELECT * FROM commitments WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, commitment_id LIMIT ?",
        "risks": "SELECT * FROM risks WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, risk_id LIMIT ?",
        "signals": "SELECT * FROM signals WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR last_seen_at>=?) ORDER BY last_seen_at DESC, signal_id LIMIT ?",
        "actions": "SELECT * FROM actions WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, action_id LIMIT ?",
        "resource_requests": "SELECT * FROM resource_requests WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, request_id LIMIT ?",
        "sales_assets": "SELECT * FROM sales_assets WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, asset_id LIMIT ?",
        "task_links": "SELECT * FROM task_links WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, task_link_id LIMIT ?",
        "artifacts": "SELECT * FROM artifacts WHERE account_id=? AND deleted_at IS NULL AND (? IS NULL OR updated_at>=?) ORDER BY updated_at DESC, artifact_id LIMIT ?",
    }
    with _sqlite_connection(backend) as connection:
        account_row = connection.execute("SELECT * FROM accounts WHERE account_id=? AND deleted_at IS NULL", (account_id,)).fetchone()
        if account_row is None:
            raise BusinessBackendError("NOT_FOUND", "客户不存在")
        section_rows: dict[str, list[dict[str, Any]]] = {}
        truncated_sections: list[str] = []
        entity_refs: list[tuple[str, str]] = [("accounts", account_id)]
        for section in selected:
            if section == "evidence_refs":
                continue
            parameters: tuple[Any, ...] = (
                (account_id, MAX_360_SECTION_ROWS + 1)
                if section == "contacts"
                else (account_id, since, since, MAX_360_SECTION_ROWS + 1)
            )
            values = [dict(row) for row in connection.execute(queries[section], parameters).fetchall()]
            if len(values) > MAX_360_SECTION_ROWS:
                truncated_sections.append(section)
                values = values[:MAX_360_SECTION_ROWS]
            section_rows[section] = values
            id_name = {
                "contacts": "contact_id", "opportunities": "opportunity_id", "activities": "activity_id",
                "commitments": "commitment_id", "risks": "risk_id", "signals": "signal_id", "actions": "action_id",
                "resource_requests": "request_id", "sales_assets": "asset_id", "task_links": "task_link_id",
                "artifacts": "artifact_id",
            }[section]
            entity_type = section
            entity_refs.extend((entity_type, str(item[id_name])) for item in values if item.get(id_name))
        if "evidence_refs" in selected:
            evidence_rows: list[dict[str, Any]] = []
            for offset in range(0, len(entity_refs), 300):
                chunk = entity_refs[offset:offset + 300]
                clauses = " OR ".join("(entity_type=? AND entity_id=?)" for _ in chunk)
                parameters = [value for pair in chunk for value in pair]
                evidence_rows.extend(dict(row) for row in connection.execute(
                    f"""SELECT e.*,
                               s.title AS source_title,
                               s.url AS source_url,
                               s.publisher AS source_publisher,
                               s.accessed_date AS source_accessed_date,
                               s.status AS source_status
                        FROM evidence_refs e
                        LEFT JOIN sources s ON s.source_id=e.source_id AND s.deleted_at IS NULL
                        WHERE e.deleted_at IS NULL AND ({clauses})
                        ORDER BY e.entity_type, e.entity_id, e.evidence_ref_id""",
                    parameters,
                ).fetchall())
                if len(evidence_rows) > MAX_360_SECTION_ROWS:
                    truncated_sections.append("evidence_refs")
                    break
            evidence_rows.sort(key=lambda item: (str(item.get("entity_type", "")), str(item.get("entity_id", "")), str(item.get("evidence_ref_id", ""))))
            section_rows["evidence_refs"] = evidence_rows[:MAX_360_SECTION_ROWS]
    return {"backend": "sqlite", "binding_id": backend.binding_id, "account": dict(account_row), "sections": section_rows, "truncated_sections": sorted(set(truncated_sections))}


def read_signals(
    project_root: Path | str,
    *,
    account_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    backend = resolve_business_backend(project_root)
    limit = _limit(limit)
    for value, label in ((account_id, "account_id"), (status, "status"), (severity, "severity")):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 128):
            raise BusinessBackendError("INVALID_INPUT", f"{label} 无效")
    scope = _cursor_scope({"kind": "signals", "filters": {"account_id": account_id, "status": status, "severity": severity}})
    position = _decode_cursor(cursor, "signals", 2, scope)
    if backend.backend == "csv":
        return {"backend": "csv", "binding_id": backend.binding_id, "rows": [], "next_cursor": None, "has_more": False}
    conditions = ["deleted_at IS NULL"]
    parameters: list[Any] = []
    for column, value in (("account_id", account_id), ("status", status), ("severity", severity)):
        if value is not None:
            conditions.append(f"{column}=?")
            parameters.append(value)
    if position:
        conditions.append("(last_seen_at < ? OR (last_seen_at = ? AND signal_id < ?))")
        parameters.extend([position[0], position[0], position[1]])
    parameters.append(limit + 1)
    with _sqlite_connection(backend) as connection:
        rows = [dict(row) for row in connection.execute(
            f"SELECT * FROM signals WHERE {' AND '.join(conditions)} ORDER BY last_seen_at DESC, signal_id DESC LIMIT ?",
            parameters,
        ).fetchall()]
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor("signals", [str(rows[-1]["last_seen_at"]), str(rows[-1]["signal_id"])], scope) if has_more and rows else None
    return {"backend": "sqlite", "binding_id": backend.binding_id, "rows": rows, "next_cursor": next_cursor, "has_more": has_more}


TIMELINE_KINDS = ("activity", "commitment", "task_link", "write_receipt", "sales_asset", "artifact")


def _account_id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(char in value for char in "\0\r\n"):
        raise BusinessBackendError("INVALID_INPUT", "客户 ID 无效")
    return value


def _timeline_row(
    kind: str,
    identity: str,
    event_at: str,
    title: str,
    *,
    summary: str = "",
    status: str = "",
    target_section: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timeline_id": f"{kind}:{identity}",
        "kind": kind,
        "event_at": event_at,
        "title": title,
        "summary": summary,
        "status": status,
        "target_section": target_section,
        "evidence_type": kind,
        "evidence_id": identity,
        **dict(extra or {}),
    }


def read_account_timeline(
    project_root: Path | str,
    account_id: str,
    *,
    kinds: Sequence[str] | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return a read-only, evidence-addressable account timeline.

    The timeline only projects existing records.  It does not infer meetings,
    commitments or approvals that are absent from the selected backend.
    """
    account_id = _account_id(account_id)
    limit = _limit(limit)
    selected = tuple(kinds or TIMELINE_KINDS)
    if not selected or len(set(selected)) != len(selected) or any(kind not in TIMELINE_KINDS for kind in selected):
        raise BusinessBackendError("INVALID_INPUT", "客户时间线类别无效")
    scope = _cursor_scope({"kind": "account_timeline", "account_id": account_id, "kinds": sorted(selected)})
    position = _decode_cursor(cursor, "account_timeline", 3, scope)
    backend = resolve_business_backend(project_root)
    items: list[dict[str, Any]] = []
    timeline_truncated = False

    def bounded(values: Sequence[Any]) -> Sequence[Any]:
        nonlocal timeline_truncated
        if len(values) > MAX_TIMELINE_SCAN_ROWS:
            timeline_truncated = True
        return values[:MAX_TIMELINE_SCAN_ROWS]

    if backend.backend == "csv":
        scanned_customers = _csv_rows(backend.root, "data/sales/customers.csv", MAX_TIMELINE_CSV_SCAN_ROWS + 1)
        if len(scanned_customers) > MAX_TIMELINE_CSV_SCAN_ROWS:
            timeline_truncated = True
        customer = next((row for row in scanned_customers[:MAX_TIMELINE_CSV_SCAN_ROWS] if row.get("customer_id") == account_id), None)
        if customer is None:
            if len(scanned_customers) > MAX_TIMELINE_CSV_SCAN_ROWS:
                raise BusinessBackendError("SCAN_LIMIT", "客户文件超过受控扫描上限，无法确认该客户是否存在")
            raise BusinessBackendError("NOT_FOUND", "客户不存在")
        activities: list[dict[str, str]] = []
        if "activity" in selected or "commitment" in selected:
            scanned_activities = _csv_rows(backend.root, "data/sales/activities.csv", MAX_TIMELINE_CSV_SCAN_ROWS + 1)
            if len(scanned_activities) > MAX_TIMELINE_CSV_SCAN_ROWS:
                timeline_truncated = True
            activities = [
                row for row in scanned_activities[:MAX_TIMELINE_CSV_SCAN_ROWS]
                if row.get("customer_id") == account_id
            ]
            activities.sort(key=lambda row: (str(row.get("occurred_at") or row.get("created_at") or ""), str(row.get("activity_id") or "")), reverse=True)
        if "activity" in selected:
            for row in bounded(activities):
                identity = str(row.get("activity_id") or "")
                event_at = str(row.get("occurred_at") or row.get("created_at") or "")
                if identity and event_at:
                    items.append(_timeline_row(
                        "activity", identity, event_at, str(row.get("summary") or "客户互动"),
                        summary=" · ".join(value for value in (row.get("channel", ""), row.get("activity_type", "")) if value),
                        status=str(row.get("evidence_status") or ""), target_section="activities",
                    ))
        if "commitment" in selected:
            commitments = [row for row in activities if str(row.get("commitment") or "").strip()]
            for row in bounded(commitments):
                commitment = str(row.get("commitment") or "").strip()
                activity_id = str(row.get("activity_id") or "")
                event_at = str(row.get("occurred_at") or row.get("created_at") or "")
                if commitment and activity_id and event_at:
                    items.append(_timeline_row(
                        "commitment", f"legacy-{activity_id}", event_at, commitment,
                        summary="来自已记录互动", status="legacy_text", target_section="commitments",
                        extra={"source_activity_id": activity_id},
                    ))
        if "sales_asset" in selected:
            scanned_artifacts = _csv_rows(backend.root, "data/sales/sales-assets.csv", MAX_TIMELINE_CSV_SCAN_ROWS + 1)
            if len(scanned_artifacts) > MAX_TIMELINE_CSV_SCAN_ROWS:
                timeline_truncated = True
            artifacts = [
                row for row in scanned_artifacts[:MAX_TIMELINE_CSV_SCAN_ROWS]
                if row.get("customer_id") == account_id
            ]
            artifacts.sort(key=lambda row: (str(row.get("updated_at") or row.get("last_validated_at") or ""), str(row.get("asset_id") or "")), reverse=True)
            for row in bounded(artifacts):
                identity = str(row.get("asset_id") or "")
                event_at = str(row.get("updated_at") or row.get("last_validated_at") or "")
                if row.get("customer_id") == account_id and identity and event_at:
                    items.append(_timeline_row(
                        "sales_asset", identity, event_at, str(row.get("title") or "销售资料"),
                        summary=str(row.get("asset_type") or ""), status=str(row.get("status") or ""),
                        target_section="sales_assets", extra={"relative_path": row.get("source_path", "")},
                    ))
    else:
        with _sqlite_connection(backend) as connection:
            if connection.execute("SELECT 1 FROM accounts WHERE account_id=? AND deleted_at IS NULL", (account_id,)).fetchone() is None:
                raise BusinessBackendError("NOT_FOUND", "客户不存在")
            scan_limit = MAX_TIMELINE_SCAN_ROWS + 1
            if "activity" in selected:
                rows = connection.execute(
                    "SELECT activity_id,occurred_at,summary,channel,activity_type,evidence_status FROM activities WHERE account_id=? AND deleted_at IS NULL ORDER BY occurred_at DESC,activity_id DESC LIMIT ?",
                    (account_id, scan_limit),
                ).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "activity", row["activity_id"], row["occurred_at"], row["summary"],
                        summary=" · ".join(str(value) for value in (row["channel"], row["activity_type"]) if value),
                        status=row["evidence_status"], target_section="activities",
                    ))
            if "commitment" in selected:
                rows = connection.execute(
                    "SELECT commitment_id,updated_at,commitment_text,status,due_at,direction,source_activity_id FROM commitments WHERE account_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,commitment_id DESC LIMIT ?",
                    (account_id, scan_limit),
                ).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "commitment", row["commitment_id"], row["updated_at"], row["commitment_text"],
                        summary=(f"承诺期限：{row['due_at']}" if row["due_at"] else ""), status=row["status"],
                        target_section="commitments", extra={"due_at": row["due_at"], "direction": row["direction"], "source_activity_id": row["source_activity_id"]},
                    ))
            if "task_link" in selected:
                rows = connection.execute(
                    "SELECT task_link_id,task_id,relation_type,updated_at,project_id FROM task_links WHERE account_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,task_link_id DESC LIMIT ?",
                    (account_id, scan_limit),
                ).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "task_link", row["task_link_id"], row["updated_at"], "关联任务",
                        summary=str(row["relation_type"] or ""), status="linked", target_section="task_links",
                        extra={"task_id": row["task_id"], "project_id": row["project_id"]},
                    ))
            if "artifact" in selected:
                rows = connection.execute(
                    "SELECT artifact_id,relative_path,artifact_type,status,task_id,updated_at FROM artifacts WHERE account_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,artifact_id DESC LIMIT ?",
                    (account_id, scan_limit),
                ).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "artifact", row["artifact_id"], row["updated_at"], str(row["relative_path"] or "产物"),
                        summary=str(row["artifact_type"] or ""), status=row["status"], target_section="artifacts",
                        extra={"relative_path": row["relative_path"], "task_id": row["task_id"]},
                    ))
            if "sales_asset" in selected:
                rows = connection.execute(
                    "SELECT asset_id,title,asset_type,status,source_path,updated_at FROM sales_assets WHERE account_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,asset_id DESC LIMIT ?",
                    (account_id, scan_limit),
                ).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "sales_asset", row["asset_id"], row["updated_at"], str(row["title"] or "销售资料"),
                        summary=str(row["asset_type"] or ""), status=row["status"], target_section="sales_assets",
                        extra={"relative_path": row["source_path"] or ""},
                    ))
            if "write_receipt" in selected:
                receipt_sql = """
                    SELECT wr.intent_id,wr.task_id,wr.logical_tool,wr.status,wr.committed_at
                    FROM write_receipts wr
                    WHERE wr.status='committed'
                      AND json_extract(wr.result_json,'$.approved_payload_sha256') IS NOT NULL
                      AND (
                        EXISTS (
                          SELECT 1 FROM json_each(wr.result_json,'$.mutations') mutation
                          WHERE
                            (json_extract(mutation.value,'$.table')='accounts' AND json_extract(mutation.value,'$.record_id')=?) OR
                            (json_extract(mutation.value,'$.table')='activities' AND json_extract(mutation.value,'$.record_id') IN (SELECT activity_id FROM activities WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='commitments' AND json_extract(mutation.value,'$.record_id') IN (SELECT commitment_id FROM commitments WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='actions' AND json_extract(mutation.value,'$.record_id') IN (SELECT action_id FROM actions WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='resource_requests' AND json_extract(mutation.value,'$.record_id') IN (SELECT request_id FROM resource_requests WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='sales_assets' AND json_extract(mutation.value,'$.record_id') IN (SELECT asset_id FROM sales_assets WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='task_links' AND json_extract(mutation.value,'$.record_id') IN (SELECT task_link_id FROM task_links WHERE account_id=?)) OR
                            (json_extract(mutation.value,'$.table')='artifacts' AND json_extract(mutation.value,'$.record_id') IN (SELECT artifact_id FROM artifacts WHERE account_id=?))
                        )
                        OR EXISTS (SELECT 1 FROM task_links link WHERE link.task_id=wr.task_id AND link.account_id=? AND link.deleted_at IS NULL)
                      )
                    ORDER BY wr.committed_at DESC,wr.intent_id DESC LIMIT ?
                """
                parameters = (account_id, account_id, account_id, account_id, account_id, account_id, account_id, account_id, account_id, scan_limit)
                rows = connection.execute(receipt_sql, parameters).fetchall()
                for row in bounded(rows):
                    items.append(_timeline_row(
                        "write_receipt", row["intent_id"], row["committed_at"], "已批准写入",
                        summary=str(row["logical_tool"] or ""), status=row["status"], target_section="write_receipts",
                        extra={"task_id": row["task_id"]},
                    ))

    items.sort(key=lambda item: (str(item["event_at"]), str(item["kind"]), str(item["timeline_id"])), reverse=True)
    if position:
        items = [item for item in items if (str(item["event_at"]), str(item["kind"]), str(item["timeline_id"])) < position]
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = (
        _encode_cursor("account_timeline", [str(page[-1]["event_at"]), str(page[-1]["kind"]), str(page[-1]["timeline_id"])], scope)
        if has_more and page else None
    )
    return {
        "backend": backend.backend, "binding_id": backend.binding_id, "account_id": account_id,
        "rows": page, "next_cursor": next_cursor, "has_more": has_more,
        "truncated": timeline_truncated,
    }


def _focus_item(
    account_id: str,
    account_name: str,
    kind: str,
    reason: str,
    *,
    due_at: str | None,
    event_at: str | None,
    target_section: str,
    evidence_type: str,
    evidence_id: str,
    severity: str = "attention",
) -> dict[str, Any]:
    return {
        "focus_id": f"{kind}:{evidence_type}:{evidence_id}",
        "account_id": account_id, "account_name": account_name, "kind": kind, "reason": reason,
        "due_at": due_at, "event_at": event_at, "target_section": target_section,
        "evidence_type": evidence_type, "evidence_id": evidence_id, "severity": severity,
    }


def read_today_focus(
    project_root: Path | str,
    *,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project current attention facts without persisting inferred signals.

    This is intentionally a deterministic read model, not the A4 rule engine.
    """
    limit = _limit(limit)
    if now is not None and not isinstance(now, datetime):
        raise BusinessBackendError("INVALID_INPUT", "now 必须是 datetime")
    local_zone = datetime.now().astimezone().tzinfo or timezone.utc
    reference = now or datetime.now().astimezone()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=local_zone)
    reference = reference.astimezone(local_zone)
    reference_utc = reference.astimezone(timezone.utc)
    today = reference.date()
    horizon = today + timedelta(days=FOCUS_DUE_DAYS)
    stale_before = today - timedelta(days=FOCUS_STALE_DAYS)
    backend = resolve_business_backend(project_root)
    items: list[dict[str, Any]] = []

    def parsed_date(value: Any) -> Any:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return _parse_iso8601(text, "业务日期").date()
        except BusinessBackendError:
            return None

    def parsed_moment(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return _parse_iso8601(text, "业务时间")
        except BusinessBackendError:
            return None

    if backend.backend == "csv":
        customers = {
            str(row.get("customer_id") or ""): row
            for row in _complete_business_csv_rows(backend.root, "data/sales/customers.csv")
            if row.get("customer_id")
        }
        for account_id, row in customers.items():
            name = str(row.get("customer_name") or "")
            action_due = parsed_moment(row.get("next_action_due"))
            if str(row.get("next_action") or "").strip() and action_due and action_due < reference_utc:
                items.append(_focus_item(
                    account_id, name, "overdue_action", f"行动已逾期：{row['next_action']}",
                    due_at=str(row.get("next_action_due") or ""), event_at=None, target_section="actions",
                    evidence_type="account", evidence_id=account_id, severity="high",
                ))
            last_activity = parsed_date(row.get("last_evidence_date"))
            if last_activity and last_activity < stale_before:
                items.append(_focus_item(
                    account_id, name, "stale_account", f"超过 {FOCUS_STALE_DAYS} 天没有已记录互动",
                    due_at=None, event_at=str(row.get("last_evidence_date") or ""), target_section="activities",
                    evidence_type="account", evidence_id=account_id,
                ))
        for row in _complete_business_csv_rows(backend.root, "data/sales/resource-requests.csv"):
            account_id = str(row.get("customer_id") or "")
            deadline = parsed_date(row.get("deadline"))
            status = str(row.get("status") or "").casefold()
            if account_id in customers and deadline and deadline <= horizon and status not in {"completed", "closed", "rejected", "cancelled"}:
                items.append(_focus_item(
                    account_id, str(customers[account_id].get("customer_name") or ""), "resource_deadline",
                    f"资源申请临近期限：{row.get('request_summary') or '未命名申请'}",
                    due_at=str(row.get("deadline") or ""), event_at=str(row.get("requested_at") or ""),
                    target_section="resource_requests", evidence_type="resource_request",
                    evidence_id=str(row.get("request_id") or ""), severity="high" if deadline < today else "attention",
                ))
    else:
        with _sqlite_connection(backend) as connection:
            scan_limit = MAX_TIMELINE_SCAN_ROWS + 1
            existing_signal_subjects: set[tuple[str, str, str]] = set()
            signal_reason = {
                "overdue_action": "已有规则信号：行动逾期",
                "stale_account": "已有规则信号：客户长期无互动",
                "commitment_due": "已有规则信号：承诺临近期限",
                "missing_critical_field": "已有规则信号：客户关键信息缺失",
                "resource_deadline": "已有规则信号：资源申请临近期限",
            }
            for row in connection.execute(
                "SELECT x.signal_id,x.account_id,a.name,x.signal_type,x.subject_type,x.subject_id,x.severity,x.last_seen_at FROM signals x JOIN accounts a ON a.account_id=x.account_id AND a.deleted_at IS NULL WHERE x.deleted_at IS NULL AND x.resolved_at IS NULL AND x.status='open' ORDER BY x.last_seen_at DESC,x.signal_id DESC LIMIT ?",
                (scan_limit,),
            ).fetchall():
                account_id = str(row["account_id"])
                existing_signal_subjects.add((str(row["signal_type"]), str(row["subject_type"]), str(row["subject_id"])))
                items.append(_focus_item(
                    account_id, str(row["name"]), str(row["signal_type"]), signal_reason.get(str(row["signal_type"]), "已有待处理规则信号"),
                    due_at=None, event_at=row["last_seen_at"], target_section="signals",
                    evidence_type="signal", evidence_id=row["signal_id"], severity=str(row["severity"] or "attention"),
                ))
            for row in connection.execute(
                "SELECT x.action_id,x.account_id,a.name,x.action_text,x.due_at FROM actions x JOIN accounts a ON a.account_id=x.account_id AND a.deleted_at IS NULL WHERE x.deleted_at IS NULL AND x.due_at IS NOT NULL AND julianday(x.due_at)<julianday(?) AND x.status NOT IN ('completed','cancelled') ORDER BY julianday(x.due_at),x.action_id LIMIT ?",
                (reference_utc.isoformat(), scan_limit),
            ).fetchall():
                if ("overdue_action", "action", str(row["action_id"])) not in existing_signal_subjects:
                    items.append(_focus_item(
                        row["account_id"], row["name"], "overdue_action", f"行动已逾期：{row['action_text']}",
                        due_at=row["due_at"], event_at=None, target_section="actions", evidence_type="action",
                        evidence_id=row["action_id"], severity="high",
                    ))
            for row in connection.execute(
                "SELECT x.commitment_id,x.account_id,a.name,x.commitment_text,x.due_at,x.updated_at FROM commitments x JOIN accounts a ON a.account_id=x.account_id AND a.deleted_at IS NULL WHERE x.deleted_at IS NULL AND x.due_at IS NOT NULL AND date(x.due_at)<=date(?) AND x.status IN ('open','overdue','unknown') ORDER BY x.due_at,x.commitment_id LIMIT ?",
                (horizon.isoformat(), scan_limit),
            ).fetchall():
                if ("commitment_due", "commitment", str(row["commitment_id"])) not in existing_signal_subjects:
                    items.append(_focus_item(
                        row["account_id"], row["name"], "commitment_due", f"承诺临近期限：{row['commitment_text']}",
                        due_at=row["due_at"], event_at=row["updated_at"], target_section="commitments",
                        evidence_type="commitment", evidence_id=row["commitment_id"], severity="high" if parsed_date(row["due_at"]) and parsed_date(row["due_at"]) < today else "attention",
                    ))
            for row in connection.execute(
                "SELECT x.request_id,x.account_id,a.name,x.request_summary,x.deadline,x.requested_at FROM resource_requests x JOIN accounts a ON a.account_id=x.account_id AND a.deleted_at IS NULL WHERE x.deleted_at IS NULL AND x.deadline IS NOT NULL AND date(x.deadline)<=date(?) AND lower(coalesce(x.status,'')) NOT IN ('completed','closed','rejected','cancelled') ORDER BY x.deadline,x.request_id LIMIT ?",
                (horizon.isoformat(), scan_limit),
            ).fetchall():
                if ("resource_deadline", "resource_request", str(row["request_id"])) not in existing_signal_subjects:
                    deadline = parsed_date(row["deadline"])
                    items.append(_focus_item(
                        row["account_id"], row["name"], "resource_deadline", f"资源申请临近期限：{row['request_summary']}",
                        due_at=row["deadline"], event_at=row["requested_at"], target_section="resource_requests",
                        evidence_type="resource_request", evidence_id=row["request_id"], severity="high" if deadline and deadline < today else "attention",
                    ))
            for row in connection.execute(
                """
                SELECT a.account_id,a.name,a.created_at,max(x.occurred_at) AS last_activity_at
                FROM accounts a LEFT JOIN activities x ON x.account_id=a.account_id AND x.deleted_at IS NULL
                WHERE a.deleted_at IS NULL
                GROUP BY a.account_id,a.name,a.created_at
                HAVING date(coalesce(max(x.occurred_at),a.created_at))<date(?)
                ORDER BY coalesce(max(x.occurred_at),a.created_at),a.account_id LIMIT ?
                """,
                (stale_before.isoformat(), scan_limit),
            ).fetchall():
                if ("stale_account", "account", str(row["account_id"])) not in existing_signal_subjects:
                    last_seen = row["last_activity_at"] or row["created_at"]
                    items.append(_focus_item(
                        row["account_id"], row["name"], "stale_account", f"超过 {FOCUS_STALE_DAYS} 天没有已记录互动",
                        due_at=None, event_at=last_seen, target_section="activities", evidence_type="account",
                        evidence_id=row["account_id"],
                    ))

    rank = {"overdue_action": 0, "commitment_due": 1, "resource_deadline": 2, "missing_critical_field": 3, "stale_account": 4}
    items.sort(key=lambda item: (
        rank.get(str(item["kind"]), 3), str(item.get("due_at") or "9999-12-31"),
        str(item.get("event_at") or ""), str(item["account_id"]), str(item["focus_id"]),
    ))
    truncated = len(items) > limit
    return {
        "backend": backend.backend, "binding_id": backend.binding_id,
        "generated_at": reference.isoformat(), "rows": items[:limit],
        "truncated": truncated, "read_model": "deterministic_attention_projection_v1",
    }


KNOWLEDGE_FIELDS = (
    "source_id", "title", "url", "publisher", "published_date", "accessed_date", "region", "topic",
    "source_type", "quality", "exposure_status", "key_facts", "important_quotes", "interpretation",
    "limitations", "status", "notes",
)


def knowledge_entries(project_root: Path | str, limit: int = 500) -> dict[str, Any]:
    backend = resolve_business_backend(project_root)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 5000:
        raise BusinessBackendError("INVALID_INPUT", "知识条目 limit 无效")
    if backend.backend == "csv":
        rows = _csv_rows(backend.root, "data/knowledge/source-register.csv", limit + 1)
        entries = [{field: str(row.get(field) or "")[:12000] for field in KNOWLEDGE_FIELDS} | {"_record_version": _row_version(row)} for row in rows[:limit]]
    else:
        with _sqlite_connection(backend) as connection:
            raw = connection.execute("""
                SELECT source_id,title,url,publisher,published_date,accessed_date,region,topic,source_type,quality,
                       exposure_status,legacy_key_facts AS key_facts,legacy_important_quotes AS important_quotes,
                       legacy_interpretation AS interpretation,limitations,status,notes,version AS _record_version
                FROM sources WHERE deleted_at IS NULL
                ORDER BY coalesce(accessed_date,'') DESC, coalesce(published_date,'') DESC, source_id DESC LIMIT ?
            """, (limit + 1,)).fetchall()
        entries = [{key: ("" if value is None else value) for key, value in dict(row).items()} for row in raw[:limit]]
        rows = list(raw)
    entries.sort(key=lambda row: (str(row.get("accessed_date", "")), str(row.get("published_date", "")), str(row.get("source_id", ""))), reverse=True)
    return {"backend": backend.backend, "version": backend.binding_id, "entries": entries, "truncated": len(rows) > limit}


def knowledge_urls(project_root: Path | str) -> set[str]:
    return {str(row.get("url") or "").strip() for row in knowledge_entries(project_root, 5000)["entries"] if str(row.get("url") or "").strip()}


def data_summary(project_root: Path | str) -> dict[str, Any]:
    backend = resolve_business_backend(project_root)
    if backend.backend == "csv":
        return {"backend": "csv", "binding_id": backend.binding_id}
    tables = {"knowledge": ("sources",), "sales": ("accounts", "activities", "resource_requests", "sales_assets")}
    with _sqlite_connection(backend) as connection:
        result = {
            group: [
                {"path": f"SQLite · {table}", "exists": True, "records": connection.execute(f"SELECT count(*) FROM {table} WHERE deleted_at IS NULL").fetchone()[0], "updated_at": None, "version": backend.binding_id}
                for table in names
            ]
            for group, names in tables.items()
        }
    return {"backend": "sqlite", "binding_id": backend.binding_id, **result}


def search_business_records(project_root: Path | str, query: str, limit: int = 60) -> list[dict[str, Any]]:
    normalized = _normalize_query(query)
    if not normalized:
        return []
    backend = resolve_business_backend(project_root)
    results: list[dict[str, Any]] = []
    if backend.backend == "csv":
        sources = (
            ("data/sales/customers.csv", "客户", "customer_name", "customer_id", ("region", "sector", "owner", "stage", "health"), ("risks", "next_action")),
            ("data/sales/activities.csv", "跟进", "summary", "activity_id", ("occurred_at", "channel", "activity_type"), ("commitment", "next_action")),
            ("data/sales/resource-requests.csv", "资源", "request_summary", "request_id", ("resource_type", "owner", "status", "deadline"), ("business_reason", "decision")),
            ("data/sales/sales-assets.csv", "资料", "title", "asset_id", ("asset_type", "owner", "status"), ("use_case", "usage_feedback")),
        )
        for relative, kind, title, identity, subtitle_fields, snippet_fields in sources:
            for row in _csv_rows(backend.root, relative):
                if normalized in "\n".join(str(value) for value in row.values()).casefold():
                    results.append({
                        "kind": kind,
                        "title": row.get(title) or f"未命名{kind}",
                        "subtitle": " · ".join(row.get(field, "") for field in subtitle_fields if row.get(field, ""))[:200],
                        "snippet": " · ".join(row.get(field, "") for field in snippet_fields if row.get(field, ""))[:400],
                        "reference": row.get(identity) or relative,
                    })
                    if len(results) >= limit:
                        return results
        return results
    patterns = [f"%{normalized.replace('%', '').replace('_', '')}%"]
    queries = (
        ("客户", "SELECT account_id AS reference,name AS title,coalesce(owner,'') AS subtitle,coalesce(summary,'') AS snippet FROM accounts WHERE deleted_at IS NULL AND lower(name||' '||coalesce(region,'')||' '||coalesce(sector,'')||' '||coalesce(owner,'')||' '||coalesce(summary,'')) LIKE ? ORDER BY updated_at DESC LIMIT ?"),
        ("跟进", "SELECT activity_id AS reference,summary AS title,coalesce(occurred_at,'') AS subtitle,coalesce(channel,'')||' · '||coalesce(activity_type,'') AS snippet FROM activities WHERE deleted_at IS NULL AND lower(summary||' '||coalesce(participants_text,'')||' '||coalesce(channel,'')) LIKE ? ORDER BY occurred_at DESC LIMIT ?"),
        ("资源", "SELECT request_id AS reference,request_summary AS title,coalesce(status,'') AS subtitle,coalesce(business_reason,'') AS snippet FROM resource_requests WHERE deleted_at IS NULL AND lower(request_summary||' '||coalesce(business_reason,'')||' '||coalesce(owner,'')) LIKE ? ORDER BY requested_at DESC LIMIT ?"),
        ("资料", "SELECT asset_id AS reference,title,coalesce(status,'') AS subtitle,coalesce(use_case,'') AS snippet FROM sales_assets WHERE deleted_at IS NULL AND lower(title||' '||coalesce(use_case,'')||' '||coalesce(owner,'')) LIKE ? ORDER BY updated_at DESC LIMIT ?"),
    )
    with _sqlite_connection(backend) as connection:
        for kind, sql in queries:
            for row in connection.execute(sql, (patterns[0], limit - len(results))).fetchall():
                results.append({"kind": kind, **dict(row)})
            if len(results) >= limit:
                break
    return results[:limit]
