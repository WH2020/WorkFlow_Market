"""Native-owned Windows updater worker. No user data or credentials are read.

The live desktop launches --spawn. A pinned copy then runs with the separately
staged embedded Python, waits for the old desktop tree, and only changes the
closed program-file plan. Startup recovery uses the same private job copy.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as wt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import socket


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


DIRECTORY = Path(__file__).resolve().parent
engine = module("update_engine", DIRECTORY / ("engine.py" if (DIRECTORY / "engine.py").is_file() else "windows_update_engine.py"))


def environment():
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME", "USERDOMAIN", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATH"}
    return {**{key: value for key, value in os.environ.items() if key.upper() in allowed}, "PYTHONDONTWRITEBYTECODE": "1"}


def validate_job(root, identifier, version):
    engine.require(os.name == "nt" and engine.JOB_ID.fullmatch(identifier) and engine.VERSION.fullmatch(version), "INVALID_JOB")
    root = Path(os.path.abspath(root))
    engine.require(root.parent == engine.load_policy("installer").profile_path() and root.name.startswith("Agent4Market-"), "INVALID_ROOT")
    engine.private_directory(root)
    job = root / ".pi/app-updates" / identifier
    engine.private_directory(job)
    info = engine.read_json(job / "job.json", 65536)
    engine.require(info["format"] == 1 and info["root"] == str(root) and info["job_id"] == identifier
                   and info["to_version"] == version and root.name == "Agent4Market-" + info["origin_version"], "INVALID_JOB")
    engine.require(engine.VERSION.fullmatch(info["from_version"]) and engine.SHA256.fullmatch(info["nonce"]), "INVALID_JOB")
    engine.require(set(info["helpers"]) == {"worker.py", "engine.py", "privacy.py", "installer_policy.py", "peer.py"}, "HELPER_CHANGED")
    for name, row in info["helpers"].items():
        engine.require(engine.file_matches(job, name, row), "HELPER_CHANGED")
    engine.require(engine.digest(engine.regular(job, "new-manifest.json")) == info["new_manifest_sha256"], "PLAN_CHANGED")
    stage = engine.load_policy("installer").target_path(str(root.parent / f"Agent4Market-{version}-test-{identifier}"), version, identifier)
    rows = engine.manifest(engine.read_json(job / "new-manifest.json"), version)
    # Standalone durable recovery closure; rollback does not need stage.
    for name, row in rows.items():
        if name.startswith(".venv/Scripts/"):
            engine.require(engine.file_matches(job / "recovery", name, row), "RECOVERY_RUNTIME_CHANGED")
    pointer = engine.regular(job.parent, "active")
    engine.require(pointer.read_text().splitlines() == [identifier, version], "INVALID_JOB")
    return root, job, stage, info


class ProcessTree:
    """Hold live process handles, not bare PIDs, before acknowledging handoff."""
    def __init__(self, parent_pid, launcher_pid, job, *, owned=False):
        peer = module("update_peer", Path(job) / "peer.py")
        self.api = peer._WindowsPeerAPI()
        kernel = self.api.kernel
        kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        kernel.WaitForSingleObject.restype = wt.DWORD
        kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
        kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
        kernel.TerminateProcess.restype = wt.BOOL
        self.owned, self.access = owned, 0x00100000 | 0x1000 | (1 if owned else 0)
        class Entry(ctypes.Structure):
            _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ctypes.c_size_t),
                        ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("priority", wt.LONG),
                        ("flags", wt.DWORD), ("exe", wt.WCHAR * 260)]
        kernel.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32FirstW.restype = wt.BOOL
        kernel.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.restype = wt.BOOL
        self.Entry = Entry
        self.handles = []
        self.live_pids = {parent_pid}
        self.pinned = {}
        self.excluded = {os.getpid(), launcher_pid}
        current = self.api.identity(kernel.GetCurrentProcess())
        try:
            parent = kernel.OpenProcess(self.access, False, parent_pid)
            engine.require(parent, "PARENT_NOT_RUNNING")
            self.handles.append(parent)
            self.identity = self.api.identity(parent)
            engine.require(self.identity[:2] == current[:2], "PARENT_IDENTITY_CHANGED")
            self.pinned[parent_pid] = parent
            self.refresh()
        except Exception:
            self.close()
            raise

    def refresh(self):
        kernel = self.api.kernel
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        engine.require(snapshot and snapshot != ctypes.c_void_p(-1).value, "PROCESS_SNAPSHOT_FAILED")
        try:
            entry = self.Entry()
            entry.size = ctypes.sizeof(self.Entry)
            pairs, good = [], kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while good:
                pairs.append((entry.pid, entry.parent))
                engine.require(len(pairs) <= 32768, "PROCESS_SNAPSHOT_FAILED")
                good = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        family = set(self.pinned)
        for _ in range(32):
            additions = {pid for pid, owner in pairs if owner in family and pid not in self.excluded} - family
            if not additions:
                break
            family.update(additions)
        engine.require(len(family) <= 512, "PROCESS_SNAPSHOT_FAILED")
        for pid in family - self.pinned.keys():
            handle = kernel.OpenProcess(self.access, False, pid)
            if not handle:
                engine.require(ctypes.get_last_error() == 87, "PROCESS_HANDLE_FAILED")
                continue
            self.handles.append(handle)
            selected = self.api.identity(handle)
            engine.require(selected[:2] == self.identity[:2] and selected[2] >= self.identity[2], "PROCESS_IDENTITY_CHANGED")
            self.pinned[pid] = handle
        self.live_pids = {pid for pid, handle in self.pinned.items() if kernel.WaitForSingleObject(handle, 0) != 0}

    def wait(self, timeout=120):
        deadline = time.monotonic() + timeout
        stable = 0
        while time.monotonic() < deadline:
            # Keep even exited-parent handles pinned while collecting late
            # descendants. Two empty snapshots must agree before replacement.
            self.refresh()
            stable = stable + 1 if not self.live_pids else 0
            if stable >= 2:
                return
            time.sleep(0.2)
        raise engine.UpdateFailure("PARENT_STILL_RUNNING")

    def terminate(self):
        engine.require(self.owned, "PROCESS_NOT_OWNED")
        self.refresh()
        # Handles remain usable even if the original desktop PID has exited.
        for handle in reversed(self.handles):
            if self.api.kernel.WaitForSingleObject(handle, 0) != 0:
                engine.require(self.api.kernel.TerminateProcess(handle, 2), "PROCESS_STOP_FAILED")
        self.wait(timeout=20)

    def close(self):
        for handle in self.handles:
            self.api.kernel.CloseHandle(handle)
        self.handles.clear()
        self.pinned.clear()


def port_free():
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", 8765)) != 0


def acknowledgement(job, name, nonce):
    path = Path(job) / name
    return path.is_file() and engine.read_json(path, 4096).get("nonce") == nonce


def spawn(root, job, stage, info, parent_pid, recover):
    python = engine.regular(job / "recovery", ".venv/Scripts/python.exe")
    command = [str(python), "-I", "-B", str(job / "worker.py"), "--run", "--root", str(root), "--job", job.name,
               "--version", info["to_version"], "--parent-pid", str(parent_pid), "--launcher-pid", str(os.getpid())]
    if recover:
        command.append("--recover")
    process = subprocess.Popen(command, cwd=os.environ["SystemRoot"], env=environment(), creationflags=subprocess.CREATE_NO_WINDOW,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if acknowledgement(job, "ready.json", info["nonce"]):
            receipt = engine.read_json(job / "ready.json", 4096)
            if receipt["pid"] == process.pid and process.poll() is None:
                return
        engine.require(process.poll() != 3, "UPDATE_ALREADY_RUNNING")
        engine.require(process.poll() is None, "WORKER_NOT_READY")
        time.sleep(0.1)
    raise engine.UpdateFailure("WORKER_NOT_READY")


def record_result(root, job, info, status, code=None):
    result = {"status": status, "from_version": info["from_version"], "to_version": info["to_version"],
              "code": code, "job_id": job.name, "program_only": True, "data_migrated": False}
    engine.write_json(job / "result.json", result)
    engine.write_json(job.parent / "last-result.json", result)
    pointer = engine.regular(job.parent, "active")
    engine.require(pointer.read_text().splitlines() == [job.name, info["to_version"]], "INVALID_JOB")
    engine.durable_replace(pointer, job / "finished-active")


def update_display_version(root, info):
    import winreg
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Agent4Market-" + info["origin_version"]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            if os.path.normcase(winreg.QueryValueEx(key, "InstallLocation")[0]) != os.path.normcase(str(root)):
                return
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, info["to_version"])
    except OSError:
        pass  # Registration is cosmetic; never create or redirect an uninstall key.


def start_application(root, trial=None):
    engine.require(port_free(), "PORT_BUSY")
    command = [str(engine.regular(root, "Agent4Market.exe"))]
    if trial:
        command.extend(["--update-trial", trial["job_id"], trial["nonce"]])
    return subprocess.Popen(command, cwd=os.environ["SystemRoot"], env=environment(),
                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


def wait_trial(process, job, info, tree, timeout=120):
    deadline, ready_since = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        tree.refresh()
        engine.require(process.poll() is None, "STARTUP_TRIAL_FAILED")
        if acknowledgement(job, "launch-ready.json", info["nonce"]) and acknowledgement(job, "core-ready.json", info["nonce"]):
            desktop = engine.read_json(job / "launch-ready.json", 4096)
            core = engine.read_json(job / "core-ready.json", 4096)
            engine.require(desktop.get("pid") == process.pid, "STARTUP_TRIAL_FAILED")
            engine.require(core.get("pid") in tree.live_pids and core.get("pid") != process.pid, "STARTUP_TRIAL_FAILED")
            ready_since = ready_since or time.monotonic()
            if time.monotonic() - ready_since >= 3:
                return
        time.sleep(0.2)
    raise engine.UpdateFailure("STARTUP_TRIAL_FAILED")


def stop_trial(process, job, tree=None):
    if tree is not None:
        try:
            tree.terminate()
            process.wait(timeout=10)
            engine.require(port_free(), "PORT_BUSY")
            return
        finally:
            tree.close()
    if process.poll() is not None:
        # A dead desktop with an orphan listener is never safe to overwrite.
        engine.require(port_free(), "PORT_BUSY")
        return
    tree = ProcessTree(process.pid, 0, job, owned=True)
    try:
        # This tree was launched solely for the frozen, no-business trial.
        # Popen and tree handles prevent PID reuse during the bounded stop.
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                       creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        process.wait(timeout=10)
        tree.wait(timeout=20)
        engine.require(port_free(), "PORT_BUSY")
    finally:
        tree.close()


def notify_failure(code):
    # No filenames, account identifiers, secret/config values or raw exceptions.
    text = "更新未完成（" + code + "）。程序文件未被进一步替换。请关闭工作台后重新启动以重试恢复；不要删除更新备份目录。"
    ctypes.windll.user32.MessageBoxW(None, text, "Agent4Market 更新", 0x10)


def run(root, job, stage, info, parent_pid, launcher_pid, recover):
    with engine.exclusive_job(job):
        tree = ProcessTree(parent_pid, launcher_pid, job)
        try:
            engine.write_json(job / "ready.json", {"pid": os.getpid(), "nonce": info["nonce"]})
            tree.wait()
        finally:
            tree.close()
        engine.require(port_free(), "PORT_BUSY")
        has_journal = (job / "journal.json").is_file()
        if has_journal and engine.read_json(job / "journal.json")["phase"] == "complete":
            engine.verify_outcome(root, job, "new")
            record_result(root, job, info, "complete")
            start_application(root)
            return
        if recover or not acknowledgement(job, "stopped.json", info["nonce"]):
            if has_journal:
                engine.rollback(root, job)
            else:
                engine.require(engine.digest(root / "runtime/install-manifest.json") == info["old_manifest_sha256"], "PROGRAM_MODIFIED")
                engine.verify_programs(root, engine.manifest(engine.read_json(root / "runtime/install-manifest.json"), info["from_version"]))
            record_result(root, job, info, "rolled_back", "INTERRUPTED")
            start_application(root)
            return
        trial, trial_tree = None, None
        try:
            engine.require(not has_journal and engine.digest(root / "runtime/install-manifest.json") == info["old_manifest_sha256"], "PROGRAM_MODIFIED")
            old = engine.read_json(root / "runtime/install-manifest.json")
            new = engine.read_json(job / "new-manifest.json")
            engine.backup(root, stage, job, old, new)
            engine.apply(root, stage, job)
            # Explicit no-data/no-scheduler probe: do not open/migrate live SQLite.
            tested = subprocess.Popen([str(root / "Agent4Market.exe"), "--update-self-test"], cwd=os.environ["SystemRoot"],
                                      env=environment(), creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            probe_tree = ProcessTree(tested.pid, 0, job, owned=True)
            try:
                code = tested.wait(timeout=120)
                if code != 0:
                    probe_tree.terminate()
                else:
                    probe_tree.wait(timeout=15)
            except subprocess.TimeoutExpired:
                probe_tree.terminate()
                tested.wait(timeout=10)
                code = 2
            except Exception:
                probe_tree.terminate()
                raise
            finally:
                probe_tree.close()
            engine.require(code == 0 and port_free(), "STARTUP_PROBE_FAILED")
            trial = start_application(root, info)
            trial_tree = ProcessTree(trial.pid, 0, job, owned=True)
            wait_trial(trial, job, info, trial_tree)
            engine.verify_outcome(root, job, "new")
            journal = engine.read_json(job / "journal.json")
            journal["phase"] = "complete"
            engine.write_json(job / "journal.json", journal)
        except Exception as error:
            if trial is not None:
                stop_trial(trial, job, trial_tree)
            # Never recover while a failed probe still owns the shared port.
            engine.require(port_free(), "PORT_BUSY")
            if (job / "journal.json").is_file():
                engine.rollback(root, job)
            else:
                engine.require(engine.digest(root / "runtime/install-manifest.json") == info["old_manifest_sha256"], "PROGRAM_MODIFIED")
            record_result(root, job, info, "rolled_back", str(error) if isinstance(error, engine.UpdateFailure) else "UPDATE_FAILED")
        else:
            trial_tree.close()
            update_display_version(root, info)
            record_result(root, job, info, "complete")
            return  # The verified desktop is already running; release its gate.
        start_application(root)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--spawn", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--root", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--launcher-pid", type=int, default=0)
    args = parser.parse_args()
    try:
        root, job, stage, info = validate_job(args.root, args.job, args.version)
        if args.spawn:
            spawn(root, job, stage, info, args.parent_pid, args.recover)
        else:
            run(root, job, stage, info, args.parent_pid, args.launcher_pid, args.recover)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, engine.UpdateFailure) else "UPDATE_FAILED"
        if code == "UPDATE_ALREADY_RUNNING":
            return 3
        if args.run:
            notify_failure(code)
        else:
            print("Update handoff refused: " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
