"""Build the cumulative 0.20.2 update-button + Codex catalog program bundle.

Reuses the hash-pinned, closed-allowlist bundle builder. Neither the installed
application nor previously generated release artifacts are read or modified.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("app_updates_hotfix_base", ROOT / "scripts/build-windows-cli-catalog-hotfix.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
BUILDER.HOTFIX_ID = "0.20.2-app-updates-20260910"
BUILDER.NOTES_PATH = ROOT / "desktop/windows-app-updates-hotfix-notes.md"
BUILDER.PATCH_FILES += ("agent_platform/app_updates.py", "ui/styles.css")
BUILDER.EXPECTED_SOURCE_SHA256 = "f78084882a2501314e50bda2f0efdc93989bf68db50d5fa505dc4a1b37614204"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    result = BUILDER.build(arguments.output, arguments.manifest)
    print(json.dumps({"status": "built", "hotfix_id": result["hotfix_id"], "files": len(result["changes"]),
                      "output": str(arguments.output), "installed_application_modified": False}))
