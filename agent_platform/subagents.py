from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .model_provider import pi_agent_dir


SUBAGENT_PACKAGE_VERSION = "0.51.0"

PROJECT_SUBAGENT_SETTINGS: dict[str, Any] = {
    "projectRootResolution": "git-root",
    "disableBuiltins": True,
    "defaultExtensions": [],
}

GOVERNED_EXTENSION_CONFIG: dict[str, Any] = {
    "toolDescriptionMode": "compact",
    "inlineToolDisplay": "summary",
    "asyncByDefault": False,
    "defaultSubagentContext": "fresh",
    "fleetView": False,
    "asyncWidget": False,
    "waitTool": {"enabled": False},
    "forceTopLevelAsync": False,
    "timeoutMs": 600_000,
    "toolTimeoutMs": 120_000,
    "globalConcurrencyLimit": 2,
    "maxSubagentSpawnsPerSession": 4,
    "maxSubagentSpawnsPerRun": 2,
    "maxActiveAsyncRunsPerSession": 1,
    "maxSubagentDepth": 1,
    "parallel": {"maxTasks": 2, "concurrency": 2},
    "scheduledRuns": {"enabled": False},
    "missions": {"enabled": False, "globalIndex": False, "retainTerminal": 20},
    "authorityPolicy": {
        "discardWorktree": "forbid",
        "destructiveCleanup": "forbid",
        "spawnBudgetGrant": "forbid",
        "scheduleCreate": "forbid",
        "stopRun": "auto",
        "steerRun": "auto",
    },
    "artifactDir": "session",
    "intercomBridge": {"mode": "off", "resultDelivery": False},
}

SUBAGENT_ENVIRONMENT: dict[str, str] = {
    "PI_SUBAGENT_TASK_DELIVERY": "file",
    "PI_SUBAGENT_WAIT_TOOL_ENABLED": "false",
    "PI_SUBAGENT_MAX_SPAWNS_PER_SESSION": "4",
    "PI_SUBAGENT_MAX_SPAWNS_PER_RUN": "2",
    "PI_SUBAGENT_MAX_DEPTH": "1",
    "PI_SUBAGENT_FS_RETRY_MAX_TOTAL_MS": "1000",
}


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 Subagent 配置 {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Subagent 配置必须是 JSON 对象：{path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def project_settings_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "settings.json"


def extension_config_path(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    return (
        pi_agent_dir(environ=environ, home=home)
        / "extensions"
        / "subagent"
        / "config.json"
    )


def subagent_environment() -> dict[str, str]:
    return dict(SUBAGENT_ENVIRONMENT)


def _validate_bundle(root: Path) -> None:
    package_path = root / "node_modules" / "pi-subagents" / "package.json"
    package = _read_object(package_path)
    if package.get("name") != "pi-subagents" or package.get("version") != SUBAGENT_PACKAGE_VERSION:
        raise RuntimeError(
            f"需要项目内 pi-subagents {SUBAGENT_PACKAGE_VERSION}，请重新安装冻结依赖"
        )
    required = (
        root / "node_modules" / "pi-subagents" / "index.ts",
        root / "pi" / "extensions" / "subagent-readonly.ts",
        root / "pi" / "subagents" / "agents" / "director-research-scout.md",
        root / "pi" / "subagents" / "agents" / "director-readonly-reviewer.md",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"受管 Subagent 组件缺失：{', '.join(missing)}")


def ensure_subagent_configuration(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, str]:
    root = Path(project_root).resolve()
    _validate_bundle(root)

    settings_path = project_settings_path(root)
    settings = _read_object(settings_path)
    packages = settings.get("packages")
    if packages is None:
        packages = []
    if not isinstance(packages, list) or any(not isinstance(item, (str, dict)) for item in packages):
        raise RuntimeError("项目 .pi/settings.json 的 packages 配置无效")
    if ".." not in packages:
        packages.insert(0, "..")
    settings["packages"] = packages
    current_subagents = settings.get("subagents")
    if current_subagents is None:
        current_subagents = {}
    if not isinstance(current_subagents, dict):
        raise RuntimeError("项目 .pi/settings.json 的 subagents 配置必须是对象")
    current_subagents.update(PROJECT_SUBAGENT_SETTINGS)
    settings["subagents"] = current_subagents
    _atomic_json(settings_path, settings)

    config_path = extension_config_path(environ=environ, home=home)
    config = _read_object(config_path)
    config.update(GOVERNED_EXTENSION_CONFIG)
    _atomic_json(config_path, config)
    return {
        "project_settings": str(settings_path),
        "extension_config": str(config_path),
    }


def subagent_doctor_check(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    try:
        _validate_bundle(root)
        settings = _read_object(project_settings_path(root))
        subagents = settings.get("subagents")
        if not isinstance(subagents, dict):
            raise RuntimeError("项目 Subagent 设置缺失")
        for key, expected in PROJECT_SUBAGENT_SETTINGS.items():
            if subagents.get(key) != expected:
                raise RuntimeError(f"项目 Subagent 设置 {key} 未受控")
        config = _read_object(extension_config_path(environ=environ, home=home))
        for key, expected in GOVERNED_EXTENSION_CONFIG.items():
            if config.get(key) != expected:
                raise RuntimeError(f"Subagent 扩展设置 {key} 未受控")
        return {
            "name": "subagents",
            "ok": True,
            "version": SUBAGENT_PACKAGE_VERSION,
            "roles": ["director-research-scout", "director-readonly-reviewer"],
            "mode": "governed_read_only",
        }
    except (OSError, RuntimeError) as error:
        return {"name": "subagents", "ok": False, "reason": str(error)}

