"""Tests for TechnicalAnalysisService indicator computation."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.services.technical_analysis import TechnicalAnalysisService


def _make_sample_df(n: int = 120, base_price: float = 100.0) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing.

    Creates a random-walk price series with realistic OHLCV structure.
    """
    rng = np.random.default_rng(seed=42)
    dates = pd.date_range(
        end=datetime.now(tz=timezone.utc),
        periods=n,
        freq="1D",
    )

    close_returns = rng.normal(0.001, 0.02, size=n)
    close = base_price * np.cumprod(1 + close_returns)

    high = close * (1 + rng.uniform(0.001, 0.03, size=n))
    low = close * (1 - rng.uniform(0.001, 0.03, size=n))
    open_ = low + (high - low) * rng.uniform(0.2, 0.8, size=n)
    volume = rng.integers(100_000, 10_000_000, size=n).astype(float)

    df = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )
    return df


@pytest.fixture
def service() -> TechnicalAnalysisService:
    return TechnicalAnalysisService()


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return _make_sample_df()


class TestComputeIndicators:
    def test_sma_computed(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, ["sma"])
        assert "SMA_20" in result
        assert len(result["SMA_20"].values) == len(sample_df)
        # First 19 values should be None (not enough data for SMA_20)
        assert result["SMA_20"].values[0] is None
        # Later values should be populated
        assert result["SMA_20"].values[-1] is not None

    def test_rsi_computed(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, ["rsi"])
        assert "RSI_14" in result
        values = result["RSI_14"].values
        assert len(values) == len(sample_df)
        # RSI should be between 0 and 100 for non-None values
        for v in values:
            if v is not None:
                assert 0 <= v <= 100

    def test_macd_computed(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, ["macd"])
        assert "MACD" in result
        assert "MACD_signal" in result
        assert "MACD_hist" in result
        assert len(result["MACD"].values) == len(sample_df)

    def test_bbands_computed(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, ["bbands"])
        assert "BB_upper" in result
        assert "BB_mid" in result
        assert "BB_lower" in result
        # Upper should be >= mid >= lower for non-None values
        for i in range(len(sample_df)):
            upper = result["BB_upper"].values[i]
            mid = result["BB_mid"].values[i]
            lower = result["BB_lower"].values[i]
            if all(v is not None for v in [upper, mid, lower]):
                assert upper >= mid >= lower

    def test_multiple_indicators(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, ["sma", "rsi", "macd", "bbands"])
        assert len(result) >= 7  # SMA, RSI, MACD*3, BB*3

    def test_empty_indicators_list(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        result = service.compute_indicators(sample_df, [])
        assert len(result) == 0

    def test_unknown_indicator_ignored(
        self, service: TechnicalAnalysisService, sample_df: pd.DataFrame
    ):
        result = service.compute_indicators(sample_df, ["nonexistent"])
        assert len(result) == 0


class TestGenerateSignals:
    def test_signals_returned(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        indicators = service.compute_indicators(sample_df, ["rsi", "macd"])
        signals = service.generate_signals(sample_df, indicators)
        assert len(signals) >= 1
        for sig in signals:
            assert sig.type in ("buy", "sell", "hold")
            assert 0.0 <= sig.strength <= 1.0
            assert sig.reason

    def test_hold_signal_when_no_indicators(
        self, service: TechnicalAnalysisService, sample_df: pd.DataFrame
    ):
        signals = service.generate_signals(sample_df, {})
        assert len(signals) == 1
        assert signals[0].type == "hold"


class TestDetectPatterns:
    def test_returns_empty_list(self, service: TechnicalAnalysisService, sample_df: pd.DataFrame):
        patterns = service.detect_patterns(sample_df)
        assert patterns == []
