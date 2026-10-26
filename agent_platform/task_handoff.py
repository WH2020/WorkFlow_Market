"""User-reviewed conversation requirements carried by existing governed tasks."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

SERVICES = {"sales-review", "industry-research", "pdf-import", "presentation-studio", "weekly-deck"}


def base_service(service):
    return str(service).removesuffix("-readonly").removesuffix("-quick")


def validate(value, *, service_id, entity_id, project_id, profile_id, tasks: Path):
    def text(item, limit, empty=True):
        return (isinstance(item, str) and len(item) <= limit and (empty or bool(item.strip()))
                and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", item))

    keys = {"schema", "plan_id", "steps", "step", "scope_id", "brief", "previous_result", "previous"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("任务需求草稿格式无效")
    if (type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["plan_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["plan_id"])
            or not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 3
            or type(value["step"]) is not int or not 0 <= value["step"] < len(value["steps"])
            or not text(value["scope_id"], 128) or value["scope_id"] != entity_id
            or not text(value["brief"], 6000) or not text(value["previous_result"], 6000)):
        raise ValueError("任务需求超出范围，请核对草稿、步骤与关联档案")
    for step in value["steps"]:
        if (not isinstance(step, dict) or set(step) != {"service_id", "request"}
                or not isinstance(step["service_id"], str) or step["service_id"] not in SERVICES
                or not text(step["request"], 1000, False)):
            raise ValueError("连续计划仅支持客户复盘、行业研究、资料解读、PPT 和销售简报")
    if value["steps"][value["step"]]["service_id"] != base_service(service_id):
        raise ValueError("当前服务与计划步骤不一致，请重新核对")
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 32 * 1024:
        raise ValueError("任务需求草稿超过 32 KiB，请精简后继续；未自动截断")
    if value["step"] == 0:
        if value["previous"] is not None or value["previous_result"]:
            raise ValueError("首个步骤不能引用其他任务结果")
    else:
        previous = value["previous"]
        if (not isinstance(previous, dict) or set(previous) != {"task_id", "version"}
                or not isinstance(previous["task_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", previous["task_id"])
                or type(previous["version"]) is not int or previous["version"] < 1 or not value["previous_result"].strip()):
            raise ValueError("请先核对上一项任务的结果，再准备后续步骤")
        path = tasks / (previous["task_id"] + ".json")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("上一项任务不可用")
        source = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(source, dict) or not isinstance(source.get("handoff"), dict):
            raise ValueError("上一项任务没有可继续的计划")
        source_plan = source["handoff"]
        if (source.get("task_id") != previous["task_id"] or source.get("status") != "completed"
                or source.get("version") != previous["version"] or source.get("profile_id") != profile_id
                or source.get("project_id") != project_id or source.get("superseded_by_task_id")
                or source_plan.get("plan_id") != value["plan_id"] or source_plan.get("step") != value["step"] - 1
                or source_plan.get("scope_id") != entity_id or source_plan.get("steps") != value["steps"]):
            raise ValueError("上一项任务未完成、已变化或不属于同一计划和资料范围")
    return copy.deepcopy(value)
