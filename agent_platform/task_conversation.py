"""Read existing Pi assistant text for one governed task; never copy raw sessions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MAX_SESSION = 64 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024
MAX_PAGE = 2 * 1024 * 1024


def read(root: Path, sessions: Path, task: dict, *, after: str = "", leaf: str | None = None) -> dict:
    key = task.get("session_key")
    empty = {"messages": [], "has_more": False, "next_after": "", "reset": False,
             "source_id": hashlib.sha256(str(key or "").encode()).hexdigest(),
             "note": "只显示当前绑定会话已完成轮次的可见回复；不包含隐藏推理、工具原文或其他任务。"}
    if not isinstance(key, str) or not key or key.startswith("unsaved-"):
        return {**empty, "status": "pending", "note": "尚无已保存的模型回复；本轮结束后显示原文。"}
    path = Path(key)
    # Client cannot supply paths. Reject junctions/symlinks along the complete
    # session ancestry, even if they currently point back inside the root.
    if not path.is_absolute() or path.suffix != ".jsonl":
        return {**empty, "status": "unavailable", "note": "会话路径不受支持，未读取其他文件。"}
    path, sessions = Path(os.path.abspath(path)), Path(os.path.abspath(sessions))
    if not path.is_relative_to(sessions):
        return {**empty, "status": "unavailable", "note": "会话不在当前智能核心的会话目录，未扩大读取范围。"}
    entries = {}
    try:
        for parent in (path, *path.parents):
            if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400):
                return {**empty, "status": "unavailable", "note": "会话路径含重定向，未读取内容。"}
        if not path.resolve().is_relative_to(sessions.resolve()):
            return {**empty, "status": "unavailable", "note": "会话不在当前智能核心的会话目录，未扩大读取范围。"}
        if not path.is_file():
            return {**empty, "status": "pending", "note": "模型尚未保存首轮回复，完成后会自动显示。"}
        if path.stat().st_size > MAX_SESSION:
            return {**empty, "status": "limited", "note": "原生会话超过 64 MiB 读取上限；未将部分内容宣称为全文。"}
        with path.open("rb") as stream:
            header = json.loads(stream.readline(MAX_LINE + 1))
            if not isinstance(header, dict) or header.get("type") != "session" or Path(header.get("cwd", "")).resolve() != root.resolve():
                raise ValueError("wrong session")
            total = 0
            while line := stream.readline(MAX_LINE + 1):
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_SESSION or len(entries) >= 100000:
                    return {**empty, "status": "limited", "note": "会话读取达到安全上限，未返回截断回复。"}
                if not line.endswith(b"\n"):
                    break  # Writer may still be appending this entry.
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError("invalid entry")
                identity = entry.get("id")
                if not isinstance(identity, str) or identity in entries:
                    raise ValueError("invalid entry")
                item = {"parent": entry.get("parentId"), "id": identity}
                if item["parent"] is not None and not isinstance(item["parent"], str):
                    raise ValueError("invalid parent")
                if entry.get("type") == "custom" and entry.get("customType") == "director-task-state":
                    state = entry.get("data", {})
                    if not isinstance(state, dict):
                        raise ValueError("invalid state")
                    item["scope"] = all(state.get(k) == task.get(k) for k in ("task_id", "profile_id", "workflow_id", "session_key")) and type(state.get("version")) is int and state["version"] <= task.get("version", -1)
                    item["terminal"] = state.get("status") in {"completed", "failed", "rejected", "cancelled"}
                elif entry.get("type") == "message":
                    message = entry.get("message", {})
                    if not isinstance(message, dict):
                        raise ValueError("invalid message")
                    item["role"] = message.get("role")
                    if message.get("role") == "assistant":
                        blocks = message.get("content", [])
                        item["text"] = "\n".join(b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)) if isinstance(blocks, list) else ""
                        item["at"] = entry.get("timestamp", "")
                entries[identity] = item
    except (OSError, ValueError, TypeError, KeyError):
        return {**empty, "status": "unavailable", "note": "会话尚不可完整读取，稍后刷新重试；没有读取其他任务。"}
    if leaf is not None and leaf not in entries:
        return {**empty, "status": "pending", "note": "等待当前会话分支完成保存。"}
    if leaf is None:
        # Physical append order does not identify Pi's selected branch. Offline
        # and terminal tasks may use a unique branch only, never guess a fork.
        leaves = set(entries) - {item["parent"] for item in entries.values()}
        if entries and len(leaves) != 1:
            return {**empty, "status": "unavailable", "note": "会话存在多个分支或分支异常，暂时无法确认当前分支；未混入其他分支的回复。"}
        leaf = next(iter(leaves), None)
    branch, visited, identity = [], set(), leaf
    while identity is not None:
        if identity in visited or identity not in entries:
            return {**empty, "status": "unavailable", "note": "会话分支不完整，未混用其他分支内容。"}
        visited.add(identity); item = entries[identity]; branch.append(item); identity = item["parent"]
    messages, scoped, terminal = [], False, False
    for item in reversed(branch):
        if "scope" in item:
            scoped, terminal = item["scope"], item["terminal"]
        elif scoped and item.get("role") == "assistant":
            if item.get("text"):
                messages.append({"id": item["id"], "text": item["text"], "at": item["at"]})
                if terminal:
                    scoped = False  # Include final answer, not subsequent unrelated conversation.
        elif terminal and item.get("role") == "user":
            scoped = False
    start = next((i + 1 for i, item in enumerate(messages) if item["id"] == after), None) if after else 0
    reset = start is None
    start = start or 0
    page, size = [], 0
    for item in messages[start:start + 12]:
        length = len(item["text"].encode("utf-8"))
        if length > MAX_PAGE:
            return {**empty, "status": "limited", "note": "单轮回复超过 2 MiB，未返回截断内容。"}
        if size + length > MAX_PAGE:
            break
        page.append(item); size += length
    return {**empty, "status": "ready", "messages": page, "reset": reset,
            "has_more": start + len(page) < len(messages), "next_after": page[-1]["id"] if page else after,
            "total": len(messages)}
