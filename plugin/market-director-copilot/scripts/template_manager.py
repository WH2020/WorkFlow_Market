from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def load_catalog(project: Path) -> tuple[Path, dict]:
    path = project / "library" / "templates" / "template-catalog.json"
    if not path.exists():
        raise SystemExit(f"Template catalog not found: {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def save_catalog(path: Path, catalog: dict) -> None:
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="List, activate, or import market presentation templates.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    activate = sub.add_parser("activate")
    activate.add_argument("template_id")
    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("--id", required=True, dest="template_id")
    import_cmd.add_argument("--name", required=True)
    import_cmd.add_argument("--kind", required=True, choices=["weekly", "government", "customer", "research", "sales"])
    import_cmd.add_argument("--file", required=True, type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    catalog_path, catalog = load_catalog(project)
    templates = catalog.get("templates", [])

    if args.command == "list":
        print(json.dumps({"active": catalog.get("active"), "templates": templates}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "activate":
        if args.template_id not in {item["id"] for item in templates}:
            raise SystemExit(f"Unknown template: {args.template_id}")
        catalog["active"] = args.template_id
        save_catalog(catalog_path, catalog)
        print(f"Active template: {args.template_id}")
        return 0

    source = args.file.resolve()
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise SystemExit("Imported template must be an existing .pptx file")
    if args.template_id in {item["id"] for item in templates}:
        raise SystemExit("Template id already exists; choose a new id so built-in templates are preserved")
    destination = catalog_path.parent / f"{args.template_id}.pptx"
    shutil.copy2(source, destination)
    templates.append({
        "id": args.template_id,
        "name": args.name,
        "file": destination.name,
        "kind": args.kind,
        "editable": True,
        "builtin": False,
    })
    catalog["active"] = args.template_id
    save_catalog(catalog_path, catalog)
    print(json.dumps({"imported": str(destination), "active": args.template_id}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
