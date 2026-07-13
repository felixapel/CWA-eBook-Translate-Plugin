"""Integration tests for work limits across providers and batch workers."""
import os
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
        self.original_post = translator.requests.post
        self.original_sleep = translator.time.sleep
        self.original_batch_size = translator.BT_BATCH_SIZE
        self.original_primary = translator._primary_provider
        self.original_fallback = translator._fallback_provider
        self.original_sem = translator._UPSTREAM_SEM
        self.original_queue_timeout = getattr(
            translator, "BT_UPSTREAM_QUEUE_TIMEOUT", None)
        self.original_response_limit = translator.BT_MAX_UPSTREAM_RESPONSE_BYTES
        translator._primary_provider = None
        translator._fallback_provider = "unset"

    def tearDown(self):
        translator.requests.post = self.original_post
        translator.time.sleep = self.original_sleep
        translator.BT_BATCH_SIZE = self.original_batch_size
        translator._primary_provider = self.original_primary
        translator._fallback_provider = self.original_fallback
        translator._UPSTREAM_SEM = self.original_sem
        if self.original_queue_timeout is not None:
            translator.BT_UPSTREAM_QUEUE_TIMEOUT = self.original_queue_timeout
        translator.BT_MAX_UPSTREAM_RESPONSE_BYTES = self.original_response_limit

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
        translator.requests.post = lambda *_args, **_kwargs: response
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
        translator.requests.post = lambda *_args, **_kwargs: response
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
        translator.requests.post = lambda *_args, **_kwargs: response
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

    def test_final_failed_attempt_does_not_sleep(self):
        sleeps = []

        def always_down(*_args, **_kwargs):
            raise translator.requests.exceptions.ConnectionError("synthetic")

        translator.requests.post = always_down
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
        translator.requests.post = rate_limited
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

    def test_non_finite_time_limits_fail_at_import(self):
        for name, value in (
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

        translator.requests.post = always_down
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

        translator.requests.post = must_not_run
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

        translator.requests.post = must_not_run
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

    def test_default_budget_serves_max_batch_via_healthy_fallback(self):
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

        translator.requests.post = primary_down_fallback_up
        translator.time.sleep = lambda _seconds: None
        translator.BT_BATCH_SIZE = 5

        results = translator.translate_batch(
            [f"{i:02d}" + "🙂" * 7998 for i in range(50)],
            max_concurrent=2,
            budget=translator.create_work_budget(),
        )

        self.assertEqual(len(results), 50)
        self.assertTrue(all(text == "translated" for text, _ in results))
        self.assertTrue(all(provider == "minimax" for _, provider in results))
        self.assertEqual(calls, {"primary": 10, "fallback": 10})

    def test_invalid_segment_cancels_queued_groups_without_waiting(self):
        calls = 0
        calls_lock = threading.Lock()
        slow_done = threading.Event()
        first_wave = threading.Barrier(2)

        class OpenAIResponse:
            status_code = 200
            text = ""

            def __init__(self, payload, malformed=False):
                user_content = payload["messages"][1]["content"]
                request_body = translator.json.loads(user_content)
                if malformed:
                    output = "not-json"
                else:
                    output = translator.json.dumps({
                        "protocol": translator.SEGMENT_PROTOCOL,
                        "translations": [
                            {"id": segment["id"], "text": "translated"}
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

        def one_malformed_others_slow(_url, **kwargs):
            nonlocal calls
            request_body = translator.json.loads(
                kwargs["json"]["messages"][1]["content"])
            malformed = request_body["segments"][0]["text"] == "bad-0"
            with calls_lock:
                calls += 1
            first_wave.wait(timeout=1)
            if not malformed:
                time.sleep(0.25)
                slow_done.set()
            return OpenAIResponse(kwargs["json"], malformed=malformed)

        translator.requests.post = one_malformed_others_slow
        translator.BT_BATCH_SIZE = 2
        translator._fallback_provider = None
        translator._UPSTREAM_SEM = threading.BoundedSemaphore(2)

        started = time.monotonic()
        with self.assertRaises(translator.SegmentProtocolError):
            translator.translate_batch(
                ["bad-0", "bad-1", "slow-0", "slow-1",
                 "queued-0", "queued-1", "queued-2", "queued-3"],
                max_concurrent=2,
                budget=self.budget(max_attempts=20),
            )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertEqual(calls, 2)
        self.assertTrue(slow_done.wait(timeout=1))


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

    def test_readme_and_unraid_template_do_not_document_unlimited_default(self):
        readme = (ROOT / "README.md").read_text()
        template = (ROOT / "my-book-translator-api.xml.tmpl").read_text()
        self.assertIn("| `BT_MAX_UPSTREAM_INFLIGHT` | `2` |", readme)
        self.assertNotIn("| `BT_MAX_UPSTREAM_INFLIGHT` | `0` |", readme)
        self.assertIn('Target="BT_MAX_UPSTREAM_INFLIGHT" Default="2"', template)
        self.assertNotIn("(0 = unlimited)", template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
