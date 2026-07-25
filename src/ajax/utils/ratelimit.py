"""Token-bucket throttling and retry-with-backoff for external data providers.

Yahoo rate-limits aggressively and yfinance surfaces this as YFRateLimitError.
Rather than silently returning a partial universe (which would quietly corrupt
the cross-sectional ranking), repeated failure is escalated to the caller.
"""

from __future__ import annotations

import functools
import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimitExceeded(RuntimeError):
    """Raised when a provider kept refusing after every retry was exhausted."""


class TokenBucket:
    """Simple thread-safe token bucket."""

    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                time.sleep(deficit / self.rate)


# Alpaca's documented limit is 200 requests/minute on both the trading API and
# the Basic market-data plan. We sit deliberately under it.
ALPACA_BUCKET = TokenBucket(rate_per_sec=3.0, capacity=20)

# Yahoo publishes no limit; this is conservative by construction.
YAHOO_BUCKET = TokenBucket(rate_per_sec=0.7, capacity=5)


def _is_rate_limit_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if "RateLimit" in name or "TooManyRequests" in name:
        return True
    text = str(exc).lower()
    return "too many requests" in text or "rate limit" in text or "429" in text


def throttled(
    bucket: TokenBucket,
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    jitter: float = 0.5,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Throttle a callable and retry rate-limit failures with exponential backoff.

    Non-rate-limit exceptions propagate immediately — only throttling is retried,
    because retrying a genuine bug just wastes time.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last: BaseException | None = None
            for attempt in range(attempts):
                bucket.acquire()
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - re-raised below
                    if not _is_rate_limit_error(exc):
                        raise
                    last = exc
                    if attempt == attempts - 1:
                        break
                    delay = base_delay * (2**attempt) + random.uniform(0, jitter)
                    log.warning(
                        "rate limited by provider (attempt %d/%d), sleeping %.1fs: %s",
                        attempt + 1,
                        attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
            raise RateLimitExceeded(
                f"{fn.__name__} exhausted {attempts} attempts against provider rate limits"
            ) from last

        return wrapper

    return decorator
