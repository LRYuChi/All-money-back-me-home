"""Discovery — 找出今日要掃描的候選錢包池.

這是 scanner 第一階段。職責很單純：
    從 trades 表中列出最近 N 天有活動的錢包，依 24h 成交額降序，取前 M 個。

排序邏輯刻意保留——鯨魚通常是高成交額而非高頻，依量排序讓真正的鯨魚優先進入
後續的特徵計算階段（重要因為 features 計算成本是 discovery 的數百倍）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def discover_active_wallets(
    repo: Any,  # SqliteRepo（避免 circular import）
    pre_reg: dict[str, Any],
    *,
    limit_override: int | None = None,
) -> list[str]:
    """列出候選錢包.

    Args:
        repo: SqliteRepo 實例
        pre_reg: pre_registered.yaml 內容
        limit_override: 強制覆蓋 max_candidates_per_run（測試用）

    Returns:
        錢包地址列表，依最近 24h 成交額降序排列
    """
    cfg = pre_reg["scanner"]["discovery"]
    days = int(cfg["active_window_days"]["value"])
    cap = limit_override if limit_override is not None else int(cfg["max_candidates_per_run"]["value"])

    # repo.recent_unique_wallets 內部用 24h 視窗，這裡我們改用 active_window_days
    # 但保留 24h notional 排序（因為長期窗口的 notional 排序需要新查詢）
    hours = days * 24
    wallets = repo.recent_unique_wallets(hours=hours, limit=cap)
    logger.info("discovery: %d candidate wallets (window=%dd, cap=%d)", len(wallets), days, cap)
    return wallets
