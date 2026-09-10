"""Prepare a new, private, directly runnable 0.20.2 directory from the release.

This is a build-machine tool, not an installer: no registry, shortcuts, global
dependencies, existing ACLs or user data are changed. Only manifest files ship.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.20.2"
RELEASE_MANIFEST_SHA256 = "7c945f7aa030f368afdee08a33e02a6fff364469998c59474648919cf88e9191"


def module_at(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Required build helper is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = module_at("portable_install_policy", ROOT / "scripts/windows-installer-bootstrap.py")
PRIVACY = module_at("portable_privacy", ROOT / "agent_platform/wechat_privacy.py")
INITIALIZER = module_at("portable_initialization", ROOT / "plugin/market-director-copilot/scripts/init_local_data.py")


def target_path(verification: str = "") -> Path:
    if verification and not re.fullmatch(r"[0-9a-f]{32}", verification):
        raise ValueError("Invalid verification identifier")
    suffix = f"-test-{verification}" if verification else ""
    return POLICY.profile_path() / f"Agent4Market-{VERSION}-portable{suffix}"


def source_path(raw: Path) -> Path:
    # Validate the lexical path before resolving or reading anything beneath it.
    # In particular, an installed 0.20.1/0.20.2 directory is never an input.
    if (not raw.is_absolute() or raw.parent != POLICY.profile_path() or
            not re.fullmatch(rf"Agent4Market-{re.escape(VERSION)}-payload-[0-9]{{8}}", raw.name)):
        raise ValueError("Only the clean, version-specific build payload is accepted")
    PRIVACY._windows_verify(raw)
    return raw


def validated_manifest(manifest: dict) -> list[dict]:
    rows = manifest.get("files")
    if manifest.get("version") != VERSION or not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
        raise ValueError("Invalid portable release manifest")
    seen: set[str] = set()
    for row in rows:
        relative = str(POLICY.safe_relative(row["path"]))
        if (relative.casefold() in seen or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) or
                type(row.get("bytes")) is not int or row["bytes"] < 0):
            raise ValueError("Invalid portable release record")
        seen.add(relative.casefold())
    required = {"Agent4Market.exe", "runtime/private-runtime.marker", ".venv/Scripts/python311._pth",
                ".venv/Scripts/python.exe", "runtime/node/node.exe", "agent_platform/cli_process_host.py",
                "pi/extensions/cli-model-provider.ts", *INITIALIZER.TEMPLATES}
    if not {item.casefold() for item in required} <= seen:
        raise ValueError("Portable release is missing required files")
    if manifest.get("total_bytes") != sum(row["bytes"] for row in rows):
        raise ValueError("Portable release size mismatch")
    return rows


def read_manifest(path: Path) -> tuple[bytes, list[dict]]:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or
            info.st_size > 32 * 1024 * 1024 or POLICY.digest(path) != RELEASE_MANIFEST_SHA256):
        raise ValueError("Use the exact previously verified 0.20.2 release manifest")
    content = path.read_bytes()
    import hashlib
    if hashlib.sha256(content).hexdigest() != RELEASE_MANIFEST_SHA256:
        raise ValueError("Release manifest changed while reading")
    return content, validated_manifest(json.loads(content))


def copy_verified(source: Path, destination: Path, row: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    if destination.stat().st_size != row["bytes"] or POLICY.digest(destination) != row["sha256"]:
        raise ValueError("Copied program file failed release verification")


def build(payload: Path, manifest_path: Path, verification: str = "") -> Path:
    if os.name != "nt":
        raise RuntimeError("This private portable release requires Windows NTFS")
    target = target_path(verification)
    if os.path.lexists(target):
        raise FileExistsError("Portable destination already exists; no files or ACLs were changed")
    payload = source_path(payload)
    manifest_content, rows = read_manifest(manifest_path)
    POLICY.check_prerequisites()
    for row in rows:
        source = POLICY.regular_file(payload, row["path"])
        if source.stat().st_size != row["bytes"] or POLICY.digest(source) != row["sha256"]:
            raise ValueError("Clean build payload no longer matches the released version")
    print(f"Verified {len(rows)} release inputs; creating only {target}", flush=True)
    POLICY.prepare(target, PRIVACY)  # Atomic new directory, protected owner/SYSTEM ACL.
    late = {"Agent4Market.exe", "runtime/private-runtime.marker"}
    for index, row in enumerate(rows, 1):
        if row["path"] not in late:
            copy_verified(POLICY.regular_file(payload, row["path"]), target / row["path"], row)
        if index % 5000 == 0:
            print(f"Copied and verified {index}/{len(rows)} release records", flush=True)
    with (target / "runtime/install-manifest.json").open("xb") as output:
        output.write(manifest_content)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP"}}
    initialized = subprocess.run([
        str(target / ".venv/Scripts/python.exe"), "-I", "-B",
        str(target / "plugin/market-director-copilot/scripts/init_local_data.py"), "--project", str(target),
    ], cwd=os.environ["SystemRoot"], env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True, text=True, timeout=45)
    if initialized.returncode:
        raise RuntimeError("Empty-data initialization failed; incomplete directory retained")
    for example, relative in INITIALIZER.TEMPLATES.items():
        if POLICY.digest(target / example) != POLICY.digest(target / relative):
            raise ValueError("Initial data must be byte-identical to the public blank examples")
    with (target / "免安装版使用说明.md").open("xb") as output:
        output.write((ROOT / "desktop/windows-portable-notes.md").read_bytes())
    POLICY.verify(target, VERSION, PRIVACY, staging=True)
    marker = next(row for row in rows if row["path"] == "runtime/private-runtime.marker")
    copy_verified(POLICY.regular_file(payload, marker["path"]), target / marker["path"], marker)
    exe = next(row for row in rows if row["path"] == "Agent4Market.exe")
    pending = target / ".Agent4Market.exe.pending"
    copy_verified(POLICY.regular_file(payload, exe["path"]), pending, exe)
    receipt = {"version": VERSION, "kind": "private-portable-directory", "program_files": len(rows),
               "program_bytes": sum(row["bytes"] for row in rows), "manifest_sha256": RELEASE_MANIFEST_SHA256,
               "executable_sha256": exe["sha256"], "initialized_files": list(INITIALIZER.TEMPLATES.values()),
               "registry_written": False, "shortcuts_written": False, "existing_data_read": False}
    with (target / "runtime/portable-build.json").open("x", encoding="utf-8") as output:
        json.dump(receipt, output, ensure_ascii=False, indent=2)
    PRIVACY._windows_verify(target)
    # Windows rename fails if the destination exists. Publish the entry point
    # only after complete code/data verification; partial copies cannot launch.
    os.rename(pending, target / "Agent4Market.exe")
    print(json.dumps({"status": "built", "target": str(target), **receipt}, ensure_ascii=False), flush=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verification-id", default="")
    arguments = parser.parse_args()
    build(arguments.payload, arguments.manifest, arguments.verification_id)
