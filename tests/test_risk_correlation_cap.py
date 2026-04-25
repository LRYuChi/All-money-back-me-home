"""Tests for CorrelationMatrix + G7 CorrelationCapGuard (round 29)."""
from __future__ import annotations

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    CorrelationCapGuard,
    GuardContext,
    GuardPipeline,
    GuardResult,
    InMemoryCorrelationMatrix,
    InMemoryExposureProvider,
    NoOpCorrelationMatrix,
    YamlCorrelationMatrix,
    build_correlation_matrix,
    make_context_provider,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    symbol: str = "crypto:OKX:BTC/USDT:USDT",
    notional: float = 500.0,
) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1",
        symbol=symbol,
        side="long",
        target_notional_usd=notional,
        mode="shadow",
    )


# ================================================================== #
# CorrelationMatrix backends
# ================================================================== #
def test_noop_matrix_returns_zero():
    m = NoOpCorrelationMatrix()
    assert m.get("a", "b") == 0.0
    assert m.get("a", "a") == 0.0
    assert m.known_pairs() == 0


def test_inmemory_default_self_one():
    m = InMemoryCorrelationMatrix()
    assert m.get("BTC", "BTC") == 1.0


def test_inmemory_default_self_overrideable():
    m = InMemoryCorrelationMatrix(default_self=0.5)
    assert m.get("BTC", "BTC") == 0.5


def test_inmemory_default_missing_zero():
    m = InMemoryCorrelationMatrix()
    assert m.get("BTC", "ETH") == 0.0


def test_inmemory_returns_seeded_pair():
    m = InMemoryCorrelationMatrix([("BTC", "ETH", 0.85)])
    assert m.get("BTC", "ETH") == 0.85


def test_inmemory_symmetric_lookup():
    """get(a,b) == get(b,a) regardless of insertion order."""
    m = InMemoryCorrelationMatrix([("BTC", "ETH", 0.85)])
    assert m.get("ETH", "BTC") == 0.85


def test_inmemory_clamps_rho_to_unit_interval():
    m = InMemoryCorrelationMatrix([
        ("A", "B", 1.5),
        ("C", "D", -2.0),
    ])
    assert m.get("A", "B") == 1.0
    assert m.get("C", "D") == -1.0


def test_inmemory_known_pairs_count():
    m = InMemoryCorrelationMatrix([
        ("A", "B", 0.5),
        ("C", "D", 0.6),
    ])
    assert m.known_pairs() == 2


def test_inmemory_self_pair_seedable():
    """Caller can override default self-correlation per symbol."""
    m = InMemoryCorrelationMatrix([("X", "X", 0.5)])
    assert m.get("X", "X") == 0.5


# ================================================================== #
# YAML loader
# ================================================================== #
def test_yaml_loads_pairs(tmp_path):
    f = tmp_path / "matrix.yaml"
    f.write_text(
        "defaults:\n"
        "  self: 1.0\n"
        "  missing: 0.0\n"
        "pairs:\n"
        "  - [BTC, ETH, 0.82]\n"
        "  - [BTC, SOL, 0.75]\n"
    )
    m = YamlCorrelationMatrix.from_path(f)
    assert m.known_pairs() == 2
    assert m.get("BTC", "ETH") == 0.82
    assert m.get("BTC", "SOL") == 0.75


def test_yaml_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        YamlCorrelationMatrix.from_path(tmp_path / "nope.yaml")


def test_yaml_malformed_pairs_raise(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text(
        "pairs:\n"
        "  - [BTC, ETH]\n"   # missing rho
    )
    with pytest.raises(ValueError, match="must be"):
        YamlCorrelationMatrix.from_path(f)


def test_yaml_top_level_must_be_mapping(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        YamlCorrelationMatrix.from_path(f)


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_returns_noop_when_no_path():
    class S:
        correlation_matrix_path = ""
    assert isinstance(build_correlation_matrix(S()), NoOpCorrelationMatrix)


def test_factory_returns_yaml_when_path_set(tmp_path):
    f = tmp_path / "m.yaml"
    f.write_text("pairs: []\n")

    class S:
        correlation_matrix_path = str(f)
    m = build_correlation_matrix(S())
    assert isinstance(m, YamlCorrelationMatrix)


def test_factory_falls_back_to_noop_on_missing_file(tmp_path):
    class S:
        correlation_matrix_path = str(tmp_path / "missing.yaml")
    m = build_correlation_matrix(S())
    assert isinstance(m, NoOpCorrelationMatrix)


# ================================================================== #
# CorrelationCapGuard — construction
# ================================================================== #
def test_g7_construction_requires_matrix():
    with pytest.raises(ValueError, match="matrix"):
        CorrelationCapGuard()


def test_g7_construction_rejects_invalid_threshold():
    m = NoOpCorrelationMatrix()
    with pytest.raises(ValueError, match="correlation_threshold"):
        CorrelationCapGuard(matrix=m, correlation_threshold=1.5)


# ================================================================== #
# CorrelationCapGuard — behavior
# ================================================================== #
def test_g7_allows_when_no_open_positions():
    m = InMemoryCorrelationMatrix()
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g7_allows_when_no_correlated_open_positions():
    """Open BTC + open uncorrelated GOLD; new ETH → no cluster."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:ETH/USDT:USDT", "us:NYSE:GLD", 0.05),  # uncorrelated
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4,
                            correlation_threshold=0.7)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"us:NYSE:GLD": 3000.0},
    )
    d = g.check(make_order(symbol="crypto:OKX:ETH/USDT:USDT"), ctx)
    assert d.result == GuardResult.ALLOW


def test_g7_denies_when_cluster_already_at_cap():
    """Open BTC + ETH both at 2k each = 4k; cap = 4k of 10k. Adding more BTC denies."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", 0.85),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={
            "crypto:OKX:BTC/USDT:USDT": 2000.0,
            "crypto:OKX:ETH/USDT:USDT": 2000.0,
        },
    )
    d = g.check(make_order(symbol="crypto:OKX:BTC/USDT:USDT"), ctx)
    assert d.result == GuardResult.DENY
    assert "correlation cluster" in d.reason


def test_g7_scales_when_cluster_partially_full():
    """Cluster 2k of 4k cap. New 1500 request → scaled to 2000 room."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", 0.85),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:ETH/USDT:USDT": 2000.0},
    )
    d = g.check(
        make_order(symbol="crypto:OKX:BTC/USDT:USDT", notional=2500),
        ctx,
    )
    assert d.result == GuardResult.SCALE
    assert d.scaled_size_usd == 2000.0


def test_g7_denies_when_scaled_below_floor():
    """Cluster 3.95k of 4k cap → only $50 room. 10% floor of $1000 = $100. DENY."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", 0.85),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4, deny_floor_pct=0.10)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:ETH/USDT:USDT": 3950.0},
    )
    d = g.check(
        make_order(symbol="crypto:OKX:BTC/USDT:USDT", notional=1000),
        ctx,
    )
    assert d.result == GuardResult.DENY
    assert "below" in d.reason


def test_g7_self_correlation_counts_existing_position():
    """Adding more BTC counts existing BTC in the cluster."""
    m = InMemoryCorrelationMatrix()  # empty — relies on default_self=1.0
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:BTC/USDT:USDT": 3500.0},
    )
    d = g.check(
        make_order(symbol="crypto:OKX:BTC/USDT:USDT", notional=1000),
        ctx,
    )
    # 3500 + 1000 = 4500 > 4000 cap → SCALE to 500
    assert d.result == GuardResult.SCALE
    assert d.scaled_size_usd == 500.0


def test_g7_negative_correlation_below_threshold_skipped():
    """ρ = -0.6, threshold 0.7 → not counted in cluster."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", -0.6),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4,
                            correlation_threshold=0.7)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:ETH/USDT:USDT": 5000.0},
    )
    d = g.check(make_order(symbol="crypto:OKX:BTC/USDT:USDT"), ctx)
    assert d.result == GuardResult.ALLOW


def test_g7_strong_negative_correlation_counts():
    """ρ = -0.85 → |ρ| ≥ 0.7 → counts as correlated cluster (e.g. inverse ETF)."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "us:NYSE:SQQQ", -0.85),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"us:NYSE:SQQQ": 3500.0},
    )
    d = g.check(
        make_order(symbol="crypto:OKX:BTC/USDT:USDT", notional=1000),
        ctx,
    )
    assert d.result == GuardResult.SCALE


def test_g7_matrix_failure_fails_open():
    class BadMatrix:
        def get(self, a, b):
            raise ConnectionError("matrix lookup down")
        def known_pairs(self): return 0
    g = CorrelationCapGuard(matrix=BadMatrix(), cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:ETH/USDT:USDT": 3000.0},
    )
    d = g.check(make_order(), ctx)
    assert d.result == GuardResult.ALLOW
    assert "fail-open" in d.reason


def test_g7_skips_symbols_with_zero_open_notional():
    """A row with 0 open shouldn't poison the cluster (e.g. just-closed)."""
    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", 0.85),
    ])
    g = CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4)
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_symbol={"crypto:OKX:ETH/USDT:USDT": 0.0},
    )
    d = g.check(make_order(symbol="crypto:OKX:BTC/USDT:USDT"), ctx)
    assert d.result == GuardResult.ALLOW


# ================================================================== #
# Pipeline integration
# ================================================================== #
def test_g7_in_pipeline_after_per_market():
    """Realistic: per-market caps allow but G7 catches concentrated cluster."""
    from risk import PerMarketExposureGuard

    m = InMemoryCorrelationMatrix([
        ("crypto:OKX:BTC/USDT:USDT", "crypto:OKX:ETH/USDT:USDT", 0.85),
    ])
    pipeline = GuardPipeline([
        PerMarketExposureGuard(default_cap_pct=0.8),     # 8k cap
        CorrelationCapGuard(matrix=m, cluster_cap_pct=0.4),  # 4k cap on cluster
    ])
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_market={"crypto": 3500.0},      # under per-market 8k
        open_notional_by_symbol={
            "crypto:OKX:ETH/USDT:USDT": 3500.0,          # alone in cluster
        },
    )
    run = pipeline.evaluate(
        make_order(symbol="crypto:OKX:BTC/USDT:USDT", notional=1000),
        ctx,
    )
    # Per-market allows (8k cap, 3.5k used, 1k request fits)
    # G7 catches: 3500 + 1000 = 4500 > 4000 cluster cap → SCALE 500
    assert run.accepted   # SCALE accepts
    assert run.final_notional_usd == 500


# ================================================================== #
# make_context_provider supplies open_by_symbol
# ================================================================== #
def test_make_context_provider_supplies_open_by_symbol():
    exposure = InMemoryExposureProvider([
        {"strategy_id": "s1", "symbol": "crypto:OKX:BTC/USDT:USDT", "notional_usd": 1000},
        {"strategy_id": "s1", "symbol": "crypto:OKX:ETH/USDT:USDT", "notional_usd": 500},
    ])
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=exposure,
    )
    ctx = ctx_provider(make_order())
    assert ctx.open_notional_by_symbol == {
        "crypto:OKX:BTC/USDT:USDT": 1000.0,
        "crypto:OKX:ETH/USDT:USDT": 500.0,
    }


def test_make_context_provider_handles_missing_open_by_symbol():
    """Backward compat: legacy ExposureProvider without open_by_symbol →
    empty dict, not crash."""
    class LegacyProvider:
        def open_by_strategy(self): return {}
        def open_by_market(self): return {}
        def global_open(self): return 0.0
    ctx_provider = make_context_provider(
        capital_usd=10_000,
        exposure=LegacyProvider(),
    )
    ctx = ctx_provider(make_order())
    assert ctx.open_notional_by_symbol == {}
