"""Unit tests for smart_money.ranking (Phase 2).

這是系統核心判定邏輯,測試特別嚴謹:
- 每個 metric 獨立驗證(已知輸入 → 已知輸出)
- determinism(相同輸入相同輸出)
- edge cases(空輸入、全贏、全輸、單一幣種、反手)
- filters 的 min/max 邊界
- scorer 的權重效應與解釋性
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from smart_money.config import RankingSettings
from smart_money.ranking.filters import (
    FilterThresholds,
    active_days,
    apply_filters,
    avg_holding_seconds,
    symbol_concentration,
)
from smart_money.ranking.metrics import (
    closed_pnls,
    compute_all,
    compute_drawdown,
    drawdown_recovery_score,
    holding_time_cv,
    martingale_penalty,
    profit_factor,
    regime_stability,
    sortino_ratio,
)
from smart_money.ranking.scorer import (
    norm_holding_cv,
    norm_profit_factor,
    norm_sortino,
    score_and_rank,
    score_wallet,
)
from smart_money.store.schema import Trade


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
WID = uuid4()
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def tr(
    *,
    tid: int | str,
    ts: datetime,
    action: str,
    side: str = "long",
    symbol: str = "BTC",
    size: float = 0.1,
    price: float = 50000.0,
    pnl: float | None = None,
) -> Trade:
    return Trade(
        wallet_id=WID,
        hl_trade_id=str(tid),
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        size=size,
        price=price,
        pnl=pnl,
        fee=0.05,
        ts=ts,
    )


def open_close_pair(tid_base: int, ts: datetime, *, hold_hours: float, pnl: float,
                    symbol: str = "BTC", side: str = "long", size: float = 0.1) -> list[Trade]:
    return [
        tr(tid=f"{tid_base}-o", ts=ts, action="open", side=side, symbol=symbol, size=size),
        tr(tid=f"{tid_base}-c", ts=ts + timedelta(hours=hold_hours),
           action="close", side=side, symbol=symbol, size=size, pnl=pnl),
    ]


# ------------------------------------------------------------------ #
# 1) filters
# ------------------------------------------------------------------ #
def test_active_days_zero_for_single_trade():
    assert active_days([tr(tid=1, ts=T0, action="open")]) == 0.0


def test_active_days_span():
    trades = [tr(tid=1, ts=T0, action="open"),
              tr(tid=2, ts=T0 + timedelta(days=10, hours=12), action="close", pnl=1.0)]
    assert active_days(trades) == pytest.approx(10.5)


def test_symbol_concentration_empty():
    assert symbol_concentration([]) == 0.0


def test_symbol_concentration_mixed():
    trades = ([tr(tid=i, ts=T0, action="open", symbol="BTC") for i in range(8)]
              + [tr(tid=i + 100, ts=T0, action="open", symbol="ETH") for i in range(2)])
    assert symbol_concentration(trades) == pytest.approx(0.8)


def test_avg_holding_seconds_pair():
    trades = open_close_pair(1, T0, hold_hours=2, pnl=100)
    assert avg_holding_seconds(trades) == pytest.approx(7200.0)


def test_apply_filters_passes_healthy_wallet():
    # 50 筆完整 open/close 對,跨 60 天,分散 3 個幣種以通過 concentration
    trades = []
    symbols = ["BTC", "ETH", "SOL"]
    for i in range(50):
        trades += open_close_pair(
            i, T0 + timedelta(days=i * 1.2), hold_hours=6,
            pnl=(10 if i % 2 else -5), symbol=symbols[i % 3],
        )
    r = apply_filters(trades)
    assert r.passed, r.reason


def test_apply_filters_rejects_low_sample():
    trades = open_close_pair(1, T0, hold_hours=1, pnl=5)
    r = apply_filters(trades)
    assert not r.passed and "sample_size" in r.reason


def test_apply_filters_rejects_short_activity():
    trades = []
    for i in range(60):
        trades += open_close_pair(i, T0 + timedelta(hours=i), hold_hours=1, pnl=1)
    # Active 僅 ~60 小時 < 30 天
    r = apply_filters(trades)
    assert not r.passed and "active_days" in r.reason


def test_apply_filters_rejects_high_concentration():
    trades = []
    for i in range(60):
        trades += open_close_pair(
            i, T0 + timedelta(days=i), hold_hours=3, pnl=1, symbol="BTC",
        )
    r = apply_filters(trades)
    assert not r.passed and "concentration" in r.reason


def test_apply_filters_rejects_hft_like():
    # 分散到 4 個幣種避開 concentration,跨 40 天,持倉 10 秒
    trades = []
    symbols = ["BTC", "ETH", "SOL", "BNB"]
    for i in range(60):
        sym = symbols[i % 4]
        ts = T0 + timedelta(days=i * 0.7)
        trades.append(tr(tid=f"{i}-o", ts=ts, action="open", symbol=sym))
        trades.append(tr(tid=f"{i}-c", ts=ts + timedelta(seconds=10),
                         action="close", pnl=1, symbol=sym))
    r = apply_filters(trades)
    assert not r.passed and "HFT" in r.reason


def test_apply_filters_configurable_thresholds():
    symbols = ["BTC", "ETH", "SOL"]
    trades = [tr(tid=i, ts=T0 + timedelta(days=i), action="close", pnl=1,
                 symbol=symbols[i % 3]) for i in range(10)]
    r = apply_filters(trades, thresholds=FilterThresholds(min_sample_size=5, min_active_days=1))
    assert r.passed, r.reason


# ------------------------------------------------------------------ #
# 2) metrics — Sortino
# ------------------------------------------------------------------ #
def test_closed_pnls_only_close_actions():
    trades = [
        tr(tid=1, ts=T0, action="open", pnl=None),
        tr(tid=2, ts=T0, action="close", pnl=10),
        tr(tid=3, ts=T0, action="decrease", pnl=-2),
        tr(tid=4, ts=T0, action="increase", pnl=None),
    ]
    assert closed_pnls(trades) == [10, -2]


def test_sortino_empty_and_single():
    assert sortino_ratio([]) == 0.0
    assert sortino_ratio([tr(tid=1, ts=T0, action="close", pnl=10)]) == 0.0


def test_sortino_all_wins_hits_cap():
    trades = [tr(tid=i, ts=T0 + timedelta(hours=i), action="close", pnl=10) for i in range(10)]
    # 無下行 → cap 10.0
    assert sortino_ratio(trades) == 10.0


def test_sortino_all_losses_negative_cap():
    trades = [tr(tid=i, ts=T0 + timedelta(hours=i), action="close", pnl=-10) for i in range(10)]
    # 全虧:downside_dev > 0,avg < 0
    val = sortino_ratio(trades)
    assert val < 0


def test_sortino_symmetric_wins_losses_near_zero():
    # 5 wins of +10 and 5 losses of -10 (alternating)
    trades = []
    for i in range(10):
        pnl = 10 if i % 2 == 0 else -10
        trades.append(tr(tid=i, ts=T0 + timedelta(hours=i), action="close", pnl=pnl))
    # avg_excess = 0,downside_dev > 0 → Sortino = 0
    assert sortino_ratio(trades) == pytest.approx(0.0)


def test_sortino_deterministic():
    trades = [tr(tid=i, ts=T0 + timedelta(hours=i), action="close",
                 pnl=(-5, 10, -2, 8, -1)[i % 5]) for i in range(20)]
    assert sortino_ratio(trades) == sortino_ratio(trades)


# ------------------------------------------------------------------ #
# 3) metrics — Profit Factor
# ------------------------------------------------------------------ #
def test_profit_factor_basic():
    trades = [
        tr(tid=1, ts=T0, action="close", pnl=10),
        tr(tid=2, ts=T0, action="close", pnl=20),
        tr(tid=3, ts=T0, action="close", pnl=-10),
    ]
    # wins=30 losses=10 → PF=3
    assert profit_factor(trades) == 3.0


def test_profit_factor_only_wins_cap():
    trades = [tr(tid=i, ts=T0, action="close", pnl=5) for i in range(5)]
    assert profit_factor(trades) == 10.0


def test_profit_factor_only_losses():
    trades = [tr(tid=i, ts=T0, action="close", pnl=-5) for i in range(5)]
    assert profit_factor(trades) == 0.0


def test_profit_factor_empty():
    assert profit_factor([]) == 0.0


# ------------------------------------------------------------------ #
# 4) metrics — Drawdown
# ------------------------------------------------------------------ #
def test_compute_drawdown_no_trades():
    stats = compute_drawdown([])
    assert stats.max_drawdown == 0.0
    assert stats.recovery_days is None


def test_compute_drawdown_detects_peak_and_recovery():
    # equity curve: 100 → 50 → 30 → 80 → 100 → 150
    pnls = [100, -50, -20, 50, 20, 50]
    trades = [
        tr(tid=i, ts=T0 + timedelta(days=i), action="close", pnl=p)
        for i, p in enumerate(pnls)
    ]
    stats = compute_drawdown(trades)
    # max_dd: peak=100 at day0, trough=30 at day2 → 70
    assert stats.max_drawdown == 70.0
    # recovery: 從 day2 的 equity=30 開始,equity>=100 @ day4 (100)
    assert stats.recovery_days is not None
    assert stats.recovery_days == pytest.approx(2.0)


def test_compute_drawdown_unrecovered():
    pnls = [100, -80]
    trades = [tr(tid=i, ts=T0 + timedelta(days=i), action="close", pnl=p)
              for i, p in enumerate(pnls)]
    stats = compute_drawdown(trades)
    assert stats.max_drawdown == 80.0
    assert stats.recovery_days is None


def test_drawdown_recovery_score_ranges():
    # 無 DD → 1.0
    trades = [tr(tid=i, ts=T0 + timedelta(days=i), action="close", pnl=10) for i in range(3)]
    assert drawdown_recovery_score(trades) == 1.0

    # 有 DD 未恢復 → 0.0
    trades = [tr(tid=i, ts=T0 + timedelta(days=i), action="close", pnl=p)
              for i, p in enumerate([50, -40])]
    assert drawdown_recovery_score(trades) == 0.0


# ------------------------------------------------------------------ #
# 5) metrics — Holding Time CV
# ------------------------------------------------------------------ #
def test_holding_cv_uniform_is_zero():
    trades = []
    for i in range(10):
        ts = T0 + timedelta(days=i)
        trades.append(tr(tid=f"{i}-o", ts=ts, action="open"))
        trades.append(tr(tid=f"{i}-c", ts=ts + timedelta(hours=3), action="close", pnl=1))
    assert holding_time_cv(trades) == pytest.approx(0.0)


def test_holding_cv_variable_is_positive():
    trades = []
    hold_hours = [1, 6, 0.5, 12, 3, 24]
    for i, h in enumerate(hold_hours):
        ts = T0 + timedelta(days=i)
        trades.append(tr(tid=f"{i}-o", ts=ts, action="open"))
        trades.append(tr(tid=f"{i}-c", ts=ts + timedelta(hours=h), action="close", pnl=1))
    cv = holding_time_cv(trades)
    assert cv > 0.5


# ------------------------------------------------------------------ #
# 6) metrics — Martingale Penalty
# ------------------------------------------------------------------ #
def test_martingale_penalty_clean_trader_is_zero():
    # 一般交易:開倉 size 一致,沒有連虧加倉
    trades = []
    for i in range(10):
        ts = T0 + timedelta(days=i)
        trades.append(tr(tid=f"{i}-o", ts=ts, action="open", size=1.0))
        trades.append(tr(tid=f"{i}-c", ts=ts + timedelta(hours=1),
                         action="close", pnl=(1 if i % 2 == 0 else -1), size=1.0))
    assert martingale_penalty(trades) == 0.0


def test_martingale_penalty_detects_doubling_after_losses():
    trades = []
    # 先建立 3 筆正常 size 的 open(作為 baseline)
    for i in range(3):
        ts = T0 + timedelta(hours=i)
        trades.append(tr(tid=f"{i}-o", ts=ts, action="open", size=1.0))
        trades.append(tr(tid=f"{i}-c", ts=ts + timedelta(minutes=30),
                         action="close", pnl=-1, size=1.0))
    # 連續 2 虧後:開倉 size 變 3 倍
    ts = T0 + timedelta(hours=5)
    trades.append(tr(tid="mart-o", ts=ts, action="open", size=3.0))
    trades.append(tr(tid="mart-c", ts=ts + timedelta(hours=1), action="close",
                     pnl=-1, size=3.0))
    penalty = martingale_penalty(trades)
    assert penalty > 0


# ------------------------------------------------------------------ #
# 7) metrics — Regime Stability
# ------------------------------------------------------------------ #
def test_regime_stability_consistent_winner():
    # 6 個月,每 30 天 window 都賺
    trades = []
    for m in range(6):
        for i in range(5):
            ts = T0 + timedelta(days=m * 30 + i * 2)
            trades.append(tr(tid=f"{m}-{i}", ts=ts, action="close", pnl=10))
    assert regime_stability(trades) == 1.0


def test_regime_stability_mixed():
    # 6 個 window,3 個正 3 個負
    trades = []
    for m in range(6):
        # 大致 30 天一個 window
        for i in range(5):
            ts = T0 + timedelta(days=m * 30 + i * 2)
            pnl = 10 if m < 3 else -10
            trades.append(tr(tid=f"{m}-{i}", ts=ts, action="close", pnl=pnl))
    stability = regime_stability(trades)
    # Window 邊界容許 +/- 1 個 window 的誤差
    assert 0.3 <= stability <= 0.7


# ------------------------------------------------------------------ #
# 8) compute_all integration
# ------------------------------------------------------------------ #
def test_compute_all_healthy_wallet():
    trades = []
    for i in range(60):
        ts = T0 + timedelta(days=i * 1.2)
        trades += open_close_pair(i, ts, hold_hours=5, pnl=(8 if i % 3 else -3))
    bundle = compute_all(trades)
    assert bundle.sample_size == 60
    assert bundle.profit_factor > 1.0
    assert bundle.total_pnl > 0


# ------------------------------------------------------------------ #
# 9) scorer — normalization
# ------------------------------------------------------------------ #
def test_norm_sortino_range():
    assert norm_sortino(-5) == 0.0
    assert norm_sortino(0) == 0.5
    assert norm_sortino(5) == 1.0
    assert norm_sortino(-3) == 0.0
    assert norm_sortino(3) == 1.0


def test_norm_profit_factor_monotonic():
    vals = [norm_profit_factor(x) for x in [0, 1, 2, 3, 5, 10, 100]]
    assert vals == sorted(vals)
    assert vals[0] == 0.0
    assert vals[-1] == 1.0


def test_norm_holding_cv_bell_shape():
    # 中心 ~1.5 應近 1.0
    assert norm_holding_cv(1.5) == pytest.approx(1.0)
    # 兩側衰減
    assert norm_holding_cv(0.0) < norm_holding_cv(1.0)
    assert norm_holding_cv(3.5) < norm_holding_cv(2.0)


# ------------------------------------------------------------------ #
# 10) scorer — end-to-end
# ------------------------------------------------------------------ #
def _synthetic_metrics(
    sortino=1.5, pf=2.0, dd=0.8, cv=1.5, regime=0.7, martingale=0.0,
    sample=100, total_pnl=500.0,
):
    from smart_money.ranking.metrics import MetricsBundle
    return MetricsBundle(
        sortino=sortino,
        profit_factor=pf,
        drawdown_recovery=dd,
        holding_time_cv=cv,
        martingale_penalty=martingale,
        regime_stability=regime,
        sample_size=sample,
        total_pnl=total_pnl,
    )


def test_score_wallet_range():
    sb = score_wallet(_synthetic_metrics())
    assert 0.0 <= sb.score <= 1.0


def test_score_wallet_deterministic():
    m = _synthetic_metrics()
    assert score_wallet(m).score == score_wallet(m).score


def test_score_wallet_penalises_martingale():
    good = _synthetic_metrics(martingale=0.0)
    bad = _synthetic_metrics(martingale=1.0)
    assert score_wallet(good).score > score_wallet(bad).score


def test_score_wallet_rewards_sortino():
    low = _synthetic_metrics(sortino=-1.0)
    high = _synthetic_metrics(sortino=3.0)
    assert score_wallet(high).score > score_wallet(low).score


def test_score_wallet_breakdown_sums_to_raw():
    sb = score_wallet(_synthetic_metrics())
    # contributions 加總 + 重整後 == score (in [0,1])
    raw = sum(sb.contributions.values())
    cfg = RankingSettings()
    denom = (cfg.w_sortino + cfg.w_profit_factor + cfg.w_dd_recovery +
             cfg.w_holding_cv + cfg.w_regime_stability + cfg.w_martingale_penalty)
    expected = (raw + cfg.w_martingale_penalty) / denom
    assert sb.score == pytest.approx(max(0, min(1, expected)))


def test_score_and_rank_sorts_descending():
    wallets = [
        ("w1", _synthetic_metrics(sortino=0.5, pf=1.2, dd=0.3, regime=0.4)),
        ("w2", _synthetic_metrics(sortino=2.5, pf=3.0, dd=0.9, regime=0.9)),
        ("w3", _synthetic_metrics(sortino=1.0, pf=2.0, dd=0.6, regime=0.6)),
    ]
    ranked = score_and_rank(wallets)
    scores = [sb.score for _, sb in ranked]
    assert scores == sorted(scores, reverse=True)
    # w2 最好 → rank 1
    assert ranked[0][0] == "w2"


def test_score_weight_override():
    """改 config 權重應該影響最終分數."""
    m = _synthetic_metrics(sortino=3.0, martingale=0.5)
    default_score = score_wallet(m).score
    heavy_penalty_cfg = RankingSettings(w_martingale_penalty=1.0)
    penalised_score = score_wallet(m, config=heavy_penalty_cfg).score
    assert penalised_score != default_score


def test_score_explain_is_readable():
    sb = score_wallet(_synthetic_metrics())
    text = sb.explain()
    assert "SCORE" in text
    assert "sortino" in text
    assert "martingale" in text


# ------------------------------------------------------------------ #
# 11) Sanity: healthy > grid-bot > martingale
# ------------------------------------------------------------------ #
def test_ranking_order_healthy_beats_suspicious():
    """End-to-end sanity check:健康交易員應排在可疑錢包前面."""
    healthy = _synthetic_metrics(sortino=2.0, pf=2.5, dd=0.8, cv=1.5, regime=0.8,
                                  martingale=0.0)
    grid_bot = _synthetic_metrics(sortino=0.8, pf=1.15, dd=0.5, cv=0.1, regime=0.5,
                                   martingale=0.0)  # 低 CV = bot-like
    martingale_gambler = _synthetic_metrics(sortino=1.0, pf=1.5, dd=0.2, cv=1.5,
                                             regime=0.4, martingale=0.8)

    ranked = score_and_rank([
        ("healthy", healthy),
        ("grid", grid_bot),
        ("gambler", martingale_gambler),
    ])
    order = [name for name, _ in ranked]
    assert order[0] == "healthy"
    # martingale 或 grid 誰倒數不重要,但 healthy 必在最前
