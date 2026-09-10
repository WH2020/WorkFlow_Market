"""Apply one reviewed, program-only 0.20.2 patch to the closed portable app.

Default is read-only preflight. --apply is an explicit deployment action.
No recursion over installed content, configuration, business data or logins.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("apply_patch_base", ROOT / "scripts/build-windows-app-updates-hotfix.py")
BASE_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE_MODULE)
BUILDER = BASE_MODULE.BUILDER
POLICY, PRIVACY = BUILDER.BASE.POLICY, BUILDER.BASE.PRIVACY
HOTFIX_ID = "0.20.2-app-updates-20260910"
TARGET_NAME = "Agent4Market-0.20.2-portable"
ZIP_PATH = ROOT / "outputs/releases/0.20.2-app-updates-hotfix-20260910.zip"
ZIP_SHA256 = "d3a494149cb17b2351e07e9527009ef6f0ead446bc0f072a619092d32da7c861"
RECEIPT_SHA256 = "1d3a019d4091a3446e5d932e13860c5f95e38be35702f349bf7a7813609184ba"
BASE_SHA256 = "7c945f7aa030f368afdee08a33e02a6fff364469998c59474648919cf88e9191"
MANIFEST_PATH = "runtime/install-manifest.json"
PROGRAM_FILES = (*BUILDER.PATCH_FILES, MANIFEST_PATH)
NEW_FILES = frozenset({"agent_platform/cli_model_catalog.py", "agent_platform/app_updates.py"})
LAUNCH_FILES = ("Agent4Market.exe", "runtime/node/node.exe", ".venv/Scripts/python.exe")
BACKUP_NAME = "hotfix-backup-" + HOTFIX_ID
RECEIPT_NAME = "hotfix-" + HOTFIX_ID + ".json"
LOCK_NAME = "hotfix-" + HOTFIX_ID + ".lock"


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def regular(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
        raise ValueError("Reparse points and symlinks are forbidden")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Expected an ordinary directory")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Expected a single-link regular file")


def installed_path(target: Path, relative: str) -> Path:
    if relative not in (*PROGRAM_FILES, *LAUNCH_FILES):
        raise ValueError("File is outside the closed program allowlist")
    path = target
    for component in relative.split("/")[:-1]:
        path /= component
        regular(path, directory=True)
    return path / relative.split("/")[-1]


def read_program(path: Path, *, limit: int = 32 * 1024 * 1024) -> bytes:
    regular(path)
    PRIVACY._windows_verify_file(path)
    if path.stat().st_size > limit:
        raise ValueError("Program file exceeds the accepted size")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Program file exceeds the accepted size")
    return content


def validate_target(target: Path) -> None:
    expected = POLICY.profile_path() / TARGET_NAME
    if os.name != "nt" or target != expected or not target.is_absolute():
        raise ValueError("Only this Windows user's exact 0.20.2 portable directory is accepted")
    PRIVACY._normalise_path(target)
    PRIVACY._windows_verify(target)


def load_patch() -> tuple[dict, dict[str, bytes]]:
    regular(ZIP_PATH)
    if ZIP_PATH.stat().st_size != 1654535:
        raise ValueError("Patch ZIP size mismatch")
    archive_bytes = ZIP_PATH.read_bytes()
    if sha(archive_bytes) != ZIP_SHA256:
        raise ValueError("Patch ZIP hash mismatch")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        expected = {*("program/" + relative for relative in PROGRAM_FILES), "hotfix-manifest.json", "热修复说明.md"}
        if len(archive.infolist()) != 20 or set(archive.namelist()) != expected:
            raise ValueError("Patch ZIP is not the reviewed closed file set")
        if any(info.file_size > 8 * 1024 * 1024 for info in archive.infolist()) or sum(info.file_size for info in archive.infolist()) > 16 * 1024 * 1024:
            raise ValueError("Patch payload exceeds the accepted size")
        raw = archive.read("hotfix-manifest.json")
        if sha(raw) != RECEIPT_SHA256:
            raise ValueError("Patch receipt hash mismatch")
        manifest = json.loads(raw)
        rows = manifest["changes"]
        if (manifest["hotfix_id"] != HOTFIX_ID or manifest["version"] != "0.20.2" or
                manifest["required_portable_name"] != TARGET_NAME or manifest["base_manifest_sha256"] != BASE_SHA256 or
                manifest["requires_closed_application"] is not True or tuple(row["path"] for row in rows) != PROGRAM_FILES):
            raise ValueError("Patch identity or plan mismatch")
        if {row["path"] for row in rows if row["previous_sha256"] is None} != NEW_FILES:
            raise ValueError("Unexpected newly created program file")
        contents = {row["path"]: archive.read("program/" + row["path"]) for row in rows}
        for row in rows:
            if sha(contents[row["path"]]) != row["sha256"] or len(contents[row["path"]]) != row["bytes"]:
                raise ValueError("Patch program hash mismatch")
    return manifest, contents


def reject_running_processes(target: Path) -> None:
    # Read image paths/PIDs only: never request or print process command lines.
    prefix = (str(target) + "\\").replace("'", "''")
    script = "$ErrorActionPreference='Stop'; $a4mPrefix='" + prefix + "'; @(" + (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
        "$_.ExecutablePath.StartsWith($a4mPrefix,[System.StringComparison]::OrdinalIgnoreCase) } "
        "| Select-Object ProcessId,Name) | ConvertTo-Json -Compress"
    )
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode != 0 or len(result.stdout) > 64 * 1024:
        raise RuntimeError("Cannot verify application process shutdown")
    if json.loads(result.stdout or b"[]"):
        raise RuntimeError("Application processes are still running; nothing was replaced")


@contextmanager
def locked_program(target: Path, launch_hashes: dict[str, str]):
    import msvcrt
    sid = PRIVACY._win_current_sid()
    with ExitStack() as stack:
        # Hold every program parent without FILE_SHARE_DELETE for the operation.
        directories = {target}
        for relative in (*PROGRAM_FILES, *LAUNCH_FILES):
            parent = installed_path(target, relative).parent
            directories.update(path for path in (parent, *parent.parents) if path == target or path.is_relative_to(target))
        ancestors = PRIVACY._win_open_safe_parents(target, sid)
        for handle in ancestors:
            stack.callback(PRIVACY._win_close, handle)
        for path in sorted(directories, key=lambda item: len(item.parts)):
            handle = PRIVACY._win_open_directory(path, PRIVACY._READ_CONTROL)
            stack.callback(PRIVACY._win_close, handle)
            if path == target:
                PRIVACY._win_check_target(handle, sid)
            else:
                PRIVACY._win_check_parent(handle, sid)
        for relative in LAUNCH_FILES:
            path = installed_path(target, relative)
            regular(path)
            handle = PRIVACY._kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x00200000, None)
            if handle in (None, PRIVACY._INVALID_HANDLE_VALUE):
                raise RuntimeError("An application executable is busy; nothing was replaced")
            try:
                PRIVACY._win_check_file(handle, sid)
                descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
            except BaseException:
                PRIVACY._win_close(handle)
                raise
            stream = stack.enter_context(os.fdopen(descriptor, "rb"))
            if hashlib.file_digest(stream, "sha256").hexdigest() != launch_hashes[relative]:
                raise ValueError("An application executable differs from the reviewed release")
        reject_running_processes(target)
        yield


@contextmanager
def deployment_lock(target: Path):
    path = target / "runtime" / LOCK_NAME
    regular(path.parent, directory=True)
    # CREATE_NEW + share=0; only this newly created lock is deleted on close.
    handle = PRIVACY._kernel.CreateFileW(str(path), 0x80000000 | 0x40000000 | 0x10000,
                                        0, None, 1, 0x04000000 | 0x00200000, None)
    if handle in (None, PRIVACY._INVALID_HANDLE_VALUE):
        raise RuntimeError("A deployment lock already exists or cannot be acquired")
    try:
        yield
    finally:
        PRIVACY._win_close(handle)


def preflight(target: Path, manifest: dict) -> tuple[dict[str, bytes], dict[str, str]]:
    if (target / "runtime" / BACKUP_NAME).exists() or (target / "runtime" / RECEIPT_NAME).exists():
        raise RuntimeError("This patch has an existing backup/receipt; inspect or recover it before another deployment")
    before = {}
    for row in manifest["changes"]:
        path = installed_path(target, row["path"])
        if row["previous_sha256"] is None:
            if os.path.lexists(path):
                raise ValueError("A new patch file already exists; existing work was not overwritten")
        else:
            content = read_program(path)
            if sha(content) != row["previous_sha256"]:
                raise ValueError("An existing program differs from the patch baseline: " + row["path"])
            before[row["path"]] = content
    old = json.loads(before[MANIFEST_PATH])
    records = {row["path"]: row for row in old["files"]}
    hashes = {relative: records[relative]["sha256"] for relative in LAUNCH_FILES}
    if shutil.disk_usage(target).free < max(64 * 1024 * 1024, sum(row["bytes"] for row in manifest["changes"]) * 4):
        raise RuntimeError("Insufficient disk space for staging, backup and rollback")
    return before, hashes


def write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    PRIVACY._windows_verify_file(path)


def move_program(source: Path, destination: Path, *, replace_existing: bool = True) -> None:
    # No cross-volume copy fallback. Flush staged bytes first, then request
    # write-through for both intent-journal and program-file rename metadata.
    function = PRIVACY._kernel.MoveFileExW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    function.restype = wintypes.BOOL
    if not function(str(source), str(destination), 0x8 | (0x1 if replace_existing else 0)):
        raise OSError(ctypes.get_last_error(), "Atomic program move failed")


def save_journal(backup: Path, journal: dict) -> None:
    temporary = backup / ("journal-" + uuid4().hex + ".tmp")
    write_new(temporary, encoded(journal))
    move_program(temporary, backup / "transaction.json")


def current_digest(path: Path) -> str | None:
    return sha(read_program(path)) if os.path.lexists(path) else None


def rollback(target: Path, backup: Path, manifest: dict, attempted: list[str]) -> list[str]:
    rows = {row["path"]: row for row in manifest["changes"]}
    problems = []
    for relative in reversed(attempted):
        row = rows[relative]
        try:
            destination = installed_path(target, relative)
            current = current_digest(destination)
            if current == row["previous_sha256"]:
                continue
            if current != row["sha256"]:
                raise RuntimeError("Concurrent program change; automatic recovery refused")
            if row["previous_sha256"] is None:
                destination.unlink()  # Only a verified, newly created allowlisted file.
            else:
                original = read_program(backup / "before" / relative)
                if sha(original) != row["previous_sha256"]:
                    raise RuntimeError("Program backup hash mismatch")
                staged = backup / "restore" / uuid4().hex / relative
                write_new(staged, original)
                move_program(staged, destination)
            if current_digest(destination) != row["previous_sha256"]:
                raise RuntimeError("Restored program hash mismatch")
        except Exception:
            problems.append(relative)
    return problems


def apply_transaction(target: Path, manifest: dict, contents: dict[str, bytes], before: dict[str, bytes]) -> dict:
    PRIVACY._windows_verify(target)
    backup = target / "runtime" / BACKUP_NAME
    POLICY.prepare(backup, PRIVACY)  # Atomic NEW private directory; never repairs ACLs.
    journal = {"hotfix_id": HOTFIX_ID, "transaction_id": uuid4().hex, "phase": "preparing",
               "target": str(target), "started_at": datetime.now(timezone.utc).isoformat(),
               "changes": manifest["changes"], "attempted": [], "program_only": True}
    receipt_path = target / "runtime" / RECEIPT_NAME
    receipt_content = None
    try:
        save_journal(backup, journal)
        for relative, content in before.items():
            path = backup / "before" / relative
            write_new(path, content)
            if sha(read_program(path)) != sha(content):
                raise RuntimeError("Program backup verification failed")
        for relative, content in contents.items():
            path = backup / "stage" / relative
            write_new(path, content)
            if sha(read_program(path)) != sha(content):
                raise RuntimeError("Program staging verification failed")
        journal["phase"] = "applying"
        # The reviewed receipt places the application inventory LAST.
        for row in manifest["changes"]:
            relative = row["path"]
            destination = installed_path(target, relative)
            if current_digest(destination) != row["previous_sha256"]:
                raise RuntimeError("Program changed during staging; replacement stopped")
            journal["attempted"].append(relative)
            save_journal(backup, journal)  # Persist intent before the atomic rename.
            move_program(backup / "stage" / relative, destination)
            if current_digest(destination) != row["sha256"]:
                raise RuntimeError("Replaced program verification failed")
        for row in manifest["changes"]:
            if current_digest(installed_path(target, row["path"])) != row["sha256"]:
                raise RuntimeError("Final program verification failed")
        PRIVACY._windows_verify(target)
        result = {"status": "applied", "hotfix_id": HOTFIX_ID, "program_files_verified": len(manifest["changes"]),
                  "backup": str(backup), "configuration_or_business_data_accessed": False,
                  "application_started": False, "completed_at": datetime.now(timezone.utc).isoformat()}
        receipt_content = encoded(result)
        journal["completion_receipt_sha256"] = sha(receipt_content)
        journal["phase"] = "committing_receipt"
        save_journal(backup, journal)
        write_new(backup / "completed-receipt.json", receipt_content)
        move_program(backup / "completed-receipt.json", receipt_path, replace_existing=False)
        journal["phase"] = "applied"
        save_journal(backup, journal)
        PRIVACY._windows_verify(target)
        return result
    except BaseException as error:
        problems = rollback(target, backup, manifest, journal["attempted"])
        if receipt_content is not None and os.path.lexists(receipt_path):
            try:
                if current_digest(receipt_path) != sha(receipt_content):
                    raise RuntimeError("Unexpected receipt content")
                receipt_path.unlink()  # Only the completion receipt created by this operation.
            except Exception:
                problems.append("completion-receipt")
        journal.update(phase="needs_recovery" if problems else "rolled_back", recovery_problems=problems,
                       error_type=type(error).__name__)
        try:
            save_journal(backup, journal)
        except Exception:
            problems.append("transaction-journal")
        raise RuntimeError("Deployment failed; " + ("manual recovery is required" if problems else "program changes were rolled back") + ". Backup: " + str(backup)) from error


def recover_transaction(target: Path, manifest: dict) -> dict:
    PRIVACY._windows_verify(target)
    backup = target / "runtime" / BACKUP_NAME
    PRIVACY._windows_verify(backup)
    journal = json.loads(read_program(backup / "transaction.json", limit=256 * 1024))
    if journal.get("hotfix_id") != HOTFIX_ID or journal.get("target") != str(target) or journal.get("changes") != manifest["changes"]:
        raise ValueError("Recovery journal does not describe the reviewed target and program files")
    # Do not rely on the last persisted intent after a hard stop. Inspect ALL
    # 18 fixed program paths, accepting only exact old/new digests, before writes.
    for row in manifest["changes"]:
        if current_digest(installed_path(target, row["path"])) not in {row["previous_sha256"], row["sha256"]}:
            raise RuntimeError("Unknown program content; recovery refused without overwriting it")
        if row["previous_sha256"] is not None:
            if sha(read_program(backup / "before" / row["path"])) != row["previous_sha256"]:
                raise RuntimeError("Program backup is incomplete or altered; recovery refused")
    receipt_path = target / "runtime" / RECEIPT_NAME
    has_receipt = os.path.lexists(receipt_path)
    if has_receipt and current_digest(receipt_path) != journal.get("completion_receipt_sha256"):
        raise RuntimeError("Unknown completion receipt; recovery refused")
    problems = rollback(target, backup, manifest, [row["path"] for row in manifest["changes"]])
    if problems:
        journal.update(phase="needs_recovery", recovery_problems=problems)
        save_journal(backup, journal)
        raise RuntimeError("Some program files still require manual recovery")
    if has_receipt:
        receipt_path.unlink()  # Exact, verified completion receipt belonging to this patch.
    journal.update(phase="recovered", recovery_problems=[])
    save_journal(backup, journal)
    PRIVACY._windows_verify(target)
    return {"status": "restored_backup", "hotfix_id": HOTFIX_ID, "backup": str(backup),
            "program_files_verified": len(manifest["changes"]), "configuration_or_business_data_accessed": False}


def run(*, apply: bool = False, recover: bool = False) -> dict:
    target = POLICY.profile_path() / TARGET_NAME
    validate_target(target)
    manifest, contents = load_patch()
    reject_running_processes(target)
    if recover:
        original = read_program(target / "runtime" / BACKUP_NAME / "before" / MANIFEST_PATH)
        if sha(original) != BASE_SHA256:
            raise ValueError("Recovery baseline does not match the reviewed release")
        records = {row["path"]: row for row in json.loads(original)["files"]}
        hashes = {relative: records[relative]["sha256"] for relative in LAUNCH_FILES}
        with locked_program(target, hashes), deployment_lock(target):
            return recover_transaction(target, manifest)
    _before, launch_hashes = preflight(target, manifest)
    with locked_program(target, launch_hashes):
        before, _hashes = preflight(target, manifest)
        if not apply:
            return {"status": "ready", "target": str(target), "program_changes": len(manifest["changes"]),
                    "existing_program_backups": len(before), "new_program_files": len(NEW_FILES),
                    "configuration_or_business_data_accessed": False, "application_modified": False}
        with deployment_lock(target):
            return apply_transaction(target, manifest, contents, before)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="Deploy the reviewed patch; omit for read-only checks")
    actions.add_argument("--recover", action="store_true", help="Explicitly restore verified program backups after interruption; app must be closed")
    options = parser.parse_args()
    try:
        print(json.dumps(run(apply=options.apply, recover=options.recover), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"status": "stopped", "error": str(error), "error_type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
