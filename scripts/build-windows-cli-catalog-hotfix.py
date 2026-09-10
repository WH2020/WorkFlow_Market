"""Build a program-only Codex catalog hotfix; never open an installed app's data.

The original installer/payload and existing portable directory are not changed.
Applying the bundle is a separate, app-closed operation with program backups.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HOTFIX_ID = "0.20.2-codex-catalog-20260910"
NOTES_PATH = ROOT / "desktop/windows-cli-catalog-hotfix-notes.md"
# Frozen from the reviewed original receipt (ordered path:sha256 lines).
# A hotfix ID cannot silently acquire newer sources or missing dependencies.
EXPECTED_SOURCE_SHA256 = "ada09bae5fa8ca749864aa338ca2ebc1e8d8f04b3c4b405d039d43e7d868f68f"
PATCH_FILES = (
    "agent_platform/cli_model_catalog.py", "agent_platform/cli_process_host.py",
    "agent_platform/cli_provider.py", "agent_platform/model_provider.py", "agent_platform/model_registry.py",
    "pi/extensions/cli-model-provider.ts", "pi/extensions/model-selection.ts", "pi/extensions/task-runtime.ts",
    "pi/extensions/subagent-contracts.ts", "pi/extensions/subagent-model-guard.ts", "pi/extensions/vertical-workflow.ts",
    "ui/app.js", "ui/index.html", "ui/server.py", "README.md",
)
SPEC = importlib.util.spec_from_file_location("cli_hotfix_base", ROOT / "scripts/build-windows-portable.py")
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def archive_path(output: Path) -> Path:
    return output.with_name(output.name + ".zip")


def build(output: Path, release_manifest: Path) -> dict:
    if output.exists() or output.is_symlink() or archive_path(output).exists():
        raise FileExistsError("Hotfix output already exists; it was not overwritten")
    _content, original = BASE.read_manifest(release_manifest)
    records = {row["path"]: dict(row) for row in original}
    changes, inputs = [], {}
    for relative in PATCH_FILES:
        BASE.POLICY.safe_relative(relative)
        source = ROOT / relative
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 4 * 1024 * 1024:
            raise ValueError("Invalid hotfix source file")
        content = source.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        before = records.get(relative, {}).get("sha256")
        if before == sha:
            raise ValueError("Hotfix allowlist contains an unchanged file")
        records[relative] = {"path": relative, "bytes": len(content), "sha256": sha}
        changes.append({**records[relative], "previous_sha256": before})
        inputs[f"program/{relative}"] = content
    fingerprint = hashlib.sha256("".join(row["path"] + ":" + row["sha256"] + "\n" for row in changes).encode("utf-8")).hexdigest()
    if fingerprint != EXPECTED_SOURCE_SHA256:
        raise ValueError("Hotfix source fingerprint has changed; use the reviewed cumulative app-updates builder or a newly reviewed release. No output was created.")
    rows = sorted(records.values(), key=lambda row: row["path"])
    updated = encoded({"version": BASE.VERSION, "files": rows, "total_bytes": sum(row["bytes"] for row in rows)})
    inputs["program/runtime/install-manifest.json"] = updated
    changes.append({"path": "runtime/install-manifest.json", "bytes": len(updated),
                    "sha256": hashlib.sha256(updated).hexdigest(), "previous_sha256": BASE.RELEASE_MANIFEST_SHA256})
    receipt = {"hotfix_id": HOTFIX_ID, "version": BASE.VERSION, "kind": "program-only-hotfix",
               "required_portable_name": "Agent4Market-0.20.2-portable", "requires_closed_application": True,
               "base_manifest_sha256": BASE.RELEASE_MANIFEST_SHA256, "changes": changes,
               "existing_config_or_business_data_read": False, "installed_application_modified": False}
    inputs["hotfix-manifest.json"] = encoded(receipt)
    inputs["热修复说明.md"] = NOTES_PATH.read_bytes()
    output.mkdir(parents=True)
    for relative, content in inputs.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(content)
    with zipfile.ZipFile(archive_path(output), "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(inputs):
            archive.write(output / relative, relative)
    for change in changes:
        if BASE.POLICY.digest(output / "program" / change["path"]) != change["sha256"]:
            raise ValueError("Hotfix bundle hash mismatch")
    with zipfile.ZipFile(archive_path(output)) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(inputs):
            raise ValueError("Hotfix archive verification failed")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    result = build(arguments.output, arguments.manifest)
    print(json.dumps({"status": "built", "hotfix_id": HOTFIX_ID, "files": len(result["changes"]),
                      "output": str(arguments.output), "installed_application_modified": False}))
