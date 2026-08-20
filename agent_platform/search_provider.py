from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROVIDER_ID = "brave-search"
KEYLESS_PROVIDER_ID = "keenable-public"
API_KEY_ENV = "BRAVE_SEARCH_API_KEY"
SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
MAX_RESPONSE_BYTES = 1024 * 1024


class SearchProviderError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def secret_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "search-provider.secret"


def restart_flag_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "search-provider.restart-required.json"


def _atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def _read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32_768:
        raise SearchProviderError("本地公开检索密钥记录缺失或不安全")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SearchProviderError(f"无法读取公开检索配置：{error}") from error
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("provider_id") != PROVIDER_ID:
        raise SearchProviderError("本地公开检索配置格式无效")
    return value


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Agent4Market Brave Search key", ctypes.byref(extra), None, None, 0x1,
        ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 加密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, extra_buffer


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    source, source_buffer = _blob(data)
    extra, extra_buffer = _blob(entropy)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(output)
    ):
        raise OSError(ctypes.get_last_error(), "Windows DPAPI 解密失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, extra_buffer


def _keychain_service(project_root: Path) -> str:
    suffix = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"Agent4Market.BraveSearch.{suffix}"


def save_search_secret(
    project_root: Path | str, api_key: str, *, system_name: str | None = None
) -> None:
    root = Path(project_root).resolve()
    key = api_key.strip()
    if not key or len(key) > 4096 or any(ord(character) < 32 for character in key):
        raise SearchProviderError("请填写有效的 Brave Search API Key")
    system = (system_name or platform.system()).lower()
    path = secret_path(root)
    record: dict[str, Any] = {
        "version": 1, "provider_id": PROVIDER_ID, "updated_at": _now(),
    }
    if system == "windows":
        entropy = hashlib.sha256(f"{root}|{PROVIDER_ID}".encode("utf-8")).digest()
        encrypted = _dpapi_protect(key.encode("utf-8"), entropy)
        record.update({"backend": "windows-dpapi", "ciphertext": base64.b64encode(encrypted).decode("ascii")})
    elif system == "darwin":
        service = _keychain_service(root)
        completed = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", PROVIDER_ID, "-w", key],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise SearchProviderError("无法写入 macOS 钥匙串")
        record.update({"backend": "macos-keychain", "service": service, "account": PROVIDER_ID})
    else:
        record.update({"backend": "private-file", "secret": base64.b64encode(key.encode("utf-8")).decode("ascii")})
    _atomic_json(path, record)


def load_search_secret(
    project_root: Path | str, *, system_name: str | None = None
) -> str | None:
    root = Path(project_root).resolve()
    path = secret_path(root)
    if not path.is_file():
        return None
    record = _read_record(path)
    backend = record.get("backend")
    try:
        if backend == "windows-dpapi":
            encrypted = base64.b64decode(str(record.get("ciphertext", "")), validate=True)
            entropy = hashlib.sha256(f"{root}|{PROVIDER_ID}".encode("utf-8")).digest()
            return _dpapi_unprotect(encrypted, entropy).decode("utf-8")
        if backend == "macos-keychain":
            completed = subprocess.run(
                ["security", "find-generic-password", "-s", str(record.get("service", "")),
                 "-a", str(record.get("account", "")), "-w"],
                check=False, capture_output=True, text=True,
            )
            return completed.stdout.rstrip("\r\n") if completed.returncode == 0 else None
        if backend == "private-file":
            return base64.b64decode(str(record.get("secret", "")), validate=True).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def validate_brave_search_key(
    api_key: str,
    *,
    timeout: float = 15.0,
    opener: Callable[..., Any] | None = None,
) -> None:
    key = api_key.strip()
    if not key or len(key) > 4096:
        raise SearchProviderError("请填写有效的 Brave Search API Key")
    url = f"{SEARCH_ENDPOINT}?{urlencode({'q': 'Agent4Market', 'count': 1})}"
    request = Request(
        url,
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        method="GET",
    )
    open_request = opener or build_opener(_NoRedirect()).open
    try:
        with open_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if status in {301, 302, 303, 307, 308}:
                raise SearchProviderError("Brave Search 验证请求不接受重定向")
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "text/json"}:
                raise SearchProviderError("Brave Search 没有返回 JSON")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_RESPONSE_BYTES:
                raise SearchProviderError("Brave Search 验证响应过大")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise SearchProviderError("Brave Search API Key 无效或未开通 Web Search 权限") from error
        if error.code == 429:
            raise SearchProviderError("Brave Search 额度不足或请求过于频繁") from error
        raise SearchProviderError(f"Brave Search 返回 HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise SearchProviderError(f"无法连接 Brave Search：{error}") from error
    except ValueError as error:
        raise SearchProviderError("Brave Search 返回了无效的响应长度") from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise SearchProviderError("Brave Search 验证响应过大")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchProviderError("Brave Search 返回的内容不是有效 JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("web"), dict):
        raise SearchProviderError("Brave Search 验证响应缺少 Web Search 结果")


def _mark_restart_required(project_root: Path | str) -> None:
    _atomic_json(restart_flag_path(project_root), {
        "version": 1, "provider_id": PROVIDER_ID, "required_at": _now(),
    })


def configure_search_provider(project_root: Path | str, api_key: str | None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    key = (api_key or "").strip() or load_search_secret(root)
    if not key:
        raise SearchProviderError("请填写 Brave Search API Key")
    validate_brave_search_key(key)
    _mark_restart_required(root)
    save_search_secret(root, key)
    return search_settings_summary(root)


def clear_search_provider(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = secret_path(root)
    record: dict[str, Any] = {}
    if path.is_file():
        try:
            record = _read_record(path)
        except SearchProviderError:
            record = {}
    if record.get("backend") == "macos-keychain":
        subprocess.run(
            ["security", "delete-generic-password", "-s", str(record.get("service", "")),
             "-a", str(record.get("account", ""))],
            check=False, capture_output=True, text=True,
        )
    _mark_restart_required(root)
    path.unlink(missing_ok=True)
    return search_settings_summary(root)


def search_settings_summary(
    project_root: Path | str, *, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    environment = os.environ if environ is None else environ
    environment_key = environment.get(API_KEY_ENV, "").strip()
    stored_key: str | None = None
    record: dict[str, Any] = {}
    warning: str | None = None
    try:
        if not environment_key:
            stored_key = load_search_secret(root)
            record = _read_record(secret_path(root)) if stored_key else {}
    except SearchProviderError as error:
        warning = f"专用检索密钥不可用，已切换到免密公共检索：{error}"
    has_api_key = bool(environment_key or stored_key)
    return {
        "configured": True,
        "status": "configured",
        "provider_id": PROVIDER_ID if has_api_key else KEYLESS_PROVIDER_ID,
        "source": "environment" if environment_key else "secure_store" if stored_key else "public_pool",
        "has_api_key": has_api_key,
        "keyless": not has_api_key,
        "shared_public_pool": not has_api_key,
        "restart_required": restart_flag_path(root).is_file(),
        "updated_at": record.get("updated_at"),
        **({"warning": warning} if warning else {}),
    }


def search_runtime_environment(
    project_root: Path | str, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    environment = os.environ if environ is None else environ
    if environment.get(API_KEY_ENV, "").strip():
        return {}
    key = load_search_secret(project_root)
    return {API_KEY_ENV: key} if key else {}


def mark_search_runtime_applied(project_root: Path | str) -> None:
    restart_flag_path(project_root).unlink(missing_ok=True)
