"""Application-managed Pi providers. Discovery is not a capability or execution probe."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from . import model_provider as legacy
from .cli_provider import (
    CLI_APIS,
    backend_files_available,
    validate_backend_record,
)

API_APIS = {"openai-completions", "openai-responses", "anthropic-messages"}
APIS = API_APIS  # Public compatibility: existing transport tests enumerate HTTP APIs from this name.
ALL_APIS = API_APIS | CLI_APIS
ROLES = {"director-research-scout", "director-readonly-reviewer"}
PROVIDER_PATTERN = re.compile(r"agent4market-[a-z0-9-]{1,64}\Z")


def bindings_path(root: Path | str) -> Path:
    return legacy.settings_path(root).with_name("model-recipient-bindings.json")


def cli_backends_path(root: Path | str) -> Path:
    return legacy.settings_path(root).with_name("cli-backends.json")


def recipient_bindings(root: Path | str, registry: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Non-secret identity ledger survives removals, so a key never changes recipient."""
    value = previous if previous is not None else legacy._read_object(bindings_path(root), missing={"version": 1, "providers": {}, "models": {}, "cli_launches": {}})
    value.setdefault("cli_launches", {})
    if (value.get("version") != 1 or not isinstance(value.get("providers"), dict)
            or not isinstance(value.get("models"), dict) or not isinstance(value.get("cli_launches"), dict)):
        raise legacy.ModelProviderError("供应商历史接收方记录无效")
    for provider in registry["providers"]:
        provider_binding = {"base_url": provider["base_url"], "api": provider["api"]}
        if value["providers"].get(provider["id"], provider_binding) != provider_binding:
            raise legacy.ModelProviderError("此供应商标识已绑定其他接收方，请新增供应商实例")
        value["providers"][provider["id"]] = provider_binding
        if provider["api"] in CLI_APIS:
            launch_binding = {
                "api": provider["api"],
                "executable_path": provider["executable_path"],
                "command": provider["command"],
                "args": provider["args"],
                "version": provider["version"],
                "launch_sha256": provider["launch_sha256"],
                "runner_policy_version": provider["runner_policy_version"],
            }
            if value["cli_launches"].get(provider["id"], launch_binding) != launch_binding:
                raise legacy.ModelProviderError("此 CLI 实例已绑定其他启动入口或版本，请新增供应商实例")
            value["cli_launches"][provider["id"]] = launch_binding
        for item in provider["models"]:
            key = f"{provider['id']}/{item['id']}"
            bound = recipient(provider, item)
            if value["models"].get(key, bound) != bound:
                raise legacy.ModelProviderError("此模型标识已绑定其他协议或接收方，请新增供应商实例")
            value["models"][key] = bound
    return value


def _restore_bytes(path: Path, previous: bytes | None, mode: int) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".model-rollback-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt": os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def configuration_transaction(root: Path | str, provider_ids: list[str], *, environ=None, home=None):
    paths = [legacy.settings_path(root), bindings_path(root), legacy.pi_agent_dir(environ=environ, home=home) / "models.json",
             *(legacy.secret_path(root, item) for item in provider_ids)]
    snapshots = []
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise legacy.ModelProviderError(f"配置目标不是常规文件：{path.name}")
        snapshots.append((path, path.read_bytes() if path.exists() else None, path.stat().st_mode & 0o777 if path.exists() else 0o600))
    # Validate the shared catalog before changing any credential or project setting.
    catalog = legacy._read_object(paths[2], missing={"providers": {}})
    if not isinstance(catalog.get("providers", {}), dict):
        raise legacy.ModelProviderError("Pi 模型目录的 providers 无效")
    keychain = []
    if legacy.platform.system().lower() == "darwin":
        for item in provider_ids:
            service = legacy._keychain_service(Path(root).resolve(), item)
            # Snapshot the canonical destination derived from trusted root and
            # provider identity.  A corrupt or mismatched metadata file must
            # never choose the lookup target, but save_model_secret() can still
            # overwrite this canonical keychain entry before a later file write
            # fails, so it must always be part of the transaction rollback set.
            response = subprocess.run(["security", "find-generic-password", "-s", service, "-a", item, "-w"], capture_output=True, text=True, check=False)
            if response.returncode not in {0, 44}:  # errSecItemNotFound is the only safe missing-credential case.
                raise legacy.ModelProviderError("无法读取原钥匙串凭据，配置未修改")
            keychain.append((service, item, response.stdout.rstrip("\r\n") if response.returncode == 0 else None))
    try:
        yield
    except BaseException as error:
        failures = []
        for service, item, previous in keychain:
            command = ["security", "delete-generic-password", "-s", service, "-a", item] if previous is None else ["security", "add-generic-password", "-U", "-s", service, "-a", item, "-w", previous]
            try:
                response = subprocess.run(command, capture_output=True, check=False)
                if response.returncode not in ({0, 44} if previous is None else {0}): failures.append("钥匙串")
            except OSError: failures.append("钥匙串")
        for path, previous, mode in reversed(snapshots):
            try:
                if (path.read_bytes() if path.exists() else None) != previous:
                    _restore_bytes(path, previous, mode)
            except OSError: failures.append(path.name)
        if failures:
            raise legacy.ModelProviderError("配置保存失败，部分回滚失败，请勿启动任务；检查磁盘/钥匙串后重新配置：" + ", ".join(failures)) from error
        raise


def model_id(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 200 or not value.isprintable() or any(c.isspace() for c in value):
        raise legacy.ModelProviderError("模型 ID 无效")
    return value


def model_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise legacy.ModelProviderError("模型配置必须是对象")
    result: dict[str, Any] = {"id": model_id(value.get("id"))}
    for key, default, maximum in (("context_window", 32000, 10_000_000), ("max_tokens", 4096, 1_000_000)):
        number = value.get(key, default)
        if type(number) is not int or not 1 <= number <= maximum:
            raise legacy.ModelProviderError(f"{key} 超出有效范围")
        result[key] = number
    if result["max_tokens"] > result["context_window"]:
        raise legacy.ModelProviderError("输出上限不能超过上下文窗口")
    for key, default in (("enabled", True), ("reasoning", False), ("image_input", False), ("tools", True)):
        if type(value.get(key, default)) is not bool:
            raise legacy.ModelProviderError(f"{key} 必须是布尔值")
        result[key] = value.get(key, default)
    if value.get("api"):
        if value["api"] not in APIS:
            raise legacy.ModelProviderError("不支持的模型协议")
        result["api"] = value["api"]
    result["metadata_source"] = value.get("metadata_source") if value.get("metadata_source") in {"user", "legacy-unverified", "cli-model-list"} else "user"
    if "cli_reasoning" in value:
        from .cli_model_catalog import validate_reasoning, thinking_levels
        result["cli_reasoning"] = validate_reasoning(value["cli_reasoning"])
        levels = thinking_levels(result["cli_reasoning"])
        if value.get("default_thinking_level") not in levels or result["reasoning"] != any(level != "off" for level in levels):
            raise legacy.ModelProviderError("CLI 默认思考强度无效")
        result["default_thinking_level"] = value["default_thinking_level"]
        display = value.get("display_name", result["id"])
        if not isinstance(display, str) or not 1 <= len(display) <= 200 or not display.isprintable():
            raise legacy.ModelProviderError("CLI 模型名称无效")
        result["display_name"] = display
        if result["metadata_source"] != "cli-model-list":
            raise legacy.ModelProviderError("CLI 模型能力来源无效")
    elif "default_thinking_level" in value or result["metadata_source"] == "cli-model-list":
        raise legacy.ModelProviderError("CLI 模型能力配置不完整")
    return result


def validate_registry(value: dict[str, Any]) -> dict[str, Any]:
    providers = value.get("providers")
    if value.get("version") != 3 or not isinstance(providers, list) or len(providers) > 32:
        raise legacy.ModelProviderError("本地供应商列表无效")
    ids: set[str] = set()
    keys: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict) or not isinstance(provider.get("id"), str) or not PROVIDER_PATTERN.fullmatch(provider["id"]):
            raise legacy.ModelProviderError("供应商实例标识无效")
        if provider["id"] in ids:
            raise legacy.ModelProviderError("供应商实例标识重复")
        ids.add(provider["id"])
        if (type(provider.get("enabled", True)) is not bool or type(provider.get("allow_private_network", False)) is not bool
                or not isinstance(provider.get("name"), str) or not 1 <= len(provider["name"].strip()) <= 80):
            raise legacy.ModelProviderError("供应商名称或启用状态无效")
        if provider.get("api") not in ALL_APIS or not isinstance(provider.get("base_url"), str) or not provider["base_url"].startswith(("https://", "http://")):
            raise legacy.ModelProviderError("供应商地址或协议无效")
        is_cli = provider["api"] in CLI_APIS
        if is_cli:
            validate_backend_record(provider)
            if provider.get("allow_private_network", False):
                raise legacy.ModelProviderError("CLI 提供者不能启用私有网关")
        models = provider.get("models")
        if not isinstance(models, list) or len(models) > legacy.MAX_MODEL_COUNT:
            raise legacy.ModelProviderError("供应商模型列表无效")
        seen: set[str] = set()
        for item in models:
            record = model_record(item)
            if "cli_reasoning" in record and provider["api"] != "codex-cli":
                raise legacy.ModelProviderError("思考目录只适用于 Codex CLI")
            if is_cli and (
                record["tools"] is not True
                or record["image_input"] is not False
                or (record["reasoning"] is not False and "cli_reasoning" not in record)
                or record["context_window"] != 32000
                or record["max_tokens"] != 4096
                or record.get("api", provider["api"]) != provider["api"]
            ):
                raise legacy.ModelProviderError("CLI 模型能力声明无效")
            if record["id"] in seen:
                raise legacy.ModelProviderError("同一供应商的模型 ID 重复")
            seen.add(record["id"])
            keys.add(f"{provider['id']}/{record['id']}")
    if value.get("default_model") and value["default_model"] not in keys:
        raise legacy.ModelProviderError("默认模型不在已配置目录中")
    role_models = value.get("role_models", {})
    if not isinstance(role_models, dict) or any(role not in ROLES or not isinstance(key, str) or key not in keys for role, key in role_models.items()):
        raise legacy.ModelProviderError("只读角色模型配置无效")
    return value


def load_registry(root: Path | str) -> dict[str, Any]:
    settings = legacy.load_model_settings(root)
    if not settings or settings.get("version") == 2:
        return {"version": 3, "providers": [], "default_model": None, "role_models": {}}
    if settings["version"] == 3:
        return settings
    # Virtual migration: leave v1 and its secret untouched until an explicit save.
    models = []
    for item in settings["models"]:
        record = model_record({**item, "context_window": 128000, "max_tokens": 32768, "metadata_source": "legacy-unverified"})
        advertised = item.get("supported_endpoint_types", [])
        if "anthropic" in advertised and "openai" not in advertised:
            record["api"] = "anthropic-messages"
        models.append(record)
    return {"version": 3, "default_model": f"{legacy.PROVIDER_ID}/{settings['selected_model']}", "role_models": {},
            "providers": [{"id": legacy.PROVIDER_ID, "name": "NewAPI", "vendor": "newapi", "api": "openai-completions",
                           "base_url": settings["base_url"], "enabled": True, "models": models,
                           "discovered_models": settings["models"], "allow_private_network": bool(settings.get("allow_private_network")),
                           "updated_at": settings.get("updated_at")} ]}


def key_env(provider_id: str) -> str:
    return legacy.API_KEY_ENV if provider_id == legacy.PROVIDER_ID else "AGENT4MARKET_KEY_" + hashlib.sha256(provider_id.encode()).hexdigest()[:24].upper()


def recipient(provider: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, str]:
    return {"provider_id": str(provider["id"]), "base_url": str(provider["base_url"]),
            "api": str(item.get("api", provider["api"])), "model_id": str(item["id"])}


def summary(root: Path | str) -> dict[str, Any]:
    registry = load_registry(root)
    providers = []
    for provider in registry["providers"]:
        if provider["api"] in CLI_APIS:
            available = backend_files_available(provider, verify_hash=True)
            providers.append({
                **provider,
                "has_api_key": False,
                "authentication": "cli-managed-unverified",
                "status": "disabled" if not provider.get("enabled", True) else "configured" if available else "unavailable",
            })
        else:
            try:
                has_key = bool(legacy.load_model_secret(root, provider["base_url"], provider_id=provider["id"]))
            except legacy.ModelProviderError:
                has_key = False  # One damaged credential must not hide the other instances or prevent its removal.
            providers.append({**provider, "has_api_key": has_key,
                              "status": "disabled" if not provider.get("enabled", True) else "configured" if has_key else "missing_key"})
    default = registry.get("default_model") or ""
    selected = next((p for p in providers if default.startswith(p["id"] + "/")), {})
    selected_id = default[len(selected.get("id", "")) + 1:] if selected else ""
    selected_enabled = any(m["id"] == selected_id and m.get("enabled", True) and m.get("tools", True) for m in selected.get("models", []))
    ready = selected.get("status") == "configured" and selected_enabled
    return {"version": 3, "configured": bool(providers), "status": "configured" if ready else "missing_default" if providers else "unconfigured",
            "providers": providers, "default_model": default or None, "role_models": registry.get("role_models", {}),
            "provider_type": selected.get("vendor", "api"), "provider_id": selected.get("id", legacy.PROVIDER_ID),
            "base_url": selected.get("base_url", ""), "selected_model": selected_id, "models": selected.get("models", []),
            "has_api_key": bool(selected.get("has_api_key")), "allow_private_network": bool(selected.get("allow_private_network")),
            **({"authentication": selected["authentication"]} if selected.get("authentication") else {})}


def available_models(root: Path | str) -> dict[str, dict[str, str]]:
    result = {}
    for provider in summary(root)["providers"]:
        if provider["status"] != "configured":
            continue
        for item in provider["models"]:
            if item.get("enabled", True) and item.get("tools", True):
                result[f"{provider['id']}/{item['id']}"] = recipient(provider, item)
    return result


def sync_pi_catalog(registry: dict[str, Any], *, removed: tuple[str, ...] = (), environ=None, home=None) -> None:
    path = legacy.pi_agent_dir(environ=environ, home=home) / "models.json"
    catalog = legacy._read_object(path, missing={"providers": {}})
    providers = catalog.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise legacy.ModelProviderError("Pi 模型目录的 providers 无效")
    for provider_id in removed:
        providers.pop(provider_id, None)
    for provider in registry["providers"]:
        records = []
        if provider.get("enabled", True):
            for item in provider["models"]:
                if not item.get("enabled", True) or not item.get("tools", True):
                    continue
                api = item.get("api", provider["api"])
                is_cli = provider["api"] in CLI_APIS
                records.append({"id": item["id"], "name": item.get("display_name", item["id"]), "api": api,
                                "baseUrl": provider["base_url"] if is_cli or api == "anthropic-messages" else provider["base_url"] + "/v1",
                                "reasoning": item.get("reasoning", False), "input": ["text"] if is_cli else ["text", "image"] if item.get("image_input") else ["text"],
                                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                                "contextWindow": item.get("context_window", 32000), "maxTokens": item.get("max_tokens", 4096)})
                if is_cli and "cli_reasoning" in item:
                    from .cli_model_catalog import THINKING_LEVELS, effort_for_level
                    records[-1]["thinkingLevelMap"] = {
                        level: effort_for_level(level) if effort_for_level(level) in item["cli_reasoning"]["supported_efforts"] else None
                        for level in THINKING_LEVELS
                    }
        providers.pop(provider["id"], None)
        if records:
            providers[provider["id"]] = {
                "apiKey": "cli-managed-auth" if provider["api"] in CLI_APIS else f"${key_env(provider['id'])}",
                "models": records,
            }
    legacy._atomic_json(path, catalog)


def configure_provider(root: Path | str, *, base_url: str, api_key: str | None, selected_model: str,
                       allow_private_network: bool, provider_id: str | None = legacy.PROVIDER_ID,
                       name: str = "NewAPI", vendor: str = "newapi", api: str = "openai-completions",
                       models: list[dict[str, Any]] | None = None, discovered_models: list[dict[str, Any]] | None = None,
                       enabled: bool = True, make_default: bool = True,
                       role_models: dict[str, str] | None = None, environ=None, home=None, resolver=None, opener=None) -> dict[str, Any]:
    if api not in API_APIS or type(enabled) is not bool or type(make_default) is not bool:
        raise legacy.ModelProviderError("供应商协议或启用状态无效")
    provider_id = provider_id or f"agent4market-{uuid.uuid4().hex}"
    if not PROVIDER_PATTERN.fullmatch(provider_id):
        raise legacy.ModelProviderError("供应商实例标识无效")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise legacy.ModelProviderError("请填写 1–80 字的供应商名称")
    resolver_options = {"resolver": resolver} if resolver else {}
    normalized = legacy.normalize_base_url(base_url, allow_private_network=allow_private_network, **resolver_options)
    with legacy._MODEL_CONFIG_LOCK:
        registry = load_registry(root)
        bindings = recipient_bindings(root, registry)
        previous = next((p for p in registry["providers"] if p["id"] == provider_id), None)
        if previous and previous.get("api") in CLI_APIS:
            raise legacy.ModelProviderError("API 供应商不能覆盖已有 CLI 实例；请新增供应商实例")
        # A model key must never acquire a new recipient, including after task suspension.
        if previous and (previous["base_url"] != normalized or previous["api"] != api):
            raise legacy.ModelProviderError("更换接收地址或协议请新增供应商实例；旧任务继续绑定原接收方")
        key = (api_key or "").strip() or legacy.load_model_secret(root, normalized, provider_id=provider_id)
        if not key or len(key) > 4096:
            raise legacy.ModelProviderError("首次配置此供应商实例必须填写有效的 API Key")
        discovered = previous.get("discovered_models", []) if previous else []
        if discovered_models is not None:
            if not isinstance(discovered_models, list) or len(discovered_models) > legacy.MAX_MODEL_COUNT:
                raise legacy.ModelProviderError("发现目录无效或超过 500 个")
            discovered = []
            for item in discovered_models:
                if not isinstance(item, dict): raise legacy.ModelProviderError("发现目录条目无效")
                record = {"id": model_id(item.get("id"))}
                if isinstance(item.get("owned_by"), str): record["owned_by"] = item["owned_by"][:120]
                discovered.append(record)  # Catalog IDs are not capability declarations or enabled models.
            discovered = list({item["id"]: item for item in discovered}.values())
        if models is None:
            normalized, discovered = legacy.discover_models(normalized, key, allow_private_network=allow_private_network,
                                                           api=api, opener=opener, **resolver_options)
            models = []
            for item in discovered:
                item = {**item, "metadata_source": "legacy-unverified"}
                if vendor == "newapi" and item.get("supported_endpoint_types") == ["anthropic"]:
                    item["api"] = "anthropic-messages"
                models.append(item)
        if not isinstance(models, list) or len(models) > legacy.MAX_MODEL_COUNT:
            raise legacy.ModelProviderError("模型列表无效或超过 500 个")
        records = [model_record(item) for item in models]
        if not any(item["id"] == selected_model for item in records):
            raise legacy.ModelProviderError("默认模型必须属于本实例的已配置模型")
        if make_default and (not enabled or not any(m["id"] == selected_model and m["enabled"] and m["tools"] for m in records)):
            raise legacy.ModelProviderError("默认任务模型必须启用且允许工具调用")
        if previous:
            old_apis = {m["id"]: m.get("api", previous["api"]) for m in previous["models"]}
            if any(m["id"] in old_apis and old_apis[m["id"]] != m.get("api", api) for m in records):
                raise legacy.ModelProviderError("更换模型协议请新增供应商实例")
        provider = {"id": provider_id, "name": name.strip(), "vendor": str(vendor)[:80], "api": api, "base_url": normalized,
                    "enabled": enabled, "models": records, "discovered_models": discovered,
                    "allow_private_network": allow_private_network, "updated_at": legacy._now()}
        registry["providers"] = [p for p in registry["providers"] if p["id"] != provider_id] + [provider]
        if make_default:
            registry["default_model"] = f"{provider_id}/{selected_model}"
        if role_models is not None:
            registry["role_models"] = role_models
        validate_registry(registry)
        bindings = recipient_bindings(root, registry, bindings)
        with configuration_transaction(root, [provider_id], environ=environ, home=home):
            legacy.save_model_secret(root, key, normalized, provider_id=provider_id)
            sync_pi_catalog(registry, environ=environ, home=home)
            legacy._atomic_json(bindings_path(root), bindings)
            legacy._atomic_json(legacy.settings_path(root), registry)
    return {**summary(root), "saved_provider_id": provider_id}


def configure_cli_provider(
    root: Path | str,
    *,
    provider_type: str,
    selected_model: str,
    backend: Mapping[str, Any],
    model_metadata: Mapping[str, Any] | None = None,
    provider_id: str | None = None,
    enabled: bool = True,
    make_default: bool = True,
    environ=None,
    home=None,
) -> dict[str, Any]:
    """Add a discovered CLI as a v3 provider without touching API credentials."""
    if provider_type not in CLI_APIS or backend.get("api") != provider_type:
        raise legacy.ModelProviderError("CLI 提供者类型与检测结果不一致")
    if type(enabled) is not bool or type(make_default) is not bool:
        raise legacy.ModelProviderError("CLI 提供者启用状态无效")
    if make_default and not enabled:
        raise legacy.ModelProviderError("停用的 CLI 不能设为默认任务模型")
    validate_backend_record(backend)
    selected_model = model_id(selected_model)
    if model_metadata is not None and set(model_metadata) != {"display_name", "cli_reasoning", "default_thinking_level", "reasoning", "metadata_source"}:
        raise legacy.ModelProviderError("CLI 模型目录字段无效")
    root = Path(root).resolve()

    with legacy._MODEL_CONFIG_LOCK:
        registry = load_registry(root)
        bindings = recipient_bindings(root, registry)
        providers = registry["providers"]
        identity_fields = (
            "api", "vendor", "base_url", "authentication", "executable_path", "command",
            "args", "version", "launch_sha256", "runner_policy_version",
        )

        if provider_id is not None:
            if not PROVIDER_PATTERN.fullmatch(provider_id):
                raise legacy.ModelProviderError("供应商实例标识无效")
            previous = next((item for item in providers if item["id"] == provider_id), None)
        else:
            previous = next(
                (
                    item for item in providers
                    if item.get("api") == provider_type
                    and all(item.get(field) == backend.get(field) for field in identity_fields)
                ),
                None,
            )
            if previous:
                provider_id = previous["id"]
            else:
                base_id = f"agent4market-{provider_type}"
                used_ids = {item["id"] for item in providers}
                historic_launches = bindings.get("cli_launches", {})
                if base_id not in used_ids and (
                    base_id not in historic_launches
                    or all(historic_launches[base_id].get(field) == backend.get(field) for field in identity_fields)
                ):
                    provider_id = base_id
                else:
                    provider_id = f"agent4market-{provider_type}-{uuid.uuid4().hex}"
                previous = None
        assert provider_id is not None

        if previous:
            if previous.get("api") not in CLI_APIS:
                raise legacy.ModelProviderError("CLI 提供者不能覆盖已有 API 实例；请新增供应商实例")
            if previous.get("api") != provider_type:
                raise legacy.ModelProviderError("一个 CLI 实例不能改为另一种 CLI；请新增供应商实例")
            if any(previous.get(field) != backend.get(field) for field in identity_fields):
                raise legacy.ModelProviderError("CLI 启动入口或版本已变化；请新增供应商实例")

        cli_model = model_record({
            "id": selected_model,
            "enabled": True,
            "reasoning": False,
            "image_input": False,
            "tools": True,
            "context_window": 32000,
            "max_tokens": 4096,
            "metadata_source": "user",
            **(model_metadata or {}),
        })
        preserved_models = [] if previous is None else [
            model_record(item) for item in previous.get("models", []) if item.get("id") != selected_model
        ]
        provider = {
            "id": provider_id,
            "name": "Claude Code" if provider_type == "claude-code" else "Codex CLI",
            **{field: backend[field] for field in identity_fields},
            "enabled": enabled,
            "allow_private_network": False,
            "models": [*preserved_models, cli_model],
            "updated_at": legacy._now(),
        }
        registry["providers"] = [item for item in providers if item["id"] != provider_id] + [provider]
        if make_default:
            registry["default_model"] = f"{provider_id}/{selected_model}"
        validate_registry(registry)
        bindings = recipient_bindings(root, registry, bindings)
        # CLI configuration has no project secret and must not inspect CLI login state.
        with configuration_transaction(root, [], environ=environ, home=home):
            sync_pi_catalog(registry, environ=environ, home=home)
            legacy._atomic_json(bindings_path(root), bindings)
            legacy._atomic_json(legacy.settings_path(root), registry)
    return {**summary(root), "saved_provider_id": provider_id}


def remove_provider(root: Path | str, provider_id: str | None = None, *, environ=None, home=None) -> dict[str, Any]:
    with legacy._MODEL_CONFIG_LOCK:
        registry = load_registry(root)
        bindings = recipient_bindings(root, registry)
        ids = [p["id"] for p in registry["providers"] if provider_id is None or p["id"] == provider_id]
        if provider_id and not ids:
            raise legacy.ModelProviderError("供应商实例不存在")
        if provider_id is None and legacy.PROVIDER_ID not in ids:
            ids.append(legacy.PROVIDER_ID)  # Explicit reset may also remove the old v2 selection.
        api_ids = [
            item for item in ids
            if (provider := next((p for p in registry["providers"] if p["id"] == item), None)) is None
            or provider.get("api") not in CLI_APIS
        ]
        secrets = {}
        for item in api_ids:
            try:
                secrets[item] = legacy._read_object(legacy.secret_path(root, item))
            except legacy.ModelProviderError:
                secrets[item] = {}  # Corrupt credentials must remain removable.
        registry["providers"] = [p for p in registry["providers"] if p["id"] not in ids]
        if (registry.get("default_model") or "").split("/", 1)[0] in ids:
            registry["default_model"] = None
        registry["role_models"] = {r: m for r, m in registry.get("role_models", {}).items() if m.split("/", 1)[0] not in ids}
        with configuration_transaction(root, api_ids, environ=environ, home=home):
            sync_pi_catalog(registry, removed=tuple(ids), environ=environ, home=home)
            legacy._atomic_json(bindings_path(root), bindings)
            if registry["providers"]:
                legacy._atomic_json(legacy.settings_path(root), registry)
            else:
                legacy.settings_path(root).unlink(missing_ok=True)
            for item in api_ids:
                path = legacy.secret_path(root, item)
                record = secrets[item]
                if (record.get("backend") == "macos-keychain" and record.get("account") == item
                        and record.get("service") == legacy._keychain_service(Path(root).resolve(), item)):
                    completed = subprocess.run(["security", "delete-generic-password", "-s", str(record["service"]),
                                                "-a", item], check=False, capture_output=True)
                    if completed.returncode not in {0, 44}: raise legacy.ModelProviderError("钥匙串清理失败，删除已回滚")
                path.unlink(missing_ok=True)
    return summary(root)


def runtime_configuration(root: Path | str, *, environ=None, home=None) -> tuple[str, dict[str, str]] | None:
    settings = legacy.load_model_settings(root)
    if not settings:
        raise legacy.ModelProviderError("尚未配置任务模型；请在设置中明确选择 API 或 CLI 供应商，不会沿用全局默认模型")
    if settings.get("version") == 2:
        raise legacy.ModelProviderError("旧版 CLI 配置尚未绑定安全执行策略；请重新检测、填写模型 ID 并保存为 CLI 供应商")
    registry = load_registry(root)
    recipient_bindings(root, registry)  # Reject manual recipient drift as well as changes through the UI.
    configured_cli = [provider for provider in registry["providers"] if provider["api"] in CLI_APIS and provider.get("enabled", True)]
    cli_providers = [provider for provider in configured_cli if backend_files_available(provider, verify_hash=True)]
    invalid_cli_ids = {provider["id"] for provider in configured_cli if provider not in cli_providers}
    available = available_models(root)
    available = {
        key: value for key, value in available.items()
        if key.split("/", 1)[0] not in invalid_cli_ids
    }
    selected = registry.get("default_model")
    if selected not in available:
        if (selected or "").split("/", 1)[0] in invalid_cli_ids:
            raise legacy.ModelProviderError("默认 CLI 的启动文件已缺失或变化；请重新检测并新增实例，不会自动切换供应商")
        raise legacy.ModelProviderError("默认模型已停用或缺少凭据，请重新选择；不会自动切换供应商")
    roles = registry.get("role_models", {})
    if any(key not in available for key in roles.values()):
        raise legacy.ModelProviderError("只读角色模型已停用或缺少凭据")
    available_providers = {item["provider_id"] for item in available.values()}
    sync_pi_catalog(registry, environ=environ, home=home)
    cli_manifest = {
        "version": 1,
        "providers": [
            {
                key: provider[key]
                for key in (
                    "id", "api", "base_url", "authentication", "executable_path", "command", "args",
                    "version", "launch_sha256", "runner_policy_version",
                )
            } | {
                "models": [
                    model_record(model) for model in provider["models"]
                    if model.get("enabled", True) and model.get("tools", True)
                ]
            }
            for provider in cli_providers if provider["id"] in available_providers
        ],
    }
    cli_path = cli_backends_path(root)
    legacy._atomic_json(cli_path, cli_manifest)
    managed_path = legacy.settings_path(root).with_name("managed-models.json")
    legacy._atomic_json(managed_path, available)
    environment = {"AGENT4MARKET_MANAGED_MODELS_FILE": str(managed_path),
                   "AGENT4MARKET_MANAGED_MODELS_SHA256": hashlib.sha256(managed_path.read_bytes()).hexdigest(),
                   "AGENT4MARKET_ROLE_MODELS": json.dumps(roles)}
    if cli_manifest["providers"]:
        environment.update({"AGENT4MARKET_CLI_BACKENDS_FILE": str(cli_path),
                            "AGENT4MARKET_CLI_BACKENDS_SHA256": hashlib.sha256(cli_path.read_bytes()).hexdigest()})
    for provider in registry["providers"]:
        if provider["id"] in available_providers and provider["api"] not in CLI_APIS:
            key = legacy.load_model_secret(root, provider["base_url"], provider_id=provider["id"])
            if key:
                environment[key_env(provider["id"])] = key
    return str(selected), environment
