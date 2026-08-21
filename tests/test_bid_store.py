from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.bid_store import (
    BidStoreError,
    bid_connection,
    bid_store_summary,
    create_bid_project,
    read_bid_project,
    read_bid_timeline,
    register_bid_document,
    run_deterministic_bid_checks,
    search_bid_projects,
    transition_bid_project,
    update_bid_project,
)


class BidStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_project(self) -> dict:
        return create_bid_project(
            self.root,
            {
                "name": "省级智能装备采购项目",
                "workspace_project_id": "project-default",
                "account_id": "account-001",
                "opportunity_id": "opportunity-001",
                "buyer": "示例采购人",
                "tender_number": "ZB-2026-001",
                "owner": "销售甲",
                "deadline_at": "2030-08-30T17:00:00+08:00",
            },
        )

    def test_schema_initializes_and_project_links_remain_queryable(self) -> None:
        project = self.create_project()
        summary = bid_store_summary(self.root)
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["project_count"], 1)
        self.assertEqual(summary["active_count"], 1)
        matches = search_bid_projects(self.root, query="智能装备", account_id="account-001")
        self.assertEqual(matches["returned"], 1)
        self.assertEqual(matches["rows"][0]["opportunity_id"], "opportunity-001")
        detail = read_bid_project(self.root, project["bid_id"])
        self.assertEqual(detail["project"]["workspace_project_id"], "project-default")
        self.assertEqual(len(detail["sections"]["milestones"]), 1)

    def test_first_read_repairs_only_an_empty_sqlite_header(self) -> None:
        database = self.root / "data" / "bids" / "bidding.sqlite3"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
        finally:
            connection.close()
        self.assertGreater(database.stat().st_size, 0)
        summary = bid_store_summary(self.root)
        self.assertEqual(summary["project_count"], 0)
        verified = sqlite3.connect(database)
        try:
            self.assertEqual(
                verified.execute("SELECT count(*) FROM bid_schema_migrations").fetchone()[0],
                1,
            )
        finally:
            verified.close()

    def test_unknown_existing_tables_are_never_replaced_by_bootstrap(self) -> None:
        database = self.root / "data" / "bids" / "bidding.sqlite3"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE foreign_business_data(value TEXT)")
            connection.execute("INSERT INTO foreign_business_data(value) VALUES ('preserve')")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(BidStoreError) as blocked:
            bid_store_summary(self.root)
        self.assertEqual(blocked.exception.code, "SCHEMA_UNSUPPORTED")
        preserved = sqlite3.connect(database)
        try:
            self.assertEqual(
                preserved.execute("SELECT value FROM foreign_business_data").fetchone()[0],
                "preserve",
            )
        finally:
            preserved.close()

    def test_tender_upload_is_hash_registered_and_moves_to_interpretation(self) -> None:
        project = self.create_project()
        directory = self.root / "inputs" / "bids" / project["bid_id"]
        directory.mkdir(parents=True)
        source = directory / "tender.pdf"
        source.write_bytes(b"%PDF-1.4\ncontrolled fixture")
        document = register_bid_document(
            self.root,
            project["bid_id"],
            source.relative_to(self.root).as_posix(),
            role="tender",
        )
        self.assertEqual(document["byte_size"], source.stat().st_size)
        self.assertRegex(document["sha256"], r"^[a-f0-9]{64}$")
        detail = read_bid_project(self.root, project["bid_id"])
        self.assertEqual(detail["project"]["status"], "interpreting")
        self.assertEqual(detail["sections"]["documents"][0]["relative_path"], source.relative_to(self.root).as_posix())

    def test_document_registration_rejects_traversal_and_duplicate_files(self) -> None:
        project = self.create_project()
        outside = self.root / "inputs" / "outside.pdf"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b"%PDF-1.4\noutside")
        with self.assertRaises(BidStoreError) as traversal:
            register_bid_document(self.root, project["bid_id"], outside.relative_to(self.root).as_posix())
        self.assertEqual(traversal.exception.code, "UNSAFE_PATH")

        controlled = self.root / "inputs" / "bids" / project["bid_id"] / "tender.pdf"
        controlled.parent.mkdir(parents=True)
        controlled.write_bytes(b"%PDF-1.4\ninside")
        relative = controlled.relative_to(self.root).as_posix()
        register_bid_document(self.root, project["bid_id"], relative)
        with self.assertRaises(BidStoreError) as duplicate:
            register_bid_document(self.root, project["bid_id"], relative)
        self.assertEqual(duplicate.exception.code, "CONFLICT")

    def test_versioned_update_and_stage_transition_reject_stale_requests(self) -> None:
        project = self.create_project()
        updated = update_bid_project(
            self.root,
            project["bid_id"],
            {"expected_version": project["version"], "summary": "已确认采购主体，待核对资格条件。"},
        )
        self.assertEqual(updated["version"], project["version"] + 1)
        with self.assertRaises(BidStoreError) as stale:
            update_bid_project(
                self.root,
                project["bid_id"],
                {"expected_version": project["version"], "summary": "过期修改"},
            )
        self.assertEqual(stale.exception.code, "VERSION_CONFLICT")
        transitioned = transition_bid_project(
            self.root,
            project["bid_id"],
            status="interpreting",
            current_stage="interpretation",
            expected_version=updated["version"],
        )
        self.assertEqual(transitioned["current_stage"], "interpretation")
        with self.assertRaises(BidStoreError) as invalid:
            transition_bid_project(
                self.root,
                project["bid_id"],
                status="delivered",
                current_stage="delivery",
                expected_version=transitioned["version"],
            )
        self.assertEqual(invalid.exception.code, "INVALID_TRANSITION")

    def test_deterministic_checks_are_repeatable_and_audited(self) -> None:
        project = self.create_project()
        first = run_deterministic_bid_checks(self.root, project["bid_id"])
        second = run_deterministic_bid_checks(self.root, project["bid_id"])
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(first["open_count"], second["open_count"])
        self.assertEqual(
            {item["rule_id"] for item in first["findings"]},
            {"BID-SOURCE-001", "BID-FACT-001", "BID-OUTLINE-001", "BID-DECISION-001"},
        )
        detail = read_bid_project(self.root, project["bid_id"], sections=["checks"])
        self.assertEqual(len(detail["sections"]["checks"]), 12)
        timeline = read_bid_timeline(self.root, project["bid_id"])
        self.assertEqual(sum(item["event_type"] == "checks_run" for item in timeline["rows"]), 2)

    def test_schema_hash_tampering_is_detected_at_open_time(self) -> None:
        self.create_project()
        database = self.root / "data" / "bids" / "bidding.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE bid_schema_migrations SET script_sha256=?", ("0" * 64,))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(BidStoreError) as corrupted:
            with bid_connection(self.root):
                pass
        self.assertEqual(corrupted.exception.code, "SCHEMA_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
