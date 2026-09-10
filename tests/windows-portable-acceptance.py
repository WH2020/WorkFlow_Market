"""Exercise the newly built portable EXE, without any saved model or user data."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = load("portable_acceptance_builder", "scripts/build-windows-portable.py")
HARNESS = load("portable_acceptance_helpers", "tests/windows-installer-acceptance.py")


def port_free() -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", 8765)) != 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    target = BUILDER.target_path()
    assert target.is_dir() and not (target / ".pi").exists(), "Only the fresh, never-configured portable directory may be tested"
    assert not (target / "outputs").exists()
    assert port_free(), "Do not disturb any running version"
    receipt = json.loads((target / "runtime/portable-build.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "private-portable-directory" and receipt["version"] == BUILDER.VERSION
    assert receipt["manifest_sha256"] == BUILDER.RELEASE_MANIFEST_SHA256
    assert HARNESS.digest(target / "runtime/install-manifest.json") == BUILDER.RELEASE_MANIFEST_SHA256
    registry_before = HARNESS.registered(BUILDER.VERSION)
    BUILDER.POLICY.verify(target, BUILDER.VERSION, BUILDER.PRIVACY)
    initial_data = {relative: HARNESS.digest(target / relative) for relative in BUILDER.INITIALIZER.TEMPLATES.values()}
    for example, relative in BUILDER.INITIALIZER.TEMPLATES.items():
        assert HARNESS.digest(target / example) == initial_data[relative]
    environment = HARNESS.clean_environment()
    for item in environment["PATH"].split(os.pathsep):
        assert "codex-runtimes" not in item.lower() and not Path(item).is_relative_to(ROOT)
    # No test child receives the user's actual home/config/temp locations.
    # Tauri also pins Pi state and WebView data beneath this new application.
    BUILDER.POLICY.prepare(target / ".pi", BUILDER.PRIVACY)
    synthetic_home = target / ".pi/portable-verification-home"
    BUILDER.POLICY.prepare(synthetic_home, BUILDER.PRIVACY)
    for relative in ("AppData/Local", "AppData/Roaming", "Temp"):
        (synthetic_home / relative).mkdir(parents=True, exist_ok=True)
    environment.update({"HOME": str(synthetic_home), "USERPROFILE": str(synthetic_home),
                        "APPDATA": str(synthetic_home / "AppData/Roaming"),
                        "LOCALAPPDATA": str(synthetic_home / "AppData/Local"),
                        "TEMP": str(synthetic_home / "Temp"), "TMP": str(synthetic_home / "Temp")})
    flags = subprocess.CREATE_NO_WINDOW
    neutral = os.environ["SystemRoot"]
    python = target / ".venv/Scripts/python.exe"
    started = time.monotonic()
    runtime = subprocess.run([str(python), "-I", "-B", "-c",
        "import sys;from pathlib import Path;from agent_platform.wechat_privacy import verify_private_directory;"
        "r=Path(sys.argv[1]);verify_private_directory(r);"
        "assert sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site;"
        "assert all(Path(p).resolve().is_relative_to(r) for p in sys.path);"
        "import Crypto,zstandard,PIL,pptx,yaml,sqlite3,faster_whisper;print('Portable Python, native modules and ACL passed')", str(target)],
        cwd=neutral, env=environment, creationflags=flags, text=True, capture_output=True, timeout=60)
    assert runtime.returncode == 0, runtime.stderr
    print(runtime.stdout.strip(), flush=True)
    self_test = subprocess.run([str(target / "Agent4Market.exe"), "--self-test"],
                              cwd=neutral, env=environment, creationflags=flags, timeout=90)
    assert self_test.returncode == 0, "Portable EXE self-test failed"
    assert port_free(), "Self-test leaked its server"
    print("Portable EXE self-test passed; starting without test-mode arguments", flush=True)
    app = subprocess.Popen([str(target / "Agent4Market.exe")], cwd=neutral, env=environment, creationflags=flags)
    bootstrap = None
    window_seen = False
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            assert app.poll() is None, "Portable EXE exited before readiness"
            try:
                with urlopen("http://127.0.0.1:8765/api/bootstrap", timeout=2) as response:
                    bootstrap = json.load(response)
                assert not bootstrap["model"]["configured"], "No real model configuration is allowed in this test"
                assert not bootstrap["tasks"], "No startup tasks are allowed"
                assert bootstrap["wechat"]["message_count"] == 0
                if bootstrap["desktop_runtime"]["status"] == "idle" and bootstrap["desktop_runtime"].get("heartbeat_at"):
                    break
            except OSError:
                pass
            time.sleep(0.4)
        assert bootstrap, "Portable workbench did not become ready"
        assert bootstrap["desktop_runtime"]["status"] == "idle", "Embedded AI core never reached idle"
        assert bootstrap["wechat"]["http_available"], "Private local HTTP identity guard was not ready"
        with urlopen("http://127.0.0.1:8765/", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "Claude Code" in page and "Codex CLI" in page
        time.sleep(3)
        print("Normal startup reached idle: model unconfigured, tasks 0, WeChat messages 0", flush=True)
    finally:
        if app.poll() is None:
            window_seen = HARNESS.close_window(app)
            try:
                app.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # Only the exact still-running child launched above may be terminated.
                subprocess.run([str(Path(neutral) / "System32/taskkill.exe"), "/PID", str(app.pid), "/T", "/F"],
                               creationflags=flags, capture_output=True, timeout=20)
                raise AssertionError("Portable main window did not close cleanly")
    assert window_seen and app.returncode == 0, "Portable native window was not observed/closed normally"
    assert port_free(), "Portable workbench port remained occupied after exit"
    duplicate = subprocess.run([str(python), "-I", "-B", str(ROOT / "scripts/build-windows-portable.py"),
        "--payload", str(target.parent / "Agent4Market-0.20.2-payload-20260910"),
        "--manifest", str(ROOT / "outputs/releases/0.20.2/install-manifest.json")],
        cwd=neutral, env=environment, creationflags=flags, text=True, capture_output=True, timeout=20)
    assert duplicate.returncode != 0 and "FileExistsError" in duplicate.stderr
    assert initial_data == {relative: HARNESS.digest(target / relative) for relative in initial_data}
    assert registry_before == HARNESS.registered(BUILDER.VERSION)
    BUILDER.POLICY.verify(target, BUILDER.VERSION, BUILDER.PRIVACY)
    result = {"status": "passed", "version": BUILDER.VERSION, "portable_root": str(target),
              "executable_sha256": HARNESS.digest(target / "Agent4Market.exe"),
              "verified_program_files": receipt["program_files"], "initialized_blank_files": len(initial_data),
              "private_acl": True, "embedded_python_isolated": True, "neutral_cwd_and_path": True,
              "self_test_exit": self_test.returncode, "normal_start_no_test_flags": True,
              "ai_core_status": bootstrap["desktop_runtime"]["status"], "ai_core_heartbeat": True,
              "native_window_observed": window_seen, "graceful_exit": app.returncode, "port_released": True,
              "model_configured": False, "startup_tasks": 0, "wechat_messages": 0,
              "synthetic_home_environment": True, "credentials_supplied": False,
              "real_model_task_submitted": False, "existing_target_refused": True,
              "initial_business_data_unchanged": True, "uninstall_registration_unchanged": True,
              "seconds": round(time.monotonic() - started, 2)}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "portable-acceptance.json").open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
