from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.business_backend import BusinessBackendError
from agent_platform.sales_store import CSV_SCHEMAS, MANIFEST_PATH, MIGRATIONS
from agent_platform.weekly_briefing import build_weekly_briefing


class WeeklyBriefingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.sales = self.root / "data" / "sales"
        self.sales.mkdir(parents=True)
        (self.root / "data" / "knowledge").mkdir(parents=True)
        for name in ("customers.csv", "activities.csv", "resource-requests.csv", "sales-assets.csv"):
            self.write_csv(name, [])
        (self.sales / "salespeople.json").write_text(json.dumps({
            "salespeople": [
                {"salesperson_id": "seller-a", "name": "销售甲", "active": True},
                {"salesperson_id": "seller-new", "name": "新销售", "active": True},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_csv(self, name: str, rows: list[dict[str, str]]) -> None:
        with (self.sales / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_SCHEMAS[name], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def test_csv_preview_is_personalized_traceable_and_does_not_guess_ownership(self) -> None:
        self.write_csv("customers.csv", [
            {"customer_id": "account-a", "customer_name": "客户甲", "owner": "销售甲", "next_action": "确认试点范围", "next_action_due": "2026-08-28", "updated_at": "2026-08-25T09:00:00+08:00"},
            {"customer_id": "account-u", "customer_name": "待分配客户", "owner": "未知姓名", "updated_at": "2026-08-25T10:00:00+08:00"},
        ])
        self.write_csv("activities.csv", [{
            "activity_id": "activity-a", "customer_id": "account-a", "salesperson_id": "seller-a",
            "occurred_at": "2026-08-25T11:00:00+08:00", "channel": "现场", "activity_type": "拜访",
            "summary": "客户确认试点意向", "created_at": "2026-08-25T11:00:00+08:00",
        }])
        self.write_csv("resource-requests.csv", [{
            "request_id": "request-a", "customer_id": "account-a", "salesperson_id": "seller-a",
            "requested_at": "2026-08-25T12:00:00+08:00", "request_summary": "申请技术演示支持",
            "deadline": "2026-08-28", "status": "open", "updated_at": "2026-08-25T12:00:00+08:00",
        }])
        self.write_csv("sales-assets.csv", [
            {"asset_id": "asset-ok", "asset_type": "deck", "title": "试点方案", "scope": "customer-specific", "customer_id": "account-a", "status": "active", "authorization_status": "authorized", "source_path": "inputs/library/asset-ok/versions/plan.pptx", "updated_at": "2026-08-25T12:00:00+08:00"},
            {"asset_id": "asset-blocked", "asset_type": "deck", "title": "未授权材料", "scope": "customer-specific", "customer_id": "account-a", "status": "active", "authorization_status": "pending", "source_path": "inputs/private.pptx", "updated_at": "2026-08-25T12:00:00+08:00"},
        ])
        result = build_weekly_briefing(self.root, "2026-08-24", "2026-08-28")
        self.assertEqual(result["manager_rollup"]["seller_count"], 2)
        self.assertEqual(result["manager_rollup"]["unassigned_accounts"][0]["account_id"], "account-u")
        seller = next(item for item in result["seller_briefs"] if item["salesperson_id"] == "seller-a")
        self.assertEqual(seller["account_updates"][0]["evidence"], {"type": "activity", "id": "activity-a"})
        self.assertEqual(seller["top_actions"][0]["evidence"], {"type": "resource_request", "id": "request-a"})
        self.assertEqual(seller["recommended_assets"][0]["source_path"], "inputs/library/asset-ok/versions/plan.pptx")
        self.assertNotIn("url", seller["recommended_assets"][0])
        newcomer = next(item for item in result["seller_briefs"] if item["salesperson_id"] == "seller-new")
        self.assertIn("尚未找到可确认归属的客户", newcomer["welcome_note"])
        self.assertEqual(result["validation"]["filtered_asset_count"], 1)
        self.assertTrue(any("没有自动猜测负责人" in message for message in result["validation"]["messages"]))

    def create_sqlite(self) -> None:
        database = self.root / "data" / "agent4market.db"
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        migration = manifest["migrations"][0]
        connection = sqlite3.connect(database)
        connection.executescript((MIGRATIONS / migration["file"]).read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version,name,script_sha256,applied_at,application_version,result) VALUES (1,?,?,?,?, 'applied')",
            (migration["name"], migration["sha256"], "2026-08-24T00:00:00Z", manifest["application_version"]),
        )
        connection.execute("INSERT INTO store_metadata(key,value,updated_at) VALUES ('schema_version','1','2026-08-24T00:00:00Z')")
        connection.execute("INSERT INTO accounts(account_id,name,normalized_name,owner,version,created_at,updated_at) VALUES ('account-a','客户甲','客户甲','销售甲',1,'2026-08-24T00:00:00Z','2026-08-25T00:00:00Z')")
        connection.execute("INSERT INTO activities(activity_id,account_id,salesperson_id,occurred_at,summary,evidence_status,version,created_at,updated_at) VALUES ('activity-a','account-a','seller-a','2026-08-25T09:00:00Z','客户确认采购流程','verified',1,'2026-08-25T09:00:00Z','2026-08-25T09:00:00Z')")
        connection.execute("INSERT INTO actions(action_id,account_id,action_text,owner,due_at,status,origin,version,created_at,updated_at) VALUES ('action-a','account-a','提交试点清单','销售甲','2026-08-28','open','manual',1,'2026-08-25T09:00:00Z','2026-08-25T09:00:00Z')")
        connection.execute("INSERT INTO sales_assets(asset_id,asset_type,title,scope,account_id,status,authorization_status,source_path,source_status,version,created_at,updated_at) VALUES ('asset-a','deck','试点方案','customer-specific','account-a','active','authorized','inputs/plan.pptx','verified',1,'2026-08-25T09:00:00Z','2026-08-25T09:00:00Z')")
        connection.commit()
        connection.close()
        (self.root / "data" / "storage-backend.json").write_text(json.dumps({
            "backend": "sqlite", "schema_version": 1, "database_relative_path": "data/agent4market.db",
            "migration_batch_id": "test", "database_sha256_at_cutover": "0" * 64,
        }), encoding="utf-8")

    def test_sqlite_preview_uses_actions_and_verified_authorized_assets(self) -> None:
        self.create_sqlite()
        result = build_weekly_briefing(self.root, "2026-08-24", "2026-08-28")
        seller = next(item for item in result["seller_briefs"] if item["salesperson_id"] == "seller-a")
        self.assertEqual(result["source"]["backend"], "sqlite")
        self.assertEqual(seller["top_actions"][0]["evidence"], {"type": "action", "id": "action-a"})
        self.assertEqual(seller["recommended_assets"][0]["asset_id"], "asset-a")

    def test_invalid_period_fails_closed(self) -> None:
        with self.assertRaises(BusinessBackendError) as raised:
            build_weekly_briefing(self.root, "2026-08-30", "2026-08-01")
        self.assertEqual(raised.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
