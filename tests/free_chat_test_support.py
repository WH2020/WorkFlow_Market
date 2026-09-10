"""Synthetic loopback model for both transport and browser acceptance."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from unittest.mock import patch
from uuid import uuid4

from agent_platform import cli_process_host, wechat_privacy


@contextmanager
def private_runtime():
    base = Path.home() / ("Agent4MarketChatTests-" + uuid4().hex)
    wechat_privacy.ensure_private_directory(base)
    try:
        with patch.dict(os.environ, {name: str(base) for name in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA")}):
            yield base
    finally:
        # Only empty, verified directories created by this synthetic fixture.
        runtime = base / "Agent4MarketFreeChat/Agent4MarketCli"
        if runtime.exists():
            wechat_privacy.verify_private_directory(runtime)
            for request in runtime.iterdir():
                if request.name.startswith(cli_process_host._REQUEST_PREFIX):
                    cli_process_host._delete_owned_request(request, wechat_privacy)
        for directory in (runtime, base / "Agent4MarketFreeChat", base):
            if directory.exists():
                wechat_privacy.verify_private_directory(directory)
                directory.rmdir()


def selection(base_url="http://127.0.0.1:1", api="openai-completions", key="agent4market-chat-fixture/synthetic-model"):
    provider, model = key.split("/", 1)
    return {"key": key, "identity": {"provider_id": provider, "model_id": model, "api": api, "base_url": base_url},
            "label": "合成测试模型", "thinking": "off",
            "transport": {"model": {"id": model, "name": "合成测试模型", "provider": provider, "api": api,
                "baseUrl": base_url if api == "anthropic-messages" else base_url + "/v1", "reasoning": False,
                "input": ["text"], "contextWindow": 32000, "maxTokens": 4096,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}},
                "api_key": "synthetic-key-never-real", "thinking": "off"}}


@contextmanager
def model_server():
    stats = {"requests": [], "disconnected": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw)
            stats["requests"].append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization"),
                                      "api_key": self.headers.get("x-api-key")})
            text = raw.decode("utf-8")
            if "fixture:auth" in text:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"synthetic-key-never-real secret-error-canary"}}')
                return
            if "fixture:redirect" in text:
                self.send_response(307)
                self.send_header("Location", "/unexpected-recipient")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(kind, value):
                data = (f"event: {kind}\n" if kind else "") + "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"
                self.wfile.write(data.encode())
                self.wfile.flush()

            answer = "测试成功。" if "小松" not in text else "记得，你说的是小松。"
            if "fixture:html" in text:
                answer = '<img src="https://not-used.invalid/x" onerror="alert(1)"> 这里只显示文字。'
            slow = "fixture:slow" in text
            try:
                if self.path.startswith("/v1/chat/completions"):
                    def chunk(delta, finish=None):
                        emit(None, {"id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 1, "model": "synthetic-model",
                                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
                    chunk({"role": "assistant", "content": ""})
                    for word in answer:
                        chunk({"content": word})
                        time.sleep(0.03)
                    if slow:
                        for _ in range(150):
                            chunk({"content": "·"})
                            time.sleep(0.1)
                    chunk({}, "stop")
                    self.wfile.write(b"data: [DONE]\n\n")
                elif self.path.startswith("/v1/responses"):
                    response = {"id": "resp_fixture", "object": "response", "created_at": 1, "status": "in_progress", "output": [], "model": "synthetic-model"}
                    item = {"id": "msg_fixture", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
                    emit("response.created", {"type": "response.created", "response": response})
                    emit("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item})
                    emit("response.content_part.added", {"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0,
                                                       "part": {"type": "output_text", "text": "", "annotations": []}})
                    emit("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": answer})
                    item.update(status="completed", content=[{"type": "output_text", "text": answer, "annotations": []}])
                    emit("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item})
                    response.update(status="completed", output=[item], usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
                    emit("response.completed", {"type": "response.completed", "response": response})
                else:
                    emit("message_start", {"type": "message_start", "message": {"id": "msg_fixture", "type": "message", "role": "assistant", "model": "synthetic-model",
                        "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}})
                    emit("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
                    emit("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}})
                    emit("content_block_stop", {"type": "content_block_stop", "index": 0})
                    emit("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}})
                    emit("message_stop", {"type": "message_stop"})
                self.wfile.flush()
            except (OSError, ConnectionError):
                stats["disconnected"].set()
            finally:
                self.close_connection = True

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", stats
    finally:
        http.shutdown(); http.server_close(); worker.join(timeout=3)
