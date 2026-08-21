from __future__ import annotations

import csv
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from agent_platform.sales_store import CSV_SCHEMAS
from ui import server


class BusinessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        sales = self.root / "data" / "sales"
        knowledge = self.root / "data" / "knowledge"
        sales.mkdir(parents=True)
        knowledge.mkdir(parents=True)
        today = datetime.now(timezone.utc).date()
        self.write_csv(sales / "customers.csv", CSV_SCHEMAS["customers.csv"], [{
            "customer_id": "account-001", "customer_name": "华东脑机中心", "region": "江苏",
            "owner": "销售甲", "stage": "proposal", "health": "attention", "key_contact": "李主任",
            "risks": "预算待确认", "next_action": "安排演示",
            "next_action_due": (today - timedelta(days=1)).isoformat(), "updated_at": "2026-08-21T10:00:00Z",
        }])
        self.write_csv(sales / "activities.csv", CSV_SCHEMAS["activities.csv"], [{
            "activity_id": "activity-001", "customer_id": "account-001", "occurred_at": "2026-08-20T09:00:00Z",
            "summary": "完成需求沟通", "commitment": "客户补充预算材料", "created_at": "2026-08-20T09:00:00Z",
        }])
        self.write_csv(sales / "resource-requests.csv", CSV_SCHEMAS["resource-requests.csv"], [{
            "request_id": "request-001", "customer_id": "account-001", "requested_at": "2026-08-20T09:00:00Z",
            "request_summary": "申请演示工程师", "deadline": (today + timedelta(days=2)).isoformat(), "status": "open",
        }])
        self.write_csv(sales / "sales-assets.csv", CSV_SCHEMAS["sales-assets.csv"], [])
        self.write_csv(knowledge / "source-register.csv", CSV_SCHEMAS["source-register.csv"], [])
        self.old_root = server.ROOT
        server.ROOT = self.root
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ControlHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.ROOT = self.old_root
        self.temporary.cleanup()

    @staticmethod
    def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def get_json(self, path: str) -> dict:
        with urlopen(f"{self.base}{path}", timeout=5) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            return json.loads(response.read().decode("utf-8"))

    def test_accounts_and_360_are_local_read_only_get_apis(self) -> None:
        accounts = self.get_json(f"/api/accounts?query={quote('脑机')}&limit=20")
        self.assertEqual(accounts["backend"], "csv")
        self.assertEqual(accounts["rows"][0]["account_id"], "account-001")
        detail = self.get_json("/api/accounts/account-001/360?sections=contacts,risks,actions")
        self.assertEqual(detail["account"]["name"], "华东脑机中心")
        self.assertEqual(detail["sections"]["contacts"][0]["display_name"], "李主任")
        signals = self.get_json("/api/signals?limit=20")
        self.assertEqual(signals["rows"], [])
        timeline = self.get_json("/api/accounts/account-001/timeline?kinds=activity,commitment&limit=20")
        self.assertEqual({row["kind"] for row in timeline["rows"]}, {"activity", "commitment"})
        attention = self.get_json("/api/attention?limit=20")
        self.assertEqual({row["kind"] for row in attention["rows"]}, {"overdue_action", "resource_deadline"})
        updated = self.get_json("/api/accounts?updated_since=2026-08-21T00%3A00%3A00Z")
        self.assertEqual([row["account_id"] for row in updated["rows"]], ["account-001"])

    def test_unknown_account_and_invalid_cursor_return_structured_errors(self) -> None:
        with self.assertRaises(HTTPError) as missing:
            urlopen(f"{self.base}/api/accounts/missing/360", timeout=5)
        self.assertEqual(missing.exception.code, 404)
        missing_body = json.loads(missing.exception.read().decode("utf-8"))
        self.assertEqual(missing_body["code"], "NOT_FOUND")
        with self.assertRaises(HTTPError) as invalid:
            urlopen(f"{self.base}/api/accounts?cursor=invalid", timeout=5)
        self.assertEqual(invalid.exception.code, 400)
        invalid_body = json.loads(invalid.exception.read().decode("utf-8"))
        self.assertEqual(invalid_body["code"], "INVALID_CURSOR")
        with self.assertRaises(HTTPError) as invalid_kind:
            urlopen(f"{self.base}/api/accounts/account-001/timeline?kinds=unknown", timeout=5)
        self.assertEqual(invalid_kind.exception.code, 400)
        invalid_kind_body = json.loads(invalid_kind.exception.read().decode("utf-8"))
        self.assertEqual(invalid_kind_body["code"], "INVALID_INPUT")

    def test_scan_limit_is_reported_as_temporarily_unavailable(self) -> None:
        with patch.object(
            server,
            "read_account_timeline",
            side_effect=server.BusinessBackendError("SCAN_LIMIT", "受控扫描达到上限"),
        ):
            with self.assertRaises(HTTPError) as limited:
                urlopen(f"{self.base}/api/accounts/account-001/timeline", timeout=5)
        self.assertEqual(limited.exception.code, 503)
        body = json.loads(limited.exception.read().decode("utf-8"))
        self.assertEqual(body["code"], "SCAN_LIMIT")


if __name__ == "__main__":
    unittest.main()
