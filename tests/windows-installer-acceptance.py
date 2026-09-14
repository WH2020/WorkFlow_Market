"""Real NSIS verification install; never installs over a user's release."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid
import winreg


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("installer_policy", ROOT / "scripts/windows-installer-bootstrap.py")
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def clean_environment() -> dict[str, str]:
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME", "USERDOMAIN",
               "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
               "COMMONPROGRAMFILES", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    windows = Path(os.environ["SystemRoot"])
    paths = [windows / "System32", windows, windows / "System32/WindowsPowerShell/v1.0"]
    for tool in ("git", "rg", "fd"):
        found = shutil.which(tool)
        assert found, f"Missing prerequisite {tool}"
        paths.append(Path(found).parent)
    environment["PATH"] = os.pathsep.join(dict.fromkeys(map(str, paths)))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def registered(version: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, fr"Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-{version}"):
            return True
    except FileNotFoundError:
        return False


def close_window(process: subprocess.Popen) -> bool:
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []
    @callback_type
    def callback(window, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == process.pid and user.IsWindowVisible(window):
            title = ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(window, title, len(title))
            if title.value == "销售总监智能助手":
                windows.append(window)
        return True
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.EnumWindows(callback, 0)
    for window in windows:
        user.PostMessageW(window, 0x10, 0, 0)  # WM_CLOSE, owned app windows only.
    return bool(windows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verification-id", default=uuid.uuid4().hex)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Resume only this explicitly named verification directory after a harness failure")
    args = parser.parse_args()
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    identifier = args.verification_id
    target = POLICY.profile_path() / f"Agent4Market-{version}-test-{identifier}"
    assert target == POLICY.target_path(str(target), version, identifier)
    assert target.is_dir() if args.resume else not target.exists(), "Only a fresh or explicitly resumed verification installation is allowed"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 8765)) != 0, "Do not disturb any running workbench"
    assert not registered(version), "Verification must not replace a registered release"
    args.output.mkdir(parents=True, exist_ok=True)
    environment = clean_environment()
    neutral = os.environ["SystemRoot"]
    flags = subprocess.CREATE_NO_WINDOW
    command = [str(args.installer.resolve()), "/S", f"/VERIFY={identifier}"]
    print(f"Installing only the synthetic verification target: {target}", flush=True)
    started = time.monotonic()
    if not args.resume:
        installed = subprocess.run(command, env=environment, cwd=neutral, creationflags=flags, timeout=300)
        assert installed.returncode == 0, f"Installer returned {installed.returncode}; target retained for diagnosis"
    assert not registered(version)
    manifest = json.loads((target / "runtime/install-manifest.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        assert digest(POLICY.regular_file(target, row["path"])) == row["sha256"], row["path"]
    package_python = target / ".venv/Scripts/python.exe"
    checks = subprocess.run([str(package_python), "-I", "-B", "-c",
        "import sys;from pathlib import Path;from agent_platform.wechat_privacy import verify_private_directory;"
        "r=Path(sys.argv[1]);verify_private_directory(r);"
        "assert sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site;"
        "assert all(Path(p).resolve().is_relative_to(r) for p in sys.path);"
        "import agent_platform.app_updates,agent_platform.cli_model_catalog;"
        "import Crypto,zstandard,PIL,pptx,yaml,sqlite3;print('Installed embedded runtime and ACL verified')", str(target)],
        env=environment, cwd=neutral, creationflags=flags, text=True, capture_output=True, timeout=40)
    assert checks.returncode == 0, checks.stderr
    print(checks.stdout.strip(), flush=True)
    before_exe = digest(target / "Agent4Market.exe")
    duplicate = subprocess.run(command, env=environment, cwd=neutral, creationflags=flags, timeout=90)
    assert duplicate.returncode != 0, "Existing target must be refused"
    assert digest(target / "Agent4Market.exe") == before_exe
    self_test = subprocess.run([str(target / "Agent4Market.exe"), "--self-test"], env=environment, cwd=neutral, creationflags=flags, timeout=90)
    assert self_test.returncode == 0, "Delivered EXE self-test failed"
    print("Installed EXE self-test and no-overwrite replay passed", flush=True)
    app = subprocess.Popen([str(target / "Agent4Market.exe"), "--ui-self-test"], env=environment, cwd=neutral, creationflags=flags)
    bootstrap = None
    window_seen = False
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert app.poll() is None, "Native application exited before readiness"
            try:
                with urlopen("http://127.0.0.1:8765/api/bootstrap", timeout=1) as response:
                    bootstrap = json.load(response)
                if bootstrap:
                    break
            except OSError:
                time.sleep(0.25)
        assert bootstrap, "Native workbench did not become ready"
        assert not bootstrap["model"]["configured"]
        assert len(bootstrap["tasks"]) == 0
        assert bootstrap["desktop_runtime"]["status"] == "offline"  # No AI/core in UI-self-test.
        assert bootstrap["wechat"]["message_count"] == 0
        assert bootstrap["app_updates"]["current_version"] == version
        assert bootstrap["app_updates"]["repository"] == "WH2020/WorkFlow_Market"
        assert bootstrap["app_updates"]["automatic_install"] is False
        with urlopen("http://127.0.0.1:8765/", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "Claude Code" in page and "Codex CLI" in page
        assert "检查更新" in page and "app-update-status" in page
        time.sleep(3)  # Wait for the actual WebView host, not its helper/message windows.
    finally:
        if app.poll() is None:
            window_seen = close_window(app)
            try:
                app.wait(timeout=20)
            except subprocess.TimeoutExpired:
                subprocess.run([str(Path(neutral) / "System32/taskkill.exe"), "/PID", str(app.pid), "/T", "/F"], creationflags=flags, capture_output=True)
                raise AssertionError("Owned native window did not close cleanly")
    assert window_seen, "Native WebView host window was not observed"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 8765)) != 0, "Owned workbench process leaked"
    result = {"status": "passed", "version": version, "installer": str(args.installer.resolve()), "installer_sha256": digest(args.installer),
              "verification_root": str(target), "verified_payload_files": len(manifest["files"]), "self_test_exit": self_test.returncode,
              "duplicate_refused": True, "native_window_observed": window_seen, "model_configured": False, "tasks": 0,
              "wechat_messages": 0, "registry_written": False, "neutral_environment": True, "model_requests": 0,
              "app_updates_in_installed_package": True, "cli_model_catalog_in_installed_package": True,
              "seconds": round(time.monotonic() - started, 2)}
    if args.uninstall:
        # Only this UUID-bound, newly created test installation may be removed.
        assert target == POLICY.target_path(str(target), version, identifier)
        data_canary = target / "data/installer-verification-canary.txt"
        data_canary.write_text("SYNTHETIC USER DATA MUST SURVIVE", encoding="utf-8")
        changed = target / "ui/app.js"
        with changed.open("a", encoding="utf-8") as output:
            output.write("\n// SYNTHETIC MODIFIED PROGRAM MUST SURVIVE\n")
        changed_sha = digest(changed)
        removed = subprocess.run([str(target / "Uninstall.exe"), "/S", f"/VERIFY={identifier}", f"_?={target}"],
                                 env=environment, cwd=neutral, creationflags=flags, timeout=180)
        assert removed.returncode == 0, "Verification uninstaller failed"
        assert data_canary.read_text(encoding="utf-8") == "SYNTHETIC USER DATA MUST SURVIVE"
        assert digest(changed) == changed_sha
        assert (target / "runtime/private-runtime.marker").is_file()
        assert not (target / "Agent4Market.exe").exists()
        assert not (target / ".venv/Scripts/python.exe").exists()
        result.update({"uninstall_exit": removed.returncode, "synthetic_user_data_preserved": True, "modified_program_preserved": True})
    (args.output / "installer-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
