"""Build a NEW 0.20.2 public payload from fixed, reviewed release artifacts.

An existing application or payload directory is never read. The hash-pinned
original installer first creates a UUID-bound verification installation without
shortcuts, registration or application startup. Only its verified manifest files
are copied to a separate new private payload, with the reviewed program patch.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_payload_patch", ROOT / "scripts/apply-windows-app-updates-hotfix.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
POLICY, PRIVACY = PATCH.POLICY, PATCH.PRIVACY
BASE = PATCH.BUILDER.BASE
INSTALLER = ROOT / "outputs/releases/0.20.2/Agent4Market-0.20.2-Setup-x64.exe"
INSTALLER_SHA256 = "5519ac842b84ecfa272dca3b97552a0c0511f50962c701098c9b10abcbd54f05"
PUBLIC_NOTES = ROOT / "desktop/windows-install-notes.md"
PAYLOAD_NAME = "Agent4Market-0.20.2-payload-20260910"
BUILD_INPUTS = ("LICENSE", "desktop/installer-python311._pth", "desktop/windows-installer.nsi",
                "scripts/windows-installer-bootstrap.py", "agent_platform/wechat_privacy.py",
                "scripts/windows-installer-manifest.py", "scripts/build-windows-installer.ps1",
                "scripts/build-windows-public-payload.py", "desktop/windows-install-notes.md")


def clean_environment() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME", "USERDOMAIN",
               "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
               "COMMONPROGRAMFILES", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    windows = Path(os.environ["SystemRoot"])
    paths = [windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0"]
    for tool in ("git", "rg", "fd"):
        found = shutil.which(tool)
        if not found:
            raise RuntimeError("Required installed tool is unavailable: " + tool)
        paths.append(Path(found).parent)
    environment["PATH"] = os.pathsep.join(dict.fromkeys(map(str, paths)))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def planned_records(original: list[dict], manifest: dict, contents: dict[str, bytes], notes: bytes) -> list[dict]:
    records = {row["path"]: dict(row) for row in original}
    for change in manifest["changes"]:
        relative = change["path"]
        if relative == PATCH.MANIFEST_PATH:
            continue  # NSIS generates and embeds its inventory separately.
        if records.get(relative, {}).get("sha256") != change["previous_sha256"]:
            raise ValueError("Patch baseline differs from the original installer")
        content = contents[relative]
        if PATCH.sha(content) != change["sha256"] or len(content) != change["bytes"]:
            raise ValueError("Patch content differs from its reviewed manifest")
        records[relative] = {key: change[key] for key in ("path", "bytes", "sha256")}
    if "INSTALL-NOTES.md" not in records:
        raise ValueError("Original installer has no installation notes")
    records["INSTALL-NOTES.md"] = {"path": "INSTALL-NOTES.md", "bytes": len(notes), "sha256": PATCH.sha(notes)}
    return sorted(records.values(), key=lambda row: row["path"])


def verify_current_sources(manifest: dict, *, root: Path = ROOT) -> None:
    rows = {row["path"]: row for row in manifest["changes"]}
    for relative in PATCH.BUILDER.PATCH_FILES:
        path = root / relative
        PATCH.regular(path)
        if path.stat().st_size != rows[relative]["bytes"] or POLICY.digest(path) != rows[relative]["sha256"]:
            raise ValueError("Working source differs from the reviewed program patch: " + relative)


def frozen_build_inputs() -> dict[str, str]:
    result = {}
    for relative in BUILD_INPUTS:
        path = ROOT / relative
        PATCH.regular(path)
        result[relative] = POLICY.digest(path)
    return result


def build(identifier: str) -> dict:
    if os.name != "nt" or not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise ValueError("A new UUID and Windows x64 are required")
    profile = POLICY.profile_path()
    source = POLICY.target_path(str(profile / ("Agent4Market-0.20.2-test-" + identifier)), "0.20.2", identifier)
    job = profile / ("Agent4Market-0.20.2-release-build-" + identifier)
    payload = job / PAYLOAD_NAME
    for path in (source, job):
        if os.path.lexists(path):
            raise FileExistsError("Build/verification target already exists; nothing was overwritten")
    PATCH.regular(INSTALLER)
    if INSTALLER.stat().st_size != 173450121 or POLICY.digest(INSTALLER) != INSTALLER_SHA256:
        raise ValueError("Original installer is not the reviewed artifact")
    original_bytes, original = BASE.read_manifest(ROOT / "outputs/releases/0.20.2/install-manifest.json")
    manifest, contents = PATCH.load_patch()
    verify_current_sources(manifest)
    build_inputs = frozen_build_inputs()
    PATCH.regular(PUBLIC_NOTES)
    notes = PUBLIC_NOTES.read_bytes()
    records = planned_records(original, manifest, contents, notes)
    if shutil.disk_usage(profile).free < 3 * 1024**3:
        raise RuntimeError("At least 3 GiB free space is required for isolated build/verification copies")
    environment = clean_environment()
    POLICY.check_prerequisites()
    print("Installing the reviewed baseline only into a new UUID verification directory", flush=True)
    installed = subprocess.run([str(INSTALLER), "/S", "/VERIFY=" + identifier],
                               cwd=os.environ["SystemRoot"], env=environment, timeout=300,
                               creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if installed.returncode:
        raise RuntimeError("Baseline verification installation failed; new target retained")
    if POLICY.digest(INSTALLER) != INSTALLER_SHA256:
        raise RuntimeError("Original installer changed during extraction")
    PRIVACY._windows_verify(source)
    if (source / PATCH.MANIFEST_PATH).read_bytes() != original_bytes:
        raise ValueError("Installed baseline inventory differs from the reviewed manifest")
    # Verify the COMPLETE fixed source set before creating the build payload.
    for row in original:
        path = POLICY.regular_file(source, row["path"])
        if path.stat().st_size != row["bytes"] or POLICY.digest(path) != row["sha256"]:
            raise ValueError("Verification installation differs from the pinned baseline")
    POLICY.prepare(job, PRIVACY)
    POLICY.prepare(payload, PRIVACY)
    original_by_path = {row["path"]: row for row in original}
    for index, row in enumerate(records, 1):
        relative = row["path"]
        target = payload / POLICY.safe_relative(relative)
        if relative == "INSTALL-NOTES.md":
            PATCH.write_new(target, notes)
        elif relative in contents:
            PATCH.write_new(target, contents[relative])
        else:
            BASE.copy_verified(POLICY.regular_file(source, relative), target, original_by_path[relative])
        if target.stat().st_size != row["bytes"] or POLICY.digest(target) != row["sha256"]:
            raise ValueError("New payload content failed verification")
        if index % 5000 == 0:
            print(f"Copied and verified {index}/{len(records)} public program files", flush=True)
    PRIVACY._windows_verify(payload)
    verify_current_sources(manifest)
    if frozen_build_inputs() != build_inputs:
        raise ValueError("Installer build sources changed during payload preparation")
    expected = {"version": "0.20.2", "files": records, "total_bytes": sum(row["bytes"] for row in records)}
    with (job / "expected-install-manifest.json").open("xb") as stream:
        stream.write(PATCH.encoded(expected))
    result = {"status": "built", "version": "0.20.2", "payload": str(payload), "job": str(job),
              "verification_source": str(source), "files": len(records), "bytes": expected["total_bytes"],
              "base_installer_sha256": INSTALLER_SHA256, "base_manifest_sha256": BASE.RELEASE_MANIFEST_SHA256,
              "patch_zip_sha256": PATCH.ZIP_SHA256, "notes_sha256": PATCH.sha(notes),
              "build_source_sha256": build_inputs,
              "manifest_sha256": PATCH.sha(PATCH.encoded(expected)), "application_started": False,
              "existing_application_or_data_accessed": False, "registry_or_shortcuts_written": False}
    with (job / "build-result.json").open("xb") as stream:
        stream.write(PATCH.encoded(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.build_id)), flush=True)
