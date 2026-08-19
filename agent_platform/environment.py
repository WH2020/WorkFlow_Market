from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core import ManifestError, Platform, WorkflowError


MIN_NODE = (22, 19, 0)
MIN_PNPM = (9, 0, 0)
MIN_PI = (0, 84, 2)
PPT_PATH_KEYS = (
    "WORKFLOW_ARTIFACT_NODE",
    "WORKFLOW_ARTIFACT_TOOL_PATH",
    "WORKFLOW_PRESENTATIONS_MARKER",
    "WORKFLOW_ARTIFACT_PYTHON",
    "WORKFLOW_SLIDES_TEST",
    "RUNTIME_NODE",
    "RUNTIME_NODE_MODULES",
    "RUNTIME_BIN_DIR",
)


def platform_id(system_name: str | None = None) -> str:
    normalized = (system_name or platform.system()).strip().lower()
    return {"windows": "windows", "darwin": "macos"}.get(normalized, "linux" if normalized == "linux" else normalized)


def _version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _command_path(project_root: Path, name: str, environ: Mapping[str, str]) -> Path | None:
    path = shutil.which(name, path=environ.get("PATH"))
    if path:
        return Path(path).resolve()
    suffixes = (".cmd", ".exe", "") if platform_id() == "windows" else ("",)
    for suffix in suffixes:
        candidate = project_root / "node_modules" / ".bin" / f"{name}{suffix}"
        if candidate.is_file():
            return candidate.resolve()
    return None


def _command_check(
    project_root: Path,
    name: str,
    minimum: tuple[int, int, int] | None,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    path = _command_path(project_root, name, environ)
    if path is None:
        return {"name": name, "ok": False, "reason": "not_found"}
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=dict(environ),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"name": name, "ok": False, "path": str(path), "reason": f"cannot_run:{type(error).__name__}"}
    output = (completed.stdout or completed.stderr).strip()[:200]
    detected = _version(output)
    ok = completed.returncode == 0 and (minimum is None or (detected is not None and detected >= minimum))
    result: dict[str, Any] = {
        "name": name,
        "ok": ok,
        "path": str(path),
        "version": ".".join(str(part) for part in detected) if detected else None,
    }
    if completed.returncode != 0:
        result["reason"] = f"exit_{completed.returncode}"
    elif minimum is not None and (detected is None or detected < minimum):
        result["reason"] = f"requires_{'.'.join(str(part) for part in minimum)}"
    return result


def _existing(candidates: Sequence[Path], *, directory: bool = False, executable: bool = False, os_id: str) -> Path | None:
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
            valid = resolved.is_dir() if directory else resolved.is_file()
            if executable and os_id != "windows":
                valid = valid and os.access(resolved, os.X_OK)
            if valid:
                return resolved
        except OSError:
            continue
    return None


def _explicit_or_detected(
    key: str,
    environ: Mapping[str, str],
    candidates: Sequence[Path],
    *,
    directory: bool = False,
    executable: bool = False,
    os_id: str,
) -> Path | None:
    explicit = environ.get(key, "").strip()
    if explicit:
        return _existing([Path(explicit)], directory=directory, executable=executable, os_id=os_id)
    return _existing(candidates, directory=directory, executable=executable, os_id=os_id)


def discover_ppt_runtime(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    system_name: str | None = None,
) -> dict[str, Any]:
    environment = dict(os.environ if environ is None else environ)
    user_home = (home or Path.home()).expanduser().resolve()
    os_id = platform_id(system_name)
    cache_roots = [user_home / ".cache"]
    xdg_cache = environment.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        cache_roots.insert(0, Path(xdg_cache).expanduser())
    if os_id == "macos":
        cache_roots.append(user_home / "Library" / "Caches")
    dependencies_candidates = tuple(
        cache_root / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
        for cache_root in dict.fromkeys(cache_roots)
    )
    node_candidates = tuple(
        candidate
        for dependencies in dependencies_candidates
        for candidate in (
            dependencies / "node" / "bin" / ("node.exe" if os_id == "windows" else "node"),
            dependencies / "node" / ("node.exe" if os_id == "windows" else "bin/node"),
        )
    )
    python_candidates = tuple(
        candidate
        for dependencies in dependencies_candidates
        for candidate in (
            dependencies / "python" / ("python.exe" if os_id == "windows" else "bin/python3"),
            dependencies / "python" / ("python.exe" if os_id == "windows" else "bin/python"),
        )
    )
    node_modules_candidates = tuple(
        candidate
        for dependencies in dependencies_candidates
        for candidate in (
            dependencies / "node" / "node_modules",
            dependencies / "node_modules",
        )
    )
    bin_candidates = tuple(
        candidate
        for dependencies in dependencies_candidates
        for candidate in (
            dependencies / "bin" / "override",
            dependencies / "bin",
        )
    )
    node_modules = _explicit_or_detected(
        "RUNTIME_NODE_MODULES", environment, node_modules_candidates, directory=True, os_id=os_id
    )
    artifact_candidates = tuple(
        candidate / "@oai" / "artifact-tool" for candidate in node_modules_candidates
    )
    codex_home = Path(environment.get("CODEX_HOME", "").strip() or user_home / ".codex").expanduser()
    container_tool_directories = sorted(
        codex_home.glob(
            "plugins/cache/openai-primary-runtime/presentations/*/skills/presentations/container_tools"
        ),
        key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parents[2].name)),
        reverse=True,
    )
    marker_candidates = tuple(path / "mark_artifact_operation_started.mjs" for path in container_tool_directories)
    slides_test_candidates = tuple(path / "slides_test.py" for path in container_tool_directories)

    artifact_node = _explicit_or_detected(
        "WORKFLOW_ARTIFACT_NODE", environment, node_candidates, executable=True, os_id=os_id
    )
    runtime_node = _explicit_or_detected(
        "RUNTIME_NODE", environment, node_candidates, executable=True, os_id=os_id
    )
    artifact_python = _explicit_or_detected(
        "WORKFLOW_ARTIFACT_PYTHON", environment, python_candidates, executable=True, os_id=os_id
    )
    artifact_tool = _explicit_or_detected(
        "WORKFLOW_ARTIFACT_TOOL_PATH", environment, artifact_candidates, directory=True, os_id=os_id
    )
    marker = _explicit_or_detected(
        "WORKFLOW_PRESENTATIONS_MARKER", environment, marker_candidates, os_id=os_id
    )
    slides_test = _explicit_or_detected(
        "WORKFLOW_SLIDES_TEST", environment, slides_test_candidates, os_id=os_id
    )
    runtime_bin = _explicit_or_detected(
        "RUNTIME_BIN_DIR", environment, bin_candidates, directory=True, os_id=os_id
    )
    config = {
        "WORKFLOW_ARTIFACT_NODE": artifact_node,
        "WORKFLOW_ARTIFACT_TOOL_PATH": artifact_tool,
        "WORKFLOW_PRESENTATIONS_MARKER": marker,
        "WORKFLOW_ARTIFACT_PYTHON": artifact_python,
        "WORKFLOW_SLIDES_TEST": slides_test,
        "RUNTIME_NODE": runtime_node,
        "RUNTIME_NODE_MODULES": node_modules,
        "RUNTIME_BIN_DIR": runtime_bin,
    }
    missing = [key for key in PPT_PATH_KEYS if config[key] is None]
    if artifact_tool is not None and not (artifact_tool / "dist" / "artifact_tool.mjs").is_file():
        missing.append("WORKFLOW_ARTIFACT_TOOL_PATH/dist/artifact_tool.mjs")
    if slides_test is not None and not (slides_test.parent / "create_montage.py").is_file():
        missing.append("WORKFLOW_SLIDES_TEST/create_montage.py")
    serializable = {key: str(value) for key, value in config.items() if value is not None}
    serializable["WORKFLOW_CJK_FONT"] = environment.get("WORKFLOW_CJK_FONT", "").strip() or {
        "windows": "Microsoft YaHei",
        "macos": "PingFang SC",
    }.get(os_id, "Noto Sans CJK SC")
    serializable["WORKFLOW_LATIN_FONT"] = environment.get("WORKFLOW_LATIN_FONT", "").strip() or "Arial"
    return {
        "ready": not missing,
        "platform": os_id,
        "config": serializable,
        "missing": sorted(set(missing)),
        "source": "explicit_environment_or_codex_runtime",
    }


def doctor_report(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    system_name: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    environment = dict(os.environ if environ is None else environ)
    os_id = platform_id(system_name)
    platform_check = {
        "name": "platform",
        "ok": os_id in {"windows", "macos"},
        "value": os_id,
        **({} if os_id in {"windows", "macos"} else {"reason": "supported_platforms_are_windows_and_macos"}),
    }
    python_check = {
        "name": "python",
        "ok": sys.version_info >= (3, 11),
        "path": str(Path(sys.executable).resolve()),
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        **({} if sys.version_info >= (3, 11) else {"reason": "requires_3.11.0"}),
    }
    checks = [
        platform_check,
        python_check,
        _command_check(root, "git", None, environment),
        _command_check(root, "node", MIN_NODE, environment),
        _command_check(root, "pnpm", MIN_PNPM, environment),
        _command_check(root, "pi", MIN_PI, environment),
    ]
    try:
        validation = Platform(root).validate_all().as_dict()
        project_check = {"name": "project", "ok": True, "validation": validation}
    except (ManifestError, WorkflowError) as error:
        project_check = {"name": "project", "ok": False, "reason": str(error)}
    checks.append(project_check)
    core_ready = all(check["ok"] for check in checks)
    ppt = discover_ppt_runtime(root, environ=environment, home=home, system_name=system_name)
    local_data = all(
        (root / relative).is_file()
        for relative in (
            "data/knowledge/source-register.csv",
            "data/sales/customers.csv",
            "data/sales/activities.csv",
            "data/sales/resource-requests.csv",
            "data/sales/sales-assets.csv",
        )
    )
    status = "ok" if core_ready and ppt["ready"] else "warning" if core_ready else "error"
    return {
        "status": status,
        "platform": os_id,
        "core": {"ready": core_ready, "checks": checks},
        "ppt": ppt,
        "optional": {
            "brave_search_configured": bool(environment.get("BRAVE_SEARCH_API_KEY", "").strip()),
            "local_data_initialized": local_data,
        },
    }


def launch_pi(
    project_root: Path | str,
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, bool]:
    root = Path(project_root).resolve()
    environment = dict(os.environ if environ is None else environ)
    report = doctor_report(root, environ=environment)
    if not report["core"]["ready"]:
        raise RuntimeError("Core environment check failed; run agent_platform doctor for details")
    pi_path = _command_path(root, "pi", environment)
    if pi_path is None:
        raise RuntimeError("Pi executable was not found")
    ppt_ready = bool(report["ppt"]["ready"])
    if ppt_ready:
        environment.update(report["ppt"]["config"])
    completed = subprocess.run(
        [str(pi_path), *arguments],
        cwd=root,
        env=environment,
        check=False,
    )
    return completed.returncode, ppt_ready


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
