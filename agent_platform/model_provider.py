from __future__ import annotations

import base64
import ctypes
import hashlib
import ipaddress
import json
import os
import platform
import socket
import subprocess
import tempfile
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


PROVIDER_ID = "agent4market-newapi"
API_KEY_ENV = "AGENT4MARKET_NEWAPI_API_KEY"
MAX_MODELS_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MODEL_COUNT = 500
_MODEL_CONFIG_LOCK = threading.Lock()


class ModelProviderError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def _read_object(path: Path, *, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {} if missing is None else missing
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelProviderError(f"无法读取模型配置 {path.name}：{error}") from error
    if not isinstance(value, dict):
        raise ModelProviderError(f"模型配置 {path.name} 必须是 JSON 对象")
    return value


def settings_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "model-provider.json"


def secret_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / ".pi" / "director-runtime" / "model-provider.secret"


def pi_agent_dir(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get("PI_CODING_AGENT_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else (home or Path.home()).resolve() / ".pi" / "agent"


def _address_is_internal(address: str) -> bool:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    return not parsed.is_global


def normalize_base_url(
    raw: str,
    *,
    allow_private_network: bool,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    value = raw.strip().rstrip("/")
    if not value or len(value) > 500:
        raise ModelProviderError("请填写有效的 NewAPI 网关地址")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelProviderError("网关地址必须使用 http:// 或 https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelProviderError("网关地址不能包含账号、密码、查询参数或片段")
    if parsed.path.endswith("/v1"):
        parsed = parsed._replace(path=parsed.path[:-3])
    normalized_path = parsed.path.rstrip("/")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = {
            item[4][0]
            for item in resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        }
    except OSError as error:
        raise ModelProviderError(f"无法解析网关域名：{error}") from error
    if not addresses:
        raise ModelProviderError("网关域名没有可用地址")
    has_internal = any(_address_is_internal(address) for address in addresses)
    if has_internal and not allow_private_network:
        raise ModelProviderError("该地址指向本机或局域网；确认可信后勾选“允许本机/局域网网关”")
    if parsed.scheme == "http" and not has_internal:
        raise ModelProviderError("公网网关必须使用 HTTPS")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", "")).rstrip("/")


def discover_models(
    base_url: str,
    api_key: str,
    *,
    allow_private_network: bool,
    timeout: float = 15.0,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    opener: Callable[..., Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    normalized = normalize_base_url(
        base_url, allow_private_network=allow_private_network, resolver=resolver
    )
    key = api_key.strip()
    if not key or len(key) > 4096:
        raise ModelProviderError("请填写有效的 API Key")
    request = Request(
        f"{normalized}/v1/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
        method="GET",
    )
    open_request = opener or build_opener(_NoRedirect()).open
    try:
        with open_request(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if status in {301, 302, 303, 307, 308}:
                raise ModelProviderError("模型发现请求不接受重定向，请填写最终网关地址")
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "text/json"}:
                raise ModelProviderError("网关的 /v1/models 没有返回 JSON")
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > MAX_MODELS_RESPONSE_BYTES:
                        raise ModelProviderError("模型列表响应过大")
                except ValueError as error:
                    raise ModelProviderError("网关返回了无效的 Content-Length") from error
            body = response.read(MAX_MODELS_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ModelProviderError("API Key 无效或没有读取模型列表的权限") from error
        if error.code in {301, 302, 303, 307, 308}:
            raise ModelProviderError("模型发现请求不接受重定向，请填写最终网关地址") from error
        raise ModelProviderError(f"网关返回 HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ModelProviderError(f"无法连接 NewAPI 网关：{error}") from error
    if len(body) > MAX_MODELS_RESPONSE_BYTES:
        raise ModelProviderError("模型列表响应过大")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelProviderError("网关返回的模型列表不是有效 JSON") from error
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelProviderError("网关模型列表缺少 data 数组")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
            continue
        model_id = model_id.strip()
        if not model_id.isprintable() or any(character.isspace() for character in model_id):
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        model = {"id": model_id}
        if isinstance(item.get("owned_by"), str) and item["owned_by"].strip():
            model["owned_by"] = item["owned_by"].strip()[:120]
        endpoint_types = item.get("supported_endpoint_types")
        if isinstance(endpoint_types, list):
            supported = sorted({
                value.strip().lower() for value in endpoint_types
                if isinstance(value, str) and value.strip().lower() in {"openai", "anthropic"}
            })
            if supported:
                model["supported_endpoint_types"] = supported
        models.append(model)
        if len(models) >= MAX_MODEL_COUNT:
            break
    models.sort(key=lambda item: item["id"].casefold())
    if not models:
        raise ModelProviderError("网关没有返回可选择的模型")
    return normalized, models


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


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
        ctypes.byref(source), "Agent4Market NewAPI key", ctypes.byref(extra), None, None, 0x1,
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


def _secret_binding(base_url: str) -> str:
    return hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _keychain_service(project_root: Path) -> str:
    suffix = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"Agent4Market.NewAPI.{suffix}"


def save_model_secret(
    project_root: Path | str, api_key: str, base_url: str, *, system_name: str | None = None
) -> None:
    root = Path(project_root).resolve()
    key = api_key.strip()
    if not key:
        raise ModelProviderError("API Key 不能为空")
    system = (system_name or platform.system()).lower()
    binding = _secret_binding(base_url)
    path = secret_path(root)
    if system == "windows":
        entropy = hashlib.sha256(str(root).encode("utf-8")).digest()
        encrypted = _dpapi_protect(key.encode("utf-8"), entropy)
        record = {"version": 1, "backend": "windows-dpapi", "binding": binding,
                  "ciphertext": base64.b64encode(encrypted).decode("ascii")}
    elif system == "darwin":
        service = _keychain_service(root)
        completed = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", PROVIDER_ID, "-w", key],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise ModelProviderError("无法写入 macOS 钥匙串")
        record = {"version": 1, "backend": "macos-keychain", "binding": binding,
                  "service": service, "account": PROVIDER_ID}
    else:
        record = {"version": 1, "backend": "private-file", "binding": binding,
                  "secret": base64.b64encode(key.encode("utf-8")).decode("ascii")}
    _atomic_json(path, record)


def load_model_secret(
    project_root: Path | str, base_url: str, *, system_name: str | None = None
) -> str | None:
    root = Path(project_root).resolve()
    path = secret_path(root)
    if not path.is_file():
        return None
    record = _read_object(path)
    if record.get("version") != 1 or record.get("binding") != _secret_binding(base_url):
        return None
    backend = record.get("backend")
    try:
        if backend == "windows-dpapi":
            encrypted = base64.b64decode(str(record.get("ciphertext", "")), validate=True)
            entropy = hashlib.sha256(str(root).encode("utf-8")).digest()
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


def load_model_settings(project_root: Path | str) -> dict[str, Any] | None:
    path = settings_path(project_root)
    if not path.is_file():
        return None
    value = _read_object(path)
    if (
        value.get("version") != 1
        or value.get("provider_id") != PROVIDER_ID
        or not isinstance(value.get("base_url"), str)
        or not value["base_url"].startswith(("http://", "https://"))
        or not isinstance(value.get("selected_model"), str)
        or not value["selected_model"]
        or len(value["selected_model"]) > 200
        or not isinstance(value.get("models"), list)
    ):
        raise ModelProviderError("本地模型配置格式无效")
    model_ids = {
        item.get("id") for item in value["models"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if value["selected_model"] not in model_ids:
        raise ModelProviderError("本地模型配置中的已选模型不在模型列表中")
    return value


def model_settings_summary(project_root: Path | str) -> dict[str, Any]:
    try:
        settings = load_model_settings(project_root)
    except ModelProviderError as error:
        return {"configured": False, "status": "error", "error": str(error)}
    if settings is None:
        return {"configured": False, "status": "unconfigured", "provider_id": PROVIDER_ID}
    base_url = settings["base_url"]
    has_secret = bool(load_model_secret(project_root, base_url))
    return {
        "configured": True,
        "status": "configured" if has_secret else "missing_key",
        "provider_id": PROVIDER_ID,
        "base_url": base_url,
        "selected_model": settings["selected_model"],
        "models": [item for item in settings["models"] if isinstance(item, dict) and isinstance(item.get("id"), str)],
        "allow_private_network": bool(settings.get("allow_private_network")),
        "has_api_key": has_secret,
        "updated_at": settings.get("updated_at"),
    }


def _pi_model_record(base_url: str, model: Mapping[str, Any]) -> dict[str, Any]:
    endpoint_types = model.get("supported_endpoint_types")
    advertised = set(endpoint_types) if isinstance(endpoint_types, list) else set()
    api = "anthropic-messages" if "anthropic" in advertised and "openai" not in advertised else "openai-completions"
    api_base_url = base_url if api == "anthropic-messages" else f"{base_url}/v1"
    model_id = str(model["id"])
    return {
        "id": model_id,
        "name": model_id,
        "api": api,
        "baseUrl": api_base_url,
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 128_000,
        "maxTokens": 32_768,
    }


def _merge_provider_files(
    base_url: str,
    discovered_models: list[dict[str, Any]],
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> None:
    agent_dir = pi_agent_dir(environ=environ, home=home)
    models_path = agent_dir / "models.json"
    models = _read_object(models_path, missing={"providers": {}})
    model_providers = models.setdefault("providers", {})
    if not isinstance(model_providers, dict):
        raise ModelProviderError("Pi models.json 的 providers 配置无效")
    model_providers[PROVIDER_ID] = {
        "apiKey": f"${API_KEY_ENV}",
        "models": [_pi_model_record(base_url, model) for model in discovered_models],
    }
    _atomic_json(models_path, models)


def clear_model_provider(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    with _MODEL_CONFIG_LOCK:
        root = Path(project_root).resolve()
        try:
            secret_record = _read_object(secret_path(root)) if secret_path(root).is_file() else {}
        except ModelProviderError:
            secret_record = {}
        try:
            settings_path(root).unlink(missing_ok=True)
            secret_path(root).unlink(missing_ok=True)
        except OSError as error:
            raise ModelProviderError(f"无法清除本地模型设置：{error}") from error

        if secret_record.get("backend") == "macos-keychain":
            subprocess.run(
                ["security", "delete-generic-password", "-s", str(secret_record.get("service", "")),
                 "-a", str(secret_record.get("account", ""))],
                check=False, capture_output=True, text=True,
            )

        models_path = pi_agent_dir(environ=environ, home=home) / "models.json"
        if models_path.is_file():
            models = _read_object(models_path)
            providers = models.get("providers")
            if isinstance(providers, dict) and PROVIDER_ID in providers:
                providers.pop(PROVIDER_ID, None)
                _atomic_json(models_path, models)
        return model_settings_summary(root)


def configure_model_provider(
    project_root: Path | str,
    *,
    base_url: str,
    api_key: str | None,
    selected_model: str,
    allow_private_network: bool,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    normalized_for_secret = normalize_base_url(
        base_url, allow_private_network=allow_private_network, resolver=resolver
    )
    key = (api_key or "").strip() or load_model_secret(root, normalized_for_secret)
    if not key:
        raise ModelProviderError("首次配置或更换网关时必须填写 API Key")
    normalized, models = discover_models(
        normalized_for_secret,
        key,
        allow_private_network=allow_private_network,
        resolver=resolver,
        opener=opener,
    )
    if selected_model not in {item["id"] for item in models}:
        raise ModelProviderError("选择的模型不在网关最新模型列表中，请重新获取模型")
    with _MODEL_CONFIG_LOCK:
        _merge_provider_files(normalized, models, environ=environ, home=home)
        save_model_secret(root, key, normalized)
        settings = {
            "version": 1,
            "provider_id": PROVIDER_ID,
            "base_url": normalized,
            "selected_model": selected_model,
            "models": models,
            "allow_private_network": allow_private_network,
            "updated_at": _now(),
        }
        _atomic_json(settings_path(root), settings)
        return model_settings_summary(root)


def model_runtime_configuration(project_root: Path | str) -> tuple[str, dict[str, str]] | None:
    settings = load_model_settings(project_root)
    if settings is None:
        return None
    key = load_model_secret(project_root, settings["base_url"])
    if not key:
        return None
    model = f"{PROVIDER_ID}/{settings['selected_model']}"
    return model, {API_KEY_ENV: key}
