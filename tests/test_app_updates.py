from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from agent_platform import app_updates as updates


def release(tag="v0.20.3", **extra):
    return {"tag_name": tag, "name": "Synthetic release", "draft": False, "prerelease": False,
            "published_at": "2026-09-10T00:00:00Z", "body": "Synthetic notes", **extra}


class Response(io.BytesIO):
    def __init__(self, payload, headers=None, status=200, url=updates.LATEST_API_URL):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers, self.status, self.url = headers or {}, status, url

    def geturl(self):
        return self.url


class Clock:
    value = 1000.0

    def mono(self):
        return self.value

    def now(self):
        return 1788998400 + self.value


def complete(checker):
    checker._worker.join(timeout=2)
    assert not checker._worker.is_alive()
    return checker.snapshot()


class AppUpdateTests(unittest.TestCase):
    def test_semantic_version_order_and_invalid_inputs(self):
        key = updates.version_key
        self.assertGreater(key("0.20.10"), key("0.20.9"))
        self.assertGreater(key("1.0.0"), key("1.0.0-rc.2"))
        self.assertGreater(key("1.0.0-rc.10"), key("1.0.0-rc.2"))
        self.assertLess(key("1.0.0-alpha.1"), key("1.0.0-alpha.beta"))
        self.assertLess(key("1.0.0-alpha"), key("1.0.0-alpha.1"))
        self.assertEqual(key("v0.20.2+build.5", tag=True), key("0.20.2"))
        for value in (None, "0.20", "01.2.3", "1.2.3-01", "1.2.3\n", "v1.2.3", "1.2.3/../../", "1" * 150):
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                key(value)

    def test_current_version_reads_only_public_package_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(updates.application_version(root), "unknown")
            (root / "package.json").write_text('{"version":"0.20.2"}', encoding="utf-8")
            self.assertEqual(updates.application_version(root), "0.20.2")
            (root / "package.json").write_text('{"version":"invalid"}', encoding="utf-8")
            self.assertEqual(updates.application_version(root), "unknown")

    def test_release_metadata_is_bounded_and_external_urls_are_not_trusted(self):
        actual = updates.release_record(release(html_url="https://evil.example/file.exe", body="<img onerror=evil()>\x00" + "x" * 15000))
        self.assertEqual(actual["release_url"], f"{updates.RELEASES_URL}/tag/v0.20.3")
        self.assertNotIn("\x00", actual["notes"])
        self.assertTrue(actual["notes_truncated"])
        self.assertLessEqual(len(actual["notes"]), updates.MAX_NOTES)
        self.assertNotIn("html_url", actual)
        for extra in ({"draft": True}, {"prerelease": True}, {"draft": "false"}, {"published_at": None}, {"published_at": "2026-09-10"}, {"tag_name": "v1.0.0-rc.1"}):
            with self.subTest(extra=extra), self.assertRaises(updates.UpdateError):
                updates.release_record(release(**extra))

    def test_request_is_fixed_public_get_without_auth_or_local_data(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            self.assertEqual(timeout, updates.SOCKET_TIMEOUT)
            return Response(release(), {"ETag": 'W/"fixture"'})

        value, etag = updates.fetch_latest('"previous"', opener=opener)
        self.assertEqual(value["version"], "0.20.3")
        self.assertEqual(etag, 'W/"fixture"')
        self.assertEqual(seen[0].full_url, updates.LATEST_API_URL)
        self.assertEqual(seen[0].method, "GET")
        self.assertIsNone(seen[0].data)
        headers = dict((name.lower(), value) for name, value in seen[0].header_items())
        self.assertEqual(headers["if-none-match"], '"previous"')
        self.assertFalse({"authorization", "cookie", "x-api-key"} & headers.keys())
        self.assertIsNone(updates._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_network_protocol_rejects_oversize_corrupt_and_redirected_responses(self):
        cases = [Response(b"not-json"), Response(b"x" * (updates.MAX_RESPONSE_BYTES + 1)),
                 Response(release(), {"Content-Length": str(updates.MAX_RESPONSE_BYTES + 1)}),
                 Response(release(), {"Content-Encoding": "gzip"}),
                 Response(release(), url="https://evil.example"), Response(release(), {"Content-Length": "invalid"})]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(updates.UpdateError):
                updates.fetch_latest(opener=lambda *_a, **_k: response)
            self.assertTrue(response.closed)

    def test_http_errors_are_specific_redacted_and_close_the_response(self):
        cases = [(404, {}, "SOURCE_UNAVAILABLE"), (403, {}, "ACCESS_DENIED"),
                 (429, {"Retry-After": "120"}, "RATE_LIMITED"),
                 (403, {"X-RateLimit-Remaining": "0", "Retry-After": "90"}, "RATE_LIMITED"),
                 (302, {"Location": "https://evil.example"}, "REDIRECT_REJECTED"), (500, {}, "HTTP_ERROR")]
        for status, headers, expected in cases:
            body = io.BytesIO(b"SYNTHETIC_SECRET")
            with self.subTest(status=status), self.assertRaises(updates.UpdateError) as caught:
                updates.fetch_latest(opener=Mock(side_effect=HTTPError(updates.LATEST_API_URL, status, "SYNTHETIC_SECRET", headers, body)))
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
            self.assertTrue(body.closed)
            if status == 429:
                self.assertEqual(caught.exception.retry_after, 120)
        with self.assertRaises(updates.UpdateError) as caught:
            updates.fetch_latest(opener=Mock(side_effect=URLError("SYNTHETIC_SECRET")))
        self.assertEqual(caught.exception.code, "NETWORK_ERROR")
        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))

    def test_response_etag_is_whitelisted_and_304_is_supported(self):
        self.assertIsNone(updates.fetch_latest(opener=lambda *_a, **_k: Response(release(), {"ETag": "bad\r\nheader"}))[1])
        self.assertEqual(updates.fetch_latest('"old"', opener=lambda *_a, **_k: Response(b"", status=304)), (None, '"old"'))
        self.assertEqual(updates.fetch_latest('"old"', opener=Mock(side_effect=HTTPError(updates.LATEST_API_URL, 304, "", {}, io.BytesIO()))), (None, '"old"'))

    def test_no_network_before_check_and_unknown_current_never_claims_latest(self):
        fetch = Mock()
        checker = updates.UpdateChecker("unknown", fetcher=fetch)
        self.assertEqual(checker.snapshot()["status"], "not_checked")
        state = checker.check(manual=True)
        self.assertEqual(state["error"]["code"], "CURRENT_VERSION_UNKNOWN")
        self.assertFalse(state["update_available"])
        fetch.assert_not_called()

    def test_new_equal_and_older_releases_have_distinct_states(self):
        for version, expected in (("0.20.2", "available"), ("0.20.3", "up_to_date"), ("0.20.4", "current_ahead")):
            with self.subTest(version=version):
                checker = updates.UpdateChecker(version, fetcher=lambda _: (updates.release_record(release()), None))
                self.assertTrue(checker.check()["checking"])
                state = complete(checker)
                self.assertEqual(state["status"], expected)
                self.assertEqual(state["can_open_release"], expected == "available")
                self.assertFalse(state["automatic_install"])

    def test_single_flight_and_nonblocking_state_during_slow_check(self):
        entered, finish = threading.Event(), threading.Event()

        def fetch(_):
            entered.set()
            finish.wait(timeout=2)
            return updates.release_record(release()), None

        fetcher = Mock(side_effect=fetch)
        checker = updates.UpdateChecker("0.20.2", fetcher=fetcher)
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                states = list(pool.map(lambda _: checker.check(manual=True), range(20)))
            self.assertTrue(entered.wait(timeout=1))
            self.assertTrue(all(state["checking"] for state in states))
            self.assertEqual(checker.snapshot()["status"], "checking")
            fetcher.assert_called_once()
        finally:
            finish.set()
            complete(checker)

    def test_manual_cooldown_auto_interval_and_conditional_cache(self):
        clock = Clock()
        fetch = Mock(side_effect=[(updates.release_record(release()), '"etag"'), (None, '"etag"')])
        checker = updates.UpdateChecker("0.20.2", fetcher=fetch, monotonic=clock.mono, now=clock.now)
        checker.check(); first = complete(checker)
        clock.value += 59
        checker.check(manual=True)
        clock.value += 2
        checker.check()
        fetch.assert_called_once_with(None)
        checker.check(manual=True); second = complete(checker)
        self.assertEqual(fetch.call_args.args, ('"etag"',))
        self.assertEqual(second["latest"], first["latest"])
        self.assertEqual(second["status"], "available")
        self.assertNotEqual(second["last_success_at"], first["last_success_at"])

    def test_failure_retains_last_good_metadata_without_claiming_freshness(self):
        clock = Clock()
        fetch = Mock(side_effect=[(updates.release_record(release()), '"etag"'), updates.UpdateError("SOURCE_UNAVAILABLE", "Synthetic 404")])
        checker = updates.UpdateChecker("0.20.2", fetcher=fetch, monotonic=clock.mono, now=clock.now)
        checker.check(); first = complete(checker)
        clock.value += 301
        checker.check(); second = complete(checker)
        self.assertEqual(second["status"], "error")
        self.assertTrue(second["stale"])
        self.assertFalse(second["update_available"])
        self.assertEqual(first["latest"], second["latest"])
        self.assertGreaterEqual(second["auto_retry_after_seconds"], 600)
        self.assertEqual(second["retry_after_seconds"], 60)

    def test_error_backoff_grows_but_manual_retry_and_rate_limits_are_distinct(self):
        clock = Clock()
        fetch = Mock(side_effect=updates.UpdateError("SOURCE_UNAVAILABLE", "Synthetic 404"))
        checker = updates.UpdateChecker("0.20.2", fetcher=fetch, monotonic=clock.mono, now=clock.now)
        with patch.object(updates.random, "uniform", return_value=0):
            checker.check(); complete(checker)
            clock.value += 301
            checker.check(); self.assertEqual(fetch.call_count, 1)
            checker.check(manual=True); state = complete(checker)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(state["auto_retry_after_seconds"], 1200)
        rate_fetch = Mock(side_effect=updates.UpdateError("RATE_LIMITED", "Synthetic rate limit", 3600))
        limited = updates.UpdateChecker("0.20.2", fetcher=rate_fetch, monotonic=clock.mono, now=clock.now)
        limited.check(); complete(limited)
        clock.value += 1000
        self.assertGreater(limited.check(manual=True)["retry_after_seconds"], 0)
        rate_fetch.assert_called_once()

    def test_invalid_cache_and_internal_errors_do_not_escape(self):
        for fetch, code in ((Mock(return_value=(None, None)), "INVALID_CACHE"), (Mock(side_effect=RuntimeError("SYNTHETIC_SECRET")), "CHECK_FAILED")):
            checker = updates.UpdateChecker("0.20.2", fetcher=fetch)
            checker.check(); state = complete(checker)
            self.assertEqual(state["error"]["code"], code)
            self.assertNotIn("SYNTHETIC_SECRET", json.dumps(state))

    def test_failed_thread_start_does_not_leave_permanent_checking(self):
        checker = updates.UpdateChecker("0.20.2", fetcher=Mock())
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("Synthetic unavailable")):
            state = checker.check()
        self.assertFalse(state["checking"])
        self.assertEqual(state["error"]["code"], "CHECK_FAILED")

    def test_open_release_requires_current_selection_and_never_downloads_or_installs(self):
        fetch = Mock(return_value=(updates.release_record(release(html_url="file:///private")), None))
        checker = updates.UpdateChecker("0.20.2", fetcher=fetch)
        open_page = Mock()
        with self.assertRaises(updates.UpdateError):
            checker.open_release("v0.20.3", opener=open_page)
        checker.check(); complete(checker)
        for tag in ("v0.20.4", "https://evil.example", "../anything"):
            with self.subTest(tag=tag), self.assertRaises(updates.UpdateError):
                checker.open_release(tag, opener=open_page)
        open_page.assert_not_called()
        result = checker.open_release("v0.20.3", opener=open_page)
        open_page.assert_called_once_with(f"{updates.RELEASES_URL}/tag/v0.20.3")
        self.assertEqual(result["status"], "opened")
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
