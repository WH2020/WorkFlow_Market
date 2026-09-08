"""Isolated manual/browser QA: fake credentials, no model calls, no scheduler or core launch.

Run: python -B -m tests.workbench_ui_fixture
All mutable paths and the Pi catalog are redirected into an owned temporary directory.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from agent_platform import model_registry
from agent_platform.model_provider import ModelProviderError
from tests.test_model_provider import public_resolver
from ui import server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent4market-ui-qa-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        original = server.ROOT
        for name, value in list(vars(server).items()):
            if isinstance(value, Path) and value.is_relative_to(original) and name not in {"PROFILES", "PLUGINS", "UI_ROOT"}:
                setattr(server, name, root / value.relative_to(original))
        server.ACTIVE_PROFILE_ID = "sales-director"
        server.A4_API_HANDLER = server.create_api_handler()
        configure = server.configure_model_provider

        def configure_fixture(project_root, **options):
            return configure(project_root, **options, environ={}, home=Path(directory) / "home", resolver=public_resolver)

        def offline_discovery(*_args, **_kwargs):
            raise ModelProviderError("离线验证：服务暂不可达，已保存模型仍保留")

        server.configure_model_provider = configure_fixture
        server.discover_models = offline_discovery
        for name, protocol, make_default in [("QA 供应商 A", "openai-completions", True), ("QA 供应商 B", "openai-responses", False)]:
            configure_fixture(root, provider_id=None, name=name, vendor="custom", api=protocol,
                              base_url="https://example.com", api_key="non-secret-ui-fixture",
                              selected_model="same-model", models=[{"id": "same-model"}],
                              make_default=make_default, allow_private_network=False)

        class FixtureHandler(server.ControlHandler):
            def do_POST(self):
                if self.path not in {"/api/model-settings", "/api/model-discovery", "/api/task-requests", "/api/projects"}:
                    self.send_json(403, {"error": "隔离验证环境禁止该操作"})
                    return
                super().do_POST()

        http_server = server.ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler)
        print(f"QA_URL=http://127.0.0.1:{http_server.server_port}", flush=True)
        try:
            http_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            http_server.server_close()


if __name__ == "__main__":
    main()
