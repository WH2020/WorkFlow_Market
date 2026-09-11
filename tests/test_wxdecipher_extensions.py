"""Synthetic WAL/media/process fixtures. Never enumerate or read real WeChat."""
from __future__ import annotations

from contextlib import closing
import ctypes
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from Crypto.Cipher import AES
from PIL import Image

from agent_platform import wxdecipher, wxdecipher_capture as capture, wxdecipher_media as media
from agent_platform.wechat_store import WechatStoreError
from agent_platform.wxdecipher_crypto import decrypt_database, open_readonly
from agent_platform.wxdecipher_wal import replay_wal
from tests.wxdecipher_fixture import CLIENT, EPOCH, KEY, KEY_HEX, SELF, make_database, reserved_plaintext, encrypt_fixture, table_for


SALT = bytes(range(16, 32))


def checksum_ref(data, big=False, state=(0, 0)):
    numbers = [int.from_bytes(data[index:index + 4], "big" if big else "little") for index in range(0, len(data), 4)]
    a, b = state
    for index in range(0, len(numbers), 2):
        a = (a + b + numbers[index]) % 2 ** 32
        b = (b + a + numbers[index + 1]) % 2 ** 32
    return a, b


def encode_page(page, number, salt=SALT):
    start = 16 if number == 1 else 0
    iv = hashlib.sha256(b"wal-fixture-iv" + page + number.to_bytes(4, "little")).digest()[:16]
    ciphertext = AES.new(KEY, AES.MODE_CBC, iv).encrypt(page[start:4016])
    mac_key = hashlib.pbkdf2_hmac("sha512", KEY, bytes(value ^ 0x3a for value in salt), 2, 32)
    mac = hmac.new(mac_key, ciphertext + iv + number.to_bytes(4, "little"), hashlib.sha512).digest()
    return (salt if number == 1 else b"") + ciphertext + iv + mac


def wal_parts(data):
    page_size = int.from_bytes(data[8:12], "big")
    return data[:32], [(data[pos:pos + 24], data[pos + 24:pos + 24 + page_size])
                      for pos in range(32, len(data), page_size + 24)]


def build_wal(header, frames, *, big=None, encrypted=False):
    if big is None:
        big = int.from_bytes(header[:4], "big") == 0x377f0683
    first = (0x377f0683 if big else 0x377f0682).to_bytes(4, "big") + header[4:24]
    state = checksum_ref(first, big)
    result = bytearray(first + struct.pack(">II", *state))
    for frame_header, page in frames:
        pgno = int.from_bytes(frame_header[:4], "big")
        page = encode_page(page, pgno) if encrypted else page
        state = checksum_ref(frame_header[:8] + page, big, state)
        result.extend(frame_header[:16] + struct.pack(">II", *state) + page)
    return bytes(result)


def sqlite_wal_fixture(root: Path, *, reserve=False, count=12, conversation=CLIENT):
    base = make_database(root / "seed.db", conversation=conversation)
    live = root / "live.db"
    if reserve:
        reserved_plaintext(base, live)
    else:
        shutil.copyfile(base, live)
    connection = sqlite3.connect(live)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # test-owned DB only
        for index in range(4, count + 4):
            connection.execute(f'INSERT INTO "{table_for(conversation)}" VALUES(?,?,?,?,?,?,?,?)',
                               (index, 10000 + index, 1, index + 1000, 1, EPOCH + index, "合成 WAL 增长消息" + "x" * 2400, None))
        connection.commit()
        connection.execute(f'UPDATE "{table_for(conversation)}" SET message_content=? WHERE local_id=1', ("第一次提交更新",))
        connection.commit()
        connection.execute(f'UPDATE "{table_for(conversation)}" SET message_content=? WHERE local_id=1', ("最后一次已提交更新",))
        connection.commit()
        snapshot, wal = root / "snapshot.db", root / "selected.wal"
        shutil.copyfile(live, snapshot)
        shutil.copyfile(Path(str(live) + "-wal"), wal)
    finally:
        connection.close()
    return snapshot, wal


class WalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wxdecipher-test-", dir=Path.home())
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_rows(self, output, count):
        with open_readonly(output) as connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual(count, connection.execute(f'SELECT count(*) FROM "{table_for(CLIENT)}"').fetchone()[0])
            self.assertEqual("最后一次已提交更新", connection.execute(f'SELECT message_content FROM "{table_for(CLIENT)}" WHERE local_id=1').fetchone()[0])
        self.assertEqual(b"\x01\x01", output.read_bytes()[18:20])

    def test_real_sqlite_commits_growth_duplicate_pages_and_both_checksum_endians(self):
        source, wal = sqlite_wal_fixture(self.root)
        before = (source.read_bytes(), wal.read_bytes(), source.stat().st_mtime_ns, wal.stat().st_mtime_ns)
        header, frames = wal_parts(wal.read_bytes())
        self.assertEqual(wal.read_bytes(), build_wal(header, frames), "Independent checksum oracle agrees with SQLite")
        for big in (False, True):
            with self.subTest(big=big):
                selected = self.root / f"selected-{big}.wal"; selected.write_bytes(build_wal(header, frames, big=big))
                merged = self.root / f"merged-{big}.db"
                report = replay_wal(source, selected, merged)
                plain = self.root / f"plain-{big}.db"; decrypt_database(merged, plain)
                self.assert_rows(plain, 15)
                self.assertGreater(merged.stat().st_size, source.stat().st_size)
                self.assertEqual(report["database_pages"] * 4096, merged.stat().st_size)
                self.assertEqual(len(frames), report["committed_frames"])
        self.assertEqual(before, (source.read_bytes(), wal.read_bytes(), source.stat().st_mtime_ns, wal.stat().st_mtime_ns))

    def test_encrypted_wal_pages_and_page_one_use_real_page_numbers_and_salt(self):
        source, wal = sqlite_wal_fixture(self.root, reserve=True)
        plain_main = source.read_bytes()
        cipher = self.root / "encrypted.db"
        cipher.write_bytes(b"".join(encode_page(plain_main[index:index + 4096], index // 4096 + 1) for index in range(0, len(plain_main), 4096)))
        header, frames = wal_parts(wal.read_bytes())
        self.assertTrue(any(int.from_bytes(item[0][:4], "big") == 1 for item in frames))
        for big in (False, True):
            with self.subTest(big=big):
                log = self.root / f"encrypted-{big}.wal"; log.write_bytes(build_wal(header, frames, big=big, encrypted=True))
                merged = self.root / f"merged-{big}.db"; replay_wal(cipher, log, merged)
                plain = self.root / f"plain-{big}.db"; decrypt_database(merged, plain, KEY_HEX)
                self.assert_rows(plain, 15)

    def test_real_sqlite_autovacuum_shrink_truncates_exactly(self):
        live = self.root / "shrink-live.db"
        with closing(sqlite3.connect(live)) as connection:
            connection.execute("PRAGMA auto_vacuum=FULL")
            connection.execute("VACUUM")
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value BLOB)")
            connection.executemany("INSERT INTO items(value) VALUES(?)", [(b"x" * 3000,) for _ in range(80)])
            connection.commit()
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            source = self.root / "shrink.db"; shutil.copyfile(live, source)
            connection.execute("DELETE FROM items WHERE id>2"); connection.commit()
            wal = self.root / "shrink.wal"; shutil.copyfile(Path(str(live) + "-wal"), wal)
        merged = self.root / "shrunk.db"; report = replay_wal(source, wal, merged)
        self.assertLess(merged.stat().st_size, source.stat().st_size)
        self.assertEqual(report["database_pages"] * 4096, merged.stat().st_size)
        with open_readonly(merged) as connection:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual(2, connection.execute("SELECT count(*) FROM items").fetchone()[0])

    def test_stops_at_first_invalid_tail_and_ignores_uncommitted_frames(self):
        source, wal = sqlite_wal_fixture(self.root)
        header, frames = wal_parts(wal.read_bytes())
        uncommitted_header = frames[-1][0][:4] + bytes(4) + frames[-1][0][8:]
        valid_uncommitted = build_wal(header, frames + [(uncommitted_header, frames[-1][1])])
        cases = {
            "partial": wal.read_bytes() + b"partial",
            "uncommitted": valid_uncommitted,
            "old-salt": wal.read_bytes() + frames[-1][0][:8] + b"outdated" + frames[-1][0][16:] + frames[-1][1],
            "bad-checksum": wal.read_bytes() + frames[-1][0] + frames[-1][1],
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                selected = self.root / (name + ".wal"); selected.write_bytes(content)
                merged = self.root / (name + ".db"); report = replay_wal(source, selected, merged)
                self.assertEqual(len(frames), report["committed_frames"])
                self.assertGreater(report["ignored_tail_bytes"], 0)
                self.assertTrue(report["warnings"])
                self.assert_rows(merged, 15)

    def test_header_corruption_growth_holes_and_revival_after_shrink_fail_closed(self):
        source, wal = sqlite_wal_fixture(self.root)
        header, frames = wal_parts(wal.read_bytes())
        main_pages = source.stat().st_size // 4096
        first = frames[0]
        holes = [(struct.pack(">II", 1, main_pages + 2) + first[0][8:], source.read_bytes()[:4096])]
        revival = [(struct.pack(">II", 1, size) + first[0][8:], source.read_bytes()[:4096]) for size in (1, 2)]
        cases = [b"short", bytes(32), bytes([wal.read_bytes()[0] ^ 1]) + wal.read_bytes()[1:],
                 build_wal(header, holes), build_wal(header, revival)]
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                selected = self.root / f"bad-{index}.wal"; selected.write_bytes(content)
                output = self.root / f"bad-{index}.db"
                with self.assertRaises(WechatStoreError): replay_wal(source, selected, output)
                self.assertFalse(output.exists())

    def test_empty_header_only_and_no_commit_are_explicit_noops(self):
        source, wal = sqlite_wal_fixture(self.root)
        header, frames = wal_parts(wal.read_bytes())
        no_commit = [(item[0][:4] + bytes(4) + item[0][8:], item[1]) for item in frames]
        for index, data in enumerate((b"", header, build_wal(header, no_commit))):
            selected = self.root / f"empty-{index}.wal"; selected.write_bytes(data)
            output = self.root / f"empty-{index}.db"; report = replay_wal(source, selected, output)
            self.assertEqual(0, report["committed_frames"])
            self.assertEqual(0, report["applied_pages"])
            with open_readonly(output) as connection:
                self.assertEqual(3, connection.execute(f'SELECT count(*) FROM "{table_for(CLIENT)}"').fetchone()[0])

    def test_session_wal_pairing_consent_and_import(self):
        source, wal = sqlite_wal_fixture(self.root, count=2)
        for consent in (False, True):
            session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True, "self_username": SELF})["session_id"]
            for name, path in (("message_0.db", source), ("message_0.db-wal", wal)):
                with path.open("rb") as reader: wxdecipher.upload_database(self.root, session, name, reader, path.stat().st_size)
            if consent:
                result = wxdecipher.run_session(self.root, {"session_id": session, "wal_replay_confirmed": True})
                self.assertEqual(5, result["decipher"]["messages"])
                self.assertTrue(result["decipher"]["databases"][0]["wal"]["verified"])
            else:
                with self.assertRaises(WechatStoreError) as raised: wxdecipher.run_session(self.root, {"session_id": session})
                self.assertEqual("AUTHORIZATION_REQUIRED", raised.exception.code)
            self.assertFalse((self.root / "data/wechat/decipher" / session).exists())


def image_fixture(format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (24, 16), (37, 110, 190)).save(buffer, format=format)
    return buffer.getvalue()


def v2_fixture(data, *, key=b"0123456789abcdef", xor=0x37, prefix_size=32, tail_size=16):
    prefix = data[:prefix_size]
    padding = 16 - len(prefix) % 16
    encrypted = AES.new(key, AES.MODE_ECB).encrypt(prefix + bytes([padding]) * padding)
    return media.V2_MAGIC + struct.pack("<II", prefix_size, tail_size) + b"\0" + encrypted + data[prefix_size:len(data) - tail_size] + bytes(value ^ xor for value in data[len(data) - tail_size:])


class MediaTests(unittest.TestCase):
    def test_plain_and_legacy_xor_images_are_fully_decoded(self):
        for format in ("PNG", "JPEG", "GIF", "BMP", "WEBP"):
            for xor in (0, 1, 0x88, 255):
                with self.subTest(format=format, xor=xor):
                    plain = image_fixture(format)
                    raw, report = media.decode_media(bytes(value ^ xor for value in plain))
                    self.assertEqual(plain, raw)
                    self.assertTrue(report["previewable"])
                    self.assertEqual(24, report["width"])

    def test_v2_full_padding_and_tail_inference_require_valid_result(self):
        for format in ("PNG", "JPEG"):
            for size in (16, 31, 32):
                with self.subTest(format=format, size=size):
                    plain = image_fixture(format)
                    raw, report = media.decode_media(v2_fixture(plain, prefix_size=size), image_key="0123456789abcdef")
                    self.assertEqual(plain, raw)
                    self.assertEqual("v2-aes-xor", report["decoder"])
        raw = image_fixture("GIF")
        with self.assertRaises(WechatStoreError) as raised:
            media.decode_media(v2_fixture(raw, tail_size=2), image_key="0123456789abcdef")
        self.assertEqual("IMAGE_XOR_REQUIRED", raised.exception.code)
        recovered, _ = media.decode_media(v2_fixture(raw, tail_size=2), image_key=b"0123456789abcdef".hex(), xor_key=0x37)
        self.assertEqual(raw, recovered)

    def test_wrong_keys_truncated_segments_bad_images_and_large_pixels_fail(self):
        good = v2_fixture(image_fixture())
        corrupt_length = good[:6] + struct.pack("<II", 0xFFFFFFFF, 1) + good[14:]
        cases = [(good, "f" * 16), (good[:-20], "0123456789abcdef"), (corrupt_length, "0123456789abcdef"),
                 (b"<svg onload=alert(1)>", ""), (b"\x89PNG\r\n\x1a\nnot-an-image", ""), (b"\x07\x08V3\x08\x07", "")]
        for data, key in cases:
            with self.subTest(size=len(data)), self.assertRaises(WechatStoreError): media.decode_media(data, image_key=key)
        with patch.object(media, "MAX_PIXELS", 10), self.assertRaises(WechatStoreError): media.decode_media(image_fixture())
        for bad in (True, -1, 256, "0x88", [], "1234"):
            with self.subTest(xor=repr(bad)), self.assertRaises(WechatStoreError): media.parse_xor_key(bad)

    def test_local_media_types_do_not_claim_audio_video_decoding(self):
        silk = b"\x02#!SILK_V3" + bytes(16)
        raw, report = media.decode_media(silk)
        self.assertEqual(silk, raw); self.assertFalse(report["previewable"])
        self.assertEqual("signature-only", report["validation"])
        mp4 = b"".join(struct.pack(">I4s", 12, name) + bytes(4) for name in (b"ftyp", b"moov", b"mdat"))
        self.assertEqual("container-only", media.decode_media(mp4)[1]["validation"])
        with self.assertRaises(WechatStoreError): media.decode_media(mp4[:-1])
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(24000); sound.writeframes(bytes(200))
        self.assertEqual("pcm-length", media.decode_media(buffer.getvalue())[1]["validation"])

    def test_media_request_consent_short_read_limits_and_no_persistence(self):
        content = image_fixture()
        for length, confirmed in ((len(content), False), (len(content) + 1, True), (media.MAX_MEDIA_BYTES + 1, True)):
            with self.assertRaises(WechatStoreError):
                with media.restore_upload(io.BytesIO(content), length, ownership_confirmed=confirmed): pass
        with media.restore_upload(io.BytesIO(content), len(content), ownership_confirmed=True) as (raw, result):
            self.assertEqual(content, raw)
            self.assertEqual(hashlib.sha256(content).hexdigest(), result["sha256"])
            with self.assertRaises(WechatStoreError) as raised:
                with media.restore_upload(io.BytesIO(content), len(content), ownership_confirmed=True): pass
            self.assertEqual("DECIPHER_BUSY", raised.exception.code)
        self.assertFalse(media._LOCK.locked())


class FakeProcess:
    def __init__(self, data, *, base=0x10000, changing=False):
        self.data, self.base, self.changing = bytes(data), base, changing
        self.checked = False

    def regions(self):
        yield self.base, len(self.data), 0x20000

    def read(self, address, length):
        offset = address - self.base
        if self.changing and length < len(self.data): return None
        return self.data[offset:offset + length] if offset >= 0 and offset + length <= len(self.data) else None

    def check_identity(self): self.checked = True


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wxdecipher-test-", dir=Path.home()); self.root = Path(self.temporary.name)
        self.database = encrypt_fixture(make_database(self.root / "seed.db"), self.root / "cipher.db")
        self.page = self.database.read_bytes()[:4096]

    def tearDown(self): self.temporary.cleanup()

    def test_raw_and_utf16_literals_require_closed_exact_lengths_and_matching_salt(self):
        for wide in (False, True):
            for explicit in (False, True):
                text = "x'" + KEY_HEX + (SALT.hex() if explicit else "") + "'"
                data = b"\0\0" + (text.encode("utf-16-le") if wide else text.encode()) + b"\0\0"
                process = FakeProcess(data)
                keys, report = capture.scan_keys(process, {"uploaded.db": self.page})
                self.assertEqual(KEY_HEX, keys["uploaded.db"])
                self.assertEqual(1, report["verified_databases"])
                self.assertNotIn(KEY_HEX, json.dumps(report)); self.assertTrue(process.checked)
        for invalid in (f"x'{KEY_HEX}", f"x'{KEY_HEX}0'", f"x'{KEY_HEX}{'ff' * 16}'", "x'" + "ff" * 32 + "'"):
            with self.subTest(invalid_length=len(invalid)), self.assertRaises(WechatStoreError): capture.scan_keys(FakeProcess(invalid.encode()), {"db": self.page})

    def test_experimental_wcdb_layout_requires_stable_bounded_pointer_chain(self):
        data = bytearray(32768); base = 0x10000
        marker, node, config, blob = base + 64, base + 4096, base + 8192, base + 16384
        data[64:64 + len(capture.MARKER)] = capture.MARKER
        struct.pack_into("<QQ", data, node - base + 0x10, marker, len(capture.MARKER))
        struct.pack_into("<Q", data, node - base + 0x28, config)
        literal = ("x'" + KEY_HEX + SALT.hex() + "'").encode()
        encoded = bytes(value ^ capture.CONFIG_MASK[index % len(capture.CONFIG_MASK)] for index, value in enumerate(literal))
        struct.pack_into("<QQ", data, config - base + 0x88 + 8, blob, len(encoded))
        data[blob - base:blob - base + len(encoded)] = encoded
        keys, report = capture.scan_keys(FakeProcess(data), {"db": self.page})
        self.assertEqual(KEY_HEX, keys["db"])
        self.assertEqual(["experimental-wcdb-x64"], report["methods"])
        for offset, value in ((node - base + 0x28, 0xFFFFFFFFFFFFFFF0), (config - base + 0x88 + 16, 0xFFFFFFFF), (config - base + 0x88 + 8, 0)):
            broken = bytearray(data); struct.pack_into("<Q", broken, offset, value)
            with self.assertRaises(WechatStoreError): capture.scan_keys(FakeProcess(broken), {"db": self.page})

    def test_short_reads_timeout_budget_and_header_mismatch_fail_without_keys(self):
        data = ("before x'" + KEY_HEX + "' after").encode()
        with self.assertRaises(WechatStoreError): capture.scan_keys(FakeProcess(data, changing=True), {"db": self.page})
        with patch.object(capture, "MAX_SCAN_BYTES", 1), self.assertRaises(WechatStoreError) as raised:
            capture.scan_keys(FakeProcess(data), {"db": self.page})
        self.assertEqual("CAPTURE_LIMIT", raised.exception.code)
        times = iter([0, 31, 32, 33])
        with self.assertRaises(WechatStoreError): capture.scan_keys(FakeProcess(data), {"db": self.page}, clock=lambda: next(times))
        for pid, created in ((True, "123"), (0, "123"), (4, 123), (4, ""), (2 ** 32, "1")):
            with self.assertRaises(WechatStoreError): capture._validate_identity(pid, created)

    def test_capture_consent_and_run_keep_keys_inside_request_and_cleanup(self):
        with patch.object(capture, "_WindowsProcess", side_effect=AssertionError("No process access without consent")):
            with self.assertRaises(WechatStoreError): capture.capture_keys({"db": self.database}, {})
            with self.assertRaises(WechatStoreError): capture.list_processes({})
        session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True, "self_username": SELF})["session_id"]
        with self.database.open("rb") as reader: wxdecipher.upload_database(self.root, session, "message_0.db", reader, self.database.stat().st_size)
        selected = {"confirmed": True, "process_id": 123, "created_at": "456"}
        consent = wxdecipher.issue_capture_consent(self.root, {"session_id": session, **selected})
        with patch.object(capture, "capture_keys", return_value=({"message_0.db": KEY_HEX}, {"verified_databases": 1})) as called:
            result = wxdecipher.run_session(self.root, {"session_id": session, "auto_capture": {**selected, "consent_token": consent["consent_token"]}, "cipher_mode": "sqlcipher4"})
        self.assertEqual(1, called.call_count)
        self.assertEqual("sqlcipher4-raw", result["decipher"]["databases"][0]["cipher_mode"])
        self.assertEqual(3, result["decipher"]["messages"])
        self.assertNotIn(KEY_HEX, json.dumps(result))
        self.assertFalse((self.root / "data/wechat/decipher" / session).exists())

    def test_capture_ticket_is_hashed_bound_expiring_and_consumed_before_process_access(self):
        for defect in ("missing", "wrong-ticket", "wrong-pid", "wrong-created", "expired", "future", "changed-files", "reissued"):
            with self.subTest(defect=defect):
                session = wxdecipher.create_session(self.root, {"ownership_confirmed": True, "snapshot_confirmed": True, "self_username": SELF})["session_id"]
                with self.database.open("rb") as reader:
                    wxdecipher.upload_database(self.root, session, "message_0.db", reader, self.database.stat().st_size)
                selected = {"confirmed": True, "process_id": 123, "created_at": "456"}
                consent = wxdecipher.issue_capture_consent(self.root, {"session_id": session, **selected})
                selected["consent_token"] = consent["consent_token"]
                path = self.root / "data/wechat/decipher" / session
                manifest = json.loads((path / "session.json").read_text(encoding="utf-8"))
                self.assertNotIn(consent["consent_token"], json.dumps(manifest))
                if defect == "missing": selected.pop("consent_token")
                if defect == "wrong-ticket": selected["consent_token"] = "x" * 43
                if defect == "wrong-pid": selected["process_id"] += 1
                if defect == "wrong-created": selected["created_at"] = "457"
                if defect in {"expired", "future"}:
                    manifest["capture_consent"]["issued_at"] += -61 if defect == "expired" else 100
                    wxdecipher._save_session(path, manifest)
                if defect == "changed-files":
                    with self.database.open("rb") as reader:
                        wxdecipher.upload_database(self.root, session, "message_1.db", reader, self.database.stat().st_size)
                if defect == "reissued":
                    wxdecipher.issue_capture_consent(self.root, {"session_id": session, **selected})
                with patch.object(capture, "capture_keys", side_effect=AssertionError("Never read process for an invalid ticket")):
                    with self.assertRaises(WechatStoreError) as raised:
                        wxdecipher.run_session(self.root, {"session_id": session, "auto_capture": selected})
                self.assertEqual("CAPTURE_CONSENT_REQUIRED", raised.exception.code)
                self.assertFalse(path.exists())

    def test_windows_identity_and_minimum_rights_fail_closed(self):
        # API doubles only: never open or enumerate a live WeChat PID.
        def fake_api(*, path="C:/Synthetic/Weixin.exe", owner=b"self", session=1, denied=False, privileged=False):
            api = SimpleNamespace(kernel=SimpleNamespace())
            kernel = api.kernel
            kernel.GetCurrentProcess = Mock(return_value=100)
            kernel.GetCurrentProcessId = Mock(return_value=999)
            kernel.OpenProcess = Mock(return_value=0 if denied else 200)
            kernel.CloseHandle = Mock(return_value=True)
            def image_name(handle, flags, output, size): output.value = path; return True
            def process_times(handle, creation, *_): creation._obj.dwLowDateTime = 456; return True
            def exit_code(handle, output): output._obj.value = 259; return True
            def wow(handle, process, native): process._obj.value = 0; native._obj.value = 0x8664; return True
            kernel.QueryFullProcessImageNameW = image_name; kernel.GetProcessTimes = process_times
            kernel.GetExitCodeProcess = exit_code; kernel.IsWow64Process2 = wow
            def token(handle, *, check_privileges=False):
                if check_privileges and privileged: raise WechatStoreError("CAPTURE_PRIVILEGED", "synthetic elevated host")
                return b"self" if handle == 100 else owner
            api.token_info = token; api.session_id = lambda pid: 1 if pid == 999 else session
            return api
        for options, expected in (({}, None), ({"owner": b"someone-else"}, "CAPTURE_DENIED"),
                                  ({"session": 2}, "CAPTURE_DENIED"), ({"path": "C:/Synthetic/Other.exe"}, "CAPTURE_SELECTION"),
                                  ({"denied": True}, "CAPTURE_DENIED"), ({"privileged": True}, "CAPTURE_PRIVILEGED")):
            with self.subTest(options=options):
                api = fake_api(**options)
                with patch.object(capture, "_WindowsAPI", return_value=api):
                    selected = capture._WindowsProcess(123, "456")
                    if expected:
                        with self.assertRaises(WechatStoreError) as raised:
                            with selected: pass
                        self.assertEqual(expected, raised.exception.code)
                    else:
                        with selected as process: process.check_identity()
                if options.get("privileged"):
                    api.kernel.OpenProcess.assert_not_called()
                else:
                    api.kernel.OpenProcess.assert_called_once_with(0x0410, False, 123)
                self.assertIsNone(selected.handle)

    def test_native_reader_preflight_cross_region_guard_and_exact_read(self):
        selected = capture._WindowsProcess.__new__(capture._WindowsProcess)
        selected.handle = 200
        protection = 4
        short = False
        def query(handle, address, info, size):
            info._obj.BaseAddress = 0x10000; info._obj.RegionSize = 4096
            info._obj.State = 0x1000; info._obj.Protect = protection
            return ctypes.sizeof(capture._MemoryInfo)
        def read(handle, address, target, length, received):
            ctypes.memmove(target, b"x" * length, length)
            received._obj.value = length - 1 if short else length
            return True
        selected.api = SimpleNamespace(kernel=SimpleNamespace(VirtualQueryEx=query, ReadProcessMemory=Mock(side_effect=read)))
        self.assertEqual(b"x" * 16, selected.read(0x10000, 16))
        for address, length in ((0, 16), (0x10fff, 16), (0xFFFFFFFFFFFFFFF0, 128), (0x10000, 2 * capture.CHUNK_BYTES)):
            self.assertIsNone(selected.read(address, length))
        for protection in (1, 0x104, 0x10): self.assertIsNone(selected.read(0x10000, 16))
        protection = 4; short = True
        self.assertIsNone(selected.read(0x10000, 16))

    def test_no_write_suspend_injection_or_privilege_adjustment_apis(self):
        import ast
        source = Path(capture.__file__).read_text(encoding="utf-8")
        attributes = {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
        self.assertFalse(attributes & {"WriteProcessMemory", "VirtualProtectEx", "CreateRemoteThread", "OpenThread",
                                       "AdjustTokenPrivileges", "NtSuspendProcess", "SuspendThread", "MiniDumpWriteDump"})

    @unittest.skipUnless(capture.available(), "requires Windows x64, only reads a test-created helper")
    def test_native_windows_read_of_our_own_helper_only(self):
        helper_path = Path(__file__).with_name("wxdecipher_capture_helper.py")
        child = subprocess.Popen([sys.executable, str(helper_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            line = child.stdout.readline()
            self.assertTrue(line, child.stderr.read() if child.poll() is not None else "helper did not start")
            description = json.loads(line)
            self.assertEqual(child.pid, description["pid"])
            # Only this one newly-created helper PID is allowed in this test.
            with patch.object(capture, "ALLOWED_IMAGES", {description["image_name"].casefold()}):
                try:
                    selected = capture._WindowsProcess(child.pid, description["created_at"])
                    with selected as process:
                        raw = process.read(description["address"], description["length"])
                        self.assertIn(KEY_HEX.encode(), raw)
                        process.regions = lambda: [(description["address"], description["length"], 0x20000)]
                        keys, report = capture.scan_keys(process, {"db": self.page})
                        self.assertEqual(KEY_HEX, keys["db"])
                        self.assertEqual(1, report["verified_databases"])
                except WechatStoreError as error:
                    if error.code == "CAPTURE_PRIVILEGED": self.skipTest("Host is privileged; production capture correctly refused")
                    raise
                with self.assertRaises(WechatStoreError) as raised:
                    with capture._WindowsProcess(child.pid, str(int(description["created_at"]) + 1)): pass
                self.assertEqual("CAPTURE_PROCESS_CHANGED", raised.exception.code)
            self.assertIsNone(selected.handle)
        finally:
            if child.poll() is None:
                child.communicate("done\n", timeout=5)


if __name__ == "__main__":
    unittest.main()
