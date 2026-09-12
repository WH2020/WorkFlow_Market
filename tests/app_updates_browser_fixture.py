"""Production UI/HTTP and update state machine; synthetic releases only.

No remote network, CLI processes, credentials or installed application data.
The release opener only records a URL; it cannot launch a browser/installer.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.app_updates import UpdateChecker, UpdateError, release_record
from ui import server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("available", "current", "ahead", "unavailable", "network", "rate"), default="available")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="a4m-update-browser-", dir=Path.home()) as directory:
        root, original = Path(directory), server.ROOT
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(original) and name not in {"UI_ROOT", "PROFILES", "PLUGINS"}:
                setattr(server, name, root / value.relative_to(original))
        stats = {"fetches": 0, "opened": [], "installed": [], "cancelled": 0}

        def fetch(_etag):
            stats["fetches"] += 1
            time.sleep(0.4)  # A bounded synthetic delay to exercise nonblocking checks.
            failures = {
                "unavailable": ("SOURCE_UNAVAILABLE", "更新源暂不可用：仓库可能未公开、尚无正式 Release，或地址已变化。"),
                "network": ("NETWORK_ERROR", "无法连接 GitHub 更新服务，请检查网络后重试。"),
                "rate": ("RATE_LIMITED", "GitHub 暂时限制了更新请求，将在等待后重试。"),
            }
            if options.mode in failures:
                code, message = failures[options.mode]
                raise UpdateError(code, message, 120 if options.mode == "rate" else 0)
            version = {"available": "0.20.3", "current": "0.20.2", "ahead": "0.20.1"}[options.mode]
            return release_record({"tag_name": f"v{version}", "draft": False, "prerelease": False,
                "published_at": "2026-09-10T01:23:45Z", "name": "Agent4Market 合成测试版本",
                "body": "合成版本，仅用于更新交互验收。\n<script>window.syntheticReleaseExecuted = true</script>\n不会下载、安装或修改真实程序。"}), '"synthetic-etag"'

        server.APP_UPDATES = UpdateChecker("0.20.2", fetcher=fetch)
        open_release = server.APP_UPDATES.open_release
        server.APP_UPDATES.open_release = lambda tag: open_release(tag, opener=stats["opened"].append)
        class SyntheticInstallation:
            phase, revision = "idle", 0

            def operation(self):
                return nullcontext()

            def snapshot(self):
                return {"phase": self.phase, "revision": self.revision, "supported": True,
                        "can_start": server.APP_UPDATES.snapshot()["update_available"] and self.phase in {"idle", "cancelled"},
                        "can_cancel": self.phase == "downloading", "received_bytes": 50, "total_bytes": 100}

            def start(self, tag, confirmed):
                assert confirmed is True and tag == "v0.20.3"
                stats["installed"].append(tag)  # No downloader, installer or process can run.
                self.phase, self.revision = "downloading", self.revision + 1

            def cancel_update(self):
                stats["cancelled"] += 1
                self.phase, self.revision = "cancelled", self.revision + 1

        server.WINDOWS_UPDATES = SyntheticInstallation()
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.process_due_schedules = lambda: None
        server.model_settings_summary = lambda *_a, **_k: {"configured": False, "providers": [], "status": "unconfigured"}
        server.search_settings_summary = lambda *_a, **_k: {"configured": False}
        server.search_gateway_settings_summary = lambda *_a, **_k: {"configured": False}
        server.mail_settings_summary = lambda *_a, **_k: {"configured": False}
        server.desktop_runtime_summary = lambda: {"status": "offline", "label": "合成验收：智能核心未启动", "mode": "synthetic-update-test"}

        class Handler(server.ControlHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/fixture/stats":
                    self.send_json(200, stats)
                elif self.path == "/api/coding-assistants/detect":
                    self.send_json(200, {})
                elif self.path.startswith("/api/a4/"):
                    self.send_json(200, {"success": True, "data": {"recommendations": []}})
                else:
                    super().do_GET()

            def do_POST(self):
                if self.path.split("?", 1)[0] not in {"/api/app-updates/check", "/api/app-updates/open-release", "/api/app-updates/install", "/api/app-updates/cancel"}:
                    self.send_json(403, {"error": "Only synthetic update actions are permitted"})
                    return
                super().do_POST()

        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

        def stop_on_input():
            sys.stdin.readline()
            http.shutdown()

        threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{http.server_port}", "mode": options.mode}), flush=True)
        try:
            http.serve_forever()
        finally:
            http.server_close()


if __name__ == "__main__":
    main()
