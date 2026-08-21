from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from types import SimpleNamespace

from agent_platform.cli import main as platform_main

from agent_platform.sales_store import (
    CSV_SCHEMAS,
    MAX_SOURCE_BYTES,
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


class SalesStoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # macOS exposes temporary directories through /var while resolve() returns
        # the canonical /private/var path used by the production path guard. Keep
        # fault-injection comparisons on that same canonical identity.
        self.root = Path(self.temporary.name).resolve()
        self.sales = self.root / "data" / "sales"
        self.knowledge = self.root / "data" / "knowledge"
        self.inputs = self.root / "inputs"
        self.sales.mkdir(parents=True)
        self.knowledge.mkdir(parents=True)
        self.inputs.mkdir(parents=True)
        (self.inputs / "meeting.pdf").write_bytes(b"%PDF-1.4\nfixture")
        (self.inputs / "proposal.docx").write_bytes(b"PK fixture")
        (self.sales / "salespeople.json").write_text(
            json.dumps({"salespeople": [{"salesperson_id": "sales-001", "name": "销售甲", "active": True}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.write_csv("customers.csv", [{
            "customer_id": "customer-001", "customer_name": "华东,脑机\n联合中心",
            "region": "江苏", "sector": "脑机接口", "owner": "销售甲", "stage": "proposal",
            "health": "attention", "key_contact": "李主任", "decision_maker": "王局长",
            "budget_path": "专项资金", "next_action": "确认技术交流时间",
            "next_action_due": "2026-08-28T02:00:00.000Z", "last_evidence_date": "2026-08-20",
            "risks": "预算批复时间待确认", "updated_at": "2026-08-20T10:00:00.000Z",
        }])
        self.write_csv("activities.csv", [{
            "activity_id": "activity-001", "customer_id": "customer-001", "salesperson_id": "sales-001",
            "occurred_at": "2026-08-20T03:00:00.000Z", "channel": "现场", "activity_type": "需求沟通",
            "summary": "客户希望先验证数据采集方案。", "evidence_path": "inputs/meeting.pdf",
            "commitment": "我方下周提交接口清单", "next_action": "整理接口清单",
            "next_action_due": "2026-08-27T03:00:00.000Z", "created_at": "2026-08-20T04:00:00.000Z",
        }])
        self.write_csv("resource-requests.csv", [{
            "request_id": "request-001", "customer_id": "customer-001", "salesperson_id": "sales-001",
            "requested_at": "2026-08-20T05:00:00.000Z", "resource_type": "技术支持",
            "request_summary": "安排一次产品演示", "business_reason": "确认技术适配性",
            "deadline": "2026-08-29T03:00:00.000Z", "owner": "售前甲", "status": "pending",
            "decision": "", "decision_reason": "", "updated_at": "2026-08-20T05:00:00.000Z",
        }])
        self.write_csv("sales-assets.csv", [{
            "asset_id": "asset-001", "asset_type": "proposal", "title": "客户方案初稿", "scope": "customer-specific",
            "customer_id": "customer-001", "audience_role": "决策人", "sales_stage": "proposal",
            "use_case": "内部沟通", "owner": "销售甲", "status": "draft", "authorization_status": "pending",
            "deidentification_status": "pending", "version": "1", "source_path": "inputs/proposal.docx",
            "evidence_refs": "", "last_validated_at": "", "next_review_at": "2026-09-01T00:00:00.000Z",
            "usage_feedback": "", "updated_at": "2026-08-20T06:00:00.000Z",
        }])
        self.write_source_register([{
            "source_id": "source-001", "title": "省级产业政策", "url": "https://example.gov.cn/policy/1",
            "publisher": "示例省政府", "published_date": "2026-08-01", "accessed_date": "2026-08-20",
            "region": "江苏", "topic": "产业政策", "source_type": "web", "quality": "official",
            "exposure_status": "public", "key_facts": "政策原文待逐条引用", "important_quotes": "",
            "interpretation": "", "limitations": "仅用于测试", "status": "verified", "notes": "",
        }])

    def tearDown(self):
        self.temporary.cleanup()

    def write_csv(self, name: str, rows: list[dict[str, str]], *, bom: bool = False):
        path = self.sales / name
        with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_SCHEMAS[name], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def write_source_register(self, rows: list[dict[str, str]]):
        path = self.knowledge / "source-register.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_SCHEMAS["source-register.csv"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def stage(self):
        return migrate_sales_store(
            self.root,
            database_path="data/staging.db",
            report_path="data/imports/migration-report.json",
            dry_run=False,
        )

    def test_dry_run_reads_every_row_without_creating_a_database(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        report = migrate_sales_store(self.root, dry_run=True)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report["mode"], "preflight")
        self.assertEqual(report["total_rows"], 5)
        self.assertEqual(report["counts"], {"imported": 5, "skipped_duplicate": 0, "quarantined": 0, "failed": 0})
        self.assertTrue(report["staging_eligible"])
        self.assertFalse(report["cutover_ready"])
        self.assertFalse((self.root / "data" / "staging.db").exists())

    def test_staging_import_reconciles_entities_and_preserves_row_results(self):
        report = self.stage()
        self.assertTrue(report["cutover_ready"])
        database = self.root / report["database_relative_path"]
        verified = verify_sales_store(database)
        self.assertEqual(verified["schema_version"], 1)
        self.assertEqual(verified["integrity_check"], "ok")
        self.assertEqual(verified["tables"]["accounts"], 1)
        self.assertEqual(verified["tables"]["activities"], 1)
        self.assertEqual(verified["tables"]["contacts"], 2)
        self.assertEqual(verified["tables"]["actions"], 2)
        connection = sqlite3.connect(database)
        try:
            results = connection.execute("SELECT result, count(*) FROM import_rows GROUP BY result").fetchall()
            account = connection.execute("SELECT name, normalized_name FROM accounts").fetchone()
            evidence = connection.execute("SELECT evidence_status, source_sha256 FROM activities").fetchone()
        finally:
            connection.close()
        self.assertEqual(results, [("imported", 5)])
        self.assertEqual(account[0], "华东,脑机\n联合中心")
        self.assertTrue(account[1])
        self.assertEqual(evidence[0], "verified")
        self.assertRegex(evidence[1], r"^[a-f0-9]{64}$")

    def test_invalid_and_duplicate_rows_are_quarantined_without_entering_business_tables(self):
        rows = [
            {
                "customer_id": "customer-001", "customer_name": "华东中心", "updated_at": "2026-08-20T10:00:00.000Z",
            },
            {
                "customer_id": "customer-001", "customer_name": "被替换的重复客户", "updated_at": "2026-08-20T11:00:00.000Z",
            },
            {
                "customer_id": "customer-formula", "customer_name": "=HYPERLINK(\"x\")", "updated_at": "2026-08-20T11:00:00.000Z",
            },
        ]
        self.write_csv("customers.csv", rows, bom=True)
        activity = {
            "activity_id": "orphan-001", "customer_id": "missing-customer", "salesperson_id": "sales-001",
            "occurred_at": "2026-08-20T03:00:00.000Z", "summary": "孤立互动",
            "created_at": "2026-08-20T03:00:00.000Z",
        }
        self.write_csv("activities.csv", [activity])
        self.write_source_register([
            {"source_id": "source-good", "title": "普通来源", "url": "https://example.gov.cn/a", "status": "pending"},
            {"source_id": "source-secret", "title": "含密钥来源", "url": "https://example.gov.cn/a?access_token=secret", "status": "verified"},
            {"source_id": "source-status", "title": "未知状态来源", "url": "https://example.gov.cn/b", "status": "mystery"},
        ])
        report = migrate_sales_store(
            self.root, database_path="data/invalid-staging.db", report_path="data/imports/invalid.json", dry_run=False
        )
        self.assertFalse(report["cutover_ready"])
        codes = {item["code"] for item in report["issues"]}
        self.assertTrue({"duplicate_key", "formula_injection", "orphan_reference", "unsafe_url", "invalid_status"}.issubset(codes))
        database = self.root / "data" / "invalid-staging.db"
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM accounts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM activities").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM import_rows").fetchone()[0], report["total_rows"])
        finally:
            connection.close()

    def test_header_only_files_create_an_empty_but_fully_reconciled_staging_store(self):
        for name in ("customers.csv", "activities.csv", "resource-requests.csv", "sales-assets.csv"):
            self.write_csv(name, [])
        self.write_source_register([])
        report = migrate_sales_store(
            self.root, database_path="data/empty-staging.db", report_path="data/imports/empty.json", dry_run=False
        )
        self.assertEqual(report["total_rows"], 0)
        self.assertEqual(report["counts"], {"imported": 0, "skipped_duplicate": 0, "quarantined": 0, "failed": 0})
        self.assertTrue(report["cutover_ready"])
        self.assertTrue(all(value == 0 for value in verify_sales_store(self.root / "data" / "empty-staging.db")["tables"].values()))

    def test_missing_and_traversal_asset_paths_are_preserved_as_unverified_metadata(self):
        self.write_csv("sales-assets.csv", [{
            "asset_id": "asset-missing", "asset_type": "proposal", "title": "缺失文件资料", "scope": "customer-specific",
            "customer_id": "customer-001", "status": "draft", "authorization_status": "pending",
            "deidentification_status": "pending", "version": "1", "source_path": "../outside.docx",
            "updated_at": "2026-08-20T06:00:00.000Z",
        }])
        report = migrate_sales_store(
            self.root, database_path="data/path-staging.db", report_path="data/imports/path.json", dry_run=False
        )
        self.assertTrue(report["cutover_ready"])
        self.assertIn("asset_file_unavailable", {item["code"] for item in report["issues"]})
        connection = sqlite3.connect(self.root / "data" / "path-staging.db")
        try:
            path, status = connection.execute(
                "SELECT source_path, source_status FROM sales_assets WHERE asset_id='asset-missing'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(path)
        self.assertEqual(status, "rejected")

    def test_backup_restore_and_formula_safe_export_never_overwrite_targets(self):
        self.stage()
        database = self.root / "data" / "staging.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE accounts SET owner = '=cmd|test' WHERE account_id = 'customer-001'")
            connection.commit()
        finally:
            connection.close()
        backup = backup_sales_store(self.root, "data/staging.db", "backups/database/store-v1.db")
        self.assertTrue((self.root / backup["backup_relative_path"]).is_file())
        restored = restore_sales_store(self.root, backup["backup_relative_path"], "data/restored.db")
        self.assertEqual(restored["tables"], verify_sales_store(self.root / "data" / "restored.db")["tables"])
        exported = export_sales_store(self.root, "data/restored.db", "data/exports/export-001")
        accounts_csv = self.root / exported["export_relative_path"] / "accounts.csv"
        self.assertIn("'=cmd|test", accounts_csv.read_text(encoding="utf-8-sig"))
        self.assertGreaterEqual(exported["formula_cells_escaped"], 1)
        round_trip = import_sales_store_export(
            self.root, exported["export_relative_path"], "data/round-trip.db"
        )
        self.assertEqual(round_trip["tables"], restored["tables"])
        round_trip_connection = sqlite3.connect(self.root / "data" / "round-trip.db")
        try:
            restored_owner = round_trip_connection.execute(
                "SELECT owner FROM accounts WHERE account_id = 'customer-001'"
            ).fetchone()[0]
        finally:
            round_trip_connection.close()
        self.assertEqual(restored_owner, "=cmd|test")
        with self.assertRaises(SalesStoreError) as caught:
            backup_sales_store(self.root, "data/staging.db", "backups/database/store-v1.db")
        self.assertEqual(caught.exception.code, "TARGET_EXISTS")

    def test_activation_requires_exact_report_database_and_approval_hashes(self):
        report = self.stage()
        approval = {
            "approval_type": "sales-store-cutover", "approved": True,
            "migration_batch_id": report["batch_id"], "database_sha256": report["database_sha256"],
            "report_sha256": report["report_sha256"], "approval_id": "approval-test-001",
            "approved_at": "2026-08-21T00:00:00.000Z",
        }
        approval_path = self.root / "data" / "imports" / "approval.json"
        approval_path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
        activated = activate_sales_store(
            self.root,
            database_path="data/staging.db",
            report_path="data/imports/migration-report.json",
            approval_path="data/imports/approval.json",
            expected_pointer_sha256=None,
        )
        self.assertEqual(activated["activation"]["backend"], "sqlite")
        pointer = self.root / "data" / "storage-backend.json"
        self.assertEqual(json.loads(pointer.read_text(encoding="utf-8"))["migration_batch_id"], report["batch_id"])
        repeated = activate_sales_store(
            self.root,
            database_path="data/staging.db",
            report_path="data/imports/migration-report.json",
            approval_path="data/imports/approval.json",
            expected_pointer_sha256=None,
        )
        self.assertEqual(repeated["pointer_sha256"], activated["pointer_sha256"])
        rolled_back = rollback_sales_store_activation(
            self.root,
            batch_id=report["batch_id"],
            expected_current_pointer_sha256=activated["pointer_sha256"],
        )
        self.assertEqual(rolled_back["activation_status"], "rolled_back")
        self.assertFalse(pointer.exists())
        repeated_rollback = rollback_sales_store_activation(
            self.root,
            batch_id=report["batch_id"],
            expected_current_pointer_sha256=activated["pointer_sha256"],
        )
        self.assertEqual(repeated_rollback["activation_status"], "rolled_back")

    def test_rollback_stops_after_any_post_cutover_business_change(self):
        report = self.stage()
        approval = {
            "approval_type": "sales-store-cutover", "approved": True,
            "migration_batch_id": report["batch_id"], "database_sha256": report["database_sha256"],
            "report_sha256": report["report_sha256"], "approval_id": "approval-test-write",
            "approved_at": "2026-08-21T00:00:00.000Z",
        }
        approval_path = self.root / "data" / "imports" / "approval-write.json"
        approval_path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
        activated = activate_sales_store(
            self.root,
            database_path="data/staging.db",
            report_path="data/imports/migration-report.json",
            approval_path="data/imports/approval-write.json",
            expected_pointer_sha256=None,
        )
        database = self.root / "data" / "staging.db"
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE accounts SET owner='切换后新负责人', version=version+1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(SalesStoreError) as caught:
            rollback_sales_store_activation(
                self.root,
                batch_id=report["batch_id"],
                expected_current_pointer_sha256=activated["pointer_sha256"],
            )
        self.assertEqual(caught.exception.code, "ROLLBACK_REQUIRES_RECONCILIATION")
        self.assertTrue((self.root / "data" / "storage-backend.json").is_file())

    def test_activation_and_rollback_recover_across_receipt_finalize_failures(self):
        report = self.stage()
        approval = {
            "approval_type": "sales-store-cutover", "approved": True,
            "migration_batch_id": report["batch_id"], "database_sha256": report["database_sha256"],
            "report_sha256": report["report_sha256"], "approval_id": "approval-crash-recovery",
            "approved_at": "2026-08-21T00:00:00.000Z",
        }
        approval_path = self.root / "data" / "imports" / "approval-crash.json"
        approval_path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
        from agent_platform import sales_store as module

        original_replace = module._atomic_replace
        receipt_path = self.root / ".pi" / "director-runtime" / "storage-activations" / f"{report['batch_id']}.json"

        def fail_activation_receipt(path, content):
            if Path(path) == receipt_path:
                raise OSError("injected receipt finalize failure")
            original_replace(path, content)

        with patch("agent_platform.sales_store._atomic_replace", side_effect=fail_activation_receipt):
            with self.assertRaises(OSError):
                activate_sales_store(
                    self.root,
                    database_path="data/staging.db",
                    report_path="data/imports/migration-report.json",
                    approval_path="data/imports/approval-crash.json",
                    expected_pointer_sha256=None,
                )
        prepared = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(prepared["status"], "prepared")
        pointer = self.root / "data" / "storage-backend.json"
        self.assertEqual(self.root.joinpath("data", "storage-backend.json").is_file(), True)
        recovered = activate_sales_store(
            self.root,
            database_path="data/staging.db",
            report_path="data/imports/migration-report.json",
            approval_path="data/imports/approval-crash.json",
            expected_pointer_sha256=None,
        )
        self.assertEqual(recovered["activation_status"], "committed")

        def fail_rollback_receipt(path, content):
            if Path(path) == receipt_path:
                raise OSError("injected rollback receipt failure")
            original_replace(path, content)

        with patch("agent_platform.sales_store._atomic_replace", side_effect=fail_rollback_receipt):
            with self.assertRaises(OSError):
                rollback_sales_store_activation(
                    self.root,
                    batch_id=report["batch_id"],
                    expected_current_pointer_sha256=recovered["pointer_sha256"],
                )
        self.assertFalse(pointer.exists())
        recovered_rollback = rollback_sales_store_activation(
            self.root,
            batch_id=report["batch_id"],
            expected_current_pointer_sha256=recovered["pointer_sha256"],
        )
        self.assertEqual(recovered_rollback["activation_status"], "rolled_back")

    def test_damaged_csv_fails_before_database_creation(self):
        (self.sales / "customers.csv").write_text("wrong,header\n1,2\n", encoding="utf-8")
        with self.assertRaises(SalesStoreError) as caught:
            migrate_sales_store(self.root, database_path="data/never.db", dry_run=False)
        self.assertEqual(caught.exception.code, "SCHEMA_MISMATCH")
        self.assertFalse((self.root / "data" / "never.db").exists())

    def test_source_larger_than_the_fixed_budget_is_rejected_before_reading(self):
        target = self.sales / "customers.csv"
        with target.open("wb") as handle:
            handle.write((",".join(CSV_SCHEMAS["customers.csv"]) + "\n").encode("utf-8"))
            handle.seek(MAX_SOURCE_BYTES)
            handle.write(b"x")
        with self.assertRaises(SalesStoreError) as caught:
            migrate_sales_store(self.root, database_path="data/oversized.db", dry_run=False)
        self.assertEqual(caught.exception.code, "FILE_TOO_LARGE")
        self.assertFalse((self.root / "data" / "oversized.db").exists())

    def test_controlled_migration_directories_reject_symlink_aliases(self):
        alias = self.root / "data" / "sales-alias"
        try:
            alias.symlink_to(self.sales, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks are unavailable: {error}")
        with self.assertRaises(SalesStoreError) as caught:
            migrate_sales_store(self.root, sales_dir="data/sales-alias", dry_run=True)
        self.assertEqual(caught.exception.code, "UNSAFE_PATH")

    def test_staging_stops_before_database_creation_when_disk_budget_is_insufficient(self):
        with patch("agent_platform.sales_store.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaises(SalesStoreError) as caught:
                migrate_sales_store(self.root, database_path="data/no-space.db", dry_run=False)
        self.assertEqual(caught.exception.code, "INSUFFICIENT_SPACE")
        self.assertFalse((self.root / "data" / "no-space.db").exists())

    def test_schema_manifest_contracts_and_gitignore_remain_aligned(self):
        repository = Path(__file__).resolve().parents[1]
        manifest = json.loads((repository / "agent_platform" / "migrations" / "manifest.json").read_text(encoding="utf-8"))
        package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
        migration = manifest["migrations"][0]
        script = (repository / "agent_platform" / "migrations" / migration["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(script).hexdigest(), migration["sha256"])
        self.assertEqual(manifest["application_version"], package["version"])
        self.assertEqual(manifest["schema_version"], 1)
        for name in ("sales-mutation.schema.json", "migration-report.schema.json"):
            contract = json.loads((repository / "contracts" / name).read_text(encoding="utf-8"))
            self.assertEqual(contract["$schema"], "https://json-schema.org/draft/2020-12/schema")
        ignored = (repository / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("data/*.db", "data/*.db-*", "data/imports/", "data/exports/", "data/storage-backend.json", "backups/"):
            self.assertIn(pattern, ignored)
        attributes = (repository / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("agent_platform/migrations/*.sql text eol=lf", attributes)

    def test_cli_exposes_read_only_preflight_and_explicit_staging_modes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = platform_main(["--root", str(self.root), "migrate-sales-store", "--dry-run"])
        self.assertEqual(code, 0)
        preflight = json.loads(output.getvalue())
        self.assertEqual(preflight["status"], "ok")
        self.assertEqual(preflight["mode"], "preflight")
        output = io.StringIO()
        with redirect_stdout(output):
            code = platform_main([
                "--root", str(self.root), "migrate-sales-store", "--staging",
                "--database", "data/cli-staging.db", "--report", "data/imports/cli-report.json",
            ])
        self.assertEqual(code, 0)
        staged = json.loads(output.getvalue())
        self.assertTrue(staged["cutover_ready"])
        self.assertTrue((self.root / "data" / "cli-staging.db").is_file())


if __name__ == "__main__":
    unittest.main()
