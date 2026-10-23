from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_platform import business_library as dossiers, chat_assistance as assistance, free_chat, project_index
from tests.free_chat_test_support import selection
from tests import test_business_library_api as dossier_api
from tests.test_free_chat import payload
from ui import server


class AssistanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = [dossiers.source_entity("project", "workspace:p1", "合成项目", workspace_id="p1", archived=False)]
        self.entity = self.sources[0]["id"]
        self.seen = []

        def transport(root, request, cancel):
            self.seen.append(copy.deepcopy(request))
            if request["messages"][-1]["content"] == "失败":
                yield {"type": "error", "code": "MODEL_ERROR"}
            else:
                yield {"type": "delta", "text": "合成回复"}
                yield {"type": "done", "text": "合成回复"}

        self.manager = free_chat.ChatManager(resolver=lambda *_: selection(), transport=transport)

    def view(self):
        return dossiers.snapshot(self.root, self.sources)

    def context(self):
        return project_index.context(self.view(), {"entity_id": self.entity, "revision": self.view()["revision"]})

    def complete(self, text="项目下周评审", **kwargs):
        turn = self.manager.begin(self.root, payload(message=text), project_context=self.context(), **kwargs)
        events = list(self.manager.events(self.root, turn))
        return turn, events

    def save_pref(self, key="length", value="brief", view=None):
        return assistance.change_preference(self.root, {"confirmed": True, "revision": (view or assistance.preferences(self.root))["revision"], "key": key, "value": value})

    def note_payload(self, turn, **changes):
        return {"session_id": turn.session.session_id, "request_id": turn.request_id, "confirmed": True,
                "revision": self.view()["revision"], "text": "客户下周内部评审；预算待确认", "kind": "followup", "due": "", "include_source_quote": True, **changes}

    def http(self, path, body):
        handler = dossier_api.DossierApiTests().handler("/api/chat/" + path, body)
        with patch.object(server, "ROOT", self.root), patch.object(server, "FREE_CHAT", self.manager), \
                patch.object(server, "dossier_sources", return_value=self.sources), patch.object(server, "project_records", return_value=[]), \
                patch.object(server, "ACTIVE_PROFILE_ID", "sales-director"):
            handler._do_POST()
        return handler.replies[0]

    def test_empty_read_is_non_mutating_and_preferences_survive_new_reader(self):
        initial = assistance.preferences(self.root)
        self.assertEqual(initial["values"], {})
        self.assertFalse((self.root / "data").exists())
        saved = self.save_pref()
        self.assertEqual(assistance.preferences(self.root), saved)
        self.assertEqual(assistance.preference_context(saved)["text"], '{"回答详略": "简短回答"}')
        self.assertEqual(dossiers.snapshot(self.root, self.sources)["entities"], self.view()["entities"])
        forgotten = self.save_pref(value="")
        self.assertIsNone(assistance.preference_context(forgotten))

    def test_saved_preferences_survive_a_fresh_python_process(self):
        self.save_pref()
        code = "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from agent_platform.chat_assistance import preferences; print(json.dumps(preferences(Path(sys.argv[2]))['values']))"
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path(__file__).resolve().parents[1]), str(self.root)],
                                capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(json.loads(result.stdout)["length"]["value"], "brief")

    def test_preferences_do_not_change_dossier_revision_and_cannot_silently_overwrite(self):
        original, stale = self.view()["revision"], assistance.preferences(self.root)
        self.save_pref()
        self.assertEqual(original, self.view()["revision"])
        with self.assertRaisesRegex(dossiers.DossierError, "已变化"):
            self.save_pref(value="detailed", view=stale)
        self.assertEqual(assistance.preferences(self.root)["values"]["length"]["value"], "brief")

    def test_only_explicit_enum_preferences_are_accepted(self):
        base = {"confirmed": True, "revision": assistance.preferences(self.root)["revision"], "key": "length", "value": "brief"}
        for delta in ({"confirmed": False}, {"key": "personality"}, {"value": "<script>"}, {"value": []}, {"key": []}, {"instruction": "read files"}):
            with self.subTest(delta=delta), self.assertRaises(dossiers.DossierError):
                assistance.change_preference(self.root, {**base, **delta})
        self.assertEqual(assistance.preferences(self.root)["values"], {})

    def test_explicit_preference_suggestions_are_not_automatic_writes(self):
        view = assistance.preferences(self.root)
        cards = assistance.suggestions("以后回答请简短，先给结论，用中文", view)
        self.assertEqual({c["key"] for c in cards}, {"length", "structure", "language"})
        self.assertEqual(assistance.preferences(self.root), view)
        for text in ("客户希望方案简短", "我不喜欢简短回答", "以后用中文还是英文？", "例如：以后回答简短", "他说以后用英文", "以后简短又详细", "我希望客户提供详细报价", "以后客户用英文交流"):
            self.assertEqual(assistance.suggestions(text, view), [], text)
        self.assertEqual(assistance.suggestions("以后简短回答", self.save_pref()), [])

    def test_bad_preference_store_is_rejected_not_replaced(self):
        self.save_pref()
        with dossiers._connection(self.root, True) as connection:
            connection.execute("UPDATE chat_preferences SET payload='null'")
        with self.assertRaises(dossiers.DossierError):
            assistance.preferences(self.root)
        with dossiers._connection(self.root) as connection:
            self.assertEqual(connection.execute("SELECT payload FROM chat_preferences").fetchone()[0], "null")

    def test_project_cards_show_only_bounded_server_context(self):
        cards = assistance.context_cards(self.context(), self.save_pref())
        self.assertEqual([c["type"] for c in cards], ["project", "preferences_used"])
        self.assertIn("未读取文件正文", cards[0]["boundary"])
        self.assertIn("负责人", cards[0]["missing"])
        self.assertEqual(cards[0]["records"], [])

    def test_preferences_are_per_turn_data_not_history_or_tool_access(self):
        preferences = assistance.preference_context(self.save_pref())
        turn, events = self.complete(preference_context=preferences)
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(self.seen[-1]["preference_context"], preferences)
        self.assertNotIn("简短回答", json.dumps(turn.session.messages, ensure_ascii=False))
        retry = self.manager.begin(self.root, payload(message="继续", session_id=turn.session.session_id, version=1), project_context=self.context())
        list(self.manager.events(self.root, retry))
        self.assertNotIn("preference_context", self.seen[-1])
        with self.assertRaises(free_chat.ChatError):
            self.manager.begin(self.root, payload(preference_context=preferences))

    def test_preferences_count_against_context_budget(self):
        with self.assertRaises(free_chat.ChatError) as error:
            self.complete(preference_context={"revision": "a" * 64, "text": "x" * 100000})
        self.assertEqual(error.exception.code, "CONTEXT_LIMIT")

    def test_note_requires_completed_turn_confirmation_and_current_revision(self):
        turn, _ = self.complete()
        request = self.note_payload(turn)
        for change, status in (({"confirmed": False}, 400), ({"request_id": "a" * 32}, 400), ({"session_id": "b" * 32}, 409),
                               ({"revision": "c" * 64}, 409), ({"entity_id": "other"}, 400), ({"due": "2026-02-31"}, 400)):
            self.assertEqual(self.http("project-note", {**request, **change})[0], status)
        self.assertEqual(self.view()["entities"][0]["records"], [])
        self.assertEqual(self.http("project-note", request)[0], 200)
        record = self.view()["entities"][0]["records"][0]
        self.assertIn("用户确认的对话记录（非独立核验）", record["source"])
        self.assertIn(turn.text, record["source"])
        self.assertEqual(record["text"], request["text"])
        self.assertEqual(self.http("project-note", request)[0], 200, "Lost HTTP response retries must not duplicate notes")
        self.assertEqual(len(self.view()["entities"][0]["records"]), 1)
        self.assertEqual(self.http("project-note", {**request, "text": "覆盖尝试"})[0], 400)
        self.assertIn(request["text"], self.context()["text"])

    def test_failed_cancelled_closed_and_busy_turns_cannot_save(self):
        failed, events = self.complete("失败")
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.http("project-note", self.note_payload(failed))[0], 400)
        cancelled = self.manager.begin(self.root, payload(), project_context=self.context())
        cancelled.cancel.set(); list(self.manager.events(self.root, cancelled))
        self.assertEqual(self.http("project-note", self.note_payload(cancelled))[0], 400)
        turn, _ = self.complete()
        running = self.manager.begin(self.root, payload(session_id=turn.session.session_id, version=1), project_context=self.context())
        self.assertEqual(self.http("project-note", self.note_payload(turn))[0], 409)
        self.manager.finish(running)
        self.manager.close({"session_id": turn.session.session_id})
        self.assertEqual(self.http("project-note", self.note_payload(turn))[0], 409)

    def test_source_quote_only_persists_when_explicitly_selected(self):
        turn, _ = self.complete("原话含不应长期保存的合成细节")
        request = self.note_payload(turn, include_source_quote=False)
        self.assertEqual(self.http("project-note", request)[0], 200)
        record = self.view()["entities"][0]["records"][0]
        self.assertNotIn(turn.text, record["source"])
        self.assertIn("用户确认的对话记录", record["source"])
        self.assertEqual(self.http("project-note", {**request, "include_source_quote": True})[0], 400)

    def test_stream_contains_context_then_success_cards_but_no_automatic_notes(self):
        self.save_pref()
        handler = dossier_api.DossierApiTests().handler("/api/chat/messages", payload(message="合成项目下周评审", ui_version=1, use_preferences=True, project_selection={"entity_id": self.entity, "revision": self.view()["revision"]}))
        handler.connection = Mock(); handler.wfile = io.BytesIO()
        handler.send_response = Mock(); handler.send_header = Mock(); handler.end_headers = Mock()
        with patch.object(server, "ROOT", self.root), patch.object(server, "FREE_CHAT", self.manager), \
                patch.object(server, "business_library_snapshot", side_effect=self.view):
            handler._do_POST()
        events = [json.loads(line) for line in handler.wfile.getvalue().splitlines()]
        self.assertEqual([e["type"] for e in events], ["meta", "ui", "delta", "done"])
        self.assertEqual(events[1]["cards"][0]["type"], "project")
        self.assertEqual(events[-1]["cards"][0]["type"], "project_note")
        self.assertEqual(self.view()["entities"][0]["records"], [])

    def test_preferences_disabled_does_not_read_store_or_send_preferences(self):
        handler = dossier_api.DossierApiTests().handler("/api/chat/messages", payload(message="以后简短回答", ui_version=1, use_preferences=False))
        handler.connection = Mock(); handler.wfile = io.BytesIO()
        handler.send_response = Mock(); handler.send_header = Mock(); handler.end_headers = Mock()
        with patch.object(server, "ROOT", self.root), patch.object(server, "FREE_CHAT", self.manager), \
                patch.object(assistance, "preferences", side_effect=AssertionError("No preference reads")):
            handler._do_POST()
        self.assertNotIn("preference_context", self.seen[-1])
        self.assertEqual(json.loads(handler.wfile.getvalue().splitlines()[-1])["cards"], [])

    def test_legacy_client_remains_text_only_and_does_not_read_preferences(self):
        handler = dossier_api.DossierApiTests().handler("/api/chat/messages", payload())
        handler.connection = Mock(); handler.wfile = io.BytesIO()
        handler.send_response = Mock(); handler.send_header = Mock(); handler.end_headers = Mock()
        with patch.object(server, "ROOT", self.root), patch.object(server, "FREE_CHAT", self.manager), \
                patch.object(assistance, "preferences", side_effect=AssertionError("Legacy page must not read memories")):
            handler._do_POST()
        events = [json.loads(line) for line in handler.wfile.getvalue().splitlines()]
        self.assertEqual([e["type"] for e in events], ["meta", "delta", "done"])
        self.assertNotIn("cards", events[-1])

    def test_bad_card_version_and_memory_flags_rejected_before_model(self):
        for change in ({"ui_version": 2}, {"ui_version": True}, {"use_preferences": "yes"}):
            self.assertEqual(self.http("messages", payload(**change))[0], 400)
        self.assertEqual(self.seen, [])

    def test_preferences_endpoints_and_profile_gate(self):
        self.assertEqual(self.http("preferences", {})[0], 200)
        self.assertEqual(self.http("preferences", {"user": "someone-else"})[0], 400)
        view = assistance.preferences(self.root)
        body = {"confirmed": True, "key": "length", "value": "brief", "revision": view["revision"]}
        self.assertEqual(self.http("preferences/save", body)[0], 200)
        self.assertEqual(self.http("preferences/save", body)[0], 409)
        handler = dossier_api.DossierApiTests().handler("/api/chat/preferences", {})
        with patch.object(server, "ACTIVE_PROFILE_ID", "other"), patch.object(assistance, "preferences") as read:
            handler._do_POST()
        self.assertEqual(handler.replies[0][0], 403)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
