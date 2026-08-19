import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from ui import server


class ControlCentreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runtime, self.old_tasks, self.old_requests, self.old_plans, self.old_active_profile = (
            server.RUNTIME, server.TASKS, server.REQUESTS, server.PRESENTATION_PLANS, server.ACTIVE_PROFILE_ID
        )
        server.RUNTIME = Path(self.temporary.name)
        server.TASKS = server.RUNTIME / "tasks"
        server.REQUESTS = server.RUNTIME / "requests"
        server.PRESENTATION_PLANS = server.RUNTIME / "presentation-plans"
        server.ACTIVE_PROFILE_ID = None

    def write_plan(self, task_id="task-ppt", **changes):
        plan = {
            "schema_version": "1.0", "task_id": task_id, "profile_id": "market-director",
            "phase": "outline", "version": 1, "context_snapshot_sha256": "a" * 64,
            "outline": [
                {"slide_id": f"slide-{index}", "order": index, "conclusion_title": f"页面 {index}"}
                for index in range(1, 5)
            ],
        }
        plan.update(changes)
        plan["plan_sha256"] = server.presentation_plan_sha256(plan)
        server.atomic_json(server.PRESENTATION_PLANS / f"{task_id}.json", plan)
        return plan

    def tearDown(self):
        server.RUNTIME, server.TASKS, server.REQUESTS, server.PRESENTATION_PLANS, server.ACTIVE_PROFILE_ID = (
            self.old_runtime, self.old_tasks, self.old_requests, self.old_plans, self.old_active_profile
        )
        self.temporary.cleanup()

    def test_atomic_json_round_trip(self):
        target = server.TASKS / "task-a.json"
        server.atomic_json(target, {"task_id": "task-a", "version": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 1)
        self.assertFalse(list(server.TASKS.glob("*.tmp")))

    def test_safe_id_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            server.safe_id("../task")

    def test_sales_director_edition_returns_only_one_role(self):
        server.ACTIVE_PROFILE_ID = "sales-director"
        profiles = server.profiles()
        self.assertEqual([profile["id"] for profile in profiles], ["sales-director"])
        service_ids = {service["id"] for service in profiles[0]["services"]}
        self.assertIn("sales-review", service_ids)
        self.assertIn("government-proposal", service_ids)
        self.assertNotIn("product-discovery", service_ids)
        workflow_ids = set(server.workflows())
        self.assertIn("market.government.proposal", workflow_ids)
        self.assertFalse(any(workflow_id.startswith("product.") for workflow_id in workflow_ids))

    def test_sales_workbench_uses_guided_forms_and_one_click_weekly_report(self):
        ui_root = Path(server.__file__).parent
        html = (ui_root / "index.html").read_text(encoding="utf-8")
        javascript = (ui_root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="guided-fields"', html)
        self.assertIn('id="quick-prompts"', html)
        self.assertIn('id="weekly-task-form"', html)
        self.assertIn('id="create-weekly"', html)
        self.assertIn("高级设置（页数、风格和文件名）", html)
        self.assertNotIn('id="request"', html)
        self.assertNotIn('id="ppt-purpose"', html)
        self.assertNotIn('id="ppt-occasion"', html)
        self.assertIn('"sales-review":', javascript)
        self.assertIn('"industry-research":', javascript)
        self.assertIn('"pdf-import":', javascript)
        self.assertIn('"government-proposal":', javascript)
        self.assertIn('"office-document":', javascript)
        self.assertIn("function weeklyBrief()", javascript)
        self.assertIn("function guidedRequest()", javascript)

    def test_exclusive_task_rejects_a_concurrent_writer(self):
        target = server.TASKS / "task-a.json"
        with server.exclusive_task(target):
            with self.assertRaisesRegex(RuntimeError, "正在由 Pi"):
                with server.exclusive_task(target):
                    pass
        self.assertFalse(Path(f"{target}.lock").exists())

    def test_exclusive_task_never_deletes_a_replaced_lock(self):
        target = server.TASKS / "task-a.json"
        lock = Path(f"{target}.lock")
        with server.exclusive_task(target):
            lock.write_text(json.dumps({"pid": 999, "nonce": "replacement"}), encoding="utf-8")
        self.assertTrue(lock.exists())

    def test_file_summary_does_not_return_csv_content(self):
        data = Path(self.temporary.name) / "data" / "sales"
        data.mkdir(parents=True)
        target = data / "customers.csv"
        target.write_text("id,name\n1,private-name\n", encoding="utf-8")
        previous_root = server.ROOT
        try:
            server.ROOT = Path(self.temporary.name)
            summary = server.file_summary("data/sales/customers.csv")
        finally:
            server.ROOT = previous_root
        self.assertEqual(summary["records"], 1)
        self.assertNotIn("private-name", json.dumps(summary, ensure_ascii=False))

    def test_file_summary_counts_multiline_csv_as_one_record(self):
        data = Path(self.temporary.name) / "data" / "knowledge"
        data.mkdir(parents=True)
        target = data / "source-register.csv"
        target.write_text('id,notes\n1,"line one\nline two"\n', encoding="utf-8")
        previous_root = server.ROOT
        try:
            server.ROOT = Path(self.temporary.name)
            summary = server.file_summary("data/knowledge/source-register.csv")
        finally:
            server.ROOT = previous_root
        self.assertEqual(summary["records"], 1)

    def test_presentation_brief_limits_match_the_plan_contract(self):
        brief = {
            "schema_version": "1.0", "scene": "industry", "mode": "standard",
            "topic": "主题", "audience": "管理层", "purpose": "形成判断", "occasion": "专题会", "language": "zh-CN",
            "duration_minutes": 15, "target_slides": 6,
            "design_system": {"token_id": "technology-research"},
            "source_scope": "public-web-and-profile-knowledge", "confidentiality": "internal",
            "expected_decision": "确认下一步", "output_name": "research.pptx",
        }
        request = f"[PRESENTATION_BRIEF]\n{json.dumps(brief, ensure_ascii=False)}\n[/PRESENTATION_BRIEF]"
        self.assertEqual(server.validate_presentation_brief_request(request)["target_slides"], 6)
        for field, length in (("topic", 241), ("expected_decision", 501)):
            invalid = {**brief, field: "字" * length}
            invalid_request = f"[PRESENTATION_BRIEF]\n{json.dumps(invalid, ensure_ascii=False)}\n[/PRESENTATION_BRIEF]"
            with self.assertRaises(ValueError):
                server.validate_presentation_brief_request(invalid_request)
        invalid_scope = {**brief, "source_scope": "internal"}
        with self.assertRaises(ValueError):
            server.validate_presentation_brief_request(
                f"[PRESENTATION_BRIEF]\n{json.dumps(invalid_scope, ensure_ascii=False)}\n[/PRESENTATION_BRIEF]"
            )

    def test_presentation_plan_is_task_bound_and_filters_internal_fields(self):
        server.PRESENTATION_PLANS.mkdir(parents=True)
        self.write_plan(
            "task-a", version=2, outline=[{"conclusion_title": "本周结论"}], internal_secret="hidden",
        )
        plan = server.presentation_plan("task-a")
        self.assertEqual(plan["outline"][0]["conclusion_title"], "本周结论")
        self.assertNotIn("internal_secret", plan)
        server.atomic_json(server.PRESENTATION_PLANS / "task-b.json", {"task_id": "another-task"})
        self.assertIsNone(server.presentation_plan("task-b"))

    def test_approval_must_bind_the_frozen_write_hash(self):
        task = {
            "task_id": "task-a", "version": 7, "status": "waiting_approval",
            "pending_write": {
                "intent_id": "intent-a", "payload_sha256": "a" * 64,
                "status": "prepared", "canonical_payload": "{}",
            },
        }
        path = server.TASKS / "task-a.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        handler.decide("task-a", {"decision": "approve", "version": 7})
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertNotIn("approval_request", server.load_json(path))

        handler.decide("task-a", {
            "decision": "approve", "version": 7,
            "intent_id": "intent-a", "payload_sha256": "a" * 64,
        })
        saved = server.load_json(path)
        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        self.assertEqual(saved["approval_request"]["intent_id"], "intent-a")
        self.assertEqual(saved["approval_request"]["payload_sha256"], "a" * 64)

    def test_presentation_revision_creates_a_new_request_and_rejects_the_old_plan(self):
        task = {
            "task_id": "task-ppt", "profile_id": "market-director", "service_id": "presentation-studio",
            "workflow_id": "shared.presentation.studio", "version": 4, "status": "waiting_approval",
        }
        server.atomic_json(server.TASKS / "task-ppt.json", task)
        plan = self.write_plan()
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        payload = {
            "version": 4, "plan_sha256": plan["plan_sha256"],
            "outline": [
                {"slide_id": "slide-2", "title": "调整后的第二页"},
                {"slide_id": "slide-1", "title": "调整后的第一页"},
                {"slide_id": "slide-3", "title": "第三页"},
                {"slide_id": "slide-4", "title": "第四页"},
            ],
        }
        profile = {"id": "market-director", "services": [{"id": "presentation-studio"}]}
        with patch("ui.server.profiles", return_value=[profile]):
            handler.create_presentation_revision("task-ppt", payload)

        self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
        saved_task = server.load_json(server.TASKS / "task-ppt.json")
        self.assertEqual(saved_task["approval_request"]["decision"], "reject")
        request = server.load_json(next(server.REQUESTS.glob("*.json")))
        self.assertEqual(request["source"], "local-workbench")
        self.assertEqual(request["request_kind"], "presentation-plan-revision")
        self.assertEqual(request["revision_of_task_id"], "task-ppt")
        self.assertIn("调整后的第二页", request["request"])
        self.assertLess(request["request"].index("调整后的第二页"), request["request"].index("调整后的第一页"))

    def test_presentation_revision_rejects_a_stale_plan_hash(self):
        server.atomic_json(server.TASKS / "task-ppt.json", {
            "task_id": "task-ppt", "profile_id": "market-director", "service_id": "presentation-studio",
            "workflow_id": "shared.presentation.studio", "version": 4, "status": "waiting_approval",
        })
        self.write_plan()
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        handler.create_presentation_revision("task-ppt", {
            "version": 4, "plan_sha256": "c" * 64,
            "outline": [{"slide_id": f"slide-{index}", "title": f"页面 {index}"} for index in range(1, 5)],
        })
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertFalse(server.REQUESTS.exists())
        self.assertNotIn("approval_request", server.load_json(server.TASKS / "task-ppt.json"))

    def test_presentation_revision_rejects_plan_content_tampering_with_a_reused_hash(self):
        server.atomic_json(server.TASKS / "task-ppt.json", {
            "task_id": "task-ppt", "profile_id": "market-director", "service_id": "presentation-studio",
            "workflow_id": "shared.presentation.studio", "version": 4, "status": "waiting_approval",
        })
        plan = self.write_plan()
        tampered = server.load_json(server.PRESENTATION_PLANS / "task-ppt.json")
        tampered["outline"][0]["conclusion_title"] = "未经受控流程修改的标题"
        server.atomic_json(server.PRESENTATION_PLANS / "task-ppt.json", tampered)
        self.assertIsNone(server.presentation_plan("task-ppt"))

        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        handler.create_presentation_revision("task-ppt", {
            "version": 4, "plan_sha256": plan["plan_sha256"],
            "outline": [{"slide_id": f"slide-{index}", "title": f"页面 {index}"} for index in range(1, 5)],
        })
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertFalse(server.REQUESTS.exists())
        self.assertNotIn("approval_request", server.load_json(server.TASKS / "task-ppt.json"))

    def test_presentation_revision_publish_recovers_after_task_rejection(self):
        server.atomic_json(server.TASKS / "task-ppt.json", {
            "task_id": "task-ppt", "profile_id": "market-director", "service_id": "presentation-studio",
            "workflow_id": "shared.presentation.studio", "version": 4, "status": "waiting_approval", "audit": [],
        })
        plan = self.write_plan()
        payload = {
            "version": 4, "plan_sha256": plan["plan_sha256"],
            "outline": [{"slide_id": f"slide-{index}", "title": f"页面 {index}"} for index in range(1, 5)],
        }
        profile = {"id": "market-director", "services": [{"id": "presentation-studio"}]}
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        real_atomic = server.atomic_json
        calls = 0

        def fail_final_publish(path, value):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated publish interruption")
            real_atomic(path, value)

        with patch("ui.server.profiles", return_value=[profile]), patch("ui.server.atomic_json", side_effect=fail_final_publish):
            handler.create_presentation_revision("task-ppt", payload)

        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        request_path = next(server.REQUESTS.glob("*.json"))
        self.assertEqual(server.load_json(request_path)["status"], "prepared")
        self.assertEqual(server.load_json(server.TASKS / "task-ppt.json")["approval_request"]["decision"], "reject")
        server.recover_prepared_presentation_revisions()
        self.assertEqual(server.load_json(request_path)["status"], "requested")

    def test_prepared_revision_is_not_published_if_process_stops_before_task_rejection(self):
        server.atomic_json(server.TASKS / "task-ppt.json", {
            "task_id": "task-ppt", "profile_id": "market-director", "service_id": "presentation-studio",
            "workflow_id": "shared.presentation.studio", "version": 4, "status": "waiting_approval", "audit": [],
        })
        plan = self.write_plan()
        payload = {
            "version": 4, "plan_sha256": plan["plan_sha256"],
            "outline": [{"slide_id": f"slide-{index}", "title": f"页面 {index}"} for index in range(1, 5)],
        }
        profile = {"id": "market-director", "services": [{"id": "presentation-studio"}]}
        handler = object.__new__(server.ControlHandler)
        handler.send_json = lambda *_args: None
        real_atomic = server.atomic_json
        calls = 0

        def stop_before_rejection(path, value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SystemExit("simulated process stop")
            real_atomic(path, value)

        with patch("ui.server.profiles", return_value=[profile]), patch("ui.server.atomic_json", side_effect=stop_before_rejection):
            with self.assertRaises(SystemExit):
                handler.create_presentation_revision("task-ppt", payload)

        request_path = next(server.REQUESTS.glob("*.json"))
        self.assertEqual(server.load_json(request_path)["status"], "prepared")
        self.assertNotIn("approval_request", server.load_json(server.TASKS / "task-ppt.json"))
        server.recover_prepared_presentation_revisions()
        self.assertEqual(server.load_json(request_path)["status"], "prepared")

        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        with patch("ui.server.profiles", return_value=[profile]):
            handler.create_presentation_revision("task-ppt", payload)
        self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
        self.assertEqual(server.load_json(request_path)["status"], "requested")
        self.assertEqual(server.load_json(server.TASKS / "task-ppt.json")["approval_request"]["decision"], "reject")


if __name__ == "__main__":
    unittest.main()
