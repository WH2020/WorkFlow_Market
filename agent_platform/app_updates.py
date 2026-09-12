"""Read-only public GitHub release checks, separate from model/business traffic.

No credentials, telemetry, downloads, installers or application files are used.
Opening a validated release page is a separate, explicit user action.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4


REPOSITORY = "WH2020/WorkFlow_Market"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = 300
MANUAL_COOLDOWN = 60
MAX_RESPONSE_BYTES = 512 * 1024
MAX_NOTES = 12000
SOCKET_TIMEOUT = 8
TOTAL_TIMEOUT = 20
_VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")


class UpdateError(ValueError):
    def __init__(self, code: str, message: str, retry_after: int = 0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def version_key(value: str, *, tag: bool = False) -> tuple:
    if not isinstance(value, str) or len(value) > 120:
        raise UpdateError("INVALID_VERSION", "版本号无法识别；请使用语义版本号，例如 0.20.3。")
    value = value[1:] if tag and value.startswith("v") else value
    match = _VERSION.fullmatch(value)
    if not match:
        raise UpdateError("INVALID_VERSION", "版本号无法识别；请使用语义版本号，例如 0.20.3。")
    prerelease = match.group(4)
    parts = prerelease.split(".") if prerelease else []
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in parts):
        raise UpdateError("INVALID_VERSION", "预发布版本号无效。")
    suffix = (0, tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)) if parts else (1,)
    return (tuple(int(match.group(index)) for index in (1, 2, 3)), suffix)


def application_version(root: Path) -> str:
    try:
        path = root / "package.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            return "unknown"
        version = json.loads(path.read_text(encoding="utf-8"))["version"]
        version_key(version)
        return version
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value[:limit])


def release_record(value: object) -> dict:
    if not isinstance(value, dict) or value.get("draft") is not False or value.get("prerelease") is not False:
        raise UpdateError("INVALID_RELEASE", "更新源未返回可用的正式发布版本。")
    tag = value.get("tag_name")
    key = version_key(tag, tag=True)
    if key[1][0] != 1:
        raise UpdateError("INVALID_RELEASE", "更新源返回了预发布标签；当前只检查正式版本。")
    published = value.get("published_at")
    try:
        timestamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("Timezone required")
    except (AttributeError, ValueError, TypeError):
        raise UpdateError("INVALID_RELEASE", "更新源缺少有效的发布时间。") from None
    return {"version": tag[1:] if tag.startswith("v") else tag, "tag": tag,
            "name": _text(value.get("name"), 160) or tag,
            "published_at": timestamp.astimezone(timezone.utc).isoformat(),
            "notes": _text(value.get("body"), MAX_NOTES),
            "notes_truncated": isinstance(value.get("body"), str) and len(value["body"]) > MAX_NOTES,
            # Never trust an upstream or client-supplied external URL.
            "release_url": f"{RELEASES_URL}/tag/{quote(tag, safe='')}",
            "windows_assets": windows_release_assets(value, tag),
            "macos_assets": macos_release_assets(value, tag)}


def macos_release_assets(release: dict, tag: str) -> dict | None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return None
    names = {"programs": (f"Agent4Market-{tag[1:]}-macos-programs.zip", 1024 ** 3),
             "app": (f"Agent4Market-{tag[1:]}-macos-universal-app.zip", 1024 ** 3),
             "manifest": ("macOS-install-manifest.json", 32 * 1024 ** 2),
             "signature": ("macOS-update-signature.json", 4096)}
    rows = release.get("assets")
    if not isinstance(rows, list) or len(rows) > 32:
        return None
    result = {}
    for key, (name, limit) in names.items():
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
        if len(matches) != 1:
            return None
        row = matches[0]
        if (row.get("state") != "uploaded" or type(row.get("id")) is not int or row["id"] <= 0
                or type(row.get("size")) is not int or not 0 < row["size"] <= limit
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(row.get("digest", "")))):
            return None
        result[key] = {"id": row["id"], "name": name, "bytes": row["size"], "sha256": row["digest"][7:]}
    return result


def windows_release_assets(release: dict, tag: str) -> dict | None:
    """Only server-provided immutable asset IDs and digests, never URLs."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return None
    version = tag[1:]
    names = {f"Agent4Market-{version}-Setup-x64.exe": 1024 ** 3,
             "Windows-install-manifest.json": 32 * 1024 ** 2,
             "Windows-update-signature.json": 4096}
    rows = release.get("assets")
    if not isinstance(rows, list) or len(rows) > 32:
        return None
    result = {}
    for name, limit in names.items():
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
        if len(matches) != 1:
            return None
        row = matches[0]
        if (row.get("state") != "uploaded" or type(row.get("id")) is not int or row["id"] <= 0
                or type(row.get("size")) is not int or not 0 < row["size"] <= limit
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(row.get("digest", "")))):
            return None
        key = "installer" if name.endswith(".exe") else "signature" if name == "Windows-update-signature.json" else "manifest"
        result[key] = {
            "id": row["id"], "name": name, "bytes": row["size"], "sha256": row["digest"][7:]}
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _retry_delay(headers, now: float) -> int:
    value = headers.get("Retry-After", "")
    try:
        delay = int(value) if str(value).isdigit() else math.ceil(parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        try:
            delay = int(headers.get("X-RateLimit-Reset", "0")) - int(now)
        except (TypeError, ValueError, OverflowError):
            delay = 0
    return max(MANUAL_COOLDOWN, min(86400, delay or 3600))


def fetch_latest(etag: str | None = None, *, opener=None) -> tuple[dict | None, str | None]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Agent4Market-update-check",
               "X-GitHub-Api-Version": "2026-03-10", "Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag
    request = Request(LATEST_API_URL, headers=headers, method="GET")
    # Honor the OS/environment transport proxy, without loading app/model tokens.
    open_request = opener or build_opener(ProxyHandler(), _NoRedirect()).open
    deadline = time.monotonic() + TOTAL_TIMEOUT
    try:
        with open_request(request, timeout=SOCKET_TIMEOUT) as response:
            if response.geturl() != LATEST_API_URL:
                raise UpdateError("UNTRUSTED_RESPONSE", "更新源地址不匹配，已停止检查。")
            if response.status == 304:
                return None, etag
            if response.status != 200:
                raise UpdateError("HTTP_ERROR", "更新服务暂不可用，请稍后重试。")
            if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise UpdateError("INVALID_RESPONSE", "更新服务返回了不支持的响应格式。")
            length = response.headers.get("Content-Length")
            if length and (not str(length).isdigit() or int(length) > MAX_RESPONSE_BYTES):
                raise UpdateError("RESPONSE_TOO_LARGE", "更新信息过大，已停止读取。")
            content = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise UpdateError("TIMEOUT", "检查更新超时，请稍后重试。")
                chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise UpdateError("RESPONSE_TOO_LARGE", "更新信息过大，已停止读取。")
            result = json.loads(content.decode("utf-8"))
            received_etag = response.headers.get("ETag")
            safe_etag = received_etag if isinstance(received_etag, str) and 0 < len(received_etag) <= 512 and all(32 <= ord(c) < 127 for c in received_etag) else None
            return release_record(result), safe_etag
    except HTTPError as error:
        try:
            if error.code == 304:
                return None, etag
            if error.code == 404:
                raise UpdateError("SOURCE_UNAVAILABLE", "更新源暂不可用：仓库可能未公开、尚无正式 Release，或地址已变化。") from None
            if error.code == 429 or (error.code == 403 and (error.headers.get("Retry-After") or error.headers.get("X-RateLimit-Remaining") == "0")):
                raise UpdateError("RATE_LIMITED", "GitHub 暂时限制了更新请求，将在等待后重试。", _retry_delay(error.headers, time.time())) from None
            if error.code == 403:
                raise UpdateError("ACCESS_DENIED", "GitHub 拒绝了更新访问，请检查发布源是否公开或网络访问是否受限。") from None
            if 300 <= error.code < 400:
                raise UpdateError("REDIRECT_REJECTED", "更新源发生重定向，请由维护者核实发布地址。") from None
            raise UpdateError("HTTP_ERROR", "更新服务暂不可用，请稍后重试。") from None
        finally:
            error.close()
    except (TimeoutError, URLError, OSError):
        raise UpdateError("NETWORK_ERROR", "无法连接 GitHub 更新服务，请检查网络后重试。") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpdateError("INVALID_RESPONSE", "更新服务返回的信息无法解析，请稍后重试。") from None


def _open_external(url: str) -> None:
    if sys.platform == "win32":
        os.startfile(url)
    else:
        command = "/usr/bin/open" if sys.platform == "darwin" else "/usr/bin/xdg-open"
        subprocess.Popen([command, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


class UpdateChecker:
    def __init__(self, current_version: str, *, fetcher: Callable = fetch_latest,
                 monotonic: Callable = time.monotonic, now: Callable = time.time):
        self.current_version = current_version
        self._fetch, self._mono, self._now = fetcher, monotonic, now
        self._lock = threading.Lock()
        self._etag = None
        self._latest = None
        self._retry_until = 0.0
        self._auto_retry_until = 0.0
        self._failures = 0
        self._last_attempt = None
        self._worker = None
        self._state = {"instance_id": uuid4().hex, "revision": 0, "status": "not_checked", "checking": False,
                       "checked_at": None, "last_success_at": None, "error": None}

    def _iso(self) -> str:
        return datetime.fromtimestamp(self._now(), timezone.utc).isoformat()

    def _snapshot(self) -> dict:
        retry = max(0, math.ceil(self._retry_until - self._mono()))
        newer = bool(self._latest) and version_key(self._latest["version"]) > version_key(self.current_version)
        return copy.deepcopy({**self._state, "current_version": self.current_version, "repository": REPOSITORY,
                              "channel": "stable", "check_interval_seconds": CHECK_INTERVAL,
                              "retry_after_seconds": retry, "latest": self._latest,
                              "auto_retry_after_seconds": max(0, math.ceil(self._auto_retry_until - self._mono())),
                              "update_available": self._state["status"] == "available",
                              "can_open_release": newer, "stale": bool(self._latest) and self._state["status"] == "error",
                              "automatic_install": False})

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def check(self, *, manual: bool = False) -> dict:
        with self._lock:
            try:
                version_key(self.current_version)
            except UpdateError:
                self._state.update(status="error", error={"code": "CURRENT_VERSION_UNKNOWN", "message": "本机版本无法识别，暂不能判断是否需要更新。"})
                self._state["revision"] += 1
                return self._snapshot()
            now = self._mono()
            interval = MANUAL_COOLDOWN if manual else CHECK_INTERVAL
            if self._state["checking"] or now < self._retry_until or (not manual and now < self._auto_retry_until) or (self._last_attempt is not None and now - self._last_attempt < interval):
                return self._snapshot()
            self._last_attempt = now
            self._retry_until = now + MANUAL_COOLDOWN
            self._auto_retry_until = now + CHECK_INTERVAL
            self._state.update(status="checking", checking=True, error=None)
            self._state["revision"] += 1
            self._worker = threading.Thread(target=self._run, name="agent4market-update-check", daemon=True)
            try:
                self._worker.start()
            except RuntimeError:
                self._state.update(status="error", checking=False, checked_at=self._iso(), error={"code": "CHECK_FAILED", "message": "暂时无法启动更新检查，请稍后重试。"})
                self._state["revision"] += 1
            return self._snapshot()

    def _run(self) -> None:
        try:
            release, etag = self._fetch(self._etag)
            with self._lock:
                if release is not None:
                    self._latest, self._etag = release, etag
                if self._latest is None:
                    raise UpdateError("INVALID_CACHE", "更新缓存不可用，请重新检查。")
                latest, current = version_key(self._latest["version"]), version_key(self.current_version)
                status = "available" if latest > current else "up_to_date" if latest == current else "current_ahead"
                self._state.update(status=status, last_success_at=self._iso(), error=None)
                self._failures = 0
        except Exception as error:
            safe = error if isinstance(error, UpdateError) else UpdateError("CHECK_FAILED", "检查更新失败，请稍后重试。")
            with self._lock:
                self._state.update(status="error", error={"code": safe.code, "message": str(safe)})
                self._retry_until = max(self._retry_until, self._mono() + safe.retry_after)
                self._failures += 1
                delay = min(21600, CHECK_INTERVAL * 2 ** min(self._failures, 7))
                self._auto_retry_until = max(self._retry_until, self._mono() + delay + random.uniform(0, 60))
        finally:
            with self._lock:
                self._state.update(checking=False, checked_at=self._iso())
                self._state["revision"] += 1

    def open_release(self, expected_tag: str, *, opener: Callable = _open_external) -> dict:
        with self._lock:
            state = self._snapshot()
            if not state["can_open_release"] or not self._latest or expected_tag != self._latest["tag"]:
                raise UpdateError("STALE_SELECTION", "更新检测结果已变化，请重新检查后打开发布页。")
            url = self._latest["release_url"]
        try:
            opener(url)
        except OSError:
            raise UpdateError("OPEN_FAILED", "无法打开系统浏览器，请检查浏览器安装情况。") from None
        return {"status": "opened", "release_url": url, "message": "已打开 GitHub 发布页；请按发布说明获取适用的新版本。当前程序未被覆盖。"}
