from __future__ import annotations

import argparse
import shutil
from pathlib import Path


TEMPLATES = {
    "data/knowledge/source-register.example.csv": "data/knowledge/source-register.csv",
    "data/sales/activities.example.csv": "data/sales/activities.csv",
    "data/sales/customers.example.csv": "data/sales/customers.csv",
    "data/sales/resource-requests.example.csv": "data/sales/resource-requests.csv",
    "data/sales/sales-assets.example.csv": "data/sales/sales-assets.csv",
    "data/sales/salespeople.example.json": "data/sales/salespeople.json",
}


def initialize(project: Path) -> tuple[list[Path], list[Path]]:
    created: list[Path] = []
    skipped: list[Path] = []

    missing = [project / source_name for source_name in TEMPLATES if not (project / source_name).is_file()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing templates: {formatted}")

    for source_name, target_name in TEMPLATES.items():
        source = project / source_name
        target = project / target_name
        if target.exists():
            skipped.append(target)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            created.append(target)
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise

    return created, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize local market-director data without overwriting existing files."
    )
    parser.add_argument("--project", default=".", help="Project root directory")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    created, skipped = initialize(project)
    for path in created:
        print(f"CREATED {path.relative_to(project)}")
    for path in skipped:
        print(f"SKIPPED {path.relative_to(project)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
