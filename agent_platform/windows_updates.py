"""User-confirmed update preparation; downloading never changes live programs."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, ProxyHandler, build_opener

from .app_updates import UpdateError, _NoRedirect, fetch_latest, REPOSITORY
from . import windows_update_engine as engine
from . import windows_update_signatures as signatures


MESSAGES = {
    "WINDOWS_ONLY": "一键更新仅支持 Windows x64 安装版；其他平台请查看发布页。",
    "INSTALLED_DESKTOP_REQUIRED": "请从正式安装的桌面程序使用一键更新。",
    "UPDATE_PROTOCOL_UNSUPPORTED": "此版本不具备兼容的一键更新协议，请按发布说明手动安装。",
    "DATA_FORMAT_CHANGED": "新版需要变更数据格式，不能仅更新程序；请按发布说明手动升级。",
    "BUSY": "仍有任务、对话或文件操作未结束，请完成后重试更新。",
    "UPDATING": "正在准备更新，暂不接受其他操作。可在更新面板取消。",
    "PROGRAM_MODIFIED": "程序文件与安装清单不一致，已停止更新；你的改动和数据未被覆盖。",
    "DOWNLOAD_FAILED": "下载失败或连接超时，当前程序未改动，可重试。",
    "DOWNLOAD_HASH_MISMATCH": "下载文件未通过大小和 SHA-256 校验，已停止更新。",
    "UNTRUSTED_DOWNLOAD": "下载地址不符合固定 GitHub 发布源，已拒绝。",
    "NOT_ENOUGH_SPACE": "磁盘空间不足，已停止更新；请留出安装暂存和程序备份空间。",
    "CANCELLED": "更新已取消，当前程序和数据未改动。",
    "STAGE_EXISTS": "更新暂存目录已存在，已停止；不会覆盖已有目录。",
    "INSTALLER_FAILED": "新版暂存安装失败，当前程序未改动。",
    "STALE_SELECTION": "发布版本或附件已变化，请重新检查更新。",
    "NATIVE_STOP_FAILED": "智能核心未能安全退出，未更新程序；请关闭诊断窗口后重试。",
    "UPDATE_FAILED": "更新未完成，当前程序未被覆盖；请查看更新结果或重新检查。",
    "PERSISTENCE_INITIALIZATION_FAILED": "新版已启动，但持久化服务初始化失败；工作台已解除更新锁，请重启后检查存储状态。",
    "SIGNING_KEY_REQUIRED": "此安装尚未配置可信发布公钥，一键安装保持禁用；请安装维护者提供的签名更新版本。",
    "INVALID_SIGNATURE": "更新包未通过发布者签名验证，已拒绝执行；当前程序和数据未改动。",
}


def public_failure(error):
    code = str(error) if isinstance(error, engine.UpdateFailure) else "UPDATE_FAILED"
    return {"code": code if code in MESSAGES else "UPDATE_FAILED", "message": MESSAGES.get(code, MESSAGES["UPDATE_FAILED"])}


def neutral_environment():
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME", "USERDOMAIN", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATH"}
    return {**{key: value for key, value in os.environ.items() if key.upper() in allowed}, "PYTHONDONTWRITEBYTECODE": "1"}


def download_asset(row, target, cancel, progress, *, opener=None):
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{row['id']}"
    open_request = opener or build_opener(ProxyHandler(), _NoRedirect()).open
    headers = {"Accept": "application/octet-stream", "Accept-Encoding": "identity", "User-Agent": "Agent4Market-update-download", "X-GitHub-Api-Version": "2026-03-10"}
    response = None
    try:
        try:
            response = open_request(Request(url, headers=headers), timeout=15)
        except HTTPError as error:
            try:
                location = error.headers.get("Location", "")
                parsed = urlparse(location)
                engine.require(error.code == 302 and len(location) <= 8192 and parsed.scheme == "https"
                               and parsed.hostname == "release-assets.githubusercontent.com" and parsed.port in (None, 443)
                               and not parsed.username and not parsed.password and not parsed.fragment, "UNTRUSTED_DOWNLOAD")
            finally:
                error.close()
            url = location
            response = open_request(Request(url, headers={"Accept": "application/octet-stream", "Accept-Encoding": "identity", "User-Agent": "Agent4Market-update-download"}), timeout=15)
        with response:
            engine.require(response.status == 200 and response.geturl() == url, "UNTRUSTED_DOWNLOAD")
            engine.require(response.headers.get("Content-Encoding", "identity").lower() in {"", "identity"}, "UNTRUSTED_DOWNLOAD")
            length = response.headers.get("Content-Length")
            engine.require(length is None or length == str(row["bytes"]), "DOWNLOAD_HASH_MISMATCH")
            count, sha, deadline = 0, hashlib.sha256(), time.monotonic() + 1200
            with Path(target).open("xb") as output:
                while True:
                    engine.require(not cancel.is_set(), "CANCELLED")
                    engine.require(time.monotonic() < deadline, "DOWNLOAD_FAILED")
                    block = response.read(min(1024 * 1024, row["bytes"] + 1 - count))
                    if not block:
                        break
                    count += len(block)
                    engine.require(count <= row["bytes"], "DOWNLOAD_HASH_MISMATCH")
                    sha.update(block)
                    output.write(block)
                    progress(count, row["bytes"])
                output.flush()
                os.fsync(output.fileno())
            engine.require(count == row["bytes"] and sha.hexdigest() == row["sha256"], "DOWNLOAD_HASH_MISMATCH")
    except (HTTPError, URLError, TimeoutError, OSError):
        raise engine.UpdateFailure("DOWNLOAD_FAILED") from None


class WindowsUpdateManager:
    def __init__(self, root, checker, *, idle=lambda: True, downloader=download_asset, fetcher=fetch_latest, pending=None, on_committed=lambda: None):
        self.root, self.checker, self.idle = Path(root), checker, idle
        self.downloader, self.fetcher = downloader, fetcher
        self.lock, self.cancel = threading.RLock(), threading.Event()
        self.frozen, self.operations, self.worker, self.job = False, 0, None, None
        self.revision = 0
        self.state = {"phase": "idle", "received_bytes": 0, "total_bytes": 0, "error": None, "job_id": None}
        self.pending, self.on_committed = pending, on_committed
        if pending:
            engine.require(engine.JOB_ID.fullmatch(pending), "INVALID_JOB")
            self.frozen = True
            self.state.update(phase="starting", job_id=pending)

    def _refresh_pending(self):
        if not self.pending or (self.root / ".pi/app-updates/active").exists():
            return
        result = engine.read_json(self.root / ".pi/app-updates/last-result.json", 4096)
        engine.require(result.get("job_id") == self.pending and result.get("status") == "complete", "UPDATING")
        # Serialized with all operations. SQLite initialization is deferred
        # until the worker durably commits, never during the rollback trial.
        try:
            if self.on_committed() is False:
                self.state["error"] = public_failure(engine.UpdateFailure("PERSISTENCE_INITIALIZATION_FAILED"))
        except Exception:
            self.state["error"] = public_failure(engine.UpdateFailure("PERSISTENCE_INITIALIZATION_FAILED"))
        self.pending, self.frozen = None, False
        self.state.update(phase="complete")
        self.revision += 1

    def capability(self):
        try:
            engine.require(os.name == "nt", "WINDOWS_ONLY")
            engine.require(os.environ.get("AGENT4MARKET_UPDATE_CONTROL") and os.environ.get("AGENT4MARKET_DESKTOP_PID"), "INSTALLED_DESKTOP_REQUIRED")
            engine.require(os.environ.get("AGENT4MARKET_UPDATE_PROBE") != "1", "INSTALLED_DESKTOP_REQUIRED")
            install = engine.installation(self.root)
            signatures.public_key(self.root)
            engine.require(engine.digest(engine.regular(self.root, signatures.TRUST_FILE)) == install["rows"][signatures.TRUST_FILE]["sha256"], "PROGRAM_MODIFIED")
            return install, None
        except Exception as error:
            return None, public_failure(error)

    def snapshot(self):
        with self.lock:
            self._refresh_pending()
            install, error = self.capability()
            latest = self.checker.snapshot()
            previous = None
            if install:
                try:
                    result = engine.read_json(self.root / ".pi/app-updates/last-result.json", 4096)
                    if result.get("status") in {"complete", "rolled_back"}:
                        previous = {key: result.get(key) for key in ("status", "from_version", "to_version")}
                except (OSError, ValueError):
                    pass
            return {**copy.deepcopy(self.state), "revision": self.revision, "previous_result": previous, "supported": install is not None,
                    "unavailable_reason": error["message"] if error else None,
                    "can_start": bool(install and not self.frozen and latest["update_available"] and latest["latest"].get("windows_assets")),
                    "can_cancel": self.frozen and self.state["phase"] in {"downloading", "staging", "verifying"},
                    "preserves_data": True, "confirmation_required": True}

    @contextmanager
    def operation(self):
        with self.lock:
            self._refresh_pending()
            engine.require(not self.frozen, "UPDATING")
            self.operations += 1
        try:
            yield
        finally:
            with self.lock:
                self.operations -= 1

    def _set(self, **values):
        with self.lock:
            self.state.update(values)
            self.revision += 1

    def start(self, tag, confirmed):
        with self.lock:
            engine.require(confirmed is True, "CONFIRMATION_REQUIRED")
            if self.frozen:
                return self.snapshot()  # Duplicate clicks cannot start a second worker.
            install, error = self.capability()
            engine.require(install, error["code"] if error else "INSTALLED_DESKTOP_REQUIRED")
            latest = self.checker.snapshot()
            engine.require(latest["update_available"] and latest["latest"]["tag"] == tag and latest["latest"].get("windows_assets"), "STALE_SELECTION")
            engine.require(self.operations == 0 and self.idle(), "BUSY")
            self.frozen = True
            self.cancel.clear()
            identifier = secrets.token_hex(16)
            self.state = {"phase": "downloading", "received_bytes": 0, "total_bytes": 0, "error": None, "job_id": identifier}
            self.revision += 1
            try:
                self.job = engine.create_job(self.root, identifier)
                self.worker = threading.Thread(target=self._prepare, args=(install, copy.deepcopy(latest["latest"])), name="windows-update-prepare", daemon=True)
                self.worker.start()
            except Exception:
                self.frozen = False
                self.state["phase"] = "error"
                raise
            return self.snapshot()

    def _prepare(self, install, selected):
        try:
            fresh, _etag = self.fetcher(None)
            engine.require(fresh and fresh["tag"] == selected["tag"] and fresh.get("windows_assets") == selected["windows_assets"], "STALE_SELECTION")
            rows = selected["windows_assets"]
            old_manifest = engine.read_json(self.root / "runtime/install-manifest.json")
            engine.verify_programs(self.root, engine.manifest(old_manifest, install["version"]))
            engine.require(shutil.disk_usage(self.root).free >= rows["manifest"]["bytes"] + 64 * 1024 ** 2, "NOT_ENOUGH_SPACE")
            self.downloader(rows["signature"], self.job / "signature.json", self.cancel, lambda *_args: None)
            signatures.verify(self.root, self.job / "signature.json", selected["version"], rows)
            for key, name in (("manifest", "new-manifest.json"), ("installer", "Setup.exe")):
                self.downloader(rows[key], self.job / name, self.cancel,
                                lambda received, total: self._set(received_bytes=received, total_bytes=total))
                if key == "manifest":
                    new_manifest = engine.read_json(self.job / name)
                    engine.manifest(new_manifest, selected["version"])
                    operations = engine.make_plan(old_manifest, new_manifest)
                    needed = (rows["installer"]["bytes"] + sum(row["bytes"] for row in new_manifest["files"])
                              + sum(op["old"]["bytes"] for op in operations if op["old"])
                              + sum(op["old"]["bytes"] for op in operations if op["old"] and not op["new"])
                              + max([op["new"]["bytes"] for op in operations if op["new"]] or [0])
                              + sum(row["bytes"] for row in new_manifest["files"] if row["path"].startswith(".venv/Scripts/"))
                              + 256 * 1024 ** 2)
                    engine.require(shutil.disk_usage(self.root).free >= needed, "NOT_ENOUGH_SPACE")
            engine.require(not self.cancel.is_set(), "CANCELLED")
            stage = engine.load_policy("installer").target_path(str(self.root.parent / f"Agent4Market-{selected['version']}-test-{self.job.name}"), selected["version"], self.job.name)
            engine.require(not stage.exists(), "STAGE_EXISTS")
            self._set(phase="staging")
            result = subprocess.run([str(self.job / "Setup.exe"), "/S", "/VERIFY=" + self.job.name],
                                    cwd=os.environ["SystemRoot"], env=neutral_environment(), creationflags=subprocess.CREATE_NO_WINDOW,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            engine.require(result.returncode == 0, "INSTALLER_FAILED")
            engine.require(not self.cancel.is_set(), "CANCELLED")
            self._set(phase="verifying")
            engine.require(engine.digest(engine.regular(stage, "runtime/install-manifest.json")) == rows["manifest"]["sha256"], "DOWNLOAD_HASH_MISMATCH")
            engine.verify_programs(stage, engine.manifest(new_manifest, selected["version"]))
            engine.require(not self.cancel.is_set(), "CANCELLED")
            # Recovery must not depend on the replaceable application runtime
            # or on NSIS's staging writes surviving a later interruption.
            recovery = engine.private_directory(self.job / "recovery", create=True)
            for name, row in engine.manifest(new_manifest, selected["version"]).items():
                if name.startswith(".venv/Scripts/"):
                    engine.require(not self.cancel.is_set(), "CANCELLED")
                    engine.mkdirs(recovery, str(Path(name).parent).replace("\\", "/"))
                    engine.copy_new(engine.regular(stage, name), recovery / name, row)
            # Copy the complete trusted helper closure BEFORE changing any program.
            helpers = {"worker.py": "agent_platform/windows_update_worker.py", "engine.py": "agent_platform/windows_update_engine.py",
                       "privacy.py": "agent_platform/wechat_privacy.py", "installer_policy.py": "scripts/windows-installer-bootstrap.py",
                       "peer.py": "agent_platform/local_http_security.py"}
            copied = {}
            for name, relative in helpers.items():
                row = install["rows"][relative]
                engine.copy_new(engine.regular(self.root, relative), self.job / name, row)
                copied[name] = {"bytes": row["bytes"], "sha256": row["sha256"]}
            plan = {"format": 1, "root": str(self.root), "job_id": self.job.name, "from_version": install["version"],
                    "to_version": selected["version"], "origin_version": install["origin_version"], "helpers": copied,
                    "old_manifest_sha256": engine.digest(self.root / "runtime/install-manifest.json"),
                    "new_manifest_sha256": rows["manifest"]["sha256"], "installer_sha256": rows["installer"]["sha256"],
                    "nonce": secrets.token_hex(32)}
            engine.write_json(self.job / "job.json", plan)
            with self.lock:
                engine.require(self.operations == 0 and self.idle(), "BUSY")
                engine.require(not self.cancel.is_set(), "CANCELLED")
                # Native polling uses a separate control capability, not /health's public token.
                engine.write_atomic(self.job.parent / "active", (self.job.name + "\n" + selected["version"] + "\n").encode())
                self.state.update(phase="ready", target_version=selected["version"])
                self.revision += 1
        except Exception as error:
            with self.lock:
                self.frozen = False
                self.state.update(phase="cancelled" if self.cancel.is_set() else "error", error=public_failure(error))
                self.revision += 1

    def cancel_update(self):
        with self.lock:
            engine.require(self.state["phase"] in {"downloading", "staging", "verifying"}, "INVALID_PHASE")
            self.cancel.set()
            return self.snapshot()

    def native_request(self):
        with self.lock:
            if self.state["phase"] != "ready":
                return "none\n"
            return self.job.name + "\n" + self.state["target_version"] + "\n"

    def native_abort(self):
        with self.lock:
            if self.state["phase"] == "ready":
                self.state.update(phase="error", error=public_failure(engine.UpdateFailure("NATIVE_STOP_FAILED")))
                self.revision += 1
                self.frozen = False
                # Only our own pointer; no program/backup/user-data deletion.
                pointer = engine.regular(self.job.parent, "active")
                engine.require(pointer.read_text().splitlines()[0] == self.job.name, "INVALID_JOB")
                engine.durable_replace(pointer, self.job / "aborted-active")

    def native_stop(self):
        with self.lock:
            engine.require(self.state["phase"] == "ready" and self.operations == 0 and self.idle(), "BUSY")
            self.state["phase"] = "installing"
            self.revision += 1
