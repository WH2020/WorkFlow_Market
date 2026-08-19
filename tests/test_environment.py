from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_platform.environment import discover_ppt_runtime, launch_pi


class EnvironmentTests(unittest.TestCase):
    def _runtime_fixture(
        self,
        system_name: str,
        cache_directory: Path = Path(".cache"),
    ) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        home = root / "home"
        dependencies = home / cache_directory / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
        if system_name == "Darwin":
            node = dependencies / "node" / "bin" / "node"
            python = dependencies / "python" / "bin" / "python3"
        else:
            node = dependencies / "node" / "bin" / "node.exe"
            python = dependencies / "python" / "python.exe"
        for executable in (node, python):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("fixture", encoding="utf-8")
            executable.chmod(0o755)
        node_modules = dependencies / "node" / "node_modules"
        artifact = node_modules / "@oai" / "artifact-tool"
        (artifact / "dist").mkdir(parents=True)
        (artifact / "dist" / "artifact_tool.mjs").write_text("fixture", encoding="utf-8")
        (dependencies / "bin" / "override").mkdir(parents=True)
        tools = (
            home / ".codex" / "plugins" / "cache" / "openai-primary-runtime" /
            "presentations" / "26.1.0" / "skills" / "presentations" / "container_tools"
        )
        tools.mkdir(parents=True)
        for name in ("mark_artifact_operation_started.mjs", "slides_test.py", "create_montage.py"):
            (tools / name).write_text("fixture", encoding="utf-8")
        return temporary, root, home

    def test_discovers_complete_macos_codex_runtime_and_native_font(self) -> None:
        temporary, root, home = self._runtime_fixture("Darwin")
        try:
            result = discover_ppt_runtime(root, environ={}, home=home, system_name="Darwin")
            self.assertTrue(result["ready"])
            self.assertEqual([], result["missing"])
            self.assertEqual("macos", result["platform"])
            self.assertEqual("PingFang SC", result["config"]["WORKFLOW_CJK_FONT"])
            self.assertTrue(result["config"]["WORKFLOW_ARTIFACT_NODE"].replace("\\", "/").endswith("/node/bin/node"))
        finally:
            temporary.cleanup()

    def test_discovers_complete_windows_codex_runtime_and_native_font(self) -> None:
        temporary, root, home = self._runtime_fixture("Windows")
        try:
            result = discover_ppt_runtime(root, environ={}, home=home, system_name="Windows")
            self.assertTrue(result["ready"])
            self.assertEqual("windows", result["platform"])
            self.assertEqual("Microsoft YaHei", result["config"]["WORKFLOW_CJK_FONT"])
            self.assertTrue(result["config"]["WORKFLOW_ARTIFACT_PYTHON"].endswith("python.exe"))
        finally:
            temporary.cleanup()

    def test_discovers_native_macos_cache_layout(self) -> None:
        temporary, root, home = self._runtime_fixture("Darwin", Path("Library/Caches"))
        try:
            result = discover_ppt_runtime(root, environ={}, home=home, system_name="Darwin")
            self.assertTrue(result["ready"])
            self.assertIn("Library", result["config"]["WORKFLOW_ARTIFACT_NODE"])
        finally:
            temporary.cleanup()

    def test_an_invalid_explicit_path_is_not_silently_replaced_by_auto_detection(self) -> None:
        temporary, root, home = self._runtime_fixture("Darwin")
        try:
            result = discover_ppt_runtime(
                root,
                environ={"WORKFLOW_ARTIFACT_TOOL_PATH": str(root / "untrusted-missing")},
                home=home,
                system_name="Darwin",
            )
            self.assertFalse(result["ready"])
            self.assertIn("WORKFLOW_ARTIFACT_TOOL_PATH", result["missing"])
        finally:
            temporary.cleanup()

    def test_runtime_discovery_never_copies_model_or_search_secrets(self) -> None:
        temporary, root, home = self._runtime_fixture("Darwin")
        try:
            result = discover_ppt_runtime(
                root,
                environ={
                    "BRAVE_SEARCH_API_KEY": "search-secret",
                    "OPENAI_API_KEY": "model-secret",
                    "WORKFLOW_CJK_FONT": "Director's CJK",
                },
                home=home,
                system_name="Darwin",
            )
            serialized = str(result["config"])
            self.assertNotIn("search-secret", serialized)
            self.assertNotIn("model-secret", serialized)
            self.assertEqual("Director's CJK", result["config"]["WORKFLOW_CJK_FONT"])
        finally:
            temporary.cleanup()

    def test_launch_injects_validated_ppt_paths_only_into_the_child_environment(self) -> None:
        source_environment = {"PATH": "/usr/bin", "OPENAI_API_KEY": "model-secret"}
        report = {
            "core": {"ready": True},
            "ppt": {
                "ready": True,
                "config": {"WORKFLOW_ARTIFACT_NODE": "/trusted/runtime/node", "WORKFLOW_CJK_FONT": "PingFang SC"},
            },
        }
        completed = Mock(returncode=0)
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch("agent_platform.environment.subprocess.run", return_value=completed) as run,
        ):
            return_code, ppt_ready = launch_pi(Path.cwd(), ["--version"], environ=source_environment)
        self.assertEqual(0, return_code)
        self.assertTrue(ppt_ready)
        self.assertNotIn("WORKFLOW_ARTIFACT_NODE", source_environment)
        child_environment = run.call_args.kwargs["env"]
        self.assertEqual("/trusted/runtime/node", child_environment["WORKFLOW_ARTIFACT_NODE"])
        self.assertEqual("model-secret", child_environment["OPENAI_API_KEY"])
        self.assertEqual([str(Path("/trusted/pi")), "--version"], run.call_args.args[0])

    def test_launch_does_not_inject_a_partial_ppt_configuration(self) -> None:
        report = {
            "core": {"ready": True},
            "ppt": {"ready": False, "config": {"WORKFLOW_ARTIFACT_NODE": "/partial/node"}},
        }
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            _return_code, ppt_ready = launch_pi(Path.cwd(), [], environ={"PATH": "/usr/bin"})
        self.assertFalse(ppt_ready)
        self.assertNotIn("WORKFLOW_ARTIFACT_NODE", run.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
