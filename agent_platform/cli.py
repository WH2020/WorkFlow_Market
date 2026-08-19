from __future__ import annotations

import argparse
import json
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
