"""Local-only control centre for the vertical director agents.

This is deliberately a small, dependency-free HTTP server.  It is a control
surface, not an agent executor: task requests are picked up by Pi, and the UI
can only submit an approval decision for a task already waiting for approval.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".pi" / "director-runtime"
TASKS = RUNTIME / "tasks"
REQUESTS = RUNTIME / "requests"
PROFILES = ROOT / "profiles"
PLUGINS = ROOT / "vertical_plugins"
OUTPUTS = ROOT / "outputs"
SERVER_TOKEN = secrets.token_urlsafe(32)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive_task(path: Path):
    """Serialize UI and Pi read-modify-write transitions for one task."""
    lock = Path(f"{path}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("任务正在由 Pi 或另一个窗口更新，请刷新后重试") from error
    nonce = uuid.uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "nonce": nonce, "created_at": now()}).encode("utf-8")
    os.write(descriptor, payload)
    os.fsync(descriptor)
    os.close(descriptor)
    try:
        yield
    finally:
        try:
            if load_json(lock).get("nonce") == nonce:
                lock.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            # Never delete a lock if ownership can no longer be proven.
            pass


def safe_id(value: str) -> str:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in value):
        raise ValueError("标识符只能包含字母、数字、连字符和下划线")
    return value


def profiles() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(PROFILES.glob("*/profile.json")):
        profile = load_json(path)
        result.append({
            "id": profile["id"], "display_name": profile["display_name"],
            "description": profile.get("description", ""),
            "default_service": profile.get("default_service"),
            "services": profile.get("services", []),
        })
    return result


def workflows() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(PLUGINS.glob("**/workflows/*.json")):
        workflow = load_json(path)
        result[workflow["id"]] = {
            "id": workflow["id"], "display_name": workflow.get("display_name", workflow["id"]),
            "nodes": [{key: node.get(key) for key in ("id", "type", "depends_on", "tool", "skill", "check", "policy")}
                      for node in workflow.get("nodes", [])],
        }
    return result


def task_summaries() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not TASKS.exists():
        return result
    candidates = list(TASKS.glob("*.json"))[:500]
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = load_json(path)
            result.append({key: task.get(key) for key in (
                "task_id", "profile_id", "service_id", "workflow_id", "status", "current_stage",
                "current_node", "waiting_node", "completed_nodes", "version", "created_at", "updated_at",
                "approval_request", "pending_write", "artifacts", "request"
            )})
        except (OSError, ValueError, json.JSONDecodeError):
            # A concurrently written file is simply omitted until the next refresh.
            continue
    return result


def file_summary(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    is_regular = path.is_file() and not path.is_symlink()
    info: dict[str, Any] = {"path": relative, "exists": is_regular, "updated_at": None, "records": None}
    if not is_regular:
        return info
    info["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="minutes")
    try:
        # Parse record boundaries correctly without returning business fields.
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            info["records"] = max(0, sum(1 for _ in csv.reader(handle)) - 1)
    except (OSError, UnicodeError, csv.Error):
        info["records"] = None
    return info


def data_summary() -> dict[str, Any]:
    return {
        "knowledge": [file_summary("data/knowledge/source-register.csv")],
        "sales": [file_summary(f"data/sales/{name}") for name in (
            "customers.csv", "activities.csv", "resource-requests.csv", "sales-assets.csv")],
    }


def output_summary() -> list[dict[str, Any]]:
    if not OUTPUTS.exists():
        return []
    result: list[tuple[float, dict[str, Any]]] = []
    for index, item in enumerate(OUTPUTS.rglob("*")):
        if index >= 2000:
            break
        try:
            if item.is_symlink() or not item.is_file():
                continue
            modified = item.stat().st_mtime
        except OSError:
            continue
        result.append((modified, {
            "name": item.name,
            "modified_at": datetime.fromtimestamp(modified).astimezone().isoformat(timespec="minutes"),
        }))
    return [entry for _, entry in sorted(result, key=lambda pair: pair[0], reverse=True)[:20]]


class ControlHandler(SimpleHTTPRequestHandler):
    server_version = "DirectorWorkbench/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(UI_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def local_host(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def send_json(self, status: HTTPStatus, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("请求体不能为空")
        if length > 16_384:
            raise ValueError("请求过大")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求体必须是对象")
        return value

    def do_GET(self) -> None:
        if not self.local_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        route = urlparse(self.path).path
        if route == "/api/bootstrap":
            self.send_json(HTTPStatus.OK, {"profiles": profiles(), "workflows": workflows(), "tasks": task_summaries(),
                                           "data": data_summary(), "outputs": output_summary(), "request_token": SERVER_TOKEN})
            return
        if route == "/api/tasks":
            self.send_json(HTTPStatus.OK, task_summaries())
            return
        if route in ("/", "/index.html"):
            self.path = "/index.html"
        elif route not in ("/app.js", "/styles.css"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            if not self.local_host():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not secrets.compare_digest(self.headers.get("X-Director-Token", ""), SERVER_TOKEN):
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "工作台令牌无效，请刷新页面"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "只接受 JSON 请求"})
                return
            payload = self.body()
            route = urlparse(self.path).path
            if route == "/api/task-requests":
                self.create_request(payload)
            elif route.startswith("/api/tasks/") and route.endswith("/decision"):
                self.decide(route.split("/")[3], payload)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def create_request(self, payload: dict[str, Any]) -> None:
        profile_id = safe_id(str(payload.get("profile_id", "")))
        service_id = safe_id(str(payload.get("service_id", "")))
        request_text = str(payload.get("request", "")).strip()
        if not request_text or len(request_text) > 4000:
            raise ValueError("请填写 1 到 4000 字的任务说明")
        profile = next((item for item in profiles() if item["id"] == profile_id), None)
        if profile is None:
            raise ValueError("未知角色")
        service = next((item for item in profile["services"] if item["id"] == service_id), None)
        if service is None:
            raise ValueError("该服务不属于当前角色")
        request_id = f"request-{uuid.uuid4().hex[:12]}"
        record = {"schema_version": "1.0", "request_id": request_id, "status": "requested", "profile_id": profile_id,
                  "service_id": service_id, "workflow_id": service["workflow"], "request": request_text,
                  "created_at": now(), "source": "local-workbench"}
        atomic_json(REQUESTS / f"{request_id}.json", record)
        self.send_json(HTTPStatus.CREATED, record)

    def decide(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        decision = str(payload.get("decision", ""))
        expected_version = payload.get("version")
        intent_id = payload.get("intent_id")
        payload_sha256 = payload.get("payload_sha256")
        if decision not in {"approve", "reject", "cancel"}:
            raise ValueError("无效操作")
        if not isinstance(expected_version, int):
            raise ValueError("缺少任务版本")
        path = TASKS / f"{task_id}.json"
        if not path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
            return
        try:
            with exclusive_task(path):
                task = load_json(path)
                if task.get("version") != expected_version:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务已更新，请刷新后重试", "task": task})
                    return
                if task.get("approval_request") is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "已有操作正在等待 Pi 处理，请稍后刷新"})
                    return
                if decision in {"approve", "reject"} and task.get("status") != "waiting_approval":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只有等待审批的任务可以批准或驳回"})
                    return
                if decision == "cancel" and task.get("status") != "waiting_approval":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "工作台只在人工关口取消任务；运行中请在 Pi 使用 /director-cancel"})
                    return
                pending_write = task.get("pending_write")
                if decision == "approve" and isinstance(pending_write, dict) and pending_write.get("status") == "prepared":
                    if intent_id != pending_write.get("intent_id") or payload_sha256 != pending_write.get("payload_sha256"):
                        self.send_json(HTTPStatus.CONFLICT, {"error": "审批内容与当前冻结变更不一致，请刷新后重试"})
                        return
                elif intent_id is not None or payload_sha256 is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务没有可绑定的冻结变更"})
                    return
                # This is a request only. Pi performs the only legal state transition.
                task["approval_request"] = {
                    "decision": decision, "requested_at": now(), "requested_by": "local-workbench",
                    "expected_version": expected_version,
                }
                if decision == "approve" and isinstance(pending_write, dict) and pending_write.get("status") == "prepared":
                    task["approval_request"]["intent_id"] = pending_write["intent_id"]
                    task["approval_request"]["payload_sha256"] = pending_write["payload_sha256"]
                task["updated_at"] = now()
                task["version"] = expected_version + 1
                atomic_json(path, task)
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self.send_json(HTTPStatus.ACCEPTED, {"task": task, "message": "操作已提交，等待 Pi 工作流确认。"})


def main() -> None:
    parser = argparse.ArgumentParser(description="启动仅本机可访问的总监工作台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler)
    print(f"总监工作台已启动：http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n总监工作台已停止")


if __name__ == "__main__":
    main()
