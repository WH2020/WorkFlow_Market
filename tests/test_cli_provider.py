from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_platform import cli_provider
from agent_platform import model_provider
from agent_platform import model_registry
from tests.test_model_provider import public_resolver


class ProbeFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.appdata = root / "appdata"
        self.npm = self.appdata / "npm"
        self.node = root / "runtime" / "node" / "node.exe"
        self.claude = self.npm / "claude.cmd"
        self.claude_js = self.npm / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        self.claude_native = self.npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        self.codex = root / "bin" / "codex.exe"
        for path, content in (
            (self.node, b"synthetic-node"),
            (self.claude, b"synthetic-shim"),
            (self.claude_js, b"synthetic-claude"),
            (self.codex, b"synthetic-codex"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.calls: list[list[str]] = []
        self.missing_flags: set[str] = set()

    @property
    def environ(self) -> dict[str, str]:
        return {
            "APPDATA": str(self.appdata),
            "PATH": str(self.root / "bin"),
            "NODE_OPTIONS": "--require=untrusted-hook.js",
        }

    def which(self, name: str, *, path: str | None = None) -> str | None:
        del path
        return {
            "claude": str(self.claude),
            "codex": str(self.codex),
            "node": str(self.root / "other-node.exe"),
        }.get(name)

    def runner(self, command, **options):
        self.calls.append(list(command))
        if options.get("shell") is not False:
            raise AssertionError("probe must never use a shell")
        if "NODE_OPTIONS" in options.get("env", {}):
            raise AssertionError("probe environment must exclude Node preload hooks")
        if not Path(options.get("cwd", "")).is_dir():
            raise AssertionError("probe must use an isolated temporary working directory")
        provider = "claude-code" if any("claude" in str(part).casefold() for part in command) else "codex-cli"
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="2.1.245 (Claude Code)" if provider == "claude-code" else "codex-cli 0.149.1", stderr="")
        flags = [flag for flag in cli_provider.CLI_REQUIRED_FLAGS[provider] if flag not in self.missing_flags]
        return SimpleNamespace(returncode=0, stdout="usage " + " ".join(flags), stderr="")

    def detection_options(self) -> dict[str, object]:
        return {"runner": self.runner, "which": self.which, "system_name": "nt"}


class CliProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "project"
        self.home = base / "home"
        self.root.mkdir()
        self.probe = ProbeFixture(self.root)

    def configure_cli(self, provider_type: str, model: str, **options):
        executable = self.probe.claude if provider_type == "claude-code" else self.probe.codex
        return model_provider.configure_coding_assistant_provider(
            self.root,
            provider_type=provider_type,
            executable_path=str(executable),
            selected_model=model,
            environ=self.probe.environ,
            home=self.home,
            **self.probe.detection_options(),
            **options,
        )

    def configure_api(self) -> dict:
        return model_registry.configure_provider(
            self.root,
            base_url="https://models.example",
            api_key="fixture-api-key",
            selected_model="api-model",
            allow_private_network=False,
            provider_id="agent4market-api-fixture",
            name="Fixture API",
            vendor="custom",
            api="openai-completions",
            models=[{"id": "api-model"}],
            environ={},
            home=self.home,
            resolver=public_resolver,
        )

    def test_api_only_runtime_does_not_require_a_cli_process_host(self) -> None:
        self.configure_api()
        selected, environment = model_registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertEqual("agent4market-api-fixture/api-model", selected)
        self.assertNotIn("AGENT4MARKET_CLI_BACKENDS_FILE", environment)
        self.assertNotIn("AGENT4MARKET_CLI_BACKENDS_SHA256", environment)

    def test_cli_with_no_enabled_model_does_not_break_the_other_api_provider(self) -> None:
        self.configure_cli("codex-cli", "synthetic-codex")
        self.configure_api()
        settings = model_provider.load_model_settings(self.root)
        cli = next(item for item in settings["providers"] if item["api"] == "codex-cli")
        for model in cli["models"]:
            model["enabled"] = False
        model_provider._atomic_json(model_provider.settings_path(self.root), settings)
        selected, environment = model_registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertEqual("agent4market-api-fixture/api-model", selected)
        self.assertNotIn("AGENT4MARKET_CLI_BACKENDS_FILE", environment)

    def test_detection_resolves_windows_shim_to_bundled_node_and_requires_isolation_flags(self) -> None:
        detected = cli_provider.detect_coding_assistants(
            self.root, environ=self.probe.environ, **self.probe.detection_options()
        )
        claude = detected["claude-code"]
        self.assertTrue(claude["backend_available"])
        self.assertEqual(os.path.normcase(str(self.probe.claude.resolve())), claude["executable_path"])
        self.assertEqual(os.path.normcase(str(self.probe.node.resolve())), claude["command"])
        self.assertEqual([os.path.normcase(str(self.probe.claude_js.resolve()))], claude["args"])
        self.assertEqual("2.1.245 (Claude Code)", claude["version"])
        self.assertEqual(1, claude["runner_policy_version"])
        self.assertRegex(claude["launch_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual([], claude["models"], "detection must not guess a model catalog")
        self.assertTrue(all(call[-1] in {"--version", "--help"} for call in self.probe.calls))
        codex_help = next(call for call in self.probe.calls if call[-1] == "--help" and "codex" in Path(call[0]).name)
        self.assertEqual("exec", codex_help[-2])

        self.probe.missing_flags.add("--json-schema")
        rejected = cli_provider.detect_coding_assistants(
            self.root, environ=self.probe.environ, **self.probe.detection_options()
        )["claude-code"]
        self.assertFalse(rejected["available"])
        self.assertEqual("not_found_or_missing_isolation_flags", rejected["reason"])

        original_runner = self.probe.runner

        def future_codex_runner(command, **options):
            completed = original_runner(command, **options)
            if command[-1] == "--version" and "codex" in Path(command[0]).name.casefold():
                completed.stdout = "codex-cli 0.150.0"
            return completed

        unsupported = cli_provider.detect_coding_assistants(
            self.root,
            environ=self.probe.environ,
            runner=future_codex_runner,
            which=self.probe.which,
            system_name="nt",
        )["codex-cli"]
        self.assertFalse(unsupported["backend_available"])
        self.assertEqual("unsupported_policy_version", unsupported["reason"])
        self.assertEqual("unsupported_policy_version", unsupported["attempts"][0]["reason"])

        def future_claude_runner(command, **options):
            completed = original_runner(command, **options)
            if command[-1] == "--version" and any("claude" in str(part).casefold() for part in command):
                completed.stdout = "2.1.246 (Claude Code)"
            return completed

        unsupported_claude = cli_provider.detect_coding_assistants(
            self.root,
            environ=self.probe.environ,
            runner=future_claude_runner,
            which=self.probe.which,
            system_name="nt",
        )["claude-code"]
        self.assertFalse(unsupported_claude["backend_available"])
        self.assertEqual("unsupported_policy_version", unsupported_claude["reason"])

    def test_detection_prefers_fixed_native_claude_package_entry(self) -> None:
        self.probe.claude_native.parent.mkdir(parents=True, exist_ok=True)
        self.probe.claude_native.write_bytes(b"synthetic-native-claude")
        detected = cli_provider.detect_coding_assistants(
            self.root, environ=self.probe.environ, **self.probe.detection_options()
        )["claude-code"]
        self.assertTrue(detected["backend_available"])
        self.assertEqual(os.path.normcase(str(self.probe.claude_native.resolve())), detected["command"])
        self.assertEqual([], detected["args"])

    def test_api_and_both_cli_providers_coexist_without_cli_secrets(self) -> None:
        api = self.configure_api()
        api_default = api["default_model"]
        claude = self.configure_cli("claude-code", "claude-manual-model")
        codex = self.configure_cli("codex-cli", "codex-manual-model")
        registry = model_registry.load_registry(self.root)
        self.assertEqual(3, len(registry["providers"]))
        self.assertIn(api_default.split("/", 1)[0], {item["id"] for item in registry["providers"]})
        self.assertEqual(f"{codex['saved_provider_id']}/codex-manual-model", registry["default_model"])
        self.assertEqual({}, registry["role_models"])

        cli_records = [item for item in registry["providers"] if item["api"] in cli_provider.CLI_APIS]
        self.assertEqual({"claude-code", "codex-cli"}, {item["api"] for item in cli_records})
        for provider in cli_records:
            self.assertEqual("cli-managed-unverified", provider["authentication"])
            self.assertEqual(1, provider["runner_policy_version"])
            for model in provider["models"]:
                self.assertEqual(
                    (True, False, False, 32000, 4096),
                    (model["tools"], model["image_input"], model["reasoning"], model["context_window"], model["max_tokens"]),
                )
            self.assertFalse(model_provider.secret_path(self.root, provider["id"]).exists())

        summary = model_registry.summary(self.root)
        self.assertEqual("configured", summary["status"])
        self.assertEqual("cli-managed-unverified", summary["authentication"])
        self.assertFalse(summary["has_api_key"])

        selected, environment = model_registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertEqual(registry["default_model"], selected)
        self.assertIn(model_registry.key_env(api_default.split("/", 1)[0]), environment)
        for provider in cli_records:
            self.assertNotIn(model_registry.key_env(provider["id"]), environment)
        manifest = json.loads(Path(environment["AGENT4MARKET_CLI_BACKENDS_FILE"]).read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["version"])
        self.assertEqual(2, len(manifest["providers"]))
        self.assertEqual(
            model_registry.hashlib.sha256(Path(environment["AGENT4MARKET_CLI_BACKENDS_FILE"]).read_bytes()).hexdigest(),
            environment["AGENT4MARKET_CLI_BACKENDS_SHA256"],
        )
        for provider in manifest["providers"]:
            self.assertIn("version", provider)
            self.assertIn("launch_sha256", provider)
            self.assertEqual(1, provider["runner_policy_version"])
        catalog = json.loads((self.home / ".pi" / "agent" / "models.json").read_text(encoding="utf-8"))["providers"]
        for provider in cli_records:
            configured = catalog[provider["id"]]
            self.assertEqual("cli-managed-auth", configured["apiKey"])
            self.assertEqual(provider["api"], configured["models"][0]["api"])
            self.assertEqual(provider["base_url"], configured["models"][0]["baseUrl"])

    def test_only_detected_path_is_accepted_and_failed_save_is_atomic(self) -> None:
        self.configure_api()
        settings_before = model_provider.settings_path(self.root).read_bytes()
        malicious = self.root / "other.exe"
        malicious.write_bytes(b"not-detected")
        with self.assertRaisesRegex(model_provider.ModelProviderError, "当前检测"):
            model_provider.configure_coding_assistant_provider(
                self.root,
                provider_type="codex-cli",
                executable_path=str(malicious),
                selected_model="manual",
                environ=self.probe.environ,
                home=self.home,
                **self.probe.detection_options(),
            )
        self.assertEqual(settings_before, model_provider.settings_path(self.root).read_bytes())

        paths = [
            model_provider.settings_path(self.root),
            model_registry.bindings_path(self.root),
            self.home / ".pi" / "agent" / "models.json",
        ]
        snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
        original_sync = model_registry.sync_pi_catalog

        def sync_then_fail(*args, **kwargs):
            original_sync(*args, **kwargs)
            raise OSError("synthetic sync failure")

        with patch.object(model_registry, "sync_pi_catalog", side_effect=sync_then_fail):
            with self.assertRaisesRegex(OSError, "synthetic"):
                self.configure_cli("codex-cli", "manual")
        self.assertEqual(snapshots, {path: path.read_bytes() if path.exists() else None for path in paths})

    def test_launcher_identity_is_immutable_and_tampering_fails_runtime_closed(self) -> None:
        configured = self.configure_cli("claude-code", "manual-a")
        provider_id = configured["saved_provider_id"]
        self.probe.claude_js.write_bytes(b"changed-cli-entry")
        self.assertEqual(
            "unavailable",
            next(item for item in model_registry.summary(self.root)["providers"] if item["id"] == provider_id)["status"],
        )
        with self.assertRaisesRegex(model_provider.ModelProviderError, "新增供应商实例"):
            self.configure_cli("claude-code", "manual-b", provider_id=provider_id)
        with self.assertRaisesRegex(model_provider.ModelProviderError, "启动文件已缺失或变化"):
            model_registry.runtime_configuration(self.root, environ={}, home=self.home)

        replacement = self.configure_cli("claude-code", "manual-b")
        self.assertNotEqual(provider_id, replacement["saved_provider_id"])
        selected, _environment = model_registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertEqual(f"{replacement['saved_provider_id']}/manual-b", selected)

    def test_disable_and_remove_never_touch_cli_install_or_fall_back(self) -> None:
        self.configure_api()
        configured = self.configure_cli("codex-cli", "manual")
        provider_id = configured["saved_provider_id"]
        self.configure_cli("codex-cli", "manual", provider_id=provider_id, enabled=False, make_default=False)
        self.assertEqual("disabled", next(item for item in model_registry.summary(self.root)["providers"] if item["id"] == provider_id)["status"])
        with self.assertRaisesRegex(model_provider.ModelProviderError, "不会自动切换"):
            model_registry.runtime_configuration(self.root, environ={}, home=self.home)
        original_secret_path = model_provider.secret_path

        def reject_cli_secret_access(root, requested_provider_id=model_provider.PROVIDER_ID):
            if requested_provider_id == provider_id:
                raise AssertionError("CLI removal must not inspect or delete a project API secret")
            return original_secret_path(root, requested_provider_id)

        with patch.object(model_provider, "secret_path", side_effect=reject_cli_secret_access):
            model_registry.remove_provider(self.root, provider_id, environ={}, home=self.home)
        self.assertTrue(self.probe.codex.is_file())
        self.assertNotIn(provider_id, {item["id"] for item in model_registry.load_registry(self.root)["providers"]})

    def test_v2_remains_unsupported_and_bad_policy_version_is_rejected(self) -> None:
        model_provider._atomic_json(model_provider.settings_path(self.root), {
            "version": 2,
            "provider_type": "codex-cli",
            "executable_path": str(self.probe.codex),
            "selected_model": "manual",
        })
        summary = model_provider.model_settings_summary(self.root)
        self.assertEqual("unsupported_backend", summary["status"])
        self.assertFalse(summary["configured"])

        model_provider.settings_path(self.root).unlink()
        configured = self.configure_cli("codex-cli", "manual")
        registry = model_registry.load_registry(self.root)
        provider = next(item for item in registry["providers"] if item["id"] == configured["saved_provider_id"])
        provider["runner_policy_version"] = 2
        with self.assertRaisesRegex(model_provider.ModelProviderError, "身份|接收方"):
            model_registry.validate_registry(registry)


if __name__ == "__main__":
    unittest.main()
