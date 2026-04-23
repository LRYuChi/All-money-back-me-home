"""鯨魚錢包分類 — Phase 1 核心.

門檻全部讀自 polymarket/config/pre_registered.yaml。
設計對應 docs/polymarket/architecture.md §2.1：A/B/C 三層 + 穩定性後過濾。

分類邏輯：
    1. 依錢包過去 90 天的 trades 與 positions 計算統計
    2. 從 A 開始往下比對門檻，找到第一個全部滿足的 tier
    3. 通過 tier 門檻後再檢查「穩定性」：將 90d 切成 3 段 30d，每段勝率必須 ≥ tier * 0.85
    4. 穩定性失敗 → 'volatile'（不推播）
    5. 四個門檻皆不滿足 → 'excluded'

統計來源：
    - trade_count_90d: 來自 get_user_trades(last 90d).length
    - avg_trade_size: 來自 trades.notional 平均
    - cumulative_pnl: 來自 positions.realized_pnl 總和
    - win_rate: 來自 is_resolved=True 的 positions，勝 / 全部已結算
    - segment_win_rates: 將已結算 positions 依 end_date 分 3 段 30d
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from polymarket.config import load_pre_registered
from polymarket.models import Position, Trade

logger = logging.getLogger(__name__)

TIER_ORDER = ("A", "B", "C")
TIER_EMERGING = "emerging"
TIER_VOLATILE = "volatile"
TIER_EXCLUDED = "excluded"


@dataclass
class WhaleStats:
    """單一錢包的 90 天統計快照."""

    wallet_address: str
    trade_count_90d: int = 0
    avg_trade_size: float = 0.0  # USDC
    cumulative_pnl: float = 0.0  # realized only, USDC
    win_rate: float = 0.0  # 0.0-1.0, 分母為已結算倉位數
    resolved_count: int = 0
    segment_win_rates: list[float] = field(default_factory=list)  # 3 elements, 0.0-1.0
    stability_pass: bool = False
    last_trade_at: datetime | None = None
    tier: str = TIER_EXCLUDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "tier": self.tier,
            "trade_count_90d": self.trade_count_90d,
            "win_rate": self.win_rate,
            "cumulative_pnl": self.cumulative_pnl,
            "avg_trade_size": self.avg_trade_size,
            "segment_win_rates": self.segment_win_rates,
            "stability_pass": self.stability_pass,
            "resolved_count": self.resolved_count,
            "last_trade_at": self.last_trade_at.isoformat() if self.last_trade_at else None,
        }


def compute_whale_stats(
    wallet_address: str,
    trades: list[Trade],
    positions: list[Position],
    *,
    now: datetime | None = None,
) -> WhaleStats:
    """純函式計算 WhaleStats（不帶副作用，方便測試）."""
    now = now or datetime.now(timezone.utc)
    cutoff_90d = now - timedelta(days=90)
    stats = WhaleStats(wallet_address=wallet_address)

    # --- trade_count_90d, avg_trade_size, last_trade_at ---
    recent_trades = [t for t in trades if t.match_time >= cutoff_90d]
    stats.trade_count_90d = len(recent_trades)
    if recent_trades:
        total_notional = sum((t.notional_usdc() for t in recent_trades), Decimal(0))
        stats.avg_trade_size = float(total_notional / Decimal(len(recent_trades)))
        stats.last_trade_at = max(t.match_time for t in recent_trades)

    # --- cumulative_pnl：加總已結算倉位的 cashPnl ---
    # 只計已結算，避免未結算倉位的浮動 PnL 扭曲統計
    resolved_for_pnl = [p for p in positions if p.is_resolved]
    stats.cumulative_pnl = float(sum((p.cash_pnl for p in resolved_for_pnl), Decimal(0)))

    # --- win_rate ---
    resolved = [p for p in positions if p.is_resolved]
    stats.resolved_count = len(resolved)
    wins = sum(1 for p in resolved if p.is_winning)
    stats.win_rate = (wins / len(resolved)) if resolved else 0.0

    # --- segment_win_rates (3 × 30d windows, 以 end_date 歸段) ---
    stats.segment_win_rates = _compute_segment_win_rates(resolved, now=now)

    return stats


def _compute_segment_win_rates(
    resolved: list[Position],
    *,
    now: datetime,
    segment_days: int = 30,
    num_segments: int = 3,
    min_samples_per_segment: int = 3,
) -> list[float]:
    """將已結算倉位按 end_date 分段計算勝率.

    段 0 = 最近 30 天，段 1 = 30-60 天前，段 2 = 60-90 天前。
    若某段樣本不足 min_samples_per_segment，該段回傳 -1.0（呼叫端視為失敗）。
    """
    segments: list[list[Position]] = [[] for _ in range(num_segments)]
    for p in resolved:
        if p.end_date is None:
            continue
        age_days = (now - p.end_date).days
        seg_idx = age_days // segment_days
        if 0 <= seg_idx < num_segments:
            segments[seg_idx].append(p)

    rates: list[float] = []
    for seg in segments:
        if len(seg) < min_samples_per_segment:
            rates.append(-1.0)  # sentinel: insufficient data
            continue
        wins = sum(1 for p in seg if p.is_winning)
        rates.append(wins / len(seg))
    return rates


def classify_tier(stats: WhaleStats, pre_reg: dict | None = None) -> str:
    """根據 stats 與 pre_registered thresholds 判定 tier。修改 stats.tier 並回傳.

    判定順序：
      A → B → C（嚴格層級） → 穩定性檢查通過即返回
      若穩定性失敗 → 檢查 emerging（seg 0 達標 + seg 1/2 不顯著走壞）
      仍不符合 → volatile 或 excluded
    """
    cfg = pre_reg if pre_reg is not None else load_pre_registered()
    tiers_cfg = cfg["whale_tiers"]
    stability_cfg = tiers_cfg.get("stability_filter", {})
    stability_ratio = float(stability_cfg.get("min_segment_win_rate_ratio", {}).get("value", 0.85))

    # 從 A → B → C 依序檢查門檻
    for tier_key in TIER_ORDER:
        tcfg = tiers_cfg[tier_key]
        if not _meets_tier_thresholds(stats, tcfg):
            continue
        # 通過門檻後檢查穩定性
        min_win_rate = float(tcfg["min_win_rate"]["value"])
        stats.stability_pass = _check_stability(
            stats.segment_win_rates, min_win_rate, stability_ratio
        )
        if stats.stability_pass:
            stats.tier = tier_key
            return stats.tier
        # 穩定性失敗 — 嘗試 emerging 判定
        emerging_cfg = tiers_cfg.get("E")
        if emerging_cfg and _meets_emerging_criteria(stats, emerging_cfg):
            stats.tier = TIER_EMERGING
            return stats.tier
        # 既不穩又不符合 emerging → volatile
        stats.tier = TIER_VOLATILE
        return stats.tier

    stats.tier = TIER_EXCLUDED
    return stats.tier


def _meets_emerging_criteria(stats: WhaleStats, ecfg: dict) -> bool:
    """判斷是否符合 emerging tier。

    條件：
    1. 通過 emerging 的基本數量/勝率/PnL/平均尺寸門檻（與 C 相同或更嚴）
    2. Segment 0（最近 30 天）有充分樣本 + 勝率達標
    3. Segments 1, 2 允許 N/A (-1)，但若有資料，不能比 segment 0 掉太多
    """
    # 基本門檻
    if not _meets_tier_thresholds(stats, ecfg):
        return False

    # Segment 0 門檻
    # 我們需要從 segment_win_rates + 原始 segment 樣本數獲取資訊
    # 但 WhaleStats 只保存 segment_win_rates（-1 代表樣本不足）
    # 這裡用 win_rate 推估：若 segment 0 rate >= min_segment_0_win_rate
    # 並且整體 resolved_count >= min_segment_0_resolved（近似，因為 emerging 的 seg 0 通常就是全部 resolved）
    min_seg0_resolved = int(ecfg["min_segment_0_resolved"]["value"])
    min_seg0_wr = float(ecfg["min_segment_0_win_rate"]["value"])
    max_dropoff = float(ecfg["max_segment_1_2_dropoff"]["value"])

    # 至少要有 segment 0 資料
    if not stats.segment_win_rates or len(stats.segment_win_rates) < 1:
        return False
    seg0 = stats.segment_win_rates[0]
    if seg0 == -1 or seg0 < min_seg0_wr:
        return False

    # resolved_count 是整體，但對 emerging 而言 seg 1/2 通常是 0，所以 resolved_count ≈ seg 0 resolved
    if stats.resolved_count < min_seg0_resolved:
        return False

    # Segments 1, 2 的保護機制：若有資料且勝率顯著低於 seg 0 → 不是 emerging，是 volatile
    for other_seg in stats.segment_win_rates[1:]:
        if other_seg == -1:
            continue  # 允許 N/A
        if (seg0 - other_seg) > max_dropoff:
            return False

    return True


def _meets_tier_thresholds(stats: WhaleStats, tcfg: dict) -> bool:
    min_trades = int(tcfg["min_trades_90d"]["value"])
    min_win_rate = float(tcfg["min_win_rate"]["value"])
    min_pnl = float(tcfg["min_cumulative_pnl_usdc"]["value"])
    min_avg = float(tcfg["min_avg_trade_size_usdc"]["value"])
    return (
        stats.trade_count_90d >= min_trades
        and stats.win_rate >= min_win_rate
        and stats.cumulative_pnl >= min_pnl
        and stats.avg_trade_size >= min_avg
    )


def _check_stability(
    segment_win_rates: list[float],
    tier_min_win_rate: float,
    ratio: float = 0.85,
) -> bool:
    """每段勝率必須 ≥ tier_min_win_rate × ratio。

    樣本不足（sentinel -1.0）的段視為失敗——我們不接受「沒資料所以給過」的寬鬆。
    """
    if len(segment_win_rates) < 3:
        return False
    threshold = tier_min_win_rate * ratio
    return all(rate >= threshold for rate in segment_win_rates)


def classify_wallet(
    wallet_address: str,
    trades: list[Trade],
    positions: list[Position],
    *,
    now: datetime | None = None,
    pre_reg: dict | None = None,
) -> WhaleStats:
    """便利入口：compute + classify 一次完成."""
    stats = compute_whale_stats(wallet_address, trades, positions, now=now)
    classify_tier(stats, pre_reg=pre_reg)
    return stats
