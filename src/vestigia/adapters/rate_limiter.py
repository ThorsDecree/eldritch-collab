from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float = 0.0


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._hits: dict[str, deque[float]] = {}

    def check_and_record(self, key: str) -> RateLimitResult:
        now = monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return RateLimitResult(
                allowed=False,
                retry_after_seconds=max(0.0, self.window_seconds - (now - hits[0])),
            )
        hits.append(now)
        return RateLimitResult(allowed=True)

