import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent_platform import task_health, task_conversation
from ui import server
from tests import test_free_chat as chat_tests


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.task={"task_id":"t1","profile_id":"sales-director","session_key":"session","status":"running","created_at":(self.now-timedelta(minutes=5)).isoformat()}
        self.lease={**self.task,"task_status":"running","heartbeat_at":self.now.isoformat(),"activity":{"task_id":"t1","phase":"model","last_activity_at":self.now.isoformat(),"phase_started_at":self.now.isoformat()}}
    def view(self):return task_health.summary(self.task,self.lease,[],server.task_display_state(self.task,{"t1":self.lease},self.now)[0],self.now)
    def test_online_does_not_mean_running_after_settled_error(self):
        self.assertEqual(self.view()["state"],"model")
        self.lease["activity"]["phase"]="settled"
        self.assertEqual(self.view()["state"],"settled")
        self.assertEqual(server.task_display_state(self.task,{"t1":self.lease},self.now)[0],"interrupted")
        self.lease["activity"].update(phase="error",error_code="MODEL_ERROR",raw="PRIVATE_CANARY")
        self.assertEqual(self.view()["state"],"error");self.assertNotIn("PRIVATE_CANARY",json.dumps(self.view()))
    def test_wrong_binding_old_lease_approval_and_terminal(self):
        self.lease["session_key"]="wrong";self.assertEqual(self.view()["state"],"disconnected")
        self.lease["session_key"]="session";self.lease["heartbeat_at"]=(self.now-timedelta(seconds=20)).isoformat()
        self.assertEqual(self.view()["state"],"disconnected")
        self.task["status"]="waiting_approval";self.assertEqual(self.view()["state"],"waiting_approval")
        self.task["status"]="completed";self.assertEqual(self.view()["state"],"completed")
    def test_quiet_wait_is_not_failure_and_numeric_batch_only(self):
        self.lease["activity"]["last_activity_at"]=(self.now-timedelta(minutes=3)).isoformat()
        self.assertEqual(self.view()["tone"],"warning");self.assertEqual(self.view()["state"],"model")
        self.lease["activity"].update(phase="tool",tool="wechat",batch={"phase":"batch","current":3,"total":12,"messages":2500,"level":0,"secret":"PRIVATE"})
        self.assertIn("3 / 12",self.view()["title"]);self.assertNotIn("PRIVATE",json.dumps(self.view()))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.sessions=self.root/"agent/sessions";self.sessions.mkdir(parents=True);self.path=self.sessions/"sample.jsonl"
        self.task={"task_id":"t1","profile_id":"sales-director","workflow_id":"fixture","session_key":str(self.path),"version":2,"status":"running"}
        self.entries=[{"type":"session","id":"session","cwd":str(self.root)}]
    def entry(self,kind,**fields):
        identity=f"e{len(self.entries)}";self.entries.append({"type":kind,"id":identity,"parentId":self.entries[-1].get("id") if len(self.entries)>1 else None,**fields});return identity
    def state(self,**fields):return self.entry("custom",customType="director-task-state",data={**self.task,**fields})
    def message(self,text,role="assistant",**fields):return self.entry("message",timestamp="2026-09-17T01:00:00Z",message={"role":role,"content":[{"type":"text","text":text}],**fields})
    def read(self,**kwargs):
        self.path.write_text("\n".join(json.dumps(e) for e in self.entries)+"\n",encoding="utf-8")
        return task_conversation.read(self.root,self.sessions,self.task,**kwargs)
    def test_only_visible_text_bound_by_structured_state_not_marker(self):
        self.message("[DIRECTOR_TASK_CONTEXT t1]","user");self.message("OTHER_TASK")
        self.state();self.message("TOOL_SECRET","toolResult")
        self.entry("message",message={"role":"assistant","content":[{"type":"thinking","thinking":"THOUGHT_SECRET"},{"type":"toolCall","arguments":{"key":"KEY_SECRET"}},{"type":"text","text":"完整回复\n<img onerror=evil>"}],"errorMessage":"ERROR_SECRET"})
        self.state(status="completed");self.message("最终回复");self.message("后来闲聊","user");self.message("UNRELATED")
        result=self.read();self.assertEqual([m["text"] for m in result["messages"]],["完整回复\n<img onerror=evil>","最终回复"])
        for secret in ("SECRET","OTHER_TASK","UNRELATED"):self.assertNotIn(secret,json.dumps(result))
    def test_branch_isolation_pagination_and_reset(self):
        root=self.state()
        for i in range(15):self.message(f"回复{i}")
        leaf=self.entries[-1]["id"];self.entry("message",parentId=root,message={"role":"assistant","content":[{"type":"text","text":"其他分支"}]})
        first=self.read(leaf=leaf);self.assertEqual(len(first["messages"]),12);self.assertTrue(first["has_more"])
        second=self.read(leaf=leaf,after=first["next_after"]);self.assertEqual(len(second["messages"]),3);self.assertFalse(second["has_more"])
        self.assertNotIn("其他分支",json.dumps(first,ensure_ascii=False));self.assertTrue(self.read(leaf=leaf,after="gone")["reset"])
        ambiguous=self.read();self.assertEqual(ambiguous["status"],"unavailable");self.assertEqual(ambiguous["messages"],[])
    def test_rebound_session_pending_has_new_stable_source(self):
        self.state();self.message("旧会话");old=self.read()
        self.path=self.sessions/"new.jsonl";self.task["session_key"]=str(self.path)
        pending=task_conversation.read(self.root,self.sessions,self.task)
        self.assertEqual(pending["status"],"pending");self.assertNotEqual(pending["source_id"],old["source_id"])
        self.entries=[{"type":"session","id":"new","cwd":str(self.root)}]
        self.state();self.message("新会话");ready=self.read()
        self.assertEqual(ready["source_id"],pending["source_id"])
        self.assertEqual([m["text"] for m in ready["messages"]],["新会话"])
    def test_malformed_entries_fail_closed(self):
        for value in ([],None,{"id":"bad","parentId":[]},{"type":"message","id":"bad","message":None},{"type":"custom","id":"bad","customType":"director-task-state","data":[]}):
            with self.subTest(value=value):
                self.entries=[{"type":"session","id":"session","cwd":str(self.root)},value]
                result=self.read();self.assertEqual(result["status"],"unavailable");self.assertEqual(result["messages"],[])
    def test_unrelated_or_forged_state_never_exposes_text(self):
        for field,value in (("task_id","other"),("session_key","other"),("profile_id","other"),("version",999)):
            self.state(**{field:value});self.message("PRIVATE")
        self.assertEqual(self.read()["messages"],[])
    def test_missing_unsafe_large_partial_and_wrong_cwd(self):
        self.assertEqual(task_conversation.read(self.root,self.sessions,self.task)["status"],"pending")
        outside={**self.task,"session_key":str(self.root/"outside.jsonl")}
        self.assertEqual(task_conversation.read(self.root,self.sessions,outside)["status"],"unavailable")
        self.state();self.message("safe");self.read()
        with self.path.open("ab") as stream:stream.write(b'{"unfinished')
        self.assertEqual(task_conversation.read(self.root,self.sessions,self.task)["messages"][0]["text"],"safe")
        with patch.object(task_conversation,"MAX_SESSION",10):self.assertEqual(self.read()["status"],"limited")
        self.entries[0]["cwd"]=str(self.root/"wrong");self.assertEqual(self.read()["status"],"unavailable")
    def test_endpoint_requires_token_and_exact_task(self):
        self.state();self.message("safe");self.read();server_task=self.root/"tasks";server_task.mkdir();(server_task/"t1.json").write_text(json.dumps(self.task),encoding="utf-8")
        helper=chat_tests.ChatHttpTests()
        for token in ("wrong",server.SERVER_TOKEN):
            handler=helper.handler("/api/tasks/t1/conversation",{},token)
            with patch.object(server,"TASKS",server_task),patch.object(server,"ROOT",self.root),patch.object(server,"pi_agent_dir",return_value=self.sessions.parent),patch.object(server,"live_agent_task_leases",return_value={}):handler._do_GET()
            self.assertEqual(handler.send_json.call_args.args[0],200 if token==server.SERVER_TOKEN else 403)

if __name__=="__main__":unittest.main()
