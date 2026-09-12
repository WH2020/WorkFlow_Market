"""Compile reviewed runtime files into a deterministic NSIS payload manifest."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat


def quoted(value: str) -> str:
    if any(character in value for character in '\r\n\t"'):
        raise ValueError("Unsupported installer filename")
    return value.replace("$", "$$")


def payload_files(root: Path):
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            info = entry.stat(follow_symlinks=False)
            if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                raise ValueError("Installer payload must not contain links")
            path = Path(entry.path)
            if stat.S_ISDIR(info.st_mode):
                if entry.name in {"__pycache__", ".pi", "outputs"}:
                    continue
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                yield path, info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    payload = args.payload.resolve(strict=True)
    output = args.output.resolve(strict=True)
    spec = importlib.util.spec_from_file_location("installer_policy", Path(__file__).with_name("windows-installer-bootstrap.py"))
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    rows = []
    for path, info in payload_files(payload):
        relative = path.relative_to(payload).as_posix()
        # Self-tests and Python imports may generate private runtime/cache files.
        # They are not part of a release and are never opened or included.
        if relative.split("/")[0] in {".pi", "outputs"} or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        policy.safe_relative(relative)
        with path.open("rb") as source:
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        rows.append({"path": relative, "bytes": info.st_size, "sha256": sha})
        if len(rows) % 5000 == 0:
            print(f"Hashed {len(rows)} reviewed payload files", flush=True)
    rows.sort(key=lambda row: row["path"])
    paths = {row["path"] for row in rows}
    required = {"Agent4Market.exe", "runtime/private-runtime.marker", ".venv/Scripts/python311._pth",
                "agent_platform/cli_process_host.py", "agent_platform/cli_provider.py", "pi/extensions/cli-model-provider.ts",
                "agent_platform/app_updates.py", "agent_platform/cli_model_catalog.py",
                "agent_platform/windows_updates.py", "agent_platform/windows_update_engine.py",
                "agent_platform/windows_update_worker.py", "agent_platform/windows_update_protocol.json",
                "agent_platform/windows_update_signatures.py", "agent_platform/windows_update_trust.json",
                "agent_platform/local_http_security.py", "scripts/windows-installer-bootstrap.py"}
    if not required <= paths or (payload / ".venv/pyvenv.cfg").exists():
        raise ValueError("Missing CLI/runtime files or a non-portable Python environment")
    manifest = {"version": args.version, "files": rows, "total_bytes": sum(row["bytes"] for row in rows)}
    # Stable bytes across build hosts; Windows text-mode CRLF translation must
    # not change the inventory digest compared with the reviewed payload plan.
    (output / "install-manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    lines = ["; Generated from the reviewed, hashed release payload."]
    current = None
    for row in rows:
        relative = Path(row["path"])
        if row["path"] in {"Agent4Market.exe", "runtime/private-runtime.marker"}:
            continue
        parent = str(relative.parent)
        if parent != current:
            lines.append(f'SetOutPath "$INSTDIR\\{quoted(parent)}"')
            current = parent
        lines.append(f'File "/oname={quoted(relative.name)}" "{quoted(str(payload / relative))}"')
    (output / "payload.nsh").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(json.dumps({"status": "ok", "files": len(rows), "bytes": manifest["total_bytes"]}))


if __name__ == "__main__":
    main()
