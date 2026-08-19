from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_platform.environment import discover_ppt_runtime, launch_pi


class EnvironmentTests(unittest.TestCase):
    def test_setup_scripts_do_not_use_codex_pnpm_or_dependency_lifecycle_scripts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        windows = (root / "scripts" / "setup-windows.ps1").read_text(encoding="utf-8")
        macos = (root / "scripts" / "setup-macos.sh").read_text(encoding="utf-8")
        launcher_build = (root / "scripts" / "build-windows-launcher.ps1").read_text(encoding="utf-8")
        for script in (windows, macos):
            self.assertIn("codex-runtimes", script)
            self.assertIn("--ignore-scripts", script)
        self.assertIn("build-windows-launcher.ps1", windows)
        self.assertIn("--self-test", launcher_build)
        self.assertIn("BurntSushi.ripgrep.MSVC", windows)
        self.assertIn("sharkdp.fd", windows)
        self.assertIn("$env:Path, $UserPath, $MachinePath", windows)
        self.assertIn("Microsoft\\WinGet\\Packages", windows)
        self.assertIn("launcher.log", launcher_build)

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


if __name__ == "__main__":
    unittest.main()
