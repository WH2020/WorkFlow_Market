"""Read-only verifier for the Stage A SQLite driver gate."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
from pathlib import Path
from typing import Sequence


EXPECTED_TABLES = {
    "gate_records",
    "gate_schema_migrations",
    "gate_write_receipts",
}


def verify_gate_database(database: Path, expected_records: int, expected_receipts: int) -> dict[str, object]:
    if database.is_symlink():
        raise ValueError("database path must not be a symlink")
    resolved = database.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("database path must be a regular file")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"integrity_check failed: {integrity}")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        }
        missing = sorted(EXPECTED_TABLES - tables)
        if missing:
            raise ValueError(f"missing gate tables: {', '.join(missing)}")
        schema_version = int(
            connection.execute("SELECT max(version) FROM gate_schema_migrations").fetchone()[0]
        )
        records = int(connection.execute("SELECT count(*) FROM gate_records").fetchone()[0])
        receipts = int(connection.execute("SELECT count(*) FROM gate_write_receipts").fetchone()[0])
        if records != expected_records:
            raise ValueError(f"record count mismatch: expected={expected_records}, actual={records}")
        if receipts != expected_receipts:
            raise ValueError(f"receipt count mismatch: expected={expected_receipts}, actual={receipts}")
        return {
            "status": "ok",
            "database": str(resolved),
            "sqlite_version": sqlite3.sqlite_version,
            "schema_version": schema_version,
            "records": records,
            "receipts": receipts,
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "integrity_check": integrity,
        }
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a SQLite driver-gate database without modifying it")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--expect-records", type=int, required=True)
    parser.add_argument("--expect-receipts", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify_gate_database(args.database, args.expect_records, args.expect_receipts)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
