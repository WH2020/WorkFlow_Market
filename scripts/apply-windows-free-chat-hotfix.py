"""Freeze or apply the reviewed free-chat addition to the closed 0.20.2 portable app.

Default is read-only preflight. --build only creates a new immutable program
archive. --apply uses the pinned private/locked/rollback transaction engine.
No installed configuration, business files or CLI login files are inspected.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

if not __debug__ or sys.flags.optimize:
    raise RuntimeError("Hotfix deployment requires an unoptimized Python interpreter")

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "scripts/apply-windows-a4-json-hotfix.py"
BASE_SHA256 = "a2f2e3a923dc32f017d34a2a5089d528666f6d070ec001d825d8bf8e3ef0375f"
if hashlib.sha256(BASE_PATH.read_bytes()).hexdigest() != BASE_SHA256:
    raise RuntimeError("The reviewed baseline reconstruction has changed")
SPEC = importlib.util.spec_from_file_location("free_chat_hotfix_baseline", BASE_PATH)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
ENGINE = BASE.ENGINE  # Baseline module also checks the frozen engine's SHA-256.

HOTFIX_ID = "0.20.2-free-chat-20260910"
SOURCE_HASHES = {
    "ui/app.js": "13339c1b940fedc219ca1951011162105cec65b4b149d14b3cab3d66919c5c00",
    "ui/index.html": "e4cb29aa35d4070643ca12f967898fbcf36ef036bbc8a9d9b9047d8a44c41756",
    "ui/server.py": "c5dfbb7b147904a976de3c384d5dd5c395179ebe78695075307feada81ede768",
    "ui/free-chat.js": "4e33b30ead8fced4382c8cb8e39c235c5a9aaa162f7b500e3bda4640a6be1672",
    "ui/free-chat.css": "e5bd3f9a4064e190e72aa7d07150ed168c92dbd6896dfae2cf37844241e7da14",
    "agent_platform/free_chat.py": "9c7188bc72b90f560a9f54035ba33e69563faaa5a403fee8a588ecfdab711f9b",
    "pi/extensions/free-chat.ts": "76d502db715c887894fc51b443c39cc68e0d52cef3f289c4251a8a022a4a8d72",
}
PROGRAM_FILES = ("ui/free-chat.js", "ui/free-chat.css", "agent_platform/free_chat.py", "pi/extensions/free-chat.ts",
                 "ui/app.js", "ui/index.html", "ui/server.py", "runtime/install-manifest.json")
NEW_FILES = frozenset({"ui/free-chat.js", "ui/free-chat.css", "agent_platform/free_chat.py", "pi/extensions/free-chat.ts"})
ARCHIVE = ROOT / "outputs/releases/0.20.2-free-chat-hotfix-20260910-r2.zip"
ARCHIVE_SHA256 = "7e5c9ec2a30d7d608813063ec44563940122f1eeecf9b1b89867bc4528617860"
ENGINE.HOTFIX_ID, ENGINE.PROGRAM_FILES, ENGINE.NEW_FILES = HOTFIX_ID, PROGRAM_FILES, NEW_FILES
ENGINE.BACKUP_NAME = "hotfix-backup-" + HOTFIX_ID
ENGINE.RECEIPT_NAME = "hotfix-" + HOTFIX_ID + ".json"
ENGINE.LOCK_NAME = "hotfix-" + HOTFIX_ID + ".lock"


def plan(program: dict[str, bytes]) -> tuple[dict, dict[str, bytes]]:
    if set(program) != set(SOURCE_HASHES):
        raise ValueError("Unexpected program file set")
    for path, digest in SOURCE_HASHES.items():
        if ENGINE.sha(program[path]) != digest:
            raise ValueError("Program differs from the verified source: " + path)
    _baseline, baseline_contents = BASE.load_patch()
    old_inventory = baseline_contents[ENGINE.MANIFEST_PATH]
    inventory = copy.deepcopy(json.loads(old_inventory))
    records = {row["path"]: row for row in inventory["files"]}
    previous = {}
    for path in SOURCE_HASHES:
        before = records.get(path)
        if (before is None) != (path in NEW_FILES):
            raise ValueError("Unexpected baseline inventory entry: " + path)
        previous[path] = before["sha256"] if before else None
        after = {"path": path, "bytes": len(program[path]), "sha256": ENGINE.sha(program[path])}
        if before:
            before.update(after)
        else:
            inventory["files"].append(after)
    inventory["total_bytes"] = sum(row["bytes"] for row in inventory["files"])
    contents = {**program, ENGINE.MANIFEST_PATH: ENGINE.encoded(inventory)}
    previous[ENGINE.MANIFEST_PATH] = ENGINE.sha(old_inventory)
    manifest = {"hotfix_id": HOTFIX_ID, "version": "0.20.2", "program_only": True, "requires_closed_application": True,
        "changes": [{"path": path, "bytes": len(contents[path]), "sha256": ENGINE.sha(contents[path]), "previous_sha256": previous[path]} for path in PROGRAM_FILES]}
    return manifest, contents


def build() -> dict:
    program = {}
    for path in SOURCE_HASHES:
        source = ROOT / path
        ENGINE.regular(source)
        program[path] = source.read_bytes()
    manifest, contents = plan(program)
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patch.json", ENGINE.encoded(manifest))
        for path, content in contents.items():
            archive.writestr("program/" + path, content)
    return {"status": "built", "archive": str(ARCHIVE), "sha256": ENGINE.sha(ARCHIVE.read_bytes()), "bytes": ARCHIVE.stat().st_size,
            "program_files": len(contents), "installed_application_modified": False}


def load_patch() -> tuple[dict, dict[str, bytes]]:
    ENGINE.regular(ARCHIVE)
    if ARCHIVE.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Patch archive exceeds its size limit")
    data = ARCHIVE.read_bytes()
    if ENGINE.sha(data) != ARCHIVE_SHA256:
        raise ValueError("Frozen free-chat archive hash differs")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = ["patch.json", *("program/" + path for path in PROGRAM_FILES)]
        if sorted(archive.namelist()) != sorted(names) or any(info.file_size > 32 * 1024 * 1024 for info in archive.infolist()):
            raise ValueError("Unexpected archive members")
        manifest = json.loads(archive.read("patch.json"))
        contents = {path: archive.read("program/" + path) for path in PROGRAM_FILES}
    expected_manifest, expected_contents = plan({path: contents[path] for path in SOURCE_HASHES})
    if manifest != expected_manifest or contents != expected_contents:
        raise ValueError("Frozen patch differs from the reviewed plan")
    return manifest, contents


def run(*, apply=False, recover=False) -> dict:
    target = ENGINE.POLICY.profile_path() / ENGINE.TARGET_NAME
    ENGINE.validate_target(target)
    manifest, contents = load_patch()
    ENGINE.reject_running_processes(target)
    if recover:
        original = ENGINE.read_program(target / "runtime" / ENGINE.BACKUP_NAME / "before" / ENGINE.MANIFEST_PATH)
        if ENGINE.sha(original) != BASE.NEW_MANIFEST_SHA256:
            raise ValueError("Recovery baseline differs from this fixed patch")
        records = {row["path"]: row for row in json.loads(original)["files"]}
        hashes = {path: records[path]["sha256"] for path in ENGINE.LAUNCH_FILES}
        with ENGINE.locked_program(target, hashes), ENGINE.deployment_lock(target):
            return ENGINE.recover_transaction(target, manifest)
    _before, hashes = ENGINE.preflight(target, manifest)
    with ENGINE.locked_program(target, hashes):
        before, _hashes = ENGINE.preflight(target, manifest)
        if not apply:
            return {"status": "ready", "hotfix_id": HOTFIX_ID, "target": str(target), "program_changes": list(PROGRAM_FILES), "application_modified": False}
        with ENGINE.deployment_lock(target):
            return ENGINE.apply_transaction(target, manifest, contents, before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--build", action="store_true")
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--recover", action="store_true")
    arguments = parser.parse_args()
    try:
        print(json.dumps(build() if arguments.build else run(apply=arguments.apply, recover=arguments.recover), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"status": "stopped", "error": str(error), "error_type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
