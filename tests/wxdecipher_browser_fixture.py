"""Isolated real HTTP backend for the optional browser smoke test.

Only synthetic records are created. No user configuration, schedules, model
transport, existing workbench data or native application is used.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import server
from agent_platform.app_updates import UpdateChecker, release_record
from wxdecipher_fixture import KEY_HEX, SELF, encrypt_fixture, make_database
from test_wxdecipher_extensions import FakeProcess, build_wal, encode_page, image_fixture, sqlite_wal_fixture, v2_fixture, wal_parts


def main():
    with tempfile.TemporaryDirectory(prefix="wxdecipher-browser-", dir=Path.home()) as directory:
        root = Path(directory)
        original = server.ROOT
        # Static application assets/definitions are read from source; every
        # mutable server path belongs to this test's disposable root instead.
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(original) and name not in {"UI_ROOT", "PROFILES", "PLUGINS"}:
                setattr(server, name, root / value.relative_to(original))
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.process_due_schedules = lambda: None
        server.APP_UPDATES = UpdateChecker("0.20.2", fetcher=lambda _etag: (release_record({
            "tag_name": "v0.20.2", "draft": False, "prerelease": False,
            "published_at": "2026-09-10T00:00:00Z", "name": "Synthetic release", "body": "Test only",
        }), None))
        server.model_settings_summary = lambda *args, **kwargs: {"configured": False, "providers": [], "status": "unconfigured"}
        server.search_settings_summary = lambda *args, **kwargs: {"configured": False}
        server.search_gateway_settings_summary = lambda *args, **kwargs: {"configured": False}
        server.mail_settings_summary = lambda *args, **kwargs: {"configured": False}
        server.desktop_runtime_summary = lambda: {"ai_core": {"running": False, "status": "stopped"}, "mode": "test"}

        class Handler(server.ControlHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/api/coding-assistants/detect":
                    self.send_json(200, {})
                    return
                if self.path.startswith("/api/a4/"):
                    self.send_json(200, {"success": True, "data": {"recommendations": []}})
                    return
                super().do_GET()

            def do_POST(self):
                if not self.path.startswith("/api/wechat/") and self.path != "/api/app-updates/check":
                    self.send_json(403, {"error": "Non-WeChat mutations are disabled in this fixture"})
                    return
                super().do_POST()

        source = make_database(root / "synthetic-plain.db")
        encrypted = encrypt_fixture(source, root / "message_0.db")
        wal_root = root / "wal-fixture"; wal_root.mkdir()
        wal_source, wal_file = sqlite_wal_fixture(wal_root, reserve=True, count=2, conversation="wxid_wal_synthetic_client")
        wal_plain = wal_source.read_bytes()
        wal_database = wal_root / "message_1.db"
        wal_database.write_bytes(b"".join(encode_page(wal_plain[pos:pos + 4096], pos // 4096 + 1) for pos in range(0, len(wal_plain), 4096)))
        header, frames = wal_parts(wal_file.read_bytes())
        wal_selected = wal_root / "message_1.db-wal"; wal_selected.write_bytes(build_wal(header, frames, encrypted=True))
        media_plain = root / "synthetic.png"; media_plain.write_bytes(image_fixture())
        media_dat = root / "synthetic.dat"; media_dat.write_bytes(v2_fixture(media_plain.read_bytes()))
        # UI/API exercise uses the production scanner but a synthetic memory
        # adapter. The native API is independently tested on a created helper.
        class SyntheticProcess(FakeProcess):
            def __init__(self, pid, created_at, **_):
                if pid != 424242 or created_at != "123456":
                    raise server.WechatStoreError("CAPTURE_SELECTION", "Synthetic process selection mismatch")
                super().__init__(("synthetic-only x'" + KEY_HEX + "' end").encode())
            def __enter__(self): return self
            def __exit__(self, *_): pass
        server.wxdecipher.wxdecipher_capture._WindowsProcess = SyntheticProcess
        server.wxdecipher.wxdecipher_capture.available = lambda: True
        def synthetic_processes(payload):
            if payload.get("ownership_confirmed") is not True or payload.get("capture_confirmed") is not True:
                raise server.WechatStoreError("AUTHORIZATION_REQUIRED", "Synthetic consent required")
            return {"processes": [{"name": "Weixin.exe（合成测试进程）", "process_id": 424242, "created_at": "123456"}], "message": "仅合成测试，未列出或读取真实微信。"}
        server.wxdecipher.wxdecipher_capture.list_processes = synthetic_processes
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

        def stop_on_input():
            sys.stdin.readline()
            http.shutdown()

        threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{http.server_port}", "root": str(root),
                          "database": str(encrypted), "key": KEY_HEX, "self_id": SELF,
                          "wal_database": str(wal_database), "wal": str(wal_selected),
                          "media": str(media_dat), "media_plain": str(media_plain)}), flush=True)
        try:
            http.serve_forever()
        finally:
            http.server_close()


if __name__ == "__main__":
    main()
