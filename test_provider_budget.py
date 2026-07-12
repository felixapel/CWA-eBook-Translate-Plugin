"""Integration tests for work limits across providers and batch workers."""
import os
import subprocess
import sys
import threading
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


class DeploymentBudgetContractTests(unittest.TestCase):
    def test_recommended_compose_pins_safe_budget_defaults(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        expected = {
            "BT_MAX_UPSTREAM_INFLIGHT": "2",
            "BT_UPSTREAM_QUEUE_TIMEOUT": "2",
            "BT_REQUEST_MAX_ATTEMPTS": "20",
            "BT_REQUEST_MAX_INPUT_BYTES": "2000000",
            "BT_REQUEST_MAX_OUTPUT_TOKENS": "163840",
            "BT_REQUEST_DEADLINE_SECONDS": "90",
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
