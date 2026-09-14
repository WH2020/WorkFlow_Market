"""Private storage consent and persistence, using only synthetic data."""
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from agent_platform import wechat_privacy as privacy, wechat_storage as storage, wechat_store, wxdecipher
from agent_platform.wechat_store import WechatStoreError
from tests.wxdecipher_fixture import make_database, SELF


class WechatStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wechat-storage-", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        privacy.ensure_private_directory(self.root)
        self.project = self.root / "project"
        self.project.mkdir()
        self.base = self.root / "private-data"
        patcher = patch.object(storage, "_private_base", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage._PLANS.clear()
        self.addCleanup(storage._PLANS.clear)

    def grant(self, project=None):
        project = project or self.project
        plan = storage.permission_plan(project)
        return storage.grant_permission(project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})

    def test_no_write_before_confirmation_and_expiry_binding(self):
        plan = storage.permission_plan(self.project)
        self.assertFalse(self.base.exists())

    def _set_container_access(self, unsafe=False):
        if os.name == "nt":
            access = "(RX,DC)" if unsafe else "(RX)"
            subprocess.run(["icacls", str(self.base), "/grant", "*S-1-5-32-545:" + access],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.base.chmod(0o777 if unsafe else 0o755)

    def test_readable_container_preserves_private_project_boundary(self):
        selected = Path(self.grant()["directory"])
        self._set_container_access()
        with self.assertRaises(WechatStoreError):
            privacy.verify_private_directory(self.base)
        self.assertEqual(selected, storage.enrolled_root(self.project))
        other = self.root / "other-project"
        other.mkdir()
        self.assertIsNone(storage.enrolled_root(other))
        self.grant(other)
        privacy.verify_private_directory(storage.enrolled_root(other))
        # Enrollment must not repair the existing container's access policy.
        with self.assertRaises(WechatStoreError):
            privacy.verify_private_directory(self.base)

    def test_replaceable_container_still_blocks_existing_and_new_projects(self):
        self.grant()
        self._set_container_access(unsafe=True)
        with self.assertRaises(WechatStoreError):
            storage.enrolled_root(self.project)
        other = self.root / "other-project"
        other.mkdir()
        plan = storage.permission_plan(other)
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(other, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})

    def test_expired_confirmation_and_wrong_project_are_rejected(self):
        plan = storage.permission_plan(self.project)
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(self.project, {"confirmed": False, "confirmation_token": plan["confirmation_token"]})
        self.assertFalse(self.base.exists())
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(other, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        plan = storage.permission_plan(self.project)
        storage._PLANS[plan["confirmation_token"]]["created"] -= 121
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertFalse(self.base.exists())

    def test_private_storage_survives_restart_and_import_browse_export_cleanup(self):
        source = make_database(self.root / "synthetic.db")
        result = self.grant()
        selected = Path(result["directory"])
        privacy.verify_private_directory(selected)
        storage._PLANS.clear()  # persisted configuration must not rely on memory
        self.assertEqual(selected, storage.enrolled_root(self.project))
        self.assertEqual(selected / "chat-index.sqlite3", wechat_store.database_path(self.project))
        session = wxdecipher.create_session(self.project, {
            "ownership_confirmed": True, "snapshot_confirmed": True, "self_username": SELF,
        })["session_id"]
        self.assertTrue((selected / "decipher" / session).exists())
        wxdecipher.upload_database(self.project, session, "message_0.db", io.BytesIO(source.read_bytes()), source.stat().st_size)
        imported = wxdecipher.run_session(self.project, {"session_id": session})
        self.assertEqual(3, imported["decipher"]["messages"])
        self.assertEqual(3, wechat_store.dashboard(self.project)["message_count"])
        conversation = wechat_store.list_conversations(self.project)["rows"][0]["conversation_id"]
        self.assertEqual(3, wechat_store.export_selection(self.project, {"conversation_ids": [conversation], "format": "json"})["message_count"])
        self.assertFalse((self.project / "data" / "wechat").exists())
        self.assertFalse((selected / "decipher" / session).exists())
        self.assertTrue(storage.permission_plan(self.project)["configured"])
        self.assertTrue(list((selected / "imports").glob("*/*")))
        cleanup = wechat_store.cleanup_expired(self.project, reference=datetime.now(timezone.utc) + timedelta(days=8))
        self.assertEqual(3, cleanup["purged_messages"])
        self.assertEqual(1, cleanup["purged_batches"])
        self.assertFalse(list((selected / "imports").glob("*/*")))
        self.assertEqual(0, wechat_store.dashboard(self.project)["message_count"])

    def test_existing_data_and_data_created_after_plan_are_not_hidden(self):
        plan = storage.permission_plan(self.project)
        legacy = self.project / "data" / "wechat"
        legacy.mkdir(parents=True)
        keep = legacy / "keep.db"
        keep.write_bytes(b"keep")
        for action in (lambda: storage.permission_plan(self.project), lambda: storage.grant_permission(
                self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})):
            with self.assertRaises(WechatStoreError) as raised:
                action()
            self.assertEqual("STORAGE_MIGRATION_REQUIRED", raised.exception.code)
        self.assertEqual(b"keep", keep.read_bytes())
        self.assertFalse(self.base.exists())

    def test_failed_creation_does_not_activate_and_can_be_retried(self):
        plan = storage.permission_plan(self.project)
        with patch.object(privacy, "create_private_file", side_effect=WechatStoreError("UNSAFE_PATH", "synthetic denial")):
            with self.assertRaises(WechatStoreError):
                storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertIsNone(storage.enrolled_root(self.project))
        self.assertTrue(self.grant()["configured"])

    def test_custom_directory_copies_source_and_only_parses_private_copies(self):
        selected = self.root / "自选 数据"
        plan = storage.permission_plan(self.project, {"directory": str(selected)})
        self.assertFalse(selected.exists())
        storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"],
                                                "directory": str(self.root / "ignored-tampering")})
        storage._PLANS.clear()
        self.assertEqual(selected, storage.enrolled_root(self.project))
        self.assertFalse((self.root / "ignored-tampering").exists())
        source_dir = self.root / "wxid_synthetic"
        source_dir.mkdir()
        source = make_database(source_dir / "message_0.db")
        original = source.read_bytes()
        discovered = wxdecipher.discover_databases({"directory": str(source_dir), "ownership_confirmed": True})
        candidate_ids = [entry["candidate_id"] for group in discovered["groups"] for entry in group["files"]]
        created = wxdecipher.create_session(self.project, {"ownership_confirmed": True, "snapshot_confirmed": True,
                                                         "self_username": SELF, "retain_copies": True})
        session = created["session_id"]
        self.assertEqual(selected / "decipher" / session, Path(created["working_directory"]))
        wxdecipher.import_discovered_databases(self.project, {"session_id": session, "candidate_ids": candidate_ids,
                                                           "ownership_confirmed": True, "snapshot_confirmed": True})
        wxdecipher.finish_copy(self.project, {"session_id": session, "file_count": len(candidate_ids)})
        self.assertEqual(original, source.read_bytes())
        staged = selected / "decipher" / session
        metadata = wxdecipher._read_session(staged)
        self.assertEqual(original, (staged / metadata["files"][0]["stored_name"]).read_bytes())
        source.unlink()  # synthetic source is unavailable: parsing must still succeed
        decrypt = wxdecipher.decrypt_database
        convert = wxdecipher.convert_databases
        def checked_decrypt(source_path, destination, *args):
            self.assertTrue(source_path.is_relative_to(staged))
            self.assertTrue(destination.is_relative_to(staged))
            return decrypt(source_path, destination, *args)
        def checked_convert(databases, destination, **kwargs):
            self.assertTrue(all(path.is_relative_to(staged) for _, path in databases))
            self.assertTrue(destination.is_relative_to(staged))
            return convert(databases, destination, **kwargs)
        with patch.object(wxdecipher, "decrypt_database", side_effect=checked_decrypt), patch.object(
                wxdecipher, "convert_databases", side_effect=checked_convert):
            self.assertEqual(3, wxdecipher.run_session(self.project, {"session_id": session})["decipher"]["messages"])
        self.assertTrue(staged.exists())
        self.assertFalse((staged / "plain-0.db").exists())
        wxdecipher.discard_session(self.project, session)
        self.assertFalse(staged.exists())
        self.assertEqual(selected / "chat-index.sqlite3", wechat_store.database_path(self.project))
        self.assertTrue(list((selected / "imports").glob("*/*")))
        with self.assertRaises(WechatStoreError):
            storage.permission_plan(self.project, {"directory": str(self.root / "other-store")})

    def test_custom_empty_switch_and_racing_data_preserve_current_store(self):
        old = Path(self.grant()["directory"])
        selected = self.root / "chosen"
        plan = storage.permission_plan(self.project, {"directory": str(selected)})
        privacy.create_private_file(old / "keep.db")
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertEqual(old, storage.enrolled_root(self.project))
        self.assertFalse(selected.exists())
        (old / "keep.db").unlink()
        privacy.ensure_private_directory(old / "decipher")
        privacy.ensure_private_directory(old / "imports")
        plan = storage.permission_plan(self.project, {"directory": str(selected)})
        storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertEqual(selected, storage.enrolled_root(self.project))
        self.assertTrue(old.exists())  # no deletion or migration
        self.assertTrue((old / "decipher").exists())

    def test_invalid_and_nonempty_custom_directories_do_not_write(self):
        for directory in ("relative/path", str(self.root.anchor), str(self.root / "missing" / "leaf")):
            with self.subTest(directory=directory), self.assertRaises(WechatStoreError):
                storage.permission_plan(self.project, {"directory": directory})
        occupied = self.root / "occupied"
        privacy.ensure_private_directory(occupied)
        (occupied / "source.db").write_bytes(b"untouched")
        with self.assertRaises(WechatStoreError):
            storage.permission_plan(self.project, {"directory": str(occupied)})
        self.assertEqual(b"untouched", (occupied / "source.db").read_bytes())
        self.assertFalse(self.base.exists())

    def test_legacy_marker_remains_compatible(self):
        result = self.grant()
        _, target, identity = storage._locations(self.project)
        marker = target / "storage.json"
        marker.write_text(json.dumps({"schema_version": 1, "project_id": identity}), encoding="utf-8")
        self.assertEqual(Path(result["directory"]), storage.enrolled_root(self.project))

    def test_custom_target_populated_after_plan_is_not_activated(self):
        selected = self.root / "chosen"
        plan = storage.permission_plan(self.project, {"directory": str(selected)})
        privacy.ensure_private_directory(selected)
        (selected / "keep.db").write_bytes(b"keep")
        with self.assertRaises(WechatStoreError):
            storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertIsNone(storage.enrolled_root(self.project))
        self.assertEqual(b"keep", (selected / "keep.db").read_bytes())

    def test_failed_switch_keeps_previous_enrollment(self):
        old = Path(self.grant()["directory"])
        plan = storage.permission_plan(self.project, {"directory": str(self.root / "chosen")})
        with patch.object(privacy, "create_private_file", side_effect=WechatStoreError("UNSAFE_PATH", "synthetic denial")):
            with self.assertRaises(WechatStoreError):
                storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertEqual(old, storage.enrolled_root(self.project))

    def test_marker_publish_failure_keeps_old_configuration(self):
        old = Path(self.grant()["directory"])
        plan = storage.permission_plan(self.project, {"directory": str(self.root / "chosen")})
        with patch.object(Path, "replace", side_effect=OSError("synthetic publication denial")):
            with self.assertRaises(OSError):
                storage.grant_permission(self.project, {"confirmed": True, "confirmation_token": plan["confirmation_token"]})
        self.assertEqual(old, storage.enrolled_root(self.project))
        _, target, _ = storage._locations(self.project)
        self.assertFalse(list(target.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
