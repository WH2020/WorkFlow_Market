from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.subagents import (
    GOVERNED_EXTENSION_CONFIG,
    PROJECT_SUBAGENT_SETTINGS,
    ensure_subagent_configuration,
    extension_config_path,
    project_settings_path,
    subagent_doctor_check,
    subagent_environment,
)


class SubagentConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.home = Path(self.temporary.name) / "home"
        required = (
            self.root / "node_modules" / "pi-subagents" / "index.ts",
            self.root / "pi" / "extensions" / "subagent-readonly.ts",
            self.root / "pi" / "subagents" / "agents" / "director-research-scout.md",
            self.root / "pi" / "subagents" / "agents" / "director-readonly-reviewer.md",
        )
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        (self.root / "node_modules" / "pi-subagents" / "package.json").write_text(
            json.dumps({"name": "pi-subagents", "version": "0.51.0"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_configuration_merges_project_and_extension_settings_without_dropping_unknowns(self) -> None:
        project_settings_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        project_settings_path(self.root).write_text(
            json.dumps({"packages": ["npm:existing"], "theme": "custom"}),
            encoding="utf-8",
        )
        config_path = extension_config_path(environ={}, home=self.home)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"completionBatch": {"enabled": False}}), encoding="utf-8")

        paths = ensure_subagent_configuration(self.root, environ={}, home=self.home)
        settings = json.loads(project_settings_path(self.root).read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(settings["packages"], ["..", "npm:existing"])
        self.assertEqual(settings["theme"], "custom")
        self.assertEqual(settings["subagents"], PROJECT_SUBAGENT_SETTINGS)
        self.assertFalse(config["completionBatch"]["enabled"])
        for key, expected in GOVERNED_EXTENSION_CONFIG.items():
            self.assertEqual(config[key], expected)
        self.assertEqual(paths["extension_config"], str(config_path))
        self.assertTrue(subagent_doctor_check(self.root, environ={}, home=self.home)["ok"])

    def test_doctor_fails_closed_after_a_safety_setting_is_changed(self) -> None:
        ensure_subagent_configuration(self.root, environ={}, home=self.home)
        config_path = extension_config_path(environ={}, home=self.home)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["scheduledRuns"] = {"enabled": True}
        config_path.write_text(json.dumps(config), encoding="utf-8")
        result = subagent_doctor_check(self.root, environ={}, home=self.home)
        self.assertFalse(result["ok"])
        self.assertIn("scheduledRuns", result["reason"])

    def test_runtime_environment_forces_file_delivery_and_small_spawn_budgets(self) -> None:
        environment = subagent_environment()
        self.assertEqual(environment["PI_SUBAGENT_TASK_DELIVERY"], "file")
        self.assertEqual(environment["PI_SUBAGENT_MAX_SPAWNS_PER_RUN"], "2")
        self.assertEqual(environment["PI_SUBAGENT_MAX_DEPTH"], "1")
        self.assertEqual(environment["PI_SUBAGENT_WAIT_TOOL_ENABLED"], "false")


if __name__ == "__main__":
    unittest.main()

