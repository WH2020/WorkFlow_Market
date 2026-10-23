"""Read-only task health: process liveness is not proof of model progress."""
from __future__ import annotations

from datetime import datetime, timezone


def _time(value, reference):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or (parsed - reference).total_seconds() > 5:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def bound_lease(task, lease):
    return lease if isinstance(lease, dict) and all(lease.get(k) == task.get(k)
        for k in ("task_id", "profile_id", "session_key")) else None


def activity_for(task, lease, reference=None):
    reference = reference or datetime.now(timezone.utc)
    lease = bound_lease(task, lease)
    activity = lease.get("activity") if lease else None
    if (not isinstance(activity, dict) or activity.get("task_id") != task.get("task_id")
            or activity.get("phase") not in {"starting", "model", "tool", "retry_wait", "settled", "error"}
            or not _time(activity.get("last_activity_at"), reference)
            or not _time(activity.get("phase_started_at"), reference)):
        return {}
    return activity


def summary(task, lease, events, display_status, reference=None):
    reference = reference or datetime.now(timezone.utc)
    lease = bound_lease(task, lease)
    heartbeat = _time(lease.get("heartbeat_at"), reference) if lease else None
    online = bool(heartbeat and (reference - heartbeat).total_seconds() <= 15)
    activity = activity_for(task, lease, reference) if online else {}
    timestamps = [a.get("at") for a in task.get("audit", []) if isinstance(a, dict)
                  and a.get("action") in {"task_started", "node_completed", "tool_completed", "task_completed", "write_commit_started"}]
    timestamps += [e.get("created_at") for e in events if e.get("profile_id") == task.get("profile_id")]
    recorded = [_time(t, reference) for t in timestamps]
    last_progress = max((t for t in recorded if t), default=None)
    last_activity = _time(activity.get("last_activity_at"), reference) or last_progress
    started = _time(task.get("created_at"), reference)
    elapsed = max(0, int((reference - started).total_seconds())) if started else None
    quiet = max(0, int((reference - last_activity).total_seconds())) if last_activity else None
    phase_age = _time(activity.get("phase_started_at"), reference)
    phase_seconds = max(0, int((reference - phase_age).total_seconds())) if phase_age else None
    state, title, detail, tone = display_status, "状态待确认", "尚未收到足够的执行状态。", "warning"
    if display_status in {"completed", "cancelled", "rejected", "superseded", "failed"}:
        title = {"completed": "任务已完成", "cancelled": "任务已结束", "rejected": "任务已驳回", "superseded": "已由新任务替代", "failed": "任务处理失败"}[display_status]
        tone = "success" if display_status == "completed" else "error" if display_status == "failed" else "neutral"
        detail = "可以查看模型回复和已生成结果。" if display_status == "completed" else "本次处理已停止；需要重做时使用任务卡中的重新创建。"
    elif display_status in {"waiting_approval", "approval_pending", "approval_stalled"}:
        title = {"waiting_approval": "等待你确认", "approval_pending": "审批已提交，等待接手", "approval_stalled": "审批已保存，接手超时"}[display_status]
        detail = "请处理下方审批；发送消息不会替代批准或驳回。"
    elif display_status in {"resuming", "cancelling", "restarting"}:
        title, detail = "操作已提交，等待核心确认", "尚未确认操作完成，请勿重复提交。"
    elif display_status == "requested":
        title, detail = "任务已排队，尚未开始", "等待智能核心接手；此时没有模型输出。"
    elif online and (lease.get("model_error") or activity.get("phase") == "error"):
        state, title, tone = "error", "本轮处理出错，已停止", "error"
        detail = {"MODEL_ABORTED": "模型请求已中止。", "TOOL_ERROR": "工具执行失败，本轮已结束。"}.get(activity.get("error_code"), "模型调用或模型配置出现异常。") + "检查模型设置后，可在下方继续任务；不会自动换模型或重复执行。"
    elif online and activity.get("phase") == "settled":
        state, title, tone = "settled", "本轮回复已结束，任务尚未完成", "warning"
        detail = "核心在线但当前没有继续执行。请查看模型回复，再选择继续任务或补充信息。"
    elif not online or display_status == "interrupted":
        state, title, detail = "disconnected", "未收到任务心跳，执行状态未知", "不能确认任务仍在运行，也不能据此断言失败。可刷新状态、检查智能核心，或使用下方恢复操作。"
    else:
        phase = activity.get("phase")
        state, tone = phase or "unknown", "running"
        title = {"starting": "核心已接手，准备处理", "model": "模型正在生成回复", "tool": "工具正在执行", "retry_wait": "本轮模型异常，等待重试结果"}.get(phase, "核心在线，尚无详细执行反馈")
        detail = "心跳只表示核心在线；下方显示实际活动和阶段记录。"
        if phase == "retry_wait":
            tone, detail = "warning", "尚未收到最终结束状态，可能正在自动重试；不要重复启动任务。"
        batch = activity.get("batch")
        if phase == "tool" and activity.get("tool") == "wechat":
            title = "正在读取和整理所选微信会话"
            if isinstance(batch, dict) and batch.get("phase") in {"batch", "merge"} and all(
                    type(batch.get(k)) is int and 0 <= batch[k] <= 1_000_000 for k in ("current", "total", "messages", "level")) and 1 <= batch["current"] <= batch["total"] <= 128:
                title = (f"正在整理第 {batch['current']} / {batch['total']} 批微信消息" if batch["phase"] == "batch"
                         else f"正在汇总摘要：第 {batch['level']} 层，第 {batch['current']} / {batch['total']} 组")
                detail = f"所选范围共 {batch['messages']} 条消息。当前批次仍在处理，不表示这一批已经完成。"
        if quiet is not None and quiet >= 120:
            tone = "warning"
            detail += " 已超过 2 分钟没有新的执行反馈；核心仍在线，不等于任务失败。" if quiet < 600 else " 已超过 10 分钟没有新的执行反馈，建议检查核心与模型连接；不会自动重试。"
    return {"state": state, "title": title, "detail": detail, "tone": tone, "online": online,
            "observed_at": reference.isoformat(), "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
            "last_activity_at": last_activity.isoformat() if last_activity else None,
            "last_progress_at": last_progress.isoformat() if last_progress else None,
            "elapsed_seconds": elapsed, "quiet_seconds": quiet, "phase_seconds": phase_seconds}
