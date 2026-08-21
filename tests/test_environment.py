from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_platform.environment import MIN_NODE, discover_ppt_runtime, launch_pi


class EnvironmentTests(unittest.TestCase):
    def test_product_runtime_is_pinned_to_validated_node_24_line(self) -> None:
        root = Path(__file__).resolve().parents[1]
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        workflow = (root / ".github" / "workflows" / "cross-platform.yml").read_text(encoding="utf-8")
        self.assertEqual(MIN_NODE, (24, 19, 0))
        self.assertEqual(package["engines"]["node"], ">=24.19.0 <25")
        self.assertNotIn('node-version: "22.19.0"', workflow)
        self.assertGreaterEqual(workflow.count('node-version: "24.19.0"'), 2)
        windows = (root / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8")
        macos = (root / "scripts" / "setup-macos.sh").read_text(encoding="utf-8")
        self.assertIn('[Version]"24.19.0"', windows)
        self.assertIn('[Version]"25.0.0"', windows)
        self.assertIn("node-v24.19.0-win-x64.zip", windows)
        self.assertIn("57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73", windows)
        self.assertIn('"runtime\\node"', windows)
        self.assertIn('"prepare", "pnpm@10", "--activate"', windows)
        launcher = (root / "scripts" / "start-windows.ps1").read_text(encoding="utf-8")
        self.assertIn('"runtime\\node"', launcher)
        self.assertIn("$env:Path = $PortableNodeDirectory", launcher)
        self.assertIn('major !== 24 || minor < 19', macos)

    def test_setup_scripts_do_not_use_codex_pnpm_or_dependency_lifecycle_scripts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        windows = (root / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8")
        macos = (root / "scripts" / "setup-macos.sh").read_text(encoding="utf-8")
        desktop_build = (root / "scripts" / "build-windows-desktop.ps1").read_text(encoding="utf-8")
        desktop_source = (root / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        for script in (windows, macos):
            self.assertIn("codex-runtimes", script)
            self.assertIn("--ignore-scripts", script)
        self.assertIn("build-windows-desktop.ps1", windows)
        self.assertIn("--self-test", desktop_build)
        self.assertIn("TemporarySelfTestPath", desktop_build)
        self.assertIn("Start-Process -FilePath $SelfTestPath", desktop_build)
        self.assertIn("$SelfTest.ExitCode", desktop_build)
        self.assertIn("BurntSushi.ripgrep.MSVC", windows)
        self.assertIn("sharkdp.fd", windows)
        self.assertIn("$env:Path, $UserPath, $MachinePath", windows)
        self.assertIn("Microsoft\\WinGet\\Packages", windows)
        self.assertIn("desktop-launcher.log", desktop_build)
        self.assertIn('env("PYTHONUTF8", "1")', desktop_source)
        self.assertIn('env("PYTHONUNBUFFERED", "1")', desktop_source)
        self.assertIn("Duration::from_secs(60)", desktop_source)
        self.assertIn("import sys; print(sys.executable)", desktop_source)
        self.assertIn('const PROFILE_ID: &str = "sales-director"', desktop_source)
        self.assertIn('start_workbench(&root, false)', desktop_source)
        self.assertIn('arguments.push("--disable-scheduler".into())', desktop_source)
        self.assertNotIn("OpenBrowser", desktop_source)

    def test_desktop_embeds_the_ai_core_by_default_with_optional_visible_diagnostics(self) -> None:
        root = Path(__file__).resolve().parents[1]
        desktop_source = (root / "desktop" / "src-tauri" / "src" / "main.rs").read_text(
            encoding="utf-8"
        )
        launcher = (root / "scripts" / "start-windows.ps1").read_text(encoding="utf-8")
        self.assertIn("fn show_ai_core_window", desktop_source)
        self.assertIn('desktop-settings.json', desktop_source)
        self.assertIn('GET /api/health HTTP/1.1', desktop_source)
        self.assertIn('if route == "/api/health":', (root / "ui/server.py").read_text(encoding="utf-8"))
        self.assertIn('split_once("\\\"show_ai_core_window\\\":")', desktop_source)
        self.assertIn('fn start_agent(root: &Path, show_window: bool)', desktop_source)
        self.assertIn('"--mode",', desktop_source)
        self.assertIn('"rpc",', desktop_source)
        self.assertIn(".stdin(Stdio::piped())", desktop_source)
        self.assertIn("ai_core_log(root)", desktop_source)
        self.assertIn("starting embedded AI core", desktop_source)
        self.assertNotIn('Command::new("cmd.exe")', desktop_source)
        self.assertIn('const CREATE_NEW_CONSOLE: u32', desktop_source)
        self.assertIn('.creation_flags(CREATE_NEW_CONSOLE)', desktop_source)
        self.assertIn('"-NoExit"', desktop_source)
        self.assertIn('"-KeepOpen"', desktop_source)
        self.assertIn(".creation_flags(CREATE_NO_WINDOW)", desktop_source)
        self.assertIn("[switch]$KeepOpen", launcher)
        self.assertIn("if ($KeepOpen)", launcher)
        self.assertIn("exit $AgentExitCode", launcher)

    def _project_fixture(self) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        for relative in (
            "pi/artifacts/build-director-deck.mjs",
            "pi/artifacts/validate-and-render-deck.mjs",
            "node_modules/pptxgenjs/package.json",
            "node_modules/@napi-rs/canvas/package.json",
            "node_modules/jszip/package.json",
            "node_modules/pdfjs-dist/package.json",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        libreoffice = root / "tools" / "soffice.exe"
        libreoffice.parent.mkdir(parents=True)
        libreoffice.write_text("fixture", encoding="utf-8")
        libreoffice.chmod(0o755)
        return temporary, root, libreoffice

    def test_discovers_complete_macos_project_runtime_and_native_font(self) -> None:
        temporary, root, libreoffice = self._project_fixture()
        try:
            result = discover_ppt_runtime(
                root,
                environ={"WORKFLOW_LIBREOFFICE_PATH": str(libreoffice)},
                home=root / "home",
                system_name="Darwin",
            )
            self.assertTrue(result["ready"])
            self.assertEqual([], result["missing"])
            self.assertEqual("macos", result["platform"])
            self.assertEqual("PingFang SC", result["config"]["WORKFLOW_CJK_FONT"])
            self.assertEqual("PptxGenJS + LibreOffice + PDF.js", result["engine"])
        finally:
            temporary.cleanup()

    def test_discovers_windows_standard_libreoffice_location(self) -> None:
        temporary, root, _libreoffice = self._project_fixture()
        try:
            program_files = root / "Program Files"
            soffice = program_files / "LibreOffice" / "program" / "soffice.com"
            soffice.parent.mkdir(parents=True)
            soffice.write_text("fixture", encoding="utf-8")
            result = discover_ppt_runtime(
                root,
                environ={"ProgramFiles": str(program_files), "PATH": ""},
                home=root / "home",
                system_name="Windows",
            )
            self.assertTrue(result["ready"])
            self.assertEqual("windows", result["platform"])
            self.assertEqual("Microsoft YaHei", result["config"]["WORKFLOW_CJK_FONT"])
            self.assertTrue(result["config"]["WORKFLOW_LIBREOFFICE_PATH"].endswith("soffice.com"))
        finally:
            temporary.cleanup()

    def test_an_invalid_explicit_libreoffice_path_is_not_silently_replaced(self) -> None:
        temporary, root, _libreoffice = self._project_fixture()
        try:
            program_files = root / "Program Files"
            installed = program_files / "LibreOffice" / "program" / "soffice.com"
            installed.parent.mkdir(parents=True)
            installed.write_text("fixture", encoding="utf-8")
            result = discover_ppt_runtime(
                root,
                environ={
                    "WORKFLOW_LIBREOFFICE_PATH": str(root / "untrusted-missing"),
                    "ProgramFiles": str(program_files),
                },
                system_name="Windows",
            )
            self.assertFalse(result["ready"])
            self.assertIn("WORKFLOW_LIBREOFFICE_PATH", result["missing"])
        finally:
            temporary.cleanup()

    def test_missing_project_package_blocks_ppt_readiness(self) -> None:
        temporary, root, libreoffice = self._project_fixture()
        try:
            (root / "node_modules" / "pptxgenjs" / "package.json").unlink()
            result = discover_ppt_runtime(
                root,
                environ={"WORKFLOW_LIBREOFFICE_PATH": str(libreoffice)},
                system_name="Windows",
            )
            self.assertFalse(result["ready"])
            self.assertIn("package:pptxgenjs", result["missing"])
        finally:
            temporary.cleanup()

    def test_runtime_discovery_never_copies_model_or_search_secrets(self) -> None:
        temporary, root, libreoffice = self._project_fixture()
        try:
            result = discover_ppt_runtime(
                root,
                environ={
                    "WORKFLOW_LIBREOFFICE_PATH": str(libreoffice),
                    "BRAVE_SEARCH_API_KEY": "search-secret",
                    "OPENAI_API_KEY": "model-secret",
                    "WORKFLOW_CJK_FONT": "Director's CJK",
                },
                system_name="Windows",
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
                "config": {"WORKFLOW_LIBREOFFICE_PATH": "/trusted/soffice", "WORKFLOW_CJK_FONT": "PingFang SC"},
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
        self.assertNotIn("WORKFLOW_LIBREOFFICE_PATH", source_environment)
        child_environment = run.call_args.kwargs["env"]
        self.assertEqual("/trusted/soffice", child_environment["WORKFLOW_LIBREOFFICE_PATH"])
        self.assertEqual("model-secret", child_environment["OPENAI_API_KEY"])

    def test_launch_does_not_inject_a_partial_ppt_configuration(self) -> None:
        report = {
            "core": {"ready": True},
            "ppt": {"ready": False, "config": {"WORKFLOW_CJK_FONT": "PingFang SC"}},
        }
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            _return_code, ppt_ready = launch_pi(Path.cwd(), [], environ={"PATH": "/usr/bin"})
        self.assertFalse(ppt_ready)
        self.assertNotIn("WORKFLOW_CJK_FONT", run.call_args.kwargs["env"])

    def test_launch_injects_selected_newapi_model_and_secret_only_into_child(self) -> None:
        report = {"core": {"ready": True}, "ppt": {"ready": False, "config": {}}}
        source_environment = {"PATH": "/usr/bin"}
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch(
                "agent_platform.environment.model_runtime_configuration",
                return_value=(
                    "agent4market-newapi/gpt-4.1",
                    {"AGENT4MARKET_NEWAPI_API_KEY": "model-secret"},
                ),
            ),
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            return_code, _ppt_ready = launch_pi(Path.cwd(), ["--approve"], environ=source_environment)
        self.assertEqual(0, return_code)
        command = run.call_args.args[0]
        self.assertEqual(
            [str(Path("/trusted/pi")), "--model", "agent4market-newapi/gpt-4.1", "--approve"], command
        )
        self.assertNotIn("AGENT4MARKET_NEWAPI_API_KEY", source_environment)
        self.assertEqual(
            "model-secret", run.call_args.kwargs["env"]["AGENT4MARKET_NEWAPI_API_KEY"]
        )

    def test_launch_injects_search_gateway_only_into_child_and_marks_it_applied(self) -> None:
        report = {"core": {"ready": True}, "ppt": {"ready": False, "config": {}}}
        source_environment = {"PATH": "/usr/bin"}
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch("agent_platform.environment.search_runtime_environment", return_value={}),
            patch("agent_platform.environment.search_gateway_runtime_environment", return_value={
                "ONE_SEARCH_BASE_URL": "https://search.example.com",
                "ONE_SEARCH_API_TOKEN": "osr_child_secret",
                "ONE_SEARCH_MODE": "parallel",
                "ONE_SEARCH_MAX_RESULTS": "6",
                "ONE_SEARCH_ALLOW_PRIVATE_NETWORK": "0",
            }),
            patch("agent_platform.environment.mark_search_gateway_runtime_applied") as mark_applied,
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            launch_pi(Path.cwd(), ["--approve"], environ=source_environment)
        self.assertNotIn("ONE_SEARCH_API_TOKEN", source_environment)
        self.assertEqual("osr_child_secret", run.call_args.kwargs["env"]["ONE_SEARCH_API_TOKEN"])
        mark_applied.assert_called_once_with(Path.cwd().resolve())

    def test_explicit_model_argument_overrides_saved_selection(self) -> None:
        report = {"core": {"ready": True}, "ppt": {"ready": False, "config": {}}}
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch(
                "agent_platform.environment.model_runtime_configuration",
                return_value=("agent4market-newapi/gpt-4.1", {"AGENT4MARKET_NEWAPI_API_KEY": "secret"}),
            ),
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            launch_pi(Path.cwd(), ["--model", "openai/gpt-5", "--version"], environ={"PATH": "/usr/bin"})
        self.assertEqual(
            [str(Path("/trusted/pi")), "--model", "openai/gpt-5", "--version"], run.call_args.args[0]
        )

    def test_version_check_does_not_require_the_selected_model_catalog(self) -> None:
        report = {"core": {"ready": True}, "ppt": {"ready": False, "config": {}}}
        with (
            patch("agent_platform.environment.doctor_report", return_value=report),
            patch("agent_platform.environment._command_path", return_value=Path("/trusted/pi")),
            patch(
                "agent_platform.environment.model_runtime_configuration",
                return_value=("agent4market-newapi/gpt-4.1", {"AGENT4MARKET_NEWAPI_API_KEY": "secret"}),
            ),
            patch("agent_platform.environment.subprocess.run", return_value=Mock(returncode=0)) as run,
        ):
            launch_pi(Path.cwd(), ["--version"], environ={"PATH": "/usr/bin"})
        self.assertEqual([str(Path("/trusted/pi")), "--version"], run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
