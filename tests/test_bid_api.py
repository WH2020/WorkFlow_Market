from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ui import server


class BidApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "inputs").mkdir(parents=True)
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

    def get_json(self, path: str) -> dict:
        with urlopen(f"{self.base}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base}{path}", data=body, method="POST",
            headers={"Content-Type": "application/json", "X-Director-Token": server.SERVER_TOKEN},
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def create_project(self) -> dict:
        status, project = self.post_json("/api/bids", {
            "name": "某市数据采集平台采购项目", "workspace_project_id": "project-default",
            "account_id": "account-001", "buyer": "某市数据局", "tender_number": "ZB-2026-001",
            "deadline_at": "2026-09-30T17:00:00+08:00", "summary": "公开采购机会",
        })
        self.assertEqual(status, 201)
        return project

    def upload(self, bid_id: str, filename: str, content: bytes = b"%PDF-1.4\nfixture") -> tuple[int, dict]:
        request = Request(
            f"{self.base}/api/bid-files", data=content, method="POST",
            headers={
                "Content-Type": "application/octet-stream", "X-Director-Token": server.SERVER_TOKEN,
                "X-Bid-Id": bid_id, "X-Bid-Role": "tender", "X-File-Name": quote(filename),
            },
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_create_list_update_detail_and_timeline(self) -> None:
        project = self.create_project()
        listed = self.get_json("/api/bids?query=%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86&limit=20")
        self.assertEqual([row["bid_id"] for row in listed["rows"]], [project["bid_id"]])
        status, updated = self.post_json(f"/api/bids/{project['bid_id']}", {
            "expected_version": project["version"], "owner": "销售甲", "summary": "已联系采购代理",
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["version"], 2)
        detail = self.get_json(f"/api/bids/{project['bid_id']}/360")
        self.assertEqual(detail["project"]["owner"], "销售甲")
        timeline = self.get_json(f"/api/bids/{project['bid_id']}/timeline")
        self.assertEqual({row["event_type"] for row in timeline["rows"]}, {"project_created", "project_updated"})

    def test_upload_registers_hash_and_duplicate_never_overwrites(self) -> None:
        project = self.create_project()
        status, document = self.upload(project["bid_id"], "招标文件.pdf")
        self.assertEqual(status, 201)
        self.assertEqual(document["role"], "tender")
        self.assertEqual(document["byte_size"], len(b"%PDF-1.4\nfixture"))
        stored = self.root / document["relative_path"]
        self.assertEqual(stored.read_bytes(), b"%PDF-1.4\nfixture")
        with self.assertRaises(HTTPError) as duplicate:
            self.upload(project["bid_id"], "招标文件.pdf", b"different")
        self.assertEqual(duplicate.exception.code, 409)
        self.assertEqual(stored.read_bytes(), b"%PDF-1.4\nfixture")

    def test_checks_are_persisted_and_report_missing_source_before_upload(self) -> None:
        project = self.create_project()
        status, result = self.post_json(f"/api/bids/{project['bid_id']}/checks/run", {})
        self.assertEqual(status, 200)
        self.assertGreater(result["open_count"], 0)
        self.assertIn("BID-SOURCE-001", {finding["rule_id"] for finding in result["findings"]})
        detail = self.get_json(f"/api/bids/{project['bid_id']}/360?sections=checks")
        self.assertEqual(len(detail["sections"]["checks"]), 12)

    def test_upload_rejects_path_traversal_and_unknown_project(self) -> None:
        project = self.create_project()
        with self.assertRaises(HTTPError) as traversal:
            self.upload(project["bid_id"], "../escape.pdf")
        self.assertEqual(traversal.exception.code, 400)
        with self.assertRaises(HTTPError) as missing:
            self.upload("bid-does-not-exist", "file.pdf")
        self.assertEqual(missing.exception.code, 404)
        self.assertFalse((self.root / "escape.pdf").exists())


if __name__ == "__main__":
    unittest.main()
