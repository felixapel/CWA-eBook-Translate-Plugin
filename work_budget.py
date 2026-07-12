"""Thread-safe, request-scoped limits for upstream translation work."""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


class WorkBudgetExceeded(RuntimeError):
    """A request cannot start more upstream work within its configured cap."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Work budget exhausted: {reason}")


class WorkBudget:
    """Atomically accounts attempts, UTF-8 input bytes, tokens, and deadline."""

    def __init__(
        self,
        *,
        max_attempts: int,
        max_input_bytes: int,
        max_output_tokens: int,
        deadline_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        limits = {
            "max_attempts": max_attempts,
            "max_input_bytes": max_input_bytes,
            "max_output_tokens": max_output_tokens,
        }
        for name, value in limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (isinstance(deadline_seconds, bool)
                or not isinstance(deadline_seconds, (int, float))
                or not math.isfinite(deadline_seconds)
                or deadline_seconds <= 0):
            raise ValueError("deadline_seconds must be a finite positive number")

        self.max_attempts = max_attempts
        self.max_input_bytes = max_input_bytes
        self.max_output_tokens = max_output_tokens
        self._clock = clock
        self._deadline = clock() + float(deadline_seconds)
        self._lock = threading.Lock()
        self._attempts = 0
        self._input_bytes = 0
        self._output_tokens = 0

    def remaining_seconds(self) -> float:
        """Seconds until the absolute request deadline, clamped at zero."""
        return max(0.0, self._deadline - self._clock())

    def reserve_attempt(self, input_text: str, output_tokens: int) -> None:
        """Reserve one provider call or raise without consuming any counters."""
        if not isinstance(input_text, str):
            raise TypeError("input_text must be a string")
        if (isinstance(output_tokens, bool)
                or not isinstance(output_tokens, int)
                or output_tokens <= 0):
            raise ValueError("output_tokens must be a positive integer")
        input_bytes = len(input_text.encode("utf-8"))

        with self._lock:
            if self._clock() >= self._deadline:
                raise WorkBudgetExceeded("deadline")
            if self._attempts + 1 > self.max_attempts:
                raise WorkBudgetExceeded("attempts")
            if self._input_bytes + input_bytes > self.max_input_bytes:
                raise WorkBudgetExceeded("input_bytes")
            if self._output_tokens + output_tokens > self.max_output_tokens:
                raise WorkBudgetExceeded("output_tokens")

            self._attempts += 1
            self._input_bytes += input_bytes
            self._output_tokens += output_tokens

    def snapshot(self) -> dict[str, int]:
        """Return current counters for metrics and deterministic tests."""
        with self._lock:
            return {
                "attempts": self._attempts,
                "input_bytes": self._input_bytes,
                "output_tokens": self._output_tokens,
            }
