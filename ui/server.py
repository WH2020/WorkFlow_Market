"""Local-only control centre for the vertical director agents.

This is deliberately a small, dependency-free HTTP server.  It is a control
surface, not an agent executor: task and lifecycle requests are picked up by
Pi, while the UI only records version-bound user decisions.
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
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform.model_provider import (  # noqa: E402
    ModelProviderError,
    clear_model_provider,
    configure_model_provider,
    discover_models,
    load_model_secret,
    model_settings_summary,
    normalize_base_url,
)
from agent_platform.search_provider import (  # noqa: E402
    SearchProviderError,
    clear_search_provider,
    configure_search_provider,
    search_settings_summary,
)
from agent_platform.search_gateway import (  # noqa: E402
    SearchGatewayError,
    clear_search_gateway,
    configure_search_gateway,
    search_gateway_settings_summary,
)
from agent_platform.mail_provider import (  # noqa: E402
    MailProviderError,
    clear_mail_provider,
    configure_mail_provider,
    import_reimbursement_mail,
    mail_settings_summary,
    search_reimbursement_mail,
)
from agent_platform.business_backend import (  # noqa: E402
    BusinessBackendError,
    data_summary as business_data_summary,
    knowledge_entries as business_knowledge_entries,
    knowledge_urls as business_knowledge_urls,
    read_account_360,
    read_account_timeline,
    read_signals,
    read_today_focus,
    resolve_business_backend,
    search_accounts,
    search_business_records,
)
from agent_platform.bid_store import (  # noqa: E402
    ALLOWED_BID_SUFFIXES,
    MAX_BID_FILE_BYTES,
    BidStoreError,
    bid_dashboard,
    create_bid_project,
    read_bid_project,
    read_bid_timeline,
    register_bid_document,
    run_deterministic_bid_checks,
    search_bid_projects,
    transition_bid_project,
    update_bid_project,
)


UI_ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".pi" / "director-runtime"
TASKS = RUNTIME / "tasks"
REQUESTS = RUNTIME / "requests"
PRESENTATION_PLANS = RUNTIME / "presentation-plans"
PROFILES = ROOT / "profiles"
PLUGINS = ROOT / "vertical_plugins"
OUTPUTS = ROOT / "outputs"
INPUTS = ROOT / "inputs"
DATA = ROOT / "data"
PROJECTS = RUNTIME / "projects.json"
SCHEDULES = RUNTIME / "schedules.json"
AGENT_LEASES = RUNTIME / "agent-leases"
TASK_EVENTS = RUNTIME / "task-events"
TASK_MESSAGES = RUNTIME / "task-messages"
DESKTOP_SETTINGS = RUNTIME / "desktop-settings.json"
AI_CORE_LOG = RUNTIME / "ai-core.log"
REIMBURSEMENT_BATCHES = RUNTIME / "reimbursement-batches"
FILE_TRASH = RUNTIME / "file-trash"
SERVER_TOKEN = secrets.token_urlsafe(32)
ACTIVE_PROFILE_ID: str | None = None

DEFAULT_PROJECT_ID = "project-default"
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {
    ".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".pptx",
    ".png", ".jpg", ".jpeg", ".heic", ".ofd", ".xls", ".zip",
}
AGENT_LEASE_FRESH_SECONDS = 15
APPROVAL_REQUEST_STALE_SECONDS = 15
TASK_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
PUBLIC_SEARCH_SERVICES = {"industry-research", "government-proposal", "presentation-studio"}
BRAVE_DASHBOARD_URL = "https://api-dashboard.search.brave.com/app/keys"
NODE_DISPLAY_NAMES = {
    "scope": "明确研究范围", "clarify": "梳理合作目标", "load_accounts": "读取客户记录",
    "analyze": "分析客户进展", "confirm": "确认销售更新", "update": "更新销售台账",
    "validate_updates": "校验销售变更", "policy_search": "检索政策来源",
    "public_research": "检索公开资料", "search_public_sources": "检索公开资料",
    "research": "检索公开资料", "open_sources": "核验来源正文",
    "open_public_sources": "核验来源正文", "open_policy_sources": "核验政策正文",
    "internal_evidence": "读取知识库", "search_knowledge": "读取知识库",
    "join_evidence": "汇总内外部证据", "synthesize": "形成综合判断",
    "draft": "撰写方案", "independent_review": "执行独立复核", "validate": "校验内容",
    "approval": "确认合作方案", "frame_problem": "明确机会问题",
    "opportunity_brief": "形成机会简报", "evidence_gate": "校验证据质量",
    "approve_knowledge": "确认知识入库", "update_knowledge": "更新知识库",
    "persist_knowledge": "写入知识库", "load_goal": "读取指标目标",
    "design": "设计指标方案", "approve_measurement": "确认指标方案",
    "collect_release": "收集发布资料", "review": "复核发布准备",
    "gate": "检查发布门槛", "decision": "确认发布决策", "record_decision": "记录发布决策",
    "grill": "核对需求边界", "draft_prd": "起草产品需求", "validate_prd": "校验产品需求",
    "approve_scope": "确认需求范围", "load_inputs": "读取规划输入", "prioritize": "评估优先级",
    "approve_roadmap": "确认路线规划", "collect": "收集文件依据", "quality_gate": "检查文件质量",
    "create_brief": "明确演示需求", "propose_outline": "编排演示大纲", "save_outline": "保存演示大纲",
    "confirm_outline": "确认演示大纲", "build_storyboard": "制作逐页故事板",
    "select_design_system": "选择视觉体系", "save_final_plan": "保存完整演示方案",
    "validate_and_freeze": "校验并冻结正式内容", "approve_render": "确认生成正式文件",
    "render_deck": "生成正式演示文稿", "fanout": "并行收集证据", "extract_knowledge": "提取知识条目",
    "read_pdf": "读取电子文档", "validate_evidence": "校验证据记录", "collect_week": "汇总本周记录",
    "build_plan": "规划周报结构", "save_plan": "保存周报方案", "build_payload": "组织汇报内容",
    "validate_payload": "校验汇报内容", "approve": "确认正式生成",
    "load_bid": "读取投标项目", "interpret_tender": "解读招标文件",
    "validate_interpretation": "校验原文定位", "approve_bid_data": "确认写入投标项目",
    "write_bid_data": "更新投标项目", "assess_bid": "评估是否参投",
    "build_response_plan": "制定应答计划", "build_fact_baseline": "建立事实基线",
    "draft_bid_sections": "起草标书章节", "run_compliance_checks": "执行合规检查",
    "validate_bid_document": "校验正式标书", "approve_bid_document": "确认生成正式标书",
    "render_bid_document": "生成正式标书", "record_bid_outcome": "记录投标结果与复盘",
}
NODE_TYPE_DISPLAY_NAMES = {
    "agent": "智能分析", "tool": "资料处理", "validator": "规则校验", "approval": "人工确认",
    "subagent": "独立复核", "parallel": "并行处理", "join": "结果汇总",
}
TASK_STATUS_DISPLAY_NAMES = {
    "requested": "等待智能核心接手", "running": "正在处理", "waiting_approval": "等待你的审批",
    "interrupted": "已中断", "cancelling": "正在取消", "resuming": "正在恢复",
    "restarting": "正在重新开始", "superseded": "已替代", "completed": "已完成",
    "cancelled": "已取消", "rejected": "已驳回", "failed": "处理失败",
}
PROJECT_STATUS_DISPLAY_NAMES = {"active": "使用中", "archived": "已归档"}
KNOWLEDGE_WRITE_FIELDS = {
    "source_id", "title", "url", "publisher", "published_date", "accessed_date", "region",
    "topic", "source_type", "quality", "exposure_status", "key_facts", "important_quotes",
    "interpretation", "limitations", "status", "notes",
}
SALES_WRITE_FIELDS = {
    "customers": {
        "customer_id", "customer_name", "region", "sector", "owner", "stage", "health",
        "key_contact", "decision_maker", "budget_path", "next_action", "next_action_due",
        "last_evidence_date", "risks", "updated_at",
    },
    "activities": {
        "activity_id", "customer_id", "salesperson_id", "occurred_at", "channel", "activity_type",
        "summary", "evidence_path", "commitment", "next_action", "next_action_due", "created_at",
    },
    "resource_requests": {
        "request_id", "customer_id", "salesperson_id", "requested_at", "resource_type",
        "request_summary", "business_reason", "deadline", "owner", "status", "decision",
        "decision_reason", "updated_at",
    },
    "sales_assets": {
        "asset_id", "asset_type", "title", "scope", "customer_id", "audience_role", "sales_stage",
        "use_case", "owner", "status", "authorization_status", "deidentification_status", "version",
        "source_path", "evidence_refs", "last_validated_at", "next_review_at", "usage_feedback", "updated_at",
    },
}
WRITE_STABLE_KEYS = {
    "knowledge.write": "source_id", "customers": "customer_id", "activities": "activity_id",
    "resource_requests": "request_id", "sales_assets": "asset_id",
    "bid_projects": "bid_id", "bid_milestones": "milestone_id",
    "bid_requirements": "requirement_id", "bid_response_matrix": "response_id",
    "bid_facts": "fact_id", "bid_sections": "section_id", "bid_checks": "check_id",
    "bid_risks": "risk_id", "bid_decisions": "decision_id", "bid_outcomes": "outcome_id",
}
BID_WRITE_FIELDS = {
    "bid_projects": {
        "account_id", "opportunity_id", "workspace_project_id", "name", "buyer", "tender_number",
        "lot_name", "owner", "deadline_at", "budget_minor", "currency", "status", "current_stage",
        "go_no_go", "decision_reason", "summary",
    },
    "bid_milestones": {"bid_id", "milestone_type", "title", "due_at", "owner", "status", "evidence_json"},
    "bid_requirements": {
        "bid_id", "category", "mandatory", "score_points", "title", "requirement_text",
        "evidence_locator_json", "verification_status", "response_status", "owner", "due_at",
    },
    "bid_response_matrix": {
        "bid_id", "requirement_id", "section_id", "response_strategy", "material_need",
        "material_status", "owner", "due_at", "deviation", "status",
    },
    "bid_facts": {
        "bid_id", "category", "field_name", "value_text", "evidence_json", "verification_status",
        "affected_sections_json",
    },
    "bid_sections": {
        "bid_id", "parent_section_id", "order_index", "level", "title", "objective", "owner",
        "content_markdown", "evidence_json", "status", "input_sha256",
    },
    "bid_checks": {
        "bid_id", "rule_id", "rule_version", "category", "severity", "status", "finding",
        "recommendation", "requirement_id", "section_id", "evidence_json", "input_sha256",
        "resolved_by", "resolved_at",
    },
    "bid_risks": {
        "bid_id", "category", "risk_text", "impact", "likelihood", "status", "owner",
        "mitigation_action", "evidence_json",
    },
    "bid_decisions": {
        "bid_id", "decision_type", "decision", "rationale", "approved_by", "approval_task_id",
        "payload_sha256", "decided_at",
    },
    "bid_outcomes": {
        "bid_id", "result", "amount_minor", "currency", "reason", "competitor_notes", "lessons",
        "evidence_json", "decided_at",
    },
}


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
        raise RuntimeError("任务正在由智能核心或另一个窗口更新，请刷新后重试") from error
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


def _stored_runtime_selection(value: dict[str, Any]) -> dict[str, str]:
    selection: dict[str, str] = {}
    requested_model = value.get("requested_model")
    requested_thinking = value.get("requested_thinking_level")
    if requested_model is not None:
        if (
            not isinstance(requested_model, str) or not requested_model or len(requested_model) > 300 or
            any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in requested_model) or
            not requested_model.startswith("agent4market-newapi/") or requested_model == "agent4market-newapi/"
        ):
            raise ValueError("任务模型标识无效")
        selection["requested_model"] = requested_model
    if requested_thinking is not None:
        if not isinstance(requested_thinking, str) or requested_thinking not in TASK_THINKING_LEVELS:
            raise ValueError("任务思考强度无效")
        selection["requested_thinking_level"] = requested_thinking
    return selection


def task_runtime_selection(payload: dict[str, Any]) -> dict[str, str]:
    selection = _stored_runtime_selection(payload)
    requested_model = selection.get("requested_model")
    if requested_model:
        settings = model_settings_summary(ROOT)
        if settings.get("status") != "configured" or not settings.get("has_api_key"):
            raise ValueError("当前模型网关尚未配置完成，不能为任务指定模型")
        provider_id = str(settings.get("provider_id", ""))
        allowed = {
            f"{provider_id}/{item['id']}"
            for item in settings.get("models", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if requested_model not in allowed:
            raise ValueError("所选任务模型不在当前已配置的模型列表中")
    return selection


def fresh_agent_leases(reference: datetime | None = None) -> list[dict[str, Any]]:
    """Return fresh Pi leases, including an idle embedded core."""
    if AGENT_LEASES.is_symlink() or not AGENT_LEASES.is_dir():
        return []
    current = (reference or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result: list[dict[str, Any]] = []
    for path in sorted(AGENT_LEASES.glob("*.json"))[:64]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
                continue
            lease = load_json(path)
            pid = lease.get("pid")
            nonce = lease.get("nonce")
            task_id = lease.get("task_id")
            profile_id = lease.get("profile_id")
            session_key = lease.get("session_key")
            heartbeat_at = lease.get("heartbeat_at")
            if (
                lease.get("schema_version") != "1.0" or not isinstance(pid, int) or pid <= 0 or
                path.name != f"{pid}.json" or not isinstance(nonce, str) or
                re.fullmatch(r"[a-f0-9-]{36}", nonce) is None or
                (task_id is not None and (not isinstance(task_id, str) or safe_id(task_id) != task_id)) or
                not isinstance(profile_id, str) or safe_id(profile_id) != profile_id or
                not isinstance(session_key, str) or not session_key or len(session_key) > 4096 or
                not isinstance(heartbeat_at, str)
            ):
                continue
            heartbeat = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
            if heartbeat.tzinfo is None:
                continue
            age = (current - heartbeat.astimezone(timezone.utc)).total_seconds()
            if age < -5 or age > AGENT_LEASE_FRESH_SECONDS:
                continue
            result.append(lease)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return result


def live_agent_task_leases(reference: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Return fresh task-bound Pi leases without trusting stale process state."""
    return {
        lease["task_id"]: lease
        for lease in fresh_agent_leases(reference)
        if isinstance(lease.get("task_id"), str)
    }


def desktop_settings() -> dict[str, Any]:
    value = {"schema_version": "1.0", "show_ai_core_window": False}
    try:
        if DESKTOP_SETTINGS.is_symlink() or not DESKTOP_SETTINGS.is_file() or DESKTOP_SETTINGS.stat().st_size > 4096:
            return value
        stored = load_json(DESKTOP_SETTINGS)
        if stored.get("schema_version") == "1.0" and isinstance(stored.get("show_ai_core_window"), bool):
            value["show_ai_core_window"] = stored["show_ai_core_window"]
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return value


def save_desktop_settings(payload: dict[str, Any]) -> dict[str, Any]:
    show_window = payload.get("show_ai_core_window")
    if not isinstance(show_window, bool):
        raise ValueError("智能核心窗口设置必须为开启或关闭")
    value = {"schema_version": "1.0", "show_ai_core_window": show_window}
    atomic_json(DESKTOP_SETTINGS, value)
    return {**value, "restart_required": True}


def ai_core_log_tail() -> list[str]:
    if AI_CORE_LOG.is_symlink() or not AI_CORE_LOG.is_file():
        return []
    try:
        with AI_CORE_LOG.open("rb") as handle:
            size = AI_CORE_LOG.stat().st_size
            handle.seek(max(0, size - 64 * 1024))
            text = handle.read(64 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()[-60:]
    redacted = []
    for line in lines:
        clean = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\",}]+", r"\1[已隐藏]", line)
        clean = re.sub(r"(?i)(api[_ -]?key)([\"':= ]+)[^\s\",}]+", r"\1\2[已隐藏]", clean)
        redacted.append(clean[:1000])
    return redacted


def desktop_runtime_summary() -> dict[str, Any]:
    leases = fresh_agent_leases()
    current = max(leases, key=lambda lease: str(lease.get("heartbeat_at") or ""), default=None)
    if current is None:
        status, label = "offline", "智能核心未连接"
    elif current.get("task_id"):
        status, label = "working", "智能核心正在处理任务"
    else:
        status, label = "idle", "智能核心已就绪"
    settings = desktop_settings()
    return {
        "status": status, "label": label,
        "heartbeat_at": current.get("heartbeat_at") if current else None,
        "task_id": current.get("task_id") if current else None,
        "show_ai_core_window": settings["show_ai_core_window"],
        "window_mode": "visible" if settings["show_ai_core_window"] else "embedded",
        "log_tail": ai_core_log_tail(),
    }


def open_data_directory() -> Path:
    if DATA.is_symlink():
        raise ValueError("本地数据目录不能是符号链接")
    DATA.mkdir(parents=True, exist_ok=True)
    root = ROOT.resolve()
    directory = DATA.resolve()
    if not directory.is_relative_to(root) or directory == root:
        raise ValueError("本地数据目录越出应用安装范围")
    try:
        if sys.platform == "win32":
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("Windows 文件资源管理器不可用")
            startfile(str(directory))
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
            )
        else:
            subprocess.Popen(
                ["xdg-open", str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
            )
    except OSError as error:
        raise RuntimeError(f"无法打开本地数据目录：{error}") from error
    return directory


def open_knowledge_file() -> Path:
    backend = resolve_business_backend(ROOT)
    if backend.backend == "sqlite":
        # Do not open the SQLite file in an arbitrary desktop application or
        # silently expose the stale pre-cutover CSV as the active knowledge base.
        return open_data_directory()
    target = ROOT / "data" / "knowledge" / "source-register.csv"
    if target.is_symlink() or not target.is_file():
        raise ValueError("知识库文件尚未创建")
    root = ROOT.resolve()
    file_path = target.resolve()
    if not file_path.is_relative_to(root) or file_path == root:
        raise ValueError("知识库文件越出应用安装范围")
    try:
        if sys.platform == "win32":
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("Windows 文件打开功能不可用")
            startfile(str(file_path))
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
            )
        else:
            subprocess.Popen(
                ["xdg-open", str(file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True,
            )
    except OSError as error:
        raise OSError(f"无法打开知识库文件：{error}") from error
    return file_path


def task_display_state(
    task: dict[str, Any], leases: dict[str, dict[str, Any]] | None = None,
    reference: datetime | None = None,
) -> tuple[str, str]:
    status = str(task.get("status") or "")
    if status in {"completed", "rejected", "cancelled", "failed"}:
        if status == "cancelled" and isinstance(task.get("superseded_by_task_id"), str):
            return "superseded", "historical"
        return status, "historical"
    if status == "requested":
        return "requested", "queued"
    approval = task.get("approval_request")
    if status == "waiting_approval" and isinstance(approval, dict):
        try:
            requested_at = datetime.fromisoformat(str(approval.get("requested_at") or "").replace("Z", "+00:00"))
            if requested_at.tzinfo is None:
                raise ValueError("approval timestamp has no timezone")
            current = (reference or datetime.now(timezone.utc)).astimezone(timezone.utc)
            age = (current - requested_at.astimezone(timezone.utc)).total_seconds()
            if -5 <= age <= APPROVAL_REQUEST_STALE_SECONDS:
                return "approval_pending", "approval_pending"
        except (TypeError, ValueError):
            pass
        return "approval_stalled", "approval_stalled"
    if status != "running":
        return status, status
    if isinstance(approval, dict):
        if approval.get("decision") == "resume":
            return "resuming", "interrupted"
        if approval.get("decision") == "cancel":
            requested_by = str(approval.get("requested_by") or "")
            return ("restarting" if requested_by.startswith("local-workbench-restart:") else "cancelling"), "interrupted"
    lease = (leases if leases is not None else live_agent_task_leases()).get(str(task.get("task_id") or ""))
    if (
        lease and lease.get("profile_id") == task.get("profile_id") and
        lease.get("session_key") == task.get("session_key") and lease.get("task_status") == "running"
    ):
        return "running", "active"
    return "interrupted", "interrupted"


def task_message_records() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    if TASK_MESSAGES.is_symlink() or not TASK_MESSAGES.is_dir():
        return result
    for path in sorted(TASK_MESSAGES.glob("message-*.json"))[:2000]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
                continue
            message = load_json(path)
            message_id = message.get("message_id")
            task_id = message.get("task_id")
            profile_id = message.get("profile_id")
            content = message.get("content")
            if (
                message.get("schema_version") != "1.0" or message_id != path.stem or
                not isinstance(message_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message_id) is None or
                not isinstance(task_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) is None or
                not isinstance(profile_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", profile_id) is None or
                message.get("mode") not in {"supplement", "redirect"} or
                message.get("status") not in {"queued", "dispatching", "delivered"} or
                not isinstance(content, str) or not content.strip() or len(content) > 1200 or
                not isinstance(message.get("created_at"), str)
            ):
                continue
            result.setdefault(task_id, []).append(message)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    for messages in result.values():
        messages.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("message_id") or "")))
    return result


def task_progress_records() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    if TASK_EVENTS.is_symlink() or not TASK_EVENTS.is_dir():
        return result
    allowed_phases = {"understanding", "collecting", "analyzing", "drafting", "validating", "waiting", "delivering"}
    for path in sorted(TASK_EVENTS.glob("event-*.json"))[:4000]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
                continue
            event = load_json(path)
            event_id = event.get("event_id")
            task_id = event.get("task_id")
            profile_id = event.get("profile_id")
            summary = event.get("summary")
            if (
                event.get("schema_version") != "1.0" or event_id != path.stem or
                not isinstance(event_id, str) or not event_id.startswith("event-") or len(event_id) > 256 or
                not isinstance(task_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) is None or
                not isinstance(profile_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", profile_id) is None or
                event.get("phase") not in allowed_phases or event.get("source") not in {"assistant", "runtime"} or
                not isinstance(summary, str) or not summary.strip() or len(summary) > 240 or
                not isinstance(event.get("created_at"), str)
            ):
                continue
            result.setdefault(task_id, []).append(event)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    for events in result.values():
        events.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("event_id") or "")))
    return result


def readable_node(node_id: Any) -> str:
    value = str(node_id or "").strip()
    return NODE_DISPLAY_NAMES.get(value, "处理下一阶段" if value else "准备下一步")


def task_progress_timeline(
    task: dict[str, Any], events: list[dict[str, Any]], messages: list[dict[str, Any]], display_status: str
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    audit_titles = {
        "task_started": "任务已接收", "node_completed": "阶段分析完成", "tool_completed": "资料处理完成",
        "write_intent_prepared": "待写入内容已冻结", "write_intent_revised": "你修改了待写入内容",
        "write_commit_started": "开始提交已批准内容",
        "write_commit_rolled_back": "写入已安全回滚", "write_commit_ambiguous": "写入状态需要人工检查",
        "approval_granted": "你已批准继续", "approval_rejected": "你已驳回当前方案",
        "task_resumed": "任务已恢复", "task_cancelled": "任务已结束", "task_completed": "任务已完成",
        "write_reapproval_required": "写入需要重新确认", "task_session_rebound": "智能核心已接管任务",
    }
    for index, audit in enumerate(task.get("audit") if isinstance(task.get("audit"), list) else []):
        if not isinstance(audit, dict) or audit.get("action") not in audit_titles or not isinstance(audit.get("at"), str):
            continue
        node = readable_node(audit.get("node_id"))
        note = audit.get("note") if audit.get("action") == "node_completed" else None
        summary = str(note).strip()[:500] if isinstance(note, str) and note.strip() else (f"已完成：{node}" if audit.get("node_id") else audit_titles[audit["action"]])
        timeline.append({
            "event_id": f"audit-{index}", "at": audit["at"], "kind": "assistant" if audit.get("actor") == "model" else "system",
            "title": audit_titles[audit["action"]], "summary": summary, "status": "done",
        })
    phase_titles = {
        "understanding": "正在理解任务", "collecting": "正在收集资料", "analyzing": "正在分析",
        "drafting": "正在形成内容", "validating": "正在复核", "waiting": "等待确认", "delivering": "正在生成结果",
    }
    for event in events:
        if event.get("profile_id") != task.get("profile_id"):
            continue
        timeline.append({
            "event_id": event["event_id"], "at": event["created_at"], "kind": "assistant",
            "title": phase_titles.get(str(event.get("phase")), "智能助手处理进度"), "summary": event["summary"],
            "basis": str(event.get("basis") or "")[:300], "next_step": str(event.get("next_step") or "")[:240],
            "status": "done",
        })
    for message in messages:
        if message.get("profile_id") != task.get("profile_id"):
            continue
        message_status = str(message.get("status"))
        timeline.append({
            "event_id": message["message_id"], "at": message["created_at"], "kind": "user",
            "title": "你调整了任务方向" if message.get("mode") == "redirect" else "你补充了信息",
            "summary": str(message.get("content") or "")[:1200],
            "status": "queued" if message_status in {"queued", "dispatching"} else "done",
        })
    if display_status not in {"completed", "cancelled", "rejected", "failed", "superseded"}:
        node = readable_node(task.get("waiting_node") or task.get("current_node"))
        if display_status == "waiting_approval":
            title, summary = "等待你的确认", "内容已到人工确认关口；新消息不会替代批准或驳回。"
        elif display_status == "approval_pending":
            title, summary = "正在执行你的审批", "审批已安全保存，智能核心正在核对任务版本和内容校验码。"
        elif display_status == "approval_stalled":
            title, summary = "智能核心尚未接管", "审批已保存，但超过 15 秒仍未被智能核心接管；请检查智能核心运行状态。"
        elif display_status == "interrupted":
            title, summary = "任务已中断", "智能核心没有继续执行，可选择继续任务或重新开始。"
        else:
            title, summary = "当前处理阶段", node
        timeline.append({
            "event_id": f"current-{task.get('version')}", "at": str(task.get("updated_at") or now()),
            "kind": "assistant", "title": title, "summary": summary, "status": "current",
        })
    timeline.sort(key=lambda item: (str(item.get("at") or ""), str(item.get("event_id") or "")))
    return timeline[-80:]


def validate_presentation_brief_request(request_text: str) -> dict[str, Any]:
    prefix, suffix = "[PRESENTATION_BRIEF]", "[/PRESENTATION_BRIEF]"
    if not request_text.startswith(prefix) or not request_text.endswith(suffix):
        raise ValueError("演示文稿工作室请求必须使用结构化需求")
    value = json.loads(request_text[len(prefix):-len(suffix)].strip())
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise ValueError("演示文稿需求版本无效")
    limits = {"topic": 240, "audience": 240, "purpose": 500, "occasion": 240, "language": 40}
    for field, maximum in limits.items():
        candidate = value.get(field)
        if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > maximum:
            raise ValueError(f"演示文稿需求字段 {field} 无效或超过 {maximum} 字")
    if value.get("scene") not in {"weekly", "industry", "government", "custom"}:
        raise ValueError("演示文稿场景无效")
    if value.get("mode") not in {"quick", "standard", "strict"}:
        raise ValueError("演示文稿处理模式无效")
    if value.get("confidentiality") not in {"internal", "restricted", "public"}:
        raise ValueError("演示文稿保密等级无效")
    if value.get("source_scope") != "public-web-and-profile-knowledge":
        raise ValueError("演示文稿工作室首版只支持公开网页与当前角色知识库")
    if not isinstance(value.get("target_slides"), int) or not 4 <= value["target_slides"] <= 10:
        raise ValueError("演示文稿页数必须为 4–10")
    if not isinstance(value.get("duration_minutes"), int) or not 3 <= value["duration_minutes"] <= 120:
        raise ValueError("演示文稿时长必须为 3–120 分钟")
    decision = value.get("expected_decision")
    if not isinstance(decision, str) or not decision.strip() or len(decision) > 500:
        raise ValueError("演示文稿期望决策无效或超过 500 字")
    design = value.get("design_system")
    if not isinstance(design, dict) or design.get("token_id") not in {"management-report", "government-program", "technology-research"}:
        raise ValueError("演示文稿设计风格无效")
    output_name = value.get("output_name")
    if not isinstance(output_name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.pptx", output_name) is None:
        raise ValueError("演示文稿输出文件名无效")
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
            raise ValueError("演示方案只能包含有限数字")
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
    raise ValueError("演示方案必须是有限的结构化数据")


def _validate_write_mutation_changes(
    logical_tool: str, payload: dict[str, Any], mutation: dict[str, Any]
) -> None:
    table = str(mutation.get("table") if logical_tool == "bid.write" else payload.get("table", ""))
    if logical_tool == "knowledge.write":
        allowed = KNOWLEDGE_WRITE_FIELDS
        stable_key = WRITE_STABLE_KEYS.get("knowledge.write")
    elif logical_tool == "sales.write":
        allowed = SALES_WRITE_FIELDS.get(table)
        stable_key = WRITE_STABLE_KEYS.get(table)
    elif logical_tool == "bid.write":
        allowed = BID_WRITE_FIELDS.get(table)
        stable_key = WRITE_STABLE_KEYS.get(table)
    else:
        allowed = None
        stable_key = None
    if allowed is None or stable_key is None:
        raise ValueError("当前待写入目标不支持卡片编辑")
    record_id = mutation.get("record_id")
    changes = mutation.get("changes")
    if (
        mutation.get("operation") not in {"insert", "update"}
        or not isinstance(record_id, str)
        or not record_id.strip() or len(record_id) > 128 or any(character in record_id for character in "\x00\r\n")
        or not isinstance(changes, dict)
        or len(changes) > len(allowed)
    ):
        raise ValueError("待写入卡片结构无效")
    for field, value in changes.items():
        if field not in allowed or not isinstance(value, str) or len(value) > 10_000:
            raise ValueError(f"字段 {field} 不可编辑或内容过长")
        if value.startswith(("\t", "\r")) or re.match(r"^\s*[=+\-@]", value):
            raise ValueError(f"字段 {field} 含有表格公式前缀，不能保存")
    if stable_key in changes and changes[stable_key] != record_id:
        raise ValueError(f"不能修改稳定编号 {stable_key}")
    if logical_tool == "bid.write":
        bid_id = payload.get("bid_id")
        if table == "bid_projects":
            if record_id != bid_id:
                raise ValueError("不能修改投标项目绑定 bid_id")
        elif changes.get("bid_id") != bid_id:
            raise ValueError("不能修改投标记录所属的 bid_id")
    if mutation.get("operation") == "update" and not isinstance(mutation.get("expected_version"), str):
        raise ValueError("更新记录缺少原始版本，不能安全修改")


def revised_write_payload(
    logical_tool: str, current_payload: dict[str, Any], operation: str,
    record_id: str, replacement_changes: Any,
) -> dict[str, Any] | None:
    if logical_tool not in {"knowledge.write", "sales.write", "bid.write"}:
        raise ValueError("正式文件请使用专用修订流程")
    if logical_tool == "sales.write" and str(current_payload.get("table", "")) not in SALES_WRITE_FIELDS:
        raise ValueError("销售台账类型无效")
    if logical_tool == "bid.write":
        bid_id = current_payload.get("bid_id")
        if not isinstance(bid_id, str) or not bid_id.strip() or len(bid_id) > 128:
            raise ValueError("投标项目编号无效")
    mutations = current_payload.get("mutations")
    if not isinstance(mutations, list) or not 1 <= len(mutations) <= 100:
        raise ValueError("当前待写入内容缺少有效卡片")
    matches = [index for index, mutation in enumerate(mutations) if isinstance(mutation, dict) and mutation.get("record_id") == record_id]
    if len(matches) != 1:
        raise ValueError("目标卡片已变化，请刷新后重试")
    revised = json.loads(json.dumps(current_payload, ensure_ascii=False))
    revised_mutations = revised["mutations"]
    if operation == "remove":
        revised_mutations.pop(matches[0])
        if not revised_mutations:
            return None
    elif operation == "edit":
        if not isinstance(replacement_changes, dict):
            raise ValueError("请提交完整的卡片字段")
        revised_mutations[matches[0]]["changes"] = replacement_changes
    else:
        raise ValueError("卡片操作无效")
    seen: set[str] = set()
    for mutation in revised_mutations:
        if not isinstance(mutation, dict):
            raise ValueError("待写入卡片结构无效")
        candidate_id = mutation.get("record_id")
        if (
            not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 128
            or any(character in candidate_id for character in "\x00\r\n") or candidate_id in seen
        ):
            raise ValueError("待写入卡片编号无效或重复")
        seen.add(candidate_id)
        if operation == "edit" and candidate_id == record_id:
            _validate_write_mutation_changes(logical_tool, revised, mutation)
    canonical = canonical_plan_json(revised)
    if len(canonical.encode("utf-8")) > 256 * 1024:
        raise ValueError("修改后的待写入内容超过 256 KiB 上限")
    return revised


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
            "id": workflow["id"], "display_name": workflow.get("display_name") or "业务工作流",
            "nodes": [
                {
                    **{key: node.get(key) for key in ("id", "type", "depends_on", "tool", "skill", "check", "policy")},
                    "display_name": readable_node(node.get("id")),
                    "type_display_name": NODE_TYPE_DISPLAY_NAMES.get(str(node.get("type") or ""), "处理阶段"),
                }
                for node in workflow.get("nodes", [])
            ],
        }
    return result


def default_project() -> dict[str, Any]:
    return {
        "project_id": DEFAULT_PROJECT_ID,
        "name": "销售总监工作空间",
        "description": "默认承接未单独归档的销售任务、资料和产物。",
        "status": "active",
        "created_at": now(),
        "updated_at": now(),
    }


def _bounded_store(path: Path, key: str, maximum_bytes: int = 524_288) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    if path.is_symlink() or path.stat().st_size > maximum_bytes:
        raise ValueError(f"{path.name} 不是有效的本地工作台数据文件")
    value = load_json(path)
    if value.get("schema_version") != "1.0" or not isinstance(value.get(key), list):
        raise ValueError(f"{path.name} 数据结构无效")
    return [item for item in value[key] if isinstance(item, dict)]


def project_records() -> list[dict[str, Any]]:
    projects = _bounded_store(PROJECTS, "projects")
    if not projects:
        projects = [default_project()]
    elif not any(project.get("project_id") == DEFAULT_PROJECT_ID for project in projects):
        projects.insert(0, default_project())
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for project in projects[:50]:
        project_id = safe_id(str(project.get("project_id", "")))
        name = str(project.get("name", "")).strip()
        description = str(project.get("description", "")).strip()
        status = project.get("status")
        if project_id in seen or not name or len(name) > 80 or len(description) > 500 or status not in {"active", "archived"}:
            raise ValueError("项目空间记录无效")
        seen.add(project_id)
        result.append({
            "project_id": project_id,
            "name": name,
            "description": description,
            "status": status,
            "created_at": str(project.get("created_at", ""))[:40],
            "updated_at": str(project.get("updated_at", ""))[:40],
        })
    return result


def save_projects(projects: list[dict[str, Any]]) -> None:
    atomic_json(PROJECTS, {"schema_version": "1.0", "projects": projects, "updated_at": now()})


def create_project_record(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("项目名称必须为 1–80 字")
    if len(description) > 500:
        raise ValueError("项目说明不能超过 500 字")
    with exclusive_task(PROJECTS):
        projects = project_records()
        if len(projects) >= 50:
            raise ValueError("项目空间最多保留 50 个项目")
        if any(project["name"].casefold() == name.casefold() and project["status"] == "active" for project in projects):
            raise ValueError("已有同名的进行中项目")
        timestamp = now()
        project = {
            "project_id": f"project-{uuid.uuid4().hex[:12]}",
            "name": name,
            "description": description,
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        projects.append(project)
        save_projects(projects)
        return project


def active_project(project_id: str) -> dict[str, Any]:
    project_id = safe_id(project_id)
    project = next((item for item in project_records() if item["project_id"] == project_id), None)
    if project is None:
        raise ValueError("项目空间不存在")
    if project["status"] != "active":
        raise ValueError("归档项目不能创建新任务或上传资料")
    return project


def project_files() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    projects_root = INPUTS / "projects"
    if INPUTS.is_symlink() or projects_root.is_symlink() or not projects_root.is_dir():
        return result
    projects = {item["project_id"] for item in project_records()}
    reimbursement_links: dict[str, dict[str, str]] = {}
    ambiguous_reimbursement_links: set[str] = set()
    if REIMBURSEMENT_BATCHES.is_dir() and not REIMBURSEMENT_BATCHES.is_symlink():
        for record_path in REIMBURSEMENT_BATCHES.glob("*.json"):
            try:
                if record_path.is_symlink() or not record_path.is_file() or record_path.stat().st_size > 256_000:
                    continue
                batch = load_json(record_path)
                batch_id = safe_id(str(batch.get("batch_id") or ""))
                if batch.get("schema_version") != "1.0" or batch_id != record_path.stem:
                    continue
                for material in batch.get("materials", []):
                    if isinstance(material, dict) and material.get("location") == "project":
                        relative = str(material.get("path") or "")
                        material_id = safe_id(str(material.get("material_id") or ""))
                        link = {"batch_id": batch_id, "material_id": material_id}
                        if relative in reimbursement_links and reimbursement_links[relative] != link:
                            ambiguous_reimbursement_links.add(relative)
                            reimbursement_links.pop(relative, None)
                        elif relative not in ambiguous_reimbursement_links:
                            reimbursement_links[relative] = link
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    for index, item in enumerate(projects_root.rglob("*")):
        if index >= 2000:
            break
        try:
            if item.name.startswith(".") or item.suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES or item.is_symlink() or not item.is_file():
                continue
            relative = item.relative_to(ROOT).as_posix()
            parts = item.relative_to(projects_root).parts
            if len(parts) != 2 or parts[0] not in projects:
                continue
            project_id = parts[0]
            stat = item.stat()
        except (OSError, ValueError):
            continue
        entry = {
            "name": item.name,
            "path": relative,
            "project_id": project_id,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="minutes"),
            "version": f"{stat.st_mtime_ns}:{stat.st_size}",
            "kind": "project",
        }
        if relative in reimbursement_links:
            entry.update(reimbursement_links[relative])
            entry["kind"] = "reimbursement"
        result.append(entry)
    return sorted(result, key=lambda entry: entry["modified_at"], reverse=True)[:200]


def _managed_file_name(value: Any, suffix: str) -> str:
    name = str(value or "").strip()
    if (
        not name or len(name) > 120 or Path(name).name != name or name in {".", ".."} or
        name[-1] in {".", " "} or re.search(r'[<>:"/\\|?*]', name) is not None or
        any(ord(character) < 32 for character in name)
    ):
        raise ValueError("文件名无效")
    if Path(name).suffix.lower() != suffix.lower():
        raise ValueError("重命名不能改变文件类型")
    return name


def _file_version(path: Path) -> str:
    metadata = path.stat()
    return f"{metadata.st_mtime_ns}:{metadata.st_size}"


def _open_local_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            startfile = getattr(os, "startfile", None)
            if startfile is None:
                raise OSError("Windows 文件打开功能不可用")
            startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, start_new_session=True)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, start_new_session=True)
    except OSError as error:
        raise RuntimeError(f"无法使用系统默认应用打开文件：{error}") from error


def _safe_relative_file(relative: str, allowed_prefixes: tuple[str, ...]) -> Path:
    if not relative or len(relative) > 600 or "\\" in relative:
        raise ValueError("文件路径无效")
    candidate = ROOT / Path(relative)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("文件不存在或已发生变化")
    resolved_root = ROOT.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root) or not any(relative.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError("文件不属于受控资料库")
    return candidate


def open_bid_file(payload: dict[str, Any], *, artifact: bool = False) -> dict[str, Any]:
    bid_id = safe_id(str(payload.get("bid_id") or ""))
    id_field = "artifact_id" if artifact else "document_id"
    record_id = safe_id(str(payload.get(id_field) or ""))
    section = "artifacts" if artifact else "documents"
    snapshot = read_bid_project(ROOT, bid_id, sections=[section])
    matches = [row for row in snapshot["sections"][section] if row.get(id_field) == record_id]
    if len(matches) != 1:
        raise ValueError("投标文件记录不存在或已发生变化")
    record = matches[0]
    relative = str(record.get("relative_path") or "")
    prefix = f"outputs/bids/{bid_id}/" if artifact else f"inputs/bids/{bid_id}/"
    path = _safe_relative_file(relative, (prefix,))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if not secrets.compare_digest(digest.hexdigest(), str(record.get("sha256") or "")):
        raise RuntimeError("投标文件内容已变化，已停止打开；请重新登记或生成")
    _open_local_file(path)
    return {"message": f"已使用系统默认应用打开{'投标产物' if artifact else '投标资料'}。", "path": relative}


def _safe_relative_destination(relative: str, allowed_prefixes: tuple[str, ...]) -> Path:
    parts = Path(relative).parts
    if (
        not relative or len(relative) > 600 or "\\" in relative or not parts or
        any(part in {"", ".", ".."} for part in parts) or
        not any(relative.startswith(prefix) for prefix in allowed_prefixes)
    ):
        raise ValueError("目标文件路径无效")
    target = ROOT / Path(relative)
    resolved_root = ROOT.resolve()
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(resolved_root) or resolved == resolved_root:
        raise ValueError("目标文件路径越出应用范围")
    return target


def _controlled_trash_root() -> Path:
    if RUNTIME.is_symlink() or FILE_TRASH.is_symlink():
        raise ValueError("文件回收站不能是符号链接")
    FILE_TRASH.mkdir(parents=True, exist_ok=True)
    runtime_root = RUNTIME.resolve()
    resolved = FILE_TRASH.resolve()
    if not resolved.is_relative_to(runtime_root) or resolved == runtime_root:
        raise ValueError("文件回收站越出受控运行目录")
    return FILE_TRASH


def _reimbursement_record(batch_id: str) -> tuple[Path, dict[str, Any]]:
    batch_id = safe_id(batch_id)
    path = REIMBURSEMENT_BATCHES / f"{batch_id}.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256_000:
        raise ValueError("报销批次不存在")
    record = load_json(path)
    if record.get("schema_version") != "1.0" or record.get("batch_id") != batch_id or not isinstance(record.get("materials"), list):
        raise ValueError("报销批次记录无效")
    return path, record


def _write_reimbursement_manifest(record: dict[str, Any]) -> None:
    relative = str(record.get("manifest_path") or "")
    if not relative.startswith(f"outputs/reimbursements/{record['batch_id']}/"):
        raise ValueError("报销清单路径无效")
    target = ROOT / relative
    if target.is_symlink():
        raise ValueError("报销清单不能是符号链接")
    lines = ["# 邮箱报销材料清单", "", f"更新时间：{now()}", f"邮箱：{record.get('mailbox') or '未知'}", ""]
    for message in record.get("messages", []):
        if not isinstance(message, dict):
            continue
        lines.extend([f"## {message.get('subject') or '无主题邮件'}", f"- 发件人：{message.get('sender') or '未知'}",
                      f"- 邮件时间：{message.get('received_at') or '未知'}", ""])
    lines.append("## 当前材料")
    materials = [item for item in record["materials"] if isinstance(item, dict)]
    if materials:
        lines.extend(f"- {item.get('name') or item.get('stored_name')} → `{item.get('path')}`" for item in materials)
    else:
        lines.append("- 本批次当前没有材料。")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".material-list-", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def reimbursement_batches() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if REIMBURSEMENT_BATCHES.is_symlink() or not REIMBURSEMENT_BATCHES.is_dir():
        return result
    for path in sorted(REIMBURSEMENT_BATCHES.glob("*.json"))[:200]:
        try:
            batch_id = safe_id(path.stem)
            _, record = _reimbursement_record(batch_id)
            materials = []
            for raw in record["materials"][:200]:
                if not isinstance(raw, dict):
                    continue
                relative = str(raw.get("path") or "")
                allowed = (f"inputs/reimbursements/{batch_id}/", "inputs/projects/")
                target = _safe_relative_file(relative, allowed)
                metadata = target.stat()
                materials.append({
                    **{key: raw.get(key) for key in ("material_id", "name", "stored_name", "path", "size", "sha256", "location", "project_id")},
                    "size": metadata.st_size, "version": f"{metadata.st_mtime_ns}:{metadata.st_size}",
                    "modified_at": datetime.fromtimestamp(metadata.st_mtime).astimezone().isoformat(timespec="minutes"),
                    "batch_id": batch_id, "kind": "reimbursement",
                })
            result.append({
                "batch_id": batch_id, "created_at": str(record.get("created_at") or "")[:40],
                "updated_at": str(record.get("updated_at") or "")[:40],
                "mailbox": str(record.get("mailbox") or "")[:254],
                "message_count": int(record.get("message_count") or 0),
                "manifest_path": str(record.get("manifest_path") or "")[:600],
                "materials": materials, "material_count": len(materials),
            })
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return sorted(result, key=lambda item: item["updated_at"], reverse=True)


def _managed_file(payload: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    relative = str(payload.get("path") or "")
    batch_id = str(payload.get("batch_id") or "")
    material_id = str(payload.get("material_id") or "")
    reimbursement_records = [item for batch in reimbursement_batches() for item in batch["materials"]]
    if batch_id or material_id:
        record = next((item for item in reimbursement_records
                       if item.get("path") == relative and item.get("batch_id") == batch_id and
                       item.get("material_id") == material_id), None)
    else:
        record = next((item for item in project_files() if item.get("path") == relative), None)
    if record is None:
        raise ValueError("文件不属于当前项目或报销材料库")
    path = _safe_relative_file(relative, ("inputs/projects/", "inputs/reimbursements/"))
    expected = str(payload.get("version") or "")
    if not expected or expected != _file_version(path):
        raise RuntimeError("文件已发生变化，请刷新后重试")
    return path, record


def open_managed_file(payload: dict[str, Any]) -> dict[str, Any]:
    path, record = _managed_file(payload)
    _open_local_file(path)
    return {"message": f"已打开 {record['name']}。", "path": record["path"]}


def rename_managed_file(payload: dict[str, Any]) -> dict[str, Any]:
    path, record = _managed_file(payload)
    name = _managed_file_name(payload.get("name"), path.suffix)
    target = path.with_name(name)
    if target.exists() or target.is_symlink():
        raise RuntimeError("同名文件已存在，请换一个名称")
    path.rename(target)
    new_relative = target.relative_to(ROOT).as_posix()
    if record.get("kind") == "reimbursement":
        record_path, batch = _reimbursement_record(str(record["batch_id"]))
        previous_batch = json.loads(json.dumps(batch, ensure_ascii=False))
        material = next((item for item in batch["materials"] if item.get("material_id") == record.get("material_id")), None)
        if material is None or material.get("path") != record.get("path"):
            target.rename(path)
            raise RuntimeError("报销材料记录已变化，请刷新后重试")
        material.update({"name": name, "stored_name": name, "path": new_relative})
        batch["updated_at"] = now()
        try:
            atomic_json(record_path, batch)
            _write_reimbursement_manifest(batch)
        except Exception:
            if target.exists() and not path.exists():
                target.rename(path)
            atomic_json(record_path, previous_batch)
            _write_reimbursement_manifest(previous_batch)
            raise
    return {"message": f"已重命名为 {name}。", "path": new_relative}


def move_reimbursement_to_project(payload: dict[str, Any]) -> dict[str, Any]:
    source, record = _managed_file(payload)
    if record.get("kind") != "reimbursement" or record.get("location") == "project":
        raise ValueError("只能移动报销材料库中的文件")
    project_id = safe_id(str(payload.get("project_id") or ""))
    active_project(project_id)
    projects_root = INPUTS / "projects"
    if INPUTS.is_symlink() or projects_root.is_symlink():
        raise ValueError("项目资料目录不能是符号链接")
    project_root = projects_root / project_id
    project_root.mkdir(parents=True, exist_ok=True)
    if project_root.is_symlink() or project_root.resolve().parent != projects_root.resolve():
        raise ValueError("项目资料目录越出受控 inputs 范围")
    name = _managed_file_name(record.get("name") or record.get("stored_name"), source.suffix)
    target = project_root / name
    if target.exists() or target.is_symlink():
        raise RuntimeError("项目中已有同名文件，请先重命名材料")
    os.link(source, target)
    try:
        if hashlib.sha256(target.read_bytes()).hexdigest() != str(record.get("sha256") or ""):
            raise RuntimeError("移动后的文件校验失败")
        record_path, batch = _reimbursement_record(str(record["batch_id"]))
        previous_batch = json.loads(json.dumps(batch, ensure_ascii=False))
        material = next((item for item in batch["materials"] if item.get("material_id") == record.get("material_id")), None)
        if material is None or material.get("path") != record.get("path"):
            raise RuntimeError("报销材料记录已变化，请刷新后重试")
        material.update({"path": target.relative_to(ROOT).as_posix(), "stored_name": name,
                         "location": "project", "project_id": project_id})
        batch["updated_at"] = now()
        atomic_json(record_path, batch)
        _write_reimbursement_manifest(batch)
        source.unlink()
    except Exception:
        target.unlink(missing_ok=True)
        if "record_path" in locals() and "previous_batch" in locals():
            atomic_json(record_path, previous_batch)
            _write_reimbursement_manifest(previous_batch)
        raise
    return {"message": f"已将 {name} 移动到项目空间。", "path": target.relative_to(ROOT).as_posix()}


def _file_references(relative: str) -> list[str]:
    references: list[str] = []
    if TASKS.is_dir() and not TASKS.is_symlink():
        for path in list(TASKS.glob("*.json"))[:500]:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
                    continue
                task = load_json(path)
                if relative in json.dumps(task, ensure_ascii=False):
                    references.append(f"任务：{task.get('task_id') or path.stem}")
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    for row in _csv_rows("data/knowledge/source-register.csv", 5000):
        if relative in "\n".join(str(value or "") for value in row.values()):
            references.append(f"知识：{row.get('title') or row.get('source_id') or '未命名记录'}")
    return references[:20]


def trash_managed_file(payload: dict[str, Any]) -> dict[str, Any]:
    source, record = _managed_file(payload)
    references = _file_references(record["path"])
    if references and payload.get("acknowledge_references") is not True:
        return {"requires_confirmation": True, "references": references,
                "message": "该文件已被任务或知识记录引用，移入回收站后原路径会失效。"}
    trash_id = f"trash-{uuid.uuid4().hex[:16]}"
    destination_root = _controlled_trash_root() / trash_id
    destination_root.mkdir(parents=True, exist_ok=False)
    destination = destination_root / source.name
    source.replace(destination)
    trash_record = {
        "schema_version": "1.0", "trash_id": trash_id, "kind": "file",
        "original_path": record["path"], "stored_name": source.name,
        "trashed_at": now(), "references": references,
        "reimbursement": None,
    }
    previous_batch = None
    try:
        if record.get("kind") == "reimbursement":
            record_path, batch = _reimbursement_record(str(record["batch_id"]))
            previous_batch = json.loads(json.dumps(batch, ensure_ascii=False))
            index = next((index for index, item in enumerate(batch["materials"])
                          if item.get("material_id") == record.get("material_id") and item.get("path") == record.get("path")), None)
            if index is None:
                raise RuntimeError("报销材料记录已变化，请刷新后重试")
            trash_record["reimbursement"] = {"batch_id": record["batch_id"], "material": batch["materials"][index]}
            del batch["materials"][index]
            batch["updated_at"] = now()
        atomic_json(destination_root / "record.json", trash_record)
        if record.get("kind") == "reimbursement":
            atomic_json(record_path, batch)
            _write_reimbursement_manifest(batch)
    except Exception:
        if previous_batch is not None:
            atomic_json(record_path, previous_batch)
            _write_reimbursement_manifest(previous_batch)
        if destination.exists() and not source.exists():
            destination.replace(source)
        shutil.rmtree(destination_root, ignore_errors=True)
        raise
    return {"message": f"已将 {record['name']} 移入回收站。", "trash_id": trash_id}


def trash_reimbursement_batch(batch_id: str) -> dict[str, Any]:
    record_path, record = _reimbursement_record(batch_id)
    trash_id = f"trash-{uuid.uuid4().hex[:16]}"
    destination = _controlled_trash_root() / trash_id
    destination.mkdir(parents=True, exist_ok=False)
    manifest_root = OUTPUTS / "reimbursements" / batch_id
    entries: list[dict[str, str]] = []
    moved: list[tuple[Path, Path]] = []
    try:
        for material in record["materials"]:
            if not isinstance(material, dict):
                raise ValueError("报销批次材料记录无效")
            relative = str(material.get("path") or "")
            source = _safe_relative_file(relative, (f"inputs/reimbursements/{batch_id}/", "inputs/projects/"))
            material_id = safe_id(str(material.get("material_id") or ""))
            stored = destination / "files" / material_id / source.name
            stored.parent.mkdir(parents=True, exist_ok=False)
            source.replace(stored)
            moved.append((stored, source))
            entries.append({"original_path": relative, "stored_path": stored.relative_to(destination).as_posix()})
        if manifest_root.is_dir() and not manifest_root.is_symlink():
            manifest_root.replace(destination / "manifest")
        record_path.replace(destination / "batch.json")
    except Exception:
        for stored, source in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if stored.exists() and not source.exists():
                stored.replace(source)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    input_root = INPUTS / "reimbursements" / batch_id
    try:
        input_root.rmdir()
    except OSError:
        pass
    try:
        atomic_json(destination / "record.json", {
            "schema_version": "1.0", "trash_id": trash_id, "kind": "reimbursement-batch",
            "batch_id": batch_id, "trashed_at": now(), "material_count": len(record["materials"]),
            "entries": entries,
        })
    except Exception:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        if (destination / "batch.json").exists():
            (destination / "batch.json").replace(record_path)
        if (destination / "manifest").is_dir():
            manifest_root.parent.mkdir(parents=True, exist_ok=True)
            (destination / "manifest").replace(manifest_root)
        for stored, source in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if stored.exists() and not source.exists():
                stored.replace(source)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"message": "报销批次已移入回收站，可随时恢复。", "trash_id": trash_id}


def trash_summary() -> list[dict[str, Any]]:
    result = []
    if FILE_TRASH.is_symlink() or not FILE_TRASH.is_dir():
        return result
    for path in sorted(FILE_TRASH.glob("trash-*/record.json"))[:200]:
        try:
            record = load_json(path)
            if record.get("schema_version") != "1.0" or safe_id(str(record.get("trash_id"))) != path.parent.name:
                continue
            result.append({key: record.get(key) for key in ("trash_id", "kind", "original_path", "stored_name", "batch_id", "trashed_at", "material_count")})
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return sorted(result, key=lambda item: str(item.get("trashed_at") or ""), reverse=True)


def restore_trash(trash_id: str) -> dict[str, Any]:
    trash_id = safe_id(trash_id)
    trash_root = _controlled_trash_root()
    root = trash_root / trash_id
    record_path = root / "record.json"
    if root.is_symlink() or record_path.is_symlink() or not record_path.is_file():
        raise ValueError("回收站记录不存在")
    record = load_json(record_path)
    if record.get("trash_id") != trash_id:
        raise ValueError("回收站记录无效")
    if record.get("kind") == "file":
        original = str(record.get("original_path") or "")
        target = _safe_relative_destination(original, ("inputs/projects/", "inputs/reimbursements/"))
        if target.exists() or target.is_symlink():
            raise RuntimeError("原位置已有同名文件，暂不能恢复")
        stored = root / str(record.get("stored_name") or "")
        if stored.parent != root or stored.is_symlink() or not stored.is_file():
            raise ValueError("回收站中的文件缺失")
        target.parent.mkdir(parents=True, exist_ok=True)
        stored.replace(target)
        reimbursement = record.get("reimbursement")
        if isinstance(reimbursement, dict):
            batch_path, batch = _reimbursement_record(str(reimbursement.get("batch_id") or ""))
            previous_batch = json.loads(json.dumps(batch, ensure_ascii=False))
            material = reimbursement.get("material")
            if not isinstance(material, dict) or any(item.get("material_id") == material.get("material_id") for item in batch["materials"]):
                target.replace(stored)
                raise RuntimeError("报销批次记录已变化，暂不能恢复")
            batch["materials"].append(material)
            batch["updated_at"] = now()
            try:
                atomic_json(batch_path, batch)
                _write_reimbursement_manifest(batch)
            except Exception:
                if target.exists() and not stored.exists():
                    target.replace(stored)
                atomic_json(batch_path, previous_batch)
                _write_reimbursement_manifest(previous_batch)
                raise
    elif record.get("kind") == "reimbursement-batch":
        batch_id = safe_id(str(record.get("batch_id") or ""))
        input_target = INPUTS / "reimbursements" / batch_id
        manifest_target = OUTPUTS / "reimbursements" / batch_id
        batch_target = REIMBURSEMENT_BATCHES / f"{batch_id}.json"
        entries = record.get("entries")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("回收站中的报销批次记录无效")
        restore_pairs: list[tuple[Path, Path]] = []
        for entry in entries:
            original = str(entry.get("original_path") or "")
            stored_relative = str(entry.get("stored_path") or "")
            if not original.startswith((f"inputs/reimbursements/{batch_id}/", "inputs/projects/")) or not stored_relative.startswith("files/"):
                raise ValueError("回收站中的文件路径无效")
            stored_parts = Path(stored_relative).parts
            if any(part in {"", ".", ".."} for part in stored_parts):
                raise ValueError("回收站中的文件路径无效")
            stored = root / stored_relative
            if not stored.resolve(strict=False).is_relative_to(root.resolve()):
                raise ValueError("回收站中的文件路径越界")
            target = _safe_relative_destination(original, (f"inputs/reimbursements/{batch_id}/", "inputs/projects/"))
            restore_pairs.append((stored, target))
        if any(path.exists() or path.is_symlink() for path in (manifest_target, batch_target)) or any(
            target.exists() or target.is_symlink() for _, target in restore_pairs
        ):
            raise RuntimeError("原位置已有同名报销批次，暂不能恢复")
        if (root / "batch.json").is_symlink() or not (root / "batch.json").is_file():
            raise ValueError("回收站中的报销批次记录缺失")
        for stored, _ in restore_pairs:
            if stored.is_symlink() or not stored.is_file():
                raise ValueError("回收站中的报销材料缺失")
        manifest_target.parent.mkdir(parents=True, exist_ok=True)
        batch_target.parent.mkdir(parents=True, exist_ok=True)
        restored: list[tuple[Path, Path]] = []
        try:
            for stored, target in restore_pairs:
                target.parent.mkdir(parents=True, exist_ok=True)
                stored.replace(target)
                restored.append((target, stored))
            if (root / "manifest").is_dir():
                (root / "manifest").replace(manifest_target)
            (root / "batch.json").replace(batch_target)
        except Exception:
            if batch_target.exists() and not (root / "batch.json").exists():
                batch_target.replace(root / "batch.json")
            if manifest_target.is_dir() and not (root / "manifest").exists():
                manifest_target.replace(root / "manifest")
            for target, stored in reversed(restored):
                stored.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and not stored.exists():
                    target.replace(stored)
            raise
    else:
        raise ValueError("回收站记录类型无效")
    record_path.unlink(missing_ok=True)
    if root.resolve().parent != trash_root.resolve():
        raise RuntimeError("回收站清理路径校验失败")
    shutil.rmtree(root)
    return {"message": "文件已恢复到原位置。"}


def legacy_reimbursement_files(files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    migrated: set[str] = set()
    if REIMBURSEMENT_BATCHES.is_dir() and not REIMBURSEMENT_BATCHES.is_symlink():
        for batch_path in REIMBURSEMENT_BATCHES.glob("*.json"):
            try:
                if batch_path.is_symlink() or not batch_path.is_file():
                    continue
                sources = load_json(batch_path).get("legacy_sources", [])
                if isinstance(sources, list):
                    migrated.update(str(source) for source in sources if isinstance(source, str))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    return [item for item in (files if files is not None else project_files())
            if item["name"].startswith("mail-") and not item.get("batch_id") and item["path"] not in migrated][:200]


def migrate_legacy_reimbursements() -> dict[str, Any]:
    legacy = legacy_reimbursement_files()
    if not legacy:
        return {"message": "没有需要迁移的旧报销附件。", "migrated_count": 0}
    identity = "|".join(sorted(f"{item['path']}:{item['version']}" for item in legacy))
    batch_id = f"legacy-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    batch_root = INPUTS / "reimbursements" / batch_id
    record_path = REIMBURSEMENT_BATCHES / f"{batch_id}.json"
    manifest_root = OUTPUTS / "reimbursements" / batch_id
    batch_root.mkdir(parents=True, exist_ok=False)
    materials = []
    try:
        for index, item in enumerate(legacy, start=1):
            source = _safe_relative_file(item["path"], ("inputs/projects/",))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            target = batch_root / f"{item['project_id']}-{item['name']}"
            shutil.copy2(source, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("旧报销附件迁移校验失败")
            materials.append({
                "material_id": f"material-legacy-{index:04d}", "name": item["name"],
                "stored_name": target.name, "path": target.relative_to(ROOT).as_posix(),
                "size": target.stat().st_size, "sha256": digest, "location": "reimbursement",
                "legacy_project_id": item["project_id"],
            })
        record = {
            "schema_version": "1.0", "batch_id": batch_id, "created_at": now(), "updated_at": now(),
            "mailbox": "旧项目资料迁移", "message_count": 0, "messages": [], "materials": materials,
            "legacy_sources": [item["path"] for item in legacy],
            "manifest_path": f"outputs/reimbursements/{batch_id}/material-list.md",
        }
        atomic_json(record_path, record)
        _write_reimbursement_manifest(record)
    except Exception:
        shutil.rmtree(batch_root, ignore_errors=True)
        record_path.unlink(missing_ok=True)
        shutil.rmtree(manifest_root, ignore_errors=True)
        raise
    return {"message": f"已复制并校验 {len(materials)} 个旧报销附件；项目中的原件仍保留。",
            "batch_id": batch_id, "migrated_count": len(materials)}


def schedule_records() -> list[dict[str, Any]]:
    schedules = _bounded_store(SCHEDULES, "schedules")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schedule in schedules[:50]:
        schedule_id = safe_id(str(schedule.get("schedule_id", "")))
        project_id = safe_id(str(schedule.get("project_id", "")))
        service_id = safe_id(str(schedule.get("service_id", "")))
        workflow_id = str(schedule.get("workflow_id", "")).strip()
        name = str(schedule.get("name", "")).strip()
        request = str(schedule.get("request", "")).strip()
        time_local = str(schedule.get("time_local", ""))
        if (
            schedule_id in seen or not name or len(name) > 80 or not request or len(request) > 3500 or
            re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", workflow_id) is None or
            re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_local) is None or
            not isinstance(schedule.get("enabled"), bool)
        ):
            raise ValueError("每日定时任务记录无效")
        seen.add(schedule_id)
        runtime_selection = _stored_runtime_selection(schedule)
        result.append({
            "schedule_id": schedule_id,
            "project_id": project_id,
            "profile_id": "sales-director",
            "service_id": service_id,
            "workflow_id": workflow_id,
            "name": name,
            "request": request,
            "time_local": time_local,
            "enabled": schedule["enabled"],
            "last_enqueued_date": schedule.get("last_enqueued_date"),
            "last_enqueued_at": schedule.get("last_enqueued_at"),
            "created_at": str(schedule.get("created_at", ""))[:40],
            "updated_at": str(schedule.get("updated_at", ""))[:40],
            **runtime_selection,
        })
    return result


def save_schedules(schedules: list[dict[str, Any]]) -> None:
    atomic_json(SCHEDULES, {"schema_version": "1.0", "schedules": schedules, "updated_at": now()})


def sales_service(service_id: str) -> dict[str, Any]:
    profile = next((item for item in profiles() if item["id"] == "sales-director"), None)
    service = next((item for item in (profile or {}).get("services", []) if item.get("id") == service_id), None)
    if service is None:
        raise ValueError("定时任务服务不属于销售总监")
    return service


def create_schedule_record(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    project_id = safe_id(str(payload.get("project_id", "")))
    service_id = safe_id(str(payload.get("service_id", "")))
    request = str(payload.get("request", "")).strip()
    time_local = str(payload.get("time_local", ""))
    if not name or len(name) > 80:
        raise ValueError("定时任务名称必须为 1–80 字")
    if not request or len(request) > 3500:
        raise ValueError("定时任务说明必须为 1–3500 字")
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_local) is None:
        raise ValueError("执行时间必须为 00:00–23:59")
    active_project(project_id)
    service = sales_service(service_id)
    runtime_selection = task_runtime_selection(payload)
    if service_id in {"presentation-studio", "weekly-deck"}:
        validate_presentation_brief_request(request)
    with exclusive_task(SCHEDULES):
        schedules = schedule_records()
        if len(schedules) >= 50:
            raise ValueError("每日定时任务最多保留 50 条")
        timestamp = now()
        schedule = {
            "schedule_id": f"schedule-{uuid.uuid4().hex[:12]}",
            "project_id": project_id,
            "profile_id": "sales-director",
            "service_id": service_id,
            "workflow_id": service["workflow"],
            "name": name,
            "request": request,
            "time_local": time_local,
            "enabled": True,
            "last_enqueued_date": None,
            "last_enqueued_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            **runtime_selection,
        }
        schedules.append(schedule)
        save_schedules(schedules)
        return schedule


def _schedule_request(schedule: dict[str, Any], scheduled_date: str, *, manual: bool = False) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10] if manual else scheduled_date.replace("-", "")
    request_id = f"request-{schedule['schedule_id']}-{suffix}"
    request_path = REQUESTS / f"{request_id}.json"
    if request_path.is_symlink():
        raise ValueError("定时任务请求路径不能是符号链接")
    request_text = (
        f"【每日定时任务：{schedule['name']}】\n"
        f"计划日期：{scheduled_date}\n"
        f"项目空间：{schedule['project_id']}\n"
        f"{schedule['request']}"
    )
    if len(request_text) > 4000:
        raise ValueError("定时任务展开后超过 4000 字")
    record = {
        "schema_version": "1.0",
        "request_id": request_id,
        "status": "requested",
        "profile_id": "sales-director",
        "service_id": schedule["service_id"],
        "workflow_id": schedule["workflow_id"],
        "request": request_text,
        "created_at": now(),
        "source": "local-workbench",
        "project_id": schedule["project_id"],
        "schedule_id": schedule["schedule_id"],
        "scheduled_for": scheduled_date,
        **_stored_runtime_selection(schedule),
    }
    if request_path.is_file():
        existing = load_json(request_path)
        if existing.get("schedule_id") != schedule["schedule_id"] or existing.get("scheduled_for") != scheduled_date:
            raise ValueError("定时任务请求 ID 冲突")
        return existing
    atomic_json(request_path, record)
    return record


def process_due_schedules(current: datetime | None = None) -> int:
    current = current or datetime.now().astimezone()
    date_text = current.date().isoformat()
    time_text = current.strftime("%H:%M")
    if not SCHEDULES.is_file():
        return 0
    enqueued = 0
    try:
        with exclusive_task(SCHEDULES):
            schedules = schedule_records()
            changed = False
            for schedule in schedules:
                if not schedule["enabled"] or schedule["last_enqueued_date"] == date_text or time_text < schedule["time_local"]:
                    continue
                _schedule_request(schedule, date_text)
                schedule["last_enqueued_date"] = date_text
                schedule["last_enqueued_at"] = now()
                schedule["updated_at"] = now()
                changed = True
                enqueued += 1
            if changed:
                save_schedules(schedules)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return 0
    return enqueued


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


def recover_prepared_task_restarts() -> None:
    """Publish a prepared restart only after its source task records the matching replacement."""
    if REQUESTS.is_symlink() or not REQUESTS.is_dir():
        return
    for path in sorted(REQUESTS.glob("request-restart-*.json"))[:500]:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
                continue
            record = load_json(path)
            request_id = safe_id(str(record.get("request_id", "")))
            task_id = safe_id(str(record.get("restart_of_task_id", "")))
            if (
                request_id != path.stem or record.get("status") != "prepared" or
                record.get("source") != "local-workbench" or record.get("request_kind") != "task-restart"
            ):
                continue
            task_path = TASKS / f"{task_id}.json"
            if task_path.is_symlink() or not task_path.is_file():
                continue
            with exclusive_task(task_path):
                task = load_json(task_path)
                requested_by = f"local-workbench-restart:{request_id}"
                approval = task.get("approval_request")
                linked_pending = (
                    isinstance(approval, dict) and approval.get("decision") == "cancel" and
                    approval.get("requested_by") == requested_by and task.get("superseded_by_task_id") == request_id
                )
                linked_terminal = (
                    task.get("status") == "cancelled" and task.get("superseded_by_task_id") == request_id
                )
                if not linked_pending and not linked_terminal:
                    continue
                record["status"] = "requested"
                record["published_at"] = now()
                atomic_json(path, record)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue


def task_summaries() -> list[dict[str, Any]]:
    recover_prepared_presentation_revisions()
    recover_prepared_task_restarts()
    result: list[dict[str, Any]] = []
    if not TASKS.exists():
        return result
    leases = live_agent_task_leases()
    messages_by_task = task_message_records()
    events_by_task = task_progress_records()
    candidates = list(TASKS.glob("*.json"))[:500]
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            task = load_json(path)
            summary = {key: task.get(key) for key in (
                "task_id", "project_id", "schedule_id", "scheduled_for", "profile_id", "service_id", "workflow_id", "status", "current_stage",
                "current_node", "waiting_node", "completed_nodes", "version", "created_at", "updated_at",
                "approval_request", "pending_write", "artifacts", "request", "restarted_from_task_id", "superseded_by_task_id",
                "requested_model", "requested_thinking_level", "effective_model", "effective_thinking_level"
            )}
            if isinstance(summary.get("approval_request"), dict):
                summary["approval_request"] = {
                    key: value for key, value in summary["approval_request"].items() if key != "revised_payload"
                }
            if ACTIVE_PROFILE_ID is not None and task.get("profile_id") != ACTIVE_PROFILE_ID:
                continue
            summary["display_status"], summary["runtime_state"] = task_display_state(task, leases)
            summary["current_node_display_name"] = readable_node(task.get("current_node"))
            summary["waiting_node_display_name"] = readable_node(task.get("waiting_node")) if task.get("waiting_node") else None
            task_id = str(task.get("task_id") or "")
            task_messages = messages_by_task.get(task_id, [])
            summary["progress"] = task_progress_timeline(
                task, events_by_task.get(task_id, []), task_messages, summary["display_status"]
            )
            summary["queued_message_count"] = sum(
                message.get("status") in {"queued", "dispatching"} for message in task_messages
            )
            summary["project_id"] = summary.get("project_id") or DEFAULT_PROJECT_ID
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
    info: dict[str, Any] = {
        "path": relative, "exists": is_regular, "updated_at": None, "records": None,
        "version": "missing",
    }
    if not is_regular:
        return info
    try:
        metadata = path.stat()
    except OSError:
        info["exists"] = False
        return info
    info["updated_at"] = datetime.fromtimestamp(metadata.st_mtime).astimezone().isoformat(timespec="minutes")
    info["version"] = f"{metadata.st_mtime_ns}:{metadata.st_size}"
    try:
        # Parse record boundaries correctly without returning business fields.
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            info["records"] = max(0, sum(1 for _ in csv.reader(handle)) - 1)
    except (OSError, UnicodeError, csv.Error):
        info["records"] = None
    return info


def data_summary() -> dict[str, Any]:
    try:
        selected = business_data_summary(ROOT)
    except BusinessBackendError as error:
        return {"backend": "error", "error": str(error), "knowledge": [], "sales": []}
    if selected["backend"] == "sqlite":
        return selected
    return {
        "backend": "csv", "binding_id": selected["binding_id"],
        "knowledge": [file_summary("data/knowledge/source-register.csv")],
        "sales": [file_summary(f"data/sales/{name}") for name in (
            "customers.csv", "activities.csv", "resource-requests.csv", "sales-assets.csv")],
    }


def output_summary() -> list[dict[str, Any]]:
    if OUTPUTS.is_symlink() or not OUTPUTS.is_dir():
        return []
    result: list[tuple[float, dict[str, Any]]] = []
    known_projects = {project["project_id"] for project in project_records()}
    for index, item in enumerate(OUTPUTS.rglob("*")):
        if index >= 2000:
            break
        try:
            if item.is_symlink() or not item.is_file():
                continue
            modified = item.stat().st_mtime
        except OSError:
            continue
        relative = item.relative_to(ROOT).as_posix()
        relative_parts = item.relative_to(OUTPUTS).parts
        project_id = (
            relative_parts[1]
            if len(relative_parts) >= 3 and relative_parts[0] == "reimbursements" and
            re.fullmatch(r"[A-Za-z0-9_-]+", relative_parts[1]) and relative_parts[1] in known_projects
            else None
        )
        result.append((modified, {
            "name": item.name,
            "path": relative,
            "project_id": project_id,
            "modified_at": datetime.fromtimestamp(modified).astimezone().isoformat(timespec="minutes"),
        }))
    return [entry for _, entry in sorted(result, key=lambda pair: pair[0], reverse=True)[:20]]


def project_summaries(
    tasks: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    local_outputs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    tasks = tasks if tasks is not None else task_summaries()
    files = files if files is not None else project_files()
    local_outputs = local_outputs if local_outputs is not None else output_summary()
    result: list[dict[str, Any]] = []
    for project in project_records():
        project_tasks = [task for task in tasks if task.get("project_id", DEFAULT_PROJECT_ID) == project["project_id"]]
        artifacts = {
            str(path)
            for task in project_tasks
            for path in (task.get("artifacts") if isinstance(task.get("artifacts"), list) else [])
            if isinstance(path, str) and path.startswith("outputs/")
        }
        artifacts.update(
            item["path"] for item in local_outputs if item.get("project_id") == project["project_id"]
        )
        result.append({
            **project,
            "task_count": len(project_tasks),
            "active_task_count": sum(task.get("runtime_state") != "historical" for task in project_tasks),
            "approval_count": sum(task.get("status") == "waiting_approval" for task in project_tasks),
            "file_count": sum(item["project_id"] == project["project_id"] for item in files),
            "artifact_count": len(artifacts),
        })
    return result


def set_project_status(project_id: str, status: str) -> dict[str, Any]:
    project_id = safe_id(project_id)
    if project_id == DEFAULT_PROJECT_ID:
        raise ValueError("默认项目空间不能归档")
    if status not in {"active", "archived"}:
        raise ValueError("项目状态无效")
    with exclusive_task(PROJECTS):
        projects = project_records()
        project = next((item for item in projects if item["project_id"] == project_id), None)
        if project is None:
            raise ValueError("项目空间不存在")
        project["status"] = status
        project["updated_at"] = now()
        if status == "archived" and SCHEDULES.is_file():
            with exclusive_task(SCHEDULES):
                schedules = schedule_records()
                for schedule in schedules:
                    if schedule["project_id"] == project_id:
                        schedule["enabled"] = False
                        schedule["updated_at"] = now()
                save_schedules(schedules)
        save_projects(projects)
        return project


def set_schedule_enabled(schedule_id: str, enabled: bool) -> dict[str, Any]:
    schedule_id = safe_id(schedule_id)
    if not isinstance(enabled, bool):
        raise ValueError("定时任务状态无效")
    with exclusive_task(SCHEDULES):
        schedules = schedule_records()
        schedule = next((item for item in schedules if item["schedule_id"] == schedule_id), None)
        if schedule is None:
            raise ValueError("每日定时任务不存在")
        if enabled:
            active_project(schedule["project_id"])
        schedule["enabled"] = enabled
        schedule["updated_at"] = now()
        save_schedules(schedules)
        return schedule


def run_schedule_now(schedule_id: str) -> dict[str, Any]:
    schedule_id = safe_id(schedule_id)
    with exclusive_task(SCHEDULES):
        schedules = schedule_records()
        schedule = next((item for item in schedules if item["schedule_id"] == schedule_id), None)
        if schedule is None:
            raise ValueError("每日定时任务不存在")
        active_project(schedule["project_id"])
        record = _schedule_request(schedule, datetime.now().astimezone().date().isoformat(), manual=True)
        schedule["last_enqueued_at"] = now()
        schedule["updated_at"] = now()
        save_schedules(schedules)
        return record


def _match_text(query: str, values: list[Any]) -> bool:
    haystack = "\n".join(str(value) for value in values if value is not None).casefold()
    return query.casefold() in haystack


def _snippet(values: list[Any], maximum: int = 220) -> str:
    text = " · ".join(str(value).strip() for value in values if str(value or "").strip())
    return text[:maximum]


def service_display_name(service_id: Any) -> str:
    value = str(service_id or "").strip()
    for profile in profiles():
        for service in profile.get("services", []):
            if service.get("id") == value:
                return str(service.get("display_name") or "销售任务")
    return "销售任务"


def _csv_rows(relative: str, limit: int = 5000) -> list[dict[str, str]]:
    path = ROOT / relative
    try:
        metadata = path.stat()
    except OSError:
        return []
    if path.is_symlink() or not path.is_file() or metadata.st_size > 16 * 1024 * 1024:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for _, row in zip(range(limit), csv.DictReader(handle))]
    except (OSError, UnicodeError, csv.Error):
        return []


def knowledge_entries(limit: int = 500) -> dict[str, Any]:
    try:
        return business_knowledge_entries(ROOT, limit)
    except BusinessBackendError as error:
        return {"version": "error", "entries": [], "truncated": False, "error": str(error)}


def open_knowledge_source(payload: dict[str, Any]) -> str:
    url = str(payload.get("url") or "").strip()
    if not url or len(url) > 2048:
        raise ValueError("来源链接无效")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源链接只允许普通 HTTP/HTTPS 地址")
    try:
        allowed = business_knowledge_urls(ROOT)
    except BusinessBackendError as error:
        raise ValueError(str(error)) from error
    if url not in allowed:
        raise ValueError("该链接不属于当前知识库记录")
    if not webbrowser.open(url, new=2):
        raise RuntimeError("无法打开系统浏览器；可在知识卡片中复制来源链接")
    return url


def local_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if len(query) < 2 or len(query) > 100:
        raise ValueError("搜索词必须为 2–100 字")
    requested_scopes = payload.get("scopes", ["projects", "tasks", "knowledge", "sales", "files", "outputs"])
    allowed_scopes = {"projects", "tasks", "knowledge", "sales", "files", "outputs"}
    if not isinstance(requested_scopes, list) or not requested_scopes or any(scope not in allowed_scopes for scope in requested_scopes):
        raise ValueError("搜索范围无效")
    scopes = set(requested_scopes)
    results: list[dict[str, Any]] = []

    def add(kind: str, title: str, subtitle: str, snippet: str, reference: str, project_id: str = DEFAULT_PROJECT_ID) -> None:
        if len(results) >= 60:
            return
        results.append({
            "kind": kind, "title": title[:160], "subtitle": subtitle[:200],
            "snippet": snippet[:400], "reference": reference[:300], "project_id": project_id,
        })

    if "projects" in scopes:
        for project in project_records():
            if _match_text(query, [project["name"], project["description"]]):
                add("项目", project["name"], PROJECT_STATUS_DISPLAY_NAMES.get(project["status"], "状态未知"), project["description"], project["project_id"], project["project_id"])
    if "tasks" in scopes:
        for task in task_summaries():
            if _match_text(query, [task.get("service_id"), task.get("request"), task.get("status")]):
                add("任务", service_display_name(task.get("service_id")), TASK_STATUS_DISPLAY_NAMES.get(str(task.get("status") or ""), "状态未知"), str(task.get("request") or ""), str(task.get("task_id") or ""), str(task.get("project_id") or DEFAULT_PROJECT_ID))
    if "knowledge" in scopes:
        knowledge = knowledge_entries(5000)
        for row in knowledge.get("entries", []):
            fields = [row.get(key) for key in ("title", "publisher", "region", "topic", "notes", "status")]
            if _match_text(query, fields):
                add("知识", row.get("title") or "未命名来源", _snippet([row.get("publisher"), row.get("published_date"), row.get("status")]), _snippet([row.get("topic"), row.get("region"), row.get("notes")]), row.get("url") or row.get("source_id") or "")
    if "sales" in scopes:
        try:
            for row in search_business_records(ROOT, query, 60 - len(results)) if len(results) < 60 else []:
                add(str(row.get("kind") or "销售"), str(row.get("title") or "未命名记录"), str(row.get("subtitle") or ""), str(row.get("snippet") or ""), str(row.get("reference") or ""))
        except BusinessBackendError as error:
            add("销售数据", "业务存储暂不可用", "请检查存储配置", str(error), "storage-backend")
    if "files" in scopes:
        for item in project_files():
            if _match_text(query, [item["name"], item["path"]]):
                add("项目文件", item["name"], item["modified_at"], item["path"], item["path"], item["project_id"])
    if "outputs" in scopes:
        for item in output_summary():
            if _match_text(query, [item["name"], item["path"]]):
                add("产物", item["name"], item["modified_at"], item["path"], item["path"])
    return {"query": query, "results": results, "truncated": len(results) >= 60}


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

    def send_business_error(self, error: BusinessBackendError | ValueError) -> None:
        code = error.code if isinstance(error, BusinessBackendError) else "INVALID_INPUT"
        if code == "NOT_FOUND":
            status = HTTPStatus.NOT_FOUND
        elif code.startswith("INVALID"):
            status = HTTPStatus.BAD_REQUEST
        elif code in {"STORE_BUSY", "SCAN_LIMIT"}:
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.CONFLICT
        self.send_json(status, {"error": str(error), "code": code})

    def send_bid_error(self, error: BidStoreError | ValueError) -> None:
        code = error.code if isinstance(error, BidStoreError) else "INVALID_INPUT"
        if code == "NOT_FOUND":
            status = HTTPStatus.NOT_FOUND
        elif code in {"INVALID_INPUT", "UNSAFE_PATH", "FILE_TOO_LARGE"}:
            status = HTTPStatus.BAD_REQUEST
        elif code == "STORE_BUSY":
            status = HTTPStatus.SERVICE_UNAVAILABLE
        else:
            status = HTTPStatus.CONFLICT
        self.send_json(status, {"error": str(error), "code": code})

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

    def upload_project_file(self) -> None:
        project_id = safe_id(self.headers.get("X-Project-Id", ""))
        active_project(project_id)
        encoded_name = self.headers.get("X-File-Name", "")
        filename = unquote(encoded_name).strip()
        if (
            not filename or len(filename) > 120 or Path(filename).name != filename or
            filename in {".", ".."} or filename[-1] in {".", " "} or
            re.search(r'[<>:"/\\|?*]', filename) is not None or
            any(ord(character) < 32 for character in filename) or
            Path(filename).suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES
        ):
            raise ValueError("文件名无效；仅支持电子文档、文字文档、表格、文本和演示文稿文件")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("上传文件必须为 1 字节至 32 兆字节")
        projects_root = INPUTS / "projects"
        if INPUTS.is_symlink() or projects_root.is_symlink():
            raise ValueError("项目资料目录不能是符号链接")
        project_root = projects_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        inputs_root = INPUTS.resolve()
        actual_root = project_root.resolve()
        if not actual_root.is_relative_to(inputs_root) or project_root.is_symlink():
            raise ValueError("项目资料目录越出受控 inputs 范围")
        target = project_root / filename
        if target.exists() or target.is_symlink():
            self.send_json(HTTPStatus.CONFLICT, {"error": "同名文件已存在；请改名后上传，工作台不会覆盖原文件"})
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=project_root)
        temporary = Path(temporary_name)
        received = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while received < length:
                    block = self.rfile.read(min(1024 * 1024, length - received))
                    if not block:
                        raise ValueError("上传连接提前中断")
                    handle.write(block)
                    received += len(block)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target)
        except FileExistsError:
            self.send_json(HTTPStatus.CONFLICT, {"error": "同名文件已存在；请改名后上传，工作台不会覆盖原文件"})
            return
        finally:
            temporary.unlink(missing_ok=True)
        relative = target.relative_to(ROOT).as_posix()
        self.send_json(HTTPStatus.CREATED, {
            "name": filename, "path": relative, "project_id": project_id,
            "size": received, "message": "资料已保存到项目空间；创建任务时可直接引用该路径。",
        })

    def upload_bid_file(self) -> None:
        bid_id = safe_id(self.headers.get("X-Bid-Id", ""))
        role = str(self.headers.get("X-Bid-Role", "tender")).strip()
        if role not in {"tender", "addendum", "template", "reference", "company_material"}:
            raise ValueError("请选择有效的投标资料类型")
        read_bid_project(ROOT, bid_id, sections=[])
        encoded_name = self.headers.get("X-File-Name", "")
        filename = unquote(encoded_name).strip()
        if (
            not filename or len(filename) > 120 or Path(filename).name != filename or
            filename in {".", ".."} or filename[-1] in {".", " "} or
            re.search(r'[<>:"/\\|?*]', filename) is not None or
            any(ord(character) < 32 for character in filename) or
            Path(filename).suffix.lower() not in ALLOWED_BID_SUFFIXES
        ):
            raise ValueError("文件名无效；仅支持常见招标文件、表格、图片、压缩包和电子文档")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BID_FILE_BYTES:
            raise ValueError("上传文件必须为 1 字节至 32 兆字节")
        inputs_root_path = ROOT / "inputs"
        bids_root = inputs_root_path / "bids"
        if inputs_root_path.is_symlink() or bids_root.is_symlink():
            raise ValueError("投标资料目录不能是符号链接")
        bid_root = bids_root / bid_id
        bid_root.mkdir(parents=True, exist_ok=True)
        inputs_root = inputs_root_path.resolve()
        actual_root = bid_root.resolve()
        if not actual_root.is_relative_to(inputs_root) or bid_root.is_symlink():
            raise ValueError("投标资料目录越出受控 inputs 范围")
        target = bid_root / filename
        if target.exists() or target.is_symlink():
            self.send_json(HTTPStatus.CONFLICT, {"error": "当前投标项目已有同名文件；请改名后上传，工作台不会覆盖原文件"})
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=".bid-upload-", suffix=".tmp", dir=bid_root)
        temporary = Path(temporary_name)
        received = 0
        linked = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while received < length:
                    block = self.rfile.read(min(1024 * 1024, length - received))
                    if not block:
                        raise ValueError("上传连接提前中断")
                    handle.write(block)
                    received += len(block)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target)
            linked = True
            document = register_bid_document(
                ROOT,
                bid_id,
                target.relative_to(ROOT).as_posix(),
                role=role,
                display_name=filename,
            )
        except FileExistsError:
            self.send_json(HTTPStatus.CONFLICT, {"error": "当前投标项目已有同名文件；请改名后上传，工作台不会覆盖原文件"})
            return
        except Exception:
            if linked:
                target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        self.send_json(HTTPStatus.CREATED, {
            **document,
            "message": "资料已保存到投标项目并完成文件指纹登记。",
        })

    def do_GET(self) -> None:
        if not self.local_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        parsed_request = urlparse(self.path)
        route = parsed_request.path
        if route == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if route == "/api/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "profile_id": ACTIVE_PROFILE_ID})
            return
        if route == "/api/bootstrap":
            process_due_schedules()
            tasks = task_summaries()
            files = project_files()
            outputs = output_summary()
            batches = reimbursement_batches()
            self.send_json(HTTPStatus.OK, {"profiles": profiles(), "workflows": workflows(), "tasks": tasks,
                                           "data": data_summary(), "outputs": outputs,
                                            "projects": project_summaries(tasks, files, outputs), "project_files": files,
                                            "schedules": schedule_records(),
                                            "model": model_settings_summary(ROOT),
                                            "search": search_settings_summary(ROOT),
                                            "search_gateway": search_gateway_settings_summary(ROOT),
                                            "mail": mail_settings_summary(ROOT),
                                            "reimbursements": {"batches": batches,
                                                               "trash": trash_summary(),
                                                               "legacy_count": len(legacy_reimbursement_files(files))},
                                            "bidding": bid_dashboard(ROOT),
                                            "desktop_runtime": desktop_runtime_summary(),
                                            "request_token": SERVER_TOKEN})
            return
        if route == "/api/desktop-settings":
            self.send_json(HTTPStatus.OK, desktop_runtime_summary())
            return
        if route == "/api/model-settings":
            self.send_json(HTTPStatus.OK, model_settings_summary(ROOT))
            return
        if route == "/api/search-settings":
            self.send_json(HTTPStatus.OK, search_settings_summary(ROOT))
            return
        if route == "/api/search-gateway":
            self.send_json(HTTPStatus.OK, search_gateway_settings_summary(ROOT))
            return
        if route == "/api/mail-settings":
            self.send_json(HTTPStatus.OK, mail_settings_summary(ROOT))
            return
        if route == "/api/tasks":
            self.send_json(HTTPStatus.OK, task_summaries())
            return
        if route == "/api/knowledge":
            self.send_json(HTTPStatus.OK, knowledge_entries())
            return
        if route == "/api/accounts":
            try:
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=20)
                filters = {
                    key: query[key][0]
                    for key in ("owner", "region", "sector", "lifecycle_stage", "health", "project_id")
                    if key in query and query[key]
                }
                result = search_accounts(
                    ROOT,
                    query=query.get("query", [""])[0],
                    filters=filters,
                    updated_since=query.get("updated_since", [None])[0],
                    cursor=query.get("cursor", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                )
                self.send_json(HTTPStatus.OK, result)
            except (BusinessBackendError, ValueError) as error:
                self.send_business_error(error)
            return
        if route.startswith("/api/accounts/") and route.endswith("/360"):
            try:
                parts = route.split("/")
                if len(parts) != 5:
                    raise ValueError("客户 360 地址无效")
                account_id = unquote(parts[3])
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=10)
                sections_text = query.get("sections", [""])[0]
                sections = [item for item in sections_text.split(",") if item] if sections_text else None
                result = read_account_360(
                    ROOT,
                    account_id,
                    sections=sections,
                    since=query.get("since", [None])[0],
                )
                self.send_json(HTTPStatus.OK, result)
            except (BusinessBackendError, ValueError) as error:
                self.send_business_error(error)
            return
        if route.startswith("/api/accounts/") and route.endswith("/timeline"):
            try:
                parts = route.split("/")
                if len(parts) != 5:
                    raise ValueError("客户时间线地址无效")
                account_id = unquote(parts[3])
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=10)
                kinds_text = query.get("kinds", [""])[0]
                kinds = [item for item in kinds_text.split(",") if item] if kinds_text else None
                result = read_account_timeline(
                    ROOT,
                    account_id,
                    kinds=kinds,
                    cursor=query.get("cursor", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                )
                self.send_json(HTTPStatus.OK, result)
            except (BusinessBackendError, ValueError) as error:
                self.send_business_error(error)
            return
        if route == "/api/attention":
            try:
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=5)
                result = read_today_focus(ROOT, limit=int(query.get("limit", ["20"])[0]))
                self.send_json(HTTPStatus.OK, result)
            except (BusinessBackendError, ValueError) as error:
                self.send_business_error(error)
            return
        if route == "/api/signals":
            try:
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=10)
                result = read_signals(
                    ROOT,
                    account_id=query.get("account_id", [None])[0],
                    status=query.get("status", [None])[0],
                    severity=query.get("severity", [None])[0],
                    cursor=query.get("cursor", [None])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                )
                self.send_json(HTTPStatus.OK, result)
            except (BusinessBackendError, ValueError) as error:
                self.send_business_error(error)
            return
        if route == "/api/bids":
            try:
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=20)
                statuses_text = query.get("statuses", [""])[0]
                statuses = [item for item in statuses_text.split(",") if item] if statuses_text else None
                result = search_bid_projects(
                    ROOT,
                    query=query.get("query", [""])[0],
                    statuses=statuses,
                    account_id=query.get("account_id", [None])[0],
                    workspace_project_id=query.get("project_id", [None])[0],
                    limit=int(query.get("limit", ["50"])[0]),
                )
                self.send_json(HTTPStatus.OK, result)
            except (BidStoreError, ValueError) as error:
                self.send_bid_error(error)
            return
        if route == "/api/bids/dashboard":
            try:
                self.send_json(HTTPStatus.OK, bid_dashboard(ROOT))
            except (BidStoreError, ValueError) as error:
                self.send_bid_error(error)
            return
        if route.startswith("/api/bids/") and route.endswith("/timeline"):
            try:
                parts = route.split("/")
                if len(parts) != 5:
                    raise ValueError("投标时间线地址无效")
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=5)
                self.send_json(
                    HTTPStatus.OK,
                    read_bid_timeline(ROOT, unquote(parts[3]), limit=int(query.get("limit", ["100"])[0])),
                )
            except (BidStoreError, ValueError) as error:
                self.send_bid_error(error)
            return
        if route.startswith("/api/bids/") and route.endswith("/360"):
            try:
                parts = route.split("/")
                if len(parts) != 5:
                    raise ValueError("投标项目地址无效")
                query = parse_qs(parsed_request.query, keep_blank_values=True, max_num_fields=5)
                sections_text = query.get("sections", [""])[0]
                sections = [item for item in sections_text.split(",") if item] if sections_text else None
                self.send_json(HTTPStatus.OK, read_bid_project(ROOT, unquote(parts[3]), sections=sections))
            except (BidStoreError, ValueError) as error:
                self.send_bid_error(error)
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
            route = urlparse(self.path).path
            if route == "/api/project-files":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
                    self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "项目资料上传只接受二进制文件"})
                    return
                self.upload_project_file()
                return
            if route == "/api/bid-files":
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
                    self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "投标资料上传只接受二进制文件"})
                    return
                self.upload_bid_file()
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "只接受 JSON 请求"})
                return
            payload = self.body()
            if route == "/api/task-requests":
                self.create_request(payload)
            elif route == "/api/bids":
                self.send_json(HTTPStatus.CREATED, create_bid_project(ROOT, payload))
            elif route.startswith("/api/bids/") and route.endswith("/transition"):
                parts = route.split("/")
                if len(parts) != 5:
                    raise ValueError("投标项目阶段地址无效")
                self.send_json(HTTPStatus.OK, transition_bid_project(
                    ROOT,
                    unquote(parts[3]),
                    status=str(payload.get("status", "")),
                    current_stage=str(payload.get("current_stage", "")),
                    expected_version=payload.get("expected_version"),
                ))
            elif route.startswith("/api/bids/") and route.endswith("/checks/run"):
                parts = route.split("/")
                if len(parts) != 6:
                    raise ValueError("投标检查地址无效")
                self.send_json(HTTPStatus.OK, run_deterministic_bid_checks(ROOT, unquote(parts[3])))
            elif route.startswith("/api/bids/") and len(route.split("/")) == 4:
                self.send_json(HTTPStatus.OK, update_bid_project(ROOT, unquote(route.split("/")[3]), payload))
            elif route == "/api/projects":
                self.send_json(HTTPStatus.CREATED, create_project_record(payload))
            elif route.startswith("/api/projects/") and route.endswith("/status"):
                self.send_json(HTTPStatus.OK, set_project_status(route.split("/")[3], str(payload.get("status", ""))))
            elif route == "/api/schedules":
                self.send_json(HTTPStatus.CREATED, create_schedule_record(payload))
            elif route.startswith("/api/schedules/") and route.endswith("/enabled"):
                self.send_json(HTTPStatus.OK, set_schedule_enabled(route.split("/")[3], payload.get("enabled")))
            elif route.startswith("/api/schedules/") and route.endswith("/run"):
                self.send_json(HTTPStatus.CREATED, run_schedule_now(route.split("/")[3]))
            elif route == "/api/search":
                self.send_json(HTTPStatus.OK, local_search(payload))
            elif route == "/api/model-discovery":
                self.discover_model_options(payload)
            elif route == "/api/model-settings":
                self.configure_model(payload)
            elif route == "/api/model-settings/reset":
                self.reset_model()
            elif route == "/api/search-settings":
                self.configure_search(payload)
            elif route == "/api/search-settings/reset":
                self.reset_search()
            elif route == "/api/search-settings/open-dashboard":
                if not webbrowser.open(BRAVE_DASHBOARD_URL, new=2):
                    raise RuntimeError("无法打开系统浏览器；请手动访问公开检索服务控制台")
                self.send_json(HTTPStatus.OK, {"message": "已在系统浏览器中打开公开检索服务控制台。"})
            elif route == "/api/search-gateway":
                self.configure_search_gateway(payload)
            elif route == "/api/search-gateway/reset":
                self.reset_search_gateway()
            elif route == "/api/search-gateway/open-dashboard":
                settings = search_gateway_settings_summary(ROOT)
                base_url = str(settings.get("base_url", ""))
                if not settings.get("configured") or not base_url:
                    raise SearchGatewayError("请先配置 One Search 聚合网关")
                if not webbrowser.open(base_url, new=2):
                    raise RuntimeError("无法打开系统浏览器；请手动访问 One Search 网关地址")
                self.send_json(HTTPStatus.OK, {"message": "已在系统浏览器中打开 One Search。"})
            elif route == "/api/data-directory/open":
                directory = open_data_directory()
                self.send_json(HTTPStatus.OK, {
                    "message": "已打开本地数据目录。", "path": str(directory),
                })
            elif route == "/api/knowledge/file/open":
                file_path = open_knowledge_file()
                sqlite_directory = file_path.is_dir()
                self.send_json(HTTPStatus.OK, {
                    "message": (
                        "当前知识库使用 SQLite；已打开其所在数据目录，请在工作台知识库页面查看和检索内容。"
                        if sqlite_directory else "已使用系统默认表格软件打开知识库。"
                    ),
                    "path": str(file_path),
                })
            elif route == "/api/knowledge/source/open":
                url = open_knowledge_source(payload)
                self.send_json(HTTPStatus.OK, {"message": "已在系统浏览器中打开来源。", "url": url})
            elif route == "/api/mail-settings":
                result = configure_mail_provider(
                    ROOT, payload, str(payload.get("credential", "")).strip()
                )
                self.send_json(HTTPStatus.OK, {
                    **result,
                    "message": "邮箱已验证并安全保存在本机；后续只读检索收件箱。",
                })
            elif route == "/api/mail-settings/reset":
                self.send_json(HTTPStatus.OK, {
                    **clear_mail_provider(ROOT),
                    "message": "邮箱连接已移除，本机保存的授权码也已清除。",
                })
            elif route == "/api/reimbursements/mail/search":
                self.send_json(HTTPStatus.OK, search_reimbursement_mail(ROOT, payload))
            elif route == "/api/reimbursements/mail/import":
                result = import_reimbursement_mail(
                    ROOT, INPUTS, OUTPUTS, payload.get("selected")
                )
                self.send_json(HTTPStatus.CREATED, result)
            elif route == "/api/bid-files/open":
                self.send_json(HTTPStatus.OK, open_bid_file(payload))
            elif route == "/api/bid-artifacts/open":
                self.send_json(HTTPStatus.OK, open_bid_file(payload, artifact=True))
            elif route == "/api/files/open":
                self.send_json(HTTPStatus.OK, open_managed_file(payload))
            elif route == "/api/files/rename":
                self.send_json(HTTPStatus.OK, rename_managed_file(payload))
            elif route == "/api/files/trash":
                self.send_json(HTTPStatus.OK, trash_managed_file(payload))
            elif route.startswith("/api/reimbursements/batches/") and route.endswith("/trash"):
                self.send_json(HTTPStatus.OK, trash_reimbursement_batch(route.split("/")[4]))
            elif route == "/api/reimbursements/move-to-project":
                self.send_json(HTTPStatus.OK, move_reimbursement_to_project(payload))
            elif route == "/api/reimbursements/migrate-legacy":
                self.send_json(HTTPStatus.OK, migrate_legacy_reimbursements())
            elif route.startswith("/api/file-trash/") and route.endswith("/restore"):
                self.send_json(HTTPStatus.OK, restore_trash(route.split("/")[3]))
            elif route == "/api/desktop-settings":
                self.send_json(HTTPStatus.OK, {
                    **save_desktop_settings(payload),
            "message": "运行方式已保存，关闭并重新打开销售总监智能工作台后生效。",
                })
            elif route.startswith("/api/tasks/") and route.endswith("/decision"):
                self.decide(route.split("/")[3], payload)
            elif route.startswith("/api/tasks/") and route.endswith("/restart"):
                self.restart_task(route.split("/")[3], payload)
            elif route.startswith("/api/tasks/") and route.endswith("/delete"):
                self.delete_task(route.split("/")[3], payload)
            elif route.startswith("/api/tasks/") and route.endswith("/messages"):
                self.create_task_message(route.split("/")[3], payload)
            elif route.startswith("/api/tasks/") and route.endswith("/presentation-revision"):
                self.create_presentation_revision(route.split("/")[3], payload)
            elif route.startswith("/api/tasks/") and route.endswith("/write-intent-revision"):
                self.revise_write_intent(route.split("/")[3], payload)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except BidStoreError as error:
            self.send_bid_error(error)
        except (ValueError, KeyError, json.JSONDecodeError, ModelProviderError, SearchProviderError, SearchGatewayError, MailProviderError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
        except OSError as error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"本地文件操作失败：{error}"})

    def discover_model_options(self, payload: dict[str, Any]) -> None:
        base_url = str(payload.get("base_url", ""))
        supplied_key = str(payload.get("api_key", "")).strip()
        allow_private = payload.get("allow_private_network") is True
        normalized = normalize_base_url(base_url, allow_private_network=allow_private)
        api_key = supplied_key or load_model_secret(ROOT, normalized)
        if not api_key:
            raise ModelProviderError("请填写接口密钥；已保存的密钥只可用于同一个网关地址")
        normalized, models = discover_models(
            normalized, api_key, allow_private_network=allow_private
        )
        self.send_json(HTTPStatus.OK, {
            "base_url": normalized, "models": models,
            "message": f"已从网关读取 {len(models)} 个模型，接口密钥未写入页面或模型目录。",
        })

    def configure_model(self, payload: dict[str, Any]) -> None:
        selected_model = str(payload.get("selected_model", "")).strip()
        if not selected_model or len(selected_model) > 200:
            raise ModelProviderError("请先获取并选择一个模型")
        result = configure_model_provider(
            ROOT,
            base_url=str(payload.get("base_url", "")),
            api_key=str(payload.get("api_key", "")).strip() or None,
            selected_model=selected_model,
            allow_private_network=payload.get("allow_private_network") is True,
        )
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "模型配置已保存。请关闭并重新打开销售总监智能工作台，使新模型接管后续任务。",
        })

    def reset_model(self) -> None:
        result = clear_model_provider(ROOT)
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "已恢复为智能核心默认模型。请关闭并重新打开销售总监智能工作台后生效。",
        })

    def configure_search(self, payload: dict[str, Any]) -> None:
        result = configure_search_provider(
            ROOT,
            api_key=str(payload.get("api_key", "")).strip() or None,
        )
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "公开检索服务已验证并使用系统保护存储保存。请关闭并重新打开销售总监智能工作台后重试检索任务。",
        })

    def reset_search(self) -> None:
        result = clear_search_provider(ROOT)
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "公开检索密钥已删除。请关闭并重新打开销售总监智能工作台，使运行时停止使用旧密钥。",
        })

    def configure_search_gateway(self, payload: dict[str, Any]) -> None:
        max_results = payload.get("max_results", 8)
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise SearchGatewayError("每次查询结果数必须是 1–10 的整数")
        selected_providers = payload.get("selected_providers", [])
        if not isinstance(selected_providers, list):
            raise SearchGatewayError("搜索来源选项格式无效")
        result = configure_search_gateway(
            ROOT,
            base_url=str(payload.get("base_url", "")),
            token=str(payload.get("token", "")).strip() or None,
            mode=str(payload.get("mode", "parallel")),
            max_results=max_results,
            allow_private_network=payload.get("allow_private_network") is True,
            selected_providers=selected_providers,
        )
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "One Search 已验证并保存。请关闭并重新打开工作台，使后续公开检索优先使用聚合网关。",
        })

    def reset_search_gateway(self) -> None:
        result = clear_search_gateway(ROOT)
        self.send_json(HTTPStatus.OK, {
            **result,
            "restart_required": True,
            "message": "搜索聚合网关已停用。请关闭并重新打开工作台，恢复使用原有公开检索。",
        })

    def create_request(self, payload: dict[str, Any]) -> None:
        profile_id = safe_id(str(payload.get("profile_id", "")))
        service_id = safe_id(str(payload.get("service_id", "")))
        project_id = safe_id(str(payload.get("project_id", DEFAULT_PROJECT_ID)))
        request_text = str(payload.get("request", "")).strip()
        if not request_text or len(request_text) > 4000:
            raise ValueError("请填写 1 到 4000 字的任务说明")
        profile = next((item for item in profiles() if item["id"] == profile_id), None)
        if profile is None:
            raise ValueError("未知角色")
        active_project(project_id)
        service = next((item for item in profile["services"] if item["id"] == service_id), None)
        if service is None:
            raise ValueError("该服务不属于当前角色")
        if service_id in PUBLIC_SEARCH_SERVICES:
            search = search_settings_summary(ROOT)
            gateway = search_gateway_settings_summary(ROOT)
            if search.get("status") != "configured":
                raise ValueError("该任务需要公开检索；请前往“设置 > 公开检索”查看服务状态")
            if gateway.get("status") not in {"disabled", "configured"}:
                raise ValueError("搜索聚合网关配置异常；请前往“设置 > 搜索聚合网关”修复或停用")
            if gateway.get("restart_required"):
                raise ValueError("搜索聚合网关配置已变更；请关闭并重新打开销售总监智能工作台后重试")
            if search.get("restart_required"):
                raise ValueError("公开检索密钥已保存，但智能核心尚未加载；请关闭并重新打开销售总监智能工作台后重试")
        if service_id in {"presentation-studio", "weekly-deck"}:
            validate_presentation_brief_request(request_text)
        runtime_selection = task_runtime_selection(payload)
        request_id = f"request-{uuid.uuid4().hex[:12]}"
        record = {"schema_version": "1.0", "request_id": request_id, "status": "requested", "profile_id": profile_id,
                  "service_id": service_id, "workflow_id": service["workflow"], "request": request_text,
                  "created_at": now(), "source": "local-workbench", "project_id": project_id,
                  **runtime_selection}
        atomic_json(REQUESTS / f"{request_id}.json", record)
        self.send_json(HTTPStatus.CREATED, record)

    def create_task_message(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        mode = str(payload.get("mode", ""))
        content = str(payload.get("content", "")).strip()
        if mode not in {"supplement", "redirect"}:
            raise ValueError("请选择补充信息或调整方向")
        if not content or len(content) > 1200:
            raise ValueError("任务消息必须为 1–1200 字")
        task_path = TASKS / f"{task_id}.json"
        if task_path.is_symlink() or not task_path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
            return
        task = load_json(task_path)
        if task.get("task_id") != task_id:
            self.send_json(HTTPStatus.CONFLICT, {"error": "任务记录与文件名不一致"})
            return
        if ACTIVE_PROFILE_ID is not None and task.get("profile_id") != ACTIVE_PROFILE_ID:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "该任务不属于当前销售总监版本"})
            return
        if task.get("status") in {"completed", "rejected", "cancelled", "failed"}:
            self.send_json(HTTPStatus.CONFLICT, {"error": "任务已经结束；请使用“再次创建”发起新任务"})
            return
        existing = task_message_records().get(task_id, [])
        if len(existing) >= 100:
            self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务消息已达到 100 条上限，请完成后新建任务"})
            return
        if TASK_MESSAGES.exists() and (TASK_MESSAGES.is_symlink() or not TASK_MESSAGES.is_dir()):
            raise ValueError("任务消息目录不安全")
        message_id = f"message-{uuid.uuid4().hex}"
        record = {
            "schema_version": "1.0", "message_id": message_id, "task_id": task_id,
            "profile_id": safe_id(str(task.get("profile_id", ""))), "mode": mode,
            "content": content, "status": "queued", "created_at": now(),
        }
        target = TASK_MESSAGES / f"{message_id}.json"
        if target.exists() or target.is_symlink():
            raise RuntimeError("任务消息 ID 冲突，请重试")
        atomic_json(target, record)
        self.send_json(HTTPStatus.ACCEPTED, {
            **record,
            "message": "方向调整已排队，将在当前工具调用结束后优先生效。" if mode == "redirect" else "补充信息已排队，将在下一处理步骤前加入任务。",
        })

    def delete_task(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        expected_version = payload.get("version")
        if not isinstance(expected_version, int):
            raise ValueError("缺少任务版本")
        if payload.get("confirmation") != "永久删除":
            raise ValueError("永久删除确认文字不正确")
        if TASKS.is_symlink() or not TASKS.is_dir():
            raise ValueError("任务目录不安全")
        task_path = TASKS / f"{task_id}.json"
        if task_path.is_symlink() or not task_path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在或已经删除"})
            return
        removed = 0
        try:
            with exclusive_task(task_path):
                task = load_json(task_path)
                if task.get("task_id") != task_id:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务记录与文件名不一致"})
                    return
                if ACTIVE_PROFILE_ID is not None and task.get("profile_id") != ACTIVE_PROFILE_ID:
                    self.send_json(HTTPStatus.FORBIDDEN, {"error": "该任务不属于当前销售总监版本"})
                    return
                if task.get("version") != expected_version:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务已更新，请刷新后重试", "task": task})
                    return
                if task.get("status") not in {"completed", "rejected", "cancelled", "failed"}:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只能彻底删除已经结束的历史任务"})
                    return
                if task.get("approval_request") is not None or task_id in live_agent_task_leases():
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务仍有处理请求或运行会话，暂不能删除"})
                    return
                pending_write = task.get("pending_write")
                if isinstance(pending_write, dict) and pending_write.get("status") == "committing":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务仍在提交写入，完成恢复后才能删除"})
                    return
                task_path.unlink()
                removed += 1
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return

        exact_records = [
            REQUESTS / f"{task_id}.json",
            PRESENTATION_PLANS / f"{task_id}.json",
            RUNTIME / "evidence" / f"{task_id}.json",
        ]
        for path in exact_records:
            if path.parent.is_dir() and not path.parent.is_symlink() and (path.is_symlink() or path.is_file()):
                path.unlink(missing_ok=True)
                removed += 1
        if TASK_EVENTS.is_dir() and not TASK_EVENTS.is_symlink():
            for path in list(TASK_EVENTS.glob(f"event-{task_id}-*.json"))[:4000]:
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
                    removed += 1
        if TASK_MESSAGES.is_dir() and not TASK_MESSAGES.is_symlink():
            for path in list(TASK_MESSAGES.glob("message-*.json"))[:5000]:
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    belongs_to_task = load_json(path).get("task_id") == task_id
                except (OSError, ValueError, json.JSONDecodeError):
                    belongs_to_task = False
                if belongs_to_task:
                    path.unlink(missing_ok=True)
                    removed += 1
        self.send_json(HTTPStatus.OK, {
            "task_id": task_id,
            "removed_records": removed,
            "message": "任务记录及其运行过程已彻底删除；已生成文件、知识库和销售台账未受影响。",
        })

    def revise_write_intent(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        expected_version = payload.get("version")
        operation = str(payload.get("operation", ""))
        record_id = str(payload.get("record_id", ""))
        intent_id = str(payload.get("intent_id", ""))
        payload_sha256 = str(payload.get("payload_sha256", ""))
        if not isinstance(expected_version, int):
            raise ValueError("缺少任务版本")
        if operation not in {"edit", "remove"}:
            raise ValueError("请选择编辑或删除卡片")
        if not record_id.strip() or len(record_id) > 128 or any(character in record_id for character in "\x00\r\n"):
            raise ValueError("待修改卡片编号无效")
        if re.fullmatch(r"[A-Fa-f0-9-]{16,64}", intent_id) is None or re.fullmatch(r"[a-f0-9]{64}", payload_sha256) is None:
            raise ValueError("缺少当前待写入内容的有效校验信息")
        task_path = TASKS / f"{task_id}.json"
        if task_path.is_symlink() or not task_path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
            return
        try:
            with exclusive_task(task_path):
                task = load_json(task_path)
                if task.get("task_id") != task_id:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务记录与文件名不一致"})
                    return
                if ACTIVE_PROFILE_ID is not None and task.get("profile_id") != ACTIVE_PROFILE_ID:
                    self.send_json(HTTPStatus.FORBIDDEN, {"error": "该任务不属于当前销售总监版本"})
                    return
                if task.get("version") != expected_version:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务已更新，请刷新后重试", "task": task})
                    return
                if task.get("status") != "waiting_approval" or task.get("approval_request") is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只有尚未处理的待写入内容可以修改"})
                    return
                pending = task.get("pending_write")
                if (
                    not isinstance(pending, dict) or pending.get("status") != "prepared"
                    or pending.get("intent_id") != intent_id or pending.get("payload_sha256") != payload_sha256
                ):
                    self.send_json(HTTPStatus.CONFLICT, {"error": "待写入内容已变化，请刷新后重试"})
                    return
                logical_tool = str(pending.get("logical_tool", ""))
                try:
                    current_payload = json.loads(str(pending.get("canonical_payload", "")))
                except json.JSONDecodeError as error:
                    raise ValueError("当前待写入内容不是有效结构化数据") from error
                if not isinstance(current_payload, dict):
                    raise ValueError("当前待写入内容结构无效")
                revised = revised_write_payload(
                    logical_tool, current_payload, operation, record_id, payload.get("changes")
                )
                requested_at = now()
                if revised is None:
                    task["approval_request"] = {
                        "decision": "reject", "requested_at": requested_at,
                        "requested_by": "local-workbench-write-card", "expected_version": expected_version,
                    }
                    message = "已删除最后一张卡片；本次没有内容需要写入，任务将安全结束。"
                else:
                    revised_canonical = canonical_plan_json(revised)
                    if revised_canonical == str(pending.get("canonical_payload", "")):
                        raise ValueError("卡片内容没有变化")
                    task["approval_request"] = {
                        "decision": "revise", "requested_at": requested_at,
                        "requested_by": "local-workbench-write-card", "expected_version": expected_version,
                        "intent_id": intent_id, "payload_sha256": payload_sha256,
                        "revised_payload": revised,
                    }
                    message = "修改请求已提交；旧校验码已作废，智能核心将生成新的待审批内容。"
                task["updated_at"] = requested_at
                task["version"] = expected_version + 1
                atomic_json(task_path, task)
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self.send_json(HTTPStatus.ACCEPTED, {
            "task_id": task_id, "version": task["version"], "message": message,
        })

    def decide(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        decision = str(payload.get("decision", ""))
        expected_version = payload.get("version")
        intent_id = payload.get("intent_id")
        payload_sha256 = payload.get("payload_sha256")
        if decision not in {"approve", "reject", "cancel", "resume"}:
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
                    self.send_json(HTTPStatus.CONFLICT, {"error": "已有操作正在等待智能核心处理，请稍后刷新"})
                    return
                if decision in {"approve", "reject"} and task.get("status") != "waiting_approval":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只有等待审批的任务可以批准或驳回"})
                    return
                pending_write = task.get("pending_write")
                if decision == "cancel":
                    display_status, _runtime_state = task_display_state(task)
                    if task.get("status") != "waiting_approval" and display_status != "interrupted":
                        self.send_json(HTTPStatus.CONFLICT, {"error": "只有等待审批或已中断的任务可以从工作台取消"})
                        return
                    if isinstance(pending_write, dict) and pending_write.get("status") == "committing":
                        self.send_json(HTTPStatus.CONFLICT, {"error": "任务正在提交写入，请先完成恢复，不能取消"})
                        return
                if decision == "resume":
                    display_status, _runtime_state = task_display_state(task)
                    if display_status != "interrupted":
                        self.send_json(HTTPStatus.CONFLICT, {"error": "只有已中断且没有其他处理请求的任务可以继续"})
                        return
                    if isinstance(pending_write, dict) and pending_write.get("status") == "committing":
                        self.send_json(HTTPStatus.CONFLICT, {"error": "任务正在提交写入，请先完成恢复，不能继续"})
                        return
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
        message = "正在从最后一个安全节点恢复；如有已批准但未提交的写入，将重新等待你确认。" if decision == "resume" else "操作已提交，等待智能核心确认。"
        self.send_json(HTTPStatus.ACCEPTED, {"task": task, "message": message})

    def restart_task(self, task_id: str, payload: dict[str, Any]) -> None:
        task_id = safe_id(task_id)
        expected_version = payload.get("version")
        if not isinstance(expected_version, int):
            raise ValueError("缺少任务版本")
        task_path = TASKS / f"{task_id}.json"
        if task_path.is_symlink() or not task_path.is_file():
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
            return
        request_id = f"request-restart-{uuid.uuid4().hex[:12]}"
        request_path = REQUESTS / f"{request_id}.json"
        try:
            with exclusive_task(task_path):
                task = load_json(task_path)
                if task.get("version") != expected_version:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务已更新，请刷新后重试", "task": task})
                    return
                display_status, runtime_state = task_display_state(task)
                historical = runtime_state == "historical"
                if display_status != "interrupted" and not historical:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "只有已中断或历史任务可以重新创建"})
                    return
                if task.get("approval_request") is not None:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "已有操作正在等待智能核心处理，请稍后刷新"})
                    return
                pending_write = task.get("pending_write")
                if isinstance(pending_write, dict) and pending_write.get("status") == "committing":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "任务正在提交写入，请先完成恢复，不能重新创建"})
                    return
                profile_id = safe_id(str(task.get("profile_id", "")))
                service_id = safe_id(str(task.get("service_id", "")))
                workflow_id = str(task.get("workflow_id", ""))
                project_id = safe_id(str(task.get("project_id") or DEFAULT_PROJECT_ID))
                request_text = str(task.get("request", "")).strip()
                profile = next((item for item in profiles() if item["id"] == profile_id), None)
                service = next((item for item in (profile or {}).get("services", []) if item.get("id") == service_id), None)
                if profile is None or service is None or service.get("workflow") != workflow_id:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "原任务对应的服务已不可用，不能重新创建"})
                    return
                active_project(project_id)
                if not request_text or len(request_text) > 4000:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "原任务说明无效，不能重新创建"})
                    return
                if service_id in {"presentation-studio", "weekly-deck"}:
                    validate_presentation_brief_request(request_text)
                record = {
                    "schema_version": "1.0", "request_id": request_id,
                    "status": "requested" if historical else "prepared",
                    "profile_id": profile_id, "service_id": service_id, "workflow_id": workflow_id,
                    "request": request_text, "created_at": now(), "source": "local-workbench",
                    "project_id": project_id, "request_kind": "task-restart",
                    "restart_of_task_id": task_id, "source_task_version": expected_version,
                    **_stored_runtime_selection(task),
                }
                atomic_json(request_path, record)
                if not historical:
                    task["approval_request"] = {
                        "decision": "cancel", "requested_at": now(),
                        "requested_by": f"local-workbench-restart:{request_id}",
                        "expected_version": expected_version,
                    }
                    task["superseded_by_task_id"] = request_id
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
                            "request_id": request_id,
                            "message": "重新开始请求已安全保存，将在下次刷新时继续发布。",
                        })
                        return
        except RuntimeError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self.send_json(HTTPStatus.CREATED, {
            "request_id": request_id,
            "message": "已复用原任务内容创建新任务；原中断任务将移入历史。" if not historical else "已复用历史任务内容创建新任务。",
        })

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
                        "message": "修订任务已从中断点恢复，等待智能核心接手。",
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
                    self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务没有可修订的演示方案"})
                    return
                if plan.get("profile_id") != task.get("profile_id") or plan.get("plan_sha256") != expected_plan_sha256:
                    self.send_json(HTTPStatus.CONFLICT, {"error": "演示方案已变化，请刷新后重试"})
                    return
                service_id = str(task.get("service_id", ""))
                profile_id = str(task.get("profile_id", ""))
                project_id = safe_id(str(task.get("project_id") or DEFAULT_PROJECT_ID))
                workflow_id = str(task.get("workflow_id", ""))
                if service_id != "presentation-studio" or workflow_id != "shared.presentation.studio":
                    self.send_json(HTTPStatus.CONFLICT, {"error": "当前任务不是可修订的演示文稿工作室任务"})
                    return
                profile = next((item for item in profiles() if item["id"] == profile_id), None)
                if profile is None or not any(service.get("id") == service_id for service in profile["services"]):
                    self.send_json(HTTPStatus.CONFLICT, {"error": "演示文稿工作室服务与当前角色不匹配"})
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
                    "project_id": project_id,
                    "request_kind": "presentation-plan-revision",
                    "revision_of_task_id": task_id, "source_plan_sha256": expected_plan_sha256,
                    "source_task_version": expected_version,
                    **_stored_runtime_selection(task),
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


def schedule_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        process_due_schedules()
        stop.wait(20)


def console_message(message: str, ascii_fallback: str) -> None:
    """Keep startup diagnostics usable on Windows consoles without CJK support."""
    try:
        print(message)
    except UnicodeEncodeError:
        print(ascii_fallback)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动仅本机可访问的销售总监工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile", default=os.environ.get("WORKFLOW_AGENT_EDITION_PROFILE", "sales-director"))
    parser.add_argument("--disable-scheduler", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    global ACTIVE_PROFILE_ID
    ACTIVE_PROFILE_ID = safe_id(args.profile)
    if not (PROFILES / ACTIVE_PROFILE_ID / "profile.json").is_file():
        parser.error(f"未知发行版角色：{ACTIVE_PROFILE_ID}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ControlHandler)
    schedule_stop = threading.Event()
    schedule_thread = None
    if not args.disable_scheduler:
        schedule_thread = threading.Thread(target=schedule_loop, args=(schedule_stop,), name="director-daily-scheduler", daemon=True)
        schedule_thread.start()
    console_message(
        f"销售总监工作台已启动：http://127.0.0.1:{args.port}",
        f"Agent4Market workbench started: http://127.0.0.1:{args.port}",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console_message("\n销售总监工作台已停止", "\nAgent4Market workbench stopped")
    finally:
        schedule_stop.set()
        if schedule_thread is not None:
            schedule_thread.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
