"""Real UI and native chat transport with only private, synthetic model data."""
from __future__ import annotations

import json
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
             "models": [{"id": "synthetic-model", "display_name": "聊天验收", "enabled": True, "reasoning": False, "tools": False}]}]}
        server.search_settings_summary = lambda *_a, **_k: {"configured": False}
        server.search_gateway_settings_summary = lambda *_a, **_k: {"configured": False}
        server.mail_settings_summary = lambda *_a, **_k: {"configured": False}
        server.desktop_runtime_summary = lambda: {"status": "offline", "label": "合成验收：仅聊天进程", "mode": "synthetic-chat-test"}

        class Handler(server.ControlHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/fixture/stats":
                    self.send_json(200, {"requests": stats["requests"], "active": len(server.FREE_CHAT.active), "sessions": len(server.FREE_CHAT.sessions)})
                elif self.path == "/api/coding-assistants/detect":
                    self.send_json(200, {})
                elif self.path.startswith("/api/a4/"):
                    self.send_json(200, {"success": True, "data": {"recommendations": []}})
                else:
                    super().do_GET()

            def do_POST(self):
                if self.path not in {"/api/chat/messages", "/api/chat/cancel", "/api/chat/close"}:
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
