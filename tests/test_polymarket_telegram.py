"""Tests for polymarket.telegram — message formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from polymarket.telegram import format_tier_change, format_whale_alert, send_whale_alert


class TestFormatWhaleAlert:
    def test_basic_format(self):
        text = format_whale_alert(
            tier="A",
            wallet_address="0x1234567890abcdef1234567890abcdef12345678",
            market_question="Will Fed raise 25bps?",
            market_category="Macro",
            side="BUY",
            outcome="Yes",
            price=0.63,
            size=1000.0,
            notional=630.0,
            match_time=datetime(2026, 4, 21, 14, 23, 5, tzinfo=timezone.utc),
        )
        assert "[POLY-A] 鯨魚交易" in text
        assert "0x1234...5678" in text
        assert "Will Fed raise 25bps?" in text
        assert "Tier A" in text
        assert "BUY Yes @ 0.6300" in text
        assert "$630" in text
        assert "2026-04-21 14:23:05 UTC" in text

    def test_large_notional_gets_flag(self):
        text = format_whale_alert(
            tier="A",
            wallet_address="0xabc",
            market_question="Q?",
            side="BUY",
            outcome="Yes",
            price=0.5,
            size=25000,
            notional=12500,
        )
        assert "(大額)" in text

    def test_small_notional_no_flag(self):
        text = format_whale_alert(
            tier="C",
            wallet_address="0xabc",
            market_question="Q?",
            side="SELL",
            outcome="No",
            price=0.5,
            size=200,
            notional=100,
        )
        assert "(大額)" not in text

    def test_wallet_stats_rendered(self):
        text = format_whale_alert(
            tier="A",
            wallet_address="0xabc",
            market_question="Q?",
            side="BUY",
            outcome="Yes",
            price=0.5,
            size=1,
            notional=0.5,
            wallet_stats={
                "trade_count_90d": 24,
                "win_rate": 0.67,
                "cumulative_pnl": 34200,
                "avg_trade_size": 850,
            },
        )
        assert "交易數: 24" in text
        assert "勝率: 67.0%" in text
        assert "累積 PnL: +$34,200" in text
        assert "平均尺寸: $850" in text


class TestFormatTierChange:
    def test_initial(self):
        text = format_tier_change("0x1234567890abcdef1234567890abcdef12345678", None, "A", "initial")
        assert "(新)" in text
        assert " → A" in text

    def test_promotion(self):
        text = format_tier_change("0xabc", "B", "A", "promoted")
        assert "B → A" in text
        assert "promoted" in text


class TestSendWhaleAlertDryRun:
    def test_dry_run_returns_text(self):
        ok, text = send_whale_alert(
            tier="B",
            wallet_address="0xabc",
            market_question="Q?",
            side="BUY",
            outcome="Yes",
            price=0.5,
            size=1,
            notional=1,
            dry_run=True,
        )
        assert ok is True
        assert "[POLY-B]" in text
