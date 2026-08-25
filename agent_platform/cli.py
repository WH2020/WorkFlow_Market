from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .coding_agents import (
    CodingAgentBridgeError,
    WorkbenchClient,
    coding_agent_doctor,
    sales_services,
)
from .core import ManifestError, Platform, WorkflowError
from .environment import (
    discover_ppt_runtime,
    doctor_report,
    json_text,
    launch_pi,
)
from .subagents import ensure_subagent_configuration
from .sales_store import (
    SalesStoreError,
    activate_sales_store,
    backup_sales_store,
    export_sales_store,
    import_sales_store_export,
    migrate_sales_store,
    restore_sales_store,
    rollback_sales_store_activation,
    verify_sales_store,
)


def configure_utf8_console() -> None:
    """Emit stable UTF-8 JSON and Chinese messages on Windows and macOS."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and inspect vertical agent bundles")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("configure-subagents", help="Install governed project and extension settings for pi-subagents")
    resolve = subparsers.add_parser("resolve-profile")
    resolve.add_argument("profile_id")
    services = subparsers.add_parser("list-services")
    services.add_argument("--profile")
    plan = subparsers.add_parser("plan-workflow")
    plan.add_argument("workflow_id")
    plan.add_argument("--profile")
    doctor = subparsers.add_parser("doctor", help="Check Windows/macOS toolchain readiness")
    doctor.add_argument("--require-ppt", action="store_true", help="Fail unless the independent PPT toolchain is complete")
    launch = subparsers.add_parser("launch", help="Start Pi with the validated project-local PPT toolchain")
    launch.add_argument("pi_args", nargs=argparse.REMAINDER)
    coding = subparsers.add_parser(
        "coding-agent",
        help="Use Codex CLI or Claude Code as a governed local entry point",
    )
    coding_commands = coding.add_subparsers(dest="coding_command", required=True)
    coding_commands.add_parser("doctor", help="Check both coding-agent hosts and the local workbench")
    coding_commands.add_parser("services", help="List Sales Director services available to coding agents")
    coding_commands.add_parser("projects", help="List active local project spaces")
    coding_tasks = coding_commands.add_parser("tasks", help="List governed tasks without raw write payloads")
    coding_tasks.add_argument("--include-history", action="store_true")
    coding_tasks.add_argument("--limit", type=int, default=20)
    coding_status = coding_commands.add_parser("status", help="Read one governed task")
    coding_status.add_argument("--task-id", required=True)
    coding_submit = coding_commands.add_parser("submit", help="Create a Sales Director task through the local workbench")
    coding_submit.add_argument("--service", required=True)
    coding_submit.add_argument("--project", default="project-default")
    request_source = coding_submit.add_mutually_exclusive_group()
    request_source.add_argument("--request", help="Task text; prefer --request-file or stdin for sensitive text")
    request_source.add_argument("--request-file", help="UTF-8 text file, or '-' for stdin")
    coding_submit.add_argument("--model", help="Configured Agent4Market model identifier")
    coding_submit.add_argument(
        "--thinking",
        choices=("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    )
    coding_message = coding_commands.add_parser("message", help="Supplement or redirect a running task")
    coding_message.add_argument("--task-id", required=True)
    coding_message.add_argument("--mode", choices=("supplement", "redirect"), required=True)
    message_source = coding_message.add_mutually_exclusive_group()
    message_source.add_argument("--content", help="Message text; prefer --content-file or stdin for sensitive text")
    message_source.add_argument("--content-file", help="UTF-8 text file, or '-' for stdin")
    coding_commands.add_parser("open", help="Open an already-running Sales Director workbench")
    migrate = subparsers.add_parser("migrate-sales-store", help="Preflight CSV data or build a new staging SQLite store")
    migrate.add_argument("--source", type=Path, default=Path("data/sales"), help="Controlled sales CSV directory")
    migrate.add_argument("--knowledge", type=Path, default=Path("data/knowledge/source-register.csv"))
    migrate.add_argument("--database", type=Path, help="New staging database path; required with --staging")
    migrate.add_argument("--report", type=Path, help="Optional no-overwrite JSON report path")
    migrate_mode = migrate.add_mutually_exclusive_group(required=True)
    migrate_mode.add_argument("--dry-run", action="store_true", help="Read and validate inputs without creating a database")
    migrate_mode.add_argument("--staging", action="store_true", help="Create and verify a new staging database")
    verify = subparsers.add_parser("verify-sales-store", help="Open a schema v1 store read-only and verify integrity")
    verify.add_argument("--database", type=Path, required=True)
    backup = subparsers.add_parser("backup-sales-store", help="Create a validated no-overwrite SQLite backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--target", type=Path, required=True)
    restore = subparsers.add_parser("restore-sales-store", help="Restore a backup into a new path without overwriting")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    export = subparsers.add_parser("export-sales-store", help="Export core tables as formula-safe CSV files")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--target-dir", type=Path, required=True)
    import_export = subparsers.add_parser("import-sales-store-export", help="Rebuild a new schema v1 store from a verified core-table export")
    import_export.add_argument("--source-dir", type=Path, required=True)
    import_export.add_argument("--target-database", type=Path, required=True)
    activate = subparsers.add_parser("activate-sales-store", help="Atomically switch the storage pointer after exact approval")
    activate.add_argument("--database", type=Path, required=True)
    activate.add_argument("--report", type=Path, required=True)
    activate.add_argument("--approval", type=Path, required=True)
    activate.add_argument(
        "--expected-pointer-sha256", required=True,
        help="Current pointer SHA-256, or the literal 'absent' when no pointer exists",
    )
    rollback = subparsers.add_parser("rollback-sales-store", help="Restore the exact previous pointer when no post-cutover business write exists")
    rollback.add_argument("--batch-id", required=True)
    rollback.add_argument("--expected-current-pointer-sha256", required=True)
    return parser


def _bounded_text(
    direct: str | None,
    source: str | None,
    *,
    label: str,
    maximum: int,
) -> str:
    if direct is not None:
        value = direct
    elif source == "-" or (source is None and not sys.stdin.isatty()):
        value = sys.stdin.read(maximum + 2)
    elif source:
        path = Path(source).expanduser()
        if path.is_symlink() or not path.is_file():
            raise CodingAgentBridgeError("INVALID_INPUT", f"{label}文件不存在或不是普通文件")
        if path.stat().st_size > 64 * 1024:
            raise CodingAgentBridgeError("INVALID_INPUT", f"{label}文件超过 64 KB")
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise CodingAgentBridgeError("INVALID_INPUT", f"{label}文件必须使用 UTF-8 编码") from error
    else:
        raise CodingAgentBridgeError("INVALID_INPUT", f"请通过标准输入或文件提供{label}")
    value = value.strip()
    if not value or len(value) > maximum:
        raise CodingAgentBridgeError("INVALID_INPUT", f"{label}必须为 1–{maximum} 字")
    return value


def _run_coding_agent_command(args: argparse.Namespace) -> int:
    try:
        if args.coding_command == "doctor":
            result = coding_agent_doctor(args.root)
        elif args.coding_command == "services":
            result = {"status": "ok", "profile_id": "sales-director", "services": sales_services(args.root)}
        else:
            client = WorkbenchClient()
            if args.coding_command == "projects":
                result = {"status": "ok", "projects": client.projects()}
            elif args.coding_command == "tasks":
                result = {
                    "status": "ok",
                    "tasks": client.tasks(include_history=args.include_history, limit=args.limit),
                }
            elif args.coding_command == "status":
                result = {"status": "ok", "task": client.task(args.task_id)}
            elif args.coding_command == "submit":
                request_text = _bounded_text(
                    args.request, args.request_file, label="任务说明", maximum=4000,
                )
                result = {
                    "status": "ok",
                    "task": client.submit_task(
                        service_id=args.service,
                        project_id=args.project,
                        request_text=request_text,
                        requested_model=args.model,
                        thinking_level=args.thinking,
                    ),
                }
            elif args.coding_command == "message":
                content = _bounded_text(
                    args.content, args.content_file, label="任务消息", maximum=1200,
                )
                result = {
                    "status": "ok",
                    "message": client.send_message(
                        task_id=args.task_id,
                        mode=args.mode,
                        content=content,
                    ),
                }
            else:
                result = client.open_workbench()
    except (CodingAgentBridgeError, ManifestError, WorkflowError, OSError) as error:
        code = error.code if isinstance(error, CodingAgentBridgeError) else "LOCAL_ERROR"
        print(json_text({"status": "error", "code": code, "error": str(error)}), file=sys.stderr)
        return 3
    print(json_text(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_utf8_console()
    args = build_parser().parse_args(argv)
    if args.command == "coding-agent":
        return _run_coding_agent_command(args)
    if args.command == "doctor":
        result = doctor_report(args.root)
        print(json_text(result))
        if not result["core"]["ready"] or (args.require_ppt and not result["ppt"]["ready"]):
            return 3
        return 0
    if args.command == "configure-subagents":
        try:
            result = ensure_subagent_configuration(args.root)
        except (OSError, RuntimeError) as error:
            print(json_text({"status": "error", "error": str(error)}), file=sys.stderr)
            return 3
        print(json_text({"status": "ok", **result}))
        return 0
    if args.command == "launch":
        runtime = discover_ppt_runtime(args.root)
        if not runtime["ready"]:
            print(
                "Warning: PPT runtime is incomplete; Pi will start with non-PPT services. "
                "Run the platform setup script, then 'python -m agent_platform doctor --require-ppt'.",
                file=sys.stderr,
            )
        try:
            pi_args = list(args.pi_args)
            if pi_args[:1] == ["--"]:
                pi_args = pi_args[1:]
            return_code, _ppt_ready = launch_pi(args.root, pi_args)
            return return_code
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print(json_text({"status": "error", "error": str(error)}), file=sys.stderr)
            return 3
    if args.command in {
        "migrate-sales-store", "verify-sales-store", "backup-sales-store", "restore-sales-store",
        "export-sales-store", "import-sales-store-export", "activate-sales-store", "rollback-sales-store",
    }:
        try:
            if args.command == "migrate-sales-store":
                result = migrate_sales_store(
                    args.root,
                    sales_dir=args.source,
                    knowledge_file=args.knowledge,
                    database_path=args.database,
                    report_path=args.report,
                    dry_run=bool(args.dry_run),
                )
            elif args.command == "verify-sales-store":
                result = verify_sales_store(
                    args.database if args.database.is_absolute() else args.root / args.database
                )
            elif args.command == "backup-sales-store":
                result = backup_sales_store(args.root, args.database, args.target)
            elif args.command == "restore-sales-store":
                result = restore_sales_store(args.root, args.backup, args.target)
            elif args.command == "export-sales-store":
                result = export_sales_store(args.root, args.database, args.target_dir)
            elif args.command == "import-sales-store-export":
                result = import_sales_store_export(args.root, args.source_dir, args.target_database)
            elif args.command == "activate-sales-store":
                expected = None if args.expected_pointer_sha256 == "absent" else args.expected_pointer_sha256
                if expected is not None and not re.fullmatch(r"[a-f0-9]{64}", expected):
                    raise SalesStoreError(
                        "INVALID_INPUT", "--expected-pointer-sha256 必须是小写 SHA-256 或 absent"
                    )
                result = activate_sales_store(
                    args.root,
                    database_path=args.database,
                    report_path=args.report,
                    approval_path=args.approval,
                    expected_pointer_sha256=expected,
                )
            else:
                if not re.fullmatch(r"[a-f0-9]{64}", args.expected_current_pointer_sha256):
                    raise SalesStoreError(
                        "INVALID_INPUT", "--expected-current-pointer-sha256 必须是小写 SHA-256"
                    )
                result = rollback_sales_store_activation(
                    args.root,
                    batch_id=args.batch_id,
                    expected_current_pointer_sha256=args.expected_current_pointer_sha256,
                )
        except (OSError, SalesStoreError, sqlite3.Error) as error:
            code = error.code if isinstance(error, SalesStoreError) else "IO_OR_SQLITE_ERROR"
            print(json_text({"status": "error", "code": code, "error": str(error)}), file=sys.stderr)
            return 3
        print(json_text({"status": "ok", **result}))
        return 0
    platform = Platform(args.root)
    try:
        report = platform.validate_all()
        if args.command == "validate":
            result = report.as_dict()
        elif args.command == "resolve-profile":
            result = platform.resolve_profile(args.profile_id)
        elif args.command == "plan-workflow":
            result = platform.plan_workflow(args.workflow_id, args.profile)
        else:
            result = {"status": "ok", "services": platform.list_services(args.profile)}
    except (ManifestError, WorkflowError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
