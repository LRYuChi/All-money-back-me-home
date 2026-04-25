"""Bounded exponential-backoff retry for idempotent exchange ops (round 43).

Trading reliability: OKX (and any exchange) occasionally returns
NetworkError / RequestTimeout / DDoSProtection — usually transient. Without
retry, every blip becomes a REJECTED order; the strategy then fires again
on the next tick, often racing with the still-pending exchange retry.

Safety constraints:
  - ONLY wrap idempotent calls. place_order is idempotent because OKX dedupes
    by clOrdId (a re-submission of the same coid returns DUPLICATE, mapped to
    SUBMITTED — no double-fill). fetch_order and cancel_order are intrinsically
    idempotent.
  - Don't retry on REJECTED / DUPLICATE / "InvalidOrder" / "InsufficientFunds"
    — those are decisive; retrying just spams the exchange.

Default policy (conservative):
  - max_attempts = 3 (initial + 2 retries)
  - base_delay  = 0.5s
  - max_delay   = 5.0s
  - multiplier  = 2.0
  - jitter      = ±20% to avoid thundering herd

Sync only: the calls we wrap are blocking ccxt invocations. The dispatcher's
async caller naturally yields between order processing.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ccxt exception class names that are network-class transient.
# Matched by class name (str) so we don't import ccxt at module load —
# tests can monkey-patch by raising plain exceptions with these names.
_RETRYABLE_NAMES: frozenset[str] = frozenset({
    "NetworkError",
    "RequestTimeout",
    "DDoSProtection",
    "ExchangeNotAvailable",
    "ExchangeError",       # ccxt's parent for some flaky errors
    # Built-in transports
    "ConnectionError",
    "TimeoutError",
})


@dataclass(slots=True, frozen=True)
class RetryPolicy:
    """Bounded exponential-backoff. Frozen so it's safe to share across
    callers. The default constructor matches the conservative production
    setting from the module docstring."""

    max_attempts: int = 3
    base_delay_sec: float = 0.5
    max_delay_sec: float = 5.0
    multiplier: float = 2.0
    jitter_pct: float = 0.20            # ±20% per attempt
    retryable_exception_names: frozenset[str] = _RETRYABLE_NAMES

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError(
                f"max_attempts must be ≥ 1; got {self.max_attempts}"
            )
        if self.base_delay_sec < 0:
            raise ValueError(
                f"base_delay_sec must be ≥ 0; got {self.base_delay_sec}"
            )
        if self.max_delay_sec < self.base_delay_sec:
            raise ValueError(
                f"max_delay_sec ({self.max_delay_sec}) must be ≥ "
                f"base_delay_sec ({self.base_delay_sec})"
            )
        if self.multiplier < 1.0:
            raise ValueError(
                f"multiplier must be ≥ 1.0; got {self.multiplier}"
            )
        if not (0.0 <= self.jitter_pct <= 1.0):
            raise ValueError(
                f"jitter_pct must be in [0,1]; got {self.jitter_pct}"
            )

    def should_retry(self, exc: BaseException) -> bool:
        return type(exc).__name__ in self.retryable_exception_names

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Delay BEFORE attempt N+1 (i.e. delay between attempt N and N+1).

        attempt is 0-indexed: delay_for(0) is the wait between attempts 1 and 2.
        """
        if attempt < 0:
            return 0.0
        raw = self.base_delay_sec * (self.multiplier ** attempt)
        capped = min(raw, self.max_delay_sec)
        if self.jitter_pct == 0:
            return capped
        rng = rng or random
        # Symmetric jitter: capped * (1 ± jitter_pct)
        spread = capped * self.jitter_pct
        return max(0.0, capped + rng.uniform(-spread, spread))


def retry_with_backoff(
    fn: Callable[..., T],
    *,
    policy: RetryPolicy,
    op_name: str = "exchange_op",
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> Callable[..., T]:
    """Wrap `fn` with exponential-backoff retry on policy-matched exceptions.

    Returns a callable with the same signature. Re-raises the last
    exception when max_attempts is exhausted. Non-retryable exceptions
    propagate immediately on the first attempt — no extra latency for
    decisive errors.

    `sleep` and `rng` injectable for tests so they can run sub-millisecond.
    """

    def _wrapped(*args, **kwargs) -> T:
        last_exc: BaseException | None = None
        for attempt in range(policy.max_attempts):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                if not policy.should_retry(e):
                    logger.debug(
                        "%s: non-retryable %s — propagating without retry",
                        op_name, type(e).__name__,
                    )
                    raise
                if attempt + 1 >= policy.max_attempts:
                    logger.warning(
                        "%s: exhausted %d attempts; last error %s: %s",
                        op_name, policy.max_attempts, type(e).__name__, e,
                    )
                    raise
                delay = policy.delay_for(attempt, rng=rng)
                logger.info(
                    "%s: attempt %d/%d failed (%s: %s); retrying in %.2fs",
                    op_name, attempt + 1, policy.max_attempts,
                    type(e).__name__, e, delay,
                )
                # Always call sleep — caller may rely on the call count for
                # observability (counting retry attempts in tests). sleep(0)
                # is a cheap no-op in stdlib but injectable callers can use
                # it as a signal.
                sleep(delay)
        # Theoretically unreachable: loop either returns or raises
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{op_name}: retry loop exited without result")

    return _wrapped


__all__ = ["RetryPolicy", "retry_with_backoff"]
