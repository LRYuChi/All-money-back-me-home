"""Tests for RetryPolicy + retry_with_backoff helper (round 43)."""
from __future__ import annotations

import random

import pytest

from execution.exchanges import RetryPolicy, retry_with_backoff


# ================================================================== #
# Test exception classes — named to match the policy's retryable list
# ================================================================== #
class NetworkError(Exception): pass
class RequestTimeout(Exception): pass
class DDoSProtection(Exception): pass
class ExchangeNotAvailable(Exception): pass


# Non-retryable
class InvalidOrder(Exception): pass
class AuthenticationError(Exception): pass
class InsufficientFunds(Exception): pass


# ================================================================== #
# RetryPolicy validation
# ================================================================== #
def test_policy_rejects_zero_max_attempts():
    with pytest.raises(ValueError, match="max_attempts must be ≥ 1"):
        RetryPolicy(max_attempts=0)


def test_policy_rejects_negative_base_delay():
    with pytest.raises(ValueError, match="base_delay_sec must be ≥ 0"):
        RetryPolicy(base_delay_sec=-1)


def test_policy_rejects_max_below_base():
    with pytest.raises(ValueError, match="max_delay_sec"):
        RetryPolicy(base_delay_sec=10, max_delay_sec=5)


def test_policy_rejects_multiplier_below_one():
    with pytest.raises(ValueError, match="multiplier must be ≥ 1.0"):
        RetryPolicy(multiplier=0.5)


def test_policy_rejects_jitter_outside_unit_interval():
    with pytest.raises(ValueError, match="jitter_pct"):
        RetryPolicy(jitter_pct=1.5)
    with pytest.raises(ValueError, match="jitter_pct"):
        RetryPolicy(jitter_pct=-0.1)


# ================================================================== #
# should_retry classification
# ================================================================== #
def test_should_retry_includes_default_network_classes():
    p = RetryPolicy()
    assert p.should_retry(NetworkError("x"))
    assert p.should_retry(RequestTimeout("x"))
    assert p.should_retry(DDoSProtection("x"))
    assert p.should_retry(ExchangeNotAvailable("x"))
    assert p.should_retry(ConnectionError("x"))
    assert p.should_retry(TimeoutError("x"))


def test_should_not_retry_non_retryable_classes():
    p = RetryPolicy()
    assert not p.should_retry(InvalidOrder("x"))
    assert not p.should_retry(AuthenticationError("x"))
    assert not p.should_retry(InsufficientFunds("x"))
    assert not p.should_retry(ValueError("x"))


def test_should_retry_custom_set_overrides_default():
    p = RetryPolicy(retryable_exception_names=frozenset({"InvalidOrder"}))
    assert p.should_retry(InvalidOrder("x"))
    assert not p.should_retry(NetworkError("x"))


# ================================================================== #
# delay_for backoff curve
# ================================================================== #
def test_delay_zero_jitter_doubles_each_attempt():
    p = RetryPolicy(base_delay_sec=1.0, max_delay_sec=100.0,
                    multiplier=2.0, jitter_pct=0)
    assert p.delay_for(0) == 1.0
    assert p.delay_for(1) == 2.0
    assert p.delay_for(2) == 4.0
    assert p.delay_for(3) == 8.0


def test_delay_caps_at_max_delay():
    p = RetryPolicy(base_delay_sec=1.0, max_delay_sec=5.0,
                    multiplier=2.0, jitter_pct=0)
    # 1, 2, 4, 5 (capped from 8), 5, 5...
    assert p.delay_for(0) == 1.0
    assert p.delay_for(1) == 2.0
    assert p.delay_for(2) == 4.0
    assert p.delay_for(3) == 5.0   # would be 8, capped
    assert p.delay_for(10) == 5.0


def test_delay_jitter_within_bounds():
    p = RetryPolicy(base_delay_sec=1.0, max_delay_sec=100.0,
                    multiplier=1.0, jitter_pct=0.20)
    rng = random.Random(42)
    for _ in range(50):
        d = p.delay_for(0, rng=rng)
        # ±20% of 1.0
        assert 0.8 <= d <= 1.2


def test_delay_jitter_never_negative():
    """Even with extreme jitter, delay must not go below 0."""
    p = RetryPolicy(base_delay_sec=0, max_delay_sec=10.0,
                    multiplier=2.0, jitter_pct=1.0)
    rng = random.Random(7)
    for _ in range(20):
        assert p.delay_for(0, rng=rng) >= 0


def test_delay_negative_attempt_returns_zero():
    p = RetryPolicy()
    assert p.delay_for(-1) == 0.0


# ================================================================== #
# retry_with_backoff — happy path
# ================================================================== #
def test_succeeds_on_first_attempt_no_sleep():
    sleep_calls = []
    def my_fn(): return "ok"
    wrapped = retry_with_backoff(
        my_fn, policy=RetryPolicy(),
        sleep=sleep_calls.append,
    )
    assert wrapped() == "ok"
    assert sleep_calls == []


def test_passes_args_and_kwargs():
    def add(a, b, *, c): return a + b + c
    wrapped = retry_with_backoff(
        add, policy=RetryPolicy(),
        sleep=lambda _: None,
    )
    assert wrapped(1, 2, c=3) == 6


# ================================================================== #
# retry_with_backoff — retry then succeed
# ================================================================== #
def test_retries_then_succeeds():
    """First two attempts raise NetworkError; third succeeds."""
    n = {"calls": 0}
    def flaky():
        n["calls"] += 1
        if n["calls"] < 3:
            raise NetworkError("blip")
        return "got it"

    sleep_calls = []
    wrapped = retry_with_backoff(
        flaky,
        policy=RetryPolicy(max_attempts=3, base_delay_sec=0, jitter_pct=0),
        sleep=sleep_calls.append,
    )
    assert wrapped() == "got it"
    assert n["calls"] == 3
    # Two sleeps between three attempts
    assert len(sleep_calls) == 2


# ================================================================== #
# retry_with_backoff — exhaust max_attempts
# ================================================================== #
def test_raises_after_max_attempts():
    n = {"calls": 0}
    def always_fails():
        n["calls"] += 1
        raise NetworkError("never works")

    sleep_calls = []
    wrapped = retry_with_backoff(
        always_fails,
        policy=RetryPolicy(max_attempts=3, base_delay_sec=0),
        sleep=sleep_calls.append,
    )
    with pytest.raises(NetworkError, match="never works"):
        wrapped()
    assert n["calls"] == 3
    # Two sleeps (between 3 attempts; no sleep after the final failure)
    assert len(sleep_calls) == 2


def test_max_attempts_one_means_no_retry():
    n = {"calls": 0}
    def always_fails():
        n["calls"] += 1
        raise NetworkError("nope")
    wrapped = retry_with_backoff(
        always_fails,
        policy=RetryPolicy(max_attempts=1),
        sleep=lambda _: None,
    )
    with pytest.raises(NetworkError):
        wrapped()
    assert n["calls"] == 1


# ================================================================== #
# retry_with_backoff — non-retryable propagates immediately
# ================================================================== #
def test_non_retryable_propagates_immediately():
    n = {"calls": 0}
    def boom():
        n["calls"] += 1
        raise InvalidOrder("you can't trade that")

    sleep_calls = []
    wrapped = retry_with_backoff(
        boom,
        policy=RetryPolicy(max_attempts=5, base_delay_sec=0),
        sleep=sleep_calls.append,
    )
    with pytest.raises(InvalidOrder):
        wrapped()
    assert n["calls"] == 1   # no retry
    assert sleep_calls == []   # no sleep


def test_authentication_error_not_retried():
    """Auth errors won't fix themselves — bail immediately."""
    n = {"calls": 0}
    def auth_fail():
        n["calls"] += 1
        raise AuthenticationError("bad key")
    wrapped = retry_with_backoff(
        auth_fail, policy=RetryPolicy(),
        sleep=lambda _: None,
    )
    with pytest.raises(AuthenticationError):
        wrapped()
    assert n["calls"] == 1


# ================================================================== #
# retry_with_backoff — backoff timing
# ================================================================== #
def test_sleep_durations_follow_backoff_curve():
    n = {"calls": 0}
    def always_fails():
        n["calls"] += 1
        raise NetworkError("nope")

    sleep_calls = []
    wrapped = retry_with_backoff(
        always_fails,
        policy=RetryPolicy(
            max_attempts=4, base_delay_sec=0.1, max_delay_sec=10,
            multiplier=2.0, jitter_pct=0,
        ),
        sleep=sleep_calls.append,
    )
    with pytest.raises(NetworkError):
        wrapped()
    # 4 attempts → 3 sleeps: 0.1, 0.2, 0.4
    assert sleep_calls == pytest.approx([0.1, 0.2, 0.4])


def test_sleep_durations_capped_at_max():
    n = {"calls": 0}
    def always_fails():
        n["calls"] += 1
        raise NetworkError("nope")

    sleep_calls = []
    wrapped = retry_with_backoff(
        always_fails,
        policy=RetryPolicy(
            max_attempts=5, base_delay_sec=1.0, max_delay_sec=2.0,
            multiplier=2.0, jitter_pct=0,
        ),
        sleep=sleep_calls.append,
    )
    with pytest.raises(NetworkError):
        wrapped()
    # 5 attempts → 4 sleeps: 1, 2, 2 (capped from 4), 2 (capped from 8)
    assert sleep_calls == pytest.approx([1.0, 2.0, 2.0, 2.0])


# ================================================================== #
# Mixed errors: retryable then non-retryable should bail on second
# ================================================================== #
def test_switches_to_non_retryable_mid_loop_propagates():
    """Attempt 1 raises NetworkError (retryable); attempt 2 raises
    InvalidOrder (non-retryable) — loop must propagate InvalidOrder
    immediately rather than retrying it."""
    seq: list[type] = [NetworkError, InvalidOrder]
    n = {"calls": 0}
    def shifting():
        cls = seq[n["calls"]]
        n["calls"] += 1
        raise cls("x")

    wrapped = retry_with_backoff(
        shifting,
        policy=RetryPolicy(max_attempts=5, base_delay_sec=0),
        sleep=lambda _: None,
    )
    with pytest.raises(InvalidOrder):
        wrapped()
    assert n["calls"] == 2   # one retry then bail


# ================================================================== #
# Logging side effects don't crash retry
# ================================================================== #
# ================================================================== #
# Integration: CcxtOKXClient honors retry_policy
# ================================================================== #
def test_ccxt_client_retries_place_order_on_network_error():
    """Verify the retry wrapper is actually wired into CcxtOKXClient.place_order
    by exercising the path with a stub that flips after N tries."""
    from execution.exchanges.okx import CcxtOKXClient
    from execution.exchanges.types import ExchangeRequest, ExchangeResponseStatus

    class FlakyStub:
        def __init__(self):
            self.calls = 0
        def fetch_ticker(self, _): return {"last": 50_000}
        def amount_to_precision(self, _s, a): return f"{a:.6f}"
        def create_order(self, **kw):
            self.calls += 1
            if self.calls < 3:
                raise NetworkError("flaky network")
            return {"id": "OK", "status": "open", "filled": 0, "cost": 0}
        def cancel_order(self, **kw): return {}
        def load_markets(self): return {}

    stub = FlakyStub()
    client = CcxtOKXClient(
        api_key="a", secret="b", passphrase="c", client=stub,
        retry_policy=RetryPolicy(max_attempts=5, base_delay_sec=0, jitter_pct=0),
    )
    req = ExchangeRequest(
        client_order_id="rd43-test",
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long", notional_usd=100,
    )
    r = client.place_order(req)
    assert r.status == ExchangeResponseStatus.ACCEPTED
    assert stub.calls == 3   # 2 failures + 1 success


def test_ccxt_client_no_retry_on_invalid_order():
    """Non-retryable exception bails immediately, no retry latency."""
    from execution.exchanges.okx import CcxtOKXClient
    from execution.exchanges.types import ExchangeRequest, ExchangeResponseStatus

    class InvalidStub:
        calls = 0
        def fetch_ticker(self, _): return {"last": 50_000}
        def amount_to_precision(self, _s, a): return f"{a:.6f}"
        def create_order(self, **kw):
            self.calls += 1
            raise InvalidOrder("size out of range")
        def cancel_order(self, **kw): return {}
        def load_markets(self): return {}

    stub = InvalidStub()
    client = CcxtOKXClient(
        api_key="a", secret="b", passphrase="c", client=stub,
        # 10s max with 10s base is fine — we'll never sleep since
        # InvalidOrder is non-retryable. The huge delay here serves as a
        # canary: if retry kicks in incorrectly, the test will hang.
        retry_policy=RetryPolicy(
            max_attempts=5, base_delay_sec=10, max_delay_sec=10,
        ),
    )
    req = ExchangeRequest(
        client_order_id="rd43-bad", symbol="crypto:OKX:BTC/USDT:USDT",
        side="long", notional_usd=100,
    )
    r = client.place_order(req)
    assert r.status == ExchangeResponseStatus.REJECTED
    assert stub.calls == 1   # no retry — InvalidOrder is non-retryable


def test_retry_with_real_sleep_doesnt_block_long(caplog):
    """Smoke: with small base delay, real time.sleep doesn't add much."""
    import time
    import logging

    n = {"calls": 0}
    def flaky():
        n["calls"] += 1
        if n["calls"] < 2:
            raise NetworkError("blip")
        return "ok"

    wrapped = retry_with_backoff(
        flaky,
        policy=RetryPolicy(
            max_attempts=3, base_delay_sec=0.001, max_delay_sec=0.01,
            jitter_pct=0,
        ),
    )
    t0 = time.monotonic()
    with caplog.at_level(logging.INFO):
        result = wrapped()
    elapsed = time.monotonic() - t0
    assert result == "ok"
    assert elapsed < 0.5   # well under any human-perceptible threshold
    assert any("retrying" in m for m in caplog.messages)
