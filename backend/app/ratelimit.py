"""Minimal per-IP token-bucket rate limiter.

Protects the paid model endpoints from cost-abuse / DoS by an open internet
client (the same failure mode that let a botnet hammer a prior public endpoint).

In-memory and per-process: with multiple gunicorn workers the effective limit is
(rate x workers), and buckets reset on restart. That is acceptable for a
prototype; production should move this to a shared store (Redis) or, better, put
the app behind Azure AD / API Management. See SECURITY.md.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


class DailyCircuitBreaker:
    """Global daily cap on model usage (a cost circuit breaker). Independent of
    per-IP limits, this bounds total spend even under a distributed scan of the
    public URL. Counts 'units' (one per bottle/panel verified) and resets at UTC
    midnight. Set high enough that legitimate evaluation never trips it.

    In-memory and per-process: with N gunicorn workers the effective cap is N x
    the configured value, fine as a backstop. Production would use a shared
    counter; see SECURITY.md."""

    def __init__(self, daily_cap: int):
        self.cap = daily_cap
        self._day = None
        self._count = 0

    def allow(self, units: int = 1) -> bool:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self._count = today, 0
        if self._count + units > self.cap:
            return False
        self._count += units
        return True


class RateLimiter:
    def __init__(self, rate_per_min: int):
        self.capacity = float(rate_per_min)
        self.refill_per_sec = rate_per_min / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True


def client_ip(request) -> str:
    """Best-effort client IP for rate-limit bucketing.

    App Service sits behind a proxy and sets X-Forwarded-For as "<ip>:<port>"
    (and may chain multiple hops). The ephemeral source port differs on every
    connection, so we MUST strip it, otherwise each request lands in its own
    bucket and the limiter never engages. We take the first hop and drop a
    trailing ":port" (IPv4); IPv6 (multiple colons) is left intact."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first.count(":") == 1:  # ip:port -> ip ; leave bare IPv6 alone
            first = first.rsplit(":", 1)[0]
        return first
    return request.client.host if request.client else "unknown"
