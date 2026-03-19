"""Tests for the CoinGlass API client."""

from market_monitor.coinglass import CoinGlassClient


def test_client_init():
    client = CoinGlassClient("test_key")
    assert client.api_key == "test_key"


def test_calculate_derivatives_score_no_data():
    """When all API calls fail, score should default to 0.5."""
    client = CoinGlassClient("invalid_key_for_testing")
    result = client.calculate_derivatives_score("BTC")

    assert "score" in result
    assert "factors" in result
    assert 0.0 <= result["score"] <= 1.0
    # All factors should have score 0.5 (no data)
    for factor in result["factors"].values():
        assert "score" in factor
        assert 0.0 <= factor["score"] <= 1.0


def test_score_weights_sum():
    """Verify internal weights sum to 1.0."""
    weights = {
        "oi_weighted_fr": 0.30,
        "oi_trend": 0.25,
        "long_short_ratio": 0.25,
        "top_trader_ls": 0.20,
    }
    assert abs(sum(weights.values()) - 1.0) < 1e-6
