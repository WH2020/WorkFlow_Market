"""Real UI and native chat transport with only private, synthetic model data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.app_updates import UpdateChecker
from agent_platform.free_chat import ChatManager, transport_events
from tests.free_chat_test_support import model_server, private_runtime, selection
from ui import server


def main():
    program = server.ROOT
    task_mode = os.environ.get("A4M_TEST_TASK_HANDOFF") == "1"
    with tempfile.TemporaryDirectory(prefix="a4m-chat-browser-", dir=Path.home()) as directory, private_runtime(), model_server() as (url, stats):
        root = Path(directory)
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(program) and name not in {"UI_ROOT", "PROFILES", "PLUGINS"}:
                setattr(server, name, root / value.relative_to(program))
        server.FREE_CHAT = ChatManager(resolver=lambda *_: selection(url), transport=lambda _root, request, cancel: transport_events(program, request, cancel))
        server.APP_UPDATES = UpdateChecker("0.20.2", fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("Offline fixture")))
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.process_due_schedules = lambda: None
        server.model_settings_summary = lambda *_a, **_k: {"configured": True, "default_model": "agent4market-chat-fixture/synthetic-model", "providers": [
            {"id": "agent4market-chat-fixture", "name": "本机合成模型", "api": "openai-completions", "enabled": True, "status": "configured",
             "models": [{"id": "synthetic-model", "display_name": "聊天验收", "enabled": True, "reasoning": False, "tools": task_mode}]}]}
        server.search_settings_summary = lambda *_a, **_k: {"configured": task_mode, "status":"configured" if task_mode else "missing"}
        server.search_gateway_settings_summary = lambda *_a, **_k: {"configured": False,"status":"disabled"}
        server.mail_settings_summary = lambda *_a, **_k: {"configured": False}
        server.desktop_runtime_summary = lambda: {"status": "offline", "label": "合成验收：仅聊天进程", "mode": "synthetic-chat-test"}
        if task_mode:
            server.task_runtime_selection = lambda payload: {"requested_model":"agent4market-chat-fixture/synthetic-model","requested_thinking_level":"off"}
            server.pi_agent_dir = lambda: root/"synthetic-agent"
        for name, description in (("星河制造（合成）", "本客户正在确认三条产线的试点范围"), ("北辰制造（合成）", "OTHER_CUSTOMER_CONTEXT_CANARY")):
            view = server.business_library_snapshot()
            server.business_library_mutation({"confirmed": True, "operation": "save", "revision": view["revision"],
                                              "type": "customer", "fields": {"name": name, "description": description, "industry": "制造业"}})
        for name, alias in (("苏州产业园项目", "园区,苏园"), ("杭州产业园项目", "园区,杭园")):
            project = server.create_project_record({"name": name, "description": name + "的合成独立背景"})
            view = server.business_library_snapshot()
            entity = next(e for e in view["entities"] if e.get("workspace_id") == project["project_id"])
            server.business_library_mutation({"confirmed": True, "operation": "save", "revision": view["revision"], "id": entity["id"],
                                              "type": "project", "fields": {"aliases": alias, "stage": "方案评审"}})

        class Handler(server.ControlHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if task_mode and self.path == "/fixture/task-requests":
                    self.send_json(200,[server.load_json(p) for p in sorted(server.REQUESTS.glob("*.json"))])
                elif self.path == "/fixture/stats":
                    self.send_json(200, {"requests": stats["requests"], "active": len(server.FREE_CHAT.active), "sessions": len(server.FREE_CHAT.sessions)})
                elif self.path == "/api/coding-assistants/detect":
                    self.send_json(200, {})
                elif self.path.startswith("/api/a4/"):
                    self.send_json(200, {"success": True, "data": {"recommendations": []}})
                else:
                    super().do_GET()

            def do_POST(self):
                if task_mode and self.path == "/fixture/complete":
                    payload=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
                    identity=server.safe_id(payload["request_id"])
                    record=server.load_json(server.REQUESTS/(identity+".json"))
                    session=server.pi_agent_dir()/"sessions"/(identity+".jsonl")
                    task={**record,"task_id":identity,"status":payload.get("status","completed"),"version":1,"session_key":str(session),
                          "updated_at":server.now(),"completed_nodes":[],"current_stage":"completed","artifacts":[]}
                    session.parent.mkdir(parents=True,exist_ok=True)
                    entries=[{"type":"session","id":"session","cwd":str(root)},
                             {"type":"custom","id":"state","parentId":None,"customType":"director-task-state","data":task},
                             {"type":"message","id":"reply","parentId":"state","message":{"role":"assistant","content":[{"type":"text","text":payload.get("result","已核对：交付日期尚未确认，需在汇报中说明。")}]}}]
                    session.write_text("\n".join(json.dumps(e,ensure_ascii=False) for e in entries)+"\n",encoding="utf-8")
                    server.atomic_json(server.TASKS/(identity+".json"),task)
                    self.send_json(200,task);return
                if task_mode and self.path == "/api/task-requests":
                    super().do_POST();return
                if self.path not in {"/api/chat/messages", "/api/chat/cancel", "/api/chat/close", "/api/chat/project-context", "/api/chat/customer-context",
                                     "/api/chat/preferences", "/api/chat/preferences/save", "/api/chat/project-note"}:
                    self.send_json(403, {"error": "Only synthetic chat actions are permitted"})
                    return
                super().do_POST()

        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

        def stop_on_input():
            sys.stdin.readline()
            http.shutdown()

        threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{http.server_port}"}), flush=True)
        try:
            http.serve_forever()
        finally:
            http.server_close()


if __name__ == "__main__":
    main()
