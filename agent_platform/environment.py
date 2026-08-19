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
PPT_PACKAGES = ("pptxgenjs", "@napi-rs/canvas", "jszip", "pdfjs-dist")


def platform_id(system_name: str | None = None) -> str:
    normalized = (system_name or platform.system()).strip().lower()
    return {"windows": "windows", "darwin": "macos"}.get(
        normalized, "linux" if normalized == "linux" else normalized
    )


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


def _executable(path: Path, os_id: str) -> bool:
    try:
        return path.is_file() and (os_id == "windows" or os.access(path, os.X_OK))
    except OSError:
        return False


def _env_value(environ: Mapping[str, str], name: str) -> str:
    direct = environ.get(name)
    if direct is not None:
        return direct
    lowered = name.lower()
    return next((value for key, value in environ.items() if key.lower() == lowered), "")


def _libreoffice_candidates(
    environ: Mapping[str, str], user_home: Path, os_id: str
) -> list[Path]:
    candidates: list[Path] = []
    for command in ("soffice", "libreoffice"):
        detected = shutil.which(command, path=environ.get("PATH"))
        if detected:
            candidates.append(Path(detected))
    if os_id == "windows":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = _env_value(environ, key).strip()
            if root:
                candidates.extend(
                    [
                        Path(root) / "LibreOffice" / "program" / "soffice.com",
                        Path(root) / "LibreOffice" / "program" / "soffice.exe",
                    ]
                )
    elif os_id == "macos":
        candidates.extend(
            [
                Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                user_home / "Applications" / "LibreOffice.app" / "Contents" / "MacOS" / "soffice",
                Path("/opt/homebrew/bin/soffice"),
                Path("/usr/local/bin/soffice"),
            ]
        )
    else:
        candidates.extend([Path("/usr/bin/soffice"), Path("/usr/local/bin/soffice")])
    return candidates


def discover_ppt_runtime(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    system_name: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    environment = dict(os.environ if environ is None else environ)
    user_home = (home or Path.home()).expanduser().resolve()
    os_id = platform_id(system_name)
    explicit = _env_value(environment, "WORKFLOW_LIBREOFFICE_PATH").strip()
    libreoffice: Path | None = None
    candidates = [Path(explicit)] if explicit else _libreoffice_candidates(environment, user_home, os_id)
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if _executable(resolved, os_id):
            libreoffice = resolved
            break

    required_files = {
        "builder": root / "pi" / "artifacts" / "build-director-deck.mjs",
        "qa": root / "pi" / "artifacts" / "validate-and-render-deck.mjs",
    }
    package_files = {
        package: root / "node_modules" / package / "package.json"
        for package in PPT_PACKAGES
    }
    missing = [f"file:{name}" for name, path in required_files.items() if not path.is_file()]
    missing.extend(f"package:{name}" for name, path in package_files.items() if not path.is_file())
    if libreoffice is None:
        missing.append("WORKFLOW_LIBREOFFICE_PATH")
    config = {
        **({"WORKFLOW_LIBREOFFICE_PATH": str(libreoffice)} if libreoffice else {}),
        "WORKFLOW_CJK_FONT": environment.get("WORKFLOW_CJK_FONT", "").strip()
        or {"windows": "Microsoft YaHei", "macos": "PingFang SC"}.get(os_id, "Noto Sans CJK SC"),
        "WORKFLOW_LATIN_FONT": environment.get("WORKFLOW_LATIN_FONT", "").strip() or "Arial",
    }
    return {
        "ready": not missing,
        "platform": os_id,
        "engine": "PptxGenJS + LibreOffice + PDF.js",
        "config": config,
        "missing": sorted(set(missing)),
        "source": "project_local_dependencies",
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
    completed = subprocess.run([str(pi_path), *arguments], cwd=root, env=environment, check=False)
    return completed.returncode, ppt_ready


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
