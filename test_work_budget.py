"""Deterministic contracts for request-wide upstream work limits."""
import threading
import unittest

from work_budget import WorkBudget, WorkBudgetExceeded


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class WorkBudgetUnitTests(unittest.TestCase):
    def make_budget(self, **overrides):
        values = {
            "max_attempts": 2,
            "max_input_bytes": 16,
            "max_output_tokens": 10,
            "deadline_seconds": 5,
            "clock": FakeClock(),
        }
        values.update(overrides)
        return WorkBudget(**values)

    def test_exact_attempt_cap_and_atomic_rejection(self):
        budget = self.make_budget(max_input_bytes=100, max_output_tokens=100)
        budget.reserve_attempt("one", 4)
        budget.reserve_attempt("two", 4)

        with self.assertRaises(WorkBudgetExceeded) as raised:
            budget.reserve_attempt("three", 4)

        self.assertEqual(raised.exception.reason, "attempts")
        self.assertEqual(budget.snapshot(), {
            "attempts": 2,
            "input_bytes": 6,
            "output_tokens": 8,
        })

    def test_utf8_bytes_and_output_tokens_are_cumulative(self):
        budget = self.make_budget(max_input_bytes=4, max_output_tokens=6)
        budget.reserve_attempt("é", 3)  # two UTF-8 bytes
        budget.reserve_attempt("é", 3)
        self.assertEqual(budget.snapshot()["input_bytes"], 4)

        with self.assertRaises(WorkBudgetExceeded) as raised:
            budget.reserve_attempt("x", 1)
        self.assertEqual(raised.exception.reason, "attempts")

        token_budget = self.make_budget(
            max_attempts=5, max_input_bytes=100, max_output_tokens=3)
        token_budget.reserve_attempt("x", 3)
        with self.assertRaises(WorkBudgetExceeded) as token_error:
            token_budget.reserve_attempt("x", 1)
        self.assertEqual(token_error.exception.reason, "output_tokens")

    def test_expired_deadline_consumes_nothing(self):
        clock = FakeClock()
        budget = self.make_budget(clock=clock)
        clock.advance(5)

        with self.assertRaises(WorkBudgetExceeded) as raised:
            budget.reserve_attempt("never sent", 1)

        self.assertEqual(raised.exception.reason, "deadline")
        self.assertEqual(budget.snapshot(), {
            "attempts": 0,
            "input_bytes": 0,
            "output_tokens": 0,
        })

    def test_reservations_are_thread_safe(self):
        budget = self.make_budget(
            max_attempts=1, max_input_bytes=100, max_output_tokens=100)
        barrier = threading.Barrier(3)
        outcomes = []

        def reserve():
            barrier.wait()
            try:
                budget.reserve_attempt("x", 1)
                outcomes.append("allowed")
            except WorkBudgetExceeded as exc:
                outcomes.append(exc.reason)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertCountEqual(outcomes, ["allowed", "attempts"])
        self.assertEqual(budget.snapshot()["attempts"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
