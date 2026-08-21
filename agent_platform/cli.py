from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
