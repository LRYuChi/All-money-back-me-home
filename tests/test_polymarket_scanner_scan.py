"""Tests for polymarket.scanner.scan — orchestrator end-to-end."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from polymarket.models import Position, Trade
from polymarket.scanner import SCANNER_VERSION
from polymarket.scanner.scan import scan_wallet

NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def _trade(market: str, days_ago: int, notional: float = 500.0) -> Trade:
    price = Decimal("0.5")
    return Trade(
        id=f"t-{market}-{days_ago}",
        market=market,
        asset_id="tok1",
        price=price,
        size=Decimal(str(notional / 0.5)),
        side="BUY",
        match_time=NOW - timedelta(days=days_ago),
    )


def _resolved(pnl: float, days_ago: int = 5) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId="0xA",
        outcome="Yes",
        size=Decimal("100"),
        cashPnl=Decimal(str(pnl)),
        curPrice=Decimal("1") if pnl > 0 else Decimal("0"),
        initialValue=Decimal("50"),
        redeemable=True,
        endDate=NOW - timedelta(days=days_ago),
    )


class TestScanWallet:
    def test_excluded_when_no_trades(self):
        profile = scan_wallet("0xw", [], [], now=NOW)
        assert profile.tier == "excluded"
        assert profile.passed_coarse_filter is False
        assert profile.scanner_version == SCANNER_VERSION
        assert profile.scanned_at == NOW

    def test_excluded_when_coarse_filter_fails(self):
        # 1 trade < min_trades_total
        profile = scan_wallet("0xw", [_trade("0xA", 1)], [], now=NOW)
        assert profile.passed_coarse_filter is False
        assert profile.tier == "excluded"
        assert any("insufficient_trades" in r for r in profile.coarse_filter_reasons)

    def test_passes_coarse_filter_with_normal_data(self):
        trades = [_trade(f"0x{i}", i) for i in range(10)]
        profile = scan_wallet("0xw", trades, [_resolved(100)], now=NOW)
        assert profile.passed_coarse_filter is True
        # tier 可能是 excluded（量不足以達 C 級）但 coarse 通過
        assert "core_stats" in profile.features
        assert profile.features["core_stats"].confidence == "ok"

    def test_features_only_includes_enabled(self):
        # 1.5b.1 啟用 core_stats + category_specialization + time_slice_consistency + steady_growth
        trades = [_trade(f"0x{i}", i) for i in range(10)]
        profile = scan_wallet("0xw", trades, [_resolved(100)], now=NOW)
        assert set(profile.features.keys()) == {
            "core_stats",
            "category_specialization",
            "time_slice_consistency",
            "steady_growth",
        }

    def test_archetypes_empty_until_15c(self):
        trades = [_trade(f"0x{i}", i) for i in range(10)]
        profile = scan_wallet("0xw", trades, [_resolved(100)], now=NOW)
        # 1.5b 仍不啟用 archetype（1.5c 才會）
        assert profile.archetypes == []

    def test_risk_flags_empty_until_15c(self):
        trades = [_trade(f"0x{i}", i) for i in range(10)]
        profile = scan_wallet("0xw", trades, [_resolved(100)], now=NOW)
        assert profile.risk_flags == []

    def test_sample_size_warning_set_when_low(self):
        # 5 trades + 0 resolved = warning
        trades = [_trade(f"0x{i}", i) for i in range(5)]
        profile = scan_wallet("0xw", trades, [], now=NOW)
        assert profile.sample_size_warning is True

    def test_to_db_dict_round_trip(self):
        from polymarket.scanner.profile import WalletProfile

        trades = [_trade(f"0x{i}", i) for i in range(10)]
        profile = scan_wallet("0xw", trades, [_resolved(100)], now=NOW)
        d = profile.to_db_dict()

        # 模擬 DB 讀回（INTEGER → bool 的還原）
        d["passed_coarse_filter"] = bool(d["passed_coarse_filter"])
        d["sample_size_warning"] = bool(d["sample_size_warning"])
        restored = WalletProfile.from_db_row(d)

        assert restored.wallet_address == profile.wallet_address
        assert restored.tier == profile.tier
        assert restored.scanner_version == profile.scanner_version
        assert restored.coarse_filter_reasons == profile.coarse_filter_reasons
        assert "core_stats" in restored.features
