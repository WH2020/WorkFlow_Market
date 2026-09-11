from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_platform import model_provider as provider
from agent_platform import model_registry as registry
from tests.test_model_provider import FakeResponse, public_resolver


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.home = Path(self.temp.name) / "home"
        self.root.mkdir()

    def configure(self, **options):
        defaults = dict(base_url="https://example.com", api_key="fixture-key-a", selected_model="same-id",
                        allow_private_network=False, provider_id=None, name="A", vendor="custom",
                        api="openai-completions", models=[{"id": "same-id"}],
                        environ={}, home=self.home, resolver=public_resolver)
        defaults.update(options)
        return registry.configure_provider(self.root, **defaults)

    def test_two_instances_with_identical_model_ids_keep_keys_protocols_and_defaults_isolated(self):
        first = self.configure()
        a = first["saved_provider_id"]
        second = self.configure(name="B", api="openai-responses", api_key="fixture-key-b", make_default=False,
                                models=[{"id": "same-id", "reasoning": True, "image_input": True, "context_window": 100000, "max_tokens": 8000}])
        b = second["saved_provider_id"]
        self.assertNotEqual(a, b)
        self.assertEqual(f"{a}/same-id", second["default_model"])
        model, environment = registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertEqual(f"{a}/same-id", model)
        self.assertEqual("fixture-key-a", environment[registry.key_env(a)])
        self.assertEqual("fixture-key-b", environment[registry.key_env(b)])
        catalog = json.loads((self.home / ".pi/agent/models.json").read_text())
        self.assertEqual("openai-responses", catalog["providers"][b]["models"][0]["api"])
        self.assertFalse(catalog["providers"][a]["models"][0]["reasoning"])
        self.assertEqual(["text", "image"], catalog["providers"][b]["models"][0]["input"])
        self.assertEqual(2, len(json.loads(Path(environment["AGENT4MARKET_MANAGED_MODELS_FILE"]).read_text(encoding="utf-8"))))
        for path in [provider.settings_path(self.root), self.home / ".pi/agent/models.json"]:
            self.assertNotIn("fixture-key", path.read_text(encoding="utf-8"))
        self.assertNotIn("fixture-key", json.dumps(second))

    def test_existing_instance_cannot_change_recipient_or_reuse_another_instances_secret(self):
        first = self.configure()
        with self.assertRaisesRegex(provider.ModelProviderError, "新增供应商实例"):
            self.configure(provider_id=first["saved_provider_id"], base_url="https://other.example.com", api_key=None)
        with self.assertRaisesRegex(provider.ModelProviderError, "首次配置"):
            self.configure(api_key=None)
        self.assertEqual(1, len(registry.load_registry(self.root)["providers"]))

    def test_swapped_or_corrupt_credentials_fail_closed_but_remain_removable(self):
        a = self.configure()["saved_provider_id"]
        b = self.configure(name="B", api_key="fixture-key-b", make_default=False)["saved_provider_id"]
        provider.secret_path(self.root, a).write_bytes(provider.secret_path(self.root, b).read_bytes())
        self.assertIsNone(provider.load_model_secret(self.root, "https://example.com", provider_id=a))
        self.assertNotIn(f"{a}/same-id", registry.available_models(self.root))
        provider.secret_path(self.root, a).write_text("{broken", encoding="utf-8")
        self.assertIn(f"{b}/same-id", registry.available_models(self.root))
        registry.remove_provider(self.root, a, environ={}, home=self.home)
        self.assertEqual("fixture-key-b", provider.load_model_secret(self.root, "https://example.com", provider_id=b))

    def test_mac_secret_lookup_and_removal_cannot_target_unrelated_keychain_entries(self):
        a = self.configure()["saved_provider_id"]
        secret = provider._read_object(provider.secret_path(self.root, a))
        secret.update(backend="macos-keychain", service="unrelated-service", account="unrelated-account")
        provider._atomic_json(provider.secret_path(self.root, a), secret)
        run = Mock(return_value=Mock(returncode=44, stdout=""))
        with (patch.object(provider.platform, "system", return_value="Darwin"),
              patch.object(provider.subprocess, "run", run)):
            self.assertIsNone(provider.load_model_secret(self.root, "https://example.com", provider_id=a))
            registry.remove_provider(self.root, a, environ={}, home=self.home)
        canonical = provider._keychain_service(self.root.resolve(), a)
        self.assertEqual(
            [["security", "find-generic-password", "-s", canonical, "-a", a, "-w"]],
            [call.args[0] for call in run.call_args_list],
        )

    def test_mac_corrupt_secret_rolls_back_canonical_keychain_after_later_failure(self):
        a = self.configure()["saved_provider_id"]
        secret_path = provider.secret_path(self.root, a)
        secret_path.write_text("{broken", encoding="utf-8")
        corrupt_bytes = secret_path.read_bytes()
        canonical = provider._keychain_service(self.root.resolve(), a)
        keychain = {canonical: "old-key"}
        commands = []

        def security(command, **_options):
            commands.append(command)
            service = command[command.index("-s") + 1]
            self.assertEqual(canonical, service)
            self.assertEqual(a, command[command.index("-a") + 1])
            if command[1] == "find-generic-password":
                value = keychain.get(service)
                return Mock(returncode=0 if value is not None else 44, stdout=(value or "") + ("\n" if value else ""))
            if command[1] == "add-generic-password":
                keychain[service] = command[command.index("-w") + 1]
                return Mock(returncode=0, stdout="")
            raise AssertionError(command)

        with (patch.object(provider.platform, "system", return_value="Darwin"),
              patch.object(provider.subprocess, "run", side_effect=security),
              patch.object(registry, "sync_pi_catalog", side_effect=OSError("synthetic later failure"))):
            with self.assertRaisesRegex(OSError, "synthetic later failure"):
                self.configure(provider_id=a, api_key="new-key")

        self.assertEqual("old-key", keychain[canonical])
        self.assertEqual(corrupt_bytes, secret_path.read_bytes())
        self.assertEqual(
            ["find-generic-password", "add-generic-password", "add-generic-password"],
            [command[1] for command in commands],
        )

    def test_disabled_or_deleted_default_never_falls_back_to_another_provider(self):
        first = self.configure()
        a = first["saved_provider_id"]
        b = self.configure(name="B", make_default=False)["saved_provider_id"]
        self.configure(provider_id=a, enabled=False, make_default=False, api_key=None)
        self.assertNotIn(f"{a}/same-id", registry.available_models(self.root))
        with self.assertRaisesRegex(provider.ModelProviderError, "不会自动切换"):
            registry.runtime_configuration(self.root, environ={}, home=self.home)
        result = registry.remove_provider(self.root, a, environ={}, home=self.home)
        self.assertIsNone(result["default_model"])
        self.assertEqual([b], [p["id"] for p in result["providers"]])
        self.assertFalse(provider.secret_path(self.root, a).exists())
        self.assertTrue(provider.secret_path(self.root, b).exists())

    def test_manual_models_do_not_require_discovery_and_failed_discovery_preserves_saved_catalog(self):
        with patch.object(provider, "discover_models", side_effect=AssertionError("manual save must be offline")):
            first = self.configure()
        before = provider.settings_path(self.root).read_bytes()
        with patch.object(provider, "discover_models", side_effect=provider.ModelProviderError("offline")):
            with self.assertRaisesRegex(provider.ModelProviderError, "offline"):
                self.configure(provider_id=first["saved_provider_id"], models=None)
        self.assertEqual(before, provider.settings_path(self.root).read_bytes())

    def test_discovery_cache_is_saved_separately_from_enabled_models_and_capabilities(self):
        first = self.configure(discovered_models=[{"id": "same-id"}, {"id": "catalog-only", "reasoning": True, "owned_by": "fixture"}])
        instance = registry.summary(self.root)["providers"][0]
        self.assertEqual(["same-id"], [item["id"] for item in instance["models"]])
        self.assertEqual(["same-id", "catalog-only"], [item["id"] for item in instance["discovered_models"]])
        self.assertNotIn("reasoning", instance["discovered_models"][1])
        self.assertNotIn(f"{first['saved_provider_id']}/catalog-only", registry.available_models(self.root))

    def test_v1_virtual_migration_preserves_legacy_secret_and_other_pi_providers(self):
        provider.save_model_secret(self.root, "legacy-fixture", "https://example.com")
        legacy_value = {"version": 1, "provider_id": provider.PROVIDER_ID, "base_url": "https://example.com",
                        "selected_model": "same-id", "models": [{"id": "same-id"}]}
        provider._atomic_json(provider.settings_path(self.root), legacy_value)
        path = self.home / ".pi/agent/models.json"
        provider._atomic_json(path, {"providers": {"unrelated": {"apiKey": "$KEEP_ME", "models": []}}})
        before = provider.settings_path(self.root).read_bytes()
        summary = registry.summary(self.root)
        self.assertEqual(before, provider.settings_path(self.root).read_bytes())
        self.assertEqual(provider.PROVIDER_ID, summary["providers"][0]["id"])
        self.configure(name="New", make_default=False)
        self.assertEqual(3, provider.load_model_settings(self.root)["version"])
        self.assertEqual("legacy-fixture", provider.load_model_secret(self.root, "https://example.com"))
        self.assertIn("unrelated", json.loads(path.read_text())["providers"])

    def test_cli_v2_is_inspectable_but_never_marked_ready_or_run_as_a_model(self):
        for kind in ["codex-cli", "claude-code"]:
            provider._atomic_json(provider.settings_path(self.root), {"version": 2, "provider_type": kind,
                                 "executable_path": "fixture.exe", "selected_model": "fixture"})
            summary = provider.model_settings_summary(self.root)
            self.assertFalse(summary["configured"])
            self.assertEqual("unsupported_backend", summary["status"])
            with self.assertRaisesRegex(provider.ModelProviderError, "CLI"):
                provider.model_runtime_configuration(self.root, environ={}, home=self.home)

    def test_cli_configuration_cannot_overwrite_a_working_registry(self):
        self.configure()
        before = provider.settings_path(self.root).read_bytes()
        with self.assertRaises(provider.ModelProviderError):
            provider.configure_coding_assistant_provider(self.root, provider_type="codex-cli", executable_path="fixture", selected_model="fixture")
        self.assertEqual(before, provider.settings_path(self.root).read_bytes())

    def test_removed_provider_or_model_identity_cannot_be_rebound(self):
        a = self.configure(models=[{"id": "same-id"}, {"id": "second"}])["saved_provider_id"]
        self.configure(provider_id=a, models=[{"id": "same-id"}], api_key=None)
        with self.assertRaisesRegex(provider.ModelProviderError, "已绑定"):
            self.configure(provider_id=a, api_key=None, models=[{"id": "same-id"}, {"id": "second", "api": "openai-responses"}])
        registry.remove_provider(self.root, a, environ={}, home=self.home)
        self.assertTrue(registry.bindings_path(self.root).exists())
        with self.assertRaisesRegex(provider.ModelProviderError, "已绑定"):
            self.configure(provider_id=a, base_url="https://other.example.com")
        self.assertFalse(provider.settings_path(self.root).exists())

    def test_save_and_delete_roll_back_all_files_when_catalog_sync_fails(self):
        a = self.configure()["saved_provider_id"]
        paths = [provider.settings_path(self.root), provider.secret_path(self.root, a), registry.bindings_path(self.root), self.home / ".pi/agent/models.json"]
        snapshots = {path: path.read_bytes() for path in paths}
        sync = registry.sync_pi_catalog

        def sync_then_fail(*args, **kwargs):
            sync(*args, **kwargs)
            raise OSError("injected post-write catalog failure")

        for operation in [lambda: self.configure(provider_id=a, api_key="new-fixture-key"),
                          lambda: registry.remove_provider(self.root, a, environ={}, home=self.home)]:
            with patch.object(registry, "sync_pi_catalog", side_effect=sync_then_fail):
                with self.assertRaisesRegex(OSError, "injected"):
                    operation()
            self.assertEqual(snapshots, {path: path.read_bytes() for path in paths})
            self.assertEqual("fixture-key-a", provider.load_model_secret(self.root, "https://example.com", provider_id=a))
        registry.remove_provider(self.root, a, environ={}, home=self.home)  # A failed delete remains retryable.
        self.assertFalse(provider.secret_path(self.root, a).exists())

    def test_maximum_provider_model_list_uses_a_hashed_file_not_an_environment_blob(self):
        self.configure(selected_model="model-0", models=[{"id": f"model-{index}"} for index in range(500)])
        _, environment = registry.runtime_configuration(self.root, environ={}, home=self.home)
        self.assertNotIn("AGENT4MARKET_MANAGED_MODELS", environment)
        self.assertLess(max(map(len, environment.values())), 8192)
        catalog = Path(environment["AGENT4MARKET_MANAGED_MODELS_FILE"])
        self.assertEqual(500, len(json.loads(catalog.read_text(encoding="utf-8"))))
        self.assertEqual(registry.hashlib.sha256(catalog.read_bytes()).hexdigest(), environment["AGENT4MARKET_MANAGED_MODELS_SHA256"])

    def test_anthropic_discovery_uses_its_own_auth_headers(self):
        captured = {}
        def opener(request, **_options):
            captured.update(dict(request.header_items()))
            return FakeResponse({"data": [{"id": "model"}]})
        provider.discover_models("https://example.com", "fixture", allow_private_network=False,
                                 api="anthropic-messages", resolver=public_resolver, opener=opener)
        headers = {k.lower(): v for k, v in captured.items()}
        self.assertEqual("fixture", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertNotIn("authorization", headers)

    def test_invalid_capabilities_duplicate_models_and_unsafe_ids_fail_before_writing(self):
        for changes in [dict(models=[{"id": "same-id", "max_tokens": 100, "context_window": 10}]),
                        dict(models=[{"id": "same-id"}, {"id": "same-id"}]), dict(provider_id="../../escape"),
                        dict(models="not-list"), dict(api="unsupported"), dict(role_models={"arbitrary-role": "unknown/model"})]:
            with self.subTest(changes=changes), self.assertRaises(provider.ModelProviderError):
                self.configure(**changes)
        self.assertFalse(provider.settings_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
