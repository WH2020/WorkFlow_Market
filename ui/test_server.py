import json
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from ui import server


class ControlCentreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runtime, self.old_tasks, self.old_requests, self.old_plans, self.old_projects, self.old_schedules, self.old_agent_leases, self.old_task_events, self.old_task_messages, self.old_desktop_settings, self.old_ai_core_log, self.old_active_profile = (
            server.RUNTIME, server.TASKS, server.REQUESTS, server.PRESENTATION_PLANS, server.PROJECTS, server.SCHEDULES, server.AGENT_LEASES, server.TASK_EVENTS, server.TASK_MESSAGES, server.DESKTOP_SETTINGS, server.AI_CORE_LOG, server.ACTIVE_PROFILE_ID
        )
        server.RUNTIME = Path(self.temporary.name)
        server.TASKS = server.RUNTIME / "tasks"
        server.REQUESTS = server.RUNTIME / "requests"
        server.PRESENTATION_PLANS = server.RUNTIME / "presentation-plans"
        server.PROJECTS = server.RUNTIME / "projects.json"
        server.SCHEDULES = server.RUNTIME / "schedules.json"
        server.AGENT_LEASES = server.RUNTIME / "agent-leases"
        server.TASK_EVENTS = server.RUNTIME / "task-events"
        server.TASK_MESSAGES = server.RUNTIME / "task-messages"
        server.DESKTOP_SETTINGS = server.RUNTIME / "desktop-settings.json"
        server.AI_CORE_LOG = server.RUNTIME / "ai-core.log"
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
        server.RUNTIME, server.TASKS, server.REQUESTS, server.PRESENTATION_PLANS, server.PROJECTS, server.SCHEDULES, server.AGENT_LEASES, server.TASK_EVENTS, server.TASK_MESSAGES, server.DESKTOP_SETTINGS, server.AI_CORE_LOG, server.ACTIVE_PROFILE_ID = (
            self.old_runtime, self.old_tasks, self.old_requests, self.old_plans, self.old_projects, self.old_schedules, self.old_agent_leases, self.old_task_events, self.old_task_messages, self.old_desktop_settings, self.old_ai_core_log, self.old_active_profile
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
        self.assertIn('id="model-settings-panel"', html)
        self.assertIn('id="search-settings-panel"', html)
        self.assertIn('id="search-api-key"', html)
        self.assertIn('id="save-search-settings"', html)
        self.assertIn('id="discover-models"', html)
        self.assertIn('id="model-select"', html)
        self.assertIn('id="task-model"', html)
        self.assertIn('id="task-thinking"', html)
        self.assertIn('id="reset-model-settings"', html)
        self.assertIn('id="show-ai-core-window"', html)
        self.assertIn('id="ai-core-log"', html)
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
        self.assertIn("function renderModelSettings", javascript)
        self.assertIn("function renderSearchSettings", javascript)
        self.assertIn('api("/api/search-settings"', javascript)
        self.assertIn('api("/api/search-settings/open-dashboard"', javascript)
        self.assertIn("function renderRuntimeSettings", javascript)
        self.assertIn("function taskRuntimeSelection", javascript)
        self.assertIn('api("/api/model-discovery"', javascript)
        self.assertIn('api("/api/desktop-settings"', javascript)
        for view in ("home", "projects", "schedules", "search"):
            self.assertIn(f'data-page="{view}"', html)
        self.assertIn('id="project-file-input"', html)
        self.assertIn('id="quick-command"', html)
        self.assertIn('id="schedule-request"', html)
        self.assertIn('id="search-query"', html)
        self.assertIn('api("/api/search"', javascript)
        self.assertIn("task.display_status", javascript)
        self.assertIn('effectiveStatus === "interrupted"', javascript)
        self.assertIn('id="task-history"', html)
        self.assertIn('addRestartAction(actions, task, "重新开始")', javascript)
        self.assertIn('addAction(actions, task, "resume", "继续任务")', javascript)
        self.assertIn("function renderTaskProgress", javascript)
        self.assertIn("const taskProgressScroll = {}", javascript)
        self.assertIn("followLatest: distanceFromBottom <= 24", javascript)
        self.assertIn("previousScroll?.followLatest === false", javascript)
        self.assertIn('redirect.textContent = "调整当前方向"', javascript)
        self.assertIn("/messages`,", javascript)

    def test_project_space_is_created_and_task_summaries_default_to_general_project(self):
        project = server.create_project_record({"name": "江苏客户项目", "description": "试点机会"})
        self.assertEqual(project["status"], "active")
        self.assertEqual(server.active_project(project["project_id"])["name"], "江苏客户项目")
        server.atomic_json(server.TASKS / "task-old.json", {
            "task_id": "task-old", "profile_id": "sales-director", "status": "requested",
        })
        self.assertEqual(server.task_summaries()[0]["project_id"], server.DEFAULT_PROJECT_ID)

    def test_task_request_accepts_only_a_configured_model_and_thinking_level(self):
        handler = server.ControlHandler.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        settings = {
            "configured": True, "status": "configured", "has_api_key": True,
            "provider_id": "agent4market-newapi", "models": [{"id": "gpt-5.5"}],
        }
        payload = {
            "profile_id": "sales-director", "service_id": "sales-review",
            "project_id": server.DEFAULT_PROJECT_ID, "request": "复盘重点客户",
            "requested_model": "agent4market-newapi/gpt-5.5",
            "requested_thinking_level": "high",
        }
        with patch("ui.server.model_settings_summary", return_value=settings):
            handler.create_request(payload)
        self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
        record = server.load_json(server.REQUESTS / f"{replies[-1][1]['request_id']}.json")
        self.assertEqual(record["requested_model"], "agent4market-newapi/gpt-5.5")
        self.assertEqual(record["requested_thinking_level"], "high")

        with patch("ui.server.model_settings_summary", return_value=settings):
            with self.assertRaises(ValueError):
                handler.create_request({**payload, "requested_model": "agent4market-newapi/not-allowed"})
        with self.assertRaises(ValueError):
            handler.create_request({**payload, "requested_thinking_level": "unlimited"})

    def test_public_research_task_is_rejected_before_queueing_when_search_is_not_ready(self):
        handler = server.ControlHandler.__new__(server.ControlHandler)
        payload = {
            "profile_id": "sales-director", "service_id": "government-proposal",
            "project_id": server.DEFAULT_PROJECT_ID, "request": "制定地方政府合作方案",
        }
        with patch("ui.server.search_settings_summary", return_value={"status": "unconfigured"}):
            with self.assertRaisesRegex(ValueError, "设置 > 公开检索"):
                handler.create_request(payload)
        with patch(
            "ui.server.search_settings_summary",
            return_value={"status": "configured", "restart_required": True},
        ):
            with self.assertRaisesRegex(ValueError, "关闭并重新打开"):
                handler.create_request(payload)
        self.assertFalse(list(server.REQUESTS.glob("*.json")))

    def test_running_task_requires_a_fresh_matching_agent_lease(self):
        task = {
            "task_id": "task-running", "profile_id": "sales-director", "status": "running",
            "session_key": "session-a", "version": 1,
        }
        server.atomic_json(server.TASKS / "task-running.json", task)
        summary = server.task_summaries()[0]
        self.assertEqual(summary["display_status"], "interrupted")
        self.assertEqual(summary["runtime_state"], "interrupted")

        lease = {
            "schema_version": "1.0", "pid": 1234, "nonce": "a" * 36,
            "profile_id": "sales-director", "session_key": "session-a",
            "task_id": "task-running", "task_status": "running",
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }
        server.atomic_json(server.AGENT_LEASES / "1234.json", lease)
        summary = server.task_summaries()[0]
        self.assertEqual(summary["display_status"], "running")
        self.assertEqual(summary["runtime_state"], "active")

        lease["heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        server.atomic_json(server.AGENT_LEASES / "1234.json", lease)
        self.assertEqual(server.task_summaries()[0]["display_status"], "interrupted")

    def test_embedded_ai_core_is_default_and_runtime_summary_accepts_an_idle_lease(self):
        self.assertFalse(server.desktop_settings()["show_ai_core_window"])
        saved = server.save_desktop_settings({"show_ai_core_window": True})
        self.assertTrue(saved["show_ai_core_window"])
        self.assertTrue(saved["restart_required"])
        self.assertTrue(server.desktop_settings()["show_ai_core_window"])

        server.atomic_json(server.AGENT_LEASES / "4321.json", {
            "schema_version": "1.0", "pid": 4321, "nonce": "c" * 36,
            "profile_id": "sales-director", "session_key": "session-idle",
            "task_id": None, "task_status": None,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        })
        summary = server.desktop_runtime_summary()
        self.assertEqual(summary["status"], "idle")
        self.assertEqual(summary["label"], "AI 核心已就绪")
        self.assertEqual(summary["window_mode"], "visible")

    def test_embedded_ai_core_log_is_bounded_and_redacts_credentials(self):
        server.AI_CORE_LOG.parent.mkdir(parents=True, exist_ok=True)
        server.AI_CORE_LOG.write_text(
            "startup\nAuthorization: Bearer secret-token\napi_key=secret-value\nready\n",
            encoding="utf-8",
        )
        lines = server.ai_core_log_tail()
        self.assertLessEqual(len(lines), 60)
        self.assertNotIn("secret-token", "\n".join(lines))
        self.assertNotIn("secret-value", "\n".join(lines))
        self.assertIn("[已隐藏]", "\n".join(lines))

    def test_task_messages_are_queued_and_visible_in_progress_timeline(self):
        task = {
            "schema_version": "1.0", "task_id": "task-live", "profile_id": "sales-director",
            "service_id": "sales-review", "workflow_id": "market.sales.pipeline-review",
            "request": "复盘客户 A", "status": "running", "session_key": "session-a", "version": 2,
            "current_node": "analyze", "waiting_node": None, "completed_nodes": ["load_accounts"],
            "artifacts": [], "created_at": server.now(), "updated_at": server.now(),
            "audit": [{"at": server.now(), "action": "task_started", "actor": "user"}],
        }
        server.atomic_json(server.TASKS / "task-live.json", task)
        handler = server.ControlHandler.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        handler.create_task_message("task-live", {"mode": "redirect", "content": "先分析预算风险，再给推进建议。"})
        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        message = next(server.TASK_MESSAGES.glob("message-*.json"))
        self.assertEqual(server.load_json(message)["status"], "queued")

        event_id = "event-task-live-12345678-1234-4234-8234-123456789abc"
        server.atomic_json(server.TASK_EVENTS / f"{event_id}.json", {
            "schema_version": "1.0", "event_id": event_id, "task_id": "task-live",
            "profile_id": "sales-director", "node_id": "analyze", "phase": "analyzing",
            "summary": "正在对照预算与决策链。", "basis": "客户记录尚未确认预算负责人。",
            "next_step": "形成风险和推进建议。", "created_at": server.now(), "source": "assistant",
        })
        summary = server.task_summaries()[0]
        self.assertEqual(summary["queued_message_count"], 1)
        self.assertTrue(any(item["title"] == "你调整了任务方向" and item["status"] == "queued" for item in summary["progress"]))
        self.assertTrue(any(item.get("basis") == "客户记录尚未确认预算负责人。" for item in summary["progress"]))

    def test_terminal_task_rejects_new_messages(self):
        server.atomic_json(server.TASKS / "task-done.json", {
            "task_id": "task-done", "profile_id": "sales-director", "status": "completed",
        })
        handler = server.ControlHandler.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))
        handler.create_task_message("task-done", {"mode": "supplement", "content": "再补充一项"})
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertFalse(server.TASK_MESSAGES.exists())

    def test_workbench_can_request_cancellation_only_for_an_interrupted_task(self):
        task = {
            "task_id": "task-running", "profile_id": "sales-director", "service_id": "sales-review",
            "workflow_id": "market.sales.review", "status": "running", "session_key": "session-a",
            "version": 1, "audit": [],
        }
        path = server.TASKS / "task-running.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        server.atomic_json(server.AGENT_LEASES / "1234.json", {
            "schema_version": "1.0", "pid": 1234, "nonce": "b" * 36,
            "profile_id": "sales-director", "session_key": "session-a",
            "task_id": "task-running", "task_status": "running",
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        })
        handler.decide("task-running", {"decision": "cancel", "version": 1})
        self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
        self.assertNotIn("approval_request", server.load_json(path))

        (server.AGENT_LEASES / "1234.json").unlink()
        handler.decide("task-running", {"decision": "cancel", "version": 1})
        saved = server.load_json(path)
        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        self.assertEqual(saved["approval_request"]["decision"], "cancel")
        self.assertEqual(saved["version"], 2)
        self.assertEqual(server.task_summaries()[0]["display_status"], "cancelling")

    def test_workbench_can_resume_an_interrupted_task(self):
        task = {
            "task_id": "task-resume", "profile_id": "sales-director", "service_id": "sales-review",
            "workflow_id": "market.sales.pipeline-review", "status": "running", "session_key": "old-session",
            "version": 4, "audit": [],
        }
        path = server.TASKS / "task-resume.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        handler.decide("task-resume", {"decision": "resume", "version": 4})
        saved = server.load_json(path)
        self.assertEqual(replies[-1][0], HTTPStatus.ACCEPTED)
        self.assertEqual(saved["approval_request"]["decision"], "resume")
        self.assertEqual(saved["version"], 5)
        self.assertEqual(server.task_summaries()[0]["display_status"], "resuming")

    def test_interrupted_restart_supersedes_old_task_and_publishes_a_fresh_request(self):
        task = {
            "task_id": "task-restart", "profile_id": "sales-director", "service_id": "sales-review",
            "workflow_id": "market.sales.pipeline-review", "project_id": server.DEFAULT_PROJECT_ID,
            "request": "复盘客户推进情况", "status": "running", "session_key": "old-session",
            "version": 2, "audit": [],
            "requested_model": "agent4market-newapi/gpt-5.5", "requested_thinking_level": "high",
        }
        path = server.TASKS / "task-restart.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        handler.restart_task("task-restart", {"version": 2})
        self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
        request_id = replies[-1][1]["request_id"]
        saved_task = server.load_json(path)
        request = server.load_json(server.REQUESTS / f"{request_id}.json")
        self.assertEqual(saved_task["approval_request"]["decision"], "cancel")
        self.assertEqual(saved_task["approval_request"]["requested_by"], f"local-workbench-restart:{request_id}")
        self.assertEqual(saved_task["superseded_by_task_id"], request_id)
        self.assertEqual(server.task_summaries()[0]["display_status"], "restarting")
        self.assertEqual(request["status"], "requested")
        self.assertEqual(request["request_kind"], "task-restart")
        self.assertEqual(request["restart_of_task_id"], "task-restart")
        self.assertEqual(request["request"], task["request"])
        self.assertEqual(request["requested_model"], task["requested_model"])
        self.assertEqual(request["requested_thinking_level"], "high")

    def test_historical_task_can_be_recreated_without_mutating_the_old_record(self):
        task = {
            "task_id": "task-history", "profile_id": "sales-director", "service_id": "sales-review",
            "workflow_id": "market.sales.pipeline-review", "project_id": server.DEFAULT_PROJECT_ID,
            "request": "再次复盘客户", "status": "cancelled", "session_key": "old-session",
            "version": 7, "audit": [],
        }
        path = server.TASKS / "task-history.json"
        server.atomic_json(path, task)
        handler = object.__new__(server.ControlHandler)
        replies = []
        handler.send_json = lambda status, value: replies.append((status, value))

        handler.restart_task("task-history", {"version": 7})
        self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
        request_id = replies[-1][1]["request_id"]
        self.assertEqual(server.load_json(path), task)
        self.assertEqual(server.load_json(server.REQUESTS / f"{request_id}.json")["status"], "requested")

    def test_cancelled_replacement_is_classified_as_historical_superseded(self):
        display, runtime = server.task_display_state({
            "task_id": "old", "status": "cancelled", "superseded_by_task_id": "new",
        }, {})
        self.assertEqual(display, "superseded")
        self.assertEqual(runtime, "historical")

    def test_prepared_restart_is_published_only_when_the_source_task_is_linked(self):
        request_id = "request-restart-recover"
        record = {
            "schema_version": "1.0", "request_id": request_id, "status": "prepared",
            "source": "local-workbench", "request_kind": "task-restart",
            "restart_of_task_id": "task-old", "source_task_version": 3,
        }
        request_path = server.REQUESTS / f"{request_id}.json"
        server.atomic_json(request_path, record)
        server.atomic_json(server.TASKS / "task-old.json", {
            "task_id": "task-old", "status": "running", "version": 4,
            "superseded_by_task_id": request_id,
            "approval_request": {
                "decision": "cancel", "requested_by": f"local-workbench-restart:{request_id}",
                "requested_at": server.now(), "expected_version": 3,
            },
        })
        server.recover_prepared_task_restarts()
        self.assertEqual(server.load_json(request_path)["status"], "requested")

        orphan_id = "request-restart-orphan"
        orphan_path = server.REQUESTS / f"{orphan_id}.json"
        server.atomic_json(orphan_path, {
            **record, "request_id": orphan_id, "restart_of_task_id": "task-unlinked",
        })
        server.atomic_json(server.TASKS / "task-unlinked.json", {
            "task_id": "task-unlinked", "status": "running", "version": 1,
        })
        server.recover_prepared_task_restarts()
        self.assertEqual(server.load_json(orphan_path)["status"], "prepared")

    def test_daily_schedule_enqueues_at_most_once_per_day(self):
        service = {"id": "sales-review", "workflow": "market.sales.review"}
        settings = {
            "configured": True, "status": "configured", "has_api_key": True,
            "provider_id": "agent4market-newapi", "models": [{"id": "gpt-5.5"}],
        }
        with patch("ui.server.sales_service", return_value=service), patch(
            "ui.server.model_settings_summary", return_value=settings
        ):
            schedule = server.create_schedule_record({
                "name": "每日风险扫描", "project_id": server.DEFAULT_PROJECT_ID,
                "service_id": "sales-review", "time_local": "09:00", "request": "检查重点客户风险与下一步动作。",
                "requested_model": "agent4market-newapi/gpt-5.5", "requested_thinking_level": "high",
            })
        current = datetime.fromisoformat("2026-08-19T10:00:00+08:00")
        self.assertEqual(server.process_due_schedules(current), 1)
        self.assertEqual(server.process_due_schedules(current), 0)
        requests = list(server.REQUESTS.glob("*.json"))
        self.assertEqual(len(requests), 1)
        record = server.load_json(requests[0])
        self.assertEqual(record["schedule_id"], schedule["schedule_id"])
        self.assertEqual(record["scheduled_for"], "2026-08-19")
        self.assertEqual(record["project_id"], server.DEFAULT_PROJECT_ID)
        self.assertEqual(record["requested_model"], "agent4market-newapi/gpt-5.5")
        self.assertEqual(record["requested_thinking_level"], "high")

    def test_local_search_returns_structured_snippet_without_dumping_full_row(self):
        previous_root, previous_inputs, previous_outputs = server.ROOT, server.INPUTS, server.OUTPUTS
        try:
            root = Path(self.temporary.name)
            server.ROOT, server.INPUTS, server.OUTPUTS = root, root / "inputs", root / "outputs"
            sales = root / "data" / "sales"
            sales.mkdir(parents=True)
            (sales / "customers.csv").write_text(
                "customer_id,customer_name,region,sector,owner,stage,health,risks,next_action,private_field\n"
                "c-1,江苏客户,江苏,制造,张三,方案,关注,预算待确认,周五回访,不应直接整行返回\n",
                encoding="utf-8",
            )
            result = server.local_search({"query": "江苏客户", "scopes": ["sales"]})
        finally:
            server.ROOT, server.INPUTS, server.OUTPUTS = previous_root, previous_inputs, previous_outputs
        self.assertEqual(result["results"][0]["title"], "江苏客户")
        self.assertNotIn("private_field", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("不应直接整行返回", json.dumps(result, ensure_ascii=False))

    def test_project_upload_is_confined_and_never_overwrites(self):
        previous_root, previous_inputs = server.ROOT, server.INPUTS
        try:
            root = Path(self.temporary.name)
            server.ROOT, server.INPUTS = root, root / "inputs"
            project = server.create_project_record({"name": "上传测试", "description": ""})
            payload = b"%PDF-1.7 test"
            handler = object.__new__(server.ControlHandler)
            replies = []
            handler.headers = {
                "X-Project-Id": project["project_id"], "X-File-Name": "evidence.pdf",
                "Content-Length": str(len(payload)),
            }
            handler.rfile = io.BytesIO(payload)
            handler.send_json = lambda status, value: replies.append((status, value))
            handler.upload_project_file()
            self.assertEqual(replies[-1][0], HTTPStatus.CREATED)
            target = root / replies[-1][1]["path"]
            self.assertEqual(target.read_bytes(), payload)

            handler.rfile = io.BytesIO(b"replacement")
            handler.headers["Content-Length"] = str(len(b"replacement"))
            handler.upload_project_file()
            self.assertEqual(replies[-1][0], HTTPStatus.CONFLICT)
            self.assertEqual(target.read_bytes(), payload)
        finally:
            server.ROOT, server.INPUTS = previous_root, previous_inputs

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
