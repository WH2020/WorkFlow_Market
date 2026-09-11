"""Ephemeral, text-only conversations using one explicitly configured recipient.

Only provider configuration/its credential is read. Dialogue stays in memory
and crosses a pipe to the tool-free model adapter, never a task or data store.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Iterator

from . import model_registry
from .cli_provider import CLI_APIS, _node_command, backend_files_available
from .environment import cli_python_executable
from .model_provider import ModelProviderError, load_model_secret
from . import wechat_privacy

MAX_MESSAGES = 40
MAX_INPUT_CHARS = 8000
MAX_HISTORY_BYTES = 96 * 1024
MAX_OUTPUT_BYTES = 128 * 1024
SESSION_TTL = 1800
REQUEST_TIMEOUT = 180
ERROR_MESSAGES = {
    "CANCELLED": "已停止生成，本轮未加入后续对话。",
    "TIMEOUT": "模型响应超时，可以重新发送或开始新对话。",
    "AUTH_FAILED": "模型认证失败，请检查此供应商的凭据或本机 CLI 登录。",
    "MISSING_KEY": "此供应商尚未配置凭据，请前往设置。",
    "MODEL_ERROR": "模型请求未完成，请检查所选模型、网络或本机 CLI 登录后重试。",
    "TOOLS_DISABLED": "模型返回了工具操作请求，自由聊天未执行该操作。",
    "OUTPUT_LIMIT": "模型回复过长，请缩小问题范围后重试。",
    "EMPTY_RESPONSE": "模型没有返回可显示的文字，请重试或调整思考强度。",
    "INVALID_MODEL": "模型配置不可用，请检查设置后开始新对话。",
    "RUNTIME_UNAVAILABLE": "聊天运行环境不可用，请检查应用安装目录与当前用户的私有目录权限。",
}
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
THINKING = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
_CREDENTIAL_BINDING_KEY = secrets.token_bytes(32)


class ChatError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_INPUT", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ChatError("对话或请求标识无效")
    return value


def resolve_model(root: Path, requested: str, thinking: str) -> dict:
    """Read one provider without syncing catalogs or reading global agent state."""
    registry = model_registry.load_registry(root)
    model_registry.recipient_bindings(root, registry)
    key = requested or registry.get("default_model")
    if not isinstance(key, str) or not key or len(key) > 300:
        raise ChatError("请先在设置中配置模型，再开始自由聊天。", "MODEL_UNCONFIGURED", 409)
    provider = next((item for item in registry["providers"] if key.startswith(item["id"] + "/")), None)
    record = next((item for item in provider["models"] if key == provider["id"] + "/" + item["id"]), None) if provider else None
    if not provider or not record or not provider.get("enabled", True) or not record.get("enabled", True):
        raise ChatError("所选模型已停用或不存在，请重新选择。", "MODEL_UNAVAILABLE", 409)
    is_cli = provider["api"] in CLI_APIS
    record = model_registry.model_record(record)
    level = thinking or (record.get("default_thinking_level", "off") if is_cli else "medium" if record["reasoning"] else "off")
    if level not in THINKING:
        raise ChatError("思考强度无效")
    if is_cli:
        if os.name != "nt":
            raise ChatError("当前 CLI 自由聊天仅支持 Windows。", "MODEL_UNAVAILABLE", 409)
        from .cli_model_catalog import thinking_levels
        levels = thinking_levels(record["cli_reasoning"]) if "cli_reasoning" in record else ["off"]
        if level not in levels:
            raise ChatError("所选 CLI 模型不支持该思考强度")
        if not backend_files_available(provider, verify_hash=True):
            raise ChatError("CLI 启动文件不可用或已变化，请重新检测并配置。", "MODEL_UNAVAILABLE", 409)
    elif level != "off" and not record["reasoning"]:
        raise ChatError("该模型未配置思考能力，请选择关闭思考")
    identity = model_registry.recipient(provider, record)
    if is_cli:
        identity = {**identity, "launch_sha256": provider["launch_sha256"]}
    api = record.get("api", provider["api"])
    model = {"id": record["id"], "name": record.get("display_name", record["id"]), "provider": provider["id"], "api": api,
             "baseUrl": provider["base_url"] if is_cli or api == "anthropic-messages" else provider["base_url"] + "/v1",
             "reasoning": record["reasoning"], "input": ["text"], "contextWindow": record["context_window"],
             "maxTokens": min(record["max_tokens"], 8192, max(1, record["context_window"] // 2)),
             "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}
    transport = {"model": model, "thinking": level}
    if is_cli:
        transport["cli"] = {name: copy.deepcopy(provider[name]) for name in (
            "id", "api", "base_url", "executable_path", "command", "args", "version", "launch_sha256", "runner_policy_version")}
        transport["cli"]["models"] = [record]
    else:
        key_value = load_model_secret(root, provider["base_url"], provider_id=provider["id"])
        if not key_value:
            raise ChatError(ERROR_MESSAGES["MISSING_KEY"], "MISSING_KEY", 409)
        transport["api_key"] = key_value
        identity = {**identity, "credential_binding": hmac.new(_CREDENTIAL_BINDING_KEY, key_value.encode(), hashlib.sha256).hexdigest()}
    return {"key": key, "identity": identity, "transport": transport, "thinking": level,
            "label": provider["name"] + " · " + model["name"]}


def transport_events(root: Path, request: dict, cancel: threading.Event) -> Iterator[dict]:
    """Reuse the native process host: neutral cwd and an owned Windows kill Job."""
    names = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA",
             "TEMP", "TMP", "COMSPEC", "LANG", "LC_ALL"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in names}
    # A separate private runtime never repairs or reuses a pre-existing CLI
    # directory. HOME/USERPROFILE/APPDATA and the selected login stay unchanged.
    profile = Path(environment.get("USERPROFILE" if os.name == "nt" else "HOME", ""))
    if not profile.is_absolute():
        raise ChatError(ERROR_MESSAGES["RUNTIME_UNAVAILABLE"], "RUNTIME_UNAVAILABLE", 503)
    runtime = profile / "Agent4MarketFreeChat"
    try:
        if os.path.lexists(runtime):
            wechat_privacy.verify_private_directory(runtime)
        else:
            wechat_privacy.ensure_private_directory(runtime)
    except (OSError, ValueError) as error:
        raise ChatError(ERROR_MESSAGES["RUNTIME_UNAVAILABLE"], "RUNTIME_UNAVAILABLE", 503) from error
    if os.name == "nt":
        environment["LOCALAPPDATA"] = str(runtime)
    else:
        if request.get("cli"):
            raise ChatError("当前 CLI 自由聊天仅支持 Windows。", "MODEL_UNAVAILABLE", 409)
        environment["HOME"] = str(runtime)
    node = _node_command(root, environment, shutil.which)
    if node is None:
        raise ChatError("找不到应用的 Node 运行环境，请检查安装。", "RUNTIME_UNAVAILABLE", 503)
    script = root / "pi/extensions/free-chat.ts"
    host = root / "agent_platform/cli_process_host.py"
    if not script.is_file() or not host.is_file():
        raise ChatError("自由聊天组件不完整，请检查安装。", "RUNTIME_UNAVAILABLE", 503)
    try:
        python = cli_python_executable()
    except (OSError, RuntimeError) as error:
        raise ChatError("找不到可用的 Python 运行环境，请检查安装。", "RUNTIME_UNAVAILABLE", 503) from error
    environment.update({"A4M_CLI_LAUNCH": json.dumps([str(node), "--disable-warning=ExperimentalWarning", str(script)]),
                        "A4M_CLI_SCHEMA": "{}", "AGENT4MARKET_CLI_PYTHON": python, "NO_COLOR": "1"})
    if cancel.is_set():
        yield {"type": "error", "code": "CANCELLED"}
        return
    encoded = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > 512 * 1024:
        raise ChatError("本次对话已达到上下文上限，请开始新对话。", "CONTEXT_LIMIT", 409)
    process = subprocess.Popen([python, "-I", "-B", str(host)], cwd=host.parent, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                               start_new_session=os.name != "nt")
    events: queue.Queue = queue.Queue(maxsize=64)

    def read_output() -> None:
        total = 0
        try:
            while True:
                line = process.stdout.readline(1024 * 1024)
                if not line:
                    break
                total += len(line)
                if len(line) >= 1024 * 1024 or total > 2 * 1024 * 1024:
                    break
                value = json.loads(line)
                while not cancel.is_set():
                    try:
                        events.put(value, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError):
            pass
        finally:
            while True:
                try:
                    events.put(None, timeout=0.1)
                    break
                except queue.Full:
                    if cancel.is_set():
                        break

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    def write_input() -> None:
        try:
            if not cancel.is_set():
                process.stdin.write(encoded)
                process.stdin.flush()
            cancel.wait()
            if process.poll() is None:
                process.stdin.write(b"{}\n")
                process.stdin.flush()
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    started, stopping = time.monotonic(), None
    terminal = None
    reason = "CANCELLED"
    try:
        while True:
            current = time.monotonic()
            if current - started > REQUEST_TIMEOUT and stopping is None:
                reason = "TIMEOUT"
                cancel.set()
            if cancel.is_set() and stopping is None:
                stopping = current
            if stopping is not None and current - stopping > 3:
                break
            try:
                event = events.get(timeout=0.4)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                yield {"type": "ping"}
                continue
            if event is None:
                break
            if not isinstance(event, dict) or terminal is not None:
                raise ValueError("Unexpected model event")
            if event.get("type") == "delta" and isinstance(event.get("text"), str):
                yield {"type": "delta", "text": event["text"]}
            elif event.get("type") in {"done", "error"}:
                terminal = event
            else:
                raise ValueError("Unexpected model event")
        if cancel.is_set():
            yield {"type": "error", "code": reason}
        elif terminal and process.wait(timeout=3) == 0:
            yield terminal
        else:
            yield {"type": "error", "code": "MODEL_ERROR"}
    finally:
        cancel.set()
        if process.poll() is None:
            if os.name == "nt":
                process.kill()  # The reviewed host owns the entire nested Job.
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait(timeout=5)
        writer.join(timeout=2)
        try:
            process.stdin.close()
        except OSError:
            pass  # A stopped pipe may retain buffered bytes that cannot flush.
        reader.join(timeout=2)
        process.stdout.close()


@dataclass
class Session:
    session_id: str
    key: str
    identity: dict
    label: str
    thinking: str
    messages: list[dict] = field(default_factory=list)
    version: int = 0
    touched: float = field(default_factory=time.monotonic)
    requests: list[str] = field(default_factory=list)
    current: Any = None


@dataclass
class Turn:
    session: Session
    request_id: str
    text: str
    transport: dict
    cancel: threading.Event = field(default_factory=threading.Event)


class ChatManager:
    def __init__(self, *, resolver=resolve_model, transport=transport_events, clock=time.monotonic):
        self.sessions: dict[str, Session] = {}
        self.active: dict[str, Turn] = {}
        self.lock = threading.RLock()
        self.resolver, self.transport, self.clock = resolver, transport, clock

    def begin(self, root: Path, payload: dict) -> Turn:
        fields = {"session_id", "request_id", "message", "version", "requested_model", "requested_thinking_level"}
        if not isinstance(payload, dict) or set(payload) - fields:
            raise ChatError("自由聊天只接受文字、会话标识与模型选择")
        text = payload.get("message")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT_CHARS:
            raise ChatError(f"请输入 1 至 {MAX_INPUT_CHARS} 字的消息")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
            raise ChatError("消息包含无效字符")
        request_id = _identifier(payload.get("request_id"))
        session_id = _identifier(payload["session_id"]) if payload.get("session_id") else None
        version = payload.get("version", 0)
        if type(version) is not int or version < 0:
            raise ChatError("对话版本无效")
        requested, thinking = payload.get("requested_model", ""), payload.get("requested_thinking_level", "")
        if not isinstance(requested, str) or not isinstance(thinking, str):
            raise ChatError("模型选择无效")
        try:
            selected = self.resolver(root, requested, thinking)
        except ModelProviderError as error:
            raise ChatError("模型配置不可用，请检查设置。", "MODEL_UNAVAILABLE", 409) from error
        with self.lock:
            for key, session in list(self.sessions.items()):
                if session.current is None and self.clock() - session.touched > SESSION_TTL:
                    del self.sessions[key]
            if len(self.active) >= 2:
                raise ChatError("已有两段对话正在生成，请稍后再试。", "BUSY", 429)
            session = self.sessions.get(session_id) if session_id else None
            if session_id and session is None:
                raise ChatError("对话已过期，请开始新对话。", "SESSION_EXPIRED", 409)
            if session is None:
                if version or len(self.sessions) >= 8:
                    raise ChatError("请关闭已有对话后重新开始。", "SESSION_LIMIT", 409)
                session = Session(secrets.token_hex(16), selected["key"], selected["identity"], selected["label"], selected["thinking"])
            if session.current or version != session.version or request_id in session.requests or request_id in self.active:
                raise ChatError("该轮消息已提交或对话状态已变化，请等待回复。", "TURN_CONFLICT", 409)
            if session.key != selected["key"] or session.identity != selected["identity"] or session.thinking != selected["thinking"]:
                raise ChatError("模型或思考配置已变化，请开始新对话。", "RECIPIENT_CHANGED", 409)
            model = selected["transport"]["model"]
            budget = min(MAX_HISTORY_BYTES, model["contextWindow"] - min(model["maxTokens"], 8192) - 2048)
            messages = [*session.messages, {"role": "user", "content": text.strip()}]
            if len(session.messages) >= MAX_MESSAGES or sum(len(item["content"].encode()) for item in messages) > budget:
                raise ChatError("本次对话已达到上下文上限，请开始新对话或缩短消息。", "CONTEXT_LIMIT", 409)
            turn = Turn(session, request_id, text.strip(), {**selected["transport"], "messages": copy.deepcopy(messages)})
            session.requests = [*session.requests[-31:], request_id]
            session.current, session.touched = turn, self.clock()
            self.sessions[session.session_id] = session
            self.active[request_id] = turn
            return turn

    def events(self, root: Path, turn: Turn) -> Iterator[dict]:
        session, text, terminal = turn.session, "", False
        try:
            yield {"type": "meta", "session_id": session.session_id, "request_id": turn.request_id, "version": session.version,
                   "model": session.key, "model_label": session.label, "thinking": session.thinking}
            for event in self.transport(root, turn.transport, turn.cancel):
                kind = event.get("type")
                if kind == "ping":
                    yield {"type": "ping"}
                elif kind == "delta":
                    chunk = event.get("text")
                    if not isinstance(chunk, str) or len((text + chunk).encode()) > MAX_OUTPUT_BYTES:
                        raise ChatError(ERROR_MESSAGES["OUTPUT_LIMIT"], "OUTPUT_LIMIT")
                    text += chunk
                    yield {"type": "delta", "text": chunk}
                elif kind == "done":
                    if terminal or not text.strip() or event.get("text") != text:
                        raise ChatError(ERROR_MESSAGES["MODEL_ERROR"], "MODEL_ERROR")
                    with self.lock:
                        if turn.cancel.is_set() or self.sessions.get(session.session_id) is not session or session.current is not turn:
                            raise ChatError(ERROR_MESSAGES["CANCELLED"], "CANCELLED")
                        session.messages.extend([{"role": "user", "content": turn.text}, {"role": "assistant", "content": text}])
                        session.version += 1
                        session.touched = self.clock()
                        session.current = None
                    terminal = True
                    yield {"type": "done", "version": session.version, "limited": event.get("limited") is True}
                elif kind == "error":
                    code = event.get("code") if event.get("code") in ERROR_MESSAGES else "MODEL_ERROR"
                    raise ChatError(ERROR_MESSAGES[code], code)
                else:
                    raise ChatError(ERROR_MESSAGES["MODEL_ERROR"], "MODEL_ERROR")
            if not terminal:
                raise ChatError(ERROR_MESSAGES["MODEL_ERROR"], "MODEL_ERROR")
        except (GeneratorExit, BrokenPipeError, ConnectionResetError):
            raise
        except Exception as error:
            code = error.code if isinstance(error, ChatError) and error.code in ERROR_MESSAGES else "MODEL_ERROR"
            yield {"type": "error", "code": code, "error": ERROR_MESSAGES[code]}
        finally:
            self.finish(turn)

    def finish(self, turn: Turn) -> None:
        turn.cancel.set()
        turn.transport.clear()  # Also used when HTTP headers fail before iteration.
        with self.lock:
            if turn.session.current is turn:
                turn.session.current = None
            if self.active.get(turn.request_id) is turn:
                del self.active[turn.request_id]

    def cancel(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"session_id", "request_id"}:
            raise ChatError("停止请求格式无效")
        session_id, request_id = _identifier(payload["session_id"]), _identifier(payload["request_id"])
        with self.lock:
            session = self.sessions.get(session_id)
            if session and session.current and session.current.request_id == request_id:
                session.current.cancel.set()
                return {"status": "stopping"}
        return {"status": "already_finished"}

    def close(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"session_id"}:
            raise ChatError("关闭对话请求格式无效")
        with self.lock:
            session = self.sessions.pop(_identifier(payload["session_id"]), None)
            if session and session.current:
                session.current.cancel.set()
        return {"status": "closed"}
