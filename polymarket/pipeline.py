"""Polymarket Phase 1 排程管線 — 5 分鐘一跑.

流程：
    1. 抓 top N 活躍市場（Gamma）
    2. 對每個市場抓最近 trades（Data API）→ 寫入 trades 表
    3. 從最近 trades 中取不重複錢包地址
    4. 對每個「新錢包」或「last_computed > 24h 的錢包」：
       a. 拉 90d 交易 + 持倉
       b. 計算 WhaleStats 並分類
       c. 寫 whale_stats（若 tier 變動會自動記 whale_tier_history）
    5. 對每個 tier in (A, B, C) 的新交易：
       a. record_alert（idempotent）
       b. 若本次才寫入（非重複），送 Telegram

執行方式：
    python -m polymarket.pipeline          # 單次執行
    python -m polymarket.pipeline --loop   # 每 5 分鐘迴圈
    python -m polymarket.pipeline --dry-run  # 不真送 Telegram
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from polymarket.clients.clob import ClobClient
from polymarket.clients.data_api import DataApiClient
from polymarket.clients.gamma import GammaClient
from polymarket.features.whales import TIER_ORDER, classify_wallet
from polymarket.storage.repo import SqliteRepo
from polymarket.telegram import send_whale_alert

logger = logging.getLogger(__name__)


# === Pipeline 常數（此檔案的基礎設施參數，不是業務門檻） ===
ACTIVE_MARKETS_PER_RUN = 20       # 每次掃幾個市場
TRADES_PER_MARKET = 50            # 每市場取最近 N 筆成交
WALLET_REFRESH_INTERVAL_HOURS = 24  # 錢包統計的快取壽命
WALLET_COMPUTE_CAP_PER_RUN = 30     # 每次最多重算幾個錢包（保護 API quota）
ALERT_TIME_WINDOW_HOURS = 24        # 推播範圍：只對過去 N 小時的鯨魚交易推播


@dataclass
class RunStats:
    markets_scanned: int = 0
    trades_ingested: int = 0
    unique_wallets_seen: int = 0
    wallets_recomputed: int = 0
    tier_changes: int = 0
    alerts_sent: int = 0
    alerts_skipped_dup: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"markets={self.markets_scanned} trades={self.trades_ingested} "
            f"wallets={self.unique_wallets_seen} recomputed={self.wallets_recomputed} "
            f"tier_changes={self.tier_changes} alerts_sent={self.alerts_sent} "
            f"alerts_dup={self.alerts_skipped_dup} errors={len(self.errors)}"
        )


def run_once(
    *,
    dry_run: bool = False,
    markets_limit: int = ACTIVE_MARKETS_PER_RUN,
    wallets_cap: int = WALLET_COMPUTE_CAP_PER_RUN,
) -> RunStats:
    """單次完整 pipeline 執行."""
    stats = RunStats()

    with (
        GammaClient() as gamma,
        ClobClient() as clob,
        DataApiClient() as data_api,
        SqliteRepo() as repo,
    ):
        # --- Step 1: 抓活躍市場並 upsert ---
        try:
            raw_list = gamma.list_markets(
                limit=markets_limit, active=True, closed=False, order="volume24hr"
            )
        except Exception as exc:
            stats.errors.append(f"gamma.list_markets: {exc}")
            logger.error("gamma.list_markets failed: %s", exc)
            return stats

        market_category: dict[str, str] = {}
        market_question: dict[str, str] = {}

        for raw in raw_list:
            cond = raw.get("conditionId") or raw.get("condition_id")
            if not cond:
                continue
            try:
                m = clob.get_market(cond)
            except Exception as exc:
                stats.errors.append(f"clob.get_market({cond[:10]}): {exc}")
                continue
            repo.upsert_market(m)
            market_category[cond] = m.category
            market_question[cond] = m.question
            stats.markets_scanned += 1

        # --- Step 2: 抓每個市場的近期 trades ---
        for cond in list(market_category.keys()):
            try:
                trades = data_api.get_market_trades(market=cond, limit=TRADES_PER_MARKET)
            except Exception as exc:
                stats.errors.append(f"get_market_trades({cond[:10]}): {exc}")
                continue
            new_count, _ = repo.insert_trades(trades)
            stats.trades_ingested += new_count

        # --- Step 3: 找不重複錢包 ---
        wallets = repo.recent_unique_wallets(hours=ALERT_TIME_WINDOW_HOURS, limit=500)
        stats.unique_wallets_seen = len(wallets)

        # --- Step 4: 為新/過期錢包重新計算統計 ---
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=WALLET_REFRESH_INTERVAL_HOURS)).isoformat()
        recomputed = 0

        for wallet in wallets:
            if recomputed >= wallets_cap:
                break
            prev = repo.get_whale_stats(wallet)
            if prev and prev.get("last_computed_at", "") > cutoff:
                continue  # 快取仍新
            try:
                user_trades = data_api.get_user_trades(wallet, limit=500)
                positions = data_api.get_user_positions(wallet, limit=500)
            except Exception as exc:
                stats.errors.append(f"fetch wallet {wallet[:10]}: {exc}")
                continue

            whale_stats = classify_wallet(wallet, user_trades, positions, now=now)
            changed_from = repo.upsert_whale_stats(whale_stats.to_dict())
            if changed_from is not None:
                stats.tier_changes += 1
                logger.info(
                    "tier change: %s %s -> %s", wallet[:10], changed_from, whale_stats.tier
                )
            recomputed += 1

        stats.wallets_recomputed = recomputed

        # --- Step 5: 對 A/B/C 鯨魚的新交易送推播 ---
        whales = repo.list_whales_by_tier("A", "B", "C")
        whale_tiers = {w["wallet_address"]: w["tier"] for w in whales}
        whale_dicts = {w["wallet_address"]: w for w in whales}

        # 從已存的 trades 表找出屬於鯨魚且在推播窗口內的交易
        since = (now - timedelta(hours=ALERT_TIME_WINDOW_HOURS)).isoformat()
        for wallet, tier in whale_tiers.items():
            # Tier C 的規格是「每日彙整推播」——這裡我們簡化為仍用單筆推播
            # 但透過提高門檻（notional > $100）過濾掉太小的 C 級訊號（以後再改成批次）
            trades_rows = _select_wallet_trades_since(repo, wallet, since)
            for row in trades_rows:
                notional = float(row["notional"] or 0)
                if tier == "C" and notional < 500:
                    continue  # C 級的小額交易先過濾（避免刷屏）

                alert: dict[str, Any] = {
                    "wallet_address": wallet,
                    "tx_hash": row["tx_hash"],
                    "event_index": row["event_index"],
                    "tier": tier,
                    "condition_id": row["condition_id"],
                    "market_question": market_question.get(row["condition_id"], ""),
                    "side": row["side"],
                    "outcome": row.get("outcome") or "",
                    "size": row["size"],
                    "price": row["price"],
                    "notional": notional,
                    "match_time": row["match_time"],
                }

                new_record = repo.record_alert({**alert, "telegram_sent": False})
                if not new_record:
                    stats.alerts_skipped_dup += 1
                    continue

                ok, _ = send_whale_alert(
                    tier=tier,
                    wallet_address=wallet,
                    market_question=market_question.get(row["condition_id"], "(未知市場)"),
                    market_category=market_category.get(row["condition_id"], ""),
                    side=row["side"],
                    outcome=row.get("outcome") or "",
                    price=row["price"],
                    size=row["size"],
                    notional=notional,
                    match_time=_parse_iso(row["match_time"]),
                    wallet_stats=whale_dicts[wallet],
                    dry_run=dry_run,
                )
                if ok:
                    stats.alerts_sent += 1
                # 更新 telegram_sent 標記由 record_alert 決定（INSERT 時為 False）
                # 若需要更新已推播狀態，可後續加 update method。Phase 1 先接受此限制。

    return stats


def _select_wallet_trades_since(repo: SqliteRepo, wallet: str, since_iso: str) -> list[dict]:
    """從 trades 表抓出屬於某錢包的交易，時間窗口內。

    trade.id 格式是 "tx_hash:event_index"（見 DataApiClient._normalize_trade_fields）。
    這裡拆回 tx_hash 與 event_index 給 alert 的 idempotency key 用。
    """
    conn = repo._connect()  # 直接用連線以避免新增過多 query method
    rows = conn.execute(
        """
        SELECT id, condition_id, token_id, price, size, notional, side, match_time
        FROM trades
        WHERE (taker_address=? OR maker_address=?)
          AND match_time >= ?
        ORDER BY match_time DESC
        """,
        (wallet, wallet, since_iso),
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        tx_hash, _, event_idx_s = (r["id"] or "").rpartition(":")
        try:
            event_idx = int(event_idx_s) if event_idx_s else 0
        except ValueError:
            event_idx = 0
        out.append(
            {
                "id": r["id"],
                "tx_hash": tx_hash or r["id"],
                "event_index": event_idx,
                "condition_id": r["condition_id"],
                "token_id": r["token_id"],
                "price": r["price"],
                "size": r["size"],
                "notional": r["notional"],
                "side": r["side"],
                "match_time": r["match_time"],
                "outcome": "",  # trades 表沒有 outcome，留白（未來可 JOIN tokens）
            }
        )
    return out


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="polymarket.pipeline", description="Polymarket Phase 1 pipeline")
    parser.add_argument("--loop", action="store_true", help="每 5 分鐘迴圈執行")
    parser.add_argument("--interval-sec", type=int, default=300, help="迴圈間隔秒數（預設 300 = 5 分鐘）")
    parser.add_argument("--dry-run", action="store_true", help="不真送 Telegram（列印訊息即可）")
    parser.add_argument("--markets-limit", type=int, default=ACTIVE_MARKETS_PER_RUN)
    parser.add_argument("--wallets-cap", type=int, default=WALLET_COMPUTE_CAP_PER_RUN,
                        help="每次最多重算幾個錢包（保護 API quota）")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.loop:
        stats = run_once(dry_run=args.dry_run, markets_limit=args.markets_limit, wallets_cap=args.wallets_cap)
        print(f"[pipeline] {stats.summary()}")
        if stats.errors:
            print("errors:")
            for e in stats.errors[:5]:
                print(f"  - {e}")
        return 0

    # Loop mode
    logger.info("pipeline loop mode: interval=%ds dry_run=%s", args.interval_sec, args.dry_run)
    while True:
        t0 = time.time()
        try:
            stats = run_once(dry_run=args.dry_run, markets_limit=args.markets_limit, wallets_cap=args.wallets_cap)
            logger.info("[pipeline] %s", stats.summary())
        except KeyboardInterrupt:
            logger.info("interrupted; exiting loop")
            return 0
        except Exception as exc:
            logger.exception("pipeline crash (will retry next cycle): %s", exc)
        elapsed = time.time() - t0
        sleep_for = max(0, args.interval_sec - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    sys.exit(main())
