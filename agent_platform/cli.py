from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import ManifestError, Platform, WorkflowError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and inspect vertical agent bundles")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    resolve = subparsers.add_parser("resolve-profile")
    resolve.add_argument("profile_id")
    services = subparsers.add_parser("list-services")
    services.add_argument("--profile")
    plan = subparsers.add_parser("plan-workflow")
    plan.add_argument("workflow_id")
    plan.add_argument("--profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
