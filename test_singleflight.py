"""Concurrency contracts for duplicate-work coalescing."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from singleflight import SingleFlight, SingleFlightTimeout
from work_budget import WorkBudget
import translator


def budget() -> WorkBudget:
    return WorkBudget(
        max_attempts=20,
        max_input_bytes=1_000_000,
        max_output_tokens=100_000,
        deadline_seconds=5,
    )


class SingleFlightTests(unittest.TestCase):
    def test_concurrent_identical_calls_execute_once(self) -> None:
        flights = SingleFlight(max_entries=16, result_ttl_seconds=1)
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()
        results = []

        def operation():
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            self.assertTrue(release.wait(1))
            return "translated"

        def invoke():
            results.append(flights.run("same-key", operation, timeout=1))

        leader = threading.Thread(target=invoke)
        follower = threading.Thread(target=invoke)
        leader.start()
        self.assertTrue(entered.wait(1))
        follower.start()
        deadline = time.monotonic() + 1
        while flights.stats()["followers_waiting"] < 1 and time.monotonic() < deadline:
            time.sleep(0.001)
        release.set()
        leader.join(1)
        follower.join(1)

        self.assertEqual(calls, 1)
        self.assertEqual([result.value for result in results], ["translated"] * 2)
        self.assertCountEqual([result.shared for result in results], [False, True])

    def test_follower_timeout_does_not_cancel_leader(self) -> None:
        flights = SingleFlight(max_entries=4, result_ttl_seconds=1)
        entered = threading.Event()
        release = threading.Event()
        leader_result = []

        def operation():
            entered.set()
            self.assertTrue(release.wait(1))
            return "done"

        leader = threading.Thread(
            target=lambda: leader_result.append(
                flights.run("key", operation, timeout=1).value
            )
        )
        leader.start()
        self.assertTrue(entered.wait(1))

        with self.assertRaises(SingleFlightTimeout):
            flights.run("key", operation, timeout=0.01)

        self.assertTrue(leader.is_alive())
        release.set()
        leader.join(1)
        self.assertEqual(leader_result, ["done"])
        self.assertEqual(flights.stats()["wait_timeouts"], 1)

    def test_exception_is_shared_and_key_can_be_invalidated(self) -> None:
        flights = SingleFlight(max_entries=4, result_ttl_seconds=10)
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            raise RuntimeError("provider failed")

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                flights.run("key", fail, timeout=1)
        self.assertEqual(calls, 1)

        flights.invalidate("key")
        with self.assertRaises(RuntimeError):
            flights.run("key", fail, timeout=1)
        self.assertEqual(calls, 2)

    def test_completed_registry_is_bounded(self) -> None:
        flights = SingleFlight(max_entries=2, result_ttl_seconds=60)
        for index in range(10):
            result = flights.run(
                f"key-{index}", lambda index=index: index, timeout=1
            )
            self.assertEqual(result.value, index)
        self.assertLessEqual(flights.stats()["retained_entries"], 2)

    def test_invalid_limits_fail_closed(self) -> None:
        for max_entries, ttl in ((0, 1), (-1, 1), (1, -1), (True, 1)):
            with self.subTest(max_entries=max_entries, ttl=ttl):
                with self.assertRaises(ValueError):
                    SingleFlight(
                        max_entries=max_entries,
                        result_ttl_seconds=ttl,
                    )


class TranslatorSingleFlightTests(unittest.TestCase):
    def test_default_retention_does_not_reuse_sequential_results(self) -> None:
        flights = SingleFlight(max_entries=16, result_ttl_seconds=0)
        with (
            mock.patch.object(translator, "_TRANSLATION_SINGLEFLIGHT", flights),
            mock.patch.object(
                translator, "_complete", return_value=("hola", "local")
            ) as complete,
        ):
            for _ in range(2):
                translator.translate_text(
                    "hello",
                    budget=budget(),
                    operation_namespace="same-namespace",
                )
        self.assertEqual(complete.call_count, 2)

    def test_identical_single_translations_share_one_completion(self) -> None:
        flights = SingleFlight(max_entries=16, result_ttl_seconds=1)
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        results = []
        errors = []

        def complete(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            self.assertTrue(release.wait(1))
            return "hola", "local"

        def invoke():
            try:
                results.append(translator.translate_text(
                    "hello",
                    budget=budget(),
                    operation_namespace="tenant-book-chapter",
                ))
            except Exception as exc:  # pragma: no cover - assertion evidence
                errors.append(exc)

        with (
            mock.patch.object(translator, "_TRANSLATION_SINGLEFLIGHT", flights),
            mock.patch.object(translator, "_complete", side_effect=complete),
        ):
            leader = threading.Thread(target=invoke)
            follower = threading.Thread(target=invoke)
            leader.start()
            self.assertTrue(entered.wait(1))
            follower.start()
            deadline = time.monotonic() + 1
            while flights.stats()["followers_waiting"] < 1 and time.monotonic() < deadline:
                time.sleep(0.001)
            release.set()
            leader.join(1)
            follower.join(1)

        self.assertEqual(errors, [])
        self.assertEqual(calls, 1)
        self.assertEqual(results, [("hola", "local"), ("hola", "local")])

    def test_identical_batch_groups_share_one_provider_operation(self) -> None:
        flights = SingleFlight(max_entries=16, result_ttl_seconds=0)
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        results = []
        errors = []

        def group_operation(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            self.assertTrue(release.wait(1))
            return [("uno", "local"), ("dos", "local")]

        def invoke():
            try:
                results.append(translator.translate_batch(
                    ["one", "two"],
                    max_concurrent=1,
                    budget=budget(),
                    operation_namespace="tenant-book-chapter",
                ))
            except Exception as exc:  # pragma: no cover - assertion evidence
                errors.append(exc)

        with (
            mock.patch.object(translator, "_TRANSLATION_SINGLEFLIGHT", flights),
            mock.patch.object(
                translator, "_translate_group_operation", side_effect=group_operation
            ),
        ):
            leader = threading.Thread(target=invoke)
            follower = threading.Thread(target=invoke)
            leader.start()
            self.assertTrue(entered.wait(1))
            follower.start()
            deadline = time.monotonic() + 1
            while flights.stats()["followers_waiting"] < 1 and time.monotonic() < deadline:
                time.sleep(0.001)
            release.set()
            leader.join(1)
            follower.join(1)

        self.assertEqual(errors, [])
        self.assertEqual(calls, 1)
        self.assertEqual(results, [
            [("uno", "local"), ("dos", "local")],
            [("uno", "local"), ("dos", "local")],
        ])

    def test_different_namespaces_never_share_work(self) -> None:
        flights = SingleFlight(max_entries=16, result_ttl_seconds=10)
        with (
            mock.patch.object(translator, "_TRANSLATION_SINGLEFLIGHT", flights),
            mock.patch.object(
                translator, "_complete", return_value=("hola", "local")
            ) as complete,
        ):
            translator.translate_text(
                "hello", budget=budget(), operation_namespace="tenant-a"
            )
            translator.translate_text(
                "hello", budget=budget(), operation_namespace="tenant-b"
            )
        self.assertEqual(complete.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
