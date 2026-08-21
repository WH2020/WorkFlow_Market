from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


BID_SCHEMA_VERSION = 1
BID_APPLICATION_VERSION = "0.15.0"
BID_DATABASE_RELATIVE_PATH = Path("data/bids/bidding.sqlite3")
BID_MIGRATIONS = Path(__file__).resolve().parent / "bid_migrations"
BID_MANIFEST = BID_MIGRATIONS / "manifest.json"
MAX_BID_FILE_BYTES = 32 * 1024 * 1024
MAX_LIST_LIMIT = 100
MAX_SECTION_ROWS = 1000
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ALLOWED_BID_SUFFIXES = {
    ".pdf", ".docx", ".xlsx", ".xls", ".csv", ".txt", ".md", ".pptx",
    ".png", ".jpg", ".jpeg", ".heic", ".ofd", ".zip",
}

BID_SECTIONS = (
    "documents", "milestones", "requirements", "response_matrix", "facts", "sections",
    "checks", "risks", "decisions", "artifacts", "outcomes",
)

PROJECT_STATUSES = {
    "draft", "interpreting", "decision_pending", "planning", "drafting", "checking",
    "delivery_pending", "delivered", "closed", "no_bid", "cancelled",
}

PROJECT_STAGES = {
    "intake", "interpretation", "decision", "planning", "drafting", "checking", "delivery", "retrospective",
}

STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"interpreting", "cancelled"},
    "interpreting": {"decision_pending", "cancelled"},
    "decision_pending": {"planning", "no_bid", "interpreting", "cancelled"},
    "planning": {"drafting", "interpreting", "no_bid", "cancelled"},
    "drafting": {"checking", "interpreting", "cancelled"},
    "checking": {"drafting", "delivery_pending", "interpreting", "cancelled"},
    "delivery_pending": {"checking", "delivered", "interpreting", "cancelled"},
    "delivered": {"closed", "interpreting"},
    "closed": {"interpreting"},
    "no_bid": {"interpreting", "cancelled"},
    "cancelled": {"draft"},
}


class BidStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise BidStoreError("PROJECT_MISSING", "项目目录不存在")
    return root


def _safe_id(value: Any, label: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip()
    if optional and not text:
        return None
    if not SAFE_ID.fullmatch(text):
        raise BidStoreError("INVALID_INPUT", f"{label} 必须是 1–128 位安全编号")
    return text


def _text(value: Any, label: str, maximum: int, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise BidStoreError("INVALID_INPUT", f"{label}不能为空")
        return None
    text = str(value).strip()
    if (required and not text) or len(text) > maximum or any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise BidStoreError("INVALID_INPUT", f"{label}不能为空或超过 {maximum} 字")
    return text or None


def _timestamp(value: Any, label: str, *, optional: bool = True) -> str | None:
    text = str(value or "").strip()
    if optional and not text:
        return None
    if not text or len(text) > 64:
        raise BidStoreError("INVALID_INPUT", f"{label}无效")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as error:
        raise BidStoreError("INVALID_INPUT", f"{label}必须是有效日期时间") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _controlled_database_path(project_root: Path | str) -> tuple[Path, Path]:
    root = _root(project_root)
    candidate = root / BID_DATABASE_RELATIVE_PATH
    cursor = root
    for part in BID_DATABASE_RELATIVE_PATH.parts[:-1]:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise BidStoreError("UNSAFE_PATH", "招投标数据目录不能包含符号链接")
    parent = candidate.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise BidStoreError("UNSAFE_PATH", "招投标数据目录必须是普通目录")
    resolved_parent = parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(root):
        raise BidStoreError("UNSAFE_PATH", "招投标数据目录越出项目范围")
    if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
        raise BidStoreError("UNSAFE_PATH", "招投标数据库必须是普通文件")
    return root, candidate


def _migration() -> tuple[str, Mapping[str, Any]]:
    try:
        manifest = json.loads(BID_MANIFEST.read_text(encoding="utf-8"))
        migrations = manifest["migrations"]
        entry = migrations[0]
        path = BID_MIGRATIONS / entry["file"]
        script = path.read_text(encoding="utf-8")
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise BidStoreError("SCHEMA_MANIFEST", f"无法读取招投标 schema：{error}") from error
    if (
        manifest.get("schema_version") != BID_SCHEMA_VERSION
        or manifest.get("application_version") != BID_APPLICATION_VERSION
        or len(migrations) != 1
        or entry.get("version") != BID_SCHEMA_VERSION
    ):
        raise BidStoreError("SCHEMA_MANIFEST", "招投标迁移清单版本与应用不一致")
    actual = _sha256_bytes(script.encode("utf-8"))
    if entry.get("sha256") != actual:
        raise BidStoreError("SCHEMA_HASH_MISMATCH", "招投标 schema SQL 哈希不一致")
    return script, entry


def _configure(connection: sqlite3.Connection, *, writable: bool) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if writable:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    else:
        connection.execute("PRAGMA query_only = ON")


def _initialize_if_needed(connection: sqlite3.Connection) -> None:
    script, entry = _migration()
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if "bid_schema_migrations" not in tables:
            if tables:
                raise BidStoreError(
                    "SCHEMA_UNSUPPORTED",
                    "招投标数据库已有未知业务表，拒绝自动初始化",
                )
            statement = ""
            for line in script.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    sql = statement.strip()
                    if sql:
                        connection.execute(sql)
                    statement = ""
            if statement.strip():
                raise BidStoreError("SCHEMA_MANIFEST", "招投标 schema 含不完整 SQL 语句")
            applied_at = _now()
            connection.execute(
                "INSERT INTO bid_schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (?,?,?,?,?,'applied')",
                (BID_SCHEMA_VERSION, entry["name"], entry["sha256"], applied_at, BID_APPLICATION_VERSION),
            )
            connection.execute(
                "INSERT INTO bid_metadata(key,value,updated_at) VALUES ('schema_version',?,?)",
                (str(BID_SCHEMA_VERSION), applied_at),
            )
        connection.commit()
    except (sqlite3.Error, BidStoreError):
        connection.rollback()
        raise


def _validate_schema(connection: sqlite3.Connection) -> None:
    try:
        row = connection.execute(
            "SELECT version, script_sha256 FROM bid_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as error:
        raise BidStoreError("SCHEMA_UNSUPPORTED", "招投标数据库缺少受支持的 schema") from error
    script, entry = _migration()
    _ = script
    if row is None or int(row[0]) != BID_SCHEMA_VERSION or row[1] != entry["sha256"]:
        raise BidStoreError("SCHEMA_UNSUPPORTED", f"只支持招投标 schema v{BID_SCHEMA_VERSION}")


@contextmanager
def bid_connection(project_root: Path | str, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
    _, path = _controlled_database_path(project_root)
    existed = path.exists()
    if not existed:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            existed = True
    connection: sqlite3.Connection | None = None
    try:
        needs_bootstrap = not existed
        if existed:
            probe = sqlite3.connect(f"{path.as_uri()}?mode=ro", timeout=5, isolation_level=None, uri=True)
            try:
                tables = probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchall()
                needs_bootstrap = not tables
            finally:
                probe.close()
        if needs_bootstrap:
            bootstrap = sqlite3.connect(path, timeout=5, isolation_level=None)
            try:
                bootstrap.row_factory = sqlite3.Row
                _configure(bootstrap, writable=True)
                _initialize_if_needed(bootstrap)
                _validate_schema(bootstrap)
            finally:
                bootstrap.close()
        connection_target = path if writable else f"{path.as_uri()}?mode=ro"
        connection = sqlite3.connect(
            connection_target,
            timeout=5,
            isolation_level=None if not writable else "DEFERRED",
            uri=not writable,
        )
        connection.row_factory = sqlite3.Row
        _configure(connection, writable=writable)
        _validate_schema(connection)
    except BidStoreError:
        if connection is not None:
            connection.close()
        if not existed:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{path}{suffix}")
                if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                    candidate.unlink()
        raise
    except (sqlite3.Error, OSError) as error:
        if connection is not None:
            connection.close()
        if not existed:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{path}{suffix}")
                if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                    candidate.unlink()
        code = "STORE_BUSY" if "locked" in str(error).lower() or "busy" in str(error).lower() else "SQLITE_ERROR"
        raise BidStoreError(code, f"无法打开招投标数据库：{error}") from error
    if connection is None:
        raise BidStoreError("SQLITE_ERROR", "无法打开招投标数据库")
    try:
        yield connection
    except sqlite3.Error as error:
        if writable:
            connection.rollback()
        code = "STORE_BUSY" if "locked" in str(error).lower() or "busy" in str(error).lower() else "SQLITE_ERROR"
        raise BidStoreError(code, f"招投标数据库操作失败：{error}") from error
    finally:
        connection.close()


def bid_store_summary(project_root: Path | str) -> dict[str, Any]:
    _, path = _controlled_database_path(project_root)
    with bid_connection(project_root) as connection:
        projects = int(connection.execute("SELECT count(*) FROM bid_projects WHERE deleted_at IS NULL").fetchone()[0])
        active = int(connection.execute(
            "SELECT count(*) FROM bid_projects WHERE deleted_at IS NULL AND status NOT IN ('closed','no_bid','cancelled')"
        ).fetchone()[0])
        updated = connection.execute("SELECT max(updated_at) FROM bid_projects WHERE deleted_at IS NULL").fetchone()[0]
    return {
        "backend": "sqlite", "schema_version": BID_SCHEMA_VERSION,
        "path": path.relative_to(_root(project_root)).as_posix(), "project_count": projects,
        "active_count": active, "updated_at": updated,
    }


def bid_dashboard(project_root: Path | str) -> dict[str, Any]:
    summary = bid_store_summary(project_root)
    with bid_connection(project_root) as connection:
        status_counts = {
            str(row["status"]): int(row["total"])
            for row in connection.execute(
                "SELECT status,count(*) AS total FROM bid_projects WHERE deleted_at IS NULL GROUP BY status"
            ).fetchall()
        }
        decision_pending = int(connection.execute(
            "SELECT count(*) FROM bid_projects WHERE deleted_at IS NULL AND go_no_go='pending' AND status NOT IN ('closed','no_bid','cancelled')"
        ).fetchone()[0])
        high_risk = int(connection.execute(
            """SELECT count(DISTINCT p.bid_id) FROM bid_projects p
               JOIN bid_checks c ON c.bid_id=p.bid_id
               WHERE p.deleted_at IS NULL AND c.deleted_at IS NULL AND c.status='open'
                 AND c.severity IN ('critical','high')"""
        ).fetchone()[0])
        next_deadlines = [dict(row) for row in connection.execute(
            """SELECT bid_id,name,buyer,deadline_at,status,current_stage,owner
               FROM bid_projects
               WHERE deleted_at IS NULL AND deadline_at IS NOT NULL
                 AND status NOT IN ('closed','no_bid','cancelled')
               ORDER BY deadline_at,bid_id LIMIT 10"""
        ).fetchall()]
    return {
        **summary,
        "status_counts": status_counts,
        "decision_pending_count": decision_pending,
        "high_risk_project_count": high_risk,
        "next_deadlines": next_deadlines,
    }


def create_bid_project(project_root: Path | str, payload: Mapping[str, Any]) -> dict[str, Any]:
    name = _text(payload.get("name"), "投标项目名称", 500, required=True)
    workspace_project_id = _safe_id(payload.get("workspace_project_id") or "project-default", "项目空间编号")
    account_id = _safe_id(payload.get("account_id"), "客户编号", optional=True)
    opportunity_id = _safe_id(payload.get("opportunity_id"), "销售机会编号", optional=True)
    buyer = _text(payload.get("buyer"), "采购人", 500)
    tender_number = _text(payload.get("tender_number"), "招标编号", 200)
    lot_name = _text(payload.get("lot_name"), "标段", 300)
    owner = _text(payload.get("owner"), "负责人", 200)
    deadline_at = _timestamp(payload.get("deadline_at"), "投标截止时间")
    summary = _text(payload.get("summary"), "项目说明", 4000)
    bid_id = f"bid-{uuid.uuid4().hex[:16]}"
    created_at = _now()
    with bid_connection(project_root, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO bid_projects(
                 bid_id,account_id,opportunity_id,workspace_project_id,name,buyer,tender_number,lot_name,owner,
                 deadline_at,currency,status,current_stage,go_no_go,summary,version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,'CNY','draft','intake','pending',?,1,?,?)""",
            (
                bid_id, account_id, opportunity_id, workspace_project_id, name, buyer, tender_number,
                lot_name, owner, deadline_at, summary, created_at, created_at,
            ),
        )
        if deadline_at:
            connection.execute(
                """INSERT INTO bid_milestones(
                     milestone_id,bid_id,milestone_type,title,due_at,status,evidence_json,version,created_at,updated_at
                   ) VALUES (?,?, 'submission_deadline','投标截止',?,'pending','[]',1,?,?)""",
                (f"milestone-{uuid.uuid4().hex[:16]}", bid_id, deadline_at, created_at, created_at),
            )
        connection.execute(
            "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,created_at) VALUES (?,?, 'project_created','创建投标项目',?,'user',?)",
            (f"event-{uuid.uuid4().hex[:16]}", bid_id, _canonical_json({"name": name}), created_at),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM bid_projects WHERE bid_id=?", (bid_id,)).fetchone()
    return dict(row)


def register_bid_document(
    project_root: Path | str,
    bid_id: str,
    relative_path: str,
    *,
    role: str = "tender",
    display_name: str | None = None,
) -> dict[str, Any]:
    bid_id = _safe_id(bid_id, "投标项目编号") or ""
    if role not in {"tender", "addendum", "template", "reference", "company_material"}:
        raise BidStoreError("INVALID_INPUT", "文件角色无效")
    root = _root(project_root)
    candidate = root / Path(relative_path)
    expected_root = root / "inputs" / "bids" / bid_id
    if candidate.is_symlink() or not candidate.is_file():
        raise BidStoreError("UNSAFE_PATH", "投标资料必须是受控目录中的普通文件")
    canonical = candidate.resolve(strict=True)
    if expected_root.is_symlink() or not expected_root.is_dir():
        raise BidStoreError("UNSAFE_PATH", "投标资料必须位于当前投标项目的受控目录")
    controlled = expected_root.resolve(strict=True)
    if not canonical.is_relative_to(controlled) or not canonical.is_relative_to(root):
        raise BidStoreError("UNSAFE_PATH", "投标资料越出当前投标项目目录")
    if canonical.suffix.lower() not in ALLOWED_BID_SUFFIXES:
        raise BidStoreError("INVALID_INPUT", "不支持该投标资料格式")
    size = canonical.stat().st_size
    if size < 1 or size > MAX_BID_FILE_BYTES:
        raise BidStoreError("FILE_TOO_LARGE", "投标资料必须为 1 字节至 32 兆字节")
    document_id = f"bid-document-{uuid.uuid4().hex[:16]}"
    timestamp = _now()
    relative = canonical.relative_to(root).as_posix()
    digest = _sha256_file(canonical)
    with bid_connection(root, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL", (bid_id,)).fetchone() is None:
            raise BidStoreError("NOT_FOUND", "投标项目不存在")
        if connection.execute(
            "SELECT 1 FROM bid_documents WHERE bid_id=? AND relative_path=? AND deleted_at IS NULL",
            (bid_id, relative),
        ).fetchone() is not None:
            raise BidStoreError("CONFLICT", "该文件已经登记到当前投标项目")
        connection.execute(
            """INSERT INTO bid_documents(
                 document_id,bid_id,role,display_name,relative_path,sha256,byte_size,media_type,source_status,
                 document_version,version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?, 'verified',1,1,?,?)""",
            (
                document_id, bid_id, role, display_name or canonical.name, relative, digest, size,
                mimetypes.guess_type(canonical.name)[0] or "application/octet-stream", timestamp, timestamp,
            ),
        )
        project = connection.execute("SELECT status,version FROM bid_projects WHERE bid_id=?", (bid_id,)).fetchone()
        if project and project["status"] == "draft":
            connection.execute(
                "UPDATE bid_projects SET status='interpreting',current_stage='interpretation',version=version+1,updated_at=? WHERE bid_id=? AND version=?",
                (timestamp, bid_id, project["version"]),
            )
        connection.execute(
            "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,created_at) VALUES (?,?, 'document_added','新增投标资料',?,'user',?)",
            (
                f"event-{uuid.uuid4().hex[:16]}", bid_id,
                _canonical_json({"document_id": document_id, "role": role, "path": relative, "sha256": digest}), timestamp,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM bid_documents WHERE document_id=?", (document_id,)).fetchone()
    return dict(row)


def search_bid_projects(
    project_root: Path | str,
    *,
    query: str = "",
    statuses: Sequence[str] | None = None,
    account_id: str | None = None,
    workspace_project_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    normalized = str(query or "").strip().casefold()
    if len(normalized) > 500 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise BidStoreError("INVALID_INPUT", "投标项目查询条件无效")
    filters = list(statuses or [])
    if any(status not in PROJECT_STATUSES for status in filters):
        raise BidStoreError("INVALID_INPUT", "投标状态筛选无效")
    account_id = _safe_id(account_id, "客户编号", optional=True)
    workspace_project_id = _safe_id(workspace_project_id, "项目空间编号", optional=True)
    conditions = ["p.deleted_at IS NULL"]
    parameters: list[Any] = []
    if normalized:
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("lower(p.name||' '||coalesce(p.buyer,'')||' '||coalesce(p.tender_number,'')||' '||coalesce(p.lot_name,'')) LIKE ? ESCAPE '\\'")
        parameters.append(f"%{escaped}%")
    if filters:
        conditions.append(f"p.status IN ({','.join('?' for _ in filters)})")
        parameters.extend(filters)
    if account_id:
        conditions.append("p.account_id=?")
        parameters.append(account_id)
    if workspace_project_id:
        conditions.append("p.workspace_project_id=?")
        parameters.append(workspace_project_id)
    parameters.append(limit)
    sql = f"""
        SELECT p.*,
          (SELECT count(*) FROM bid_documents d WHERE d.bid_id=p.bid_id AND d.deleted_at IS NULL) AS document_count,
          (SELECT count(*) FROM bid_requirements r WHERE r.bid_id=p.bid_id AND r.deleted_at IS NULL) AS requirement_count,
          (SELECT count(*) FROM bid_requirements r WHERE r.bid_id=p.bid_id AND r.deleted_at IS NULL AND r.mandatory=1 AND r.response_status NOT IN ('compliant','not_applicable')) AS mandatory_gap_count,
          (SELECT count(*) FROM bid_checks c WHERE c.bid_id=p.bid_id AND c.deleted_at IS NULL AND c.status='open' AND c.severity IN ('critical','high')) AS high_risk_check_count,
          (SELECT count(*) FROM bid_response_matrix m WHERE m.bid_id=p.bid_id AND m.deleted_at IS NULL AND m.material_status IN ('missing','requested')) AS material_gap_count
        FROM bid_projects p WHERE {' AND '.join(conditions)}
        ORDER BY CASE WHEN p.deadline_at IS NULL THEN 1 ELSE 0 END, p.deadline_at, p.updated_at DESC, p.bid_id
        LIMIT ?
    """
    with bid_connection(project_root) as connection:
        rows = [dict(row) for row in connection.execute(sql, parameters).fetchall()]
    return {"schema_version": BID_SCHEMA_VERSION, "rows": rows, "returned": len(rows)}


def read_bid_project(
    project_root: Path | str,
    bid_id: str,
    *,
    sections: Sequence[str] | None = None,
) -> dict[str, Any]:
    bid_id = _safe_id(bid_id, "投标项目编号") or ""
    selected = tuple(BID_SECTIONS if sections is None else sections)
    if len(set(selected)) != len(selected) or any(section not in BID_SECTIONS for section in selected):
        raise BidStoreError("INVALID_INPUT", "投标项目分区无效")
    table_map = {
        "documents": ("bid_documents", "created_at DESC, document_id"),
        "milestones": ("bid_milestones", "due_at, milestone_id"),
        "requirements": ("bid_requirements", "mandatory DESC, category, requirement_id"),
        "response_matrix": ("bid_response_matrix", "status, response_id"),
        "facts": ("bid_facts", "category, field_name, fact_id"),
        "sections": ("bid_sections", "order_index, section_id"),
        "checks": ("bid_checks", "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, status, check_id"),
        "risks": ("bid_risks", "CASE impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, risk_id"),
        "decisions": ("bid_decisions", "decided_at DESC, decision_id"),
        "artifacts": ("bid_artifacts", "updated_at DESC, artifact_id"),
        "outcomes": ("bid_outcomes", "updated_at DESC, outcome_id"),
    }
    with bid_connection(project_root) as connection:
        project = connection.execute("SELECT * FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL", (bid_id,)).fetchone()
        if project is None:
            raise BidStoreError("NOT_FOUND", "投标项目不存在")
        result: dict[str, Any] = {"project": dict(project), "sections": {}, "truncated_sections": []}
        for section in selected:
            table, order = table_map[section]
            rows = [dict(row) for row in connection.execute(
                f"SELECT * FROM {table} WHERE bid_id=? AND deleted_at IS NULL ORDER BY {order} LIMIT ?",
                (bid_id, MAX_SECTION_ROWS + 1),
            ).fetchall()]
            if len(rows) > MAX_SECTION_ROWS:
                result["truncated_sections"].append(section)
                rows = rows[:MAX_SECTION_ROWS]
            result["sections"][section] = rows
    return result


def read_bid_timeline(project_root: Path | str, bid_id: str, *, limit: int = 100) -> dict[str, Any]:
    bid_id = _safe_id(bid_id, "投标项目编号") or ""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise BidStoreError("INVALID_INPUT", "时间线 limit 无效")
    with bid_connection(project_root) as connection:
        if connection.execute("SELECT 1 FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL", (bid_id,)).fetchone() is None:
            raise BidStoreError("NOT_FOUND", "投标项目不存在")
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM bid_events WHERE bid_id=? ORDER BY created_at DESC,event_id DESC LIMIT ?",
            (bid_id, limit),
        ).fetchall()]
    return {"rows": rows, "returned": len(rows)}


def update_bid_project(
    project_root: Path | str,
    bid_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    bid_id = _safe_id(bid_id, "投标项目编号") or ""
    expected_version = payload.get("expected_version")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise BidStoreError("INVALID_INPUT", "缺少投标项目版本")
    allowed = {
        "name": ("投标项目名称", 500, True), "buyer": ("采购人", 500, False),
        "tender_number": ("招标编号", 200, False), "lot_name": ("标段", 300, False),
        "owner": ("负责人", 200, False), "summary": ("项目说明", 4000, False),
        "decision_reason": ("决策说明", 4000, False),
    }
    values: dict[str, Any] = {}
    for field, (label, maximum, required) in allowed.items():
        if field in payload:
            values[field] = _text(payload.get(field), label, maximum, required=required)
    if "deadline_at" in payload:
        values["deadline_at"] = _timestamp(payload.get("deadline_at"), "投标截止时间")
    if "account_id" in payload:
        values["account_id"] = _safe_id(payload.get("account_id"), "客户编号", optional=True)
    if "opportunity_id" in payload:
        values["opportunity_id"] = _safe_id(payload.get("opportunity_id"), "销售机会编号", optional=True)
    if not values:
        raise BidStoreError("INVALID_INPUT", "没有可更新的投标项目字段")
    timestamp = _now()
    assignments = ",".join(f"{field}=?" for field in values)
    with bid_connection(project_root, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = connection.execute(
            f"UPDATE bid_projects SET {assignments},version=version+1,updated_at=? WHERE bid_id=? AND version=? AND deleted_at IS NULL",
            (*values.values(), timestamp, bid_id, expected_version),
        )
        if result.rowcount != 1:
            exists = connection.execute("SELECT version FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL", (bid_id,)).fetchone()
            if exists is None:
                raise BidStoreError("NOT_FOUND", "投标项目不存在")
            raise BidStoreError("VERSION_CONFLICT", "投标项目已更新，请刷新后重试")
        connection.execute(
            "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,created_at) VALUES (?,?, 'project_updated','更新投标项目',?,'user',?)",
            (f"event-{uuid.uuid4().hex[:16]}", bid_id, _canonical_json({"fields": sorted(values)}), timestamp),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM bid_projects WHERE bid_id=?", (bid_id,)).fetchone()
    return dict(row)


def transition_bid_project(
    project_root: Path | str,
    bid_id: str,
    *,
    status: str,
    current_stage: str,
    expected_version: int,
) -> dict[str, Any]:
    bid_id = _safe_id(bid_id, "投标项目编号") or ""
    if status not in PROJECT_STATUSES or current_stage not in PROJECT_STAGES:
        raise BidStoreError("INVALID_INPUT", "投标项目阶段无效")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
        raise BidStoreError("INVALID_INPUT", "缺少投标项目版本")
    timestamp = _now()
    with bid_connection(project_root, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT status,version FROM bid_projects WHERE bid_id=? AND deleted_at IS NULL", (bid_id,)).fetchone()
        if current is None:
            raise BidStoreError("NOT_FOUND", "投标项目不存在")
        if current["version"] != expected_version:
            raise BidStoreError("VERSION_CONFLICT", "投标项目已更新，请刷新后重试")
        if status != current["status"] and status not in STATUS_TRANSITIONS.get(str(current["status"]), set()):
            raise BidStoreError("INVALID_TRANSITION", f"不能从 {current['status']} 直接切换到 {status}")
        connection.execute(
            "UPDATE bid_projects SET status=?,current_stage=?,version=version+1,updated_at=? WHERE bid_id=? AND version=?",
            (status, current_stage, timestamp, bid_id, expected_version),
        )
        connection.execute(
            "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,created_at) VALUES (?,?, 'stage_changed','更新投标阶段',?,'user',?)",
            (
                f"event-{uuid.uuid4().hex[:16]}", bid_id,
                _canonical_json({"from": current["status"], "to": status, "stage": current_stage}), timestamp,
            ),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM bid_projects WHERE bid_id=?", (bid_id,)).fetchone()
    return dict(row)


def _check_finding(
    rule_id: str,
    category: str,
    severity: str,
    finding: str,
    recommendation: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "rule_id": rule_id, "category": category, "severity": severity,
        "finding": finding, "recommendation": recommendation, "evidence": evidence,
    }


def run_deterministic_bid_checks(project_root: Path | str, bid_id: str) -> dict[str, Any]:
    snapshot = read_bid_project(project_root, bid_id)
    project = snapshot["project"]
    sections = snapshot["sections"]
    findings: list[dict[str, Any]] = []
    deadline = project.get("deadline_at")
    if not deadline:
        findings.append(_check_finding("BID-DEADLINE-001", "deadline", "high", "尚未登记投标截止时间。", "核对招标公告或招标文件并登记带时区的截止时间。", []))
    else:
        try:
            if datetime.fromisoformat(str(deadline).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                findings.append(_check_finding("BID-DEADLINE-001", "deadline", "critical", "投标截止时间已到或已过。", "立即停止自动编制并由负责人确认是否已提交、延期或终止。", [{"deadline_at": deadline}]))
        except ValueError:
            findings.append(_check_finding("BID-DEADLINE-001", "deadline", "high", "投标截止时间格式无效。", "重新核对并登记有效日期时间。", [{"deadline_at": deadline}]))
    tender_docs = [item for item in sections["documents"] if item.get("role") in {"tender", "addendum"}]
    if not tender_docs:
        findings.append(_check_finding("BID-SOURCE-001", "source", "critical", "没有已登记的招标文件或补遗。", "先上传招标原件；不得仅凭搜索摘要或口述内容编制。", []))
    mandatory_gaps = [item for item in sections["requirements"] if item.get("mandatory") == 1 and item.get("response_status") not in {"compliant", "not_applicable"}]
    if mandatory_gaps:
        findings.append(_check_finding("BID-MANDATORY-001", "requirements", "critical", f"仍有 {len(mandatory_gaps)} 条强制要求未形成合规响应。", "逐条补齐响应、材料与证据；全部关闭前不得标记交付就绪。", [{"requirement_id": item["requirement_id"]} for item in mandatory_gaps[:50]]))
    unverified_requirements = [item for item in sections["requirements"] if item.get("verification_status") != "verified"]
    if unverified_requirements:
        findings.append(_check_finding("BID-EVIDENCE-001", "evidence", "high", f"有 {len(unverified_requirements)} 条要求尚未完成原文定位核验。", "补充文件、页码或段落定位并人工核对原文。", [{"requirement_id": item["requirement_id"]} for item in unverified_requirements[:50]]))
    response_ids = {item.get("requirement_id") for item in sections["response_matrix"]}
    uncovered = [item for item in sections["requirements"] if item.get("requirement_id") not in response_ids]
    if uncovered:
        findings.append(_check_finding("BID-MATRIX-001", "planning", "high", f"应答矩阵尚未覆盖 {len(uncovered)} 条招标要求。", "为每条要求指定章节、材料、负责人、截止时间与响应策略。", [{"requirement_id": item["requirement_id"]} for item in uncovered[:50]]))
    missing_materials = [item for item in sections["response_matrix"] if item.get("material_status") in {"missing", "requested"}]
    if missing_materials:
        findings.append(_check_finding("BID-MATERIAL-001", "materials", "high", f"仍有 {len(missing_materials)} 项材料缺失或待提供。", "按截止时间和废标影响排序催办，并核验有效期与适用主体。", [{"response_id": item["response_id"]} for item in missing_materials[:50]]))
    if not sections["facts"]:
        findings.append(_check_finding("BID-FACT-001", "facts", "medium", "尚未建立项目事实基线。", "登记项目名、编号、主体、人员、资质、金额、工期和承诺等统一事实。", []))
    else:
        pending_facts = [item for item in sections["facts"] if item.get("verification_status") != "verified"]
        if pending_facts:
            findings.append(_check_finding("BID-FACT-001", "facts", "high", f"事实基线中有 {len(pending_facts)} 项尚未核验。", "核验后再用于标书正文；未核实值保持待补充，不得编造。", [{"fact_id": item["fact_id"]} for item in pending_facts[:50]]))
    if not sections["sections"]:
        findings.append(_check_finding("BID-OUTLINE-001", "drafting", "high", "尚未建立经确认的标书目录。", "根据评分点和强制要求建立目录并单独确认。", []))
    open_risks = [item for item in sections["risks"] if item.get("status") in {"open", "mitigating"} and item.get("impact") in {"high", "critical"}]
    if open_risks:
        severity = "critical" if any(item.get("impact") == "critical" for item in open_risks) else "high"
        findings.append(_check_finding("BID-RISK-001", "risk", severity, f"有 {len(open_risks)} 项高影响风险尚未关闭。", "明确责任人、缓解动作；无法关闭时由负责人显式接受风险。", [{"risk_id": item["risk_id"]} for item in open_risks[:50]]))
    overdue = []
    for item in sections["milestones"]:
        if item.get("status") in {"completed", "cancelled"} or not item.get("due_at"):
            continue
        try:
            if datetime.fromisoformat(str(item["due_at"]).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                overdue.append(item)
        except ValueError:
            overdue.append(item)
    if overdue:
        findings.append(_check_finding("BID-MILESTONE-001", "schedule", "high", f"有 {len(overdue)} 个里程碑已逾期或时间无效。", "立即更新责任人、完成状态和恢复计划。", [{"milestone_id": item["milestone_id"]} for item in overdue[:50]]))
    if project.get("go_no_go") == "pending":
        findings.append(_check_finding("BID-DECISION-001", "decision", "high", "尚未完成人工参投决策。", "在编制投入扩大前完成 Go/No-Go 审批。", []))
    if project.get("status") in {"delivery_pending", "delivered", "closed"} and not any(item.get("artifact_type") == "final_docx" and item.get("status") in {"approved", "ready"} for item in sections["artifacts"]):
        findings.append(_check_finding("BID-DELIVERY-001", "delivery", "critical", "交付阶段缺少已批准的正式 DOCX 记录。", "重新生成并完成正式文件审批、渲染检查和哈希登记。", []))

    snapshot_hash = _sha256_bytes(_canonical_json({
        "project": project,
        "documents": sections["documents"], "requirements": sections["requirements"],
        "response_matrix": sections["response_matrix"], "facts": sections["facts"],
        "sections": sections["sections"], "risks": sections["risks"], "milestones": sections["milestones"],
        "artifacts": sections["artifacts"],
    }).encode("utf-8"))
    timestamp = _now()
    rule_version = "2026.08.1"
    active_rules = {item["rule_id"] for item in findings}
    all_rules = {
        "BID-DEADLINE-001", "BID-SOURCE-001", "BID-MANDATORY-001", "BID-EVIDENCE-001",
        "BID-MATRIX-001", "BID-MATERIAL-001", "BID-FACT-001", "BID-OUTLINE-001",
        "BID-RISK-001", "BID-MILESTONE-001", "BID-DECISION-001", "BID-DELIVERY-001",
    }
    finding_map = {item["rule_id"]: item for item in findings}
    with bid_connection(project_root, writable=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for rule_id in sorted(all_rules):
            finding = finding_map.get(rule_id)
            check_id = f"check-{hashlib.sha256(f'{bid_id}:{rule_id}'.encode()).hexdigest()[:24]}"
            current = connection.execute("SELECT version,input_sha256,status FROM bid_checks WHERE check_id=?", (check_id,)).fetchone()
            status = "open" if finding else "not_applicable"
            category = finding["category"] if finding else "deterministic"
            severity = finding["severity"] if finding else "info"
            message = finding["finding"] if finding else "本轮未发现该规则对应的问题。"
            recommendation = finding["recommendation"] if finding else "无需处理。"
            evidence_json = _canonical_json(finding["evidence"] if finding else [])
            if current is None:
                connection.execute(
                    """INSERT INTO bid_checks(
                         check_id,bid_id,rule_id,rule_version,category,severity,status,finding,recommendation,
                         evidence_json,input_sha256,version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (check_id, bid_id, rule_id, rule_version, category, severity, status, message, recommendation, evidence_json, snapshot_hash, timestamp, timestamp),
                )
            elif current["input_sha256"] != snapshot_hash or current["status"] in {"open", "not_applicable"}:
                connection.execute(
                    """UPDATE bid_checks SET rule_version=?,category=?,severity=?,status=?,finding=?,recommendation=?,
                       evidence_json=?,input_sha256=?,resolved_by=NULL,resolved_at=NULL,version=version+1,updated_at=? WHERE check_id=?""",
                    (rule_version, category, severity, status, message, recommendation, evidence_json, snapshot_hash, timestamp, check_id),
                )
        connection.execute(
            "INSERT INTO bid_events(event_id,bid_id,event_type,title,detail_json,actor,created_at) VALUES (?,?, 'checks_run','执行确定性检查',?,'system',?)",
            (f"event-{uuid.uuid4().hex[:16]}", bid_id, _canonical_json({"rule_version": rule_version, "open_rules": sorted(active_rules), "snapshot_sha256": snapshot_hash}), timestamp),
        )
        connection.commit()
    return {
        "bid_id": bid_id, "rule_version": rule_version, "snapshot_sha256": snapshot_hash,
        "open_count": len(findings), "critical_count": sum(item["severity"] == "critical" for item in findings),
        "high_count": sum(item["severity"] == "high" for item in findings), "findings": findings,
    }
