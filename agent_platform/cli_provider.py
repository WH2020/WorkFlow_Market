"""Offline discovery and validation for supported coding-agent CLI backends.

This module never reads a CLI's authentication state and never starts a model
request.  Discovery is limited to ``--version`` and ``--help``.  The returned
command is always a native executable; Windows npm shims are resolved to their
fixed package entry point and launched through Node without a shell.
"""
from __future__ import annotations

import os
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .model_provider import ModelProviderError


CLI_APIS = {"claude-code", "codex-cli"}
CLI_BASE_URLS = {
    "claude-code": "https://api.anthropic.com",
    "codex-cli": "https://chatgpt.com/backend-api/codex",
}
CLI_COMMANDS = {"claude-code": "claude", "codex-cli": "codex"}
RUNNER_POLICY_VERSION = 1
CLI_POLICY_VERSIONS = {
    "claude-code": {"2.1.245"},
    "codex-cli": {"0.149.1"},
}
CLI_PACKAGE_ENTRIES = {
    "claude-code": Path("node_modules") / "@anthropic-ai" / "claude-code" / "cli.js",
    "codex-cli": Path("node_modules") / "@openai" / "codex" / "bin" / "codex.js",
}
CLI_NATIVE_PACKAGE_ENTRIES = {
    "claude-code": Path("node_modules") / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
}
CLI_REQUIRED_FLAGS = {
    "claude-code": (
        "--safe-mode",
        "--tools",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--permission-mode",
        "--json-schema",
    ),
    "codex-cli": (
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--output-schema",
        "--json",
    ),
}
PROBE_ENVIRONMENT_KEYS = {
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
}


def _normal_path(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def launch_sha256(executable_path: str, command: str, args: Sequence[str]) -> str:
    """Bind the configured launcher to both canonical paths and file bytes."""
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in (executable_path, command, *args):
        path = Path(raw)
        if not path.is_absolute() or not _is_regular_file(path):
            raise ModelProviderError("CLI 启动文件不存在或不是普通文件")
        normalized = _normal_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        digest = hashlib.sha256()
        try:
            with Path(normalized).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as error:
            raise ModelProviderError("无法核验 CLI 启动文件") from error
        records.append({"path": normalized, "sha256": digest.hexdigest()})
    payload = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_entries(
    command: str,
    environment: Mapping[str, str],
    which: Callable[..., str | None],
    *,
    system_name: str,
) -> list[Path]:
    candidates: list[Path] = []
    detected = which(command, path=environment.get("PATH"))
    if detected:
        candidates.append(Path(detected))
    if system_name == "nt":
        app_data = environment.get("APPDATA")
        if app_data:
            npm = Path(app_data) / "npm"
            candidates.extend(npm / f"{command}{suffix}" for suffix in (".cmd", ".ps1", ".exe"))
        profile = environment.get("USERPROFILE")
        if profile:
            candidates.append(Path(profile) / ".local" / "bin" / f"{command}.exe")
    else:
        profile = environment.get("HOME")
        if profile:
            candidates.append(Path(profile) / ".local" / "bin" / command)
        candidates.extend((Path("/usr/local/bin") / command, Path("/opt/homebrew/bin") / command))

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = _normal_path(candidate)
        except (OSError, RuntimeError):
            continue
        if normalized not in seen:
            result.append(Path(normalized))
            seen.add(normalized)
    return result


def _node_command(
    project_root: Path,
    environment: Mapping[str, str],
    which: Callable[..., str | None],
) -> Path | None:
    bundled = project_root / "runtime" / "node" / "node.exe"
    if _is_regular_file(bundled):
        return Path(_normal_path(bundled))
    detected = which("node", path=environment.get("PATH"))
    if not detected:
        return None
    path = Path(_normal_path(detected))
    return path if _is_regular_file(path) else None


def _launch_record(
    provider_type: str,
    entry: Path,
    project_root: Path,
    environment: Mapping[str, str],
    which: Callable[..., str | None],
    *,
    system_name: str,
) -> dict[str, Any] | None:
    if not _is_regular_file(entry):
        return None
    suffix = entry.suffix.casefold()
    if system_name == "nt" and suffix in {".cmd", ".ps1"}:
        native_relative = CLI_NATIVE_PACKAGE_ENTRIES.get(provider_type)
        native = entry.parent / native_relative if native_relative is not None else None
        if native is not None and _is_regular_file(native):
            return {
                "executable_path": _normal_path(entry),
                "command": _normal_path(native),
                "args": [],
            }
        script = entry.parent / CLI_PACKAGE_ENTRIES[provider_type]
        node = _node_command(project_root, environment, which)
        if not _is_regular_file(script) or node is None:
            return None
        return {
            "executable_path": _normal_path(entry),
            "command": _normal_path(node),
            "args": [_normal_path(script)],
        }
    if system_name == "nt" and suffix != ".exe":
        return None
    return {"executable_path": _normal_path(entry), "command": _normal_path(entry), "args": []}


def _run_probe(
    command: str,
    prefix: Sequence[str],
    option: str,
    *,
    environment: Mapping[str, str],
    runner: Callable[..., Any],
    working_directory: Path,
    subcommand: Sequence[str] = (),
    system_name: str,
) -> tuple[int, str]:
    clean_environment = {
        key: value for key, value in environment.items()
        if key.upper() in PROBE_ENVIRONMENT_KEYS
    }
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if system_name == "nt" else 0
    )
    completed = runner(
        [command, *prefix, *subcommand, option],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        env=clean_environment,
        cwd=str(working_directory),
        shell=False,
        creationflags=creation_flags,
    )
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
    return int(completed.returncode), output


def _version_is_supported(provider_type: str, version_output: str) -> bool:
    """Only approve versions whose isolation behavior was verified offline."""
    allowed = CLI_POLICY_VERSIONS.get(provider_type)
    if allowed is None:
        return True
    pattern = (
        r"\s*(\S+)\s+\(Claude Code\)\s*"
        if provider_type == "claude-code"
        else rf"\s*{re.escape(provider_type)}\s+(\S+)\s*"
    )
    match = re.fullmatch(pattern, version_output)
    return bool(match and match.group(1) in allowed)


def detect_coding_assistants(
    project_root: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[..., str | None] = shutil.which,
    system_name: str = os.name,
) -> dict[str, dict[str, Any]]:
    """Detect supported CLIs without reading credentials or making model calls."""
    root = Path(project_root or Path.cwd()).resolve()
    environment = dict(os.environ if environ is None else environ)
    result: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="agent4market-cli-probe-") as temporary:
        working_directory = Path(temporary)
        for provider_type, command_name in CLI_COMMANDS.items():
            attempts: list[dict[str, str]] = []
            selected: dict[str, Any] | None = None
            version = ""
            for entry in _candidate_entries(command_name, environment, which, system_name=system_name):
                launch = _launch_record(
                    provider_type, entry, root, environment, which, system_name=system_name
                )
                if launch is None:
                    continue
                try:
                    code, version_output = _run_probe(
                        launch["command"], launch["args"], "--version",
                        environment=environment, runner=runner, working_directory=working_directory,
                        system_name=system_name,
                    )
                    if code != 0:
                        attempts.append({"path": launch["executable_path"], "reason": f"version_exit_{code}"})
                        continue
                    if not _version_is_supported(provider_type, version_output):
                        attempts.append({"path": launch["executable_path"], "reason": "unsupported_policy_version"})
                        continue
                    code, help_output = _run_probe(
                        launch["command"], launch["args"], "--help",
                        environment=environment, runner=runner, working_directory=working_directory,
                        subcommand=("exec",) if provider_type == "codex-cli" else (),
                        system_name=system_name,
                    )
                    if code != 0:
                        attempts.append({"path": launch["executable_path"], "reason": f"help_exit_{code}"})
                        continue
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    attempts.append({"path": launch["executable_path"], "reason": type(error).__name__})
                    continue
                missing = [flag for flag in CLI_REQUIRED_FLAGS[provider_type] if flag not in help_output]
                if missing:
                    attempts.append({
                        "path": launch["executable_path"],
                        "reason": "missing_required_flags",
                        "missing_flags": ",".join(missing),
                    })
                    continue
                selected = launch
                version = version_output.replace("\r", " ").replace("\n", " ").strip()[:200]
                break
            if selected is None:
                reason = (
                    "unsupported_policy_version"
                    if any(attempt["reason"] == "unsupported_policy_version" for attempt in attempts)
                    else "not_found_or_missing_isolation_flags"
                )
                result[provider_type] = {
                    "available": False,
                    "backend_available": False,
                    "models": [],
                    "reason": reason,
                    "attempts": attempts[:8],
                }
            else:
                result[provider_type] = {
                    "available": True,
                    "backend_available": True,
                    "path": selected["executable_path"],
                    "executable_path": selected["executable_path"],
                    "command": selected["command"],
                    "args": selected["args"],
                    "version": version,
                    "launch_sha256": launch_sha256(
                        selected["executable_path"], selected["command"], selected["args"]
                    ),
                    "models": [],
                    "authentication": "cli-managed-unverified",
                    "runner_policy_version": RUNNER_POLICY_VERSION,
                }
    return result


def resolve_configured_backend(
    project_root: Path | str,
    provider_type: str,
    executable_path: str,
    *,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[..., str | None] = shutil.which,
    system_name: str = os.name,
) -> dict[str, Any]:
    if provider_type not in CLI_APIS:
        raise ModelProviderError("未知的 CLI 提供者类型")
    if not isinstance(executable_path, str) or not executable_path.strip():
        raise ModelProviderError("CLI 可执行文件路径无效")
    detected = detect_coding_assistants(
        project_root,
        environ=environ,
        runner=runner,
        which=which,
        system_name=system_name,
    )[provider_type]
    if not detected.get("available"):
        raise ModelProviderError("未检测到支持所需隔离参数的 CLI，配置未保存")
    try:
        requested = _normal_path(executable_path)
    except (OSError, RuntimeError) as error:
        raise ModelProviderError("CLI 可执行文件路径无效") from error
    if requested != detected["executable_path"]:
        raise ModelProviderError("只能配置本机当前检测到的 CLI 入口")
    return {
        "api": provider_type,
        "vendor": provider_type,
        "base_url": CLI_BASE_URLS[provider_type],
        "authentication": "cli-managed-unverified",
        "runner_policy_version": RUNNER_POLICY_VERSION,
        "executable_path": detected["executable_path"],
        "command": detected["command"],
        "args": list(detected["args"]),
        "version": detected["version"],
        "launch_sha256": detected["launch_sha256"],
    }


def backend_files_available(provider: Mapping[str, Any], *, verify_hash: bool = False) -> bool:
    """Validate launcher files without rerunning CLI discovery or reading auth state."""
    try:
        paths = [Path(str(provider["executable_path"])), Path(str(provider["command"]))]
        args = provider.get("args", [])
        if not isinstance(args, list) or len(args) > 1:
            return False
        paths.extend(Path(str(value)) for value in args)
        if not all(path.is_absolute() and _is_regular_file(path) for path in paths):
            return False
        return not verify_hash or launch_sha256(
            str(provider["executable_path"]), str(provider["command"]), [str(value) for value in args]
        ) == provider.get("launch_sha256")
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


def validate_backend_record(provider: Mapping[str, Any]) -> None:
    provider_type = provider.get("api")
    if provider_type not in CLI_APIS:
        raise ModelProviderError("CLI 提供者协议无效")
    if (
        provider.get("vendor") != provider_type
        or provider.get("base_url") != CLI_BASE_URLS[provider_type]
        or provider.get("authentication") != "cli-managed-unverified"
        or provider.get("runner_policy_version") != RUNNER_POLICY_VERSION
    ):
        raise ModelProviderError("CLI 提供者身份或接收方无效")
    executable = provider.get("executable_path")
    command = provider.get("command")
    args = provider.get("args")
    version = provider.get("version")
    launch_hash = provider.get("launch_sha256")
    if (
        not isinstance(executable, str)
        or not isinstance(command, str)
        or not Path(executable).is_absolute()
        or not Path(command).is_absolute()
        or not isinstance(args, list)
        or len(args) > 1
        or any(not isinstance(value, str) or not Path(value).is_absolute() for value in args)
        or (args and Path(args[0]).suffix.casefold() != ".js")
        or not isinstance(version, str)
        or not version
        or len(version) > 200
        or not isinstance(launch_hash, str)
        or len(launch_hash) != 64
        or any(character not in "0123456789abcdef" for character in launch_hash)
    ):
        raise ModelProviderError("CLI 启动入口无效")
