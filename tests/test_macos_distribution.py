import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MacOSDistributionTests(unittest.TestCase):
    def test_versions_and_bundle_configuration_are_aligned(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        tauri = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        cargo = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
        cargo_version = re.search(r'^version = "([^"]+)"$', cargo, re.MULTILINE)
        self.assertIsNotNone(cargo_version)
        self.assertEqual(package["version"], tauri["version"])
        self.assertEqual(package["version"], cargo_version.group(1))
        self.assertTrue(tauri["bundle"]["active"])
        self.assertEqual(tauri["bundle"]["macOS"]["minimumSystemVersion"], "13.0")
        self.assertEqual(tauri["bundle"]["macOS"]["signingIdentity"], "-")
        self.assertTrue((ROOT / "desktop/src-tauri/icons/icon.icns").is_file())

    def test_builder_produces_and_verifies_a_universal_runtime_package(self):
        script = (ROOT / "scripts/build-macos-desktop.sh").read_text(encoding="utf-8")
        for required in (
            "universal-apple-darwin",
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
            "--bundles app,dmg",
            "codesign --verify --deep --strict",
            "lipo -archs",
            "runtime.zip",
            "SHA256SUMS.txt",
            "--self-test",
        ):
            self.assertIn(required, script)

    def test_setup_installs_the_app_and_writes_a_private_runtime_marker(self):
        setup = (ROOT / "scripts/setup-macos.sh").read_text(encoding="utf-8")
        self.assertIn('SUPPORT_DIR="$HOME/Library/Application Support/Agent4Market"', setup)
        self.assertIn('chmod 700 "$SUPPORT_DIR"', setup)
        self.assertIn('chmod 600 "$MARKER_TEMP"', setup)
        self.assertIn('INSTALL_APP="$USER_APPLICATIONS/Agent4Market.app"', setup)
        self.assertIn('codesign --verify --deep --strict "$INSTALL_APP"', setup)
        self.assertIn('"$INSTALL_APP/Contents/MacOS/Agent4Market" --self-test', setup)
        runtime = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("configured_macos_project_root", runtime)
        self.assertIn("Library/Application Support/Agent4Market/install-root", runtime)
        self.assertIn("metadata.file_type().is_symlink()", runtime)

    def test_main_push_uploads_all_macos_artifacts(self):
        workflow = (ROOT / ".github/workflows/cross-platform.yml").read_text(encoding="utf-8")
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("Agent4Market-macOS-universal-${{ github.sha }}", workflow)
        for artifact in (
            "Agent4Market-universal-apple-darwin.dmg",
            "Agent4Market-universal-apple-darwin-app.zip",
            "Agent4Market-universal-apple-darwin-runtime.zip",
            "SHA256SUMS.txt",
        ):
            self.assertIn(artifact, workflow)


if __name__ == "__main__":
    unittest.main()
