import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.library_store import (
    LibraryStoreError,
    append_file_version,
    archive_record,
    build_library_snapshot,
    catalog_revision,
    find_entry,
    register_file,
    register_url,
    restore_record,
    update_metadata,
)


class LibraryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self):
        return build_library_snapshot(
            self.root,
            sources=[{
                "source_id": "source-policy", "title": "某市人工智能扶持政策", "url": "https://example.com/policy",
                "publisher": "某市主管部门", "published_date": "2026-08-01", "accessed_date": "2026-08-20",
                "region": "江苏", "topic": "具身智能", "source_type": "html", "quality": "high",
                "status": "verified", "key_facts": "支持示范项目", "_record_version": "source-v1",
            }],
            project_files=[{
                "name": "A客户沟通纪要.pdf", "path": "inputs/projects/project-a/A客户沟通纪要.pdf",
                "project_id": "project-a", "size": 123, "modified_at": "2026-08-23T10:00:00+08:00", "version": "file-v1",
            }],
            artifacts=[{
                "name": "销售方案.pptx", "path": "outputs/销售方案.pptx", "project_id": "project-a",
                "modified_at": "2026-08-23T12:00:00+08:00",
            }],
        )

    def test_snapshot_merges_sources_files_and_artifacts_without_copying_content(self):
        snapshot = self.snapshot()
        self.assertEqual(len(snapshot["entries"]), 3)
        by_kind = {entry["kind"]: entry for entry in snapshot["entries"]}
        self.assertEqual(by_kind["source"]["category"], "government")
        self.assertEqual(by_kind["project_file"]["category"], "customer")
        self.assertEqual(by_kind["artifact"]["path"], "outputs/销售方案.pptx")
        self.assertEqual(snapshot["stats"]["verified"], 1)
        self.assertFalse((self.root / "data" / "library" / "catalog.json").exists())

    def test_metadata_overlay_is_version_bound_and_preserves_source_facts(self):
        original = next(entry for entry in self.snapshot()["entries"] if entry["kind"] == "source")
        updated = update_metadata(self.root, original, {
            "expected_version": 0, "title": "人工智能扶持政策（销售版）", "category": "government",
            "status": "verified", "confidentiality": "internal", "project_id": "project-a",
            "account_id": "account-a", "tags": ["政策", "具身智能"], "notes": "用于政府合作初筛",
        })
        self.assertEqual(updated["version"], 1)
        merged = find_entry(self.snapshot(), original["library_id"])
        self.assertEqual(merged["title"], "人工智能扶持政策（销售版）")
        self.assertEqual(merged["key_facts"], "支持示范项目")
        self.assertEqual(merged["account_id"], "account-a")
        with self.assertRaisesRegex(LibraryStoreError, "已变化"):
            update_metadata(self.root, merged, {"expected_version": 0, "title": "过期修改"})

    def test_url_registration_normalizes_fragments_and_deduplicates(self):
        first = register_url(self.root, {
            "url": "HTTPS://Example.com/policy#section-2", "title": "政策网页", "category": "government",
        })
        second = register_url(self.root, {
            "url": "https://example.com/policy", "title": "重复网页", "category": "industry",
        })
        self.assertEqual(first["url"], "https://example.com/policy")
        self.assertEqual(first["library_id"], second["library_id"])
        self.assertTrue(second["duplicate"])

    def test_url_registration_uses_a_readable_default_title(self):
        record = register_url(self.root, {"url": "https://example.com/policy"})
        self.assertEqual(record["title"], "网页资料 · example.com")

    def test_library_file_keeps_versions_and_rejects_duplicate_content(self):
        first = register_file(self.root, {
            "library_id": "library-file-a", "version_id": "version-0001",
            "path": "inputs/library/library-file-a/versions/version-0001/a.pdf", "filename": "a.pdf",
            "sha256": "a" * 64, "size": 10, "title": "客户资料", "category": "customer",
        })
        second = append_file_version(self.root, first["library_id"], {
            "version_id": "version-0002", "path": "inputs/library/library-file-a/versions/version-0002/a.pdf",
            "filename": "a.pdf", "sha256": "b" * 64, "size": 12,
        }, first["version"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(len(second["versions"]), 2)
        self.assertEqual(second["status"], "pending")
        with self.assertRaisesRegex(LibraryStoreError, "完全相同"):
            append_file_version(self.root, second["library_id"], {
                "version_id": "version-0003", "path": "inputs/library/library-file-a/versions/version-0003/a.pdf",
                "filename": "a.pdf", "sha256": "b" * 64, "size": 12,
            }, second["version"])

    def test_archive_and_restore_are_reversible_tombstones(self):
        entry = next(item for item in self.snapshot()["entries"] if item["kind"] == "project_file")
        archived = archive_record(self.root, entry, 0)
        archived_snapshot = self.snapshot()
        self.assertFalse(any(item["library_id"] == entry["library_id"] for item in archived_snapshot["entries"]))
        self.assertEqual(find_entry(archived_snapshot, entry["library_id"], include_trash=True)["catalog_version"], archived["version"])
        restored = restore_record(self.root, entry["library_id"], archived["version"])
        self.assertEqual(restored["deleted_at"], "")
        self.assertTrue(any(item["library_id"] == entry["library_id"] for item in self.snapshot()["entries"]))

    def test_catalog_revision_changes_after_write_and_catalog_is_not_secretly_tracked_elsewhere(self):
        before = catalog_revision(self.root)
        register_url(self.root, {"url": "https://example.com/a", "title": "网页 A"})
        after = catalog_revision(self.root)
        self.assertNotEqual(before, after)
        payload = json.loads((self.root / "data" / "library" / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(len(payload["records"]), 1)


if __name__ == "__main__":
    unittest.main()
