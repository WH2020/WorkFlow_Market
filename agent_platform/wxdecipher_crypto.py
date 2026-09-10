"""Offline SQLCipher 4 page reader; never discovers accounts or obtains keys.

Based on WXDecipher's MIT-licensed design (docs/third-party/WXDecipher-LICENSE.txt)
and SQLCipher's format. Only caller-selected, immutable copies are accepted.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .wechat_store import WechatStoreError


SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_SIZE = 4096
RESERVE = 80  # 16-byte IV + 64-byte SHA-512 HMAC
MAX_DATABASE_BYTES = 256 * 1024 * 1024
CIPHER_MODES = ("auto", "sqlcipher4", "sqlcipher4-raw")


def parse_key(value: object) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
        raise WechatStoreError("KEY_FORMAT", "密钥必须是 64 位十六进制字符（32 字节），不会保存到磁盘")
    return bytes.fromhex(value.strip())


def _aes():
    try:
        from Crypto.Cipher import AES
    except ImportError as error:
        raise WechatStoreError(
            "DEPENDENCY_MISSING", "缺少本地解密依赖 pycryptodome，请安装 requirements-wxdecipher.txt 后重试",
        ) from error
    return AES


def derive_keys(key: bytes, salt: bytes, mode: str) -> tuple[bytes, bytes]:
    if len(key) != 32 or len(salt) != 16 or mode not in CIPHER_MODES[1:]:
        raise WechatStoreError("KEY_FORMAT", "密钥、盐或解密模式无效")
    # SQLCipher accepts both a password (PBKDF2) and a raw AES key (x'...').
    # Its HMAC-key KDF uses the *KDF* hash, not a separately chosen HMAC hash.
    aes_key = key if mode == "sqlcipher4-raw" else hashlib.pbkdf2_hmac("sha512", key, salt, 256000, 32)
    mac_key = hashlib.pbkdf2_hmac("sha512", aes_key, bytes(byte ^ 0x3A for byte in salt), 2, 32)
    return aes_key, mac_key


def decrypt_page(page: bytes, number: int, aes_key: bytes, mac_key: bytes) -> bytes:
    if len(page) != PAGE_SIZE or not 1 <= number <= 0xFFFFFFFF:
        raise WechatStoreError("INVALID_DATABASE", "数据库包含不完整页或无效页号")
    if number > 1 and not any(page):
        # SQLCipher's zero-page exception concerns auto-vacuum pager short reads,
        # not unauthenticated full pages inside an uploaded static database.
        raise WechatStoreError("UNAUTHENTICATED_ZERO_PAGE", f"第 {number} 页为无法认证的全零页；请重新准备完整副本")
    start = 16 if number == 1 else 0
    end = PAGE_SIZE - RESERVE
    iv = page[end:end + 16]
    actual = hmac.digest(mac_key, page[start:end + 16] + struct.pack("<I", number), "sha512")
    if not hmac.compare_digest(actual, page[end + 16:]):
        raise WechatStoreError("HMAC_FAILED", f"第 {number} 页校验失败：密钥、参数不匹配或数据库副本损坏；未导入")
    AES = _aes()
    plaintext = AES.new(aes_key, AES.MODE_CBC, iv).decrypt(page[start:end])
    return (SQLITE_HEADER if number == 1 else b"") + plaintext + bytes(RESERVE)


@contextmanager
def open_readonly(path: Path, *, timeout_seconds: float = 60) -> Iterator[sqlite3.Connection]:
    """Read only a stationary application-owned snapshot, with bounded SQL work."""
    if path.is_symlink() or not path.is_file():
        raise WechatStoreError("INVALID_DATABASE", "数据库副本必须是普通文件")
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        # ORDER BY must not spill decrypted message text to untracked OS temp.
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-8192")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
        deadline = time.monotonic() + timeout_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        yield connection
    except sqlite3.Error as error:
        # SQLite errors may contain schema text. Never echo untrusted DB content.
        raise WechatStoreError("INVALID_DATABASE", "数据库结构、完整性或查询校验失败；请使用完整的静态副本") from error
    finally:
        if connection is not None:
            connection.close()


def verify_database(path: Path) -> None:
    with open_readonly(path) as connection:
        result = connection.execute("PRAGMA integrity_check(1)").fetchone()
        if result is None or result[0] != "ok":
            raise WechatStoreError("INVALID_DATABASE", "数据库完整性校验未通过；未导入任何消息")


def decrypt_database(source: Path, destination: Path, key_text: str = "", mode: str = "auto") -> dict:
    """Stream to a new destination, remove partial output on *any* failure.

    Plain SQLite is also accepted, but still verified. WAL replay and alternative
    page sizes/hashes are deliberately not guessed. Inputs must be static copies.
    """
    if mode not in CIPHER_MODES:
        raise WechatStoreError("INVALID_CIPHER", "解密模式无效")
    if source.is_symlink() or not source.is_file():
        raise WechatStoreError("INVALID_DATABASE", "请选择普通数据库副本")
    if destination.exists() or destination.is_symlink() or source.resolve() == destination.resolve():
        raise WechatStoreError("UNSAFE_PATH", "解密输出必须是新的应用副本，不能覆盖源文件")
    initial = source.stat()
    if not 512 <= initial.st_size <= MAX_DATABASE_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "单个数据库必须为 512 字节至 256 兆字节")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise WechatStoreError("WAL_UNSUPPORTED", "所选副本含未合并日志；请先准备已完成 checkpoint 的数据库副本")
    created = False
    try:
        with source.open("rb") as reader:
            first = reader.read(PAGE_SIZE)
            plain = first.startswith(SQLITE_HEADER)
            if plain:
                page_size = int.from_bytes(first[16:18], "big")
                page_size = 65536 if page_size == 1 else page_size
                if page_size < 512 or page_size > 65536 or page_size & (page_size - 1) or initial.st_size % page_size:
                    raise WechatStoreError("INVALID_DATABASE", "SQLite 页大小或文件长度无效")
                selected_mode = "plaintext"
            else:
                if initial.st_size % PAGE_SIZE:
                    raise WechatStoreError("INVALID_DATABASE", "加密数据库长度不是 4096 字节的整数倍；不截断、不跳过残页")
                key = parse_key(key_text)
                candidates = ("sqlcipher4-raw", "sqlcipher4") if mode == "auto" else (mode,)
                selected_mode = ""
                for candidate in candidates:
                    aes_key, mac_key = derive_keys(key, first[:16], candidate)
                    try:
                        first_plain = decrypt_page(first, 1, aes_key, mac_key)
                    except WechatStoreError as error:
                        if error.code != "HMAC_FAILED":
                            raise
                        continue
                    selected_mode = candidate
                    break
                if not selected_mode:
                    raise WechatStoreError("KEY_MISMATCH", "首页校验失败：请检查密钥和账号；当前支持 4096 页、SHA-512 的 SQLCipher 4")
                if int.from_bytes(first_plain[16:18], "big") != PAGE_SIZE or first_plain[20] != RESERVE:
                    raise WechatStoreError("INVALID_DATABASE", "解密页头参数与支持的 SQLCipher 4 格式不符")
                page_size = PAGE_SIZE
            with destination.open("xb") as writer:
                created = True
                if plain:
                    writer.write(first)
                    for block in iter(lambda: reader.read(1024 * 1024), b""):
                        writer.write(block)
                else:
                    # The output is an independent, journal-free snapshot.
                    first_plain = first_plain[:18] + b"\x01\x01" + first_plain[20:]
                    writer.write(first_plain)
                    for number in range(2, initial.st_size // PAGE_SIZE + 1):
                        writer.write(decrypt_page(reader.read(PAGE_SIZE), number, aes_key, mac_key))
                    if reader.read(1):
                        raise WechatStoreError("FILE_CHANGED", "数据库副本在处理期间发生变化")
                writer.flush()
                os.fsync(writer.fileno())
        final = source.stat()
        if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise WechatStoreError("FILE_CHANGED", "数据库副本在处理期间发生变化")
        verify_database(destination)
        return {"cipher_mode": selected_mode, "pages": initial.st_size // page_size, "verified": True}
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
