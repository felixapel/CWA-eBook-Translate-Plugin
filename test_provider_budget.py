"""Integration tests for work limits across providers and batch workers."""
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ["LLM_PROVIDER"] = "local"
os.environ["LLM_MODEL"] = "fake-model"
os.environ["LLM_FALLBACK_PROVIDER"] = "minimax"
os.environ["LLM_FALLBACK_MODEL"] = "fake-fallback"
os.environ["LLM_FALLBACK_API_KEY"] = "x" * 20
os.environ.pop("BT_MAX_UPSTREAM_INFLIGHT", None)

from work_budget import WorkBudget, WorkBudgetExceeded  # noqa: E402
import translator  # noqa: E402

ROOT = Path(__file__).parent


class ProviderBudgetTests(unittest.TestCase):
    def setUp(self):
        self.original_post = translator._provider_post
        self.original_sleep = translator.time.sleep
        self.original_batch_size = translator.BT_BATCH_SIZE
        self.original_context_window = translator.BT_CONTEXT_WINDOW
        self.original_primary = translator._primary_provider
        self.original_fallback = translator._fallback_provider
        self.original_sem = translator._UPSTREAM_SEM
        self.original_queue_timeout = getattr(
            translator, "BT_UPSTREAM_QUEUE_TIMEOUT", None)
        self.original_response_limit = translator.BT_MAX_UPSTREAM_RESPONSE_BYTES
        self.original_provider_config = (
            translator.LLM_PROVIDER,
            translator.LLM_MODEL,
            translator.LLM_FALLBACK_PROVIDER,
            translator.LLM_FALLBACK_MODEL,
            translator.LLM_FALLBACK_API_KEY,
        )
        # Keep this module deterministic even when another test imported the
        # translator before the environment variables above were applied.
        translator.LLM_PROVIDER = "local"
        translator.LLM_MODEL = "fake-model"
        translator.LLM_FALLBACK_PROVIDER = "minimax"
        translator.LLM_FALLBACK_MODEL = "fake-fallback"
        translator.LLM_FALLBACK_API_KEY = "x" * 20
        translator._primary_provider = None
        translator._fallback_provider = "unset"

    def tearDown(self):
        translator._provider_post = self.original_post
        translator.time.sleep = self.original_sleep
        translator.BT_BATCH_SIZE = self.original_batch_size
        translator.BT_CONTEXT_WINDOW = self.original_context_window
        translator._primary_provider = self.original_primary
        translator._fallback_provider = self.original_fallback
        translator._UPSTREAM_SEM = self.original_sem
        if self.original_queue_timeout is not None:
            translator.BT_UPSTREAM_QUEUE_TIMEOUT = self.original_queue_timeout
        translator.BT_MAX_UPSTREAM_RESPONSE_BYTES = self.original_response_limit
        (
            translator.LLM_PROVIDER,
            translator.LLM_MODEL,
            translator.LLM_FALLBACK_PROVIDER,
            translator.LLM_FALLBACK_MODEL,
            translator.LLM_FALLBACK_API_KEY,
        ) = self.original_provider_config

    @staticmethod
    def budget(max_attempts=20, deadline_seconds=60):
        return WorkBudget(
            max_attempts=max_attempts,
            max_input_bytes=1_000_000,
            max_output_tokens=1_000_000,
            deadline_seconds=deadline_seconds,
        )

    def test_default_global_upstream_limit_is_not_unbounded(self):
        self.assertGreater(translator.BT_MAX_UPSTREAM_INFLIGHT, 0)
        self.assertIsNotNone(translator._UPSTREAM_SEM)

    def test_remote_fallback_requires_explicit_consent(self):
        calls = {"primary": 0, "fallback": 0}

        class FallbackResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"content": [{"type": "text", "text": "traducido"}]}

            def close(self):
                return None

        def primary_down_fallback_up(url, **_kwargs):
            if "minimax" in url:
                calls["fallback"] += 1
                return FallbackResponse()
            calls["primary"] += 1
            raise translator.requests.exceptions.ConnectionError(
                "synthetic primary outage")

        # Patch the deadline-aware transport seam.  Patching requests.post
        # would bypass the production call path, which owns its own Session so
        # response headers and streaming reads share the absolute work budget.
        translator._provider_post = primary_down_fallback_up
        translator.time.sleep = lambda _seconds: None

        with self.assertRaises(translator.ProviderUnavailableError):
            translator.translate_text(
                "private book text",
                max_retries=1,
                budget=self.budget(),
            )
        self.assertEqual(calls, {"primary": 1, "fallback": 0})
        self.assertEqual(
            translator.cache_lookup_backends(),
            [("local", "fake-model")],
        )

        translated, provider = translator.translate_text(
            "private book text",
            max_retries=1,
            budget=self.budget(),
            allow_cloud_fallback=True,
        )
        self.assertEqual((translated, provider), ("traducido", "minimax"))
        self.assertEqual(calls, {"primary": 2, "fallback": 1})
        self.assertEqual(
            translator.cache_lookup_backends(allow_cloud_fallback=True),
            [("local", "fake-model"), ("minimax", "fake-fallback")],
        )

    def test_local_fallback_remains_available_without_cloud_consent(self):
        translator.LLM_PROVIDER = "openai"
        translator.LLM_MODEL = "remote-primary"
        translator.LLM_FALLBACK_PROVIDER = "local"
        translator.LLM_FALLBACK_MODEL = "private-fallback"
        translator._primary_provider = None
        translator._fallback_provider = "unset"
        calls = {"primary": 0, "fallback": 0}

        class LocalResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content": "local result"}}]
                }

            def close(self):
                return None

        def remote_down_local_up(url, **_kwargs):
            if url == translator.PROVIDER_ENDPOINTS["local"][0]:
                calls["fallback"] += 1
                return LocalResponse()
            calls["primary"] += 1
            raise translator.requests.exceptions.ConnectionError(
                "synthetic remote outage")

        translator._provider_post = remote_down_local_up
        translator.time.sleep = lambda _seconds: None
        result = translator.translate_text(
            "book text", max_retries=1, budget=self.budget()
        )

        self.assertEqual(result, ("local result", "local"))
        self.assertEqual(calls, {"primary": 1, "fallback": 1})
        self.assertEqual(
            translator.cache_lookup_backends(),
            [("openai", "remote-primary"), ("local", "private-fallback")],
        )

    def test_output_cap_never_exceeds_explicit_ceiling(self):
        original_floor = translator.BT_OUTPUT_TOKEN_FLOOR
        try:
            translator.BT_OUTPUT_TOKEN_FLOOR = 256
            self.assertEqual(translator._output_cap("x", 64), 64)
        finally:
            translator.BT_OUTPUT_TOKEN_FLOOR = original_floor

    def test_segment_envelope_rejects_oversized_translation_text(self):
        translator.BT_MAX_UPSTREAM_RESPONSE_BYTES = 128
        segment_id = "a" * 32
        output = translator.json.dumps({
            "protocol": translator.SEGMENT_PROTOCOL,
            "translations": [{"id": segment_id, "text": "x" * 256}],
        })

        self.assertIsNone(
            translator._parse_segment_envelope(output, [segment_id]))

    def test_streaming_response_is_cut_off_before_json_materialization(self):
        translator.BT_MAX_UPSTREAM_RESPONSE_BYTES = 64

        class OversizedStreamingResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.chunks_read = 0
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                self.asserted_chunk_size = chunk_size
                for chunk in (b"{" + b"x" * 40, b"y" * 40, b"z" * 40):
                    self.chunks_read += 1
                    yield chunk

            def json(self):
                raise AssertionError("streaming path must not call response.json()")

            def close(self):
                self.closed = True

        response = OversizedStreamingResponse()
        translator._provider_post = lambda *_args, **_kwargs: response
        translator._fallback_provider = None

        with self.assertRaises(translator.ProviderUnavailableError):
            translator.translate_text(
                "hello", max_retries=1, budget=self.budget(max_attempts=1))

        self.assertEqual(response.chunks_read, 2)
        self.assertTrue(response.closed)

    def test_drip_stream_stops_at_absolute_deadline_and_releases_slot(self):
        class FakeClock:
            def __init__(self):
                self.now = 10.0

            def __call__(self):
                return self.now

        clock = FakeClock()

        class DripStreamingResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.chunks_read = 0
                self.closed = False

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                self.asserted_chunk_size = chunk_size
                for chunk in (b"{", b'"choices"', b":", b"[]}"):
                    clock.now += 0.4
                    self.chunks_read += 1
                    yield chunk

            def close(self):
                self.closed = True

        budget = WorkBudget(
            max_attempts=1,
            max_input_bytes=1_000_000,
            max_output_tokens=1_000_000,
            deadline_seconds=0.9,
            clock=clock,
        )
        response = DripStreamingResponse()
        translator._provider_post = lambda *_args, **_kwargs: response
        translator._fallback_provider = None
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(1)

        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_text(
                "hello", max_retries=1, budget=budget)

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertEqual(response.chunks_read, 3)
        self.assertTrue(response.closed)
        slot_released = translator._UPSTREAM_SEM.acquire(blocking=False)
        self.assertTrue(slot_released)
        if slot_released:
            translator._UPSTREAM_SEM.release()

    def test_deadline_closes_blocked_stream_and_releases_slot(self):
        class BlockingStreamingResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.closed = False
                self.unblocked = threading.Event()

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                self.asserted_chunk_size = chunk_size
                self.unblocked.wait(timeout=2)
                if not self.closed:
                    yield b"{}"

            def close(self):
                self.closed = True
                self.unblocked.set()

        response = BlockingStreamingResponse()
        translator._provider_post = lambda *_args, **_kwargs: response
        translator._fallback_provider = None
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(1)

        started = time.monotonic()
        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_text(
                "hello",
                max_retries=1,
                budget=self.budget(max_attempts=1, deadline_seconds=0.05),
            )
        elapsed = time.monotonic() - started

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertLess(elapsed, 1.0)
        self.assertTrue(response.closed)
        slot_released = translator._UPSTREAM_SEM.acquire(blocking=False)
        self.assertTrue(slot_released)
        if slot_released:
            translator._UPSTREAM_SEM.release()

    def test_deadline_covers_slow_response_headers_and_releases_slot(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(1)
        port = listener.getsockname()[1]
        server_done = threading.Event()

        def drip_headers():
            try:
                connection, _address = listener.accept()
                with connection:
                    connection.settimeout(1)
                    request = bytearray()
                    while b"\r\n\r\n" not in request:
                        chunk = connection.recv(4096)
                        if not chunk:
                            return
                        request.extend(chunk)
                    response = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 2\r\n\r\n{}"
                    )
                    for value in response:
                        try:
                            connection.sendall(bytes((value,)))
                        except OSError:
                            break
                        time.sleep(0.02)
            finally:
                listener.close()
                server_done.set()

        server_thread = threading.Thread(target=drip_headers, daemon=True)
        server_thread.start()
        provider = translator._Provider("local", "fake-model", "")
        provider.url = f"http://127.0.0.1:{port}/v1/chat/completions"
        translator._primary_provider = provider
        translator._fallback_provider = None
        translator._provider_post = translator._deadline_provider_post
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(1)

        started = time.monotonic()
        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_text(
                "hello",
                max_retries=1,
                budget=self.budget(max_attempts=1, deadline_seconds=0.08),
            )
        elapsed = time.monotonic() - started

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertLess(elapsed, 0.5)
        slot_released = translator._UPSTREAM_SEM.acquire(blocking=False)
        self.assertTrue(slot_released)
        if slot_released:
            translator._UPSTREAM_SEM.release()
        self.assertTrue(server_done.wait(timeout=1))

    def test_final_failed_attempt_does_not_sleep(self):
        sleeps = []

        def always_down(*_args, **_kwargs):
            raise translator.requests.exceptions.ConnectionError("synthetic")

        translator._provider_post = always_down
        translator.time.sleep = sleeps.append
        translator._fallback_provider = None

        with self.assertRaises(translator.ProviderUnavailableError):
            translator.translate_text(
                "hello", max_retries=1, budget=self.budget(max_attempts=1))

        self.assertEqual(sleeps, [])

    def test_retry_backoff_is_clamped_to_request_deadline(self):
        class FakeClock:
            def __init__(self):
                self.now = 10.0

            def __call__(self):
                return self.now

        clock = FakeClock()
        sleeps = []

        def advance(seconds):
            sleeps.append(seconds)
            clock.now += seconds

        def rate_limited(*_args, **_kwargs):
            response = type("Response", (), {"status_code": 429})()
            raise translator.requests.exceptions.HTTPError(
                "synthetic", response=response)

        budget = WorkBudget(
            max_attempts=2,
            max_input_bytes=1_000_000,
            max_output_tokens=1_000_000,
            deadline_seconds=0.25,
            clock=clock,
        )
        translator._provider_post = rate_limited
        translator.time.sleep = advance
        translator._fallback_provider = None

        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_text(
                "hello", max_retries=2, budget=budget)

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertEqual(sleeps, [0.25])

    def test_zero_cannot_disable_global_upstream_limit(self):
        env = os.environ.copy()
        env["BT_MAX_UPSTREAM_INFLIGHT"] = "0"
        result = subprocess.run(
            [sys.executable, "-c", "import translator"],
            cwd=os.path.dirname(__file__),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BT_MAX_UPSTREAM_INFLIGHT", result.stderr)

    def test_invalid_numeric_limits_fail_at_import(self):
        for name, value in (
            ("BT_OUTPUT_TOKEN_FACTOR", "0"),
            ("BT_OUTPUT_TOKEN_FACTOR", "-1"),
            ("BT_OUTPUT_TOKEN_FACTOR", "inf"),
            ("BT_OUTPUT_TOKEN_FACTOR", "nan"),
            ("BT_REQUEST_DEADLINE_SECONDS", "inf"),
            ("BT_REQUEST_DEADLINE_SECONDS", "nan"),
            ("BT_UPSTREAM_QUEUE_TIMEOUT", "inf"),
            ("BT_UPSTREAM_QUEUE_TIMEOUT", "nan"),
            ("BT_MAX_UPSTREAM_RESPONSE_BYTES", "0"),
        ):
            with self.subTest(name=name, value=value):
                env = os.environ.copy()
                env[name] = value
                result = subprocess.run(
                    [sys.executable, "-c", "import translator"],
                    cwd=os.path.dirname(__file__),
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)

    def test_shared_cap_prevents_attempt_eight_across_groups_and_fallback(self):
        calls = 0
        calls_lock = threading.Lock()

        def always_down(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
            raise translator.requests.exceptions.ConnectionError("synthetic down")

        translator._provider_post = always_down
        translator.time.sleep = lambda _seconds: None
        translator.BT_BATCH_SIZE = 1
        budget = self.budget(max_attempts=7)

        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_batch(
                [f"paragraph {i}" for i in range(12)],
                max_concurrent=4,
                budget=budget,
            )

        self.assertEqual(raised.exception.reason, "attempts")
        self.assertEqual(calls, 7)

    def test_expired_budget_starts_no_provider_call(self):
        calls = 0

        def must_not_run(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("provider should not be called")

        translator._provider_post = must_not_run
        budget = self.budget(deadline_seconds=0.001)
        budget._deadline = budget._clock()  # deterministic expiry before call

        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_text("hello", budget=budget)

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertEqual(calls, 0)

    def test_full_global_gate_rejects_without_calling_provider(self):
        calls = 0

        def must_not_run(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("provider should not be called")

        translator._provider_post = must_not_run
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(1)
        translator.BT_UPSTREAM_QUEUE_TIMEOUT = 0.001
        translator._UPSTREAM_SEM.acquire()
        try:
            with self.assertRaises(WorkBudgetExceeded) as raised:
                translator.translate_text("hello", budget=self.budget())
        finally:
            translator._UPSTREAM_SEM.release()

        self.assertEqual(raised.exception.reason, "queue")
        self.assertEqual(calls, 0)

    def test_default_budget_serves_max_batch_via_consented_healthy_fallback(self):
        calls = {"primary": 0, "fallback": 0}

        class FallbackResponse:
            status_code = 200
            text = ""

            def __init__(self, payload):
                user_content = payload["messages"][0]["content"]
                request_body = translator.json.loads(user_content)
                envelope = {
                    "protocol": translator.SEGMENT_PROTOCOL,
                    "translations": [
                        {"id": segment["id"], "text": "translated"}
                        for segment in request_body["segments"]
                    ],
                }
                self._body = {
                    "content": [{
                        "type": "text",
                        "text": translator.json.dumps(envelope),
                    }],
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        def primary_down_fallback_up(url, **kwargs):
            if "minimax" not in url:
                calls["primary"] += 1
                raise translator.requests.exceptions.ConnectionError(
                    "synthetic primary outage")
            calls["fallback"] += 1
            return FallbackResponse(kwargs["json"])

        translator._provider_post = primary_down_fallback_up
        translator.time.sleep = lambda _seconds: None
        translator.BT_BATCH_SIZE = 5

        results = translator.translate_batch(
            [f"{i:02d}" + "🙂" * 7998 for i in range(50)],
            max_concurrent=2,
            budget=translator.create_work_budget(),
            allow_cloud_fallback=True,
        )

        self.assertEqual(len(results), 50)
        self.assertTrue(all(text == "translated" for text, _ in results))
        self.assertTrue(all(provider == "minimax" for _, provider in results))
        self.assertEqual(calls, {"primary": 10, "fallback": 10})

    def test_invalid_segment_retries_once_with_fresh_ids_and_shared_budget(self):
        envelopes = []

        class OpenAIResponse:
            status_code = 200
            headers = {}

            def __init__(self, payload):
                request_body = translator.json.loads(
                    payload["messages"][1]["content"])
                envelopes.append(request_body)
                if len(envelopes) == 1:
                    output = "not-json"
                else:
                    output = translator.json.dumps({
                        "protocol": translator.SEGMENT_PROTOCOL,
                        "translations": [
                            {
                                "id": segment["id"],
                                "text": f"translated:{segment['text']}",
                            }
                            for segment in request_body["segments"]
                        ],
                    })
                self._body = {
                    "choices": [{"message": {"content": output}}],
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        translator._provider_post = (
            lambda _url, **kwargs: OpenAIResponse(kwargs["json"])
        )
        translator._fallback_provider = None
        translator.BT_BATCH_SIZE = 2
        translator.BT_CONTEXT_WINDOW = 1
        budget = self.budget(max_attempts=2)
        recovery = translator.BatchRecoveryTracker()
        texts = ["before", "one", "two", "after"]

        results = translator.translate_batch_detailed(
            texts,
            selected_groups=[[1, 2]],
            max_concurrent=1,
            budget=budget,
            recovery_tracker=recovery,
        )

        self.assertEqual(
            [(item.text, item.provider) for item in results[1:3]],
            [("translated:one", "local"), ("translated:two", "local")],
        )
        self.assertEqual(
            [item.recovery_path for item in results[1:3]],
            ["envelope_retry", "envelope_retry"],
        )
        self.assertTrue(all(item.server_cacheable for item in results[1:3]))
        self.assertEqual(len(envelopes), 2)
        self.assertEqual(
            [segment["text"] for segment in envelopes[0]["segments"]],
            ["one", "two"],
        )
        self.assertEqual(envelopes[0].get("context"), envelopes[1].get("context"))
        first_ids = [segment["id"] for segment in envelopes[0]["segments"]]
        second_ids = [segment["id"] for segment in envelopes[1]["segments"]]
        self.assertTrue(set(first_ids).isdisjoint(second_ids))
        self.assertEqual(budget.snapshot()["attempts"], 2)
        self.assertEqual(recovery.snapshot(), {
            "envelope_retry_groups": 1,
            "envelope_retry_recovered_groups": 1,
            "paragraph_fallback_groups": 0,
            "paragraph_fallback_recovered_segments": 0,
            "paragraph_fallback_failed_segments": 0,
        })

    def test_two_invalid_segments_fall_back_sequentially_within_budget(self):
        calls = []

        class OpenAIResponse:
            status_code = 200
            headers = {}

            def __init__(self, payload):
                system = payload["messages"][0]["content"]
                user_content = payload["messages"][1]["content"]
                if translator.SEGMENT_PROTOCOL in system:
                    calls.append("batch")
                    output = "not-json"
                else:
                    calls.append(f"single:{user_content}")
                    output = f"translated:{user_content}"
                self._body = {
                    "choices": [{"message": {"content": output}}],
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        translator._provider_post = (
            lambda _url, **kwargs: OpenAIResponse(kwargs["json"])
        )
        translator._fallback_provider = None
        translator.BT_BATCH_SIZE = 3
        budget = self.budget(max_attempts=5)
        recovery = translator.BatchRecoveryTracker()

        results = translator.translate_batch_detailed(
            ["one", "two", "three"],
            max_concurrent=1,
            budget=budget,
            recovery_tracker=recovery,
        )

        self.assertEqual(
            [(item.text, item.provider) for item in results],
            [
                ("translated:one", "local"),
                ("translated:two", "local"),
                ("translated:three", "local"),
            ],
        )
        self.assertTrue(all(not item.server_cacheable for item in results))
        self.assertEqual(
            [item.recovery_path for item in results],
            ["paragraph_fallback"] * 3,
        )
        self.assertEqual(
            calls,
            ["batch", "batch", "single:one", "single:two", "single:three"],
        )
        self.assertEqual(budget.snapshot()["attempts"], 5)
        self.assertEqual(recovery.snapshot(), {
            "envelope_retry_groups": 1,
            "envelope_retry_recovered_groups": 0,
            "paragraph_fallback_groups": 1,
            "paragraph_fallback_recovered_segments": 3,
            "paragraph_fallback_failed_segments": 0,
        })

    def test_individual_recovery_failure_is_isolated_to_its_segment(self):
        calls = []

        class OpenAIResponse:
            status_code = 200
            headers = {}

            def __init__(self, output):
                self._body = {
                    "choices": [{"message": {"content": output}}],
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        def malformed_then_partial_recovery(_url, **kwargs):
            payload = kwargs["json"]
            system = payload["messages"][0]["content"]
            user_content = payload["messages"][1]["content"]
            if translator.SEGMENT_PROTOCOL in system:
                calls.append("batch")
                return OpenAIResponse("not-json")
            calls.append(f"single:{user_content}")
            if user_content == "two":
                raise translator.requests.exceptions.ConnectionError(
                    "synthetic recovery outage"
                )
            return OpenAIResponse(f"translated:{user_content}")

        translator._provider_post = malformed_then_partial_recovery
        translator._fallback_provider = None
        translator.BT_BATCH_SIZE = 3
        budget = self.budget(max_attempts=5)

        results = translator.translate_batch_detailed(
            ["one", "two", "three"],
            max_concurrent=1,
            budget=budget,
        )

        self.assertEqual(
            [item.text for item in results],
            [
                "translated:one",
                "[TRANSLATION ERROR: provider_unavailable]",
                "translated:three",
            ],
        )
        self.assertEqual(
            [item.recovery_path for item in results],
            [
                "paragraph_fallback",
                "paragraph_fallback_failed",
                "paragraph_fallback",
            ],
        )
        self.assertTrue(all(not item.server_cacheable for item in results))
        self.assertEqual(
            calls,
            ["batch", "batch", "single:one", "single:two", "single:three"],
        )
        self.assertEqual(budget.snapshot()["attempts"], 5)

    def test_individual_recovery_preserves_remote_fallback_consent(self):
        calls = {"primary": 0, "fallback": 0}

        class OpenAIResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content": "not-json"}}],
                }

        class AnthropicResponse:
            status_code = 200
            headers = {}

            def __init__(self, output):
                self.output = output

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "content": [{"type": "text", "text": self.output}],
                }

        def malformed_primary_and_healthy_cloud(url, **kwargs):
            payload = kwargs["json"]
            if "minimax" in url:
                calls["fallback"] += 1
                user_content = payload["messages"][0]["content"]
                return AnthropicResponse(f"cloud:{user_content}")
            calls["primary"] += 1
            system = payload["messages"][0]["content"]
            if translator.SEGMENT_PROTOCOL in system:
                return OpenAIResponse()
            raise translator.requests.exceptions.ConnectionError(
                "synthetic local recovery outage"
            )

        translator._provider_post = malformed_primary_and_healthy_cloud
        translator.BT_BATCH_SIZE = 2

        denied_budget = self.budget(max_attempts=4)
        denied = translator.translate_batch_detailed(
            ["one", "two"],
            max_concurrent=1,
            budget=denied_budget,
            allow_cloud_fallback=False,
        )

        self.assertEqual(
            [item.text for item in denied],
            [
                "[TRANSLATION ERROR: provider_unavailable]",
                "[TRANSLATION ERROR: provider_unavailable]",
            ],
        )
        self.assertEqual(calls, {"primary": 4, "fallback": 0})
        self.assertEqual(denied_budget.snapshot()["attempts"], 4)

        calls.update(primary=0, fallback=0)
        allowed_budget = self.budget(max_attempts=6)
        allowed = translator.translate_batch_detailed(
            ["one", "two"],
            max_concurrent=1,
            budget=allowed_budget,
            allow_cloud_fallback=True,
        )

        self.assertEqual(
            [(item.text, item.provider) for item in allowed],
            [("cloud:one", "minimax"), ("cloud:two", "minimax")],
        )
        self.assertTrue(all(not item.server_cacheable for item in allowed))
        self.assertEqual(calls, {"primary": 4, "fallback": 2})
        self.assertEqual(allowed_budget.snapshot()["attempts"], 6)

    def test_segment_recovery_stops_before_call_over_budget(self):
        calls = 0

        class MalformedResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content": "not-json"}}],
                }

        def malformed_provider(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return MalformedResponse()

        translator._provider_post = malformed_provider
        translator._fallback_provider = None
        translator.BT_BATCH_SIZE = 3
        budget = self.budget(max_attempts=2)
        recovery = translator.BatchRecoveryTracker()

        with self.assertRaises(WorkBudgetExceeded) as raised:
            translator.translate_batch_detailed(
                ["one", "two", "three"],
                max_concurrent=1,
                budget=budget,
                recovery_tracker=recovery,
            )

        self.assertEqual(raised.exception.reason, "attempts")
        self.assertEqual(calls, 2)
        self.assertEqual(budget.snapshot()["attempts"], 2)
        self.assertEqual(recovery.snapshot(), {
            "envelope_retry_groups": 1,
            "envelope_retry_recovered_groups": 0,
            "paragraph_fallback_groups": 1,
            "paragraph_fallback_recovered_segments": 0,
            "paragraph_fallback_failed_segments": 0,
        })

    def test_malformed_group_recovers_without_cancelling_healthy_sibling(self):
        calls = []
        calls_lock = threading.Lock()
        first_wave = threading.Barrier(2)
        bad_batch_calls = 0

        class OpenAIResponse:
            status_code = 200
            headers = {}

            def __init__(self, output):
                self._body = {
                    "choices": [{"message": {"content": output}}],
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._body

        def malformed_and_healthy_groups(_url, **kwargs):
            nonlocal bad_batch_calls
            payload = kwargs["json"]
            system = payload["messages"][0]["content"]
            user_content = payload["messages"][1]["content"]
            if translator.SEGMENT_PROTOCOL not in system:
                with calls_lock:
                    calls.append(f"single:{user_content}")
                return OpenAIResponse(f"translated:{user_content}")

            request_body = translator.json.loads(user_content)
            first_text = request_body["segments"][0]["text"]
            with calls_lock:
                calls.append(f"batch:{first_text}")
                if first_text == "bad-0":
                    bad_batch_calls += 1
                    wait_for_first_wave = bad_batch_calls == 1
                else:
                    wait_for_first_wave = True
            if wait_for_first_wave:
                first_wave.wait(timeout=1)
            if first_text == "bad-0":
                return OpenAIResponse("not-json")
            return OpenAIResponse(translator.json.dumps({
                "protocol": translator.SEGMENT_PROTOCOL,
                "translations": [
                    {
                        "id": segment["id"],
                        "text": f"translated:{segment['text']}",
                    }
                    for segment in request_body["segments"]
                ],
            }))

        translator._provider_post = malformed_and_healthy_groups
        translator.BT_BATCH_SIZE = 2
        translator._fallback_provider = None
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(2)
        budget = self.budget(max_attempts=5)

        results = translator.translate_batch_detailed(
            ["bad-0", "bad-1", "good-0", "good-1"],
            max_concurrent=2,
            budget=budget,
        )

        self.assertEqual(
            [item.text for item in results],
            [
                "translated:bad-0",
                "translated:bad-1",
                "translated:good-0",
                "translated:good-1",
            ],
        )
        self.assertEqual(
            [item.recovery_path for item in results],
            ["paragraph_fallback", "paragraph_fallback", "direct", "direct"],
        )
        self.assertEqual(
            [item.server_cacheable for item in results],
            [False, False, True, True],
        )
        self.assertCountEqual(
            calls,
            [
                "batch:bad-0",
                "batch:bad-0",
                "single:bad-0",
                "single:bad-1",
                "batch:good-0",
            ],
        )
        self.assertEqual(budget.snapshot()["attempts"], 5)


class DeploymentBudgetContractTests(unittest.TestCase):
    def test_recommended_compose_pins_safe_budget_defaults(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        expected = {
            "BT_MAX_UPSTREAM_INFLIGHT": "2",
            "BT_UPSTREAM_QUEUE_TIMEOUT": "2",
            "BT_REQUEST_MAX_ATTEMPTS": "20",
            "BT_REQUEST_MAX_INPUT_BYTES": "5000000",
            "BT_REQUEST_MAX_OUTPUT_TOKENS": "163840",
            "BT_REQUEST_DEADLINE_SECONDS": "90",
            "BT_MAX_UPSTREAM_RESPONSE_BYTES": "1048576",
        }
        for name, value in expected.items():
            self.assertIn(f"- {name}={value}", compose)

    def test_readme_and_unraid_templates_do_not_document_unlimited_default(self):
        readme = (ROOT / "README.md").read_text()
        templates = "\n".join(
            path.read_text()
            for path in (ROOT / "deploy" / "unraid").glob("*.xml.tmpl")
        )
        self.assertIn("| `BT_MAX_UPSTREAM_INFLIGHT` | `2` |", readme)
        self.assertNotIn("| `BT_MAX_UPSTREAM_INFLIGHT` | `0` |", readme)
        self.assertNotIn("(0 = unlimited)", templates)


if __name__ == "__main__":
    unittest.main(verbosity=2)
