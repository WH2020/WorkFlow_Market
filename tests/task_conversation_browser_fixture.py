"""Synthetic task metadata + native session; no real models, accounts or WeChat."""
import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ui import server
from agent_platform.app_updates import UpdateChecker


def main():
    with tempfile.TemporaryDirectory(prefix="a4m-task-dialogue-") as directory:
        root, original=Path(directory),server.ROOT
        for name,value in list(vars(server).items()):
            if isinstance(value,Path) and value.is_relative_to(original) and name not in {"UI_ROOT","PROFILES","PLUGINS"}:
                setattr(server,name,root/value.relative_to(original))
        server.ACTIVE_PROFILE_ID="sales-director";server.SCHEDULER_ENABLED=False
        server.APP_UPDATES=UpdateChecker("0.20.7",fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("Offline fixture")))
        server.model_settings_summary=lambda *_a,**_k:{"configured":False,"providers":[]}
        server.search_settings_summary=lambda *_a,**_k:{"configured":False}
        server.search_gateway_settings_summary=lambda *_a,**_k:{"status":"disabled"}
        server.mail_settings_summary=lambda *_a,**_k:{"configured":False}
        server.desktop_runtime_summary=lambda:{"status":"idle","label":"合成验收：不调用模型"}
        server.wechat_http_supported=lambda:False
        agent_dir=root/".pi/agent";sessions=agent_dir/"sessions";sessions.mkdir(parents=True)
        server.pi_agent_dir=lambda:agent_dir
        session=sessions/"fixture.jsonl";stamp=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
        task={"task_id":"task-fixture","profile_id":"sales-director","service_id":"sales-review","workflow_id":"market.sales.pipeline-review","session_key":str(session),"status":"running","version":1,"current_node":"read_accounts","current_stage":0,"completed_nodes":[],"project_id":"project-default","created_at":stamp,"updated_at":stamp,"request":"合成任务：整理客户推进情况","artifacts":[],"audit":[{"action":"task_started","at":stamp}]}
        server.atomic_json(server.TASKS/"task-fixture.json",task)
        entries=[{"type":"session","id":"fixture","cwd":str(root)}, {"type":"custom","id":"scope","parentId":None,"customType":"director-task-state","data":task}]
        parent="scope"
        for i in range(13):
            identity=f"reply-{i}";entries.append({"type":"message","id":identity,"parentId":parent,"timestamp":stamp,"message":{"role":"assistant","content":[{"type":"thinking","thinking":"THOUGHT_CANARY"},{"type":"text","text":f"第 {i+1} 轮合成模型完整回复：客户正在核对试点范围。\n下一步：确认验收时间。\n<img src=x onerror=alert(1)>"}]}});parent=identity
        session.write_text("\n".join(json.dumps(e,ensure_ascii=False) for e in entries)+"\n",encoding="utf-8")
        state={"mode":"batch"}
        def leases():
            mode=state["mode"]
            if mode=="offline":return {}
            at=server.now();activity={"task_id":task["task_id"],"phase":"tool","phase_started_at":stamp,"last_activity_at":at,"tool":"wechat","batch":{"phase":"batch","current":3,"total":12,"messages":2500,"level":0}}
            if mode in {"error","settled","model"}:activity={"task_id":task["task_id"],"phase":mode,"phase_started_at":stamp,"last_activity_at":at,"error_code":"MODEL_ERROR" if mode=="error" else None}
            return {task["task_id"]:{"task_id":task["task_id"],"profile_id":task["profile_id"],"session_key":str(session),"task_status":"running","heartbeat_at":at,"activity":activity,"session_leaf_id":parent}}
        server.live_agent_task_leases=leases
        class Handler(server.ControlHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                if self.path=="/api/coding-assistants/detect":self.send_json(200,{})
                elif self.path.startswith("/api/a4/"):self.send_json(200,{"success":True,"data":{"recommendations":[]}})
                else:super().do_GET()
            def do_POST(self):
                if self.path=="/fixture/state":
                    mode=self.body()["mode"];state["mode"]=mode
                    task["status"]="waiting_approval" if mode=="approval" else "completed" if mode=="completed" else "running"
                    server.atomic_json(server.TASKS/"task-fixture.json",task);self.send_json(200,{"ok":True})
                else:self.send_json(403,{"error":"合成验收禁止执行任务或调用模型"})
        http=server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        def stop():sys.stdin.readline();http.shutdown()
        threading.Thread(target=stop,daemon=True).start()
        print(json.dumps({"url":f"http://127.0.0.1:{http.server_port}"}),flush=True)
        try:http.serve_forever()
        finally:http.server_close()

if __name__=="__main__":main()
