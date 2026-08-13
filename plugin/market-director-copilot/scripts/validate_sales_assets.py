from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path


REQUIRED_FIELDS = {
    "asset_id", "asset_type", "title", "scope", "customer_id", "audience_role",
    "sales_stage", "use_case", "owner", "status", "authorization_status",
    "deidentification_status", "version", "source_path", "evidence_refs",
    "last_validated_at", "next_review_at", "usage_feedback", "updated_at",
}
ALLOWED_SCOPE = {"generic", "customer-specific"}
ALLOWED_STATUS = {"draft", "internal-review", "active", "stale", "retired"}
ALLOWED_AUTHORIZATION = {"unknown", "pending", "approved", "not-required"}
ALLOWED_DEIDENTIFICATION = {"unknown", "pending", "passed", "not-applicable"}


def parse_date(value: str, field: str, row_number: int, errors: list[str]) -> date | None:
    if not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        errors.append(f"row {row_number}: {field} must start with an ISO date")
        return None


def validate(path: Path, as_of: date) -> list[str]:
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted(REQUIRED_FIELDS - fields)
            if missing:
                return [f"missing fields: {', '.join(missing)}"]
            seen: set[str] = set()
            for row_number, row in enumerate(reader, start=2):
                asset_id = (row.get("asset_id") or "").strip()
                if not asset_id:
                    errors.append(f"row {row_number}: asset_id is required")
                elif asset_id in seen:
                    errors.append(f"row {row_number}: duplicate asset_id {asset_id}")
                seen.add(asset_id)
                scope = (row.get("scope") or "").strip()
                status = (row.get("status") or "").strip()
                authorization = (row.get("authorization_status") or "").strip()
                deidentification = (row.get("deidentification_status") or "").strip()
                if scope not in ALLOWED_SCOPE:
                    errors.append(f"row {row_number}: invalid scope {scope!r}")
                if status not in ALLOWED_STATUS:
                    errors.append(f"row {row_number}: invalid status {status!r}")
                if authorization not in ALLOWED_AUTHORIZATION:
                    errors.append(f"row {row_number}: invalid authorization_status {authorization!r}")
                if deidentification not in ALLOWED_DEIDENTIFICATION:
                    errors.append(f"row {row_number}: invalid deidentification_status {deidentification!r}")
                customer_id = (row.get("customer_id") or "").strip()
                if scope == "customer-specific" and not customer_id:
                    errors.append(f"row {row_number}: customer-specific asset requires customer_id")
                last_validated = parse_date(row.get("last_validated_at") or "", "last_validated_at", row_number, errors)
                next_review = parse_date(row.get("next_review_at") or "", "next_review_at", row_number, errors)
                if status == "active":
                    for field in ("owner", "version", "source_path", "evidence_refs", "last_validated_at", "next_review_at"):
                        if not (row.get(field) or "").strip():
                            errors.append(f"row {row_number}: active asset requires {field}")
                    if authorization not in {"approved", "not-required"}:
                        errors.append(f"row {row_number}: active asset requires approved authorization")
                    if deidentification not in {"passed", "not-applicable"}:
                        errors.append(f"row {row_number}: active asset requires deidentification check")
                    if next_review and next_review < as_of:
                        errors.append(f"row {row_number}: active asset is past next_review_at; mark stale")
                    if last_validated and last_validated > as_of:
                        errors.append(f"row {row_number}: last_validated_at is in the future")
    except FileNotFoundError:
        errors.append(f"file not found: {path}")
    except UnicodeDecodeError as exc:
        errors.append(f"file must be UTF-8 CSV: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the local sales collateral asset register.")
    parser.add_argument("--file", type=Path, default=Path("data/sales/sales-assets.csv"))
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    errors = validate(args.file, args.as_of)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Sales asset register is valid: {args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
