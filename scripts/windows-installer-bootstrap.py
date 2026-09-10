"""Private, new-directory-only NSIS installation and conservative removal.

Runs with the installer-owned embedded Python, never the user's interpreter.
No business data, model configuration or CLI authentication is read here.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys


PROGRAM_DIRS = frozenset({"agent_platform", "profiles", "vertical_plugins", "pi", "plugin",
                          "ui", "scripts", "library", "runtime", "node_modules", ".venv"})
PROGRAM_FILES = frozenset({"AGENTS.md", "README.md", "LICENSE", "package.json", "pnpm-lock.yaml", "tsconfig.json",
                           "requirements.txt", "requirements-wxdecipher.txt", "Agent4Market.exe",
                           "INSTALL-NOTES.md"})


class PrerequisiteError(RuntimeError):
    pass


def check_prerequisites() -> None:
    import winreg
    missing = [name for name in ("git", "rg", "fd") if shutil.which(name) is None]
    webview = False
    key_name = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValueEx(key, "pv")[0]
                    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(value)) and value != "0.0.0.0":
                        webview = True
            except OSError:
                pass
    if not webview:
        missing.append("Microsoft Edge WebView2 Runtime")
    if missing:
        raise PrerequisiteError("Missing prerequisites (nothing was installed): " + ", ".join(missing))


def profile_path() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
    shell.SHGetFolderPathW.restype = ctypes.c_long
    if shell.SHGetFolderPathW(None, 0x28, None, 0, buffer) != 0:
        raise RuntimeError("Cannot resolve the current Windows profile")
    return Path(buffer.value)


def target_path(raw: str, version: str, verification: str = "") -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    if verification and not re.fullmatch(r"[0-9a-f]{32}", verification):
        raise ValueError("Invalid verification identifier")
    suffix = f"-test-{verification}" if verification else ""
    expected = profile_path() / f"Agent4Market-{version}{suffix}"
    # No /D override, network path, other release, traversal or broad target.
    if os.path.normcase(raw) != os.path.normcase(str(expected)):
        raise ValueError("The installer only supports its new per-user release directory")
    return expected


def privacy_module():
    path = Path(__file__).with_name("privacy.py")
    spec = importlib.util.spec_from_file_location("installer_privacy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Privacy guard is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(target: Path, privacy) -> None:
    sid = privacy._win_current_sid()
    parents = privacy._win_open_safe_parents(target, sid, for_ensure=True)
    handle = None
    descriptor = ctypes.c_void_p()
    try:
        # CREATE_NEW semantics: an existing empty directory is also refused.
        # Do not call ensure(), which is permitted to repair an existing DACL.
        sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{privacy._win_sid_text(sid)})"
        if not privacy._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise RuntimeError("Cannot create the private descriptor")
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if not privacy._kernel.CreateDirectoryW(str(target), ctypes.byref(attributes)):
            raise RuntimeError("The target already exists or cannot be safely created")
        handle = privacy._win_open_directory(target, privacy._READ_CONTROL)
        privacy._win_check_target(handle, sid)
    finally:
        privacy._win_close(handle)
        if descriptor.value:
            privacy._kernel.LocalFree(descriptor)
        for parent in reversed(parents):
            privacy._win_close(parent)


def safe_relative(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (not raw or "\\" in raw or ":" in raw or path.is_absolute() or
            any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in raw.split("/"))):
        raise ValueError("Unsafe manifest path")
    if path.parts[0] == "data":
        # The manifest may contain public example files; never remove user data.
        if not re.fullmatch(r"data/[A-Za-z0-9_./-]+\.example\.(csv|json)", raw):
            raise ValueError("Non-example data is forbidden in an installer")
    elif path.parts[0] not in PROGRAM_DIRS and raw not in PROGRAM_FILES:
        raise ValueError("Non-program manifest path")
    if any(part in {".pi", "outputs", "company", "%SystemDrive%"} for part in path.parts):
        raise ValueError("Private/runtime data is forbidden in an installer")
    return path


def entries(target: Path, version: str) -> list[dict]:
    path = target / "runtime/install-manifest.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 32 * 1024 * 1024:
        raise ValueError("Invalid install manifest")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = manifest["files"]
    if manifest.get("version") != version or not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError("Invalid install manifest version")
    seen = set()
    for row in rows:
        relative = str(safe_relative(row["path"]))
        if relative.casefold() in seen or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            raise ValueError("Invalid install file record")
        seen.add(relative.casefold())
    return rows


def regular_file(target: Path, relative: str) -> Path:
    path = target
    for part in safe_relative(relative).parts:
        path = path / part
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse points are not allowed")
    if not path.is_file() or path.stat().st_nlink != 1 or not path.resolve().is_relative_to(target.resolve()):
        raise ValueError("Non-private installed file")
    return path


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify(target: Path, version: str, privacy, *, staging: bool = False) -> None:
    privacy._windows_verify(target)
    rows = entries(target, version)
    for row in rows:
        if staging and row["path"] in {"Agent4Market.exe", "runtime/private-runtime.marker"}:
            continue
        if digest(regular_file(target, row["path"])) != row["sha256"]:
            raise ValueError("Installed file verification failed")
    print(f"Verified {len(rows)} installed program files")


def uninstall(target: Path, version: str, privacy) -> None:
    privacy._windows_verify(target)
    rows = entries(target, version)  # Validate the COMPLETE plan before deleting.
    removed = retained = 0
    directories = set()
    for row in sorted(rows, key=lambda item: item["path"] == "Agent4Market.exe"):
        relative = row["path"]
        if relative.startswith("data/") or relative == "runtime/private-runtime.marker":
            retained += 1
            continue
        try:
            path = regular_file(target, relative)
            if digest(path) != row["sha256"]:
                retained += 1
                continue
            path.unlink()  # Exact, unchanged manifest entries only; no recursion.
            removed += 1
            directories.update(parent for parent in path.parents if parent != target and parent.is_relative_to(target))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            retained += 1
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            if not getattr(directory.lstat(), "st_file_attributes", 0) & 0x400:
                directory.rmdir()  # Empty directories only. The release root remains.
        except OSError:
            pass
    print(f"Removed {removed} unchanged program files; retained {retained} data/modified/locked files. User data was preserved.")


def main() -> int:
    try:
        action, raw, version, verification = sys.argv[1:]
        target = target_path(raw, version, verification)
        privacy = privacy_module()
        if action == "prepare":
            check_prerequisites()
        if action == "verify-staging":
            verify(target, version, privacy, staging=True)
        else:
            {"prepare": prepare, "verify": verify, "uninstall": uninstall}[action](target, *([] if action == "prepare" else [version]), privacy)
        return 0
    except Exception as error:
        if isinstance(error, PrerequisiteError):
            print(str(error), file=sys.stderr)
        print(f"Installation stopped safely: {type(error).__name__}. Existing versions and data were not replaced.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
