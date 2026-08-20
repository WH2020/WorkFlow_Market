from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from agent_platform.search_gateway import (
    BASE_URL_ENV,
    MAX_RESULTS_ENV,
    MODE_ENV,
    PROVIDERS_ENV,
    TOKEN_ENV,
    SearchGatewayError,
    configure_search_gateway,
    load_gateway_secret,
    mark_search_gateway_runtime_applied,
    normalize_gateway_url,
    restart_flag_path,
    save_gateway_secret,
    search_gateway_runtime_environment,
    search_gateway_settings_summary,
    secret_path,
    settings_path,
    validate_search_gateway,
)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload: object):
        self.body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        return self.body.read(size)


def public_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 443))]


def private_resolver(*_args, **_kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", 8080))]


class SearchGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_private_gateway_requires_explicit_permission(self):
        with self.assertRaisesRegex(SearchGatewayError, "允许本机/局域网"):
            normalize_gateway_url(
                "http://127.0.0.1:8080/v1", allow_private_network=False, resolver=private_resolver
            )
        self.assertEqual(
            "http://127.0.0.1:8080",
            normalize_gateway_url(
                "http://127.0.0.1:8080/v1", allow_private_network=True, resolver=private_resolver
            ),
        )

    def test_only_scoped_osr_token_is_accepted(self):
        with self.assertRaisesRegex(SearchGatewayError, "管理员凭据"):
            validate_search_gateway(
                "https://search.example.com", "oak_admin", allow_private_network=False,
                resolver=public_resolver, opener=lambda *_args, **_kwargs: FakeResponse([]),
            )
        with self.assertRaisesRegex(SearchGatewayError, "osr_"):
            validate_search_gateway(
                "https://search.example.com", "plain-token", allow_private_network=False,
                resolver=public_resolver, opener=lambda *_args, **_kwargs: FakeResponse([]),
            )

    def test_validation_uses_bearer_token_and_provider_endpoint(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse({"providers": [{"id": "brave"}, {"name": "tavily"}]})

        normalized, providers = validate_search_gateway(
            "https://search.example.com/v1", "osr_test_token", allow_private_network=False,
            resolver=public_resolver, opener=opener,
        )
        self.assertEqual(normalized, "https://search.example.com")
        self.assertEqual(captured["url"], "https://search.example.com/v1/providers")
        self.assertEqual(captured["authorization"], "Bearer osr_test_token")
        self.assertEqual(captured["timeout"], 15.0)
        self.assertEqual(providers, ["brave", "tavily"])

    def test_real_private_gateway_validation_uses_the_pinned_transport(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured["path"] = self.path
                captured["authorization"] = self.headers.get("Authorization")
                body = json.dumps({"providers": [{"id": "exa"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            normalized, providers = validate_search_gateway(
                f"http://127.0.0.1:{server.server_port}", "osr_local_test",
                allow_private_network=True,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(normalized, f"http://127.0.0.1:{server.server_port}")
        self.assertEqual(providers, ["exa"])
        self.assertEqual(captured["path"], "/v1/providers")
        self.assertEqual(captured["authorization"], "Bearer osr_local_test")

    def test_secret_is_not_saved_as_plaintext_and_runtime_env_is_complete(self):
        base_url = "https://search.example.com"
        save_gateway_secret(self.root, "osr_private_value", base_url, system_name="linux")
        self.assertEqual(load_gateway_secret(self.root, base_url), "osr_private_value")
        self.assertNotIn("osr_private_value", secret_path(self.root).read_text(encoding="utf-8"))
        settings_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        settings_path(self.root).write_text(json.dumps({
            "version": 1, "provider_id": "one-search", "base_url": base_url,
            "mode": "parallel", "max_results": 6, "allow_private_network": False,
            "providers": ["brave", "tavily"], "selected_providers": ["brave"],
            "updated_at": "2026-08-20T12:00:00+08:00",
        }), encoding="utf-8")
        environment = search_gateway_runtime_environment(self.root, environ={})
        self.assertEqual(environment[BASE_URL_ENV], base_url)
        self.assertEqual(environment[TOKEN_ENV], "osr_private_value")
        self.assertEqual(environment[MODE_ENV], "parallel")
        self.assertEqual(environment[MAX_RESULTS_ENV], "6")
        self.assertEqual(json.loads(environment[PROVIDERS_ENV]), ["brave"])
        summary = search_gateway_settings_summary(self.root)
        self.assertTrue(summary["configured"])
        self.assertNotIn("osr_private_value", json.dumps(summary))

    def test_selected_providers_must_be_available_and_single_mode_is_exact(self):
        with (
            patch("agent_platform.search_gateway.normalize_gateway_url", return_value="https://search.example.com"),
            patch(
                "agent_platform.search_gateway.validate_search_gateway",
                return_value=("https://search.example.com", ["brave", "tavily"]),
            ),
            patch("agent_platform.search_gateway.save_gateway_secret"),
            patch("agent_platform.search_gateway.load_gateway_secret", return_value="osr_test"),
        ):
            configured = configure_search_gateway(
                self.root, base_url="https://search.example.com", token="osr_test",
                mode="fallback", max_results=8, allow_private_network=False,
                selected_providers=["tavily", "brave"],
            )
            self.assertEqual(configured["selected_providers"], ["tavily", "brave"])
            with self.assertRaisesRegex(SearchGatewayError, "当前不可用"):
                configure_search_gateway(
                    self.root, base_url="https://search.example.com", token="osr_test",
                    mode="parallel", max_results=8, allow_private_network=False,
                    selected_providers=["unknown"],
                )
            with self.assertRaisesRegex(SearchGatewayError, "必须且只能选择一个"):
                configure_search_gateway(
                    self.root, base_url="https://search.example.com", token="osr_test",
                    mode="single", max_results=8, allow_private_network=False,
                    selected_providers=[],
                )

    def test_restart_flag_is_removed_only_after_runtime_applies_configuration(self):
        restart_flag_path(self.root).parent.mkdir(parents=True, exist_ok=True)
        restart_flag_path(self.root).write_text("{}", encoding="utf-8")
        mark_search_gateway_runtime_applied(self.root)
        self.assertFalse(restart_flag_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
