from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import ipaddress
import json
import os
import platform
import socket
import ssl
import subprocess
import tempfile
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request


PROVIDER_ID = "one-search"
BASE_URL_ENV = "ONE_SEARCH_BASE_URL"
TOKEN_ENV = "ONE_SEARCH_API_TOKEN"
MODE_ENV = "ONE_SEARCH_MODE"
MAX_RESULTS_ENV = "ONE_SEARCH_MAX_RESULTS"
ALLOW_PRIVATE_ENV = "ONE_SEARCH_ALLOW_PRIVATE_NETWORK"
ALLOWED_MODES = {"parallel", "fallback", "single"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_GATEWAY_CONFIG_LOCK = threading.Lock()


class SearchGatewayError(ValueError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def settings_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "search-gateway.json"


def secret_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "search-gateway.secret"


def restart_flag_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "search-gateway.restart-required.json"


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


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise SearchGatewayError(f"本地聚合检索配置 {path.name} 缺失或不安全")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SearchGatewayError(f"无法读取聚合检索配置：{error}") from error
    if not isinstance(value, dict):
        raise SearchGatewayError("聚合检索配置必须是 JSON 对象")
    return value


def _address_kind(address: str) -> str:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    if parsed.is_global:
        return "public"
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_reserved:
        return "blocked"
    if parsed.is_loopback or parsed.is_link_local:
        return "allowed-private"
    if isinstance(parsed, ipaddress.IPv4Address) and any(
        parsed in network
        for network in (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
    ):
        return "allowed-private"
    if isinstance(parsed, ipaddress.IPv6Address) and parsed in ipaddress.ip_network("fc00::/7"):
        return "allowed-private"
    return "blocked"


def _resolve_addresses(
    hostname: str, port: int, resolver: Callable[..., list[tuple[Any, ...]]]
) -> list[str]:
    try:
        addresses = sorted({
            item[4][0]
            for item in resolver(hostname, port, type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        })
    except OSError as error:
        raise SearchGatewayError(f"无法解析聚合检索网关域名：{error}") from error
    if not addresses:
        raise SearchGatewayError("聚合检索网关域名没有可用地址")
    return addresses


def normalize_gateway_url(
    raw: str,
    *,
    allow_private_network: bool,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    value = raw.strip().rstrip("/")
    if not value or len(value) > 500:
        raise SearchGatewayError("请填写有效的 One Search 网关地址")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SearchGatewayError("聚合检索网关必须使用 http:// 或 https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SearchGatewayError("网关地址不能包含账号、密码、查询参数或片段")
    normalized_path = parsed.path.rstrip("/")
    if normalized_path.endswith("/v1"):
        normalized_path = normalized_path[:-3]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_addresses(parsed.hostname, port, resolver)
    kinds = {_address_kind(address) for address in addresses}
    if "blocked" in kinds:
        raise SearchGatewayError("聚合检索网关解析到未指定、多播或保留地址，已拒绝连接")
    has_internal = "allowed-private" in kinds
    if has_internal and not allow_private_network:
        raise SearchGatewayError("该地址指向本机或局域网；确认可信后勾选“允许本机/局域网网关”")
    if parsed.scheme == "http" and not has_internal:
        raise SearchGatewayError("公网聚合检索网关必须使用 HTTPS")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", "")).rstrip("/")


def _validate_token(token: str) -> str:
    normalized = token.strip()
    if normalized.startswith("oak_"):
        raise SearchGatewayError("请勿使用 oak_ 管理员凭据；这里只接受权限受限的 osr_ 检索令牌")
    if len(normalized) <= 4 or not normalized.startswith("osr_") or len(normalized) > 4096 or any(ord(character) < 32 for character in normalized):
        raise SearchGatewayError("请填写有效的 osr_ One Search 检索令牌")
    return normalized


def _pinned_provider_response(url: str, token: str, address: str, timeout: float) -> tuple[int, Any, bytes]:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise SearchGatewayError("One Search 提供商地址无效")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    raw_socket = socket.create_connection((address, port), timeout=timeout)
    try:
        if parsed.scheme == "https":
            raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
        connection.sock = raw_socket
        request_path = parsed.path or "/"
        connection.request(
            "GET", request_path,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Host": parsed.netloc},
        )
        response = connection.getresponse()
        return response.status, response.headers, response.read(MAX_RESPONSE_BYTES + 1)
    finally:
        connection.close()
        raw_socket.close()


def validate_search_gateway(
    base_url: str,
    token: str,
    *,
    allow_private_network: bool,
    timeout: float = 15.0,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    opener: Callable[..., Any] | None = None,
) -> tuple[str, list[str]]:
    normalized = normalize_gateway_url(
        base_url, allow_private_network=allow_private_network, resolver=resolver
    )
    key = _validate_token(token)
    request = Request(
        f"{normalized}/v1/providers",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        if opener is not None:
            with opener(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                headers = response.headers
                body = response.read(MAX_RESPONSE_BYTES + 1)
        else:
            parsed = urlsplit(normalized)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = _resolve_addresses(parsed.hostname or "", port, resolver)
            kinds = {_address_kind(address) for address in addresses}
            if "blocked" in kinds or ("allowed-private" in kinds and not allow_private_network):
                raise SearchGatewayError("One Search 网关地址在验证前发生了不安全变化")
            status, headers, body = _pinned_provider_response(
                f"{normalized}/v1/providers", key, addresses[0], timeout
            )
        if status in {301, 302, 303, 307, 308}:
            raise SearchGatewayError("聚合检索验证不接受重定向，请填写最终网关地址")
        if status in {401, 403}:
            raise SearchGatewayError("osr_ 检索令牌无效或没有读取提供商的权限")
        if status < 200 or status >= 300:
            raise SearchGatewayError(f"One Search 返回 HTTP {status}")
        content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type not in {"application/json", "text/json"}:
            raise SearchGatewayError("One Search 的 /v1/providers 没有返回 JSON")
        declared = headers.get("Content-Length")
        if declared and int(declared) > MAX_RESPONSE_BYTES:
            raise SearchGatewayError("One Search 提供商列表响应过大")
    except SearchGatewayError:
        raise
    except HTTPError as error:
        if error.code in {401, 403}:
            raise SearchGatewayError("osr_ 检索令牌无效或没有读取提供商的权限") from error
        if error.code in {301, 302, 303, 307, 308}:
            raise SearchGatewayError("聚合检索验证不接受重定向，请填写最终网关地址") from error
        raise SearchGatewayError(f"One Search 返回 HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise SearchGatewayError(f"无法连接 One Search：{error}") from error
    except ValueError as error:
        raise SearchGatewayError("One Search 返回了无效的响应长度") from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise SearchGatewayError("One Search 提供商列表响应过大")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchGatewayError("One Search 返回的提供商列表不是有效 JSON") from error
    raw_providers = (
        payload.get("providers") or payload.get("data") or payload.get("items")
        if isinstance(payload, dict) else payload
    )
    if isinstance(raw_providers, dict):
        raw_providers = raw_providers.get("data") or raw_providers.get("items") or []
    if not isinstance(raw_providers, list):
        raise SearchGatewayError("One Search 提供商列表缺少 providers 数组")
    providers: list[str] = []
    for item in raw_providers:
        name = item if isinstance(item, str) else item.get("id") or item.get("name") or item.get("provider") if isinstance(item, dict) else None
        if isinstance(name, str) and name.strip():
            providers.append(name.strip()[:120])
    return normalized, sorted(set(providers), key=str.casefold)


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
        ctypes.byref(source), "Agent4Market One Search token", ctypes.byref(extra), None, None, 0x1,
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


def _binding(base_url: str) -> str:
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _keychain_service(project_root: Path) -> str:
    suffix = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"Agent4Market.OneSearch.{suffix}"


def save_gateway_secret(
    project_root: Path | str, token: str, base_url: str, *, system_name: str | None = None
) -> None:
    root = Path(project_root).resolve()
    key = _validate_token(token)
    system = (system_name or platform.system()).lower()
    record: dict[str, Any] = {
        "version": 1, "provider_id": PROVIDER_ID, "binding": _binding(base_url), "updated_at": _now(),
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
            raise SearchGatewayError("无法写入 macOS 钥匙串")
        record.update({"backend": "macos-keychain", "service": service, "account": PROVIDER_ID})
    else:
        record.update({"backend": "private-file", "secret": base64.b64encode(key.encode("utf-8")).decode("ascii")})
    _atomic_json(secret_path(root), record)


def load_gateway_secret(
    project_root: Path | str, base_url: str, *, system_name: str | None = None
) -> str | None:
    root = Path(project_root).resolve()
    path = secret_path(root)
    if not path.is_file():
        return None
    record = _read_object(path)
    if record.get("version") != 1 or record.get("provider_id") != PROVIDER_ID or record.get("binding") != _binding(base_url):
        return None
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


def load_gateway_settings(project_root: Path | str) -> dict[str, Any] | None:
    path = settings_path(project_root)
    if not path.is_file():
        return None
    value = _read_object(path)
    base_url = value.get("base_url")
    parsed = urlsplit(base_url) if isinstance(base_url, str) else None
    providers = value.get("providers", [])
    if (
        value.get("version") != 1 or value.get("provider_id") != PROVIDER_ID
        or parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or value.get("mode") not in ALLOWED_MODES
        or not isinstance(value.get("max_results"), int) or not 1 <= value["max_results"] <= 10
        or not isinstance(value.get("allow_private_network"), bool)
        or not isinstance(providers, list) or len(providers) > 100
        or any(not isinstance(provider, str) or not provider or len(provider) > 120 for provider in providers)
    ):
        raise SearchGatewayError("本地聚合检索配置格式无效")
    return value


def _mark_restart_required(project_root: Path | str) -> None:
    _atomic_json(restart_flag_path(project_root), {
        "version": 1, "provider_id": PROVIDER_ID, "required_at": _now(),
    })


def configure_search_gateway(
    project_root: Path | str,
    *,
    base_url: str,
    token: str | None,
    mode: str,
    max_results: int,
    allow_private_network: bool,
) -> dict[str, Any]:
    with _GATEWAY_CONFIG_LOCK:
        return _configure_search_gateway_unlocked(
            project_root, base_url=base_url, token=token, mode=mode,
            max_results=max_results, allow_private_network=allow_private_network,
        )


def _configure_search_gateway_unlocked(
    project_root: Path | str,
    *,
    base_url: str,
    token: str | None,
    mode: str,
    max_results: int,
    allow_private_network: bool,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if mode not in ALLOWED_MODES:
        raise SearchGatewayError("聚合模式仅支持并行、依次尝试或单提供商")
    if not isinstance(max_results, int) or not 1 <= max_results <= 10:
        raise SearchGatewayError("每次查询结果数必须是 1–10 的整数")
    provisional = normalize_gateway_url(base_url, allow_private_network=allow_private_network)
    key = (token or "").strip() or load_gateway_secret(root, provisional)
    if not key:
        raise SearchGatewayError("请填写 osr_ 检索令牌；已保存令牌只可用于同一个网关地址")
    normalized, providers = validate_search_gateway(
        provisional, key, allow_private_network=allow_private_network
    )
    if token and token.strip():
        save_gateway_secret(root, key, normalized)
    _atomic_json(settings_path(root), {
        "version": 1, "provider_id": PROVIDER_ID, "base_url": normalized,
        "mode": mode, "max_results": max_results,
        "allow_private_network": allow_private_network,
        "providers": providers, "updated_at": _now(),
    })
    _mark_restart_required(root)
    return search_gateway_settings_summary(root)


def clear_search_gateway(project_root: Path | str) -> dict[str, Any]:
    with _GATEWAY_CONFIG_LOCK:
        return _clear_search_gateway_unlocked(project_root)


def _clear_search_gateway_unlocked(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = secret_path(root)
    record: dict[str, Any] = {}
    if path.is_file():
        try:
            record = _read_object(path)
        except SearchGatewayError:
            record = {}
    if record.get("backend") == "macos-keychain":
        subprocess.run(
            ["security", "delete-generic-password", "-s", str(record.get("service", "")),
             "-a", str(record.get("account", ""))],
            check=False, capture_output=True, text=True,
        )
    path.unlink(missing_ok=True)
    settings_path(root).unlink(missing_ok=True)
    _mark_restart_required(root)
    return search_gateway_settings_summary(root)


def search_gateway_settings_summary(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    try:
        settings = load_gateway_settings(root)
    except SearchGatewayError as error:
        return {"configured": False, "status": "error", "provider_id": PROVIDER_ID, "error": str(error)}
    if settings is None:
        return {
            "configured": False, "status": "disabled", "provider_id": PROVIDER_ID,
            "restart_required": restart_flag_path(root).is_file(),
        }
    try:
        has_token = bool(load_gateway_secret(root, settings["base_url"]))
    except SearchGatewayError as error:
        return {"configured": False, "status": "error", "provider_id": PROVIDER_ID, "error": str(error)}
    return {
        "configured": has_token,
        "status": "configured" if has_token else "missing_token",
        "provider_id": PROVIDER_ID,
        "base_url": settings["base_url"],
        "mode": settings["mode"],
        "max_results": settings["max_results"],
        "allow_private_network": settings["allow_private_network"],
        "providers": settings.get("providers", []),
        "has_token": has_token,
        "updated_at": settings.get("updated_at"),
        "restart_required": restart_flag_path(root).is_file(),
    }


def search_gateway_runtime_environment(
    project_root: Path | str, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    environment = os.environ if environ is None else environ
    environment_base = environment.get(BASE_URL_ENV, "").strip()
    environment_token = environment.get(TOKEN_ENV, "").strip()
    if environment_base or environment_token:
        if not environment_base or not environment_token:
            raise SearchGatewayError("外部 One Search 环境变量必须同时提供网关地址和 osr_ 检索令牌")
        _validate_token(environment_token)
        return {}
    settings = load_gateway_settings(project_root)
    if settings is None:
        return {}
    token = load_gateway_secret(project_root, settings["base_url"])
    if not token:
        raise SearchGatewayError("One Search 配置缺少可用的 osr_ 检索令牌")
    _validate_token(token)
    return {
        BASE_URL_ENV: settings["base_url"], TOKEN_ENV: token,
        MODE_ENV: settings["mode"], MAX_RESULTS_ENV: str(settings["max_results"]),
        ALLOW_PRIVATE_ENV: "1" if settings["allow_private_network"] else "0",
    }


def mark_search_gateway_runtime_applied(project_root: Path | str) -> None:
    restart_flag_path(project_root).unlink(missing_ok=True)
