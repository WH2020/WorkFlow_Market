from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.sqlite_gate import verify_gate_database


class SqliteGateVerifierTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        database = Path(temporary.name) / "gate.db"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE gate_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                ) STRICT;
                CREATE TABLE gate_records (
                    record_id TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                ) STRICT;
                CREATE TABLE gate_write_receipts (
                    intent_id TEXT PRIMARY KEY,
                    payload_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    committed_at TEXT NOT NULL
                ) STRICT;
                INSERT INTO gate_schema_migrations VALUES (1, 'fixture', '2026-08-21T00:00:00.000Z');
                INSERT INTO gate_records VALUES ('record-1', 'value', 1, '2026-08-21T00:00:00.000Z');
                INSERT INTO gate_write_receipts VALUES (
                    'intent-1',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    '{}',
                    '2026-08-21T00:00:00.000Z'
                );
                """
            )
            connection.commit()
        finally:
            connection.close()
        return temporary, database

    def test_reads_a_valid_gate_database_without_modifying_it(self) -> None:
        temporary, database = self._fixture()
        try:
            before = database.stat().st_mtime_ns
            result = verify_gate_database(database, expected_records=1, expected_receipts=1)
            self.assertEqual("ok", result["status"])
            self.assertEqual(1, result["schema_version"])
            self.assertEqual("ok", result["integrity_check"])
            self.assertEqual(before, database.stat().st_mtime_ns)
        finally:
            temporary.cleanup()

    def test_rejects_a_count_mismatch(self) -> None:
        temporary, database = self._fixture()
        try:
            with self.assertRaisesRegex(ValueError, "record count mismatch"):
                verify_gate_database(database, expected_records=2, expected_receipts=1)
        finally:
            temporary.cleanup()

    def test_missing_database_is_never_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.db"
            with self.assertRaises(FileNotFoundError):
                verify_gate_database(database, expected_records=0, expected_receipts=0)
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
