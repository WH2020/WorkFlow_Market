"""Offline SQLite/SQLCipher WAL recovery into a NEW snapshot, never checkpoint.

Format/recovery: https://www.sqlite.org/fileformat2.html#walformat
SQLCipher writes encrypted pages into WAL (src/wal.c / sqlcipherPagerCodec).
The ordinary SQLite checksum is not authentication; same-snapshot pairing is
explicit user input. SQLCipher page authentication/integrity is performed by
decrypt_database on the resulting application-owned copy.
"""
from __future__ import annotations

import os
import shutil
import struct
import time
from pathlib import Path

from .wechat_store import WechatStoreError
from .wxdecipher_crypto import MAX_DATABASE_BYTES, PAGE_SIZE, SQLITE_HEADER

MAX_WAL_BYTES = 256 * 1024 * 1024
MAX_REPLAY_SECONDS = 60


def wal_checksum(data: bytes, endian: str, state: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    if len(data) % 8 or endian not in {"<", ">"}:
        raise WechatStoreError("INVALID_WAL", "WAL 校验输入无效")
    first, second = state
    for left, right in struct.iter_unpack(endian + "II", data):
        first = (first + left + second) & 0xFFFFFFFF
        second = (second + right + first) & 0xFFFFFFFF
    return first, second


def _ordinary(path: Path) -> os.stat_result:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise WechatStoreError("UNSAFE_PATH", "DB 与 WAL 必须为普通静态副本，不能是链接")
    return path.stat()


def _stamp(stat: os.stat_result) -> tuple:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def replay_wal(source: Path, wal: Path, destination: Path) -> dict:
    """Overlay the last committed valid prefix, leaving encryption unchanged.

Caller MUST decrypt/authenticate and integrity-check the result before import.
Only app staging uses this function; it does not expose an unverified download.
"""
    source_stat, wal_stat = _ordinary(source), _ordinary(wal)
    if source.resolve() == wal.resolve() or destination.exists() or destination.is_symlink():
        raise WechatStoreError("UNSAFE_PATH", "WAL 输出必须是新的独立副本")
    if not 512 <= source_stat.st_size <= MAX_DATABASE_BYTES or not 0 <= wal_stat.st_size <= MAX_WAL_BYTES:
        raise WechatStoreError("FILE_TOO_LARGE", "DB 或 WAL 副本超过大小上限")
    journal = Path(str(source) + "-journal")
    if journal.exists() and journal.stat().st_size:
        raise WechatStoreError("INVALID_WAL", "不能混用回滚日志与 WAL，请重新准备一致副本")
    created = False
    deadline = time.monotonic() + MAX_REPLAY_SECONDS
    try:
        with source.open("rb") as main, wal.open("rb") as log:
            main_head = main.read(PAGE_SIZE)
            encrypted = not main_head.startswith(SQLITE_HEADER)
            page_size = PAGE_SIZE if encrypted else int.from_bytes(main_head[16:18], "big")
            page_size = 65536 if page_size == 1 else page_size
            if not 512 <= page_size <= 65536 or page_size & (page_size - 1) or source_stat.st_size % page_size:
                raise WechatStoreError("INVALID_DATABASE", "主数据库页大小或长度无效")
            main_pages = source_stat.st_size // page_size
            base_pages = main_pages
            db_pages = main_pages
            last_commit = valid_frames = 0
            latest: dict[int, int] = {}
            pending: dict[int, int] = {}
            warnings = []
            if wal_stat.st_size:
                header = log.read(32)
                if len(header) != 32:
                    raise WechatStoreError("INVALID_WAL", "WAL 头部不完整")
                magic, version, wal_page_size, _, salt1, salt2, check1, check2 = struct.unpack(">8I", header)
                if magic not in {0x377F0682, 0x377F0683} or version != 3007000 or wal_page_size != page_size:
                    raise WechatStoreError("INVALID_WAL", "WAL 魔数、版本或页大小不匹配")
                endian = ">" if magic == 0x377F0683 else "<"
                checksum = wal_checksum(header[:24], endian)
                if checksum != (check1, check2):
                    raise WechatStoreError("INVALID_WAL", "WAL 头部校验失败")
                while True:
                    if time.monotonic() > deadline:
                        raise WechatStoreError("WAL_LIMIT", "WAL 重放超过 60 秒上限，请拆分或重新准备快照")
                    frame_start = log.tell()
                    frame_header = log.read(24)
                    if not frame_header:
                        break
                    page = log.read(page_size)
                    if len(frame_header) != 24 or len(page) != page_size:
                        warnings.append("WAL 尾部有不完整帧，已按 SQLite 恢复规则忽略其后内容。")
                        break
                    number, commit_pages, frame_salt1, frame_salt2, check1, check2 = struct.unpack(">6I", frame_header)
                    if not number or (frame_salt1, frame_salt2) != (salt1, salt2):
                        warnings.append("WAL 有旧世代或无效尾帧，已在首个无效点停止，不跨帧继续恢复。")
                        break
                    expected = wal_checksum(page, endian, wal_checksum(frame_header[:8], endian, checksum))
                    if expected != (check1, check2):
                        warnings.append("WAL 帧校验失败，已在该帧前停止；校验错误之后的内容未恢复。")
                        break
                    if number * page_size > MAX_DATABASE_BYTES or commit_pages * page_size > MAX_DATABASE_BYTES:
                        raise WechatStoreError("WAL_LIMIT", "WAL 页号或提交大小超过 256 兆字节上限")
                    if encrypted and number == 1 and page[:16] != main_head[:16]:
                        raise WechatStoreError("WAL_MISMATCH", "WAL 首页盐与主库不一致，不是匹配的 SQLCipher 副本")
                    checksum = expected
                    valid_frames += 1
                    pending[number] = frame_start + 24
                    if commit_pages:
                        latest.update(pending)
                        pending.clear()
                        last_commit = valid_frames
                        db_pages = commit_pages
                        # A shrink invalidates old high pages even if a later
                        # transaction grows into the original file's extent.
                        base_pages = min(base_pages, db_pages)
                        latest = {number: offset for number, offset in latest.items() if number <= db_pages}
                if valid_frames > last_commit:
                    warnings.append(f"忽略最后提交之后的 {valid_frames - last_commit} 个未提交帧。")
                if not last_commit:
                    warnings.append("WAL 没有有效提交，恢复结果仅来自主数据库。")
            if db_pages > base_pages and any(number not in latest for number in range(base_pages + 1, db_pages + 1)):
                raise WechatStoreError("WAL_MISMATCH", "WAL 扩展数据库时缺少必要页，请重新取得同一时点的 DB/WAL 副本")
            from .wechat_privacy import create_private_file
            create_private_file(destination)
            created = True
            with destination.open("wb") as output:
                main.seek(0)
                shutil.copyfileobj(main, output, length=1024 * 1024)
                applied = 0
                for number, offset in sorted(latest.items()):
                    if number > db_pages:
                        continue
                    log.seek(offset)
                    page = log.read(page_size)
                    if len(page) != page_size:
                        raise WechatStoreError("FILE_CHANGED", "WAL 副本在恢复期间变化")
                    output.seek((number - 1) * page_size)
                    output.write(page)
                    applied += 1
                output.truncate(db_pages * page_size)
                if not encrypted:
                    # No WAL sidecar is needed for the finished standalone copy.
                    output.seek(18)
                    output.write(b"\x01\x01")
                output.flush()
                os.fsync(output.fileno())
        if _stamp(_ordinary(source)) != _stamp(source_stat) or _stamp(_ordinary(wal)) != _stamp(wal_stat):
            raise WechatStoreError("FILE_CHANGED", "DB/WAL 副本在恢复期间变化，结果未导入")
        return {"committed_frames": last_commit, "valid_frames": valid_frames, "applied_pages": applied,
                "database_pages": db_pages, "ignored_tail_bytes": max(0, wal_stat.st_size - (32 + last_commit * (24 + page_size))) if wal_stat.st_size else 0,
                "warnings": warnings, "verified": False}
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
