"""Real loopback HTTP contract tests, isolated from all user/workbench data."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ui import server
from agent_platform.local_http_security import LocalAccessError
from tests.wxdecipher_fixture import KEY_HEX, SELF, encrypt_fixture, make_database
from tests.test_wxdecipher_extensions import image_fixture, v2_fixture, sqlite_wal_fixture


class WxDecipherHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wxdecipher-test-", dir=Path.home())
        self.root = Path(self.temporary.name)
        self.source = make_database(self.root / "synthetic.db")
        self.logs = []
        logs = self.logs

        class TestHandler(server.ControlHandler):
            def log_message(self, format, *args):
                logs.append(format % args)

        self.root_patch = patch.object(server, "ROOT", self.root)
        self.profile_patch = patch.object(server, "ACTIVE_PROFILE_ID", "sales-director")
        self.isolation_patch = patch.object(server, "wechat_http_supported", return_value=True)
        self.root_patch.start(); self.profile_patch.start(); self.isolation_patch.start()
        self.http = server.ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(timeout=3)
        self.isolation_patch.stop(); self.profile_patch.stop(); self.root_patch.stop()
        self.temporary.cleanup()

    def request(self, route, payload=None, *, body=None, headers=None, token=True):
        request_headers = {"Content-Type": "application/json" if body is None else "application/octet-stream"}
        if token:
            request_headers["X-Director-Token"] = server.SERVER_TOKEN
        request_headers.update(headers or {})
        content = json.dumps(payload or {}, ensure_ascii=False).encode() if body is None else body
        client = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=15)
        try:
            client.request("POST", route, body=content, headers=request_headers)
            response = client.getresponse()
            result = response.read()
            if response.headers.get("X-WXDecipher-Media"):
                return response.status, {**json.loads(response.headers["X-WXDecipher-Media"]), "binary": result}
            return response.status, json.loads(result.decode("utf-8"))
        finally:
            client.close()

    def create(self):
        status, result = self.request("/api/wechat/decipher/sessions", {
            "ownership_confirmed": True, "snapshot_confirmed": True,
            "account_label": "HTTP 合成测试", "self_username": SELF,
        })
        self.assertEqual(201, status, result)
        return result["session_id"]

    def upload(self, session, source=None):
        return self.request("/api/wechat/decipher/upload", body=(source or self.source).read_bytes(),
                            headers={"X-WXDecipher-Session": session, "X-File-Name": "message_0.db"})

    def test_real_http_decrypt_browse_export_and_secret_redaction(self):
        encrypted = encrypt_fixture(self.source, self.root / "encrypted.db")
        session = self.create()
        status, uploaded = self.upload(session, encrypted)
        self.assertEqual(201, status, uploaded)
        with patch("ui.server.task_runtime_selection", side_effect=AssertionError("Decoding/export must not select a model")):
            status, result = self.request("/api/wechat/decipher/run", {"session_id": session, "key": KEY_HEX})
            self.assertEqual(200, status, result)
            self.assertEqual(3, result["decipher"]["messages"])
            status, conversations = self.request("/api/wechat/query", {"action": "conversations"})
            self.assertEqual(200, status)
            identity = conversations["rows"][0]["conversation_id"]
            status, preview = self.request("/api/wechat/query", {"action": "messages", "conversation_id": identity})
            self.assertEqual(3, preview["returned"])
            status, exported = self.request("/api/wechat/export", {"conversation_ids": [identity], "format": "json"})
            self.assertEqual(200, status, exported)
            self.assertEqual(3, len(json.loads(exported["content"])))
        self.assertFalse((self.root / "data/wechat/decipher" / session).exists())
        self.assertNotIn(KEY_HEX, json.dumps([result, exported, self.logs], ensure_ascii=False))

    def test_structured_upload_also_uses_private_expiring_staging(self):
        content = json.dumps({"conversation": "synthetic-only", "content": "本机合成结构化测试"}, ensure_ascii=False).encode("utf-8")
        with patch("ui.server.import_wechat_export", wraps=server.import_wechat_export) as imported:
            status, result = self.request("/api/wechat/import", body=content, headers={
                "X-File-Name": "synthetic.jsonl", "X-Wechat-Ownership": "confirmed",
            })
            self.assertEqual(201, status, result)
            source = imported.call_args.args[1]
            self.assertTrue(source.is_relative_to(self.root / "data/wechat/decipher"))
            self.assertFalse(source.exists())
        self.assertEqual([], list((self.root / "data/wechat/decipher").iterdir()))

    def test_structured_upload_without_ownership_does_not_create_staging(self):
        status, result = self.request("/api/wechat/import", body=b"{}", headers={"X-File-Name": "synthetic.jsonl"})
        self.assertEqual(400, status, result)
        self.assertEqual("AUTHORIZATION_REQUIRED", result["code"])
        self.assertFalse((self.root / "data").exists())

    def test_mutations_require_token_profile_and_explicit_consent(self):
        status, _ = self.request("/api/wechat/decipher/sessions", token=False)
        self.assertEqual(403, status)
        status, result = self.request("/api/wechat/decipher/sessions")
        self.assertEqual(400, status)
        self.assertEqual("AUTHORIZATION_REQUIRED", result["code"])
        with patch.object(server, "ACTIVE_PROFILE_ID", "product-director"):
            status, result = self.request("/api/wechat/decipher/sessions", {"ownership_confirmed": True, "snapshot_confirmed": True})
            self.assertEqual(400, status)
            self.assertEqual("PROFILE_FORBIDDEN", result["code"])
        self.assertFalse((self.root / "data").exists())

    def test_other_os_user_cannot_bootstrap_token_or_trigger_any_operation(self):
        with patch("ui.server.require_local_user", side_effect=LocalAccessError("Synthetic different OS user")), \
                patch("ui.server.process_due_schedules", side_effect=AssertionError("Unauthenticated GET has no side effects")), \
                patch.object(server.wxdecipher.wxdecipher_capture, "list_processes", side_effect=AssertionError("No process enumeration")):
            for route in ("/api/bootstrap", "/api/wechat", "/api/tasks"):
                client = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
                try:
                    client.request("GET", route)
                    response = client.getresponse(); data = response.read()
                    self.assertEqual(403, response.status)
                    self.assertNotIn(server.SERVER_TOKEN.encode(), data)
                finally:
                    client.close()
            status, result = self.request("/api/wechat/decipher/processes", {"ownership_confirmed": True, "capture_confirmed": True})
            self.assertEqual(403, status, result)
            self.assertEqual("LOCAL_USER_REQUIRED", result["code"])
        self.assertFalse((self.root / "data").exists())

    def test_unsupported_user_isolation_rejects_sensitive_routes_before_body(self):
        with patch("ui.server.wechat_http_supported", return_value=False), \
                patch.object(server.wxdecipher.wxdecipher_capture, "list_processes", side_effect=AssertionError("No enumeration")):
            for route in ("processes", "sessions", "capture-consent", "discover", "import-discovered", "media", "run"):
                status, result = self.request("/api/wechat/decipher/" + route, body=b"")
                self.assertEqual(403, status, result)
                self.assertEqual("LOCAL_ISOLATION_UNSUPPORTED", result["code"])
        self.assertFalse((self.root / "data").exists())

    def test_new_stylesheet_is_served_but_private_staging_is_not(self):
        for route, expected in (("/wxdecipher.css", 200), ("/data/wechat/decipher/session.json", 404)):
            client = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
            try:
                client.request("GET", route)
                response = client.getresponse()
                self.assertEqual(expected, response.status)
                content = response.read()
                if expected == 200:
                    self.assertIn("text/css", response.headers["Content-Type"])
                    self.assertIn(b".wxdecipher-fields", content)
            finally:
                client.close()

    def test_content_types_and_bad_export_payloads_are_controlled_errors(self):
        status, _ = self.request("/api/wechat/decipher/upload", {"path": "C:/not-an-upload.db"})
        self.assertEqual(415, status)
        status, _ = self.request("/api/wechat/decipher/run", body=b"not-json")
        self.assertEqual(415, status)
        for invalid in (None, [], {}, 42):
            status, _ = self.request("/api/wechat/export", {"format": invalid, "conversation_ids": []})
            self.assertEqual(400, status)

    def test_database_discovery_routes_require_token_and_forward_only_json_payloads(self):
        response = {"platform": "windows", "platform_label": "Windows", "groups": [], "message": "synthetic"}
        with patch.object(server.wxdecipher, "discover_databases", return_value=response) as discover:
            status, result = self.request("/api/wechat/decipher/discover", {"ownership_confirmed": True})
            self.assertEqual(200, status, result)
            self.assertEqual("windows", result["platform"])
            discover.assert_called_once_with({"ownership_confirmed": True})
        with patch.object(server.wxdecipher, "discover_databases", side_effect=AssertionError("must not scan without token")):
            status, _ = self.request("/api/wechat/decipher/discover", {"ownership_confirmed": True}, token=False)
            self.assertEqual(403, status)

        imported = {"file_count": 2, "bytes": 8192, "message": "synthetic"}
        payload = {"session_id": "wxdecipher-" + "a" * 32, "candidate_ids": ["b" * 32],
                   "ownership_confirmed": True, "snapshot_confirmed": True}
        with patch.object(server.wxdecipher, "import_discovered_databases", return_value=imported) as import_selected:
            status, result = self.request("/api/wechat/decipher/import-discovered", payload)
            self.assertEqual(201, status, result)
            import_selected.assert_called_once_with(self.root, payload)

    def test_storage_permission_plan_and_grant_require_local_auth_and_confirmation(self):
        from agent_platform import wechat_storage as storage
        base = self.root / "private-user-store"
        with patch.object(storage, "_private_base", return_value=base):
            for route in ("plan", "grant"):
                status, _ = self.request("/api/wechat/storage/" + route, token=False)
                self.assertEqual(403, status)
            selected = self.root / "custom-data"
            status, result = self.request("/api/wechat/storage/plan", {"directory": str(selected)})
            self.assertEqual(200, status, result)
            self.assertEqual(str(selected), result["directory"])
            self.assertFalse(base.exists())
            self.assertFalse(selected.exists())
            token = result["confirmation_token"]
            status, _ = self.request("/api/wechat/storage/grant", {"confirmation_token": token})
            self.assertEqual(400, status)
            self.assertFalse(base.exists())
            status, result = self.request("/api/wechat/storage/grant", {"confirmation_token": token, "confirmed": True})
            self.assertEqual(200, status, result)
            self.assertTrue(result["configured"])
            self.assertEqual(str(selected), result["directory"])
            session = self.create()
            self.assertTrue((Path(result["directory"]) / "decipher" / session).is_dir())
            self.request("/api/wechat/decipher/discard", {"session_id": session})

    def test_bad_key_returns_generic_error_and_removes_plaintext_staging(self):
        cipher = encrypt_fixture(self.source, self.root / "bad.db")
        session = self.create(); self.upload(session, cipher)
        status, result = self.request("/api/wechat/decipher/run", {"session_id": session, "key": "ff" * 32})
        self.assertEqual(400, status)
        self.assertEqual("KEY_MISMATCH", result["code"])
        self.assertNotIn("ff" * 32, str(result))
        self.assertFalse((self.root / "data/wechat/decipher" / session).exists())
        status, _ = self.request("/api/wechat/decipher/discard", {"session_id": session})
        self.assertEqual(200, status)

    def test_copy_and_parse_are_separate_authenticated_operations(self):
        status, _ = self.request("/api/wechat/decipher/finish-copy", token=False)
        self.assertEqual(403, status)
        status, copied = self.request("/api/wechat/decipher/sessions", {"ownership_confirmed": True,
            "snapshot_confirmed": True, "self_username": SELF, "retain_copies": True})
        self.assertEqual(201, status)
        session = copied["session_id"]
        self.upload(session, self.source)
        status, result = self.request("/api/wechat/decipher/run", {"session_id": session})
        self.assertEqual("COPY_INCOMPLETE", result["code"])
        status, _ = self.request("/api/wechat/decipher/finish-copy", {"session_id": session, "file_count": 1})
        self.assertEqual(200, status)
        for _ in range(2):
            status, result = self.request("/api/wechat/decipher/run", {"session_id": session})
            self.assertEqual(200, status, result)
            self.assertEqual(3, result["decipher"]["messages"])
            self.assertTrue(Path(copied["working_directory"]).is_dir())
        self.request("/api/wechat/decipher/discard", {"session_id": session})
        self.assertFalse(Path(copied["working_directory"]).exists())

    def test_discard_endpoint_cannot_escape_session_root(self):
        keep = self.root / "keep.db"; keep.write_bytes(b"keep")
        for value in ("../keep.db", "C:/", "", "wxdecipher-not-a-uuid"):
            status, _ = self.request("/api/wechat/decipher/discard", {"session_id": value})
            self.assertEqual(400, status)
        self.assertEqual(b"keep", keep.read_bytes())

    def test_media_http_requires_authorization_and_does_not_save_or_log_image_keys(self):
        plain = image_fixture(); content = v2_fixture(plain)
        image_key = "0123456789abcdef"
        headers = {"X-Wechat-Ownership": "true", "X-WXDecipher-Image-Key": image_key}
        status, _ = self.request("/api/wechat/decipher/media", body=content, headers=headers, token=False)
        self.assertEqual(403, status)
        status, result = self.request("/api/wechat/decipher/media", body=content)
        self.assertEqual("AUTHORIZATION_REQUIRED", result["code"])
        with patch("ui.server.task_runtime_selection", side_effect=AssertionError("No model for media")):
            status, result = self.request("/api/wechat/decipher/media", body=content, headers=headers)
        self.assertEqual(200, status, result)
        self.assertEqual(plain, result.pop("binary"))
        self.assertNotIn(image_key, json.dumps([result, self.logs]))
        self.assertFalse((self.root / "data").exists())
        with patch.object(server, "ACTIVE_PROFILE_ID", "product-director"):
            status, result = self.request("/api/wechat/decipher/media", body=b"", headers=headers)
        self.assertEqual("PROFILE_FORBIDDEN", result["code"])

    def test_process_endpoint_is_explicit_and_run_session_cannot_capture_twice(self):
        with patch.object(server.wxdecipher.wxdecipher_capture, "_WindowsAPI", side_effect=AssertionError("Must not enumerate real processes")):
            status, result = self.request("/api/wechat/decipher/processes", {})
            self.assertEqual(400, status); self.assertEqual("AUTHORIZATION_REQUIRED", result["code"])
        selected = {"process_id": 123, "created_at": "456", "confirmed": True}
        cipher = encrypt_fixture(self.source, self.root / "cipher.db")
        session = self.create(); self.upload(session, cipher)
        status, consent = self.request("/api/wechat/decipher/capture-consent", {"session_id": session, **selected})
        self.assertEqual(201, status, consent)
        selected["consent_token"] = consent["consent_token"]
        with patch.object(server.wxdecipher.wxdecipher_capture, "capture_keys", return_value=({"message_0.db": KEY_HEX}, {"verified_databases": 1})) as capture:
            status, result = self.request("/api/wechat/decipher/run", {"session_id": session, "auto_capture": selected})
            self.assertEqual(200, status, result)
            self.assertNotIn(KEY_HEX, json.dumps([result, self.logs]))
            status, second = self.request("/api/wechat/decipher/run", {"session_id": session, "auto_capture": selected})
            self.assertEqual(404, status, second)
            self.assertEqual(1, capture.call_count)

    def test_http_wal_replay_and_failed_capture_cleanup(self):
        inputs = self.root / "wal-fixture"; inputs.mkdir()
        source, wal = sqlite_wal_fixture(inputs, count=2)
        session = self.create(); self.upload(session, source)
        status, result = self.request("/api/wechat/decipher/upload", body=wal.read_bytes(),
                                       headers={"X-WXDecipher-Session": session, "X-File-Name": "message_0.db-wal"})
        self.assertEqual(201, status, result)
        status, result = self.request("/api/wechat/decipher/run", {"session_id": session, "wal_replay_confirmed": True})
        self.assertEqual(200, status, result)
        self.assertEqual(5, result["decipher"]["messages"])
        self.assertTrue(result["decipher"]["databases"][0]["wal"]["verified"])
        session = self.create(); self.upload(session)
        status, result = self.request("/api/wechat/decipher/run", {"session_id": session, "auto_capture": {"confirmed": False}})
        self.assertEqual(400, status, result)
        self.assertFalse((self.root / "data/wechat/decipher" / session).exists())


if __name__ == "__main__":
    unittest.main()
