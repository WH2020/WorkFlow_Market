from __future__ import annotations

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from agent_platform import model_provider


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


def private_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", 8000))]


class FakeResponse:
    def __init__(self, payload: object, *, content_type: str = "application/json") -> None:
        self.status = 200
        self.body = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(self.body))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, maximum: int) -> bytes:
        return self.body[:maximum]


class ModelProviderTests(unittest.TestCase):
    def test_normalizes_gateway_root_and_rejects_unsafe_network_defaults(self) -> None:
        self.assertEqual(
            "https://ai.example.com/gateway",
            model_provider.normalize_base_url(
                "https://ai.example.com/gateway/v1/",
                allow_private_network=False,
                resolver=public_resolver,
            ),
        )
        with self.assertRaisesRegex(model_provider.ModelProviderError, "局域网"):
            model_provider.normalize_base_url(
                "http://127.0.0.1:8000", allow_private_network=False, resolver=private_resolver
            )
        self.assertEqual(
            "http://127.0.0.1:8000",
            model_provider.normalize_base_url(
                "http://127.0.0.1:8000", allow_private_network=True, resolver=private_resolver
            ),
        )
        with self.assertRaisesRegex(model_provider.ModelProviderError, "公网网关必须使用 HTTPS"):
            model_provider.normalize_base_url(
                "http://ai.example.com", allow_private_network=False, resolver=public_resolver
            )

    def test_discovers_deduplicated_bounded_model_records_without_returning_the_key(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["authorization"] = request.headers["Authorization"]
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse({
                "data": [
                    {"id": "gpt-4.1", "owned_by": "openai"},
                    {"id": "gpt-4.1", "owned_by": "duplicate"},
                    {"id": "claude-sonnet-4-5"},
                    {"invalid": True},
                ]
            })

        base_url, models = model_provider.discover_models(
            "https://ai.example.com",
            "super-secret",
            allow_private_network=False,
            resolver=public_resolver,
            opener=opener,
        )
        self.assertEqual("https://ai.example.com", base_url)
        self.assertEqual(["claude-sonnet-4-5", "gpt-4.1"], [item["id"] for item in models])
        self.assertEqual("Bearer super-secret", captured["authorization"])
        self.assertEqual("https://ai.example.com/v1/models", captured["url"])
        self.assertNotIn("super-secret", json.dumps(models))

    def test_configuration_keeps_secret_out_of_provider_and_model_catalog_files(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "project"
        home = Path(temporary.name) / "home"
        root.mkdir()

        def opener(_request, timeout=None):
            return FakeResponse({"data": [
                {"id": "gpt-4.1", "supported_endpoint_types": ["openai"]},
                {"id": "claude-sonnet-4-5", "supported_endpoint_types": ["anthropic"]},
            ]})

        def fake_save(project_root, _api_key, base_url, **_kwargs):
            model_provider._atomic_json(model_provider.secret_path(project_root), {
                "version": 1, "backend": "test", "binding": model_provider._secret_binding(base_url)
            })

        try:
            with patch("agent_platform.model_provider.save_model_secret", side_effect=fake_save):
                summary = model_provider.configure_model_provider(
                    root,
                    base_url="https://ai.example.com",
                    api_key="super-secret",
                    selected_model="gpt-4.1",
                    allow_private_network=False,
                    environ={},
                    home=home,
                    resolver=public_resolver,
                    opener=opener,
                )
            self.assertTrue(summary["configured"])
            self.assertEqual("gpt-4.1", summary["selected_model"])
            models_config = (home / ".pi" / "agent" / "models.json").read_text(encoding="utf-8")
            local_config = model_provider.settings_path(root).read_text(encoding="utf-8")
            for content in (models_config, local_config):
                self.assertNotIn("super-secret", content)
            self.assertIn("agent4market-newapi", models_config)
            self.assertIn("$AGENT4MARKET_NEWAPI_API_KEY", models_config)
            provider = json.loads(models_config)["providers"]["agent4market-newapi"]
            self.assertEqual(["claude-sonnet-4-5", "gpt-4.1"], [item["id"] for item in provider["models"]])
            self.assertEqual("anthropic-messages", provider["models"][0]["api"])
            self.assertEqual("https://ai.example.com", provider["models"][0]["baseUrl"])
            self.assertEqual("openai-completions", provider["models"][1]["api"])
            self.assertEqual("https://ai.example.com/v1", provider["models"][1]["baseUrl"])

            cleared = model_provider.clear_model_provider(root, environ={}, home=home)
            self.assertFalse(cleared["configured"])
            self.assertFalse(model_provider.settings_path(root).exists())
            self.assertFalse(model_provider.secret_path(root).exists())
            remaining = json.loads((home / ".pi" / "agent" / "models.json").read_text(encoding="utf-8"))
            self.assertNotIn("agent4market-newapi", remaining["providers"])
        finally:
            temporary.cleanup()

    def test_private_file_secret_is_project_and_gateway_bound(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        try:
            model_provider.save_model_secret(
                root, "secret-value", "https://ai.example.com", system_name="Linux"
            )
            self.assertEqual(
                "secret-value",
                model_provider.load_model_secret(
                    root, "https://ai.example.com", system_name="Linux"
                ),
            )
            self.assertIsNone(
                model_provider.load_model_secret(
                    root, "https://other.example.com", system_name="Linux"
                )
            )
            self.assertNotIn(
                "secret-value", model_provider.secret_path(root).read_text(encoding="utf-8")
            )
        finally:
            temporary.cleanup()

if __name__ == "__main__":
    unittest.main()
