from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_platform.cli import build_parser
from agent_platform.coding_agents import (
    CodingAgentBridgeError,
    WorkbenchClient,
    coding_agent_doctor,
    coding_agent_skill_status,
    sales_services,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeWorkbenchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    posts: list[tuple[str, dict[str, object], str]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, value: object) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.com/collect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/api/health":
            self._send(200, {"status": "ok", "profile_id": "sales-director"})
            return
        if self.path == "/api/bootstrap":
            self._send(200, {
                "request_token": "local-test-token",
                "profiles": [{
                    "id": "sales-director",
                    "services": [{"id": "sales-review"}, {"id": "government-proposal"}],
                }],
                "projects": [{
                    "project_id": "project-default", "name": "日常工作", "status": "active",
                    "task_count": 1, "active_task_count": 1,
                }],
            })
            return
        if self.path == "/api/tasks":
            self._send(200, [{
                "task_id": "request-test123",
                "project_id": "project-default",
                "service_id": "sales-review",
                "workflow_id": "market.sales.pipeline-review",
                "status": "waiting_approval",
                "display_status": "waiting_approval",
                "runtime_state": "approval",
                "version": 7,
                "request": "复盘重点客户",
                "pending_write": {
                    "status": "prepared",
                    "canonical_payload": "do-not-expose",
                },
                "approval_request": None,
                "artifacts": ["outputs/report.docx", "inputs/not-an-artifact.pdf"],
            }])
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        token = self.headers.get("X-Director-Token", "")
        type(self).posts.append((self.path, payload, token))
        if token != "local-test-token":
            self._send(403, {"error": "bad token"})
            return
        if self.path == "/api/task-requests":
            self._send(201, {
                "request_id": "request-created123",
                "project_id": payload["project_id"],
                "service_id": payload["service_id"],
                "workflow_id": "market.sales.pipeline-review",
                "status": "requested",
            })
            return
        if self.path == "/api/tasks/request-test123/messages":
            self._send(202, {"status": "queued", "message": "已排队"})
            return
        self._send(404, {"error": "not found"})


class CodingAgentBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWorkbenchHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = WorkbenchClient(f"http://127.0.0.1:{cls.server.server_port}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self) -> None:
        FakeWorkbenchHandler.posts.clear()

    def test_bridge_rejects_non_loopback_urls(self) -> None:
        for value in (
            "https://127.0.0.1:8765",
            "http://example.com:8765",
            "http://localhost:8765",
            "http://user@127.0.0.1:8765",
            "http://127.0.0.1:8765/path",
        ):
            with self.subTest(value=value), self.assertRaises(CodingAgentBridgeError):
                WorkbenchClient(value)

    def test_bridge_does_not_follow_redirects_or_forward_local_token(self) -> None:
        with self.assertRaises(CodingAgentBridgeError) as raised:
            self.client._request("/redirect", token="local-test-token")
        self.assertEqual(raised.exception.code, "WORKBENCH_HTTP_302")

    def test_submit_uses_local_token_and_sales_profile(self) -> None:
        result = self.client.submit_task(
            service_id="sales-review",
            project_id="project-default",
            request_text="复盘本周重点客户",
            thinking_level="high",
        )
        self.assertEqual(result["task_id"], "request-created123")
        path, payload, token = FakeWorkbenchHandler.posts[-1]
        self.assertEqual(path, "/api/task-requests")
        self.assertEqual(token, "local-test-token")
        self.assertEqual(payload["profile_id"], "sales-director")
        self.assertEqual(payload["requested_thinking_level"], "high")

    def test_status_hides_raw_write_payload_and_requires_desktop_approval(self) -> None:
        result = self.client.task("request-test123")
        self.assertTrue(result["requires_approval"])
        self.assertTrue(result["has_prepared_write"])
        self.assertNotIn("pending_write", result)
        self.assertNotIn("canonical_payload", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["artifacts"], ["outputs/report.docx"])
        self.assertIn("桌面工作台", result["next_action"])

    def test_message_is_version_bound_and_has_no_approval_operation(self) -> None:
        result = self.client.send_message(
            task_id="request-test123",
            mode="redirect",
            content="把重点调整为回款风险",
        )
        self.assertEqual(result["status"], "queued")
        path, payload, _token = FakeWorkbenchHandler.posts[-1]
        self.assertEqual(path, "/api/tasks/request-test123/messages")
        self.assertEqual(payload["version"], 7)
        coding_parser = next(
            action for action in build_parser()._actions
            if getattr(action, "dest", None) == "command"
        ).choices["coding-agent"]
        coding_commands = next(
            action for action in coding_parser._actions
            if getattr(action, "dest", None) == "coding_command"
        ).choices
        self.assertNotIn("approve", coding_commands)

    def test_project_scoped_skills_are_identical(self) -> None:
        result = coding_agent_skill_status(ROOT)
        self.assertTrue(result["ready"], result)
        self.assertEqual(set(result["files"]), {"SKILL.md", "references/services.md"})

    def test_sales_services_do_not_expose_other_profiles(self) -> None:
        services = sales_services(ROOT)
        self.assertGreaterEqual(len(services), 10)
        self.assertTrue(all("profile" not in service for service in services))
        self.assertIn("government-proposal", {service["id"] for service in services})

    def test_doctor_reports_bridge_without_requiring_installed_hosts(self) -> None:
        result = coding_agent_doctor(ROOT, environ={"PATH": ""}, client=self.client)
        self.assertTrue(result["integration_ready"])
        self.assertTrue(result["workbench"]["ready"])
        self.assertEqual(set(result["hosts"]), {"codex", "claude"})
        self.assertIn("人工审批", result["approval_boundary"])


if __name__ == "__main__":
    unittest.main()
