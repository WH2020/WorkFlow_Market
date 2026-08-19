from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_platform.core import ManifestError, Platform, WorkflowError, _satisfies, validate_workflow


ROOT = Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def test_repository_bundles_validate(self) -> None:
        report = Platform(ROOT).validate_all()
        self.assertEqual(12, report.plugins)
        self.assertEqual(14, report.workflows)
        self.assertEqual(2, report.profiles)
        self.assertEqual(14, report.services)

    def test_market_and_product_profiles_resolve_dependencies(self) -> None:
        platform = Platform(ROOT)
        market = platform.resolve_profile("market-director")
        product = platform.resolve_profile("product-director")
        self.assertIn("shared.knowledge", market["resolved_plugins"])
        self.assertIn("shared.presentation-studio", market["resolved_plugins"])
        self.assertIn("market.government", market["resolved_plugins"])
        self.assertIn("product.discovery", product["resolved_plugins"])
        self.assertIn("product.requirements", product["resolved_plugins"])
        self.assertIn("product.metrics", product["resolved_plugins"])
        self.assertIn("product.roadmap", product["resolved_plugins"])
        self.assertIn("product.release", product["resolved_plugins"])
        self.assertIn("shared.presentation-studio", product["resolved_plugins"])
        self.assertNotIn("market.sales", product["resolved_plugins"])

    def test_resolved_profiles_contain_no_chat_import_reference(self) -> None:
        platform = Platform(ROOT)
        platform.validate_all()
        for path in (ROOT / "profiles").glob("*/profile.json"):
            content = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("wechat", content)
            self.assertNotIn("weflow", content)
            self.assertNotIn("微信", content)

    def test_pi_package_exposes_every_profile_skill_without_chat_import(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("skills", package["pi"])
        catalog = json.loads(
            (ROOT / "pi" / "skill-catalog.json").read_text(encoding="utf-8")
        )
        skill_names: set[str] = set()
        self.assertNotIn("review-sales-conversations", catalog)
        self.assertNotIn("qq-mail-invoice-reimbursement", catalog)
        for expected_name, relative_path in catalog.items():
            skill_file = ROOT / relative_path / "SKILL.md"
            self.assertTrue(skill_file.is_file(), relative_path)
            match = re.search(
                r"^name:\s*([^\s]+)\s*$",
                skill_file.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            self.assertIsNotNone(match, relative_path)
            self.assertEqual(expected_name, match.group(1))
            skill_names.add(match.group(1))

        platform = Platform(ROOT)
        plugins = platform.load_plugins()
        required_skills: set[str] = set()
        for profile_id in ("market-director", "product-director"):
            resolved = platform.resolve_profile(profile_id)["resolved_plugins"]
            for plugin_id in resolved:
                required_skills.update(plugins[plugin_id].manifest["skills"])
        self.assertEqual(set(), required_skills - skill_names)

        market_plugins = platform.resolve_profile("market-director")["resolved_plugins"]
        product_plugins = platform.resolve_profile("product-director")["resolved_plugins"]
        market_skills = {
            skill for plugin_id in market_plugins for skill in plugins[plugin_id].manifest["skills"]
        }
        product_skills = {
            skill for plugin_id in product_plugins for skill in plugins[plugin_id].manifest["skills"]
        }
        self.assertNotIn("product-discovery", market_skills)
        self.assertNotIn("draft-government-cooperation", product_skills)

        for profile_id, available_skills in (
            ("market-director", market_skills),
            ("product-director", product_skills),
        ):
            for skill_name in available_skills:
                skill_text = (ROOT / catalog[skill_name] / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                references = set(re.findall(r"\$([a-z0-9-]+)", skill_text))
                self.assertEqual(
                    set(),
                    references - available_skills,
                    f"{profile_id}/{skill_name} has unavailable Skill references",
                )
        for plugin in platform.load_plugins().values():
            content = json.dumps(
                {"manifest": plugin.manifest, "workflows": plugin.workflows},
                ensure_ascii=False,
            ).lower()
            self.assertNotIn("wechat", content)
            self.assertNotIn("weflow", content)
            self.assertNotIn("微信", content)

    def test_missing_dependency_is_rejected(self) -> None:
        with self._temporary_platform() as root:
            shutil.rmtree(root / "vertical_plugins" / "shared" / "knowledge")
            with self.assertRaisesRegex(ManifestError, "missing plugin 'shared.knowledge'"):
                Platform(root).validate_all()

    def test_caret_semver_handles_zero_major_versions(self) -> None:
        self.assertTrue(_satisfies("0.1.9", "^0.1.0", "test"))
        self.assertFalse(_satisfies("0.2.0", "^0.1.0", "test"))
        self.assertTrue(_satisfies("0.0.3", "^0.0.3", "test"))
        self.assertFalse(_satisfies("0.0.4", "^0.0.3", "test"))

    def test_dependency_cycle_is_rejected(self) -> None:
        with self._temporary_platform() as root:
            path = root / "vertical_plugins" / "shared" / "knowledge" / "plugin.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["dependencies"] = [{"id": "shared.research", "version": ">=1.0.0"}]
            path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "dependency cycle"):
                Platform(root).validate_all()

    def test_duplicate_plugin_id_is_rejected(self) -> None:
        with self._temporary_platform() as root:
            source = root / "vertical_plugins" / "shared" / "knowledge"
            duplicate = root / "vertical_plugins" / "shared" / "knowledge-copy"
            shutil.copytree(source, duplicate)
            with self.assertRaisesRegex(ManifestError, "duplicate plugin id"):
                Platform(root).load_plugins()

    def test_dag_cycle_is_rejected(self) -> None:
        plugin, workflow = self._sample_workflow()
        workflow["nodes"][0]["depends_on"] = ["finish"]
        with self.assertRaisesRegex(WorkflowError, "DAG cycle"):
            validate_workflow(workflow, plugin)

    def test_node_permission_escalation_is_rejected(self) -> None:
        plugin, workflow = self._sample_workflow()
        workflow["nodes"][0]["permissions"] = ["system.admin"]
        with self.assertRaisesRegex(WorkflowError, "not declared by plugin"):
            validate_workflow(workflow, plugin)

    def test_structured_writes_require_tool_node(self) -> None:
        plugin, workflow = self._sample_workflow()
        plugin["permissions"].append("knowledge.write")
        workflow["nodes"][0]["permissions"] = ["knowledge.write"]
        with self.assertRaisesRegex(WorkflowError, "structured writes require a tool node"):
            validate_workflow(workflow, plugin)

    def test_structured_write_tool_requires_direct_approval(self) -> None:
        plugin, workflow = self._sample_workflow()
        plugin["permissions"].append("knowledge.write")
        workflow["nodes"][1] = {
            "id": "finish",
            "type": "tool",
            "tool": "knowledge.write",
            "depends_on": ["start"],
            "permissions": ["knowledge.write"],
        }
        with self.assertRaisesRegex(WorkflowError, "requires exactly one direct approval"):
            validate_workflow(workflow, plugin)

    def test_unbounded_subagent_is_rejected(self) -> None:
        plugin, workflow = self._sample_workflow()
        workflow["nodes"][0] = {
            "id": "start",
            "type": "subagent",
            "depends_on": [],
            "permissions": ["knowledge.read"],
        }
        with self.assertRaisesRegex(WorkflowError, "subagent requires boundary"):
            validate_workflow(workflow, plugin)

    def test_unknown_logical_tool_is_rejected(self) -> None:
        plugin, workflow = self._sample_workflow()
        workflow["nodes"][0] = {
            "id": "start",
            "type": "tool",
            "tool": "bash",
            "depends_on": [],
            "permissions": [],
        }
        with self.assertRaisesRegex(WorkflowError, "unknown logical tool"):
            validate_workflow(workflow, plugin)

    def test_subagent_tool_and_write_scope_cannot_bypass_permissions(self) -> None:
        plugin, workflow = self._sample_workflow()
        plugin["permissions"].append("web.read")
        workflow["nodes"][0] = {
            "id": "start",
            "type": "subagent",
            "depends_on": [],
            "permissions": ["knowledge.read"],
            "boundary": {
                "objective": "attempt escalation",
                "allowed_tools": ["bash", "write"],
                "max_turns": 2,
                "write_scope": ["C:/"],
            },
        }
        with self.assertRaisesRegex(WorkflowError, "unsupported subagent tools"):
            validate_workflow(workflow, plugin)

        workflow["nodes"][0]["permissions"] = ["web.read"]
        workflow["nodes"][0]["boundary"]["allowed_tools"] = ["web.search"]
        with self.assertRaisesRegex(WorkflowError, "unsafe subagent write scope"):
            validate_workflow(workflow, plugin)

        workflow["nodes"][0]["boundary"]["write_scope"] = ["outputs/research"]
        with self.assertRaisesRegex(WorkflowError, "read-only"):
            validate_workflow(workflow, plugin)

    def test_agent_skill_must_be_declared_by_plugin(self) -> None:
        plugin, workflow = self._sample_workflow()
        workflow["nodes"][0]["skill"] = "undeclared-skill"
        with self.assertRaisesRegex(WorkflowError, "is not declared by plugin"):
            validate_workflow(workflow, plugin)

    def test_service_mapping_must_be_provided_by_resolved_plugins(self) -> None:
        with self._temporary_platform() as root:
            path = root / "profiles" / "product-director" / "profile.json"
            profile = json.loads(path.read_text(encoding="utf-8"))
            profile["services"][0]["workflow"] = "market.sales.pipeline-review"
            path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "unavailable workflow"):
                Platform(root).validate_all()

    def test_execution_plan_is_deterministic_and_preserves_gates_and_boundaries(self) -> None:
        platform = Platform(ROOT)
        first = platform.plan_workflow("shared.research.frontier-subagent", "product-director")
        second = platform.plan_workflow("shared.research.frontier-subagent", "product-director")
        self.assertEqual(first, second)
        nodes = [node for stage in first["stages"] for node in stage["nodes"]]
        subagent = next(node for node in nodes if node["type"] == "subagent")
        source = json.loads(
            (ROOT / "vertical_plugins" / "shared" / "research" / "workflows" / "frontier-research-subagent.json").read_text(encoding="utf-8")
        )
        source_subagent = next(node for node in source["nodes"] if node["type"] == "subagent")
        self.assertEqual(source_subagent["boundary"], subagent["boundary"])
        self.assertEqual("bounded_subagent", subagent["execution_mode"])

        weekly = platform.plan_workflow("shared.reporting.weekly-deck", "market-director")
        weekly_nodes = [node for stage in weekly["stages"] for node in stage["nodes"]]
        approval = next(node for node in weekly_nodes if node["type"] == "approval")
        self.assertTrue(approval["gate"])
        self.assertEqual("human_gate", approval["execution_mode"])
        weekly_v2 = platform.plan_workflow("shared.reporting.weekly-deck-v2", "market-director")
        weekly_v2_nodes = [node for stage in weekly_v2["stages"] for node in stage["nodes"]]
        self.assertEqual(
            {"collect_week", "build_payload", "validate_payload", "approve", "render_deck"},
            {node["id"] for node in weekly_nodes},
        )
        self.assertIn("build_plan", {node["id"] for node in weekly_v2_nodes})
        self.assertIn("save_plan", {node["id"] for node in weekly_v2_nodes})
        market_services = {service["id"]: service for service in platform.resolve_profile("market-director")["profile"]["services"]}
        self.assertEqual("shared.reporting.weekly-deck-v2", market_services["weekly-deck"]["workflow"])

        studio = platform.plan_workflow("shared.presentation.studio", "market-director")
        studio_nodes = [node for stage in studio["stages"] for node in stage["nodes"]]
        self.assertEqual(2, sum(node["type"] == "approval" for node in studio_nodes))
        self.assertEqual(
            ["presentation.plan.write", "presentation.plan.write"],
            [node["tool"] for node in studio_nodes if node.get("tool") == "presentation.plan.write"],
        )

    def _sample_workflow(self) -> tuple[dict[str, object], dict[str, object]]:
        plugin: dict[str, object] = {
            "id": "test.plugin",
            "permissions": ["knowledge.read"],
            "skills": ["test-skill"],
        }
        workflow: dict[str, object] = {
            "id": "test.workflow",
            "plugin": "test.plugin",
            "display_name": "test",
            "entry_nodes": ["start"],
            "output_nodes": ["finish"],
            "nodes": [
                {"id": "start", "type": "agent", "skill": "test-skill", "depends_on": [], "permissions": ["knowledge.read"]},
                {"id": "finish", "type": "validator", "check": "test", "depends_on": ["start"], "permissions": []},
            ],
        }
        return copy.deepcopy(plugin), copy.deepcopy(workflow)

    def _temporary_platform(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        shutil.copytree(ROOT / "vertical_plugins", root / "vertical_plugins")
        shutil.copytree(ROOT / "profiles", root / "profiles")

        class Context:
            def __enter__(self):
                return root

            def __exit__(self, *args):
                temporary.cleanup()

        return Context()


if __name__ == "__main__":
    unittest.main()
