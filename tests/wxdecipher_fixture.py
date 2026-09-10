"""Synthetic-only fixtures. No WeChat account, process, or data directory access.

The small reference encoder is intentionally independent of production helpers.
SQLite verifies its reserved-page plaintext before it is used as a crypto vector.
For independent implementation coverage, see the official-oracle test alongside.
"""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
import struct
from pathlib import Path

from Crypto.Cipher import AES


KEY = bytes(range(32))
KEY_HEX = KEY.hex()
CLIENT = "wxid_synthetic_client"
SELF = "wxid_synthetic_self"
EPOCH = 1788919200


def table_for(name: str) -> str:
    return "Msg_" + hashlib.md5(name.encode(), usedforsecurity=False).hexdigest()


def make_database(path: Path, *, conversation: str = CLIENT, rows: list[tuple] | None = None,
                  names: bool = True, contacts: bool = True) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA page_size=4096")
        if names:
            connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
            connection.executemany("INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)", [(1, conversation), (2, SELF)])
        if contacts:
            connection.execute("CREATE TABLE contact(id INTEGER PRIMARY KEY,username TEXT,remark TEXT,nick_name TEXT)")
            connection.executemany("INSERT INTO contact VALUES(?,?,?,?)", [
                (1, conversation, "演示客户", "合成客户"), (2, SELF, "", "演示本人"),
            ])
        table = table_for(conversation)
        connection.execute(f'''CREATE TABLE "{table}" (
            local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,
            real_sender_id INTEGER,create_time INTEGER,message_content BLOB,compress_content BLOB
        )''')
        if rows is None:
            rows = [(1, 90071992547409931, 1, 1001, 1, EPOCH, "请确认下周的报价计划。", None),
                    (2, 90071992547409932, 1, 1002, 2, EPOCH + 1, "收到，周五提供方案。", None),
                    (3, 90071992547409933, 3, 1003, 1, EPOCH + 2, "<msg><img/></msg>", None)]
        connection.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?,?)', rows)
        connection.commit()
    finally:
        connection.close()
    return path


def reserved_plaintext(source: Path, destination: Path) -> bytes:
    raw = source.read_bytes()
    pages = []
    for index in range(0, len(raw), 4096):
        original = raw[index:index + 4096]
        page = bytearray(original)
        header = 100 if index == 0 else 0
        if page[header] != 13 or page[header + 1:header + 3] != b"\0\0" or page[header + 7] != 0:
            raise AssertionError("The reference fixture must contain compact leaf pages only")
        cells = int.from_bytes(page[header + 3:header + 5], "big")
        start = int.from_bytes(page[header + 5:header + 7], "big")
        if start - 80 <= header + 8 + cells * 2:
            raise AssertionError("The reference fixture must leave room for 80 reserved bytes")
        page[start - 80:4096 - 80] = original[start:]
        page[4096 - 80:] = bytes(80)
        page[header + 5:header + 7] = (start - 80).to_bytes(2, "big")
        for number in range(cells):
            offset = header + 8 + number * 2
            pointer = int.from_bytes(original[offset:offset + 2], "big")
            page[offset:offset + 2] = (pointer - 80).to_bytes(2, "big")
        if index == 0:
            page[20] = 80
        pages.append(bytes(page))
    result = b"".join(pages)
    destination.write_bytes(result)
    connection = sqlite3.connect(destination.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
    return result


def encrypt_fixture(source: Path, target: Path, *, raw_key: bool = True, key: bytes = KEY) -> Path:
    reserved = target.with_suffix(".reserved.sqlite")
    plaintext = reserved_plaintext(source, reserved)
    salt = bytes(range(16, 32))
    encryption_key = key if raw_key else hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
    authentication_key = hashlib.pbkdf2_hmac("sha512", encryption_key, bytes(value ^ 58 for value in salt), 2, 32)
    output = bytearray()
    for index in range(0, len(plaintext), 4096):
        number = index // 4096 + 1
        page = plaintext[index:index + 4096]
        offset = 16 if number == 1 else 0
        iv = hashlib.sha256(b"synthetic-page-" + struct.pack("<I", number)).digest()[:16]
        ciphertext = AES.new(encryption_key, AES.MODE_CBC, iv).encrypt(page[offset:4016])
        tag = hmac.new(authentication_key, ciphertext + iv + struct.pack("<I", number), hashlib.sha512).digest()
        output.extend((salt if number == 1 else b"") + ciphertext + iv + tag)
    target.write_bytes(output)
    return target
