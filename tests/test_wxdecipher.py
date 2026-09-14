from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import zstandard

from agent_platform import wechat_store, wxdecipher
from agent_platform.wechat_store import WechatStoreError
from agent_platform.wxdecipher_crypto import decrypt_database, derive_keys, open_readonly, parse_key
from agent_platform.wxdecipher_reader import convert_databases
from tests.wxdecipher_fixture import CLIENT, EPOCH, KEY, KEY_HEX, SELF, encrypt_fixture, make_database, table_for


class WxDecipherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wxdecipher-test-", dir=Path.home())
        self.root = Path(self.temporary.name)
        self.source = make_database(self.root / "source.db")
        with wxdecipher._DISCOVERY_LOCK:
            wxdecipher._DISCOVERY_TOKENS.clear()

    def tearDown(self):
        with wxdecipher._DISCOVERY_LOCK:
            wxdecipher._DISCOVERY_TOKENS.clear()
        self.temporary.cleanup()

    def create_session(self, account="合成测试", self_username=SELF):
        return wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True,
                                                    "account_label": account, "self_username": self_username})["session_id"]

    def upload(self, session, source=None, name="message_0.db"):
        source = source or self.source
        with source.open("rb") as reader:
            return wxdecipher.upload_database(self.root, session, name, reader, source.stat().st_size)

    def run_import(self, source=None, *, account="合成测试", key="", file_keys=None, self_username=SELF):
        session = self.create_session(account, self_username)
        self.upload(session, source)
        return wxdecipher.run_session(self.root, {"session_id": session, "key": key, "file_keys": file_keys or {}})

    def test_raw_and_password_key_modes_round_trip_verified_sqlite(self):
        before = (self.source.read_bytes(), self.source.stat().st_mtime_ns)
        for raw_key, expected in ((True, "sqlcipher4-raw"), (False, "sqlcipher4")):
            with self.subTest(mode=expected):
                cipher = encrypt_fixture(self.source, self.root / f"{expected}.db", raw_key=raw_key)
                destination = self.root / f"{expected}-plain.db"
                report = decrypt_database(cipher, destination, KEY_HEX)
                self.assertEqual(expected, report["cipher_mode"])
                self.assertTrue(report["verified"])
                self.assertEqual(destination.read_bytes(), cipher.with_suffix(".reserved.sqlite").read_bytes())
                with open_readonly(destination) as connection:
                    self.assertEqual(3, connection.execute(f'SELECT count(*) FROM "{table_for(CLIENT)}"').fetchone()[0])
        self.assertEqual(before, (self.source.read_bytes(), self.source.stat().st_mtime_ns))

    def copied_session(self, source=None):
        session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True,
            "self_username": SELF, "retain_copies": True})["session_id"]
        self.upload(session, source)
        wxdecipher.finish_copy(self.root, {"session_id": session, "file_count": 1})
        return session, wxdecipher._session_path(self.root, session)

    def test_split_copy_seals_complete_batch_without_parsing_or_keys(self):
        with patch.object(wxdecipher, "decrypt_database", side_effect=AssertionError("must not decrypt")), patch.object(
                wxdecipher.wxdecipher_capture, "capture_keys", side_effect=AssertionError("must not capture")):
            session, path = self.copied_session()
        self.assertTrue(wxdecipher._read_session(path)["copy_ready"])
        with self.assertRaises(WechatStoreError) as raised:
            self.upload(session, name="contact.db")
        self.assertEqual("COPY_SEALED", raised.exception.code)
        self.assertEqual(1, len(wxdecipher._read_session(path)["files"]))

    def test_split_parse_failure_retries_same_copies_without_source_and_cleans_plaintext(self):
        cipher = encrypt_fixture(self.source, self.root / "encrypted.db")
        session, path = self.copied_session(cipher)
        data = wxdecipher._read_session(path)
        original = (path / data["files"][0]["stored_name"]).read_bytes()
        cipher.unlink()
        with self.assertRaises(WechatStoreError):
            wxdecipher.run_session(self.root, {"session_id": session, "key": "ff" * 32})
        self.assertTrue(path.is_dir())
        self.assertEqual({"session.json", data["files"][0]["stored_name"]}, {entry.name for entry in path.iterdir()})
        for _ in range(2):
            result = wxdecipher.run_session(self.root, {"session_id": session, "key": KEY_HEX})
            self.assertEqual(3, result["decipher"]["messages"])
            self.assertEqual(original, (path / data["files"][0]["stored_name"]).read_bytes())
            self.assertEqual(data["created_at"], wxdecipher._read_session(path)["created_at"])
            self.assertEqual({"session.json", data["files"][0]["stored_name"]}, {entry.name for entry in path.iterdir()})
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        wxdecipher.discard_session(self.root, session)
        self.assertFalse(path.exists())
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])

    def test_split_conversion_failure_removes_outputs_and_capture_consent(self):
        session, path = self.copied_session()
        data = wxdecipher._read_session(path)
        data["capture_consent"] = {"synthetic": True}
        wxdecipher._save_session(path, data)
        with patch.object(wxdecipher, "convert_databases", side_effect=WechatStoreError("SYNTHETIC", "conversion failed")):
            with self.assertRaises(WechatStoreError):
                wxdecipher.run_session(self.root, {"session_id": session})
        self.assertNotIn("capture_consent", wxdecipher._read_session(path))
        self.assertFalse((path / "plain-0.db").exists())
        self.assertEqual(3, wxdecipher.run_session(self.root, {"session_id": session})["decipher"]["messages"])

    def test_split_incomplete_copy_and_expiry_never_parse(self):
        session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True,
            "self_username": SELF, "retain_copies": True})["session_id"]
        self.upload(session)
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.finish_copy(self.root, {"session_id": session, "file_count": 2})
        self.assertEqual("COPY_INCOMPLETE", raised.exception.code)
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.run_session(self.root, {"session_id": session})
        self.assertEqual("COPY_INCOMPLETE", raised.exception.code)
        wxdecipher.finish_copy(self.root, {"session_id": session, "file_count": 1})
        path = wxdecipher._session_path(self.root, session)
        created = wxdecipher._read_session(path)["created_at"]
        with patch.object(wxdecipher.time, "time", return_value=created + wxdecipher.SESSION_TTL + 1):
            with self.assertRaises(WechatStoreError):
                wxdecipher.run_session(self.root, {"session_id": session})
        self.assertFalse(path.exists())

    def test_split_capture_consent_is_not_reusable_after_failed_parse(self):
        cipher = encrypt_fixture(self.source, self.root / "capture-encrypted.db")
        session, path = self.copied_session(cipher)
        capture = {"session_id": session, "confirmed": True, "process_id": 123, "created_at": "456"}
        ticket = wxdecipher.issue_capture_consent(self.root, capture)
        request = {"session_id": session, "auto_capture": {**capture, "consent_token": ticket["consent_token"]}}
        with patch.object(wxdecipher.wxdecipher_capture, "capture_keys", side_effect=WechatStoreError("SYNTHETIC", "capture denied")) as mocked:
            with self.assertRaises(WechatStoreError):
                wxdecipher.run_session(self.root, request)
            self.assertNotIn("capture_consent", wxdecipher._read_session(path))
            with self.assertRaises(WechatStoreError) as raised:
                wxdecipher.run_session(self.root, request)
            self.assertEqual("CAPTURE_CONSENT_REQUIRED", raised.exception.code)
            self.assertEqual(1, mocked.call_count)
        self.assertEqual(3, wxdecipher.run_session(self.root, {"session_id": session, "key": KEY_HEX})["decipher"]["messages"])

    def test_split_sealed_batch_survives_discovery_copy_replay(self):
        source_dir = self.root / "wxid_test"
        source_dir.mkdir()
        make_database(source_dir / "message_0.db")
        discovered = wxdecipher.discover_databases({"ownership_confirmed": True, "directory": str(source_dir)}, platform_name="win32")
        session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True,
            "self_username": SELF, "retain_copies": True})["session_id"]
        payload = {"session_id": session, "ownership_confirmed": True, "snapshot_confirmed": True,
                   "candidate_ids": [entry["candidate_id"] for group in discovered["groups"] for entry in group["files"]]}
        wxdecipher.import_discovered_databases(self.root, payload)
        wxdecipher.finish_copy(self.root, {"session_id": session, "file_count": 1})
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.import_discovered_databases(self.root, payload)
        self.assertEqual("COPY_SEALED", raised.exception.code)
        self.assertTrue(wxdecipher._session_path(self.root, session).exists())
        self.assertEqual(3, wxdecipher.run_session(self.root, {"session_id": session})["decipher"]["messages"])

    def test_split_cleanup_failure_does_not_hide_success_and_is_retried(self):
        session, path = self.copied_session()
        unlink = Path.unlink
        def fail_plain(selected, *args, **kwargs):
            if selected.name == "plain-0.db":
                raise PermissionError("synthetic lock")
            return unlink(selected, *args, **kwargs)
        with patch.object(Path, "unlink", fail_plain):
            result = wxdecipher.run_session(self.root, {"session_id": session})
            self.assertTrue(result["cleanup_pending"])
            self.assertEqual(3, result["decipher"]["messages"])
            self.assertFalse((path / "messages.jsonl").exists())
            self.assertTrue(wxdecipher._read_session(path)["cleanup_pending"])
            with self.assertRaises(WechatStoreError) as raised:
                wxdecipher.run_session(self.root, {"session_id": session})
            self.assertEqual("CLEANUP_PENDING", raised.exception.code)
        wxdecipher.cleanup_sessions(self.root)
        self.assertFalse((path / "plain-0.db").exists())
        self.assertFalse(wxdecipher._read_session(path)["cleanup_pending"])
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])

    def test_key_validation_and_modes_do_not_echo_keys(self):
        self.assertEqual(KEY, parse_key("  " + KEY_HEX.upper() + "  "))
        for value in (None, "", "x" * 64, "a" * 63, KEY_HEX + "extra", [KEY_HEX]):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(WechatStoreError) as raised:
                parse_key(value)
            self.assertNotIn(KEY_HEX, str(raised.exception))
        raw = derive_keys(KEY, b"0" * 16, "sqlcipher4-raw")
        password = derive_keys(KEY, b"0" * 16, "sqlcipher4")
        self.assertEqual(KEY, raw[0])
        self.assertNotEqual(raw, password)

    def test_tampered_pages_wrong_key_and_zero_pages_leave_no_output(self):
        source = encrypt_fixture(self.source, self.root / "valid.db")
        original = source.read_bytes()
        cases = {"salt": 0, "ciphertext": 4096 + 48, "iv": 4096 + 4016, "tag": 4096 + 4032}
        for name, offset in cases.items():
            with self.subTest(name=name):
                changed = bytearray(original); changed[offset] ^= 1
                bad = self.root / f"{name}.db"; bad.write_bytes(changed)
                destination = self.root / f"{name}-out.db"
                with self.assertRaises(WechatStoreError):
                    decrypt_database(bad, destination, KEY_HEX)
                self.assertFalse(destination.exists())
        for name, bad_bytes in (("zero", original[:4096] + bytes(4096) + original[8192:]),
                                ("swapped", original[:4096] + original[8192:12288] + original[4096:8192] + original[12288:]),
                                ("partial", original + b"truncated")):
            bad = self.root / f"{name}.db"; bad.write_bytes(bad_bytes)
            destination = self.root / f"{name}-out.db"
            with self.subTest(name=name), self.assertRaises(WechatStoreError):
                decrypt_database(bad, destination, KEY_HEX)
            self.assertFalse(destination.exists())
        with self.assertRaises(WechatStoreError):
            decrypt_database(source, self.root / "wrong-key.db", "ff" * 32)
        self.assertFalse((self.root / "wrong-key.db").exists())

    def test_existing_destination_and_source_are_not_overwritten(self):
        destination = self.root / "existing.db"; destination.write_bytes(b"keep me")
        with self.assertRaises(WechatStoreError):
            decrypt_database(self.source, destination)
        with self.assertRaises(WechatStoreError):
            decrypt_database(self.source, self.source)
        self.assertEqual(b"keep me", destination.read_bytes())

    def test_plaintext_readonly_and_wal_rejection(self):
        destination = self.root / "plain.db"
        report = decrypt_database(self.source, destination)
        self.assertEqual("plaintext", report["cipher_mode"])
        with open_readonly(destination) as connection:
            with self.assertRaises(sqlite3.Error):
                connection.execute("CREATE TABLE forbidden(x)")
        Path(str(self.source) + "-wal").write_bytes(b"uncommitted fixture")
        with self.assertRaises(WechatStoreError) as raised:
            decrypt_database(self.source, self.root / "wal-output.db")
        self.assertEqual("WAL_UNSUPPORTED", raised.exception.code)

    def test_invalid_plaintext_database_is_removed(self):
        broken = self.root / "broken.db"
        data = bytearray(self.source.read_bytes()); data[4096 + 1:4096 + 100] = b"x" * 99
        broken.write_bytes(data)
        output = self.root / "bad-plain.db"
        with self.assertRaises(WechatStoreError):
            decrypt_database(broken, output)
        self.assertFalse(output.exists())

    def test_reference_conversion_names_media_and_large_ids(self):
        output = self.root / "converted.jsonl"
        report = convert_databases([("message_0.db", self.source)], output, self_username=SELF)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(3, report["messages"])
        self.assertEqual("演示客户", rows[0]["conversation_name"])
        self.assertEqual("server:90071992547409931", rows[0]["message_id"])
        self.assertEqual("我", rows[1]["sender_name"])
        self.assertTrue(rows[1]["is_self"])
        self.assertEqual("[图片]", rows[2]["content"])

    def test_zstandard_content_wins_over_auxiliary_compress_column(self):
        path = self.root / "compressed.db"
        compressed = zstandard.ZstdCompressor().compress("主要正文，保留中文".encode())
        make_database(path, rows=[(1, 1, 1, 100, 1, EPOCH, compressed, b"\xff auxiliary data")])
        out = self.root / "compressed.jsonl"
        convert_databases([("message_0.db", path)], out)
        self.assertEqual("主要正文，保留中文", json.loads(out.read_text(encoding="utf-8"))["content"])

    def test_sort_seq_not_local_id_and_sender_not_assumed(self):
        path = self.root / "sorted.db"
        make_database(path, rows=[(1, 1, 1, 200, 99, EPOCH, "第二条", None), (2, 2, 1, 100, 99, EPOCH, "第一条", None)])
        out = self.root / "sorted.jsonl"
        report = convert_databases([("message_0.db", path)], out)
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["第一条", "第二条"], [row["content"] for row in rows])
        self.assertTrue(all(row["sender_name"] == "发送者未识别" and not row["is_self"] for row in rows))
        self.assertTrue(any("未识别" in value for value in report["warnings"]))

    def test_sender_resource_mapping_variant(self):
        message_db = make_database(self.root / "resource-message.db", names=False)
        resource = self.root / "message_resource.db"
        connection = sqlite3.connect(resource)
        connection.execute("CREATE TABLE SenderName2Id(user_name TEXT)")
        connection.executemany("INSERT INTO SenderName2Id(rowid,user_name) VALUES(?,?)", [(1, CLIENT), (2, SELF)])
        connection.commit(); connection.close()
        out = self.root / "resource.jsonl"
        convert_databases([("message_0.db", message_db), ("message_resource.db", resource)], out, self_username=SELF)
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(CLIENT, rows[0]["sender_id"])
        self.assertTrue(rows[1]["is_self"])

    def test_unknown_schema_and_missing_mappings_fail_closed(self):
        unknown = self.root / "unknown.db"
        connection = sqlite3.connect(unknown)
        connection.execute("CREATE TABLE messages(id INTEGER,time INTEGER,content TEXT,type INTEGER,username TEXT)")
        connection.execute("INSERT INTO messages VALUES(1,1,'not a known WeChat schema',1,'someone')")
        connection.commit(); connection.close()
        missing = make_database(self.root / "missing.db", names=False, contacts=False)
        for name, source in (("unknown", unknown), ("missing", missing)):
            out = self.root / (name + ".jsonl")
            with self.subTest(name=name), self.assertRaises(WechatStoreError):
                convert_databases([("message_0.db", source)], out)
            self.assertFalse(out.exists())

    def test_compression_bomb_and_xml_entities_are_rejected(self):
        for name, kind, body in (("bomb", 1, zstandard.ZstdCompressor().compress(b"x" * (1024 * 1024 + 1))),
                                 ("xml", 49, '<!DOCTYPE x [<!ENTITY boom "boom">]><msg><appmsg><title>&boom;</title></appmsg></msg>')):
            source = make_database(self.root / (name + ".db"), rows=[(1, 1, kind, 1, 1, EPOCH, body, None)])
            out = self.root / (name + ".jsonl")
            with self.subTest(name=name), self.assertRaises(WechatStoreError):
                convert_databases([("message_0.db", source)], out)
            self.assertFalse(out.exists())

    def test_session_requires_both_confirmations(self):
        for payload in ({}, {"ownership_confirmed": True}, {"snapshot_confirmed": True}):
            with self.assertRaises(WechatStoreError):
                wxdecipher.create_session(self.root, payload)
        self.assertFalse((self.root / "data").exists())

    def test_upload_invalid_names_duplicates_and_short_reads(self):
        session = self.create_session()
        for filename in ("../outside.db", "a/b.db", "C:\\file.db", "x.db-shm", "database.txt"):
            with self.subTest(filename=filename), self.assertRaises(WechatStoreError):
                wxdecipher.upload_database(self.root, session, filename, io.BytesIO(b"x" * 512), 512)
        with self.assertRaises(WechatStoreError):
            wxdecipher.upload_database(self.root, session, "short.db", io.BytesIO(b"short"), 512)
        self.upload(session)
        with self.assertRaises(WechatStoreError):
            self.upload(session, name="MESSAGE_0.DB")
        with self.assertRaises(WechatStoreError):
            wxdecipher.upload_database(self.root, "../outside", "message.db", io.BytesIO(b"x" * 512), 512)
        wxdecipher.discard_session(self.root, session)

    def test_successful_encrypted_import_is_atomic_local_and_deduplicated(self):
        cipher = encrypt_fixture(self.source, self.root / "encrypted.db")
        digest = hashlib.sha256(cipher.read_bytes()).hexdigest()
        result = self.run_import(cipher, key=KEY_HEX)
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        self.assertEqual("sqlcipher4-raw", result["decipher"]["databases"][0]["cipher_mode"])
        self.assertNotIn(KEY_HEX, json.dumps(result, ensure_ascii=False))
        self.assertEqual([], list((self.root / "data/wechat/decipher").iterdir()))
        self.assertEqual(digest, hashlib.sha256(cipher.read_bytes()).hexdigest())
        second = self.run_import(cipher, key=KEY_HEX)
        self.assertTrue(second["duplicate_file"])
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        # Display labels are no longer the identity: relabeling cannot duplicate.
        self.run_import(cipher, key=KEY_HEX, account="另一个显式账号标记")
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        # Different explicit self IDs remain isolated even with the same label.
        self.run_import(cipher, key=KEY_HEX, self_username="wxid_other_synthetic_self")
        self.assertEqual(6, wechat_store.dashboard(self.root)["message_count"])
        self.assertEqual(2, wechat_store.dashboard(self.root)["conversation_count"])
        accounts = wechat_store.list_conversations(self.root)["rows"]
        self.assertEqual({SELF, "wxid_other_synthetic_self"}, {row["account_id"] for row in accounts})
        self.assertTrue(all(row["account_label"] for row in accounts))

    def test_database_import_requires_explicit_stable_account_id(self):
        with self.assertRaises(WechatStoreError):
            self.create_session(self_username="")

    def test_sqlite_sort_temporaries_stay_in_memory(self):
        source = make_database(self.root / "sort-many.db", rows=[
            (index, index, 1, 10001 - index, 1, EPOCH + index, "合成排序消息" * 80, None)
            for index in range(1, 10001)
        ])
        with open_readonly(source) as connection:
            self.assertEqual(2, connection.execute("PRAGMA temp_store").fetchone()[0])
            table = table_for(CLIENT)
            plan = connection.execute(f'SELECT * FROM "{table}" ORDER BY sort_seq,local_id')
            self.assertEqual(10000, sum(1 for _ in plan))

    def test_renamed_snapshot_without_server_ids_does_not_duplicate(self):
        source = make_database(self.root / "without-server.db", rows=[(1, 0, 1, 10, 1, EPOCH, "合成无服务器编号消息", None)])
        self.run_import(source)
        session = self.create_session()
        self.upload(session, source, name="renamed.db")
        wxdecipher.run_session(self.root, {"session_id": session})
        self.assertEqual(1, wechat_store.dashboard(self.root)["message_count"])

    def test_export_carries_account_identity_and_rejects_wrong_account_override(self):
        self.run_import()
        conversation = wechat_store.list_conversations(self.root)["rows"][0]["conversation_id"]
        exported = wechat_store.export_selection(self.root, {"conversation_ids": [conversation], "format": "jsonl"})
        self.assertEqual(SELF, json.loads(exported["content"].splitlines()[0])["account_id"])
        source = self.root / "account-bound.jsonl"
        source.write_text(exported["content"], encoding="utf-8")
        wechat_store.import_export(self.root, source, source_name=source.name, ownership_confirmed=True)
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        with self.assertRaises(WechatStoreError) as error:
            wechat_store.import_export(self.root, source, source_name=source.name, account_id="wxid_wrong", ownership_confirmed=True)
        self.assertEqual("ACCOUNT_MISMATCH", error.exception.code)

    def test_adding_optional_sender_map_does_not_change_fallback_identity(self):
        source = make_database(self.root / "without-names.db", names=False,
                               rows=[(1, 0, 1, 10, 1, EPOCH, "同一条合成消息", None)])
        self.run_import(source)
        resource = self.root / "resource.db"
        connection = sqlite3.connect(resource)
        try:
            connection.execute("CREATE TABLE SenderName2Id(user_name TEXT)")
            connection.execute("INSERT INTO SenderName2Id(rowid,user_name) VALUES(1,?)", (CLIENT,))
            connection.commit()
        finally:
            connection.close()
        session = self.create_session()
        self.upload(session, source)
        self.upload(session, resource, name="message_resource.db")
        result = wxdecipher.run_session(self.root, {"session_id": session})
        self.assertEqual(0, result["message_count"])
        self.assertEqual(1, wechat_store.dashboard(self.root)["message_count"])

    def test_crash_before_batch_commit_leaves_only_expiring_orphan(self):
        source = self.root / "crash.jsonl"
        source.write_text(json.dumps({"conversation": CLIENT, "content": "synthetic-only"}) + "\n", encoding="utf-8")
        script = """import os,sys
from agent_platform import wechat_store as w
copy = w._copy_raw
def crash(*args):
    copy(*args)
    os._exit(77)
w._copy_raw = crash
w.import_export(sys.argv[1],sys.argv[2],source_name='crash.jsonl',ownership_confirmed=True)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.root), str(source)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
        self.assertEqual(77, result.returncode, result.stderr.decode(errors="replace"))
        orphans = list((self.root / "data/wechat/imports").glob("wechat-batch-*/crash.jsonl"))
        self.assertEqual(1, len(orphans))
        self.assertEqual(0, wechat_store.cleanup_expired(self.root)["purged_files"])
        from datetime import timedelta
        result = wechat_store.cleanup_expired(self.root, reference=wechat_store._now() + timedelta(days=8))
        self.assertEqual(1, result["purged_files"])
        self.assertFalse(orphans[0].exists())
        self.assertTrue(source.exists())

    def test_per_file_key_overrides_common_key(self):
        cipher = encrypt_fixture(self.source, self.root / "per-file.db")
        result = self.run_import(cipher, key="ff" * 32, file_keys={"message_0.db": KEY_HEX})
        self.assertEqual(3, result["decipher"]["messages"])

    def test_failed_batch_cleans_all_staging_and_keeps_index_untouched(self):
        self.run_import()
        session = self.create_session()
        self.upload(session)
        cipher = encrypt_fixture(self.source, self.root / "bad-key.db")
        self.upload(session, cipher, name="message_1.db")
        with self.assertRaises(WechatStoreError):
            wxdecipher.run_session(self.root, {"session_id": session, "key": "ff" * 32})
        self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        self.assertFalse((self.root / "data/wechat/decipher" / session).exists())

    def test_modified_staging_is_rejected_and_cleared(self):
        session = self.create_session(); self.upload(session)
        path = self.root / "data/wechat/decipher" / session
        staged = next(path.glob("db-*.db"))
        content = bytearray(staged.read_bytes()); content[-1] ^= 1; staged.write_bytes(content)
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.run_session(self.root, {"session_id": session})
        self.assertEqual("FILE_CHANGED", raised.exception.code)
        self.assertFalse(path.exists())

    def test_expiry_discard_and_busy_guard(self):
        session = self.create_session(); self.upload(session)
        with patch("agent_platform.wxdecipher.time.time", return_value=time.time() + 1900):
            self.assertEqual(1, wxdecipher.cleanup_sessions(self.root))
        self.assertEqual(0, wxdecipher.cleanup_sessions(self.root))
        wxdecipher.discard_session(self.root, session)  # idempotent
        with wxdecipher._LOCK:
            with self.assertRaises(WechatStoreError) as raised:
                self.create_session()
            self.assertEqual("DECIPHER_BUSY", raised.exception.code)
            self.assertEqual(0, wxdecipher.cleanup_sessions(self.root))

    def test_windows_database_discovery_returns_opaque_group_and_imports_selected_files(self):
        home = self.root / "user"
        database_root = home / "Documents" / "xwechat_files" / "wxid_synthetic_account" / "db_storage"
        (database_root / "message").mkdir(parents=True)
        (database_root / "contact").mkdir()
        message = database_root / "message" / "message_0.db"
        contact = database_root / "contact" / "contact.db"
        message.write_bytes(self.source.read_bytes())
        contact.write_bytes(self.source.read_bytes())
        (database_root / "message" / "ignored.db-shm").write_bytes(b"ignored")

        result = wxdecipher.discover_databases(
            {"ownership_confirmed": True}, platform_name="win32", home=home,
            environ={"USERPROFILE": str(home)},
        )
        self.assertEqual("windows", result["platform"])
        self.assertEqual(1, len(result["groups"]))
        group = result["groups"][0]
        self.assertEqual({"contact.db", "message_0.db"}, {item["name"] for item in group["files"]})
        self.assertNotIn(str(home), json.dumps(result, ensure_ascii=False))
        self.assertTrue(all(len(item["candidate_id"]) == 32 for item in group["files"]))

        session = self.create_session()
        imported = wxdecipher.import_discovered_databases(self.root, {
            "session_id": session,
            "candidate_ids": [item["candidate_id"] for item in group["files"]],
            "ownership_confirmed": True,
            "snapshot_confirmed": True,
        })
        self.assertEqual(2, imported["file_count"])
        manifest = wxdecipher._read_session(wxdecipher._session_path(self.root, session))
        self.assertEqual({"contact.db", "message_0.db"}, {item["source_name"] for item in manifest["files"]})
        self.assertEqual(self.source.read_bytes(), message.read_bytes(), "Automatic import must not modify the source")
        wxdecipher.discard_session(self.root, session)

    def test_macos_database_discovery_uses_container_roots_and_rejects_changed_source(self):
        home = self.root / "mac-user"
        database_root = home / "Library" / "Containers" / "com.tencent.xinWeChat" / "Data" / "Documents" / "xwechat_files" / "wxid_mac" / "db_storage" / "message"
        database_root.mkdir(parents=True)
        source = database_root / "message_0.db"
        source.write_bytes(self.source.read_bytes())
        result = wxdecipher.discover_databases(
            {"ownership_confirmed": True}, platform_name="darwin", home=home, environ={},
        )
        self.assertEqual("macos", result["platform"])
        candidate = result["groups"][0]["files"][0]["candidate_id"]
        source.write_bytes(source.read_bytes() + b"changed")
        session = self.create_session()
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.import_discovered_databases(self.root, {
                "session_id": session, "candidate_ids": [candidate],
                "ownership_confirmed": True, "snapshot_confirmed": True,
            })
        self.assertEqual("SOURCE_CHANGED", raised.exception.code)
        self.assertFalse((self.root / "data" / "wechat" / "decipher" / session).exists())

    def test_database_discovery_requires_consent_and_supported_host(self):
        with patch.object(wxdecipher, "_scan_discovery_roots", side_effect=AssertionError("must not scan")):
            with self.assertRaises(WechatStoreError) as raised:
                wxdecipher.discover_databases({}, platform_name="win32", home=self.root, environ={})
        self.assertEqual("AUTHORIZATION_REQUIRED", raised.exception.code)
        with self.assertRaises(WechatStoreError) as raised:
            wxdecipher.discover_databases({"ownership_confirmed": True}, platform_name="linux", home=self.root, environ={})
        self.assertEqual("DISCOVERY_UNSUPPORTED", raised.exception.code)

    def test_manual_account_directory_discovers_nested_databases_as_one_group(self):
        account = self.root / "custom location" / "wxid_synthetic_100a"
        for folder, filename in (("message", "message_0.db"), ("contact", "contact.db")):
            target = account / "db_storage" / folder / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.source.read_bytes())
        wal = account / "db_storage" / "message" / "message_0.db-wal"
        wal.write_bytes(b"")
        with patch.object(wxdecipher, "_discovery_roots", side_effect=AssertionError("manual search must only use the selected root")):
            result = wxdecipher.discover_databases({"ownership_confirmed": True, "directory": '"' + str(account) + '"'}, platform_name="win32")
        self.assertEqual(1, len(result["groups"]))
        self.assertEqual({"message_0.db", "message_0.db-wal", "contact.db"}, {file["name"] for file in result["groups"][0]["files"]})
        self.assertIn("指定目录", result["message"])
        session = self.create_session()
        response = wxdecipher.import_discovered_databases(self.root, {
            "ownership_confirmed": True, "snapshot_confirmed": True, "session_id": session,
            "candidate_ids": [file["candidate_id"] for file in result["groups"][0]["files"]],
        })
        self.assertEqual(3, response["file_count"])
        wxdecipher.discard_session(self.root, session)

    def test_manual_directory_errors_and_empty_results(self):
        for value in ("", "relative/folder", str(self.root / "missing"), str(self.source), str(Path(self.root.anchor)), [], "//server/share"):
            with self.subTest(value=value), self.assertRaises(WechatStoreError) as raised:
                wxdecipher.discover_databases({"ownership_confirmed": True, "directory": value}, platform_name="win32")
            self.assertEqual("INVALID_DIRECTORY", raised.exception.code)
        empty = self.root / "empty-account"
        empty.mkdir()
        result = wxdecipher.discover_databases({"ownership_confirmed": True, "directory": str(empty)}, platform_name="darwin")
        self.assertEqual([], result["groups"])

    def test_exports_are_selected_lossless_and_roundtrip_without_duplicates(self):
        self.run_import()
        conversation = wechat_store.list_conversations(self.root)["rows"][0]["conversation_id"]
        for format_name in ("json", "jsonl", "csv"):
            export = wechat_store.export_selection(self.root, {"conversation_ids": [conversation], "format": format_name})
            self.assertEqual(3, export["message_count"])
            if format_name == "csv":
                rows = list(csv.DictReader(io.StringIO(export["content"].lstrip("\ufeff"))))
            else:
                rows = json.loads(export["content"]) if format_name == "json" else [json.loads(line) for line in export["content"].splitlines()]
            self.assertEqual(CLIENT, rows[0]["conversation"])
            self.assertEqual("server:90071992547409931", rows[0]["message_id"])
            source = self.root / ("roundtrip." + format_name); source.write_text(export["content"], encoding="utf-8")
            wechat_store.import_export(self.root, source, source_name=source.name, account_label="合成测试", account_id=SELF, ownership_confirmed=True)
            self.assertEqual(3, wechat_store.dashboard(self.root)["message_count"])
        filtered = wechat_store.export_selection(self.root, {"conversation_ids": [conversation], "query": "报价"})
        self.assertEqual(1, filtered["message_count"])
        with self.assertRaises(WechatStoreError):
            wechat_store.export_selection(self.root, {"conversation_ids": []})
        with patch.object(wechat_store, "MAX_EXPORT_ROWS", 1), self.assertRaises(WechatStoreError):
            wechat_store.export_selection(self.root, {"conversation_ids": [conversation]})
        with patch.object(wechat_store, "MAX_EXPORT_BYTES", 10), self.assertRaises(WechatStoreError):
            wechat_store.export_selection(self.root, {"conversation_ids": [conversation]})

    def test_csv_formula_content_is_escaped_but_json_is_not(self):
        source = make_database(self.root / "formula.db", rows=[(1, 1, 1, 1, 1, EPOCH, '=HYPERLINK("https://invalid.example")', None)])
        self.run_import(source)
        conversation = wechat_store.list_conversations(self.root)["rows"][0]["conversation_id"]
        exported = wechat_store.export_selection(self.root, {"conversation_ids": [conversation], "format": "csv"})
        row = next(csv.DictReader(io.StringIO(exported["content"].lstrip("\ufeff"))))
        self.assertTrue(row["content"].startswith("'="))
        json_export = wechat_store.export_selection(self.root, {"conversation_ids": [conversation], "format": "json"})
        self.assertTrue(json.loads(json_export["content"])[0]["content"].startswith("="))

    def test_expired_messages_are_not_exported_without_dashboard_refresh(self):
        self.run_import()
        conversation = wechat_store.list_conversations(self.root)["rows"][0]["conversation_id"]
        connection = sqlite3.connect(wechat_store.database_path(self.root))
        connection.execute("UPDATE messages SET purge_at='2000-01-01T00:00:00Z'")
        connection.commit(); connection.close()
        with self.assertRaises(WechatStoreError) as raised:
            wechat_store.export_selection(self.root, {"conversation_ids": [conversation]})
        self.assertEqual("EMPTY_SCOPE", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
