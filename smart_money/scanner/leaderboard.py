"""Fetch Hyperliquid public leaderboard and pick candidate seed wallets.

HL 官方 info API 沒有 leaderboard endpoint,但 SPA 的資料源
`https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` 是公開的 28MB JSON,
回傳所有 ~34k 個有過交易的帳戶及 day/week/month/allTime PnL+ROI+volume.

本 module 的職責:
1. 下載 + 快取
2. 解析為 LeaderboardRow dataclass
3. 提供多種排序 / 篩選 / dedup / 混合策略
4. 給 cli/seed.py 用來寫 seeds.yaml

重點:這只是 **seed 來源** — 真正誰該被跟單,由我們 P2 演算法決定.
不要直接拿 leaderboard top 去下單.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
Window = Literal["day", "week", "month", "allTime"]


@dataclass(slots=True, frozen=True)
class LeaderboardRow:
    address: str
    account_value: float
    display_name: str | None
    pnl: dict[Window, float]          # keyed by window
    roi: dict[Window, float]
    volume: dict[Window, float]

    def get(self, window: Window, metric: Literal["pnl", "roi", "volume"]) -> float:
        return {"pnl": self.pnl, "roi": self.roi, "volume": self.volume}[metric].get(window, 0.0)


def _parse_row(row: dict) -> LeaderboardRow:
    pnl: dict[Window, float] = {}
    roi: dict[Window, float] = {}
    vol: dict[Window, float] = {}
    for win, perf in row.get("windowPerformances", []):
        pnl[win] = float(perf.get("pnl") or 0)
        roi[win] = float(perf.get("roi") or 0)
        vol[win] = float(perf.get("vlm") or 0)
    return LeaderboardRow(
        address=str(row["ethAddress"]).lower(),
        account_value=float(row.get("accountValue") or 0),
        display_name=row.get("displayName"),
        pnl=pnl,
        roi=roi,
        volume=vol,
    )


def fetch_leaderboard(
    *,
    url: str = LEADERBOARD_URL,
    cache_path: Path | None = None,
    use_cache: bool = False,
    timeout: float = 120.0,
) -> list[LeaderboardRow]:
    """Download & parse leaderboard.

    Args:
        cache_path: if provided, write the raw JSON there on fetch
                    (or read from it when use_cache=True).
        use_cache: read cache_path instead of network; cache_path must exist.
    """
    if use_cache:
        if not cache_path or not cache_path.exists():
            raise FileNotFoundError(
                f"use_cache=True but cache_path missing: {cache_path}",
            )
        logger.info("Loading leaderboard from cache: %s", cache_path)
        raw = json.loads(cache_path.read_text())
    else:
        logger.info("Fetching leaderboard from %s (this downloads ~28MB)", url)
        r = httpx.get(url, timeout=timeout, headers={"User-Agent": "smart_money/0.1"})
        r.raise_for_status()
        raw = r.json()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw))
            logger.info("Cached leaderboard → %s", cache_path)

    rows = [_parse_row(r) for r in raw.get("leaderboardRows", [])]
    logger.info("Parsed %d leaderboard rows", len(rows))
    return rows


# ------------------------------------------------------------------ #
# Filters & Rankers
# ------------------------------------------------------------------ #
def filter_active(
    rows: list[LeaderboardRow],
    *,
    min_account_value: float = 10_000.0,
    min_month_volume: float = 100_000.0,
    min_month_pnl_abs: float = 1_000.0,
) -> list[LeaderboardRow]:
    """排除小帳戶 / 休眠帳戶.

    - min_account_value: 現在錢包 ≥ 這個 USD (預設 10k)
    - min_month_volume: 30 天有真交易量 (過濾只 hold 不動的倉)
    - min_month_pnl_abs: 30 天 PnL 絕對值 > N (純 hold 者的 PnL 變化很小)
    """
    out = []
    for r in rows:
        if r.account_value < min_account_value:
            continue
        if r.get("month", "volume") < min_month_volume:
            continue
        if abs(r.get("month", "pnl")) < min_month_pnl_abs:
            continue
        out.append(r)
    return out


def top_by(
    rows: list[LeaderboardRow],
    *,
    window: Window,
    metric: Literal["pnl", "roi", "volume"],
    n: int = 30,
) -> list[LeaderboardRow]:
    """Sort descending by (window, metric) and take top N."""
    return sorted(rows, key=lambda r: r.get(window, metric), reverse=True)[:n]


@dataclass(slots=True, frozen=True)
class SeedBucket:
    """一個 seed 採樣策略描述."""

    window: Window
    metric: Literal["pnl", "roi", "volume"]
    n: int
    label: str


DEFAULT_STRATEGY: tuple[SeedBucket, ...] = (
    # 長期累積獲利王:看 PnL 總量
    SeedBucket("allTime", "pnl", 20, "alltime_pnl_top"),
    # 本月活躍冠軍:30 天大贏家(可能是近期趨勢抓得準)
    SeedBucket("month", "pnl", 30, "month_pnl_top"),
    # 本月高效率者:按 ROI,抓小資本但運用高效的
    SeedBucket("month", "roi", 20, "month_roi_top"),
    # 本週趨勢:近期熱門
    SeedBucket("week", "pnl", 15, "week_pnl_top"),
    # 本週高 ROI:短期爆發型
    SeedBucket("week", "roi", 15, "week_roi_top"),
)


def build_seed_set(
    rows: list[LeaderboardRow],
    *,
    strategy: tuple[SeedBucket, ...] = DEFAULT_STRATEGY,
    dedup: bool = True,
) -> list[tuple[LeaderboardRow, list[str]]]:
    """Apply a mix of buckets to produce a diverse candidate list.

    Returns:
        list of (row, tags) — tags 紀錄該地址由哪些 bucket 貢獻,方便追蹤.
        結果按「tag 數量降冪」排序(多個 bucket 都命中的地址更有價值).
    """
    acc: dict[str, tuple[LeaderboardRow, list[str]]] = {}
    for bucket in strategy:
        picks = top_by(rows, window=bucket.window, metric=bucket.metric, n=bucket.n)
        for r in picks:
            if r.address in acc:
                acc[r.address][1].append(bucket.label)
            else:
                acc[r.address] = (r, [bucket.label])

    items = list(acc.values())
    if dedup:
        # 已經 dedup by address;按 tags 多寡排序(多重命中 = 更穩健)
        items.sort(key=lambda x: (-len(x[1]), -x[0].account_value))
    return items


__all__ = [
    "DEFAULT_STRATEGY",
    "LEADERBOARD_URL",
    "LeaderboardRow",
    "SeedBucket",
    "Window",
    "build_seed_set",
    "fetch_leaderboard",
    "filter_active",
    "top_by",
]
