from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


CATALOG_RELATIVE = Path("library/templates/office-template-catalog.json")
LEGACY_CATALOG_RELATIVE = Path("library/templates/template-catalog.json")
COMPANY_ROOT_RELATIVE = Path("library/templates/company")
DOCUMENT_EXTENSIONS = {
    "word": {".docx", ".dotx"},
    "excel": {".xlsx", ".xltx"},
    "powerpoint": {".pptx", ".potx"},
}
REQUIRED_PART = {
    "word": "word/document.xml",
    "excel": "xl/workbook.xml",
    "powerpoint": "ppt/presentation.xml",
}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_slug(value: str, label: str) -> str:
    if len(value) > 64 or not ID_PATTERN.fullmatch(value):
        raise SystemExit(f"{label} must be lower-case hyphen-case and at most 64 characters: {value}")
    return value


def empty_catalog() -> dict:
    return {
        "schema_version": 1,
        "active_by_type": {"word": None, "excel": None, "powerpoint": None},
        "active_by_kind": {"word": {}, "excel": {}, "powerpoint": {}},
        "templates": [],
    }


def load_catalog(project: Path, create: bool = True) -> tuple[Path, dict]:
    path = project / CATALOG_RELATIVE
    if not path.exists():
        if not create:
            return path, empty_catalog()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_catalog(path, empty_catalog())
    catalog = json.loads(path.read_text(encoding="utf-8"))
    original_state = json.dumps(catalog, ensure_ascii=False, sort_keys=True)
    catalog.setdefault("schema_version", 1)
    catalog.setdefault("active_by_type", {"word": None, "excel": None, "powerpoint": None})
    catalog.setdefault("templates", [])
    active_by_kind = catalog.setdefault("active_by_kind", {})
    if any(isinstance(value, str) for value in active_by_kind.values()):
        migrated = {"word": {}, "excel": {}, "powerpoint": {}}
        by_id = {item.get("id"): item for item in catalog["templates"]}
        for kind, template_id in active_by_kind.items():
            item = by_id.get(template_id)
            if item and item.get("document_type") in migrated:
                migrated[item["document_type"]][kind] = template_id
        catalog["active_by_kind"] = migrated
    else:
        for document_type in DOCUMENT_EXTENSIONS:
            active_by_kind.setdefault(document_type, {})
    if json.dumps(catalog, ensure_ascii=False, sort_keys=True) != original_state:
        save_catalog(path, catalog)
    return path, catalog


def save_catalog(path: Path, catalog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def template_by_id(catalog: dict, template_id: str) -> dict:
    for item in catalog["templates"]:
        if item["id"] == template_id:
            return item
    raise SystemExit(f"Unknown office template: {template_id}")


def infer_document_type(path: Path) -> str:
    extension = path.suffix.lower()
    matches = [name for name, extensions in DOCUMENT_EXTENSIONS.items() if extension in extensions]
    if not matches:
        allowed = sorted(ext for values in DOCUMENT_EXTENSIONS.values() for ext in values)
        raise SystemExit(f"Unsupported or macro-enabled template format: {extension}. Allowed: {', '.join(allowed)}")
    return matches[0]


def validate_ooxml(path: Path, document_type: str | None = None) -> str:
    if not path.is_file():
        raise SystemExit(f"Template file not found: {path}")
    inferred_type = infer_document_type(path)
    if document_type and document_type != inferred_type:
        raise SystemExit(
            f"Template type mismatch: --type {document_type}, extension {path.suffix.lower()} is {inferred_type}"
        )
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"Template is not a valid OOXML package: {path}") from exc
    required = {"[Content_Types].xml", REQUIRED_PART[inferred_type]}
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"Template package is missing required parts: {', '.join(missing)}")
    return inferred_type


def version_record(path: Path, templates_root: Path, version: int, original_name: str) -> dict:
    return {
        "version": version,
        "file": path.relative_to(templates_root).as_posix(),
        "original_name": original_name,
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
        "imported_at": utc_now(),
    }


def copy_version(
    source: Path,
    project: Path,
    document_type: str,
    template_id: str,
    version: int,
) -> Path:
    destination_dir = project / COMPANY_ROOT_RELATIVE / document_type / template_id / f"v{version:03d}"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"template{source.suffix.lower()}"
    if destination.exists():
        raise SystemExit(f"Template version already exists: {destination}")
    shutil.copy2(source, destination)
    return destination


def set_active(catalog: dict, item: dict, scope: str) -> None:
    if scope in {"type", "both"}:
        catalog["active_by_type"][item["document_type"]] = item["id"]
    if scope in {"kind", "both"}:
        catalog["active_by_kind"][item["document_type"]][item["kind"]] = item["id"]


def resolve_template(project: Path, catalog: dict, document_type: str, kind: str, template_id: str | None) -> dict:
    selected = None
    reason = None
    if template_id:
        selected = template_by_id(catalog, template_id)
        reason = "explicit-template-id"
    elif catalog["active_by_kind"][document_type].get(kind):
        selected = template_by_id(catalog, catalog["active_by_kind"][document_type][kind])
        reason = "active-by-kind"
    elif catalog["active_by_type"].get(document_type):
        selected = template_by_id(catalog, catalog["active_by_type"][document_type])
        reason = "active-by-type"

    if selected:
        if selected["document_type"] != document_type:
            raise SystemExit(
                f"Selected template {selected['id']} is {selected['document_type']}, not requested {document_type}"
            )
        path = (project / "library" / "templates" / selected["file"]).resolve()
        validate_ooxml(path, document_type)
        return {
            "template_id": selected["id"],
            "version": selected["version"],
            "document_type": document_type,
            "kind": kind,
            "file": str(path),
            "source": "company-template-library",
            "reason": reason,
        }

    if document_type == "powerpoint":
        legacy_path = project / LEGACY_CATALOG_RELATIVE
        if legacy_path.exists():
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy_templates = legacy.get("templates", [])
            legacy_selected = next((item for item in legacy_templates if item.get("kind") == kind), None)
            if legacy_selected is None:
                active_id = legacy.get("active")
                legacy_selected = next((item for item in legacy_templates if item.get("id") == active_id), None)
            if legacy_selected:
                path = (legacy_path.parent / legacy_selected["file"]).resolve()
                validate_ooxml(path, "powerpoint")
                return {
                    "template_id": legacy_selected["id"],
                    "version": 1,
                    "document_type": document_type,
                    "kind": kind,
                    "file": str(path),
                    "source": "built-in-presentation-library",
                    "reason": "powerpoint-fallback",
                }

    return {
        "template_id": None,
        "version": None,
        "document_type": document_type,
        "kind": kind,
        "file": None,
        "source": "standard-blank",
        "reason": "no-matching-template",
    }


def validate_catalog(project: Path, catalog: dict) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    templates_root = project / "library" / "templates"
    for item in catalog["templates"]:
        template_id = item.get("id", "")
        if template_id in ids:
            errors.append(f"duplicate template id: {template_id}")
        ids.add(template_id)
        try:
            validate_slug(template_id, "template id")
            path = templates_root / item["file"]
            validate_ooxml(path, item["document_type"])
            if sha256(path) != item["sha256"]:
                errors.append(f"hash mismatch: {template_id}")
            if path.stat().st_size != item["size_bytes"]:
                errors.append(f"size mismatch: {template_id}")
        except (KeyError, SystemExit, OSError) as exc:
            errors.append(f"{template_id or '<missing-id>'}: {exc}")

    for document_type, template_id in catalog["active_by_type"].items():
        if template_id and template_id not in ids:
            errors.append(f"unknown active_by_type {document_type}: {template_id}")
    for document_type, kinds in catalog["active_by_kind"].items():
        if document_type not in DOCUMENT_EXTENSIONS or not isinstance(kinds, dict):
            errors.append(f"invalid active_by_kind group: {document_type}")
            continue
        for kind, template_id in kinds.items():
            if template_id and template_id not in ids:
                errors.append(f"unknown active_by_kind {document_type}/{kind}: {template_id}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local company Word, Excel, and PowerPoint templates.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    list_cmd = commands.add_parser("list")
    list_cmd.add_argument("--type", choices=sorted(DOCUMENT_EXTENSIONS), dest="document_type")
    list_cmd.add_argument("--kind")

    import_cmd = commands.add_parser("import")
    import_cmd.add_argument("--id", required=True, dest="template_id")
    import_cmd.add_argument("--name", required=True)
    import_cmd.add_argument("--type", required=True, choices=sorted(DOCUMENT_EXTENSIONS), dest="document_type")
    import_cmd.add_argument("--kind", required=True)
    import_cmd.add_argument("--file", required=True, type=Path)
    import_cmd.add_argument("--activate", action="store_true")
    import_cmd.add_argument("--active-scope", choices=["type", "kind", "both"], default="both")

    replace_cmd = commands.add_parser("replace")
    replace_cmd.add_argument("template_id")
    replace_cmd.add_argument("--file", required=True, type=Path)

    activate_cmd = commands.add_parser("activate")
    activate_cmd.add_argument("template_id")
    activate_cmd.add_argument("--scope", choices=["type", "kind", "both"], default="both")

    resolve_cmd = commands.add_parser("resolve")
    resolve_cmd.add_argument("--type", required=True, choices=sorted(DOCUMENT_EXTENSIONS), dest="document_type")
    resolve_cmd.add_argument("--kind", default="generic")
    resolve_cmd.add_argument("--id", dest="template_id")

    commands.add_parser("validate")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project = args.project.resolve()
    catalog_path, catalog = load_catalog(project)
    templates_root = project / "library" / "templates"

    if args.command == "list":
        templates = catalog["templates"]
        if args.document_type:
            templates = [item for item in templates if item["document_type"] == args.document_type]
        if args.kind:
            templates = [item for item in templates if item["kind"] == args.kind]
        print(
            json.dumps(
                {
                    "active_by_type": catalog["active_by_type"],
                    "active_by_kind": catalog["active_by_kind"],
                    "templates": templates,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "import":
        template_id = validate_slug(args.template_id, "template id")
        kind = validate_slug(args.kind, "kind")
        if any(item["id"] == template_id for item in catalog["templates"]):
            raise SystemExit("Template id already exists; use replace to create a new version")
        source = args.file.resolve()
        validate_ooxml(source, args.document_type)
        destination = copy_version(source, project, args.document_type, template_id, 1)
        record = version_record(destination, templates_root, 1, source.name)
        item = {
            "id": template_id,
            "name": args.name,
            "document_type": args.document_type,
            "kind": kind,
            "version": 1,
            "file": record["file"],
            "original_name": record["original_name"],
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "editable": True,
            "builtin": False,
            "created_at": record["imported_at"],
            "updated_at": record["imported_at"],
            "versions": [record],
        }
        catalog["templates"].append(item)
        if args.activate:
            set_active(catalog, item, args.active_scope)
        save_catalog(catalog_path, catalog)
        print(json.dumps({"imported": item, "active": args.activate}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "replace":
        item = template_by_id(catalog, args.template_id)
        if item.get("builtin"):
            raise SystemExit("Built-in templates cannot be replaced")
        source = args.file.resolve()
        validate_ooxml(source, item["document_type"])
        version = int(item["version"]) + 1
        destination = copy_version(source, project, item["document_type"], item["id"], version)
        record = version_record(destination, templates_root, version, source.name)
        item.update(
            {
                "version": version,
                "file": record["file"],
                "original_name": record["original_name"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
                "updated_at": record["imported_at"],
            }
        )
        item.setdefault("versions", []).append(record)
        save_catalog(catalog_path, catalog)
        print(json.dumps({"replaced": item["id"], "version": version, "file": item["file"]}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "activate":
        item = template_by_id(catalog, args.template_id)
        set_active(catalog, item, args.scope)
        save_catalog(catalog_path, catalog)
        print(json.dumps({"active": item["id"], "scope": args.scope}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "resolve":
        print(
            json.dumps(
                resolve_template(project, catalog, args.document_type, validate_slug(args.kind, "kind"), args.template_id),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    errors = validate_catalog(project, catalog)
    print(json.dumps({"valid": not errors, "template_count": len(catalog["templates"]), "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
