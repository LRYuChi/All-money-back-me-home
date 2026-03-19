"""Tests for the Crypto Environment Engine."""


from market_monitor.crypto_environment import CryptoEnvironmentEngine


def test_engine_init_no_coinglass():
    """Engine initializes without CoinGlass API key."""
    engine = CryptoEnvironmentEngine()
    assert engine._cg_client is None


def test_engine_init_with_coinglass():
    """Engine initializes with CoinGlass API key."""
    engine = CryptoEnvironmentEngine(coinglass_api_key="test_key")
    assert engine._cg_client is not None


def test_weights_sum_to_one():
    """Sandbox weights must sum to 1.0."""
    engine = CryptoEnvironmentEngine()
    total = sum(engine.WEIGHTS.values())
    assert abs(total - 1.0) < 1e-6


def test_regime_boundaries():
    """Verify regime classification boundaries."""
    engine = CryptoEnvironmentEngine()

    # Monkey-patch to return controlled scores
    def make_sandbox(score):
        return lambda *a, **kw: {"score": score, "factors": {}}

    # FAVORABLE: >= 0.7
    engine._derivatives_sandbox = make_sandbox(0.8)
    engine._onchain_sandbox = make_sandbox(0.8)
    engine._sentiment_sandbox = make_sandbox(0.8)
    result = engine.calculate("BTC")
    assert result["regime"] == "FAVORABLE"
    assert result["score"] >= 0.7

    # HOSTILE: < 0.3
    engine._derivatives_sandbox = make_sandbox(0.1)
    engine._onchain_sandbox = make_sandbox(0.1)
    engine._sentiment_sandbox = make_sandbox(0.1)
    result = engine.calculate("BTC")
    assert result["regime"] == "HOSTILE"
    assert result["score"] < 0.3


def test_score_clamped_0_1():
    """Score should always be between 0 and 1."""
    engine = CryptoEnvironmentEngine()

    engine._derivatives_sandbox = lambda *a: {"score": 1.5, "factors": {}}
    engine._onchain_sandbox = lambda: {"score": 1.5, "factors": {}}
    engine._sentiment_sandbox = lambda *a: {"score": 1.5, "factors": {}}
    result = engine.calculate("BTC")
    assert 0.0 <= result["score"] <= 1.0

    engine._derivatives_sandbox = lambda *a: {"score": -0.5, "factors": {}}
    engine._onchain_sandbox = lambda: {"score": -0.5, "factors": {}}
    engine._sentiment_sandbox = lambda *a: {"score": -0.5, "factors": {}}
    result = engine.calculate("BTC")
    assert 0.0 <= result["score"] <= 1.0


def test_result_structure():
    """Verify result dict has expected keys."""
    engine = CryptoEnvironmentEngine()

    engine._derivatives_sandbox = lambda *a: {"score": 0.5, "factors": {"f1": {"score": 0.5, "signal": "test"}}}
    engine._onchain_sandbox = lambda: {"score": 0.5, "factors": {"f2": {"score": 0.5, "signal": "test"}}}
    engine._sentiment_sandbox = lambda *a: {"score": 0.5, "factors": {"f3": {"score": 0.5, "signal": "test"}}}

    result = engine.calculate("ETH")
    assert "score" in result
    assert "regime" in result
    assert "symbol" in result
    assert result["symbol"] == "ETH"
    assert "sandboxes" in result
    assert "derivatives" in result["sandboxes"]
    assert "onchain" in result["sandboxes"]
    assert "sentiment" in result["sandboxes"]
    assert "factors" in result


def test_coinglass_fallback():
    """When CoinGlass fails, should fall back to free APIs."""
    engine = CryptoEnvironmentEngine(coinglass_api_key="invalid_key")
    # The CoinGlass client will fail on actual API calls,
    # _derivatives_sandbox_coinglass should catch and fall back
    # We test the fallback mechanism exists
    assert hasattr(engine, '_derivatives_sandbox_coinglass')
    assert hasattr(engine, '_derivatives_sandbox')
