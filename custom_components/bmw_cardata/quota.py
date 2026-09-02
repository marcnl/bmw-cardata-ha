"""Rolling 24h REST request quota tracker for BMW CarData (50 requests / day)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable

from .const import QUOTA_LIMIT, QUOTA_SAFETY_MARGIN, QUOTA_WINDOW_SECONDS


class CardataQuotaError(Exception):
    """Raised when a REST call would exceed the BMW CarData daily quota."""


class QuotaTracker:
    """Track REST request timestamps over a rolling window.

    BMW rejects the 51st request in any 24h window with ``CU-429``. We stop a
    few requests short of that so an unexpected extra call (reauth, a manual
    service) does not tip us over.
    """

    def __init__(self, timestamps: Iterable[float] | None = None) -> None:
        self._events: deque[float] = deque(sorted(timestamps or []))

    def _prune(self, now: float) -> None:
        cutoff = now - QUOTA_WINDOW_SECONDS
        while self._events and self._events[0] < cutoff:
            self._events.popleft()

    @property
    def used(self) -> int:
        self._prune(time.time())
        return len(self._events)

    @property
    def remaining(self) -> int:
        return max(0, QUOTA_LIMIT - QUOTA_SAFETY_MARGIN - self.used)

    @property
    def next_reset(self) -> float | None:
        """Epoch time at which the oldest counted request falls out of the window."""
        self._prune(time.time())
        if not self._events:
            return None
        return self._events[0] + QUOTA_WINDOW_SECONDS

    def can_spend(self, count: int = 1) -> bool:
        return self.remaining >= count

    def spend(self, count: int = 1) -> None:
        """Record ``count`` requests, raising if that would break the budget."""
        now = time.time()
        self._prune(now)
        if QUOTA_LIMIT - QUOTA_SAFETY_MARGIN - len(self._events) < count:
            raise CardataQuotaError(
                f"BMW CarData daily quota exhausted ({len(self._events)} used)"
            )
        for _ in range(count):
            self._events.append(now)

    def dump(self) -> list[float]:
        self._prune(time.time())
        return list(self._events)
