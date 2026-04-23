"""Tests for risk_flags (1.5c.2) — concentration_high / loss_loading / wash_trade_suspicion."""

from __future__ import annotations

from polymarket.config import load_pre_registered
from polymarket.scanner.classify import (
    RISK_CONCENTRATION_HIGH,
    RISK_LOSS_LOADING,
    RISK_WASH_TRADE_SUSPICION,
    detect_risk_flags,
)
from polymarket.scanner.profile import FeatureResult


def _feat(name: str, value: dict, *, confidence: str = "ok") -> FeatureResult:
    return FeatureResult(
        feature_name=name,
        feature_version="1.0",
        value=value,
        confidence=confidence,
        sample_size=50,
    )


class TestConcentrationHigh:
    def test_fires_when_single_category_dominant(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 9000, "resolved": 30, "win_rate": 0.65},
                        "Sports": {"notional": 500, "resolved": 3, "win_rate": 0.5},
                    }
                },
            )
        }
        tags = detect_risk_flags(features, load_pre_registered())
        assert RISK_CONCENTRATION_HIGH in tags

    def test_no_fire_when_balanced(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 4000, "resolved": 20},
                        "Sports": {"notional": 3500, "resolved": 15},
                        "Crypto": {"notional": 2500, "resolved": 10},
                    }
                },
            )
        }
        assert RISK_CONCENTRATION_HIGH not in detect_risk_flags(features, load_pre_registered())

    def test_excludes_unknown_from_denominator(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 9000, "resolved": 30},
                        "(unknown)": {"notional": 5000, "resolved": 20},
                    }
                },
            )
        }
        # Politics 9000 / (9000 + 0) = 100% among known → fires
        assert RISK_CONCENTRATION_HIGH in detect_risk_flags(features, load_pre_registered())

    def test_low_samples_no_fire(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {"categories": {"Politics": {"notional": 9000, "resolved": 30}}},
                confidence="low_samples",
            )
        }
        assert RISK_CONCENTRATION_HIGH not in detect_risk_flags(features, load_pre_registered())

    def test_no_categories_no_fire(self):
        features = {
            "category_specialization": _feat(
                "category_specialization", {"categories": {}}
            )
        }
        assert RISK_CONCENTRATION_HIGH not in detect_risk_flags(features, load_pre_registered())


class TestLossLoading:
    def test_fires_when_recent_negative_prior_positive(self):
        features = {
            "steady_growth": _feat(
                "steady_growth",
                {"segment_pnls_usdc": [-500, 800, 600]},
            )
        }
        tags = detect_risk_flags(features, load_pre_registered())
        assert RISK_LOSS_LOADING in tags

    def test_no_fire_when_recent_positive(self):
        features = {
            "steady_growth": _feat(
                "steady_growth", {"segment_pnls_usdc": [500, -200, 300]}
            )
        }
        assert RISK_LOSS_LOADING not in detect_risk_flags(features, load_pre_registered())

    def test_no_fire_when_all_three_negative(self):
        # 整體表現差但不算「變差中」— loss_loading 表達的是「前期好現在壞」
        features = {
            "steady_growth": _feat(
                "steady_growth", {"segment_pnls_usdc": [-500, -400, -300]}
            )
        }
        assert RISK_LOSS_LOADING not in detect_risk_flags(features, load_pre_registered())

    def test_no_fire_when_only_one_prior_positive(self):
        features = {
            "steady_growth": _feat(
                "steady_growth", {"segment_pnls_usdc": [-500, 800, -200]}
            )
        }
        # 只有 seg1 是正；門檻要求 ≥ 2 → 不 fire
        assert RISK_LOSS_LOADING not in detect_risk_flags(features, load_pre_registered())

    def test_accepts_low_samples_confidence(self):
        """Loss loading 是警示，low_samples 也應觸發（不遺漏）."""
        features = {
            "steady_growth": _feat(
                "steady_growth",
                {"segment_pnls_usdc": [-500, 800, 600]},
                confidence="low_samples",
            )
        }
        assert RISK_LOSS_LOADING in detect_risk_flags(features, load_pre_registered())


class TestWashTradeSuspicion:
    def test_fires_when_category_winrate_near_random_with_high_share(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 7000, "resolved": 30, "win_rate": 0.50},
                        "Sports": {"notional": 3000, "resolved": 10, "win_rate": 0.55},
                    }
                },
            )
        }
        # Politics: notional 佔 70%, wr=0.5, resolved 30 → fire
        tags = detect_risk_flags(features, load_pre_registered())
        assert RISK_WASH_TRADE_SUSPICION in tags

    def test_no_fire_when_high_edge(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 7000, "resolved": 30, "win_rate": 0.75},
                    }
                },
            )
        }
        assert RISK_WASH_TRADE_SUSPICION not in detect_risk_flags(features, load_pre_registered())

    def test_no_fire_when_low_share(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 3000, "resolved": 30, "win_rate": 0.50},
                        "Sports": {"notional": 7000, "resolved": 30, "win_rate": 0.60},
                    }
                },
            )
        }
        # Politics 只佔 30% → fail min_notional_share
        assert RISK_WASH_TRADE_SUSPICION not in detect_risk_flags(features, load_pre_registered())

    def test_no_fire_when_insufficient_resolved(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 7000, "resolved": 10, "win_rate": 0.50},
                    }
                },
            )
        }
        # resolved < 20 → fail min_category_resolved
        assert RISK_WASH_TRADE_SUSPICION not in detect_risk_flags(features, load_pre_registered())


class TestMultipleFlags:
    def test_multiple_can_coexist(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {
                    "categories": {
                        "Politics": {"notional": 9000, "resolved": 30, "win_rate": 0.52},
                    }
                },
            ),
            "steady_growth": _feat(
                "steady_growth", {"segment_pnls_usdc": [-400, 500, 400]}
            ),
        }
        tags = detect_risk_flags(features, load_pre_registered())
        # concentration_high (85%+) + wash_trade_suspicion (50% wr, 100% share) + loss_loading
        assert RISK_CONCENTRATION_HIGH in tags
        assert RISK_WASH_TRADE_SUSPICION in tags
        assert RISK_LOSS_LOADING in tags
        # 順序穩定
        assert tags.index(RISK_CONCENTRATION_HIGH) < tags.index(RISK_LOSS_LOADING)
        assert tags.index(RISK_LOSS_LOADING) < tags.index(RISK_WASH_TRADE_SUSPICION)

    def test_empty_features_empty_flags(self):
        assert detect_risk_flags({}, load_pre_registered()) == []


class TestScanIntegration:
    def test_scanner_version_current(self):
        from polymarket.scanner import SCANNER_VERSION

        # Risk flags 從 1.5c.2 啟用；之後版本（1.5c.3+）仍應保有
        from polymarket.config import load_pre_registered

        pre_reg = load_pre_registered()
        # classify.detect_risk_flags 不依版本開關，所以只要 scanner 還存在即可
        assert "enabled_in_version" in pre_reg["scanner"]["features"]
        assert SCANNER_VERSION in pre_reg["scanner"]["features"]["enabled_in_version"]

    def test_scan_wallet_populates_risk_flags(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from polymarket.models import Position, Trade
        from polymarket.scanner.scan import scan_wallet

        NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        # 製造集中度過高情境：30 筆倉位全在同一 market / category
        positions = [
            Position(
                proxyWallet="0xw",
                conditionId="0xPol",
                outcome="Yes",
                size=Decimal("100"),
                avgPrice=Decimal("0.45"),
                initialValue=Decimal("500"),
                currentValue=Decimal("500"),
                cashPnl=Decimal("0"),
                curPrice=Decimal("1" if i % 2 == 0 else "0"),
                redeemable=True,
                endDate=NOW - timedelta(days=80 - i * 2),
            )
            for i in range(30)
        ]
        trades = [
            Trade(
                id=f"t-{i}",
                market="0xPol",
                asset_id="tok1",
                price=Decimal("0.45"),
                size=Decimal("1000"),
                side="BUY",
                match_time=NOW - timedelta(days=70 - i * 2),
            )
            for i in range(30)
        ]
        market_categories = {"0xPol": "Politics"}
        profile = scan_wallet(
            "0xw", trades, positions, now=NOW, market_categories=market_categories
        )
        assert "concentration_high" in profile.risk_flags
