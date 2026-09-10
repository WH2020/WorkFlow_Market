from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import unittest
from unittest.mock import patch

from agent_platform import cli_model_catalog as catalog, model_provider, model_registry
from tests.test_cli_provider import ProbeFixture
from agent_platform.environment import cli_python_executable


def row(identifier="synthetic"):
    return {"id": "opaque-row-not-the-model", "model": identifier, "displayName": "Synthetic model",
            "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": v, "description": "ignored"} for v in ("low", "medium", "high", "max", "ultra")],
            "isDefault": True, "hidden": False, "baseInstructions": "UNTRUSTED_INSTRUCTIONS"}


class CliCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.home = Path(self.temporary.name) / "home"
        self.probe = ProbeFixture(self.root)
        self.addCleanup(catalog._CACHE.clear)

    def discover(self, rows=None):
        with patch.object(catalog, "_query_model_list", return_value=rows if rows is not None else [row()]) as query:
            result = catalog.discover_codex_models(self.root, str(self.probe.codex), environ=self.probe.environ, **self.probe.detection_options())
            self.assertEqual(query.call_count, 1)
            return result

    def configure(self, **options):
        return model_provider.configure_coding_assistant_provider(self.root, provider_type="codex-cli", executable_path=str(self.probe.codex),
            selected_model="synthetic", environ=self.probe.environ, home=self.home, **self.probe.detection_options(), **options)

    def test_catalog_whitelists_metadata_and_retains_unrepresentable_effort_without_mapping(self):
        found = self.discover()
        model = found["models"][0]
        self.assertEqual(model["id"], "synthetic")
        self.assertEqual(model["supported_thinking_levels"], ["low", "medium", "high", "max"])
        self.assertIn("ultra", model["cli_reasoning"]["supported_efforts"])
        self.assertFalse(found["account_verified"])
        self.assertNotIn("UNTRUSTED", json.dumps(found))
        self.assertNotIn("ignored", json.dumps(found))

    def test_hidden_models_filtered_and_corrupt_metadata_rejected(self):
        self.assertIsNone(catalog.normalize_model({**row(), "hidden": True}))
        for change in ({"model": "bad id"}, {"displayName": "bad\nname"}, {"defaultReasoningEffort": "not-supported"},
                       {"supportedReasoningEfforts": [{"reasoningEffort": "high\";injection"}]},
                       {"supportedReasoningEfforts": [{"reasoningEffort": "medium"}] * 2}):
            with self.subTest(change=change), self.assertRaises(model_provider.ModelProviderError):
                catalog.normalize_model({**row(), **change})
        for rows in ([row(), row()], [{**row(), "hidden": True}]):
            with self.assertRaises(model_provider.ModelProviderError):
                self.discover(rows)
        nonreasoning = catalog.normalize_model({**row(), "supportedReasoningEfforts": [], "defaultReasoningEffort": "none"})
        self.assertEqual(nonreasoning["supported_thinking_levels"], ["off"])

    def test_save_validates_catalog_and_persists_exact_model_policy(self):
        found = self.discover()
        result = self.configure(discovery_id=found["discovery_id"], selected_thinking_level="high")
        model = result["models"][0]
        self.assertEqual(model["default_thinking_level"], "high")
        self.assertTrue(model["reasoning"])
        self.assertEqual(model["metadata_source"], "cli-model-list")
        self.assertEqual(model_registry.model_record(model), model)
        pi_path = model_provider.pi_agent_dir(environ=self.probe.environ, home=self.home) / "models.json"
        pi_model = json.loads(pi_path.read_text(encoding="utf-8"))["providers"][result["provider_id"]]["models"][0]
        self.assertEqual(pi_model["thinkingLevelMap"]["max"], "max")
        self.assertIsNone(pi_model["thinkingLevelMap"]["off"])
        before = model_provider.settings_path(self.root).read_bytes()
        for level in ("ultra", "off", "xhigh", None, "high\"injection"):
            with self.subTest(level=level), self.assertRaises(model_provider.ModelProviderError):
                self.configure(discovery_id=found["discovery_id"], selected_thinking_level=level)
            self.assertEqual(before, model_provider.settings_path(self.root).read_bytes())

    def test_old_manual_record_keeps_legacy_off_and_no_capabilities_are_invented(self):
        result = self.configure()
        self.assertFalse(result["models"][0]["reasoning"])
        self.assertNotIn("cli_reasoning", result["models"][0])
        with self.assertRaises(model_provider.ModelProviderError):
            self.configure(selected_thinking_level="high")

    def test_expired_unknown_cross_project_and_changed_binary_catalogs_rejected(self):
        found = self.discover()
        backend = {"api": "codex-cli", "launch_sha256": found["launch_sha256"], "version": found["version"]}
        cases = [(self.root, backend, "unknown"), (self.root / "other", backend, found["discovery_id"]),
                 (self.root, {**backend, "launch_sha256": "a" * 64}, found["discovery_id"]),
                 (self.root, {**backend, "version": "different"}, found["discovery_id"])]
        for root, entry, key in cases:
            with self.assertRaises(model_provider.ModelProviderError):
                catalog.selected_catalog_model(root, entry, key, "synthetic", "high")
        catalog._CACHE[found["discovery_id"]]["expires"] = time.monotonic() - 1
        with self.assertRaises(model_provider.ModelProviderError):
            self.configure(discovery_id=found["discovery_id"], selected_thinking_level="high")

    def test_registry_rejects_capability_spoofing_and_incomplete_records(self):
        found = self.discover()
        self.configure(discovery_id=found["discovery_id"], selected_thinking_level="high")
        registry = model_registry.load_registry(self.root)
        for field, value in (("default_thinking_level", "ultra"), ("reasoning", False), ("metadata_source", "user")):
            changed = copy.deepcopy(registry)
            changed["providers"][0]["models"][0][field] = value
            with self.assertRaises(model_provider.ModelProviderError):
                model_registry.validate_registry(changed)

    def test_real_host_and_synthetic_stdio_protocol_paginate_without_credentials(self):
        fixture = str(Path(__file__).with_name("fixtures") / "cli_catalog_fixture.py")
        environment = {**os.environ, "OPENAI_API_KEY": "SYNTHETIC_PRIVATE_CANARY", "NODE_OPTIONS": "--bad",
                       "AGENT4MARKET_KEY": "SYNTHETIC_PRIVATE_CANARY", "CLAUDE_CONFIG_DIR": "untrusted"}
        rows = catalog._query_model_list({"command": cli_python_executable(), "args": [fixture, "success"]}, environ=environment)
        self.assertEqual([item["model"] for item in rows], ["first", "second"])

    def test_real_host_rejects_error_truncation_overflow_and_repeated_cursor(self):
        fixture = str(Path(__file__).with_name("fixtures") / "cli_catalog_fixture.py")
        children = []
        original = subprocess.Popen
        def launch(*args, **options):
            child = original(*args, **options)
            children.append((child, Path(options["cwd"])))
            return child
        for mode in ("error", "bad-json", "overflow", "repeat", "timeout"):
            with self.subTest(mode=mode), patch.object(catalog, "QUERY_TIMEOUT", 0.5 if mode == "timeout" else 5), patch.object(catalog.subprocess, "Popen", side_effect=launch):
                with self.assertRaises(model_provider.ModelProviderError) as raised:
                    catalog._query_model_list({"command": cli_python_executable(), "args": [fixture, mode]})
                self.assertNotIn("CANARY", str(raised.exception))
                self.assertIsNotNone(children[-1][0].poll())
                self.assertFalse(children[-1][1].exists(), "the exact query cwd must be cleaned even after killing the host")

    def test_busy_directory_query_rejects_before_running_any_probe(self):
        catalog._QUERIES.acquire()
        catalog._QUERIES.acquire()
        try:
            with patch("agent_platform.cli_provider.resolve_configured_backend") as resolve_backend:
                with self.assertRaisesRegex(model_provider.ModelProviderError, "正在读取"):
                    catalog.discover_codex_models(self.root, str(self.probe.codex))
                resolve_backend.assert_not_called()
        finally:
            catalog._QUERIES.release()
            catalog._QUERIES.release()


if __name__ == "__main__":
    unittest.main()
