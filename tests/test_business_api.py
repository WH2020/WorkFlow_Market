from __future__ import annotations

import csv
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
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
        self.write_csv(sales / "customers.csv", CSV_SCHEMAS["customers.csv"], [{
            "customer_id": "account-001", "customer_name": "华东脑机中心", "region": "江苏",
            "owner": "销售甲", "stage": "proposal", "health": "attention", "key_contact": "李主任",
            "risks": "预算待确认", "next_action": "安排演示", "updated_at": "2026-08-21T10:00:00Z",
        }])
        for name in ("activities.csv", "resource-requests.csv", "sales-assets.csv"):
            self.write_csv(sales / name, CSV_SCHEMAS[name], [])
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


if __name__ == "__main__":
    unittest.main()
