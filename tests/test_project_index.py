from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_platform import business_library as dossiers, project_index, free_chat
from tests.free_chat_test_support import selection
from tests.test_free_chat import payload
from tests import test_business_library_api as dossier_api_tests
from ui import server


def fixture():
    return {"revision": "a" * 64, "links": {"file:fixture": ["p1"], "wechat:PRIVATE_CANARY": ["p1"]},
            "entities": [
                {"id": "owner", "type": "sales", "name": "合成人员"},
                {"id": "customer", "type": "customer", "name": "合成制造客户", "description": "OTHER_CUSTOMER_SECRET"},
                {"id": "p1", "type": "project", "workspace_id": "project-a", "name": "苏州产业园项目", "aliases": "苏园,SZ-PARK",
                 "customer": "customer", "owner": "owner", "stage": "待补充", "description": "仅此项目的合成背景", "source_changed": True,
                 "records": [{"kind": "milestone", "text": "合成记录" + str(i), "done": False, "due": "2026-10-01", "source": "本地手动记录", "owner": "owner", "created_at": f"2026-09-{i+1:02d}"} for i in range(12)]},
                {"id": "p2", "type": "project", "workspace_id": "project-b", "name": "杭州试点项目", "aliases": "杭试", "customer": "customer"},
            ]}


class ProjectIndexTests(unittest.TestCase):
    def test_customer_context_is_explicit_bounded_and_does_not_follow_relationships(self):
        view = fixture()
        customer = view["entities"][1]
        customer.update(industry="合成制造业", records=copy.deepcopy(view["entities"][2]["records"]),
                        contacts=[{"name": "PRIVATE_CONTACT_CANARY"}])
        resolved = project_index.resolve_customer(view, {"entity_id": "customer"})
        self.assertEqual(resolved["customer"]["entity_type"], "customer")
        result = project_index.context(view, {"entity_id": "customer", "revision": view["revision"]}, kind="customer")
        data = json.loads(result["text"])
        self.assertEqual(data["来源"], "本地客户档案")
        self.assertEqual(data["基本信息"]["行业"], "合成制造业")
        self.assertEqual(len(data["最近记录"]), 8)
        for excluded in ("PRIVATE_CONTACT_CANARY", "苏州产业园", "杭州试点", "PRIVATE_CANARY"):
            self.assertNotIn(excluded, result["text"])
        for selection in ({"entity_id": "p1", "revision": view["revision"]},
                          {"entity_id": "customer", "revision": "old"},
                          {"entity_id": "customer", "revision": view["revision"], "text": "forged"}):
            with self.assertRaises(dossiers.DossierError):
                project_index.context(view, selection, kind="customer")
        for marker in ("archived", "source_unavailable"):
            bad = copy.deepcopy(view); bad["entities"][1][marker] = True
            with self.assertRaises(dossiers.DossierError):
                project_index.resolve_customer(bad, {"entity_id": "customer"})
            with self.assertRaises(dossiers.DossierError):
                project_index.context(bad, {"entity_id": "customer", "revision": bad["revision"]}, kind="customer")

    def test_customer_http_context_requires_token_and_rebuilds_server_facts(self):
        view = fixture(); helper = dossier_api_tests.DossierApiTests()
        for token in (False, True):
            handler = helper.handler("/api/chat/customer-context", {"entity_id": "customer"}, token)
            with patch.object(server, "business_library_snapshot", return_value=view), patch.object(server.FREE_CHAT, "begin") as begin:
                handler._do_POST()
            self.assertEqual(handler.replies[0][0], 200 if token else 403)
            begin.assert_not_called()
        request = payload(customer_selection={"entity_id": "customer", "revision": view["revision"]})
        with patch.object(server, "business_library_snapshot", return_value=view), patch.object(server.FREE_CHAT, "begin", side_effect=free_chat.ChatError("stop")) as begin:
            helper.handler("/api/chat/messages", request)._do_POST()
        self.assertNotIn("customer_selection", begin.call_args.args[1])
        self.assertEqual(begin.call_args.kwargs["project_context"]["entity_id"], "customer")
        request["project_selection"] = {"entity_id": "p1", "revision": view["revision"]}
        with patch.object(server, "business_library_snapshot", return_value=view), patch.object(server.FREE_CHAT, "begin") as begin:
            helper.handler("/api/chat/messages", request)._do_POST()
        begin.assert_not_called()

    def test_names_aliases_and_explicit_choice(self):
        for query in ("苏州产业园项目进展", "苏园负责人是谁", "ＳＺ－ＰＡＲＫ 状态", "Tell me about SZ-PARK please"):
            self.assertEqual(project_index.resolve(fixture(), {"message": query})["project"]["entity_id"], "p1")
        view = fixture(); view["entities"][-1]["aliases"] = "苏园"
        result = project_index.resolve(view, {"message": "苏园怎么样"})
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(project_index.resolve(view, {"message": "苏园怎么样", "selected_entity_id": "p2"})["project"]["entity_id"], "p2")

    def test_customer_weak_match_and_multiple_project_mentions_require_choice(self):
        for query in ("合成制造客户的项目怎么样", "苏州产业园项目和杭州试点项目对比"):
            result = project_index.resolve(fixture(), {"message": query})
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(project_index.resolve(fixture(), {"message": "看看苏州产业园"})["status"], "ambiguous")

    def test_followups_and_unknown_named_project_do_not_cross_scopes(self):
        for query in ("它负责人是谁", "这个项目还有什么缺失"):
            self.assertEqual(project_index.resolve(fixture(), {"message": query, "current_entity_id": "p1"})["project"]["entity_id"], "p1")
        for query in ("北京未收录项目", "北京园区负责人是谁", "你好"):
            self.assertEqual(project_index.resolve(fixture(), {"message": query, "current_entity_id": "p1"})["status"], "not_found")
        self.assertEqual(project_index.resolve(fixture(), {"message": "你好"})["status"], "not_found")
        self.assertEqual(project_index.resolve(fixture(), {"message": "notSZ-PARKother"})["status"], "not_found")

    def test_archived_unavailable_stale_and_forged_context_rejected(self):
        view = fixture()
        for marker in ("archived", "source_unavailable"):
            bad = copy.deepcopy(view); bad["entities"][2][marker] = True
            self.assertEqual(project_index.resolve(bad, {"message": "苏园"})["status"], "not_found")
            with self.assertRaises(dossiers.DossierError):
                project_index.context(bad, {"entity_id": "p1", "revision": bad["revision"]})
        for choice in ({"entity_id": "customer", "revision": view["revision"]}, {"entity_id": "p1", "revision": "old"},
                       {"entity_id": "p1", "revision": view["revision"], "snapshot": "forged"}):
            with self.assertRaises(dossiers.DossierError): project_index.context(view, choice)

    def test_bounded_provenance_context_excludes_other_records_files_and_wechat(self):
        view = fixture(); context = project_index.context(view, {"entity_id": "p1", "revision": view["revision"]})
        data = json.loads(context["text"])
        self.assertEqual(data["记录总数"], 12); self.assertEqual(len(data["最近记录"]), 8)
        self.assertEqual(data["最近记录"][0]["内容"], "合成记录11")
        self.assertIn("阶段", data["缺失信息"]); self.assertTrue(data["来源变化待核对"])
        self.assertEqual(data["关联文件数"], 1)
        for secret in ("PRIVATE_CANARY", "OTHER_CUSTOMER_SECRET", "杭州试点"):
            self.assertNotIn(secret, context["text"])

    def test_aliases_persist_without_creating_another_index_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = dossiers.source_entity("project", "workspace:project-a", "合成项目", workspace_id="project-a", archived=False)
            view = dossiers.snapshot(root, [source])
            dossiers.mutate(root, {"confirmed": True, "operation": "save", "revision": view["revision"], "id": source["id"],
                                  "type": "project", "fields": {"aliases": "合项,Fixture A"}}, sources=[source])
            updated = dossiers.snapshot(root, [source])
            self.assertEqual(project_index.resolve(updated, {"message": "合项进度"})["status"], "matched")
            self.assertEqual([p.name for p in (root / "data/library").iterdir()], ["dossiers.sqlite3"])

    def test_http_lookup_requires_token_and_never_calls_model_or_file_catalog(self):
        helper = dossier_api_tests.DossierApiTests()
        for token in (False, True):
            handler = helper.handler("/api/chat/project-context", {"message": "苏园"}, token)
            with patch.object(server, "business_library_snapshot", return_value=fixture()), patch.object(server.FREE_CHAT, "begin") as begin, patch.object(server, "library_snapshot", side_effect=AssertionError("no files")):
                handler._do_POST()
            self.assertEqual(handler.replies[0][0], 200 if token else 403)
            begin.assert_not_called()

    def test_server_rebuilds_context_and_rejects_browser_supplied_body(self):
        view = fixture(); helper = dossier_api_tests.DossierApiTests()
        request = payload(project_selection={"entity_id": "p1", "revision": view["revision"]})
        handler = helper.handler("/api/chat/messages", request)
        with patch.object(server, "business_library_snapshot", return_value=view), patch.object(server.FREE_CHAT, "begin", side_effect=free_chat.ChatError("test stop")) as begin:
            handler._do_POST()
        self.assertNotIn("project_selection", begin.call_args.args[1])
        self.assertEqual(begin.call_args.kwargs["project_context"]["entity_id"], "p1")
        request["project_selection"]["snapshot"] = "forged"
        with patch.object(server, "business_library_snapshot", return_value=view), patch.object(server.FREE_CHAT, "begin") as begin:
            helper.handler("/api/chat/messages", request)._do_POST()
        begin.assert_not_called()


class ProjectChatTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        def transport(root, request, cancel):
            self.seen.append(copy.deepcopy(request))
            if cancel.is_set(): yield {"type": "error", "code": "CANCELLED"}
            else:
                yield {"type": "delta", "text": "合成回答"}
                yield {"type": "done", "text": "合成回答"}
        self.manager = free_chat.ChatManager(resolver=lambda *_: selection(), transport=transport)
        self.context = project_index.context(fixture(), {"entity_id": "p1", "revision": "a" * 64})

    def complete(self, context=None, **changes):
        turn = self.manager.begin(Path("."), payload(**changes), project_context=context)
        self.assertEqual(list(self.manager.events(Path("."), turn))[-1]["type"], "done")
        return turn

    def test_current_context_refreshes_without_retaining_raw_context_in_history(self):
        first = self.complete(self.context)
        updated = {**self.context, "text": "新档案", "revision": "b" * 64}
        self.complete(updated, session_id=first.session.session_id, version=1)
        self.assertEqual(self.seen[-1]["project_context"]["text"], "新档案")
        self.assertNotIn("最近记录", json.dumps(first.session.messages, ensure_ascii=False))
        self.complete()
        self.assertNotIn("project_context", self.seen[-1])

    def test_scope_change_or_clear_requires_new_conversation(self):
        first = self.complete(self.context)
        for context in (None, {**self.context, "entity_id": "p2"}):
            with self.assertRaises(free_chat.ChatError) as raised:
                self.manager.begin(Path("."), payload(session_id=first.session.session_id, version=1), project_context=context)
            self.assertEqual(raised.exception.code, "PROJECT_CHANGED")
        self.assertEqual(len(self.seen), 1)

    def test_customer_project_switch_cannot_reuse_previous_history(self):
        customer = project_index.context(fixture(), {"entity_id": "customer", "revision": "a" * 64}, kind="customer")
        first = self.complete(customer)
        for context in (None, self.context):
            with self.assertRaises(free_chat.ChatError) as raised:
                self.manager.begin(Path("."), payload(session_id=first.session.session_id, version=1), project_context=context)
            self.assertEqual(raised.exception.code, "PROJECT_CHANGED")
        self.assertEqual(len(self.seen), 1)

    def test_unscoped_history_cannot_be_reused_for_project(self):
        first = self.complete()
        with self.assertRaises(free_chat.ChatError) as raised:
            self.manager.begin(Path("."), payload(session_id=first.session.session_id, version=1), project_context=self.context)
        self.assertEqual(raised.exception.code, "PROJECT_CHANGED")
        self.assertEqual(len(self.seen), 1)

    def test_cancel_does_not_bind_scope_and_recipient_change_is_still_rejected(self):
        turn = self.manager.begin(Path("."), payload(), project_context=self.context)
        turn.cancel.set(); list(self.manager.events(Path("."), turn))
        self.assertEqual(turn.session.project_id, "")
        retry = self.complete({**self.context, "entity_id": "p2"}, session_id=turn.session.session_id)
        self.assertEqual(retry.session.project_id, "p2")
        first = self.complete(self.context)
        self.manager.resolver = lambda *_: {**selection(), "identity": {"endpoint": "different"}}
        with self.assertRaises(free_chat.ChatError) as raised:
            self.manager.begin(Path("."), payload(session_id=first.session.session_id, version=1), project_context=self.context)
        self.assertEqual(raised.exception.code, "RECIPIENT_CHANGED")

    def test_project_context_counts_against_model_budget(self):
        with self.assertRaises(free_chat.ChatError) as raised:
            self.manager.begin(Path("."), payload(), project_context={**self.context, "text": "x" * 100000})
        self.assertEqual(raised.exception.code, "CONTEXT_LIMIT")
        self.assertFalse(self.manager.active)


if __name__ == "__main__": unittest.main()
