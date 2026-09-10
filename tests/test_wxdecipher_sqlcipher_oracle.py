"""Optional offline cross-check against an independently generated official DB.

Download separately from the pinned SQLCipher v4.0.1 repository; tests never
connect to the network. Set WXDECIPHER_SQLCIPHER4_FIXTURE to that local file.
https://github.com/sqlcipher/sqlcipher/blob/v4.0.1/test/sqlcipher-compatibility.test
The official test uses password 'testkey' and expects t1 to contain 78536 rows.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from agent_platform.wxdecipher_crypto import decrypt_database, open_readonly


FIXTURE = os.environ.get("WXDECIPHER_SQLCIPHER4_FIXTURE", "")


@unittest.skipUnless(FIXTURE, "Set WXDECIPHER_SQLCIPHER4_FIXTURE for the official SQLCipher oracle")
class OfficialSqlcipherOracleTests(unittest.TestCase):
    def test_all_pages_and_official_expected_row_count(self):
        source = Path(FIXTURE)
        encrypted = source.read_bytes()
        self.assertEqual(978944, len(encrypted))
        git_blob = b"blob " + str(len(encrypted)).encode("ascii") + b"\0" + encrypted
        self.assertEqual("b8db5378cbc5b015f857e4c4a9a66f522d8f832b", hashlib.sha1(git_blob).hexdigest())
        # The UI intentionally accepts 32-byte keys, not arbitrary passwords.
        # Derive the raw AES key from this public fixture's known password.
        raw_key = hashlib.pbkdf2_hmac("sha512", b"testkey", encrypted[:16], 256000, 32).hex()
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("sqlcipher4-raw", "auto"):
                with self.subTest(mode=mode):
                    output = Path(directory) / (mode + ".db")
                    report = decrypt_database(source, output, raw_key, mode)
                    self.assertEqual({"cipher_mode": "sqlcipher4-raw", "pages": 239, "verified": True}, report)
                    with open_readonly(output) as connection:
                        self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
                        self.assertEqual(78536, connection.execute("SELECT count(*) FROM t1").fetchone()[0])
        self.assertEqual(encrypted, source.read_bytes(), "The official encrypted source is unchanged")
