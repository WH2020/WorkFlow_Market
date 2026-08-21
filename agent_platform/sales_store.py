from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit


SCHEMA_VERSION = 1
APPLICATION_VERSION = "0.13.6"
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_ROWS_PER_FILE = 250_000
MIGRATIONS = Path(__file__).resolve().parent / "migrations"
MANIFEST_PATH = MIGRATIONS / "manifest.json"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SENSITIVE_QUERY_KEYS = re.compile(
    r"(?:api[-_]?key|access[-_]?token|auth|authorization|credential|password|secret|signature|token)",
    re.IGNORECASE,
)

CSV_SCHEMAS: dict[str, tuple[str, ...]] = {
    "customers.csv": (
        "customer_id", "customer_name", "region", "sector", "owner", "stage", "health",
        "key_contact", "decision_maker", "budget_path", "next_action", "next_action_due",
        "last_evidence_date", "risks", "updated_at",
    ),
    "activities.csv": (
        "activity_id", "customer_id", "salesperson_id", "occurred_at", "channel", "activity_type",
        "summary", "evidence_path", "commitment", "next_action", "next_action_due", "created_at",
    ),
    "resource-requests.csv": (
        "request_id", "customer_id", "salesperson_id", "requested_at", "resource_type",
        "request_summary", "business_reason", "deadline", "owner", "status", "decision",
        "decision_reason", "updated_at",
    ),
    "sales-assets.csv": (
        "asset_id", "asset_type", "title", "scope", "customer_id", "audience_role", "sales_stage",
        "use_case", "owner", "status", "authorization_status", "deidentification_status", "version",
        "source_path", "evidence_refs", "last_validated_at", "next_review_at", "usage_feedback", "updated_at",
    ),
    "source-register.csv": (
        "source_id", "title", "url", "publisher", "published_date", "accessed_date", "region",
        "topic", "source_type", "quality", "exposure_status", "key_facts", "important_quotes",
        "interpretation", "limitations", "status", "notes",
    ),
}

LEGACY_SOURCE_SCHEMA = (
    "source_id", "title", "url", "publisher", "published_date", "accessed_date", "region",
    "topic", "source_type", "quality", "exposure_status", "status", "notes",
)

ENTITY_INSERT_ORDER = (
    "sources", "accounts", "contacts", "account_contacts", "opportunities", "activities",
    "commitments", "risks", "signals", "actions", "resource_requests", "sales_assets",
    "evidence_refs", "action_suggestions", "plays", "play_versions", "play_runs", "task_links", "artifacts",
)

EXPORT_TABLES = ENTITY_INSERT_ORDER


class SalesStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_root(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise SalesStoreError("PROJECT_MISSING", "项目目录不存在")
    return root


def _controlled_path(root: Path, path: Path | str, label: str, *, allow_root: bool = False) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        lexical_relative = candidate.relative_to(root)
    except ValueError:
        lexical_relative = None
    if lexical_relative is not None:
        cursor = root
        for part in lexical_relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise SalesStoreError("UNSAFE_PATH", f"{label} 的受控路径中不能包含符号链接")
    elif candidate.is_symlink():
        raise SalesStoreError("UNSAFE_PATH", f"{label} 不能是符号链接")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root) or (resolved == root and not allow_root):
        raise SalesStoreError("UNSAFE_PATH", f"{label} 必须位于当前项目目录内")
    return resolved


def _regular_file(path: Path, label: str, maximum: int | None = None) -> Path:
    if path.is_symlink() or not path.is_file():
        raise SalesStoreError("UNSAFE_PATH", f"{label} 必须是普通文件且不能是符号链接")
    if maximum is not None and path.stat().st_size > maximum:
        raise SalesStoreError("FILE_TOO_LARGE", f"{label} 超过 {maximum // 1024 // 1024} MiB 上限")
    return path.resolve(strict=True)


def _atomic_create(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise SalesStoreError("TARGET_EXISTS", f"目标已存在且内容不同：{path.name}")
        temporary.unlink(missing_ok=True)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _publish_file(temporary: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", f"目标已存在，拒绝覆盖：{target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(temporary, target)
    except FileExistsError as error:
        raise SalesStoreError("TARGET_EXISTS", f"目标已存在，拒绝覆盖：{target.name}") from error
    temporary.unlink(missing_ok=True)


def _require_free_space(directory: Path, required_bytes: int, label: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    available = shutil.disk_usage(directory).free
    if available < required_bytes:
        raise SalesStoreError(
            "INSUFFICIENT_SPACE",
            f"{label} 至少需要 {required_bytes} 字节可用空间，当前只有 {available} 字节",
        )


def _migration() -> tuple[str, dict[str, Any]]:
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        entry = manifest["migrations"][0]
        script_path = MIGRATIONS / entry["file"]
        script = script_path.read_text(encoding="utf-8")
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise SalesStoreError("SCHEMA_MANIFEST", f"无法读取 schema 迁移清单：{error}") from error
    if entry.get("version") != SCHEMA_VERSION or manifest.get("schema_version") != SCHEMA_VERSION:
        raise SalesStoreError("SCHEMA_MANIFEST", "迁移清单版本与应用不一致")
    actual = _sha256_bytes(script.encode("utf-8"))
    if entry.get("sha256") != actual:
        raise SalesStoreError("SCHEMA_HASH_MISMATCH", "schema SQL 与迁移清单哈希不一致")
    return script, entry


def _configure(connection: sqlite3.Connection, *, writable: bool) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    if writable:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    else:
        connection.execute("PRAGMA query_only = ON")


def _initialize_connection(connection: sqlite3.Connection) -> None:
    script, entry = _migration()
    connection.executescript(script)
    applied_at = _now()
    connection.execute(
        "INSERT INTO schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (?,?,?,?,?,'applied')",
        (SCHEMA_VERSION, entry["name"], entry["sha256"], applied_at, APPLICATION_VERSION),
    )
    connection.execute(
        "INSERT INTO store_metadata(key,value,updated_at) VALUES ('schema_version',?,?)",
        (str(SCHEMA_VERSION), applied_at),
    )
    connection.commit()


def _read_csv(path: Path, expected: tuple[str, ...]) -> list[dict[str, Any]]:
    canonical = _regular_file(path, path.name, MAX_SOURCE_BYTES)
    try:
        text = canonical.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise SalesStoreError("INVALID_UTF8", f"{path.name} 不是有效 UTF-8：{error}") from error
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise SalesStoreError("DAMAGED_CSV", f"{path.name} CSV 结构损坏：{error}") from error
    if not rows:
        raise SalesStoreError("SCHEMA_MISMATCH", f"{path.name} 为空文件，缺少表头")
    header = tuple(rows[0])
    accepted = (expected, LEGACY_SOURCE_SCHEMA) if path.name == "source-register.csv" else (expected,)
    if header not in accepted:
        raise SalesStoreError("SCHEMA_MISMATCH", f"{path.name} 表头与受支持 schema 不一致")
    if len(rows) - 1 > MAX_ROWS_PER_FILE:
        raise SalesStoreError("TOO_MANY_ROWS", f"{path.name} 超过 {MAX_ROWS_PER_FILE} 行上限")
    records: list[dict[str, Any]] = []
    for line_number, values in enumerate(rows[1:], start=2):
        malformed = len(values) != len(header)
        normalized = list(values[: len(header)]) + [""] * max(0, len(header) - len(values))
        row = dict(zip(header, normalized, strict=True))
        if header == LEGACY_SOURCE_SCHEMA:
            for field in set(CSV_SCHEMAS["source-register.csv"]) - set(LEGACY_SOURCE_SCHEMA):
                row[field] = ""
        records.append({
            "source_name": path.name,
            "row_number": line_number,
            "row": row,
            "malformed": malformed,
            "row_sha256": _sha256_bytes(_json_bytes(row)),
        })
    return records


def _read_salespeople(path: Path) -> tuple[set[str], dict[str, Any]]:
    canonical = _regular_file(path, path.name, 2 * 1024 * 1024)
    try:
        value = json.loads(canonical.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SalesStoreError("DAMAGED_JSON", f"salespeople.json 无效：{error}") from error
    items = value.get("salespeople") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise SalesStoreError("SCHEMA_MISMATCH", "salespeople.json 缺少 salespeople 数组")
    identifiers: set[str] = set()
    for item in items:
        identifier = item.get("salesperson_id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise SalesStoreError("SCHEMA_MISMATCH", "salespeople.json 含空值或重复 salesperson_id")
        identifiers.add(identifier)
    return identifiers, {
        "path": path.name,
        "size": canonical.stat().st_size,
        "sha256": _sha256_file(canonical),
        "rows": len(items),
    }


def _valid_timestamp(value: str, *, allow_date: bool = False) -> bool:
    if not value:
        return True
    if allow_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.fromisoformat(value)
            return True
        except ValueError:
            return False
    if not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "agent4market:" + kind + ":" + "\x1f".join(parts)))


def _formula_field(row: Mapping[str, str]) -> str | None:
    for key, value in row.items():
        if value.startswith(FORMULA_PREFIXES):
            return key
    return None


def _url_has_secret(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return True
    return any(SENSITIVE_QUERY_KEYS.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True))


def _safe_source_file(root: Path, raw_path: str) -> tuple[str | None, str | None, str]:
    if not raw_path:
        return None, None, "pending"
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return None, None, "rejected"
    resolved = (root / candidate).resolve(strict=False)
    if not resolved.is_relative_to(root):
        return None, None, "rejected"
    if not resolved.exists():
        return candidate.as_posix(), None, "missing_file"
    if resolved.is_symlink() or not resolved.is_file():
        return candidate.as_posix(), None, "rejected"
    return resolved.relative_to(root).as_posix(), _sha256_file(resolved), "verified"


def _issue(code: str, message: str, severity: str, source_name: str, row_number: int) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "source_name": source_name,
        "row_number": row_number,
    }


def _outcome(
    record: Mapping[str, Any], result: str, *, entity_type: str | None = None,
    entity_id: str | None = None, error_code: str | None = None, error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "source_name": record["source_name"],
        "row_number": record["row_number"],
        "row_sha256": record["row_sha256"],
        "raw": record["row"],
        "result": result,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "error_code": error_code,
        "error_message": error_message,
    }


def _plan_migration(root: Path, sales_dir: Path, knowledge_file: Path) -> dict[str, Any]:
    required_paths = {
        "customers.csv": sales_dir / "customers.csv",
        "activities.csv": sales_dir / "activities.csv",
        "resource-requests.csv": sales_dir / "resource-requests.csv",
        "sales-assets.csv": sales_dir / "sales-assets.csv",
        "source-register.csv": knowledge_file,
    }
    records: dict[str, list[dict[str, Any]]] = {}
    source_files: list[dict[str, Any]] = []
    for name, path in required_paths.items():
        rows = _read_csv(path, CSV_SCHEMAS[name])
        records[name] = rows
        source_files.append({
            "path": path.resolve(strict=True).relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
            "rows": len(rows),
        })
    salespeople, people_file = _read_salespeople(sales_dir / "salespeople.json")
    people_file["path"] = (sales_dir / "salespeople.json").resolve(strict=True).relative_to(root).as_posix()
    source_files.append(people_file)
    source_files.sort(key=lambda item: item["path"])
    source_fingerprint = _sha256_bytes(_json_bytes(source_files))
    batch_id = f"migration-{source_fingerprint[:20]}"
    migration_time = _now()
    entities: dict[str, list[dict[str, Any]]] = {table: [] for table in ENTITY_INSERT_ORDER}
    outcomes: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    def reject(record: Mapping[str, Any], code: str, message: str) -> None:
        outcomes.append(_outcome(record, "quarantined", error_code=code, error_message=message))
        issues.append(_issue(code, message, "error", record["source_name"], record["row_number"]))

    def common_error(record: Mapping[str, Any]) -> tuple[str, str] | None:
        if record["malformed"]:
            return "malformed_row", "字段数量与表头不一致"
        formula = _formula_field(record["row"])
        if formula:
            return "formula_injection", f"字段 {formula} 以电子表格公式前缀开头"
        return None

    known_sources: dict[str, str] = {}
    for record in records["source-register.csv"]:
        row = record["row"]
        error = common_error(record)
        source_id = row["source_id"].strip()
        if error:
            reject(record, *error)
        elif not source_id or not row["title"].strip() or not row["status"].strip():
            reject(record, "missing_required", "来源必须包含 source_id、title 和 status")
        elif row["status"] not in {"verified", "pending", "superseded"}:
            reject(record, "invalid_status", "来源 status 不是 verified、pending 或 superseded")
        elif row["url"] and _url_has_secret(row["url"]):
            reject(record, "unsafe_url", "来源 URL 无效或含敏感查询参数")
        elif source_id in known_sources:
            result = "skipped_duplicate" if known_sources[source_id] == record["row_sha256"] else "quarantined"
            outcomes.append(_outcome(
                record, result, entity_type="sources", entity_id=source_id,
                error_code=None if result == "skipped_duplicate" else "duplicate_key",
                error_message=None if result == "skipped_duplicate" else "source_id 重复且内容不同",
            ))
            if result == "quarantined":
                issues.append(_issue("duplicate_key", "source_id 重复且内容不同", "error", record["source_name"], record["row_number"]))
        elif any(not _valid_timestamp(row[field], allow_date=True) for field in ("published_date", "accessed_date")):
            reject(record, "invalid_date", "来源发布日期或访问日期无效")
        else:
            known_sources[source_id] = record["row_sha256"]
            entities["sources"].append({
                "source_id": source_id, "title": row["title"], "url": row["url"] or None,
                "publisher": row["publisher"] or None, "published_date": row["published_date"] or None,
                "accessed_date": row["accessed_date"] or None, "region": row["region"] or None,
                "topic": row["topic"] or None, "source_type": row["source_type"] or None,
                "quality": row["quality"] or None, "exposure_status": row["exposure_status"] or None,
                "status": row["status"], "limitations": row["limitations"] or None,
                "notes": row["notes"] or None, "legacy_key_facts": row["key_facts"] or None,
                "legacy_important_quotes": row["important_quotes"] or None,
                "legacy_interpretation": row["interpretation"] or None,
                "version": 1, "created_at": migration_time, "updated_at": migration_time,
            })
            outcomes.append(_outcome(record, "imported", entity_type="sources", entity_id=source_id))

    known_accounts: dict[str, str] = {}
    for record in records["customers.csv"]:
        row = record["row"]
        error = common_error(record)
        account_id = row["customer_id"].strip()
        if error:
            reject(record, *error)
        elif not account_id or not row["customer_name"].strip():
            reject(record, "missing_required", "客户必须包含 customer_id 和 customer_name")
        elif not _valid_timestamp(row["updated_at"]):
            reject(record, "invalid_timestamp", "客户 updated_at 必须是 UTC ISO 8601")
        elif not _valid_timestamp(row["next_action_due"]) or not _valid_timestamp(row["last_evidence_date"], allow_date=True):
            reject(record, "invalid_timestamp", "客户行动或证据时间无效")
        elif account_id in known_accounts:
            result = "skipped_duplicate" if known_accounts[account_id] == record["row_sha256"] else "quarantined"
            outcomes.append(_outcome(
                record, result, entity_type="accounts", entity_id=account_id,
                error_code=None if result == "skipped_duplicate" else "duplicate_key",
                error_message=None if result == "skipped_duplicate" else "customer_id 重复且内容不同",
            ))
            if result == "quarantined":
                issues.append(_issue("duplicate_key", "customer_id 重复且内容不同", "error", record["source_name"], record["row_number"]))
        else:
            known_accounts[account_id] = record["row_sha256"]
            updated_at = row["updated_at"] or migration_time
            entities["accounts"].append({
                "account_id": account_id, "name": row["customer_name"],
                "normalized_name": _normalized_name(row["customer_name"]), "region": row["region"] or None,
                "sector": row["sector"] or None, "owner": row["owner"] or None,
                "lifecycle_stage": row["stage"] or None, "health": row["health"] or None,
                "budget_path": row["budget_path"] or None, "summary": None, "project_id": None,
                "version": 1, "created_at": updated_at, "updated_at": updated_at,
            })
            for role, field in (("key_contact", "key_contact"), ("decision_maker", "decision_maker")):
                value = row[field].strip()
                if not value:
                    continue
                contact_id = _stable_id("legacy-contact", account_id, value)
                if not any(item["contact_id"] == contact_id for item in entities["contacts"]):
                    entities["contacts"].append({
                        "contact_id": contact_id, "display_name": value, "identity_status": "legacy_text",
                        "version": 1, "created_at": updated_at, "updated_at": updated_at,
                    })
                entities["account_contacts"].append({
                    "account_contact_id": _stable_id("account-contact", account_id, contact_id, role),
                    "account_id": account_id, "contact_id": contact_id, "role": role,
                    "decision_role": "decision_maker" if role == "decision_maker" else None,
                    "is_primary": 1 if role == "key_contact" else 0, "version": 1,
                    "created_at": updated_at, "updated_at": updated_at,
                })
            if row["next_action"].strip():
                entities["actions"].append({
                    "action_id": _stable_id("customer-next-action", account_id, record["row_sha256"]),
                    "account_id": account_id, "action_text": row["next_action"], "due_at": row["next_action_due"] or None,
                    "status": "open", "origin": "imported", "version": 1,
                    "created_at": updated_at, "updated_at": updated_at,
                })
            if row["risks"].strip():
                entities["risks"].append({
                    "risk_id": _stable_id("customer-risk", account_id, record["row_sha256"]),
                    "account_id": account_id, "risk_text": row["risks"], "status": "open",
                    "version": 1, "created_at": updated_at, "updated_at": updated_at,
                })
            outcomes.append(_outcome(record, "imported", entity_type="accounts", entity_id=account_id))

    known_activities: dict[str, str] = {}
    for record in records["activities.csv"]:
        row = record["row"]
        error = common_error(record)
        activity_id = row["activity_id"].strip()
        account_id = row["customer_id"].strip()
        if error:
            reject(record, *error)
        elif not activity_id or not account_id or not row["occurred_at"] or not row["summary"].strip():
            reject(record, "missing_required", "互动必须包含 activity_id、customer_id、occurred_at 和 summary")
        elif account_id not in known_accounts:
            reject(record, "orphan_reference", "互动引用了不存在或已隔离的客户")
        elif row["salesperson_id"] and row["salesperson_id"] not in salespeople:
            reject(record, "unknown_salesperson", "互动引用了 salespeople.json 中不存在的销售")
        elif any(not _valid_timestamp(row[field]) for field in ("occurred_at", "created_at", "next_action_due")):
            reject(record, "invalid_timestamp", "互动时间不是 UTC ISO 8601")
        elif activity_id in known_activities:
            result = "skipped_duplicate" if known_activities[activity_id] == record["row_sha256"] else "quarantined"
            outcomes.append(_outcome(record, result, entity_type="activities", entity_id=activity_id, error_code=None if result == "skipped_duplicate" else "duplicate_key", error_message=None if result == "skipped_duplicate" else "activity_id 重复且内容不同"))
        else:
            known_activities[activity_id] = record["row_sha256"]
            created_at = row["created_at"] or row["occurred_at"]
            source_path, source_sha, evidence_status = _safe_source_file(root, row["evidence_path"])
            source_id = None
            if source_sha:
                source_id = f"file-{source_sha[:24]}"
                if source_id not in known_sources:
                    known_sources[source_id] = source_sha
                    entities["sources"].append({
                        "source_id": source_id, "title": Path(source_path or "证据文件").name,
                        "source_type": "file", "status": "pending", "file_path": source_path,
                        "file_sha256": source_sha, "version": 1, "created_at": migration_time, "updated_at": migration_time,
                    })
            elif row["evidence_path"]:
                issues.append(_issue("evidence_file_unavailable", "互动证据路径缺失、越界或不是普通文件；已保留原值", "warning", record["source_name"], record["row_number"]))
            entities["activities"].append({
                "activity_id": activity_id, "account_id": account_id,
                "salesperson_id": row["salesperson_id"] or None, "occurred_at": row["occurred_at"],
                "channel": row["channel"] or None, "activity_type": row["activity_type"] or None,
                "summary": row["summary"], "source_id": source_id, "source_path": source_path,
                "source_sha256": source_sha, "evidence_status": evidence_status,
                "version": 1, "created_at": created_at, "updated_at": created_at,
            })
            if row["commitment"].strip():
                entities["commitments"].append({
                    "commitment_id": _stable_id("activity-commitment", activity_id), "account_id": account_id,
                    "source_activity_id": activity_id, "direction": "unknown", "commitment_text": row["commitment"],
                    "status": "unknown", "version": 1, "created_at": created_at, "updated_at": created_at,
                })
            if row["next_action"].strip():
                entities["actions"].append({
                    "action_id": _stable_id("activity-next-action", activity_id), "account_id": account_id,
                    "source_activity_id": activity_id, "action_text": row["next_action"],
                    "due_at": row["next_action_due"] or None, "status": "open", "origin": "imported",
                    "version": 1, "created_at": created_at, "updated_at": created_at,
                })
            outcomes.append(_outcome(record, "imported", entity_type="activities", entity_id=activity_id))

    known_requests: dict[str, str] = {}
    for record in records["resource-requests.csv"]:
        row = record["row"]
        error = common_error(record)
        request_id = row["request_id"].strip()
        account_id = row["customer_id"].strip()
        if error:
            reject(record, *error)
        elif not request_id or not account_id or not row["requested_at"] or not row["request_summary"].strip():
            reject(record, "missing_required", "资源申请必须包含 request_id、customer_id、requested_at 和 request_summary")
        elif account_id not in known_accounts:
            reject(record, "orphan_reference", "资源申请引用了不存在或已隔离的客户")
        elif row["salesperson_id"] and row["salesperson_id"] not in salespeople:
            reject(record, "unknown_salesperson", "资源申请引用了 salespeople.json 中不存在的销售")
        elif any(not _valid_timestamp(row[field]) for field in ("requested_at", "deadline", "updated_at")):
            reject(record, "invalid_timestamp", "资源申请时间不是 UTC ISO 8601")
        elif request_id in known_requests:
            result = "skipped_duplicate" if known_requests[request_id] == record["row_sha256"] else "quarantined"
            outcomes.append(_outcome(record, result, entity_type="resource_requests", entity_id=request_id, error_code=None if result == "skipped_duplicate" else "duplicate_key", error_message=None if result == "skipped_duplicate" else "request_id 重复且内容不同"))
        else:
            known_requests[request_id] = record["row_sha256"]
            updated_at = row["updated_at"] or row["requested_at"]
            entities["resource_requests"].append({
                "request_id": request_id, "account_id": account_id,
                "salesperson_id": row["salesperson_id"] or None, "requested_at": row["requested_at"],
                "resource_type": row["resource_type"] or None, "request_summary": row["request_summary"],
                "business_reason": row["business_reason"] or None, "deadline": row["deadline"] or None,
                "owner": row["owner"] or None, "status": row["status"] or None,
                "decision": row["decision"] or None, "decision_reason": row["decision_reason"] or None,
                "version": 1, "created_at": updated_at, "updated_at": updated_at,
            })
            outcomes.append(_outcome(record, "imported", entity_type="resource_requests", entity_id=request_id))

    known_assets: dict[str, str] = {}
    allowed_asset_status = {"draft", "internal-review", "active", "stale", "retired"}
    allowed_scope = {"generic", "customer-specific"}
    allowed_authorization = {"unknown", "pending", "approved", "not-required"}
    allowed_deidentification = {"unknown", "pending", "passed", "not-applicable"}
    for record in records["sales-assets.csv"]:
        row = record["row"]
        error = common_error(record)
        asset_id = row["asset_id"].strip()
        account_id = row["customer_id"].strip()
        try:
            version = int(row["version"] or "1")
        except ValueError:
            version = 0
        if error:
            reject(record, *error)
        elif not asset_id or not row["asset_type"].strip() or not row["title"].strip() or not row["scope"].strip():
            reject(record, "missing_required", "销售资料必须包含 asset_id、asset_type、title 和 scope")
        elif row["scope"] not in allowed_scope or row["status"] not in allowed_asset_status or row["authorization_status"] not in allowed_authorization or row["deidentification_status"] not in allowed_deidentification:
            reject(record, "invalid_status", "销售资料 scope 或状态枚举无效")
        elif row["scope"] == "customer-specific" and not account_id:
            reject(record, "missing_required", "客户专属销售资料必须填写 customer_id")
        elif account_id and account_id not in known_accounts:
            reject(record, "orphan_reference", "销售资料引用了不存在或已隔离的客户")
        elif (
            version < 1
            or not _valid_timestamp(row["last_validated_at"], allow_date=True)
            or not _valid_timestamp(row["next_review_at"], allow_date=True)
            or not _valid_timestamp(row["updated_at"])
        ):
            reject(record, "invalid_value", "销售资料版本或时间无效")
        elif row["status"] == "active" and any(not row[field].strip() for field in ("owner", "version", "source_path", "evidence_refs", "last_validated_at", "next_review_at")):
            reject(record, "missing_required", "启用销售资料缺少负责人、版本、源文件、证据或核验日期")
        elif row["status"] == "active" and row["authorization_status"] not in {"approved", "not-required"}:
            reject(record, "invalid_status", "启用销售资料尚未完成授权")
        elif row["status"] == "active" and row["deidentification_status"] not in {"passed", "not-applicable"}:
            reject(record, "invalid_status", "启用销售资料尚未完成脱敏核验")
        elif asset_id in known_assets:
            result = "skipped_duplicate" if known_assets[asset_id] == record["row_sha256"] else "quarantined"
            outcomes.append(_outcome(record, result, entity_type="sales_assets", entity_id=asset_id, error_code=None if result == "skipped_duplicate" else "duplicate_key", error_message=None if result == "skipped_duplicate" else "asset_id 重复且内容不同"))
        else:
            known_assets[asset_id] = record["row_sha256"]
            source_path, source_sha, source_status = _safe_source_file(root, row["source_path"])
            if row["source_path"] and not source_sha:
                issues.append(_issue("asset_file_unavailable", "销售资料源文件缺失、越界或不是普通文件；已标记待修复", "warning", record["source_name"], record["row_number"]))
            updated_at = row["updated_at"] or migration_time
            entities["sales_assets"].append({
                "asset_id": asset_id, "asset_type": row["asset_type"], "title": row["title"],
                "scope": row["scope"], "account_id": account_id or None,
                "audience_role": row["audience_role"] or None, "sales_stage": row["sales_stage"] or None,
                "use_case": row["use_case"] or None, "owner": row["owner"] or None,
                "status": row["status"], "authorization_status": row["authorization_status"],
                "deidentification_status": row["deidentification_status"], "source_path": source_path,
                "source_sha256": source_sha, "source_status": source_status,
                "legacy_evidence_refs": row["evidence_refs"] or None,
                "last_validated_at": row["last_validated_at"] or None,
                "next_review_at": row["next_review_at"] or None,
                "usage_feedback": row["usage_feedback"] or None, "version": version,
                "created_at": updated_at, "updated_at": updated_at,
            })
            outcomes.append(_outcome(record, "imported", entity_type="sales_assets", entity_id=asset_id))

    counts = {name: sum(1 for item in outcomes if item["result"] == name) for name in ("imported", "skipped_duplicate", "quarantined", "failed")}
    return {
        "batch_id": batch_id,
        "source_files": source_files,
        "source_fingerprint": source_fingerprint,
        "migration_time": migration_time,
        "entities": entities,
        "outcomes": outcomes,
        "issues": issues,
        "counts": counts,
        "total_rows": len(outcomes),
        "staging_eligible": counts["failed"] == 0,
    }


def _insert_entity(connection: sqlite3.Connection, table: str, entity: Mapping[str, Any]) -> None:
    columns = tuple(entity)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(entity[column] for column in columns),
    )


def _table_counts(connection: sqlite3.Connection, tables: Iterable[str] = EXPORT_TABLES) -> dict[str, int]:
    return {table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}


def _logical_state_sha256(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table in EXPORT_TABLES:
        table_info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        columns = [row[1] for row in table_info]
        primary = next((row[1] for row in table_info if row[5]), columns[0])
        digest.update(_json_bytes({"table": table, "columns": columns}))
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {primary}"):
            digest.update(_json_bytes(list(row)))
    return digest.hexdigest()


def verify_sales_store(database_path: Path | str) -> dict[str, Any]:
    path = Path(database_path).resolve(strict=False)
    _regular_file(path, "业务数据库")
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        _configure(connection, writable=False)
        version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise SalesStoreError("SCHEMA_UNSUPPORTED", f"只支持 schema v{SCHEMA_VERSION}，当前为 v{version}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        if integrity != "ok" or foreign_keys:
            raise SalesStoreError("INTEGRITY_FAILED", f"integrity={integrity}, foreign_key_violations={len(foreign_keys)}")
        return {
            "status": "ok", "schema_version": version, "integrity_check": integrity,
            "foreign_key_violations": foreign_keys, "tables": _table_counts(connection),
            "logical_state_sha256": _logical_state_sha256(connection),
            "database_sha256": _sha256_file(path), "database_size": path.stat().st_size,
        }
    except sqlite3.Error as error:
        raise SalesStoreError("SQLITE_ERROR", f"无法验证业务数据库：{error}") from error
    finally:
        connection.close()


def migrate_sales_store(
    project_root: Path | str,
    *,
    sales_dir: Path | str = "data/sales",
    knowledge_file: Path | str = "data/knowledge/source-register.csv",
    database_path: Path | str | None = None,
    report_path: Path | str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    root = _canonical_root(project_root)
    sales = _controlled_path(root, sales_dir, "销售数据目录")
    knowledge = _controlled_path(root, knowledge_file, "知识来源文件")
    if sales.is_symlink() or not sales.is_dir():
        raise SalesStoreError("UNSAFE_PATH", "销售数据目录必须是普通目录且不能是符号链接")
    plan = _plan_migration(root, sales, knowledge)
    report: dict[str, Any] = {
        "schema_version": "1.0", "store_schema_version": SCHEMA_VERSION,
        "mode": "preflight" if dry_run else "staging", "batch_id": plan["batch_id"],
        "created_at": plan["migration_time"], "source_fingerprint": plan["source_fingerprint"],
        "source_files": plan["source_files"], "total_rows": plan["total_rows"],
        "counts": plan["counts"], "issues": plan["issues"],
        "staging_eligible": plan["staging_eligible"], "cutover_ready": False,
        "entity_counts": {table: len(plan["entities"][table]) for table in ENTITY_INSERT_ORDER},
    }
    if not dry_run:
        if database_path is None:
            raise SalesStoreError("INVALID_INPUT", "staging 模式必须指定全新的 database_path")
        database = _controlled_path(root, database_path, "staging 数据库")
        if database.exists() or database.is_symlink():
            raise SalesStoreError("TARGET_EXISTS", "staging 数据库已存在，拒绝覆盖")
        source_bytes = sum(int(item["size"]) for item in plan["source_files"])
        _require_free_space(database.parent, max(64 * 1024 * 1024, source_bytes * 4 + 16 * 1024 * 1024), "staging 迁移")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{database.name}.", suffix=".tmp", dir=database.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            connection = sqlite3.connect(temporary)
            try:
                _configure(connection, writable=True)
                _initialize_connection(connection)
                connection.execute("BEGIN IMMEDIATE")
                for table in ENTITY_INSERT_ORDER:
                    for entity in plan["entities"][table]:
                        _insert_entity(connection, table, entity)
                status = "staged" if plan["counts"]["quarantined"] == 0 and plan["counts"]["failed"] == 0 else "blocked"
                connection.execute(
                    "INSERT INTO import_batches(batch_id,schema_version,mode,status,source_files_json,source_fingerprint,total_rows,imported_rows,skipped_rows,quarantined_rows,failed_rows,cutover_ready,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        plan["batch_id"], SCHEMA_VERSION, "staging", status,
                        json.dumps(plan["source_files"], ensure_ascii=False, sort_keys=True),
                        plan["source_fingerprint"], plan["total_rows"], plan["counts"]["imported"],
                        plan["counts"]["skipped_duplicate"], plan["counts"]["quarantined"],
                        plan["counts"]["failed"], 1 if status == "staged" else 0,
                        plan["migration_time"], plan["migration_time"],
                    ),
                )
                for outcome in plan["outcomes"]:
                    connection.execute(
                        "INSERT INTO import_rows(import_row_id,batch_id,source_name,row_number,row_sha256,entity_type,entity_id,result,error_code,error_message,raw_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            _stable_id("import-row", plan["batch_id"], outcome["source_name"], str(outcome["row_number"])),
                            plan["batch_id"], outcome["source_name"], outcome["row_number"], outcome["row_sha256"],
                            outcome["entity_type"], outcome["entity_id"], outcome["result"], outcome["error_code"],
                            outcome["error_message"], json.dumps(outcome["raw"], ensure_ascii=False, sort_keys=True),
                            plan["migration_time"],
                        ),
                    )
                connection.commit()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                if integrity != "ok" or foreign_keys:
                    raise SalesStoreError("INTEGRITY_FAILED", f"staging 对账失败：integrity={integrity}, foreign_keys={len(foreign_keys)}")
                report["reconciliation"] = {
                    "integrity_check": integrity, "foreign_key_violations": len(foreign_keys),
                    "database_counts": _table_counts(connection),
                }
            finally:
                connection.close()
            _publish_file(temporary, database)
            verification = verify_sales_store(database)
            report["database_relative_path"] = database.relative_to(root).as_posix()
            report["database_sha256"] = verification["database_sha256"]
            report["logical_state_sha256"] = verification["logical_state_sha256"]
            report["cutover_ready"] = plan["counts"]["quarantined"] == 0 and plan["counts"]["failed"] == 0
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    report_without_hash = dict(report)
    report["report_sha256"] = _sha256_bytes(_json_bytes(report_without_hash))
    if report_path is not None:
        target = _controlled_path(root, report_path, "迁移报告")
        _atomic_create(target, _json_bytes(report))
        report["report_relative_path"] = target.relative_to(root).as_posix()
    return report


def backup_sales_store(project_root: Path | str, database_path: Path | str, target_path: Path | str) -> dict[str, Any]:
    root = _canonical_root(project_root)
    source = _controlled_path(root, database_path, "源数据库")
    target = _controlled_path(root, target_path, "数据库备份")
    _regular_file(source, "源数据库")
    if target.exists() or target.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", "备份目标已存在，拒绝覆盖")
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", "备份清单目标已存在，拒绝覆盖")
    verify_sales_store(source)
    _require_free_space(target.parent, source.stat().st_size * 2 + 16 * 1024 * 1024, "数据库备份")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        verify_sales_store(temporary)
        _publish_file(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    verification = verify_sales_store(target)
    manifest = {
        "schema_version": "1.0", "backup_type": "agent4market-sqlite",
        "created_at": _now(), "source_relative_path": source.relative_to(root).as_posix(),
        "backup_relative_path": target.relative_to(root).as_posix(),
        "database_sha256": verification["database_sha256"],
        "logical_state_sha256": verification["logical_state_sha256"],
        "store_schema_version": SCHEMA_VERSION,
    }
    _atomic_create(manifest_path, _json_bytes(manifest))
    return {**manifest, "manifest_relative_path": manifest_path.relative_to(root).as_posix()}


def restore_sales_store(project_root: Path | str, backup_path: Path | str, target_path: Path | str) -> dict[str, Any]:
    root = _canonical_root(project_root)
    backup = _controlled_path(root, backup_path, "数据库备份")
    target = _controlled_path(root, target_path, "恢复目标")
    _regular_file(backup, "数据库备份")
    if target.exists() or target.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", "恢复目标已存在，拒绝覆盖")
    before = verify_sales_store(backup)
    _require_free_space(target.parent, backup.stat().st_size * 2 + 16 * 1024 * 1024, "数据库恢复")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        verify_sales_store(temporary)
        _publish_file(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    after = verify_sales_store(target)
    if before["tables"] != after["tables"] or before["logical_state_sha256"] != after["logical_state_sha256"]:
        raise SalesStoreError("RESTORE_MISMATCH", "恢复后的表计数或逻辑状态与备份不一致")
    return {
        "status": "ok", "restored_relative_path": target.relative_to(root).as_posix(),
        "database_sha256": after["database_sha256"], "tables": after["tables"],
    }


def _safe_csv_value(value: Any) -> tuple[str, bool]:
    if value is None:
        return "", False
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text, True
    return text, False


def export_sales_store(project_root: Path | str, database_path: Path | str, target_dir: Path | str) -> dict[str, Any]:
    root = _canonical_root(project_root)
    database = _controlled_path(root, database_path, "业务数据库")
    target = _controlled_path(root, target_dir, "导出目录")
    _regular_file(database, "业务数据库")
    if target.exists() or target.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", "导出目录已存在，拒绝覆盖")
    source_verification = verify_sales_store(database)
    _require_free_space(target.parent, database.stat().st_size * 4 + 16 * 1024 * 1024, "CSV 导出")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    files: list[dict[str, Any]] = []
    sanitized_total = 0
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            _configure(connection, writable=False)
            for table in EXPORT_TABLES:
                columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                primary = next((row[1] for row in connection.execute(f"PRAGMA table_info({table})") if row[5]), columns[0])
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY {primary}").fetchall()
                output = io.StringIO(newline="")
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(columns)
                sanitized = 0
                null_cells: list[str] = []
                formula_escapes: list[str] = []
                for row_number, row in enumerate(rows, start=2):
                    values: list[str] = []
                    for column in columns:
                        if row[column] is None:
                            null_cells.append(f"{row_number}:{column}")
                        value, changed = _safe_csv_value(row[column])
                        values.append(value)
                        sanitized += int(changed)
                        if changed:
                            formula_escapes.append(f"{row_number}:{column}")
                    writer.writerow(values)
                content = output.getvalue().encode("utf-8-sig")
                path = temporary / f"{table}.csv"
                path.write_bytes(content)
                files.append({
                    "file": path.name, "table": table, "rows": len(rows),
                    "sha256": _sha256_bytes(content), "formula_cells_escaped": sanitized,
                    "formula_escapes": formula_escapes, "null_cells": null_cells,
                })
                sanitized_total += sanitized
        finally:
            connection.close()
        manifest = {
            "schema_version": "1.0", "export_type": "agent4market-core-tables",
            "created_at": _now(), "database_sha256": source_verification["database_sha256"],
            "logical_state_sha256": source_verification["logical_state_sha256"],
            "store_schema_version": SCHEMA_VERSION, "files": files,
            "formula_cells_escaped": sanitized_total,
        }
        (temporary / "export-manifest.json").write_bytes(_json_bytes(manifest))
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {**manifest, "export_relative_path": target.relative_to(root).as_posix()}


def import_sales_store_export(
    project_root: Path | str, export_dir: Path | str, target_database: Path | str
) -> dict[str, Any]:
    root = _canonical_root(project_root)
    source = _controlled_path(root, export_dir, "CSV 导出目录")
    target = _controlled_path(root, target_database, "回环导入数据库")
    if source.is_symlink() or not source.is_dir():
        raise SalesStoreError("UNSAFE_PATH", "CSV 导出目录必须是普通目录且不能是符号链接")
    if target.exists() or target.is_symlink():
        raise SalesStoreError("TARGET_EXISTS", "回环导入目标已存在，拒绝覆盖")
    manifest_path = source / "export-manifest.json"
    _regular_file(manifest_path, "导出清单", 32 * 1024 * 1024)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SalesStoreError("DAMAGED_EXPORT", f"导出清单无效：{error}") from error
    if manifest.get("export_type") != "agent4market-core-tables" or manifest.get("store_schema_version") != SCHEMA_VERSION:
        raise SalesStoreError("SCHEMA_UNSUPPORTED", "导出清单类型或 schema 版本不受支持")
    entries = manifest.get("files")
    if not isinstance(entries, list) or {item.get("table") for item in entries if isinstance(item, dict)} != set(EXPORT_TABLES):
        raise SalesStoreError("DAMAGED_EXPORT", "导出清单未完整覆盖核心业务表")
    entries_by_table = {item["table"]: item for item in entries}
    export_bytes = 0
    for table in EXPORT_TABLES:
        entry = entries_by_table[table]
        if entry.get("file") != f"{table}.csv":
            raise SalesStoreError("UNSAFE_PATH", f"{table} 导出路径无效")
        export_bytes += _regular_file(source / entry["file"], f"{table} 导出", MAX_SOURCE_BYTES * 8).stat().st_size
    _require_free_space(target.parent, export_bytes * 3 + 16 * 1024 * 1024, "CSV 回环导入")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            _configure(connection, writable=True)
            _initialize_connection(connection)
            connection.execute("BEGIN IMMEDIATE")
            for table in EXPORT_TABLES:
                entry = entries_by_table[table]
                csv_path = source / entry["file"]
                if csv_path.parent.resolve() != source or csv_path.name != f"{table}.csv":
                    raise SalesStoreError("UNSAFE_PATH", f"{table} 导出路径无效")
                _regular_file(csv_path, f"{table} 导出", MAX_SOURCE_BYTES * 8)
                content = csv_path.read_bytes()
                if entry.get("sha256") != _sha256_bytes(content):
                    raise SalesStoreError("EXPORT_HASH_MISMATCH", f"{table}.csv 哈希不一致")
                try:
                    parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True))
                except (UnicodeDecodeError, csv.Error) as error:
                    raise SalesStoreError("DAMAGED_EXPORT", f"{table}.csv 无法读取：{error}") from error
                table_info = connection.execute(f"PRAGMA table_info({table})").fetchall()
                columns = [row[1] for row in table_info]
                types = {row[1]: row[2] for row in table_info}
                if not parsed or parsed[0] != columns or len(parsed) - 1 != entry.get("rows"):
                    raise SalesStoreError("EXPORT_SCHEMA_MISMATCH", f"{table}.csv 表头或行数与清单不一致")
                null_cells = set(entry.get("null_cells") or [])
                formula_escapes = set(entry.get("formula_escapes") or [])
                if len(formula_escapes) != entry.get("formula_cells_escaped"):
                    raise SalesStoreError("DAMAGED_EXPORT", f"{table} 公式转义清单数量不一致")
                placeholders = ",".join("?" for _ in columns)
                for row_number, values in enumerate(parsed[1:], start=2):
                    if len(values) != len(columns):
                        raise SalesStoreError("DAMAGED_EXPORT", f"{table}.csv 第 {row_number} 行字段数量错误")
                    restored: list[Any] = []
                    for column, value in zip(columns, values, strict=True):
                        coordinate = f"{row_number}:{column}"
                        if coordinate in null_cells:
                            if value != "":
                                raise SalesStoreError("DAMAGED_EXPORT", f"{table}.{coordinate} 的 null 标记与 CSV 不一致")
                            restored.append(None)
                            continue
                        if coordinate in formula_escapes:
                            if not value.startswith("'") or not value[1:].startswith(FORMULA_PREFIXES):
                                raise SalesStoreError("DAMAGED_EXPORT", f"{table}.{coordinate} 的公式转义已损坏")
                            value = value[1:]
                        if types[column].upper() == "INTEGER":
                            try:
                                restored.append(int(value))
                            except ValueError as error:
                                raise SalesStoreError("DAMAGED_EXPORT", f"{table}.{coordinate} 不是整数") from error
                        else:
                            restored.append(value)
                    connection.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        restored,
                    )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            counts = _table_counts(connection)
            if integrity != "ok" or foreign_keys:
                raise SalesStoreError("INTEGRITY_FAILED", "CSV 回环导入未通过完整性或外键检查")
        finally:
            connection.close()
        _publish_file(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    verification = verify_sales_store(target)
    expected_counts = {item["table"]: item["rows"] for item in entries}
    if verification["tables"] != expected_counts:
        raise SalesStoreError("IMPORT_RECONCILIATION_FAILED", "CSV 回环导入后的核心表计数不一致")
    if verification["logical_state_sha256"] != manifest.get("logical_state_sha256"):
        raise SalesStoreError("IMPORT_RECONCILIATION_FAILED", "CSV 回环导入后的核心字段或关联不等价")
    return {
        "status": "ok", "database_relative_path": target.relative_to(root).as_posix(),
        "database_sha256": verification["database_sha256"], "tables": verification["tables"],
        "source_export_sha256": _sha256_file(manifest_path),
    }


def activate_sales_store(
    project_root: Path | str,
    *,
    database_path: Path | str,
    report_path: Path | str,
    approval_path: Path | str,
    expected_pointer_sha256: str | None,
) -> dict[str, Any]:
    root = _canonical_root(project_root)
    database = _controlled_path(root, database_path, "业务数据库")
    report_file = _controlled_path(root, report_path, "迁移报告")
    approval_file = _controlled_path(root, approval_path, "迁移批准文件")
    _regular_file(database, "业务数据库")
    _regular_file(report_file, "迁移报告", 4 * 1024 * 1024)
    _regular_file(approval_file, "迁移批准文件", 64 * 1024)
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
        approval = json.loads(approval_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SalesStoreError("INVALID_APPROVAL", f"迁移报告或批准文件不是有效 JSON：{error}") from error
    verification = verify_sales_store(database)
    report_hash = report.get("report_sha256")
    report_without_hash = {key: value for key, value in report.items() if key not in {"report_sha256", "report_relative_path"}}
    if report_hash != _sha256_bytes(_json_bytes(report_without_hash)):
        raise SalesStoreError("REPORT_TAMPERED", "迁移报告内容与 report_sha256 不一致")
    if (
        not report.get("cutover_ready")
        or report.get("database_sha256") != verification["database_sha256"]
        or report.get("logical_state_sha256") != verification["logical_state_sha256"]
    ):
        raise SalesStoreError("CUTOVER_BLOCKED", "迁移报告未通过或数据库哈希不一致")
    required_approval = {
        "approval_type": "sales-store-cutover",
        "approved": True,
        "migration_batch_id": report.get("batch_id"),
        "database_sha256": verification["database_sha256"],
        "report_sha256": report_hash,
    }
    if any(approval.get(key) != value for key, value in required_approval.items()):
        raise SalesStoreError("INVALID_APPROVAL", "批准文件未绑定当前迁移批次、报告和数据库")
    if not isinstance(approval.get("approval_id"), str) or not approval["approval_id"] or not _valid_timestamp(str(approval.get("approved_at") or "")):
        raise SalesStoreError("INVALID_APPROVAL", "批准文件缺少 approval_id 或 UTC approved_at")
    pointer = root / "data" / "storage-backend.json"
    if pointer.is_symlink():
        raise SalesStoreError("UNSAFE_PATH", "存储指针不能是符号链接")
    receipt_path = root / ".pi" / "director-runtime" / "storage-activations" / f"{report['batch_id']}.json"
    if receipt_path.is_symlink():
        raise SalesStoreError("UNSAFE_PATH", "存储切换回执不能是符号链接")
    current_bytes: bytes | None
    if pointer.exists():
        if not pointer.is_file():
            raise SalesStoreError("UNSAFE_PATH", "存储指针路径已被非普通文件占用")
        current_bytes = pointer.read_bytes()
    else:
        current_bytes = None
    current_hash = _sha256_bytes(current_bytes) if current_bytes is not None else None
    receipt: dict[str, Any] | None = None
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", f"存储切换回执损坏：{error}") from error
        if (
            receipt.get("batch_id") != report.get("batch_id")
            or receipt.get("report_sha256") != report_hash
            or receipt.get("database_sha256") != verification["database_sha256"]
            or receipt.get("approval_id") != approval["approval_id"]
            or receipt.get("previous_pointer_sha256") != expected_pointer_sha256
        ):
            raise SalesStoreError("ACTIVATION_CONFLICT", "已有切换回执与当前批准或预期指针不一致")
        if receipt.get("status") == "rolled_back":
            raise SalesStoreError("ACTIVATION_ROLLED_BACK", "该迁移批次已经回滚，不能复用旧批准再次切换")
        payload = receipt.get("activation")
        if not isinstance(payload, dict):
            raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "切换回执缺少 activation payload")
        pointer_bytes = _json_bytes(payload)
        pointer_hash = _sha256_bytes(pointer_bytes)
        if receipt.get("pointer_sha256") != pointer_hash:
            raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "切换回执中的指针哈希无效")
        if receipt.get("status") == "committed":
            if current_hash != pointer_hash:
                raise SalesStoreError("POINTER_CONFLICT", "已提交切换回执与当前存储指针不一致")
            return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}
        if receipt.get("status") != "prepared":
            raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "切换回执状态无效")
        if current_hash == pointer_hash:
            receipt["status"] = "committed"
            receipt["updated_at"] = _now()
            _atomic_replace(receipt_path, _json_bytes(receipt))
            return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}
        if current_hash != receipt.get("previous_pointer_sha256"):
            raise SalesStoreError("POINTER_CONFLICT", "准备态切换回执对应的旧指针已经变化")
    else:
        if current_hash != expected_pointer_sha256:
            raise SalesStoreError("POINTER_CONFLICT", "存储指针版本已变化，拒绝覆盖")
        payload = {
            "backend": "sqlite", "schema_version": SCHEMA_VERSION,
            "database_relative_path": database.relative_to(root).as_posix(),
            "migration_batch_id": report["batch_id"],
            "database_sha256_at_cutover": verification["database_sha256"],
            "logical_state_sha256_at_cutover": verification["logical_state_sha256"],
            "approval_id": approval["approval_id"], "activated_at": _now(),
        }
        pointer_bytes = _json_bytes(payload)
        pointer_hash = _sha256_bytes(pointer_bytes)
        receipt = {
            "schema_version": "1.0", "status": "prepared", "batch_id": report["batch_id"],
            "activation": payload, "pointer_sha256": pointer_hash,
            "previous_pointer_sha256": current_hash,
            "previous_pointer_base64": base64.b64encode(current_bytes).decode("ascii") if current_bytes is not None else None,
            "database_sha256": verification["database_sha256"],
            "logical_state_sha256": verification["logical_state_sha256"],
            "report_sha256": report_hash, "approval_id": approval["approval_id"],
            "created_at": _now(), "updated_at": _now(),
        }
        _atomic_create(receipt_path, _json_bytes(receipt))
    _atomic_replace(pointer, pointer_bytes)
    if _sha256_file(pointer) != pointer_hash:
        raise SalesStoreError("POINTER_PUBLISH_FAILED", "存储指针发布后的哈希不一致")
    receipt["status"] = "committed"
    receipt["updated_at"] = _now()
    _atomic_replace(receipt_path, _json_bytes(receipt))
    return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}


def rollback_sales_store_activation(
    project_root: Path | str,
    *,
    batch_id: str,
    expected_current_pointer_sha256: str,
) -> dict[str, Any]:
    root = _canonical_root(project_root)
    if not re.fullmatch(r"migration-[a-f0-9]{20}", batch_id):
        raise SalesStoreError("INVALID_INPUT", "batch_id 格式无效")
    pointer = root / "data" / "storage-backend.json"
    receipt_path = root / ".pi" / "director-runtime" / "storage-activations" / f"{batch_id}.json"
    _regular_file(receipt_path, "存储切换回执", 256 * 1024)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", f"存储切换回执损坏：{error}") from error
    if receipt.get("batch_id") != batch_id or receipt.get("status") not in {"committed", "rolled_back"}:
        raise SalesStoreError("ROLLBACK_BLOCKED", "只有已提交的当前批次可以回滚")
    previous_base64 = receipt.get("previous_pointer_base64")
    try:
        previous_bytes = base64.b64decode(previous_base64, validate=True) if isinstance(previous_base64, str) else None
    except (ValueError, binascii.Error) as error:
        raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "旧指针备份不是有效 base64") from error
    previous_hash = _sha256_bytes(previous_bytes) if previous_bytes is not None else None
    if previous_hash != receipt.get("previous_pointer_sha256"):
        raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "旧指针备份与回执哈希不一致")
    if pointer.is_symlink() or (pointer.exists() and not pointer.is_file()):
        raise SalesStoreError("UNSAFE_PATH", "当前存储指针不是受控普通文件")
    current_hash = _sha256_file(pointer) if pointer.is_file() and not pointer.is_symlink() else None
    if receipt.get("status") == "rolled_back":
        if current_hash != previous_hash:
            raise SalesStoreError("POINTER_CONFLICT", "回滚回执与当前指针不一致")
        return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}
    activation = receipt.get("activation")
    if not isinstance(activation, dict):
        raise SalesStoreError("ACTIVATION_RECEIPT_CORRUPT", "回执缺少 activation payload")
    database = _controlled_path(root, str(activation.get("database_relative_path") or ""), "已激活数据库")
    verification = verify_sales_store(database)
    if verification["logical_state_sha256"] != receipt.get("logical_state_sha256"):
        raise SalesStoreError("ROLLBACK_REQUIRES_RECONCILIATION", "切换后已有业务数据变化，禁止自动回到旧存储")
    if current_hash == previous_hash:
        receipt["status"] = "rolled_back"
        receipt["rolled_back_at"] = _now()
        receipt["updated_at"] = receipt["rolled_back_at"]
        _atomic_replace(receipt_path, _json_bytes(receipt))
        return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}
    if current_hash != expected_current_pointer_sha256 or current_hash != receipt.get("pointer_sha256"):
        raise SalesStoreError("POINTER_CONFLICT", "当前指针已变化，拒绝回滚")
    if previous_bytes is None:
        pointer.unlink()
    else:
        _atomic_replace(pointer, previous_bytes)
    resulting_hash = _sha256_file(pointer) if pointer.is_file() else None
    if resulting_hash != previous_hash:
        raise SalesStoreError("POINTER_ROLLBACK_FAILED", "恢复旧存储指针后的哈希不一致")
    receipt["status"] = "rolled_back"
    receipt["rolled_back_at"] = _now()
    receipt["updated_at"] = receipt["rolled_back_at"]
    _atomic_replace(receipt_path, _json_bytes(receipt))
    return {"status": "ok", "activation_status": receipt["status"], **{key: value for key, value in receipt.items() if key != "status"}, "pointer_relative_path": pointer.relative_to(root).as_posix()}
