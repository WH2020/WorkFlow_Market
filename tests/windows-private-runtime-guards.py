"""Destructive-free launch rejection checks for a newly built private package."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.runtime_root.resolve()
    assert (root / "runtime/private-runtime.marker").read_text().strip() == "Agent4Market private runtime v1"
    executable = root / "Agent4Market.exe"
    assert executable.is_file()
    detached = root / "outputs/verification/detached-runtime-guard"
    detached.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(executable, detached / executable.name)

    def check_rejected(path: Path) -> None:
        process = subprocess.run([str(path), "--self-test"], cwd=root, timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert process.returncode == 2, process.returncode

    # Even with a complete runtime in both CWD and an ancestor, no fallback.
    check_rejected(detached / executable.name)
    # A malformed package that satisfies the root shape cannot use system Python.
    for name in ("scripts/start-windows.ps1", "node_modules/.bin/pi.CMD", "profiles/sales-director/profile.json"):
        target = detached / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    (detached / "runtime").mkdir()
    shutil.copyfile(root / "runtime/private-runtime.marker", detached / "runtime/private-runtime.marker")
    (detached / "ui").mkdir()
    (detached / "ui/server.py").write_text("from pathlib import Path\nPath('unexpected-python-fallback').write_text('FAIL')\n", encoding="utf-8")
    check_rejected(detached / executable.name)
    assert not (detached / "unexpected-python-fallback").exists()
    # Refuse any pre-existing listener, not only an Agent4Market-looking server.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 8765))
        listener.listen(1)
        check_rejected(executable)
        assert listener.getsockname() == ("127.0.0.1", 8765)
    result = {"status": "passed", "detached_exe_rejected": True,
              "missing_private_python_rejected_without_fallback": True, "foreign_listener_not_adopted": True}
    (detached / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
