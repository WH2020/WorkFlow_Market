from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .core import Platform


DEFAULT_WORKBENCH_URL = "http://127.0.0.1:8765"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
SKILL_NAME = "agent4market-sales-director"
SKILL_CANONICAL = Path("integrations") / "coding-agents" / SKILL_NAME
SKILL_TARGETS = (
    Path(".agents") / "skills" / SKILL_NAME,
    Path(".claude") / "skills" / SKILL_NAME,
)


class CodingAgentBridgeError(RuntimeError):
    """A safe, user-facing failure from the localhost coding-agent bridge."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _validate_loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise CodingAgentBridgeError(
            "UNSAFE_WORKBENCH_URL",
            "编码助手桥接只允许连接本机 HTTP 工作台地址",
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise CodingAgentBridgeError("UNSAFE_WORKBENCH_URL", "本机工作台端口无效") from error
    if port is None or not 1 <= port <= 65535:
        raise CodingAgentBridgeError("UNSAFE_WORKBENCH_URL", "本机工作台地址必须包含有效端口")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{port}"


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台返回了无效数据") from error


def _task_view(task: Mapping[str, Any]) -> dict[str, Any]:
    pending = task.get("pending_write")
    approval = task.get("approval_request")
    status = str(task.get("display_status") or task.get("status") or "unknown")
    artifacts = [
        item for item in task.get("artifacts", [])
        if isinstance(item, str) and item.startswith("outputs/")
    ] if isinstance(task.get("artifacts"), list) else []
    view = {
        key: task.get(key)
        for key in (
            "task_id", "project_id", "service_id", "workflow_id", "status", "display_status",
            "runtime_state", "version", "request", "current_node_display_name",
            "waiting_node_display_name", "updated_at", "created_at", "requested_model",
            "requested_thinking_level", "effective_model", "effective_thinking_level",
        )
        if task.get(key) is not None
    }
    view["requires_approval"] = status in {"waiting_approval", "approval_pending", "approval_stalled"}
    view["has_prepared_write"] = isinstance(pending, dict) and pending.get("status") == "prepared"
    view["has_pending_user_action"] = isinstance(approval, dict)
    view["artifacts"] = artifacts
    if view["requires_approval"]:
        view["next_action"] = "请在桌面工作台查看自然语言预览并人工审批；编码助手不能代替你批准写入。"
    elif status in {"completed", "cancelled", "rejected", "failed"}:
        view["next_action"] = "任务已经结束；可查看产物，或明确要求重新创建任务。"
    else:
        view["next_action"] = "智能核心正在处理；可稍后查询，或发送补充信息/调整方向。"
    return view


class WorkbenchClient:
    """Small localhost-only client for the existing governed workbench API."""

    def __init__(
        self,
        base_url: str = DEFAULT_WORKBENCH_URL,
        *,
        timeout: float = 8.0,
        opener: Callable[..., Any] | None = None,
    ):
        self.base_url = _validate_loopback_url(base_url)
        self.timeout = timeout
        self._open = opener or build_opener(ProxyHandler({}), _NoRedirect()).open

    def _request(
        self,
        route: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        if not route.startswith("/") or ".." in route:
            raise CodingAgentBridgeError("INVALID_ROUTE", "本机工作台请求地址无效")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Director-Token"] = token
        request = Request(f"{self.base_url}{route}", data=body, headers=headers, method=method)
        try:
            with self._open(request, timeout=self.timeout) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                    raise CodingAgentBridgeError("RESPONSE_TOO_LARGE", "本机工作台返回内容超过安全上限")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise CodingAgentBridgeError("RESPONSE_TOO_LARGE", "本机工作台返回内容超过安全上限")
                return _decode_json(raw)
        except HTTPError as error:
            raw = error.read(MAX_RESPONSE_BYTES + 1)
            detail = _decode_json(raw) if raw else {}
            message = detail.get("error") if isinstance(detail, dict) else None
            raise CodingAgentBridgeError(
                f"WORKBENCH_HTTP_{error.code}",
                str(message or f"本机工作台请求失败（HTTP {error.code}）"),
            ) from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise CodingAgentBridgeError(
                "WORKBENCH_OFFLINE",
                "销售总监工作台未运行；请先打开 Agent4Market，再重试当前操作",
            ) from error
        except ValueError as error:
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台返回头信息无效") from error

    def health(self) -> dict[str, Any]:
        value = self._request("/api/health")
        if not isinstance(value, dict) or value.get("status") != "ok":
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台健康状态无效")
        return value

    def bootstrap(self) -> dict[str, Any]:
        value = self._request("/api/bootstrap")
        if not isinstance(value, dict) or not isinstance(value.get("request_token"), str):
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台没有返回可用的本地会话令牌")
        return value

    def projects(self) -> list[dict[str, Any]]:
        value = self.bootstrap().get("projects")
        if not isinstance(value, list):
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台项目空间列表无效")
        return [
            {
                key: item.get(key)
                for key in ("project_id", "name", "status", "description", "task_count", "active_task_count")
                if item.get(key) is not None
            }
            for item in value
            if isinstance(item, dict)
        ]

    def tasks(self, *, include_history: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务数量必须为 1–100")
        value = self._request("/api/tasks")
        if not isinstance(value, list):
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台任务列表无效")
        result: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if not include_history and item.get("runtime_state") == "historical":
                continue
            result.append(_task_view(item))
            if len(result) >= limit:
                break
        return result

    def task(self, task_id: str) -> dict[str, Any]:
        if TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务编号无效")
        value = self._request("/api/tasks")
        if not isinstance(value, list):
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台任务列表无效")
        task = next((item for item in value if isinstance(item, dict) and item.get("task_id") == task_id), None)
        if task is None:
            return {
                "task_id": task_id,
                "status": "requested",
                "display_status": "等待智能核心接手",
                "requires_approval": False,
                "next_action": "任务可能刚进入队列；请稍后再次查询。",
            }
        return _task_view(task)

    def submit_task(
        self,
        *,
        service_id: str,
        request_text: str,
        project_id: str = "project-default",
        requested_model: str | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        if TASK_ID_PATTERN.fullmatch(service_id) is None or TASK_ID_PATTERN.fullmatch(project_id) is None:
            raise CodingAgentBridgeError("INVALID_INPUT", "服务或项目空间编号无效")
        request_text = request_text.strip()
        if not request_text or len(request_text) > 4000:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务说明必须为 1–4000 字")
        if thinking_level is not None and thinking_level not in THINKING_LEVELS:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务思考强度无效")
        bootstrap = self.bootstrap()
        profile = next(
            (item for item in bootstrap.get("profiles", []) if isinstance(item, dict) and item.get("id") == "sales-director"),
            None,
        )
        if profile is None:
            raise CodingAgentBridgeError("PROFILE_UNAVAILABLE", "当前运行版本没有销售总监服务")
        service_ids = {
            item.get("id") for item in profile.get("services", []) if isinstance(item, dict)
        }
        if service_id not in service_ids:
            raise CodingAgentBridgeError("SERVICE_UNAVAILABLE", "所选服务不属于当前销售总监版本")
        payload: dict[str, Any] = {
            "profile_id": "sales-director",
            "service_id": service_id,
            "project_id": project_id,
            "request": request_text,
        }
        if requested_model:
            payload["requested_model"] = requested_model
        if thinking_level:
            payload["requested_thinking_level"] = thinking_level
        value = self._request(
            "/api/task-requests",
            method="POST",
            payload=payload,
            token=bootstrap["request_token"],
        )
        if not isinstance(value, dict) or TASK_ID_PATTERN.fullmatch(str(value.get("request_id", ""))) is None:
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台没有返回有效任务编号")
        return {
            "task_id": value["request_id"],
            "request_id": value["request_id"],
            "project_id": value.get("project_id"),
            "service_id": value.get("service_id"),
            "workflow_id": value.get("workflow_id"),
            "status": value.get("status", "requested"),
            "message": "任务已交给 Agent4Market 智能核心；写入或正式文件生成仍会在桌面工作台等待人工审批。",
        }

    def send_message(self, *, task_id: str, mode: str, content: str) -> dict[str, Any]:
        if mode not in {"supplement", "redirect"}:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务消息只能选择补充信息或调整方向")
        content = content.strip()
        if not content or len(content) > 1200:
            raise CodingAgentBridgeError("INVALID_INPUT", "任务消息必须为 1–1200 字")
        current = self.task(task_id)
        version = current.get("version")
        if not isinstance(version, int):
            raise CodingAgentBridgeError("TASK_NOT_READY", "任务尚未被智能核心接手，请稍后再发送消息")
        bootstrap = self.bootstrap()
        value = self._request(
            f"/api/tasks/{quote(task_id, safe='')}/messages",
            method="POST",
            payload={"operation": mode, "mode": mode, "content": content, "version": version},
            token=bootstrap["request_token"],
        )
        if not isinstance(value, dict):
            raise CodingAgentBridgeError("INVALID_RESPONSE", "本机工作台没有返回有效消息状态")
        return {
            "task_id": task_id,
            "mode": mode,
            "status": value.get("status", "queued"),
            "message": value.get("message", "任务消息已排队。"),
        }

    def open_workbench(self) -> dict[str, Any]:
        self.health()
        opened = webbrowser.open(self.base_url, new=2)
        if not opened:
            raise CodingAgentBridgeError("BROWSER_OPEN_FAILED", "无法打开系统浏览器，请手动打开本机工作台地址")
        return {"status": "ok", "url": self.base_url, "message": "已打开销售总监工作台。"}


def sales_services(project_root: Path | str) -> list[dict[str, Any]]:
    platform = Platform(project_root)
    platform.validate_all()
    return [
        {
            key: service.get(key)
            for key in ("id", "display_name", "description", "workflow", "skill")
        }
        for service in platform.list_services("sales-director")
    ]


def _tree_fingerprint(path: Path) -> tuple[str, list[str]]:
    if path.is_symlink() or not path.is_dir():
        return "", []
    digest = hashlib.sha256()
    names: list[str] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink() or not item.is_file():
            if item.is_symlink():
                return "", []
            continue
        relative = item.relative_to(path).as_posix()
        names.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), names


def coding_agent_skill_status(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    canonical = root / SKILL_CANONICAL
    canonical_hash, canonical_files = _tree_fingerprint(canonical)
    targets: list[dict[str, Any]] = []
    for relative in SKILL_TARGETS:
        target = root / relative
        target_hash, target_files = _tree_fingerprint(target)
        targets.append({
            "path": relative.as_posix(),
            "ok": bool(canonical_hash) and target_hash == canonical_hash and target_files == canonical_files,
            "sha256": target_hash or None,
        })
    return {
        "name": SKILL_NAME,
        "canonical": SKILL_CANONICAL.as_posix(),
        "canonical_sha256": canonical_hash or None,
        "files": canonical_files,
        "targets": targets,
        "ready": bool(canonical_hash) and all(item["ok"] for item in targets),
    }


def _command_candidates(name: str, environ: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    detected = shutil.which(name, path=environ.get("PATH"))
    if detected:
        candidates.append(Path(detected))
    user_profile = Path(environ.get("USERPROFILE") or environ.get("HOME") or str(Path.home()))
    app_data = environ.get("APPDATA")
    if os.name == "nt":
        if app_data:
            candidates.extend([Path(app_data) / "npm" / f"{name}.cmd", Path(app_data) / "npm" / f"{name}.exe"])
        candidates.append(user_profile / ".local" / "bin" / f"{name}.exe")
    else:
        candidates.extend([user_profile / ".local" / "bin" / name, Path("/usr/local/bin") / name, Path("/opt/homebrew/bin") / name])
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique


def _probe_command(name: str, environ: Mapping[str, str]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for candidate in _command_candidates(name, environ):
        if not candidate.is_file():
            continue
        try:
            completed = subprocess.run(
                [str(candidate), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                env=dict(environ),
            )
        except (OSError, subprocess.SubprocessError) as error:
            failures.append({"path": str(candidate), "reason": type(error).__name__})
            continue
        output = (completed.stdout or completed.stderr).strip()[:200]
        if completed.returncode == 0:
            return {"name": name, "ready": True, "path": str(candidate.resolve()), "version": output}
        failures.append({"path": str(candidate), "reason": f"exit_{completed.returncode}"})
    return {"name": name, "ready": False, "reason": "not_found_or_not_runnable", "attempts": failures[:8]}


def coding_agent_doctor(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    client: WorkbenchClient | None = None,
) -> dict[str, Any]:
    environment = dict(os.environ if environ is None else environ)
    skill = coding_agent_skill_status(project_root)
    hosts = {name: _probe_command(name, environment) for name in ("codex", "claude")}
    workbench_client = client or WorkbenchClient()
    try:
        health = workbench_client.health()
        workbench = {"ready": health.get("profile_id") in {None, "sales-director"}, **health}
    except CodingAgentBridgeError as error:
        workbench = {"ready": False, "code": error.code, "message": str(error)}
    overall = "ok" if skill["ready"] and workbench["ready"] and all(item["ready"] for item in hosts.values()) else "warning"
    return {
        "status": overall,
        "integration_ready": skill["ready"],
        "workbench": workbench,
        "hosts": hosts,
        "skill": skill,
        "approval_boundary": "Codex CLI 与 Claude Code 可创建、查看和调整任务，但人工审批仅在桌面工作台完成。",
    }
