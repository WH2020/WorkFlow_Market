from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit


CATALOG_SCHEMA_VERSION = "1.0"
CATALOG_RELATIVE_PATH = Path("data/library/catalog.json")
MAX_RECORDS = 10_000
MAX_VERSIONS = 100
MAX_TAGS = 20
MAX_TEXT = 12_000

CATEGORIES = (
    "inbox",
    "customer",
    "opportunity",
    "government",
    "bidding",
    "industry",
    "sales_asset",
    "company",
    "internal",
)
STATUSES = ("pending", "verified", "superseded", "rejected", "archived")
CONFIDENTIALITY_LEVELS = ("internal", "restricted", "public")
KINDS = ("source", "project_file", "library_file", "artifact", "url")

EDITABLE_FIELDS = (
    "title",
    "category",
    "status",
    "confidentiality",
    "project_id",
    "account_id",
    "opportunity_id",
    "bid_id",
    "publisher",
    "published_date",
    "region",
    "topic",
    "source_type",
    "review_at",
    "notes",
    "tags",
)

_CATALOG_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class LibraryStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _canonical_root(project_root: Path | str) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise LibraryStoreError("PROJECT_MISSING", "项目目录不存在")
    return root


def _catalog_path(project_root: Path | str) -> Path:
    root = _canonical_root(project_root)
    path = root / CATALOG_RELATIVE_PATH
    if path.is_symlink() or path.parent.is_symlink():
        raise LibraryStoreError("UNSAFE_PATH", "资料库索引不能位于符号链接中")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root) or resolved == root:
        raise LibraryStoreError("UNSAFE_PATH", "资料库索引越出应用目录")
    return path


def _empty_catalog() -> dict[str, Any]:
    return {"schema_version": CATALOG_SCHEMA_VERSION, "updated_at": "", "records": []}


def _text(value: Any, label: str, maximum: int = MAX_TEXT, *, required: bool = False) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if required and not text:
        raise LibraryStoreError("INVALID_INPUT", f"{label}不能为空")
    if len(text) > maximum:
        raise LibraryStoreError("INVALID_INPUT", f"{label}超过 {maximum} 字")
    return text


def _safe_id(value: Any, label: str, *, required: bool = False) -> str:
    text = _text(value, label, 128, required=required)
    if text and _SAFE_ID.fullmatch(text) is None:
        raise LibraryStoreError("INVALID_INPUT", f"{label}格式无效")
    return text


def _normalize_tags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise LibraryStoreError("INVALID_INPUT", f"标签必须是最多 {MAX_TAGS} 项的数组")
    tags: list[str] = []
    for item in value:
        tag = _text(item, "标签", 40)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_url(value: Any) -> str:
    raw = _text(value, "网页地址", 2048, required=True)
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise LibraryStoreError("INVALID_INPUT", "网页地址只允许普通 HTTP/HTTPS 地址")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def stable_library_id(kind: str, reference: str) -> str:
    if kind not in KINDS:
        raise LibraryStoreError("INVALID_INPUT", "资料类型无效")
    normalized = unicodedata.normalize("NFKC", reference).strip()
    if not normalized:
        raise LibraryStoreError("INVALID_INPUT", "资料引用不能为空")
    digest = hashlib.sha256(f"{kind}\0{normalized}".encode("utf-8")).hexdigest()
    return f"library-{digest[:24]}"


def _read_catalog(project_root: Path | str) -> dict[str, Any]:
    path = _catalog_path(project_root)
    if not path.exists():
        return _empty_catalog()
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise LibraryStoreError("CATALOG_INVALID", "资料库索引不是受支持的普通文件")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LibraryStoreError("CATALOG_INVALID", f"资料库索引无法读取：{error}") from error
    if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise LibraryStoreError("CATALOG_INVALID", "资料库索引版本不受支持")
    records = value.get("records")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise LibraryStoreError("CATALOG_INVALID", "资料库索引记录数量无效")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise LibraryStoreError("CATALOG_INVALID", "资料库索引包含无效记录")
        identity = _safe_id(record.get("library_id"), "资料编号", required=True)
        if identity in seen:
            raise LibraryStoreError("CATALOG_INVALID", "资料库索引包含重复编号")
        seen.add(identity)
        if record.get("kind") not in KINDS or not isinstance(record.get("version"), int) or record["version"] < 1:
            raise LibraryStoreError("CATALOG_INVALID", "资料库索引记录结构无效")
        versions = record.get("versions", [])
        if not isinstance(versions, list) or len(versions) > MAX_VERSIONS:
            raise LibraryStoreError("CATALOG_INVALID", "资料版本记录无效")
    return value


def _atomic_write(project_root: Path | str, catalog: Mapping[str, Any]) -> None:
    path = _catalog_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise LibraryStoreError("UNSAFE_PATH", "资料库目录不能是符号链接")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(catalog))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def catalog_revision(project_root: Path | str) -> str:
    path = _catalog_path(project_root)
    if not path.exists():
        return "library-catalog:missing"
    if path.is_symlink() or not path.is_file():
        return "library-catalog:invalid"
    metadata = path.stat()
    return f"library-catalog:{metadata.st_mtime_ns}:{metadata.st_size}"


def _validate_record(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["library_id"] = _safe_id(result.get("library_id"), "资料编号", required=True)
    if result.get("kind") not in KINDS:
        raise LibraryStoreError("INVALID_INPUT", "资料类型无效")
    result["title"] = _text(result.get("title"), "资料名称", 500, required=True)
    category = _text(result.get("category") or "inbox", "资料分类", 40)
    if category not in CATEGORIES:
        raise LibraryStoreError("INVALID_INPUT", "资料分类无效")
    result["category"] = category
    status = _text(result.get("status") or "pending", "核验状态", 40)
    if status not in STATUSES:
        raise LibraryStoreError("INVALID_INPUT", "核验状态无效")
    result["status"] = status
    confidentiality = _text(result.get("confidentiality") or "internal", "保密级别", 40)
    if confidentiality not in CONFIDENTIALITY_LEVELS:
        raise LibraryStoreError("INVALID_INPUT", "保密级别无效")
    result["confidentiality"] = confidentiality
    for field, label in (
        ("project_id", "项目编号"), ("account_id", "客户编号"),
        ("opportunity_id", "商机编号"), ("bid_id", "投标编号"),
    ):
        result[field] = _safe_id(result.get(field), label)
    for field, maximum in (
        ("publisher", 500), ("published_date", 40), ("region", 200), ("topic", 500),
        ("source_type", 100), ("review_at", 40), ("notes", 4000),
    ):
        result[field] = _text(result.get(field), field, maximum)
    result["tags"] = _normalize_tags(result.get("tags", []))
    versions = result.get("versions", [])
    if not isinstance(versions, list) or len(versions) > MAX_VERSIONS:
        raise LibraryStoreError("INVALID_INPUT", "资料版本记录无效")
    result["versions"] = versions
    return result


def infer_category(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    rules = (
        ("bidding", r"招标|投标|标书|采购|磋商|询价|中标|废标"),
        ("government", r"政府|政策|园区|财政|申报|扶持|补贴|招商|管委会"),
        ("company", r"营业执照|资质|软著|软件著作权|专利|检测报告|审计报告|团队履历"),
        ("customer", r"客户|访谈|沟通纪要|会议纪要|需求|异议|关键人"),
        ("opportunity", r"商机|报价|合同|采购意向|预算|决策链|成交"),
        ("internal", r"负责人|审批人|内部流程|操作手册|管理制度|通讯录"),
        ("sales_asset", r"ppt|演示|方案|案例|白皮书|产品介绍|话术|faq|模板"),
        ("industry", r"行业|市场|竞品|竞争对手|脑机|具身|数据采集|数采|趋势|研究"),
    )
    for category, pattern in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return "inbox"


def _base_record(kind: str, library_id: str, title: str, *, created_at: str = "") -> dict[str, Any]:
    timestamp = created_at or _now()
    return {
        "library_id": library_id,
        "kind": kind,
        "title": title,
        "category": "inbox",
        "status": "pending",
        "confidentiality": "internal",
        "project_id": "",
        "account_id": "",
        "opportunity_id": "",
        "bid_id": "",
        "publisher": "",
        "published_date": "",
        "region": "",
        "topic": "",
        "source_type": "",
        "review_at": "",
        "notes": "",
        "tags": [],
        "versions": [],
        "created_at": timestamp,
        "updated_at": timestamp,
        "deleted_at": "",
        "version": 1,
    }


def register_url(project_root: Path | str, payload: Mapping[str, Any]) -> dict[str, Any]:
    url = normalize_url(payload.get("url"))
    library_id = stable_library_id("url", url)
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        existing = next((record for record in catalog["records"] if record.get("library_id") == library_id), None)
        if existing and not existing.get("deleted_at"):
            return {**existing, "duplicate": True}
        title = _text(payload.get("title"), "资料名称", 500) or f"网页资料 · {urlsplit(url).hostname}"
        record = _base_record("url", library_id, title)
        record.update({"url": url, "source_type": "web", "category": infer_category(payload.get("title"), payload.get("topic"))})
        for field in EDITABLE_FIELDS:
            if field in payload:
                record[field] = payload[field]
        record = _validate_record(record)
        if existing:
            record["created_at"] = existing.get("created_at") or record["created_at"]
            record["version"] = int(existing.get("version") or 1) + 1
            catalog["records"][catalog["records"].index(existing)] = record
        else:
            if len(catalog["records"]) >= MAX_RECORDS:
                raise LibraryStoreError("CATALOG_FULL", "资料库索引已达到记录上限")
            catalog["records"].append(record)
        catalog["updated_at"] = _now()
        _atomic_write(project_root, catalog)
        return record


def register_file(project_root: Path | str, metadata: Mapping[str, Any]) -> dict[str, Any]:
    library_id = _safe_id(metadata.get("library_id"), "资料编号", required=True)
    path = _text(metadata.get("path"), "资料路径", 600, required=True)
    timestamp = _now()
    version_entry = {
        "version_id": _safe_id(metadata.get("version_id") or "version-0001", "版本编号", required=True),
        "path": path,
        "filename": _text(metadata.get("filename"), "文件名", 120, required=True),
        "sha256": _text(metadata.get("sha256"), "文件摘要", 64, required=True),
        "size": int(metadata.get("size") or 0),
        "created_at": timestamp,
    }
    if not re.fullmatch(r"[0-9a-f]{64}", version_entry["sha256"]) or version_entry["size"] < 1:
        raise LibraryStoreError("INVALID_INPUT", "文件版本摘要或大小无效")
    record = _base_record("library_file", library_id, _text(metadata.get("title") or Path(version_entry["filename"]).stem, "资料名称", 500, required=True))
    record.update({
        "path": path,
        "file_sha256": version_entry["sha256"],
        "size": version_entry["size"],
        "current_version_id": version_entry["version_id"],
        "versions": [version_entry],
        "category": infer_category(record["title"], version_entry["filename"]),
    })
    for field in EDITABLE_FIELDS:
        if field in metadata:
            record[field] = metadata[field]
    record = _validate_record(record)
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        if any(item.get("library_id") == library_id for item in catalog["records"]):
            raise LibraryStoreError("TARGET_EXISTS", "资料编号已存在")
        if any(
            version.get("sha256") == version_entry["sha256"]
            for item in catalog["records"] for version in item.get("versions", [])
        ):
            raise LibraryStoreError("DUPLICATE_FILE", "相同内容的文件已经在资料库中")
        if len(catalog["records"]) >= MAX_RECORDS:
            raise LibraryStoreError("CATALOG_FULL", "资料库索引已达到记录上限")
        catalog["records"].append(record)
        catalog["updated_at"] = timestamp
        _atomic_write(project_root, catalog)
    return record


def append_file_version(project_root: Path | str, library_id: str, metadata: Mapping[str, Any], expected_version: int) -> dict[str, Any]:
    identity = _safe_id(library_id, "资料编号", required=True)
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        record = next((item for item in catalog["records"] if item.get("library_id") == identity), None)
        if record is None or record.get("kind") != "library_file" or record.get("deleted_at"):
            raise LibraryStoreError("NOT_FOUND", "可更新版本的资料不存在")
        if record.get("version") != expected_version:
            raise LibraryStoreError("VERSION_CONFLICT", "资料信息已变化，请刷新后重试")
        versions = list(record.get("versions", []))
        if len(versions) >= MAX_VERSIONS:
            raise LibraryStoreError("VERSION_LIMIT", "资料版本数量已达到上限")
        digest = _text(metadata.get("sha256"), "文件摘要", 64, required=True)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise LibraryStoreError("INVALID_INPUT", "文件摘要无效")
        if any(version.get("sha256") == digest for version in versions):
            raise LibraryStoreError("DUPLICATE_FILE", "该文件内容与已有版本完全相同")
        version_entry = {
            "version_id": _safe_id(metadata.get("version_id"), "版本编号", required=True),
            "path": _text(metadata.get("path"), "资料路径", 600, required=True),
            "filename": _text(metadata.get("filename"), "文件名", 120, required=True),
            "sha256": digest,
            "size": int(metadata.get("size") or 0),
            "created_at": _now(),
        }
        if version_entry["size"] < 1:
            raise LibraryStoreError("INVALID_INPUT", "文件大小无效")
        versions.append(version_entry)
        record.update({
            "path": version_entry["path"],
            "file_sha256": digest,
            "size": version_entry["size"],
            "current_version_id": version_entry["version_id"],
            "versions": versions,
            "updated_at": _now(),
            "version": expected_version + 1,
            "status": "pending",
        })
        validated = _validate_record(record)
        catalog["records"][catalog["records"].index(record)] = validated
        catalog["updated_at"] = validated["updated_at"]
        _atomic_write(project_root, catalog)
        return validated


def update_metadata(project_root: Path | str, base_entry: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = _safe_id(base_entry.get("library_id"), "资料编号", required=True)
    expected = payload.get("expected_version", 0)
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        raise LibraryStoreError("INVALID_INPUT", "资料版本无效")
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        existing = next((item for item in catalog["records"] if item.get("library_id") == identity), None)
        actual = int(existing.get("version") or 0) if existing else 0
        if actual != expected:
            raise LibraryStoreError("VERSION_CONFLICT", "资料信息已变化，请刷新后重试")
        if existing:
            record = dict(existing)
        else:
            record = _base_record(str(base_entry.get("kind") or "source"), identity, str(base_entry.get("title") or "未命名资料"), created_at=str(base_entry.get("created_at") or ""))
            for field in (
                "source_id", "path", "url", "file_sha256", "size", "current_version_id", "versions",
                "project_id", "account_id", "opportunity_id", "bid_id", "publisher", "published_date",
                "region", "topic", "source_type", "category", "status", "confidentiality", "notes", "tags",
            ):
                if field in base_entry:
                    record[field] = base_entry[field]
        for field in EDITABLE_FIELDS:
            if field in payload:
                record[field] = payload[field]
        record["updated_at"] = _now()
        record["version"] = actual + 1
        record["deleted_at"] = ""
        record = _validate_record(record)
        if existing:
            catalog["records"][catalog["records"].index(existing)] = record
        else:
            if len(catalog["records"]) >= MAX_RECORDS:
                raise LibraryStoreError("CATALOG_FULL", "资料库索引已达到记录上限")
            catalog["records"].append(record)
        catalog["updated_at"] = record["updated_at"]
        _atomic_write(project_root, catalog)
        return record


def archive_record(project_root: Path | str, base_entry: Mapping[str, Any], expected_version: int) -> dict[str, Any]:
    identity = _safe_id(base_entry.get("library_id"), "资料编号", required=True)
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 0:
        raise LibraryStoreError("INVALID_INPUT", "资料版本无效")
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        existing = next((item for item in catalog["records"] if item.get("library_id") == identity), None)
        actual = int(existing.get("version") or 0) if existing else 0
        if actual != expected_version:
            raise LibraryStoreError("VERSION_CONFLICT", "资料信息已变化，请刷新后重试")
        if existing:
            record = dict(existing)
        else:
            record = _base_record(str(base_entry.get("kind") or "source"), identity, str(base_entry.get("title") or "未命名资料"), created_at=str(base_entry.get("created_at") or ""))
            for field in ("source_id", "path", "url", "project_id", "account_id", "opportunity_id", "bid_id", "category", "status"):
                if field in base_entry:
                    record[field] = base_entry[field]
        timestamp = _now()
        record.update({"deleted_at": timestamp, "updated_at": timestamp, "version": actual + 1})
        record = _validate_record(record)
        if existing:
            catalog["records"][catalog["records"].index(existing)] = record
        else:
            catalog["records"].append(record)
        catalog["updated_at"] = timestamp
        _atomic_write(project_root, catalog)
        return record


def restore_record(project_root: Path | str, library_id: str, expected_version: int) -> dict[str, Any]:
    identity = _safe_id(library_id, "资料编号", required=True)
    with _CATALOG_LOCK:
        catalog = _read_catalog(project_root)
        record = next((item for item in catalog["records"] if item.get("library_id") == identity), None)
        if record is None or not record.get("deleted_at"):
            raise LibraryStoreError("NOT_FOUND", "回收站中的资料不存在")
        if record.get("version") != expected_version:
            raise LibraryStoreError("VERSION_CONFLICT", "资料信息已变化，请刷新后重试")
        timestamp = _now()
        record.update({"deleted_at": "", "updated_at": timestamp, "version": expected_version + 1})
        record = _validate_record(record)
        catalog["updated_at"] = timestamp
        _atomic_write(project_root, catalog)
        return record


def _source_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    reference = str(row.get("source_id") or row.get("url") or row.get("title") or "")
    identity = stable_library_id("source", reference)
    return {
        **_base_record("source", identity, str(row.get("title") or "未命名来源"), created_at=str(row.get("accessed_date") or row.get("published_date") or "")),
        **{key: row.get(key, "") for key in (
            "source_id", "url", "publisher", "published_date", "accessed_date", "region", "topic",
            "source_type", "quality", "exposure_status", "key_facts", "important_quotes", "interpretation",
            "limitations", "notes", "status",
        )},
        "category": infer_category(row.get("source_type"), row.get("topic"), row.get("title")),
        "record_version": row.get("_record_version") or "",
        "managed_scope": "source",
    }


def _file_entry(row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    path = str(row.get("path") or "")
    title = str(row.get("name") or Path(path).name or "未命名文件")
    identity = stable_library_id(kind, path)
    return {
        **_base_record(kind, identity, title, created_at=str(row.get("modified_at") or "")),
        "path": path,
        "project_id": str(row.get("project_id") or ""),
        "size": row.get("size") or 0,
        "modified_at": str(row.get("modified_at") or ""),
        "record_version": str(row.get("version") or ""),
        "category": infer_category(title, path),
        "managed_scope": "project" if kind == "project_file" else "output",
    }


def _overlay(base: Mapping[str, Any], record: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(base)
    if record:
        preserved = {key: value for key, value in result.items() if key in {"key_facts", "important_quotes", "interpretation", "limitations", "quality", "exposure_status", "record_version", "modified_at", "managed_scope"}}
        result.update(record)
        for key, value in preserved.items():
            if not result.get(key):
                result[key] = value
        result["catalog_version"] = int(record.get("version") or 0)
        result["is_cataloged"] = True
    else:
        result["catalog_version"] = 0
        result["is_cataloged"] = False
    result["tags"] = list(result.get("tags") or [])
    result["versions"] = list(result.get("versions") or [])
    return result


def build_library_snapshot(
    project_root: Path | str,
    *,
    sources: Iterable[Mapping[str, Any]],
    project_files: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    catalog = _read_catalog(project_root)
    catalog_by_id = {str(record["library_id"]): record for record in catalog["records"]}
    entries: list[dict[str, Any]] = []
    represented: set[str] = set()
    for row in sources:
        base = _source_entry(row)
        represented.add(base["library_id"])
        entries.append(_overlay(base, catalog_by_id.get(base["library_id"])))
    for row in project_files:
        base = _file_entry(row, kind="project_file")
        represented.add(base["library_id"])
        entries.append(_overlay(base, catalog_by_id.get(base["library_id"])))
    for row in artifacts:
        base = _file_entry(row, kind="artifact")
        represented.add(base["library_id"])
        entries.append(_overlay(base, catalog_by_id.get(base["library_id"])))
    for record in catalog["records"]:
        if record["library_id"] not in represented:
            standalone = _overlay(record, record)
            standalone.setdefault("managed_scope", "library" if record.get("kind") == "library_file" else "url")
            entries.append(standalone)

    active = [entry for entry in entries if not entry.get("deleted_at")]
    trash = [entry for entry in entries if entry.get("deleted_at")]
    active.sort(key=lambda item: (str(item.get("updated_at") or item.get("modified_at") or item.get("accessed_date") or ""), str(item.get("library_id") or "")), reverse=True)
    trash.sort(key=lambda item: str(item.get("deleted_at") or ""), reverse=True)
    now = datetime.now(timezone.utc)
    review_horizon = now + timedelta(days=30)
    week_start = now - timedelta(days=7)

    def parse_time(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    category_counts = {category: sum(entry.get("category") == category for entry in active) for category in CATEGORIES}
    stats = {
        "total": len(active),
        "pending": sum(entry.get("status") == "pending" or entry.get("category") == "inbox" for entry in active),
        "verified": sum(entry.get("status") == "verified" for entry in active),
        "review_due": sum(
            bool(moment := parse_time(entry.get("review_at"))) and moment <= review_horizon
            for entry in active
        ),
        "this_week": sum(
            bool(moment := parse_time(entry.get("created_at") or entry.get("modified_at") or entry.get("accessed_date"))) and moment >= week_start
            for entry in active
        ),
        "trash": len(trash),
        "categories": category_counts,
    }
    revision_material = [
        catalog_revision(project_root),
        *[f"{entry.get('library_id')}:{entry.get('catalog_version')}:{entry.get('record_version')}:{entry.get('deleted_at')}" for entry in entries],
    ]
    revision = hashlib.sha256("\n".join(revision_material).encode("utf-8")).hexdigest()
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "version": revision,
        "entries": active,
        "trash": trash,
        "stats": stats,
        "categories": list(CATEGORIES),
        "truncated": len(entries) > MAX_RECORDS,
    }


def find_entry(snapshot: Mapping[str, Any], library_id: str, *, include_trash: bool = False) -> dict[str, Any]:
    identity = _safe_id(library_id, "资料编号", required=True)
    pools = [snapshot.get("entries", [])]
    if include_trash:
        pools.append(snapshot.get("trash", []))
    matches = [entry for pool in pools if isinstance(pool, list) for entry in pool if isinstance(entry, dict) and entry.get("library_id") == identity]
    if len(matches) != 1:
        raise LibraryStoreError("NOT_FOUND", "资料不存在或已发生变化")
    return matches[0]
