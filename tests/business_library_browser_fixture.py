"""Production HTTP/UI against disposable synthetic data; no AI core or cloud."""
from __future__ import annotations
import json
import hashlib
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ui import server
from agent_platform.app_updates import UpdateChecker
from agent_platform.library_store import register_file


def main():
    with tempfile.TemporaryDirectory(prefix="a4m-library-browser-") as directory:
        root, original = Path(directory), server.ROOT
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(original) and name not in {"UI_ROOT", "PROFILES", "PLUGINS"}:
                setattr(server, name, root / value.relative_to(original))
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.process_due_schedules = lambda: None
        server.APP_UPDATES = UpdateChecker("0.20.7", fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("Offline fixture")))
        server.model_settings_summary = lambda *_a, **_k: {"configured":True,"default_model":"agent4market-fixture/test-model","providers":[
            {"id":"agent4market-fixture","name":"合成模型（不执行）","status":"configured","enabled":True,"api":"openai-completions",
             "models":[{"id":"test-model","display_name":"合成模型","enabled":True,"tools":True,"reasoning":False}]}]}
        server.task_runtime_selection = lambda payload: {"requested_model":"agent4market-fixture/test-model","requested_thinking_level":"off"}
        server.search_settings_summary = lambda *_a, **_k: {"status":"configured","configured":True}
        server.search_gateway_settings_summary = lambda *_a, **_k: {"status":"disabled"}
        server.mail_settings_summary = lambda *_a, **_k: {"configured":False}
        server.desktop_runtime_summary = lambda: {"status":"offline","label":"合成验收：无智能核心","mode":"test"}
        server.wechat_http_supported = lambda: False
        sales = root / "data/sales"
        sales.mkdir(parents=True)
        legacy = "customer_id,customer_name,owner,stage,updated_at\nfixture-customer,星河制造（合成）,历史负责人,需求沟通,2026-09-16\n"
        (sales/"customers.csv").write_text(legacy, encoding="utf-8")
        (sales/"salespeople.json").write_text(json.dumps({"salespeople":[{"salesperson_id":"fixture-seller","name":"林晓（合成）","active":True}]}), encoding="utf-8")
        file = root/"inputs/library/brief.txt"
        file.parent.mkdir(parents=True);file.write_text("合成需求：试点范围待确认。",encoding="utf-8")
        register_file(root,{"library_id":"library-fixture","path":"inputs/library/brief.txt","filename":"brief.txt","sha256":hashlib.sha256(file.read_bytes()).hexdigest(),"size":file.stat().st_size})

        class Handler(server.ControlHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                if self.path=="/fixture/state":
                    records=[server.load_json(p) for p in server.REQUESTS.glob("*.json")] if server.REQUESTS.exists() else []
                    self.send_json(200,{"requests":records,"legacy_unchanged":(sales/"customers.csv").read_text(encoding="utf-8")==legacy})
                elif self.path=="/api/coding-assistants/detect": self.send_json(200,{})
                elif self.path.startswith("/api/a4/"): self.send_json(200,{"success":True,"data":{"recommendations":[]}})
                else: super().do_GET()
            def do_POST(self):
                if self.path=="/fixture/complete":
                    for p in server.REQUESTS.glob("*.json"):
                        r=server.load_json(p)
                        task={**r,"task_id":r["request_id"],"status":"completed","version":1,"completed_nodes":[],"current_node":None,"waiting_node":None,"waiting_nodes":[],"artifacts":[],"updated_at":server.now()}
                        server.atomic_json(server.TASKS/(task["task_id"]+".json"),task)
                    self.send_json(200,{"ok":True})
                elif self.path.startswith("/api/chat/"):
                    self.send_json(400,{"error":"测试夹具不调用模型"})
                elif self.path in {"/api/business-library","/api/projects","/api/task-requests"} or self.path.startswith("/api/projects/"):
                    super().do_POST()
                else: self.send_json(403,{"error":"Synthetic fixture: operation disabled"})

        http=server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
        def stop():
            sys.stdin.readline();http.shutdown()
        threading.Thread(target=stop,daemon=True).start()
        print(json.dumps({"url":f"http://127.0.0.1:{http.server_port}"}),flush=True)
        try: http.serve_forever()
        finally: http.server_close()


if __name__=="__main__": main()
