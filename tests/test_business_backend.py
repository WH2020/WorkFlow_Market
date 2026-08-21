from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_platform.business_backend import (
    BusinessBackendError,
    knowledge_entries,
    read_account_360,
    read_account_timeline,
    read_signals,
    read_today_focus,
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
        self.write_csv(
            self.root / "data" / "sales" / "activities.csv",
            CSV_SCHEMAS["activities.csv"],
            [{"activity_id": "activity-001", "customer_id": "account-001", "occurred_at": "2026-07-01T09:00:00Z", "channel": "现场", "activity_type": "拜访", "summary": "完成需求沟通", "commitment": "客户补充预算材料", "created_at": "2026-07-01T09:00:00Z"}],
        )
        self.write_csv(
            self.root / "data" / "sales" / "resource-requests.csv",
            CSV_SCHEMAS["resource-requests.csv"],
            [{"request_id": "request-001", "customer_id": "account-001", "requested_at": "2026-08-18T09:00:00Z", "request_summary": "申请演示工程师", "deadline": "2026-08-23", "status": "open"}],
        )
        self.write_csv(
            self.root / "data" / "sales" / "sales-assets.csv",
            CSV_SCHEMAS["sales-assets.csv"],
            [{"asset_id": "asset-001", "asset_type": "deck", "title": "客户方案", "scope": "customer-specific", "customer_id": "account-001", "status": "active", "source_path": "outputs/customer-deck.pptx", "updated_at": "2026-08-19T08:00:00Z"}],
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
        connection.execute("INSERT INTO actions(action_id,account_id,action_text,due_at,status,origin,version,created_at,updated_at) VALUES ('action-002','account-001','确认预算','2026-08-20','open','manual',1,'2026-08-19T00:00:00Z','2026-08-19T00:00:00Z')")
        connection.execute("INSERT INTO activities(activity_id,account_id,occurred_at,channel,activity_type,summary,evidence_status,version,created_at,updated_at) VALUES ('activity-001','account-001','2026-08-18T09:00:00Z','现场','拜访','完成需求沟通','verified',1,'2026-08-18T09:00:00Z','2026-08-18T09:00:00Z')")
        connection.execute("INSERT INTO commitments(commitment_id,account_id,source_activity_id,direction,commitment_text,due_at,status,version,created_at,updated_at) VALUES ('commitment-001','account-001','activity-001','customer_to_us','补充预算材料','2026-08-24','open',1,'2026-08-18T09:00:00Z','2026-08-18T09:00:00Z')")
        connection.execute("INSERT INTO resource_requests(request_id,account_id,requested_at,request_summary,deadline,status,version,created_at,updated_at) VALUES ('request-001','account-001','2026-08-18T09:00:00Z','申请演示工程师','2026-08-23','open',1,'2026-08-18T09:00:00Z','2026-08-18T09:00:00Z')")
        connection.execute("INSERT INTO sales_assets(asset_id,asset_type,title,scope,account_id,status,source_path,source_status,version,created_at,updated_at) VALUES ('asset-001','deck','客户方案','customer-specific','account-001','active','outputs/customer-deck.pptx','verified',1,'2026-08-19T08:00:00Z','2026-08-19T08:00:00Z')")
        connection.execute("INSERT INTO task_links(task_link_id,task_id,account_id,relation_type,version,created_at,updated_at) VALUES ('link-001','task-001','account-001','supports',1,'2026-08-19T09:00:00Z','2026-08-19T09:00:00Z')")
        connection.execute("INSERT INTO artifacts(artifact_id,relative_path,artifact_type,sha256,task_id,account_id,status,version,created_at,updated_at) VALUES ('artifact-001','outputs/proposal.pptx','pptx',?,'task-001','account-001','ready',1,'2026-08-20T09:00:00Z','2026-08-20T09:00:00Z')", ("c" * 64,))
        connection.execute("INSERT INTO artifacts(artifact_id,relative_path,artifact_type,sha256,task_id,account_id,status,version,created_at,updated_at) VALUES ('artifact-002','outputs/brief.docx','docx',?,'task-001','account-001','ready',1,'2026-08-19T09:00:00Z','2026-08-19T09:00:00Z')", ("f" * 64,))
        receipt = {"intent_id": "intent-001", "task_id": "task-001", "logical_tool": "sales.write", "payload_sha256": "d" * 64, "approved_payload_sha256": "e" * 64, "mutations": [{"table": "actions", "record_id": "action-002", "operation": "insert", "version": 1}], "committed_at": "2026-08-20T10:00:00Z"}
        connection.execute("INSERT INTO write_receipts(intent_id,task_id,session_id,logical_tool,payload_sha256,status,result_json,committed_at) VALUES ('intent-001','task-001','session-001','sales.write',?,'committed',?,'2026-08-20T10:00:00Z')", ("d" * 64, json.dumps(receipt)))
        linked_receipt = {"intent_id": "intent-002", "task_id": "task-001", "logical_tool": "sales.write", "payload_sha256": "1" * 64, "approved_payload_sha256": "2" * 64, "mutations": [{"table": "accounts", "record_id": "account-002", "operation": "update", "version": 2}], "committed_at": "2026-08-20T11:00:00Z"}
        connection.execute("INSERT INTO write_receipts(intent_id,task_id,session_id,logical_tool,payload_sha256,status,result_json,committed_at) VALUES ('intent-002','task-001','session-002','sales.write',?,'committed',?,'2026-08-20T11:00:00Z')", ("1" * 64, json.dumps(linked_receipt)))
        unapproved_receipt = {"intent_id": "intent-003", "task_id": "task-001", "logical_tool": "sales.write", "payload_sha256": "3" * 64, "mutations": [{"table": "accounts", "record_id": "account-002", "operation": "update", "version": 3}], "committed_at": "2026-08-20T12:00:00Z"}
        connection.execute("INSERT INTO write_receipts(intent_id,task_id,session_id,logical_tool,payload_sha256,status,result_json,committed_at) VALUES ('intent-003','task-001','session-003','sales.write',?,'committed',?,'2026-08-20T12:00:00Z')", ("3" * 64, json.dumps(unapproved_receipt)))
        connection.execute("INSERT INTO sources(source_id,title,url,publisher,status,accessed_date,version,created_at,updated_at) VALUES ('source-001','政策原文','https://example.gov/policy','示例省政府','verified','2026-08-21',1,'2026-08-21T00:00:00Z','2026-08-21T00:00:00Z')")
        connection.execute("INSERT INTO evidence_refs(evidence_ref_id,entity_type,entity_id,field_name,source_id,locator_json,claim_kind,verification_status,note,version,created_at,updated_at) VALUES ('evidence-001','accounts','account-001','budget_path','source-001','{\"section\":\"申报条件\"}','fact','verified','预算路径来自政策原文',1,'2026-08-21T00:00:00Z','2026-08-21T00:00:00Z')")
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
        database = self.create_sqlite()
        first = search_accounts(self.root, limit=1)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["rows"][0]["account_id"], "account-001")
        second = search_accounts(self.root, limit=1, cursor=first["next_cursor"])
        self.assertEqual(second["rows"][0]["account_id"], "account-002")
        detail = read_account_360(self.root, "account-001", sections=["risks", "signals", "actions", "evidence_refs"])
        self.assertEqual(detail["sections"]["actions"][0]["action_id"], "action-001")
        self.assertEqual(detail["sections"]["risks"][0]["risk_id"], "risk-001")
        self.assertEqual(detail["sections"]["signals"][0]["signal_id"], "signal-001")
        self.assertEqual(detail["sections"]["evidence_refs"][0]["source_title"], "政策原文")
        self.assertEqual(detail["sections"]["evidence_refs"][0]["source_url"], "https://example.gov/policy")
        self.assertEqual(detail["sections"]["evidence_refs"][0]["source_publisher"], "示例省政府")
        with self.assertRaises(BusinessBackendError) as raised:
            read_account_360(self.root, "account-001", since="not-a-date")
        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        signals = read_signals(self.root, account_id="account-001", status="open")
        self.assertEqual(signals["rows"][0]["signal_id"], "signal-001")
        entries = knowledge_entries(self.root)
        self.assertEqual(entries["entries"][0]["source_id"], "source-001")
        connection = sqlite3.connect(database)
        connection.execute("UPDATE sources SET deleted_at='2026-08-21T12:00:00Z' WHERE source_id='source-001'")
        connection.commit()
        connection.close()
        deleted_source_detail = read_account_360(self.root, "account-001", sections=["evidence_refs"])
        self.assertEqual(len(deleted_source_detail["sections"]["evidence_refs"]), 1)
        self.assertIsNone(deleted_source_detail["sections"]["evidence_refs"][0]["source_title"])
        self.assertIsNone(deleted_source_detail["sections"]["evidence_refs"][0]["source_url"])

    def test_invalid_pointer_fails_closed_instead_of_falling_back_to_csv(self) -> None:
        pointer = self.root / "data" / "storage-backend.json"
        pointer.write_text('{"backend":"sqlite","schema_version":1,"database_relative_path":"../outside.db"}', encoding="utf-8")
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root)
        self.assertEqual(raised.exception.code, "UNSAFE_PATH")

    def test_csv_read_models_fail_closed_when_a_complete_scan_is_impossible(self) -> None:
        with patch("agent_platform.business_backend.MAX_BUSINESS_CSV_SCAN_ROWS", 1):
            for operation in (
                lambda: search_accounts(self.root),
                lambda: read_account_360(self.root, "account-001", sections=["contacts"]),
                lambda: read_today_focus(self.root, now=datetime(2026, 8, 21, tzinfo=timezone.utc)),
            ):
                with self.assertRaises(BusinessBackendError) as raised:
                    operation()
                self.assertEqual(raised.exception.code, "SCAN_LIMIT")

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

    def test_updated_since_is_validated_filtered_and_bound_to_cursor(self) -> None:
        csv_result = search_accounts(self.root, updated_since="2026-08-21T00:00:00Z")
        self.assertEqual([row["account_id"] for row in csv_result["rows"]], ["account-001"])
        self.create_sqlite()
        sqlite_result = search_accounts(self.root, updated_since="2026-08-21T00:00:00Z")
        self.assertEqual([row["account_id"] for row in sqlite_result["rows"]], ["account-001"])
        first = search_accounts(self.root, query="中心", updated_since="2026-08-19", limit=1)
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root, query="中心", updated_since="2026-08-20", cursor=first["next_cursor"], limit=1)
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")
        with self.assertRaises(BusinessBackendError) as raised:
            search_accounts(self.root, updated_since="not-a-date")
        self.assertEqual(raised.exception.code, "INVALID_INPUT")

    def test_csv_timeline_and_attention_only_project_existing_records(self) -> None:
        timeline = read_account_timeline(self.root, "account-001", limit=20)
        self.assertEqual({row["kind"] for row in timeline["rows"]}, {"activity", "commitment", "sales_asset"})
        self.assertEqual([row["evidence_id"] for row in timeline["rows"] if row["kind"] == "sales_asset"], ["asset-001"])
        self.assertTrue(all(row["evidence_id"] for row in timeline["rows"]))
        attention = read_today_focus(self.root, limit=20, now=datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.assertEqual({row["kind"] for row in attention["rows"]}, {"resource_deadline"})
        self.assertEqual(attention["rows"][0]["evidence_id"], "request-001")
        with patch("agent_platform.business_backend.MAX_TIMELINE_CSV_SCAN_ROWS", 1):
            truncated = read_account_timeline(self.root, "account-002", kinds=["task_link"], limit=20)
        self.assertTrue(truncated["truncated"])

    def test_sqlite_timeline_is_stable_paginated_and_attention_is_evidence_backed(self) -> None:
        self.create_sqlite()
        first = read_account_timeline(self.root, "account-001", limit=2)
        self.assertTrue(first["has_more"])
        second = read_account_timeline(self.root, "account-001", limit=20, cursor=first["next_cursor"])
        combined = first["rows"] + second["rows"]
        self.assertEqual(len({row["timeline_id"] for row in combined}), len(combined))
        self.assertIn("write_receipt", {row["kind"] for row in combined})
        receipt_ids = {row["evidence_id"] for row in combined if row["kind"] == "write_receipt"}
        self.assertEqual(receipt_ids, {"intent-001", "intent-002"})
        self.assertIn("artifact", {row["kind"] for row in combined})
        self.assertIn("sales_asset", {row["kind"] for row in combined})
        sales_assets = read_account_timeline(self.root, "account-001", kinds=["sales_asset"])
        self.assertEqual([row["evidence_id"] for row in sales_assets["rows"]], ["asset-001"])
        filtered = read_account_timeline(self.root, "account-001", kinds=["commitment"])
        self.assertEqual([row["evidence_id"] for row in filtered["rows"]], ["commitment-001"])
        with self.assertRaises(BusinessBackendError) as raised:
            read_account_timeline(self.root, "account-001", kinds=["artifact"], cursor=first["next_cursor"])
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")
        with patch("agent_platform.business_backend.MAX_TIMELINE_SCAN_ROWS", 1):
            truncated = read_account_timeline(self.root, "account-001", kinds=["artifact"], limit=20)
        self.assertTrue(truncated["truncated"])
        self.assertEqual(len(truncated["rows"]), 1)
        attention = read_today_focus(self.root, limit=20, now=datetime(2026, 8, 21, tzinfo=timezone.utc))
        kinds = {row["kind"] for row in attention["rows"]}
        self.assertTrue({"overdue_action", "commitment_due", "resource_deadline"}.issubset(kinds))
        self.assertTrue(all(row["account_id"] == "account-001" and row["evidence_id"] for row in attention["rows"]))

    def test_same_day_action_becomes_overdue_only_after_its_exact_due_time(self) -> None:
        self.write_csv(
            self.root / "data" / "sales" / "customers.csv",
            CSV_SCHEMAS["customers.csv"],
            [{"customer_id": "account-001", "customer_name": "华东脑机中心", "next_action": "确认预算", "next_action_due": "2026-08-21T08:00:00Z", "updated_at": "2026-08-21T00:00:00Z"}],
        )
        before = read_today_focus(self.root, now=datetime(2026, 8, 21, 7, tzinfo=timezone.utc))
        after = read_today_focus(self.root, now=datetime(2026, 8, 21, 12, tzinfo=timezone.utc))
        self.assertNotIn("account-001", {row["evidence_id"] for row in before["rows"] if row["kind"] == "overdue_action"})
        self.assertIn("account-001", {row["evidence_id"] for row in after["rows"] if row["kind"] == "overdue_action"})

        database = self.create_sqlite()
        connection = sqlite3.connect(database)
        connection.execute("UPDATE actions SET due_at='2026-08-21T08:00:00Z' WHERE action_id='action-002'")
        connection.commit()
        connection.close()
        sqlite_before = read_today_focus(self.root, now=datetime(2026, 8, 21, 7, tzinfo=timezone.utc))
        sqlite_after = read_today_focus(self.root, now=datetime(2026, 8, 21, 12, tzinfo=timezone.utc))
        self.assertNotIn("action-002", {row["evidence_id"] for row in sqlite_before["rows"] if row["kind"] == "overdue_action"})
        self.assertIn("action-002", {row["evidence_id"] for row in sqlite_after["rows"] if row["kind"] == "overdue_action"})


if __name__ == "__main__":
    unittest.main()
