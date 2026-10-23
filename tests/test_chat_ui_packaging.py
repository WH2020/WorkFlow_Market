"""Source-list check only; does not build or sign a macOS application."""
import importlib.util
from pathlib import Path
import re
import unittest


class ChatUIPackagingTests(unittest.TestCase):
    def test_macos_update_contains_all_page_scripts_styles_and_server_ui_dependency(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("chat_ui_package_builder", root / "scripts/build-macos-update.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        page = (root / "ui/index.html").read_text(encoding="utf-8")
        referenced = re.findall(r'(?:src|href)="/([^"/]+\.(?:js|css))"', page)
        self.assertIn("chat-cards.js", referenced)
        for name in [*referenced, "business_library_api.py"]:
            with self.subTest(name=name):
                self.assertTrue(module.selected("ui/" + name))
        for name in ("ui/test_server.py", "ui/private.sqlite3", "data/library/dossiers.sqlite3", "ui/.secret"):
            self.assertFalse(module.selected(name))


if __name__ == "__main__":
    unittest.main()
