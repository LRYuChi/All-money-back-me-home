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
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from polymarket.clients.clob import ClobClient
from polymarket.clients.data_api import DataApiClient
from polymarket.clients.gamma import GammaClient
from polymarket.config import load_pre_registered
from polymarket.features.whales import TIER_ORDER, classify_wallet
from polymarket.followers import REGISTRY as FOLLOWER_REGISTRY
from polymarket.followers.base import AlertContext, FollowerDecision
from polymarket.followers.paper_book import PAPER_INITIAL_CAPITAL_USDC, PaperBook, PaperTradeEntry
from polymarket.scanner.scan import scan_wallet
from polymarket.storage.repo import SqliteRepo
from polymarket.telegram import send_whale_alert

logger = logging.getLogger(__name__)


# === Pipeline 常數（此檔案的基礎設施參數，不是業務門檻） ===
# 1.5c.4 擴大掃描 — VPS 驗證後發現 1850 個候選錢包全 excluded，
# 代表真鯨魚沒進池子（sparse whales 分散在更多市場、更早活動）。
# 基礎設施參數調整，不動 §1 tier 門檻（pre-registration 不違反）。
ACTIVE_MARKETS_PER_RUN = 60       # 20 → 60：捕捉冷門但有高價值鯨魚的市場
TRADES_PER_MARKET = 50            # 保持；每市場 50 筆近期成交
WALLET_REFRESH_INTERVAL_HOURS = 24  # 錢包統計的快取壽命
WALLET_COMPUTE_CAP_PER_RUN = 60     # 30 → 60：配合更大候選池，每輪算更多錢包
ALERT_TIME_WINDOW_HOURS = 24        # 推播範圍：只對過去 N 小時的鯨魚交易推播
CANDIDATE_LOOKBACK_HOURS = 168      # 72 → 168 (7d)：捕捉每週才出手一次的鯨魚


@dataclass
class RunStats:
    markets_scanned: int = 0
    trades_ingested: int = 0
    unique_wallets_seen: int = 0
    wallets_recomputed: int = 0
    profiles_written: int = 0  # Phase 1.5+: append-only count
    tier_changes: int = 0
    alerts_sent: int = 0
    alerts_retried: int = 0  # E.1: Telegram 重試成功數
    alerts_retry_failed: int = 0  # E.1: Telegram 重試仍失敗數（下次再試）
    alerts_skipped_dup: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"markets={self.markets_scanned} trades={self.trades_ingested} "
            f"wallets={self.unique_wallets_seen} recomputed={self.wallets_recomputed} "
            f"profiles={self.profiles_written} tier_changes={self.tier_changes} "
            f"alerts_sent={self.alerts_sent} alerts_retried={self.alerts_retried} "
            f"alerts_retry_failed={self.alerts_retry_failed} "
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
        paper_book = PaperBook(repo)

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

        # --- Step 3: 找不重複錢包（擴窗 72h 以捕捉稀疏但高值鯨魚）---
        # 1.5b.1 observation: 真鯨魚如 0x204f72f3 可能 2-3 天才出手一次，
        # 24h 窗口會錯過他們。擴到 72h，排序仍是成交量優先。
        wallets = repo.recent_unique_wallets(hours=CANDIDATE_LOOKBACK_HOURS, limit=500)
        stats.unique_wallets_seen = len(wallets)

        # --- Step 4: 為新/過期錢包重新計算統計（同時寫 whale_stats + wallet_profiles） ---
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=WALLET_REFRESH_INTERVAL_HOURS)).isoformat()
        pre_reg = load_pre_registered()
        recomputed = 0
        profiles_written = 0

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

            # === Phase 1 contract: whale_stats（保留不動） ===
            whale_stats = classify_wallet(wallet, user_trades, positions, now=now)
            changed_from = repo.upsert_whale_stats(whale_stats.to_dict())
            if changed_from is not None:
                stats.tier_changes += 1
                logger.info(
                    "tier change: %s %s -> %s", wallet[:10], changed_from, whale_stats.tier
                )

            # === Phase 1.5+: wallet_profiles（append-only 時序） ===
            try:
                # 1.5b: 預取此錢包涉及的所有市場 category（讓 category_specialization 能用）
                wallet_conditions = list({p.condition_id for p in positions if p.condition_id})
                wallet_conditions.extend(t.market for t in user_trades if t.market)
                wallet_conditions = list(set(wallet_conditions))
                wallet_categories = repo.get_market_categories(wallet_conditions)
                # 合併今日剛掃的活躍市場 category（覆蓋率最高優先）
                wallet_categories.update(market_category)

                profile = scan_wallet(
                    wallet,
                    user_trades,
                    positions,
                    pre_reg=pre_reg,
                    market_categories=wallet_categories,
                    now=now,
                )
                repo.insert_wallet_profile(profile.to_db_dict())
                profiles_written += 1
            except Exception as exc:
                stats.errors.append(f"scan_wallet {wallet[:10]}: {exc}")
                logger.exception("scan_wallet failed for %s", wallet)

            recomputed += 1

        stats.wallets_recomputed = recomputed
        stats.profiles_written = profiles_written

        # --- Step 5: 對 A/B/C 鯨魚的新交易送推播 ---
        whales = repo.list_whales_by_tier("A", "B", "C")
        whale_tiers = {w["wallet_address"]: w["tier"] for w in whales}
        whale_dicts = {w["wallet_address"]: w for w in whales}

        # 1.5b: 取每個鯨魚最新的 wallet_profile 提取 specialist / consistency 資訊
        whale_profile_extras: dict[str, dict] = {}
        for w_addr in whale_tiers:
            wp = repo.get_latest_wallet_profile(w_addr)
            if wp is None:
                continue
            extras: dict = {}
            try:
                feats = json.loads(wp.get("features_json") or "{}")
                cs = feats.get("category_specialization", {}).get("value") or {}
                if cs:
                    extras["specialist_categories"] = cs.get("specialist_categories", [])
                    extras["primary_category"] = cs.get("primary_category")
                ts = feats.get("time_slice_consistency", {}).get("value") or {}
                if "consistent" in ts:
                    extras["is_consistent"] = ts.get("consistent")
            except (ValueError, TypeError, KeyError):
                pass
            if extras:
                whale_profile_extras[w_addr] = extras

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

                # 1.5b: 判斷此筆交易是否在錢包專長類別
                this_cat = market_category.get(row["condition_id"], "")
                extras = dict(whale_profile_extras.get(wallet, {}))
                if extras and this_cat:
                    extras["match_specialist"] = this_cat in (extras.get("specialist_categories") or [])

                # === 1.5b: Followers 決策層 ===
                # 把 alert 丟給每個 follower；可能產生 paper trade + 改變推播標記
                alert_ctx = AlertContext(
                    wallet_address=wallet,
                    tx_hash=alert["tx_hash"],
                    event_index=alert["event_index"],
                    tier=tier,
                    condition_id=row["condition_id"],
                    market_question=market_question.get(row["condition_id"], ""),
                    market_category=this_cat,
                    outcome=row.get("outcome") or "",
                    side=row["side"],
                    price=float(row["price"]),
                    size=float(row["size"]),
                    notional=notional,
                    match_time=_parse_iso(row["match_time"]) or datetime.now(timezone.utc),
                    wallet_profile=whale_dicts.get(wallet),
                )
                follow_summary = _run_followers(paper_book, alert_ctx, stats)
                if follow_summary:
                    extras["follow_summary"] = follow_summary

                ok, _ = send_whale_alert(
                    tier=tier,
                    wallet_address=wallet,
                    market_question=market_question.get(row["condition_id"], "(未知市場)"),
                    market_category=this_cat,
                    side=row["side"],
                    outcome=row.get("outcome") or "",
                    price=row["price"],
                    size=row["size"],
                    notional=notional,
                    match_time=_parse_iso(row["match_time"]),
                    wallet_stats=whale_dicts[wallet],
                    profile_extras=extras or None,
                    dry_run=dry_run,
                )
                if ok:
                    # E.1: 標記成功推播，避免下次重試
                    repo.mark_alert_sent(wallet, alert["tx_hash"], alert["event_index"])
                    stats.alerts_sent += 1

        # --- Step 5b: E.1 retry backlog — 重送 telegram_sent=0 且 <24h 的 alerts ---
        # 這些通常是上一輪 Telegram 失敗（429 限流 / 網路問題）留下的。
        # 本輪結束前再試一次；仍失敗則留待下次。超過 24h 不重試（避免推過期訊息）.
        if not dry_run:
            unsent = repo.get_unsent_alerts(hours=ALERT_TIME_WINDOW_HOURS, limit=100)
            for u in unsent:
                w = u["wallet_address"]
                # 跳過剛在本輪 Step 5 已嘗試發送的 alert（alerted_at 在過去 60 秒內）
                alerted_dt = _parse_iso(u["alerted_at"])
                if alerted_dt and (now - alerted_dt).total_seconds() < 60:
                    continue

                wallet_stats = repo.get_whale_stats(w) or {}
                ok_retry, _ = send_whale_alert(
                    tier=u["tier"],
                    wallet_address=w,
                    market_question=u["market_question"] or "(未知市場)",
                    market_category=u.get("market_category") or "",
                    side=u["side"],
                    outcome=u["outcome"] or "",
                    price=u["price"],
                    size=u["size"],
                    notional=u["notional"],
                    match_time=_parse_iso(u["match_time"]),
                    wallet_stats=wallet_stats,
                    dry_run=False,
                )
                if ok_retry:
                    repo.mark_alert_sent(w, u["tx_hash"], int(u["event_index"]))
                    stats.alerts_retried += 1
                else:
                    stats.alerts_retry_failed += 1

        # --- Step 6: 結算 paper trades (resolve when market closed) ---
        try:
            resolve_stats = paper_book.scan_and_resolve(now=now)
            stats.paper_trades_resolved = resolve_stats.get("resolved", 0)
            stats.paper_trades_timeout = resolve_stats.get("timeout", 0)
        except Exception as exc:
            stats.errors.append(f"paper_resolve: {exc}")
            logger.exception("paper_book.scan_and_resolve failed")

    return stats


def _run_followers(paper_book: PaperBook, alert_ctx: AlertContext, stats: RunStats) -> str | None:
    """把 alert 過一遍所有 followers, 寫 follower_decisions + 可能開 paper_trade.

    回傳一段簡短的文字摘要給 Telegram 訊息（若至少一個 follower 決定 follow）.
    """
    follow_summaries: list[str] = []
    for follower in FOLLOWER_REGISTRY.values():
        decision = follower.on_alert(alert_ctx)
        paper_trade_id: int | None = None

        if decision.is_follow():
            # 檢查是否已有同錢包+同市場的開倉 paper_trade
            if paper_book.has_open_position(follower.name, alert_ctx.wallet_address, alert_ctx.condition_id or ""):
                decision = FollowerDecision(
                    follower_name=follower.name,
                    follower_version=follower.version,
                    decision="skip",
                    reason="duplicate_position_already_open",
                    decided_at=decision.decided_at,
                )
            else:
                # 轉為 paper trade
                stake_pct = decision.proposed_stake_pct or 0.0
                notional = PAPER_INITIAL_CAPITAL_USDC * stake_pct
                try:
                    entry_size = notional / alert_ctx.price if alert_ctx.price > 0 else 0.0
                    entry = PaperTradeEntry(
                        follower_name=follower.name,
                        source_wallet=alert_ctx.wallet_address,
                        source_tier=alert_ctx.tier,
                        condition_id=alert_ctx.condition_id or "",
                        token_id="",  # 可後續 lookup
                        market_question=alert_ctx.market_question or "",
                        market_category=alert_ctx.market_category or "",
                        outcome=alert_ctx.outcome,
                        side=alert_ctx.side,
                        entry_price=alert_ctx.price,
                        entry_size=entry_size,
                        entry_notional=notional,
                        entry_time=alert_ctx.match_time,
                    )
                    paper_trade_id = paper_book.enter_paper_trade(entry)
                    stats.paper_trades_opened += 1
                    decision.proposed_size_usdc = notional
                    follow_summaries.append(f"{follower.name}:follow(${notional:.0f})")
                except Exception as exc:
                    stats.errors.append(f"paper_enter {follower.name}: {exc}")
                    logger.exception("paper_enter failed")
                    decision = FollowerDecision(
                        follower_name=follower.name,
                        follower_version=follower.version,
                        decision="skip",
                        reason=f"paper_enter_error:{exc}",
                        decided_at=decision.decided_at,
                    )

        # 紀錄決策（無論 follow / skip）
        try:
            paper_book.record_decision(decision, alert_ctx, paper_trade_id=paper_trade_id)
        except Exception as exc:
            stats.errors.append(f"record_decision: {exc}")

        if decision.is_follow():
            stats.follows += 1
        else:
            stats.follow_skips += 1

    return " | ".join(follow_summaries) if follow_summaries else None


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
