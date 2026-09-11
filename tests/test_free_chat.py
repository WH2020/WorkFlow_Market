from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from agent_platform import free_chat as chat
from agent_platform.cli_provider import launch_sha256
from tests.free_chat_test_support import model_server, private_runtime, selection
from ui import server

ROOT = Path(__file__).resolve().parents[1]


def payload(**changes):
    return {"request_id": uuid4().hex, "message": "合成问题", "version": 0,
            "requested_model": "agent4market-chat-fixture/synthetic-model", "requested_thinking_level": "off", **changes}


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.selected = selection()

        def transport(_root, request, cancel):
            self.seen.append(copy.deepcopy(request))
            if cancel.is_set():
                yield {"type": "error", "code": "CANCELLED"}
            else:
                yield {"type": "delta", "text": "合成回答"}
                yield {"type": "done", "text": "合成回答", "limited": False}
        self.manager = chat.ChatManager(resolver=lambda *_: copy.deepcopy(self.selected), transport=transport)

    def complete(self, **values):
        turn = self.manager.begin(ROOT, payload(**values))
        events = list(self.manager.events(ROOT, turn))
        self.assertEqual(events[-1]["type"], "done", events)
        self.assertEqual(turn.transport, {})
        return turn

    def test_history_contains_only_completed_turns_from_its_own_conversation(self):
        first = self.complete(message="合成名字：小松")
        self.complete(message="合成另一段对话")
        self.complete(session_id=first.session.session_id, version=1, message="我刚才说了什么？")
        self.assertEqual(self.seen[-1]["messages"], [{"role": "user", "content": "合成名字：小松"},
            {"role": "assistant", "content": "合成回答"}, {"role": "user", "content": "我刚才说了什么？"}])
        self.assertEqual(set(self.seen[-1]), {"model", "api_key", "thinking", "messages"})

    def test_browser_cannot_supply_system_messages_tools_files_or_business_context(self):
        for field in ("messages", "system_prompt", "tools", "account_id", "project_id", "wechat_scope", "file", "api_key", "base_url"):
            with self.subTest(field=field), self.assertRaises(chat.ChatError):
                self.manager.begin(ROOT, payload(**{field: "synthetic-untrusted"}))
        for value in ("", " " * 5, ["not text"], "中" * 8001, "\ud800"):
            with self.subTest(value=str(value)[:15]), self.assertRaises(chat.ChatError):
                self.manager.begin(ROOT, payload(message=value))
        self.assertFalse(self.seen)

    def test_recipient_or_effort_changes_never_receive_previous_messages(self):
        first = self.complete()
        for field, value in (("identity", {"base_url": "https://different.invalid"}), ("thinking", "high"), ("key", "different/model")):
            with self.subTest(field=field):
                self.selected = {**selection(), field: value}
                with self.assertRaises(chat.ChatError) as caught:
                    self.manager.begin(ROOT, payload(session_id=first.session.session_id, version=1))
                self.assertEqual(caught.exception.code, "RECIPIENT_CHANGED")
        self.assertEqual(len(self.seen), 1)

    def test_cancel_discards_pending_turn_and_prevents_duplicate_replay(self):
        turn = self.manager.begin(ROOT, payload())
        self.manager.cancel({"session_id": turn.session.session_id, "request_id": turn.request_id})
        result = list(self.manager.events(ROOT, turn))
        self.assertEqual(result[-1]["code"], "CANCELLED")
        self.assertEqual(turn.session.messages, [])
        self.assertEqual(turn.session.version, 0)
        with self.assertRaises(chat.ChatError):
            self.manager.begin(ROOT, payload(session_id=turn.session.session_id, request_id=turn.request_id))
        self.complete(session_id=turn.session.session_id, message="重试合成问题")

    def test_disconnect_after_metadata_releases_request_without_starting_transport(self):
        turn = self.manager.begin(ROOT, payload())
        events = self.manager.events(ROOT, turn)
        self.assertEqual(next(events)["type"], "meta")
        events.close()
        self.assertEqual(self.manager.active, {})
        self.assertIsNone(turn.session.current)
        self.assertEqual(turn.transport, {})
        self.assertFalse(self.seen)

    def test_close_does_not_release_capacity_before_cancelled_worker_finishes(self):
        turns = [self.manager.begin(ROOT, payload()) for _ in range(2)]
        for turn in turns:
            self.manager.close({"session_id": turn.session.session_id})
        with self.assertRaises(chat.ChatError) as caught:
            self.manager.begin(ROOT, payload())
        self.assertEqual(caught.exception.status, 429)
        for turn in turns:
            list(self.manager.events(ROOT, turn))
        self.complete()

    def test_version_context_and_session_limits_are_explicit(self):
        first = self.complete()
        with self.assertRaises(chat.ChatError):
            self.manager.begin(ROOT, payload(session_id=first.session.session_id, version=0))
        first.session.messages = [{"role": "user", "content": "x"}] * 40
        with self.assertRaises(chat.ChatError) as caught:
            self.manager.begin(ROOT, payload(session_id=first.session.session_id, version=1))
        self.assertEqual(caught.exception.code, "CONTEXT_LIMIT")
        self.manager.close({"session_id": first.session.session_id})
        with self.assertRaises(chat.ChatError) as caught:
            self.manager.begin(ROOT, payload(session_id=first.session.session_id, version=1))
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")

    def test_expired_idle_sessions_are_freed_but_active_requests_stay_counted(self):
        first = self.complete()
        self.manager.clock = lambda: first.session.touched + chat.SESSION_TTL + 1
        second = self.complete()
        self.assertNotIn(first.session.session_id, self.manager.sessions)
        self.assertIn(second.session.session_id, self.manager.sessions)

    def test_raw_transport_exceptions_do_not_reach_browser_or_history(self):
        def fail(*_args):
            raise RuntimeError("secret-error-canary")
        self.manager.transport = fail
        turn = self.manager.begin(ROOT, payload())
        result = list(self.manager.events(ROOT, turn))
        self.assertEqual(result[-1]["code"], "MODEL_ERROR")
        self.assertNotIn("secret-error-canary", json.dumps(result))
        self.assertEqual(turn.session.messages, [])

    def test_cancel_linearizes_before_pending_history_commit(self):
        acquired = threading.Event()
        real_lock = threading.RLock()
        main_thread = threading.get_ident()

        class ObservedLock:
            def __enter__(self):
                if threading.get_ident() != main_thread:
                    acquired.set()
                real_lock.acquire()

            def __exit__(self, *_args):
                real_lock.release()

        turn = self.manager.begin(ROOT, payload())
        self.manager.lock = ObservedLock()
        events = []
        with self.manager.lock:
            worker = threading.Thread(target=lambda: events.extend(self.manager.events(ROOT, turn)))
            worker.start()
            self.assertTrue(acquired.wait(2), "done must try to acquire the commit lock")
            self.assertEqual(self.manager.cancel({"session_id": turn.session.session_id, "request_id": turn.request_id})["status"], "stopping")
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(events[-1]["code"], "CANCELLED")
        self.assertEqual(turn.session.messages, [])

    def test_commit_linearizes_before_late_cancel_even_before_cleanup(self):
        turn = self.manager.begin(ROOT, payload())
        events = self.manager.events(ROOT, turn)
        for event in events:
            if event["type"] == "done":
                self.assertEqual(self.manager.cancel({"session_id": turn.session.session_id, "request_id": turn.request_id})["status"], "already_finished")
                self.assertEqual(turn.session.version, 1)


class ResolveModelTests(unittest.TestCase):
    def setUp(self):
        self.provider = {"id": "fixture", "name": "Fixture", "enabled": True, "api": "openai-completions", "base_url": "https://synthetic.invalid",
                         "models": [{"id": "text-only", "tools": False}]}
        self.enterContext(patch.object(chat.model_registry, "load_registry", return_value={"default_model": "fixture/text-only", "providers": [self.provider]}))
        self.enterContext(patch.object(chat.model_registry, "recipient_bindings", return_value={}))
        self.secret = self.enterContext(patch.object(chat, "load_model_secret", return_value="synthetic-only"))

    def test_only_selected_provider_credential_is_read_without_catalog_sync(self):
        with patch.object(chat.model_registry, "sync_pi_catalog", side_effect=AssertionError("Must not sync agent configuration")):
            result = chat.resolve_model(ROOT, "", "off")
        self.secret.assert_called_once_with(ROOT, self.provider["base_url"], provider_id="fixture")
        self.assertEqual(result["transport"]["model"]["baseUrl"], "https://synthetic.invalid/v1")
        self.assertEqual(result["key"], "fixture/text-only")
        self.assertNotIn("synthetic-only", json.dumps(result["identity"]))

    def test_credential_rotation_invalidates_old_conversation_identity(self):
        first = chat.resolve_model(ROOT, "", "off")
        self.secret.return_value = "different-synthetic-tenant"
        second = chat.resolve_model(ROOT, "", "off")
        self.assertNotEqual(first["identity"], second["identity"])

    def test_disabled_model_and_unsupported_reasoning_never_read_credentials(self):
        for changes, level in (({"enabled": False}, "off"), ({}, "high")):
            self.provider["models"][0] = {"id": "text-only", **changes}
            with self.assertRaises(chat.ChatError):
                chat.resolve_model(ROOT, "fixture/text-only", level)
        self.secret.assert_not_called()


class ChatHttpTests(unittest.TestCase):
    def handler(self, route, data, token=server.SERVER_TOKEN):
        handler = object.__new__(server.ControlHandler)
        body = json.dumps(data).encode()
        handler.path, handler.rfile = route, io.BytesIO(body)
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(body)), "X-Director-Token": token}
        handler.local_host, handler.local_user = lambda: True, lambda: True
        handler.send_json, handler.send_error = Mock(), Mock()
        handler.connection = Mock()
        return handler

    def test_every_chat_route_requires_local_user_token_and_json(self):
        for route in ("messages", "cancel", "close"):
            for condition in ("foreign_user", "bad_token", "non_json", "ok"):
                handler = self.handler("/api/chat/" + route, {})
                if condition == "foreign_user":
                    handler.local_user = lambda: False
                elif condition == "bad_token":
                    handler.headers["X-Director-Token"] = "bad"
                elif condition == "non_json":
                    handler.headers["Content-Type"] = "text/plain"
                handler.handle_free_chat = Mock()
                handler.do_POST()
                self.assertEqual(handler.handle_free_chat.call_count, int(condition == "ok"))

    def test_long_chinese_chat_input_has_its_own_bounded_http_limit(self):
        handler = self.handler("/api/chat/messages", payload(message="中" * 8000))
        handler.handle_free_chat = Mock()
        handler.do_POST()
        handler.handle_free_chat.assert_called_once()
        self.assertLess(int(handler.headers["Content-Length"]), 65536)
        handler = self.handler("/api/chat/messages", payload(message="中" * 12000))
        handler.handle_free_chat = Mock()
        handler.do_POST()
        handler.handle_free_chat.assert_not_called()

    def test_header_write_failure_releases_active_turn_and_secret(self):
        manager = chat.ChatManager(resolver=lambda *_: selection())
        handler = self.handler("/api/chat/messages", payload())
        handler.send_response = Mock(side_effect=BrokenPipeError())
        with patch.object(server, "FREE_CHAT", manager):
            handler.do_POST()
        self.assertEqual(manager.active, {})
        self.assertTrue(all(item.current is None for item in manager.sessions.values()))
        handler.connection.settimeout.assert_called_once_with(5)


@unittest.skipUnless(os.name == "nt", "Native Windows process-host integration")
class NativeChatTransportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(private_runtime())

    def cli_selection(self, api):
        chosen = selection()
        executable = str(ROOT / "pi/tests/fixtures/free-chat-cli-fixture.mjs")
        node = chat._node_command(ROOT, os.environ, shutil.which)
        self.assertIsNotNone(node, "Native CLI fixtures require a Node runtime")
        command = str(node)
        base = "https://api.anthropic.com" if api == "claude-code" else "https://chatgpt.com/backend-api/codex"
        entry = {"id": "agent4market-chat-fixture", "api": api, "base_url": base, "executable_path": executable, "command": command,
                 "args": [executable], "version": "synthetic", "runner_policy_version": 1, "models": [{"id": "synthetic-model"}],
                 "launch_sha256": launch_sha256(executable, command, [executable])}
        chosen["transport"].pop("api_key")
        chosen["transport"]["model"].update(api=api, baseUrl=base)
        chosen["transport"]["cli"] = entry
        return chosen

    def test_both_cli_adapters_through_nested_native_hosts_keep_only_dialogue(self):
        with patch.dict(os.environ, {"AGENT4MARKET_TEST_PRIVATE_KEY": "SYNTHETIC_BUSINESS_DATA_CANARY", "OPENAI_API_KEY": "SYNTHETIC_BUSINESS_DATA_CANARY"}):
            for api in ("claude-code", "codex-cli"):
                with self.subTest(api=api):
                    chosen = self.cli_selection(api)
                    manager = chat.ChatManager(resolver=lambda *_: copy.deepcopy(chosen))
                    first = manager.begin(ROOT, payload(message="合成名字：小松"))
                    events = list(manager.events(ROOT, first))
                    self.assertEqual(events[-1]["type"], "done", events)
                    second = manager.begin(ROOT, payload(session_id=first.session.session_id, version=1, message="你记得吗？"))
                    events = list(manager.events(ROOT, second))
                    self.assertEqual(events[-1]["type"], "done", events)
                    result = json.loads(second.session.messages[-1]["content"])
                    self.assertEqual(result["messages"], 3)
                    self.assertTrue(result["remembers"])
                    self.assertFalse(Path(result["cwd"]).exists(), "owned request directories are removed")
                    chosen["transport"]["cli"]["launch_sha256"] = "0" * 64
                    third = manager.begin(ROOT, payload(session_id=first.session.session_id, version=2))
                    events = list(manager.events(ROOT, third))
                    self.assertEqual(events[-1]["type"], "error", events)

    def test_nested_cli_cancel_releases_runtime_without_committing_history(self):
        for api in ("claude-code", "codex-cli"):
            with self.subTest(api=api):
                selected = self.cli_selection(api)
                manager = chat.ChatManager(resolver=lambda *_: copy.deepcopy(selected))
                turn = manager.begin(ROOT, payload(message="fixture:cancel"))
                started = time.monotonic()
                events = []
                for event in manager.events(ROOT, turn):
                    events.append(event)
                    if event["type"] == "ping" and time.monotonic() - started > 1.5:
                        manager.cancel({"session_id": turn.session.session_id, "request_id": turn.request_id})
                self.assertEqual(events[-1]["code"], "CANCELLED", events)
                self.assertLess(time.monotonic() - started, 7)
                self.assertEqual(turn.session.messages, [])
                self.assertEqual(manager.active, {})

    def test_all_three_api_protocols_through_real_node_and_process_host(self):
        for api in ("openai-completions", "openai-responses", "anthropic-messages"):
            with self.subTest(api=api), model_server() as (url, stats):
                manager = chat.ChatManager(resolver=lambda *_: selection(url, api))
                first = manager.begin(ROOT, payload(message="合成名字：小松"))
                events = list(manager.events(ROOT, first))
                self.assertEqual(events[-1]["type"], "done", events)
                second = manager.begin(ROOT, payload(session_id=first.session.session_id, version=1, message="我刚才说了什么？"))
                events = list(manager.events(ROOT, second))
                self.assertEqual(events[-1]["type"], "done", events)
                self.assertIn("小松", "".join(item.get("text", "") for item in events))
                self.assertEqual(len(stats["requests"]), 2)
                for request in stats["requests"]:
                    self.assertFalse(request["body"].get("tools"))
                    self.assertEqual(request["body"]["model"], "synthetic-model")
                    self.assertIn("synthetic-key-never-real", (request["authorization"] or "") + (request["api_key"] or ""))

    def test_cancel_closes_real_model_connection_and_discards_partial_history(self):
        with model_server() as (url, stats):
            manager = chat.ChatManager(resolver=lambda *_: selection(url))
            turn = manager.begin(ROOT, payload(message="fixture:slow"))
            events = []
            for event in manager.events(ROOT, turn):
                events.append(event)
                if event["type"] == "delta":
                    manager.cancel({"session_id": turn.session.session_id, "request_id": turn.request_id})
            self.assertEqual(events[-1]["code"], "CANCELLED", events)
            self.assertTrue(stats["disconnected"].wait(3))
            self.assertEqual(turn.session.messages, [])
            self.assertEqual(manager.active, {})

    def test_provider_error_and_redirect_never_expose_credentials_or_retry(self):
        for marker in ("fixture:auth", "fixture:redirect"):
            with self.subTest(marker=marker), model_server() as (url, stats):
                manager = chat.ChatManager(resolver=lambda *_: selection(url))
                turn = manager.begin(ROOT, payload(message=marker))
                events = list(manager.events(ROOT, turn))
                self.assertEqual(events[-1]["type"], "error", events)
                if marker == "fixture:auth":
                    self.assertEqual(events[-1]["code"], "AUTH_FAILED")
                self.assertNotIn("secret-error-canary", json.dumps(events))
                self.assertNotIn("synthetic-key-never-real", json.dumps(events))
                self.assertEqual(len(stats["requests"]), 1)
                self.assertEqual(turn.session.messages, [])

    def test_stalled_child_stdin_has_bounded_timeout_and_owned_process_cleanup(self):
        original = subprocess.Popen
        children = []

        def stalled(*args, **kwargs):
            launch = json.loads(kwargs["env"]["A4M_CLI_LAUNCH"])
            kwargs["env"]["A4M_CLI_LAUNCH"] = json.dumps([launch[0], "-e", "setInterval(()=>{}, 1000)"])
            process = original(*args, **kwargs)
            children.append(process)
            return process

        request = selection()["transport"]
        request["messages"] = [{"role": "user", "content": "合成" * 32000}]
        started = time.monotonic()
        with patch.object(chat, "REQUEST_TIMEOUT", 0.4), patch.object(chat.subprocess, "Popen", stalled):
            events = list(chat.transport_events(ROOT, request, threading.Event()))
        self.assertEqual(events[-1], {"type": "error", "code": "TIMEOUT"})
        self.assertLess(time.monotonic() - started, 7)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_native_python_entry_is_shared_by_outer_and_nested_hosts(self):
        native_python = r"C:\synthetic\python.exe"

        class Child:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(
                    b'{"type":"done","text":"synthetic response","limited":false}\n'
                )

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                raise AssertionError("completed synthetic child must not be killed")

        request = selection()["transport"]
        request["messages"] = [{"role": "user", "content": "synthetic"}]
        with (
            patch.object(chat, "cli_python_executable", return_value=native_python),
            patch.object(chat, "_node_command", return_value=ROOT / "runtime/node/node.exe"),
            patch.object(chat.subprocess, "Popen", return_value=Child()) as launch,
        ):
            events = list(chat.transport_events(ROOT, request, threading.Event()))

        self.assertEqual(
            [{"type": "done", "text": "synthetic response", "limited": False}],
            events,
        )
        arguments = launch.call_args.args[0]
        environment = launch.call_args.kwargs["env"]
        self.assertEqual(native_python, arguments[0])
        self.assertEqual(native_python, environment["AGENT4MARKET_CLI_PYTHON"])


if __name__ == "__main__":
    unittest.main()
