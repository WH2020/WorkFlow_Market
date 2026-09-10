"""Apply a fixed two-file JSON-request fix to the closed 0.20.2 portable app.

Uses the previously reviewed transaction/ACL/lock/rollback implementation.
Reconstructs the exact reviewed frontend from the immutable earlier bundle;
does not modify older artifacts or read configuration/business/CLI login data.
"""
from __future__ import annotations

import argparse
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
ENGINE_PATH = ROOT / "scripts/apply-windows-app-updates-hotfix.py"
ENGINE_SHA256 = "25ee4738ef4b533a0d2dc4fab80061983b723c38da2bda8ed12881d7170f843f"
if hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest() != ENGINE_SHA256:
    raise RuntimeError("The reviewed deployment engine has changed")
SPEC = importlib.util.spec_from_file_location("a4_json_deployment_engine", ENGINE_PATH)
ENGINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENGINE)

HOTFIX_ID = "0.20.2-a4-json-20260910"
OLD_APP_SHA256 = "837479d4c95614cd012d817b59a701b4f7d8c6ff2f31c7fef3d80ec126518cbe"
NEW_APP_SHA256 = "6c0be9705d5eca3362871ab57415e4465bf64edb6c1a8b5e362d3a47236f1347"
OLD_MANIFEST_SHA256 = "bfd6907414601fd6b57d95ee240c6902ffbb9d78253fb3f5558f743034acc0de"
NEW_MANIFEST_SHA256 = "a1c95121597a697e13f18d188ddc0d10c4fe9ff2b33596ed014e22e71dac8a14"
SOURCE_ARCHIVE = ROOT / "outputs/releases/0.20.2-app-updates-hotfix-20260910.zip"
SOURCE_ARCHIVE_SHA256 = "d3a494149cb17b2351e07e9527009ef6f0ead446bc0f072a619092d32da7c861"
PROGRAM_FILES = ("ui/app.js", "runtime/install-manifest.json")
ENGINE.HOTFIX_ID = HOTFIX_ID
ENGINE.PROGRAM_FILES = PROGRAM_FILES
ENGINE.NEW_FILES = frozenset()
ENGINE.BACKUP_NAME = "hotfix-backup-" + HOTFIX_ID
ENGINE.RECEIPT_NAME = "hotfix-" + HOTFIX_ID + ".json"
ENGINE.LOCK_NAME = "hotfix-" + HOTFIX_ID + ".lock"


def reviewed_frontend(original: bytes) -> bytes:
    if ENGINE.sha(original) != OLD_APP_SHA256:
        raise ValueError("Frontend does not match the reviewed original")
    changed = original
    for route, argument in (("recommendations/accept", "body"), ("recommendations/ignore", "body"), ("evaluate-signals", "accountData")):
        old = f'api("/api/a4/{route}", {{ method: "POST", body: JSON.stringify({argument}) }})'.encode()
        new = old.replace(b'method: "POST", body:', b'method: "POST", headers: { "Content-Type": "application/json" }, body:')
        if changed.count(old) != 1:
            raise ValueError("Expected exactly one reviewed JSON request")
        changed = changed.replace(old, new, 1)
    edits = [
        (b'async function loadCustomerSignals(accountId)', b'async function loadCustomerSignals(selectedAccountId)'),
        (b'if (!accountId || customerState.signalsLoading)', b'if (!selectedAccountId || customerState.signalsLoading)'),
        (b'customerState.rows.find((row) => accountId(row) === accountId)', b'customerState.rows.find((row) => accountId(row) === selectedAccountId)'),
        (b'api("/api/a4/match-play", {\n        method: "POST",\n',
         b'api("/api/a4/match-play", {\n        method: "POST",\n        headers: { "Content-Type": "application/json" },\n'),
    ]
    for old, new in edits:
        if changed.count(old) != 1:
            raise ValueError("Expected exactly one reviewed frontend change")
        changed = changed.replace(old, new, 1)
    if ENGINE.sha(changed) != NEW_APP_SHA256:
        raise ValueError("Reconstructed frontend differs from the tested fix")
    return changed


def load_patch() -> tuple[dict, dict[str, bytes]]:
    ENGINE.regular(SOURCE_ARCHIVE)
    if SOURCE_ARCHIVE.stat().st_size != 1654535:
        raise ValueError("Original immutable patch archive size differs")
    archive_bytes = SOURCE_ARCHIVE.read_bytes()
    if ENGINE.sha(archive_bytes) != SOURCE_ARCHIVE_SHA256:
        raise ValueError("Original immutable patch archive hash differs")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        old_app = archive.read("program/ui/app.js")
        old_inventory = archive.read("program/runtime/install-manifest.json")
    if ENGINE.sha(old_inventory) != OLD_MANIFEST_SHA256:
        raise ValueError("Original inventory does not match this portable baseline")
    app = reviewed_frontend(old_app)
    inventory = json.loads(old_inventory)
    matched = [row for row in inventory["files"] if row["path"] == "ui/app.js"]
    if inventory["version"] != "0.20.2" or len(matched) != 1 or matched[0]["sha256"] != OLD_APP_SHA256:
        raise ValueError("Original inventory has an unexpected frontend record")
    matched[0].update(bytes=len(app), sha256=NEW_APP_SHA256)
    inventory["total_bytes"] = sum(row["bytes"] for row in inventory["files"])
    contents = {"ui/app.js": app, "runtime/install-manifest.json": ENGINE.encoded(inventory)}
    if ENGINE.sha(contents["runtime/install-manifest.json"]) != NEW_MANIFEST_SHA256:
        raise ValueError("Reconstructed inventory differs from the reviewed hotfix")
    previous = dict(zip(PROGRAM_FILES, (OLD_APP_SHA256, OLD_MANIFEST_SHA256), strict=True))
    manifest = {"hotfix_id": HOTFIX_ID, "version": "0.20.2", "program_only": True, "requires_closed_application": True,
                "changes": [{"path": relative, "bytes": len(contents[relative]), "sha256": ENGINE.sha(contents[relative]),
                             "previous_sha256": previous[relative]} for relative in PROGRAM_FILES]}
    return manifest, contents


def run(*, apply: bool = False, recover: bool = False) -> dict:
    target = ENGINE.POLICY.profile_path() / ENGINE.TARGET_NAME
    ENGINE.validate_target(target)
    manifest, contents = load_patch()
    if not recover:
        ENGINE.regular(ROOT / "ui/app.js")
        if ENGINE.POLICY.digest(ROOT / "ui/app.js") != NEW_APP_SHA256:
            raise ValueError("Working frontend differs from the tested hotfix")
    ENGINE.reject_running_processes(target)
    if recover:
        original = ENGINE.read_program(target / "runtime" / ENGINE.BACKUP_NAME / "before" / ENGINE.MANIFEST_PATH)
        if ENGINE.sha(original) != OLD_MANIFEST_SHA256:
            raise ValueError("Recovery baseline differs from this fixed patch")
        records = {row["path"]: row for row in json.loads(original)["files"]}
        hashes = {relative: records[relative]["sha256"] for relative in ENGINE.LAUNCH_FILES}
        with ENGINE.locked_program(target, hashes), ENGINE.deployment_lock(target):
            return ENGINE.recover_transaction(target, manifest)
    _before, hashes = ENGINE.preflight(target, manifest)
    with ENGINE.locked_program(target, hashes):
        before, _hashes = ENGINE.preflight(target, manifest)
        if not apply:
            return {"status": "ready", "target": str(target), "hotfix_id": HOTFIX_ID,
                    "program_changes": list(PROGRAM_FILES), "application_modified": False}
        with ENGINE.deployment_lock(target):
            return ENGINE.apply_transaction(target, manifest, contents, before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--recover", action="store_true")
    options = parser.parse_args()
    try:
        print(json.dumps(run(apply=options.apply, recover=options.recover), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"status": "stopped", "error": str(error), "error_type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
