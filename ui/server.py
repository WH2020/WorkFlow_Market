"""Local-only control centre for the vertical director agents.

This is deliberately a small, dependency-free HTTP server.  It is a control
surface, not an agent executor: task requests are picked up by Pi, and the UI
can only submit an approval decision for a task already waiting for approval.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
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
PRESENTATION_PLANS = RUNTIME / "presentation-plans"
PROFILES = ROOT / "profiles"
PLUGINS = ROOT / "vertical_plugins"
OUTPUTS = ROOT / "outputs"
SERVER_TOKEN = secrets.token_urlsafe(32)
ACTIVE_PROFILE_ID: str | None = None


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


def validate_presentation_brief_request(request_text: str) -> dict[str, Any]:
    prefix, suffix = "[PRESENTATION_BRIEF]", "[/PRESENTATION_BRIEF]"
    if not request_text.startswith(prefix) or not request_text.endswith(suffix):
        raise ValueError("PPT 工作室请求必须使用结构化 brief")
    value = json.loads(request_text[len(prefix):-len(suffix)].strip())
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("PPT brief 版本无效")
    limits = {"topic": 240, "audience": 240, "purpose": 500, "occasion": 240, "language": 40}
    for field, maximum in limits.items():
        candidate = value.get(field)
        if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > maximum:
            raise ValueError(f"PPT brief.{field} 无效或超过 {maximum} 字")
    if value.get("scene") not in {"weekly", "industry", "government", "custom"}:
        raise ValueError("PPT brief 场景无效")
    if value.get("mode") not in {"quick", "standard", "strict"}:
        raise ValueError("PPT brief 模式无效")
    if value.get("confidentiality") not in {"internal", "restricted", "public"}:
        raise ValueError("PPT brief 保密等级无效")
    if value.get("source_scope") != "public-web-and-profile-knowledge":
        raise ValueError("PPT 工作室首版只支持公开网页与当前 Profile 知识库")
    if not isinstance(value.get("target_slides"), int) or not 4 <= value["target_slides"] <= 10:
        raise ValueError("PPT brief 页数必须为 4–10")
    if not isinstance(value.get("duration_minutes"), int) or not 3 <= value["duration_minutes"] <= 120:
        raise ValueError("PPT brief 时长必须为 3–120 分钟")
    decision = value.get("expected_decision")
    if not isinstance(decision, str) or not decision.strip() or len(decision) > 500:
        raise ValueError("PPT brief 期望决策无效或超过 500 字")
    design = value.get("design_system")
    if not isinstance(design, dict) or design.get("token_id") not in {"management-report", "government-program", "technology-research"}:
        raise ValueError("PPT brief 设计令牌无效")
    output_name = value.get("output_name")
    if not isinstance(output_name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx", output_name) is None:
        raise ValueError("PPT brief 输出文件名无效")
    return value


def canonical_plan_json(value: Any) -> str:
    """Match the Pi runtime's canonicalEvidence encoding for finite JSON data."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("PPT plan 只能包含有限数字")
        if value == 0:
            return "0"
        if value.is_integer():
            return str(int(value))
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    if isinstance(value, list):
        return f"[{','.join(canonical_plan_json(item) for item in value)}]"
    if isinstance(value, dict):
        pairs = (
            f"{json.dumps(str(key), ensure_ascii=False, separators=(',', ':'))}:{canonical_plan_json(child)}"
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        )
        return f"{{{','.join(pairs)}}}"
    raise ValueError("PPT plan 必须是有限 JSON 数据")


def presentation_plan_sha256(plan: dict[str, Any]) -> str:
    hash_base = {key: value for key, value in plan.items() if key not in {"plan_sha256", "updated_at"}}
    return hashlib.sha256(canonical_plan_json(hash_base).encode("utf-8")).hexdigest()


def profiles() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(PROFILES.glob("*/profile.json")):
        profile = load_json(path)
        if ACTIVE_PROFILE_ID is not None and profile.get("id") != ACTIVE_PROFILE_ID:
            continue
        result.append({
            "id": profile["id"], "display_name": profile["display_name"],
            "description": profile.get("description", ""),
            "default_service": profile.get("default_service"),
            "services": profile.get("services", []),
        })
    return result


def workflows() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    allowed_workflows = None
    if ACTIVE_PROFILE_ID is not None:
        allowed_workflows = {
            service["workflow"]
            for profile in profiles()
            for service in profile.get("services", [])
            if isinstance(service, dict) and isinstance(service.get("workflow"), str)
        }
    for path in sorted(PLUGINS.glob("**/workflows/*.json")):
        workflow = load_json(path)
        if allowed_workflows is not None and workflow.get("id") not in allowed_workflows:
            continue
        result[workflow["id"]] = {
            "id": workflow["id"], "display_name": workflow.get("display_name", workflow["id"]),
            "nodes": [{key: node.get(key) for key in ("id", "type", "depends_on", "tool", "skill", "check", "policy")}
                      for node in workflow.get("nodes", [])],
        }
    return result


def presentation_plan(task_id: str) -> dict[str, Any] | None:
    """Return a task-bound planning view without scanning arbitrary paths."""
    task_id = safe_id(task_id)
    path = PRESENTATION_PLANS / f"{task_id}.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 524_288:
            return None
        plan = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    stored_hash = plan.get("plan_sha256")
    context_hash = plan.get("context_snapshot_sha256")
    if (
        plan.get("schema_version") != "1.0" or plan.get("task_id") != task_id or
        not isinstance(plan.get("version"), int) or plan["version"] < 1 or
        not isinstance(stored_hash, str) or re.fullmatch(r"[a-f0-9]{64}", stored_hash) is None or
        not isinstance(context_hash, str) or re.fullmatch(r"[a-f0-9]{64}", context_hash) is None
    ):
        return None
    try:
        computed_hash = presentation_plan_sha256(plan)
    except (TypeError, ValueError):
        return None
    if not secrets.compare_digest(stored_hash, computed_hash):
        return None
    allowed = {
        "schema_version", "task_id", "project_id", "profile_id", "scene", "mode", "phase", "version",
        "period", "brief", "evidence_refs", "outline", "slides", "design_system", "output_name",
        "context_snapshot_sha256", "plan_sha256", "warnings", "updated_at",
    }
    return {key: value for key, value in plan.items() if key in allowed}


def recover_prepared_presentation_revisions() -> None:
    """Publish only revisions whose old task durably records the matching rejection."""
    if not REQUESTS.is_dir():
        return
    for path in sorted(REQUESTS.glob("request-revision-*.json"))[:500]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
                continue
            record = load_json(path)
            request_id = safe_id(str(record.get("request_id", "")))
            task_id = safe_id(str(record.get("revision_of_task_id", "")))
            if (
                request_id != path.stem or record.get("status") != "prepared" or
                record.get("source") != "local-workbench" or
                record.get("request_kind") != "presentation-plan-revision"
            ):
                continue
            task_path = TASKS / f"{task_id}.json"
            if task_path.is_symlink() or not task_path.is_file():
                continue
            with exclusive_task(task_path):
                task = load_json(task_path)
                requested_by = f"local-workbench-revision:{request_id}"
                approval = task.get("approval_request")
                linked_pending = isinstance(approval, dict) and approval.get("decision") == "reject" and approval.get("requested_by") == requested_by
                linked_audit = task.get("status") == "rejected" and any(
                    isinstance(event, dict) and event.get("action") == "approval_rejected" and
                    isinstance(event.get("note"), str) and event["note"].startswith(f"{requested_by} @ ")
                    for event in task.get("audit", [])
                )
                if not linked_pending and not linked_audit:
                    continue
                record["status"] = "requested"
                record["published_at"] = now()
                atomic_json(path, record)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            # A lock, partial file or unlinked task is retried on the next refresh.
            continue


def task_summaries() -> list[dict[str, Any]]:
    recover_prepared_presentation_revisions()
    result: list[dict[str, Any]] = []
    if not TASKS.exists():
        return result
    candidates = list(TASKS.glob("*.json"))[:500]
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = load_json(path)
            summary = {key: task.get(key) for key in (
                "task_id", "profile_id", "service_id", "workflow_id", "status", "current_stage",
                "current_node", "waiting_node", "completed_nodes", "version", "created_at", "updated_at",
                "approval_request", "pending_write", "artifacts", "request"
            )}
            if ACTIVE_PROFILE_ID is not None and task.get("profile_id") != ACTIVE_PROFILE_ID:
                continue
            if isinstance(task.get("task_id"), str):
                summary["presentation_plan"] = presentation_plan(task["task_id"])
            result.append(summary)
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
            elif route.startswith("/api/tasks/") and route.endswith("/presentation-revision"):
                self.create_presentation_revision(route.split("/")[3], payload)
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
        if service_id in {"presentation-studio", "weekly-deck"}:
            validate_presentation_brief_request(request_text)
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

    def create_presentation_revision(self, task_id: str, payload: dict[str, Any]) -> None:
        """Queue a new audited task instead of mutating an approved/frozen plan in place."""
        task_id = safe_id(task_id)
        expected_version = payload.get("version")
        expected_plan_sha256 = str(payload.get("plan_sha256", ""))
        outline = payload.get("outline")
        if not isinstance(expected_version, int):
            raise ValueError("缺少任务版本")
        if len(expected_plan_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_plan_sha256):
            raise ValueError("缺少有效的计划校验码")
        if not isinstance(outline, list) or not 4 <= len(outline) <= 10:
            raise ValueError("修订大纲必须包含 4–10 页")
        normalized_outline: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(outline):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 页大纲无效")
            slide_id = safe_id(str(item.get("slide_id", "")))
            if slide_id in seen_ids:
                raise ValueError("大纲页面 ID 不能重复")
            seen_ids.add(slide_id)
            title = str(item.get("title", "")).strip()
            if not title or len(title) > 120:
                raise ValueError(f"第 {index + 1} 页标题必须为 1–120 字")
            normalized_outline.append({"slide_id": slide_id, "order": index + 1, "conclusion_title": title})

        task_path = TASKS / f"{task_id}.json"
        if not task_path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
            return
        request_id = f"request-revision-{hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:16]}"
        request_path = REQUESTS / f"{request_id}.json"
        if request_path.is_symlink():
            raise ValueError("修订请求路径不能是符号链接")
        try:
            with exclusive_task(task_path):
                task = load_json(task_path)
                requested_by = f"local-workbench-revision:{request_id}"
                existing_approval = task.get("approval_request")
                if (
                    isinstance(existing_approval, dict) and existing_approval.get("decision") == "reject" and
                    existing_approval.get("requested_by") == requested_by and task.get("version") == expected_version + 1 and
                    request_path.is_file() and not request_path.is_symlink()
                ):
                    prepared = load_json(request_path)
                    if (
                        prepared.get("status") not in {"prepared", "requested"} or
                        prepared.get("source_plan_sha256") != expected_plan_sha256 or
                        prepared.get("revision_of_task_id") != task_id or prepared.get("request_id") != request_id
                    ):
                        self.send_json(HTTPStatus.CONFLICT, {"error": "修订恢复记录与当前任务不一致"})
                        return
                    if prepared.get("status") == "prepared":
                        prepared["status"] = "requested"
                        prepared["published_at"] = now()
                        atomic_json(request_path, prepared)
                    self.send_json(HTTPStatus.CREATED, {
                        "request_id": request_id, "task": task,
                        "message": "修订任务已从中断点恢复，等待 Pi 接手。",
                    })
                    return
                if task.get("version") != expected_version:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务已更新，请刷新后重试", "task": task})
                    return
                if task.get("status") != "waiting_approval" or task.get("approval_request") is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只有尚未处理的大纲确认关口可以创建修订任务"})
                    return
                if task.get("pending_write") is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "正式载荷已经冻结；请驳回后重新创建任务，不能在此修改"})
                    return
                plan = presentation_plan(task_id)
                if not plan or plan.get("phase") not in {"outline", "final"}:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务没有可修订的 PPT 计划"})
                    return
                if plan.get("profile_id") != task.get("profile_id") or plan.get("plan_sha256") != expected_plan_sha256:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "PPT 计划已变化，请刷新后重试"})
                    return
                service_id = str(task.get("service_id", ""))
                profile_id = str(task.get("profile_id", ""))
                workflow_id = str(task.get("workflow_id", ""))
                if service_id != "presentation-studio" or workflow_id != "shared.presentation.studio":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务不是可修订的 PPT 工作室任务"})
                    return
                profile = next((item for item in profiles() if item["id"] == profile_id), None)
                if profile is None or not any(service.get("id") == service_id for service in profile["services"]):
                    self.send_json(HTTPStatus.CONFLICT, {"error": "PPT 工作室服务与当前角色不匹配"})
                    return
                revision = {
                    "schema_version": "1.0", "source_task_id": task_id,
                    "source_plan_sha256": expected_plan_sha256, "outline": normalized_outline,
                }
                request_text = f"[PRESENTATION_PLAN_REVISION]\n{json.dumps(revision, ensure_ascii=False, indent=2)}\n[/PRESENTATION_PLAN_REVISION]"
                if len(request_text) > 4000:
                    raise ValueError("修订请求过大")
                record = {
                    "schema_version": "1.0", "request_id": request_id, "status": "prepared",
                    "profile_id": profile_id, "service_id": service_id, "workflow_id": workflow_id,
                    "request": request_text, "created_at": now(), "source": "local-workbench",
                    "request_kind": "presentation-plan-revision",
                    "revision_of_task_id": task_id, "source_plan_sha256": expected_plan_sha256,
                    "source_task_version": expected_version,
                }
                atomic_json(request_path, record)
                task["approval_request"] = {
                    "decision": "reject", "requested_at": now(),
                    "requested_by": requested_by, "expected_version": expected_version,
                }
                task["updated_at"] = now()
                task["version"] = expected_version + 1
                try:
                    atomic_json(task_path, task)
                except Exception:
                    request_path.unlink(missing_ok=True)
                    raise
                try:
                    record["status"] = "requested"
                    record["published_at"] = now()
                    atomic_json(request_path, record)
                except Exception:
                    self.send_json(HTTPStatus.ACCEPTED, {
                        "request_id": request_id, "task": task,
                        "message": "旧任务已安全结束；新修订请求将在刷新后自动恢复。",
                    })
                    return
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self.send_json(HTTPStatus.CREATED, {
            "request_id": request_id, "task": task,
            "message": "已保留修订大纲并请求结束旧任务；Pi 将在旧任务关闭后接手新版本。",
        })


def main() -> None:
    parser = argparse.ArgumentParser(description="启动仅本机可访问的销售总监工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile", default=os.environ.get("WORKFLOW_AGENT_EDITION_PROFILE", "sales-director"))
    args = parser.parse_args()
    global ACTIVE_PROFILE_ID
    ACTIVE_PROFILE_ID = safe_id(args.profile)
    if not (PROFILES / ACTIVE_PROFILE_ID / "profile.json").is_file():
        parser.error(f"未知发行版角色：{ACTIVE_PROFILE_ID}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler)
    print(f"销售总监工作台已启动：http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n销售总监工作台已停止")


if __name__ == "__main__":
    main()
