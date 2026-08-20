import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from agent_platform.search_provider import (
    API_KEY_ENV,
    clear_search_provider,
    configure_search_provider,
    load_search_secret,
    mark_search_runtime_applied,
    restart_flag_path,
    save_search_secret,
    search_runtime_environment,
    search_settings_summary,
    secret_path,
    validate_brave_search_key,
)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: dict):
        self.body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        return self.body.read(size)


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_private_secret_record_never_contains_plaintext(self):
        save_search_secret(self.root, "brave-secret-value", system_name="linux")
        self.assertEqual(load_search_secret(self.root), "brave-secret-value")
        self.assertNotIn("brave-secret-value", secret_path(self.root).read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
    def test_windows_dpapi_secret_round_trip(self):
        save_search_secret(self.root, "windows-dpapi-secret", system_name="windows")
        self.assertEqual(load_search_secret(self.root), "windows-dpapi-secret")
        self.assertNotIn("windows-dpapi-secret", secret_path(self.root).read_text(encoding="utf-8"))

    def test_key_validation_uses_subscription_header_and_fixed_endpoint(self):
        captured = {}

        def open_request(request, timeout):
            captured["url"] = request.full_url
            captured["key"] = request.get_header("X-subscription-token")
            captured["timeout"] = timeout
            return FakeResponse({"web": {"results": []}})

        validate_brave_search_key("valid-key", opener=open_request)
        self.assertTrue(captured["url"].startswith("https://api.search.brave.com/res/v1/web/search?"))
        self.assertEqual(captured["key"], "valid-key")
        self.assertEqual(captured["timeout"], 15.0)

    def test_configure_marks_restart_and_runtime_injects_only_the_secret(self):
        with (
            patch("agent_platform.search_provider.validate_brave_search_key"),
            patch(
                "agent_platform.search_provider.save_search_secret",
                side_effect=lambda root, key: save_search_secret(root, key, system_name="linux"),
            ),
        ):
            summary = configure_search_provider(self.root, "stored-search-key")
        self.assertTrue(summary["configured"])
        self.assertTrue(summary["restart_required"])
        self.assertEqual(search_runtime_environment(self.root, environ={}), {API_KEY_ENV: "stored-search-key"})
        mark_search_runtime_applied(self.root)
        self.assertFalse(restart_flag_path(self.root).exists())
        self.assertFalse(search_settings_summary(self.root, environ={})["restart_required"])

    def test_explicit_environment_key_wins_without_being_returned_to_ui(self):
        summary = search_settings_summary(self.root, environ={API_KEY_ENV: "environment-secret"})
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["source"], "environment")
        self.assertFalse(summary["keyless"])
        self.assertEqual(summary["provider_id"], "brave-search")
        self.assertNotIn("environment-secret", json.dumps(summary))
        self.assertEqual(search_runtime_environment(self.root, environ={API_KEY_ENV: "environment-secret"}), {})

    def test_no_key_uses_the_keyless_public_pool_without_setup(self):
        summary = search_settings_summary(self.root, environ={})
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["status"], "configured")
        self.assertTrue(summary["keyless"])
        self.assertTrue(summary["shared_public_pool"])
        self.assertEqual(summary["provider_id"], "keenable-public")
        self.assertEqual(summary["source"], "public_pool")
        self.assertEqual(search_runtime_environment(self.root, environ={}), {})

    def test_clear_removes_secret_and_requires_runtime_restart(self):
        save_search_secret(self.root, "temporary-key", system_name="linux")
        summary = clear_search_provider(self.root)
        self.assertFalse(secret_path(self.root).exists())
        self.assertTrue(summary["configured"])
        self.assertTrue(summary["keyless"])
        self.assertEqual(summary["provider_id"], "keenable-public")
        self.assertTrue(summary["restart_required"])


if __name__ == "__main__":
    unittest.main()
