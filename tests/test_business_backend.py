from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.business_backend import (
    BusinessBackendError,
    knowledge_entries,
    read_account_360,
    read_signals,
    resolve_business_backend,
    search_accounts,
)
from agent_platform.sales_store import CSV_SCHEMAS, MANIFEST_PATH, MIGRATIONS


class BusinessBackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "data" / "sales").mkdir(parents=True)
        (self.root / "data" / "knowledge").mkdir(parents=True)
        for name in ("customers.csv", "activities.csv", "resource-requests.csv", "sales-assets.csv"):
            self.write_csv(self.root / "data" / "sales" / name, CSV_SCHEMAS[name], [])
        self.write_csv(
            self.root / "data" / "knowledge" / "source-register.csv",
            CSV_SCHEMAS["source-register.csv"],
            [{"source_id": "source-001", "title": "政策原文", "url": "https://example.gov/policy", "status": "verified", "accessed_date": "2026-08-21"}],
        )
        self.write_csv(
            self.root / "data" / "sales" / "customers.csv",
            CSV_SCHEMAS["customers.csv"],
            [
                {"customer_id": "account-002", "customer_name": "华北具身中心", "region": "北京", "owner": "销售乙", "stage": "lead", "health": "good", "updated_at": "2026-08-20T10:00:00Z"},
                {"customer_id": "account-001", "customer_name": "华东脑机中心", "region": "江苏", "owner": "销售甲", "stage": "proposal", "health": "attention", "key_contact": "李主任", "risks": "预算待确认", "next_action": "安排演示", "next_action_due": "2026-08-28", "updated_at": "2026-08-21T10:00:00Z"},
            ],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def create_sqlite(self) -> Path:
        database = self.root / "data" / "agent4market.db"
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        migration = manifest["migrations"][0]
        script = (MIGRATIONS / migration["file"]).read_text(encoding="utf-8")
        connection = sqlite3.connect(database)
        connection.executescript(script)
        connection.execute(
            "INSERT INTO schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (1,?,?,?,?, 'applied')",
            (migration["name"], migration["sha256"], "2026-08-21T00:00:00Z", manifest["application_version"]),
        )
        connection.execute("INSERT INTO store_metadata(key,value,updated_at) VALUES ('schema_version','1','2026-08-21T00:00:00Z')")
        accounts = [
            ("account-001", "华东脑机中心", "华东脑机中心", "江苏", "脑机", "销售甲", "proposal", "attention", "预算路径", "重点客户", "project-a", 1, "2026-08-20T00:00:00Z", "2026-08-21T10:00:00Z"),
            ("account-002", "华北具身中心", "华北具身中心", "北京", "具身", "销售乙", "lead", "good", None, None, None, 1, "2026-08-19T00:00:00Z", "2026-08-20T10:00:00Z"),
        ]
        connection.executemany("""
            INSERT INTO accounts(account_id,name,normalized_name,region,sector,owner,lifecycle_stage,health,budget_path,summary,project_id,version,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, accounts)
        connection.execute("INSERT INTO risks(risk_id,account_id,risk_text,status,version,created_at,updated_at) VALUES ('risk-001','account-001','预算待确认','open',1,'2026-08-21T00:00:00Z','2026-08-21T00:00:00Z')")
        connection.execute("INSERT INTO actions(action_id,account_id,action_text,due_at,status,origin,version,created_at,updated_at) VALUES ('action-001','account-001','安排演示','2026-08-28','open','manual',1,'2026-08-21T00:00:00Z','2026-08-21T00:00:00Z')")
        connection.execute("INSERT INTO sources(source_id,title,url,status,accessed_date,version,created_at,updated_at) VALUES ('source-001','政策原文','https://example.gov/policy','verified','2026-08-21',1,'2026-08-21T00:00:00Z','2026-08-21T00:00:00Z')")
        connection.execute("INSERT INTO signals(signal_id,account_id,signal_type,subject_type,subject_id,rule_version,trigger_json,evidence_version_hash,fingerprint,severity,status,first_seen_at,last_seen_at,version,created_at,updated_at) VALUES ('signal-001','account-001','overdue_action','action','action-001','1','{}',?,?, 'high','open','2026-08-21T00:00:00Z','2026-08-21T01:00:00Z',1,'2026-08-21T00:00:00Z','2026-08-21T01:00:00Z')", ("a" * 64, "b" * 64))
        connection.commit()
        connection.close()
        pointer = {
            "backend": "sqlite", "schema_version": 1,
            "database_relative_path": "data/agent4market.db",
            "migration_batch_id": "fixture", "database_sha256_at_cutover": "0" * 64,
        }
        (self.root / "data" / "storage-backend.json").write_text(json.dumps(pointer), encoding="utf-8")
        return database

    def test_absent_pointer_selects_csv_and_supports_customer_360(self) -> None:
        backend = resolve_business_backend(self.root)
        self.assertEqual(backend.backend, "csv")
        result = search_accounts(self.root, query="脑机", limit=1)
        self.assertEqual(result["rows"][0]["account_id"], "account-001")
        detail = read_account_360(self.root, "account-001", sections=["contacts", "risks", "actions"])
        self.assertEqual(detail["account"]["name"], "华东脑机中心")
        self.assertEqual(detail["sections"]["contacts"][0]["display_name"], "李主任")
        self.assertEqual(detail["sections"]["risks"][0]["risk_text"], "预算待确认")
        self.assertEqual(read_signals(self.root)["rows"], [])

    def test_sqlite_pointer_supports_stable_queries_and_signals(self) -> None:
        self.create_sqlite()
        first = search_accounts(self.root, limit=1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["rows"][0]["account_id"], "account-001")
        second = search_accounts(self.root, limit=1, cursor=first["next_cursor"])
        self.assertEqual(second["rows"][0]["account_id"], "account-002")
        detail = read_account_360(self.root, "account-001", sections=["risks", "signals", "actions", "evidence_refs"])
        self.assertEqual(detail["sections"]["actions"][0]["action_id"], "action-001")
        self.assertEqual(detail["sections"]["risks"][0]["risk_id"], "risk-001")
        self.assertEqual(detail["sections"]["signals"][0]["signal_id"], "signal-001")
        with self.assertRaises(BusinessBackendError) as raised:
            read_account_360(self.root, "account-001", since="not-a-date")
        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        signals = read_signals(self.root, account_id="account-001", status="open")
        self.assertEqual(signals["rows"][0]["signal_id"], "signal-001")
        entries = knowledge_entries(self.root)
        self.assertEqual(entries["entries"][0]["source_id"], "source-001")

    def test_invalid_pointer_fails_closed_instead_of_falling_back_to_csv(self) -> None:
        pointer = self.root / "data" / "storage-backend.json"
        pointer.write_text('{"backend":"sqlite","schema_version":1,"database_relative_path":"../outside.db"}', encoding="utf-8")
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root)
        self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_query_and_cursor_are_parameterized_and_validated(self) -> None:
        self.create_sqlite()
        self.assertEqual(search_accounts(self.root, query="%' OR 1=1 --")["rows"], [])
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root, cursor="not-a-cursor")
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")
        first = search_accounts(self.root, query="中心", limit=1)
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root, query="脑机", cursor=first["next_cursor"], limit=1)
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")


if __name__ == "__main__":
    unittest.main()
