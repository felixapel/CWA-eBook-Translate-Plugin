"""Bounded duplicate-work coalescing with caller-local wait deadlines."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class SingleFlightTimeout(TimeoutError):
    """A follower's own wait deadline expired; the leader keeps running."""


class SingleFlightCapacityError(RuntimeError):
    """The bounded registry contains only active operations."""


@dataclass(frozen=True, slots=True)
class FlightResult(Generic[T]):
    value: T
    shared: bool


@dataclass(slots=True)
class _Flight(Generic[T]):
    created_at: float
    event: threading.Event = field(default_factory=threading.Event)
    completed_at: float | None = None
    value: T | None = None
    error: BaseException | None = None
    waiters: int = 0


class SingleFlight:
    """Share one callable result between callers using the same opaque key."""

    def __init__(
        self,
        *,
        max_entries: int,
        result_ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries <= 0
        ):
            raise ValueError("max_entries must be a positive integer")
        if (
            isinstance(result_ttl_seconds, bool)
            or not isinstance(result_ttl_seconds, (int, float))
            or not math.isfinite(result_ttl_seconds)
            or result_ttl_seconds < 0
        ):
            raise ValueError("result_ttl_seconds must be a finite non-negative number")
        self.max_entries = max_entries
        self.result_ttl_seconds = float(result_ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}
        self._leaders = 0
        self._shared_results = 0
        self._followers_waiting = 0
        self._wait_timeouts = 0
        self._capacity_rejections = 0

    def _expire_completed(self, now: float) -> None:
        expired = [
            key
            for key, flight in self._flights.items()
            if flight.completed_at is not None
            and now - flight.completed_at >= self.result_ttl_seconds
        ]
        for key in expired:
            del self._flights[key]

    def _evict_oldest_completed(self) -> bool:
        completed = [
            (flight.completed_at, key)
            for key, flight in self._flights.items()
            if flight.completed_at is not None
        ]
        if not completed:
            return False
        _completed_at, key = min(completed)
        del self._flights[key]
        return True

    @staticmethod
    def _validate_key_and_timeout(key: str, timeout: float) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("singleflight key must be a non-empty string")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a finite non-negative number")

    def run(
        self,
        key: str,
        operation: Callable[[], T],
        *,
        timeout: float,
    ) -> FlightResult[T]:
        self._validate_key_and_timeout(key, timeout)
        if not callable(operation):
            raise ValueError("operation must be callable")

        with self._lock:
            now = self._clock()
            self._expire_completed(now)
            flight = self._flights.get(key)
            if flight is None:
                while len(self._flights) >= self.max_entries:
                    if not self._evict_oldest_completed():
                        self._capacity_rejections += 1
                        raise SingleFlightCapacityError(
                            "singleflight registry is at active capacity"
                        )
                flight = _Flight(created_at=now)
                self._flights[key] = flight
                self._leaders += 1
                leader = True
                joined_active = False
            else:
                self._shared_results += 1
                leader = False
                joined_active = flight.completed_at is None
                if joined_active:
                    flight.waiters += 1
                    self._followers_waiting += 1

        if leader:
            try:
                value = operation()
            except BaseException as exc:
                with self._lock:
                    flight.error = exc
                    flight.completed_at = self._clock()
                    flight.event.set()
                raise
            with self._lock:
                flight.value = value
                flight.completed_at = self._clock()
                flight.event.set()
            return FlightResult(value=value, shared=False)

        completed = flight.event.wait(timeout)
        if joined_active:
            with self._lock:
                flight.waiters -= 1
                self._followers_waiting -= 1
                if not completed:
                    self._wait_timeouts += 1
        if not completed:
            raise SingleFlightTimeout("singleflight follower deadline expired")
        if flight.error is not None:
            raise flight.error
        return FlightResult(value=flight.value, shared=True)

    def invalidate(self, key: str) -> None:
        """Forget a completed key; active leaders cannot be invalidated."""
        with self._lock:
            flight = self._flights.get(key)
            if flight is not None and flight.completed_at is not None:
                del self._flights[key]

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._expire_completed(self._clock())
            active = sum(
                flight.completed_at is None for flight in self._flights.values()
            )
            return {
                "leaders": self._leaders,
                "shared_results": self._shared_results,
                "followers_waiting": self._followers_waiting,
                "wait_timeouts": self._wait_timeouts,
                "capacity_rejections": self._capacity_rejections,
                "active_entries": active,
                "retained_entries": len(self._flights),
            }
