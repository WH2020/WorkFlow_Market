"""Real settings HTTP/UI with synthetic CLI discovery and an isolated registry.

No real CLI, model transport, login, existing application or user data is used.
Run with Python, then send a newline on stdin to stop the test server.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform import cli_provider, cli_model_catalog, model_provider, model_registry
from agent_platform.app_updates import UpdateChecker, release_record
from tests.test_cli_provider import ProbeFixture
from tests.test_model_provider import public_resolver
from ui import server


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="a4m-cli-browser-", dir=Path.home()) as directory:
        root = Path(directory)
        original = server.ROOT
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(original) and name not in {"UI_ROOT", "PROFILES", "PLUGINS"}:
                setattr(server, name, root / value.relative_to(original))
        probe = ProbeFixture(root)
        model_provider.pi_agent_dir = lambda **_options: root / "isolated-pi-agent"
        configure = model_provider.configure_coding_assistant_provider

        def configure_synthetic(project_root, **options):
            return configure(project_root, **options, environ=probe.environ, home=root / "isolated-home", **probe.detection_options())

        model_provider.configure_coding_assistant_provider = configure_synthetic
        model_provider.detect_coding_assistants = lambda **_: cli_provider.detect_coding_assistants(root, environ=probe.environ, **probe.detection_options())
        discover = cli_model_catalog.discover_codex_models
        cli_model_catalog._query_model_list = lambda *_a, **_k: [
            {"id": "opaque-synthetic-sol", "model": "synthetic-sol", "displayName": "合成 Sol", "isDefault": True, "hidden": False,
             "defaultReasoningEffort": "low", "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high", "xhigh", "max", "ultra")]},
            {"id": "opaque-synthetic-luna", "model": "synthetic-luna", "displayName": "合成 Luna", "isDefault": False, "hidden": False,
             "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high", "xhigh", "max")]},
        ]
        cli_model_catalog.discover_codex_models = lambda project_root, executable_path: discover(project_root, executable_path, environ=probe.environ, **probe.detection_options())
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.process_due_schedules = lambda: None
        server.APP_UPDATES = UpdateChecker("0.20.2", fetcher=lambda _etag: (release_record({
            "tag_name": "v0.20.2", "draft": False, "prerelease": False,
            "published_at": "2026-09-10T00:00:00Z", "name": "Synthetic release", "body": "Test only",
        }), None))
        server.search_settings_summary = lambda *_a, **_k: {"configured": False}
        server.search_gateway_settings_summary = lambda *_a, **_k: {"configured": False}
        server.mail_settings_summary = lambda *_a, **_k: {"configured": False}
        server.desktop_runtime_summary = lambda: {"ai_core": {"running": False, "status": "stopped"}, "mode": "synthetic-cli-ui-test"}
        model_registry.configure_provider(root, base_url="https://synthetic-models.example", api_key="synthetic-only-key", selected_model="fixture-api-model",
            allow_private_network=False, provider_id="agent4market-api-fixture", name="合成 API 实例", vendor="custom", api="openai-completions",
            models=[{"id": "fixture-api-model"}], environ={}, home=root / "isolated-home", resolver=public_resolver)

        class Handler(server.ControlHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.startswith("/api/a4/"):
                    self.send_json(200, {"success": True, "data": {"recommendations": []}})
                    return
                super().do_GET()

            def do_POST(self):
                allowed = {"/api/model-provider-choice", "/api/model-provider/configure", "/api/model-settings", "/api/coding-assistants/models", "/api/app-updates/check"}
                if self.path.split("?", 1)[0] not in allowed:
                    self.send_json(403, {"error": "This synthetic fixture only permits CLI configuration/default changes"})
                    return
                super().do_POST()

        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)

        def stop_on_input():
            sys.stdin.readline()
            http.shutdown()

        threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{http.server_port}", "root": str(root), "settings_path": str(model_provider.settings_path(root))}), flush=True)
        try:
            http.serve_forever()
        finally:
            http.server_close()


if __name__ == "__main__":
    main()
