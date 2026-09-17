"""Per-node token-bucket rate limiting for the event-ingestion endpoint.
Protects the server from a misbehaving or compromised agent hammering
/api/v1/events - normal agents post one batch every BATCH_INTERVAL_SECONDS
(default 5s), so a generous burst capacity with a modest steady refill
rate never affects legitimate traffic.

One bucket per node_id, kept in a plain dict for the life of the process.
This can't leak unbounded: node_id only exists for nodes an admin
explicitly created (auth-gated), so the dict is bounded by the real node
count, not by anything an attacker controls.
"""
from __future__ import annotations

import time


class _TokenBucket:
    __slots__ = ("capacity", "refill_per_second", "tokens", "last")

    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.last = time.monotonic()

    def allow(self, cost: float = 1.0) -> tuple[bool, float]:
        now = time.monotonic()
        elapsed = now - self.last
        self.last = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        if self.tokens >= cost:
            self.tokens -= cost
            return True, 0.0
        deficit = cost - self.tokens
        retry_after = deficit / self.refill_per_second
        return False, retry_after


class NodeRateLimiter:
    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: dict[int, _TokenBucket] = {}

    def check(self, node_id: int) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        bucket = self._buckets.get(node_id)
        if bucket is None:
            bucket = _TokenBucket(self.capacity, self.refill_per_second)
            self._buckets[node_id] = bucket
        return bucket.allow()
