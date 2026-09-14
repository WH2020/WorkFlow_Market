"""Bounded Codex model/list discovery in a credential-free, empty CLI home.

The list is CLI metadata, not proof of account entitlement. No account, thread,
turn, history, or tool RPC is sent. Only normalized metadata is cached in memory.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Mapping

from .model_provider import ModelProviderError

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
MAX_MODELS = 128
MAX_OUTPUT = 2 * 1024 * 1024
QUERY_TIMEOUT = 20
CACHE_SECONDS = 900
_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_QUERIES = threading.BoundedSemaphore(2)
_ERROR = "无法读取 Codex 模型目录；配置未改变。请重新检测 CLI 后重试。"


def effort_for_level(level: str) -> str:
    return "none" if level == "off" else level


def validate_reasoning(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"supported_efforts", "default_effort"}:
        raise ModelProviderError("CLI 思考能力配置无效")
    efforts = value["supported_efforts"]
    if (not isinstance(efforts, list) or not 1 <= len(efforts) <= 32
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", v) for v in efforts)
            or len(set(efforts)) != len(efforts) or value["default_effort"] not in efforts):
        raise ModelProviderError("CLI 思考能力配置无效")
    return {"supported_efforts": list(efforts), "default_effort": value["default_effort"]}


def thinking_levels(reasoning: Mapping[str, Any]) -> list[str]:
    return [level for level in THINKING_LEVELS if effort_for_level(level) in reasoning["supported_efforts"]]


def normalize_model(value: Any) -> dict[str, Any] | None:
    from .model_registry import model_id

    if not isinstance(value, dict):
        raise ModelProviderError("Codex 返回的模型目录格式无效")
    if value.get("hidden") is True:
        return None
    identifier = model_id(value.get("model"))  # `id` is an opaque catalog row ID, not necessarily the model slug.
    display = value.get("displayName", identifier)
    efforts = value.get("supportedReasoningEfforts")
    if (not isinstance(display, str) or not 1 <= len(display) <= 200 or not display.isprintable()
            or not isinstance(efforts, list) or not all(isinstance(v, dict) for v in efforts)):
        raise ModelProviderError("Codex 返回的模型元数据无效")
    reasoning = validate_reasoning({
        "supported_efforts": [v.get("reasoningEffort") for v in efforts] or (["none"] if value.get("defaultReasoningEffort") == "none" else []),
        "default_effort": value.get("defaultReasoningEffort"),
    })
    return {"id": identifier, "display_name": display, "cli_reasoning": reasoning,
            "supported_thinking_levels": thinking_levels(reasoning), "is_default": value.get("isDefault") is True}


def catalog_arguments(backend: Mapping[str, Any]) -> list[str]:
    from .cli_process_host import CATALOG_QUERY_ARGS

    # No user config, rules, credentials, project files, or model turns are used.
    return [str(backend["command"]), *backend["args"], *CATALOG_QUERY_ARGS]


def _query_model_list(backend: Mapping[str, Any], *, environ: Mapping[str, str] | None = None) -> list[Any]:
    # The parent owns cleanup even when a timed-out process host is terminated.
    with tempfile.TemporaryDirectory(prefix="agent4market-model-catalog-") as directory:
        return _query_model_list_at(backend, directory, environ=environ)


def _query_model_list_at(backend: Mapping[str, Any], directory: str, *, environ: Mapping[str, str] | None = None) -> list[Any]:
    from .cli_provider import PROBE_ENVIRONMENT_KEYS
    from .environment import cli_python_executable

    environment = dict(os.environ if environ is None else environ)
    env = {key: value for key, value in environment.items() if key.upper() in PROBE_ENVIRONMENT_KEYS}
    env["A4M_CLI_LAUNCH"] = json.dumps(catalog_arguments(backend))
    env["A4M_CLI_SCHEMA"] = "{}"
    host = Path(__file__).with_name("cli_process_host.py")
    child = subprocess.Popen([cli_python_executable(), "-I", "-B", str(host), "--catalog-query"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=env, cwd=directory, shell=False, bufsize=0,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                             start_new_session=os.name != "nt")
    inbox: queue.Queue[Any] = queue.Queue(maxsize=64)
    stopped = threading.Event()
    deadline = time.monotonic() + QUERY_TIMEOUT

    def put(value: Any) -> None:
        while not stopped.is_set():
            try:
                inbox.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def read_lines() -> None:
        pending = bytearray()
        count = 0
        try:
            while not stopped.is_set():
                chunk = child.stdout.read(8192)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_OUTPUT:
                    raise ValueError("output bound")
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, tail = pending.partition(b"\n")
                    pending = bytearray(tail)
                    if line.strip():
                        put(json.loads(line.decode("utf-8")))
            put(None)
        except Exception:
            put(None)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()

    def send(value: dict[str, Any]) -> None:
        child.stdin.write((json.dumps(value) + "\n").encode("utf-8"))

    def request(identifier: int, method: str, params: dict[str, Any]) -> Any:
        send({"id": identifier, "method": method, "params": params})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            item = inbox.get(timeout=remaining)
            if not isinstance(item, dict):
                raise ValueError("closed protocol")
            if "method" in item:
                if "id" in item:
                    raise ValueError("unexpected server request")
                continue  # Never store/log notifications.
            if item.get("id") != identifier or "error" in item or "result" not in item:
                raise ValueError("invalid response")
            return item["result"]

    try:
        request(1, "initialize", {"clientInfo": {"name": "Agent4Market", "title": "Agent4Market", "version": "0.20.7"},
                                  "capabilities": {"experimentalApi": False}})
        send({"method": "initialized", "params": {}})
        rows: list[Any] = []
        cursor = None
        cursors: set[str] = set()
        for identifier in range(2, 2 + MAX_MODELS):
            page = request(identifier, "model/list", {"limit": 20, "includeHidden": False,
                                                      **({"cursor": cursor} if cursor else {})})
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise ValueError("invalid page")
            rows.extend(page["data"])
            if len(rows) > MAX_MODELS:
                raise ValueError("model bound")
            cursor = page.get("nextCursor")
            if cursor is None:
                return rows
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024 or cursor in cursors:
                raise ValueError("invalid cursor")
            cursors.add(cursor)
        raise ValueError("page bound")
    except Exception as error:
        raise ModelProviderError(_ERROR) from error
    finally:
        stopped.set()
        child.stdin.close()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if child.poll() is None:
            if os.name == "nt":
                child.kill()  # Closing the host kills the whole owned Job.
            else:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        child.wait(timeout=5)
        reader.join(timeout=1)
        child.stdout.close()


def discover_codex_models(project_root: Path | str, executable_path: str, *, environ=None, **probe_options) -> dict[str, Any]:
    from .cli_provider import resolve_configured_backend, backend_files_available

    if not _QUERIES.acquire(blocking=False):
        raise ModelProviderError("正在读取 CLI 模型目录，请稍后重试")
    try:
        backend = resolve_configured_backend(project_root, "codex-cli", executable_path, environ=environ, **probe_options)
        rows = _query_model_list(backend, environ=environ)
        models = [record for value in rows if (record := normalize_model(value)) is not None]
        if not models or len({m["id"] for m in models}) != len(models) or not backend_files_available(backend, verify_hash=True):
            raise ModelProviderError(_ERROR)
        result = {"discovery_id": uuid.uuid4().hex, "models": models, "source": "cli-model-list-isolated",
                  "account_verified": False, "launch_sha256": backend["launch_sha256"], "version": backend["version"]}
        with _LOCK:
            now = time.monotonic()
            for key in list(_CACHE):
                if _CACHE[key]["expires"] < now:
                    del _CACHE[key]
            while len(_CACHE) >= 16:
                del _CACHE[next(iter(_CACHE))]
            _CACHE[result["discovery_id"]] = {"root": str(Path(project_root).resolve()), "expires": now + CACHE_SECONDS,
                                              "result": copy.deepcopy(result)}
        return result
    except ModelProviderError:
        raise
    except Exception as error:
        raise ModelProviderError(_ERROR) from error
    finally:
        _QUERIES.release()


def selected_catalog_model(root: Path | str, backend: Mapping[str, Any], discovery_id: str, model_id: str, level: str) -> dict[str, Any]:
    with _LOCK:
        cached = copy.deepcopy(_CACHE.get(discovery_id)) if isinstance(discovery_id, str) else None
    if (not cached or cached["expires"] < time.monotonic() or cached["root"] != str(Path(root).resolve())
            or backend["api"] != "codex-cli" or cached["result"]["launch_sha256"] != backend["launch_sha256"]
            or cached["result"]["version"] != backend["version"]):
        raise ModelProviderError("模型目录已过期或 CLI 已变化，请重新读取目录后保存")
    record = next((item for item in cached["result"]["models"] if item["id"] == model_id), None)
    if record is None or level not in record["supported_thinking_levels"]:
        raise ModelProviderError("所选模型或思考强度不在当前 CLI 支持目录中")
    return {"display_name": record["display_name"], "cli_reasoning": record["cli_reasoning"],
            "default_thinking_level": level, "reasoning": any(v != "off" for v in record["supported_thinking_levels"]),
            "metadata_source": "cli-model-list"}
