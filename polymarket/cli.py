"""Polymarket CLI — Phase 0 基礎指令.

用法:
    python -m polymarket.cli fetch-markets --limit 20
    python -m polymarket.cli fetch-book --token-id <token_id>
    python -m polymarket.cli fetch-trades --condition-id <condition_id> --limit 100
    python -m polymarket.cli stats
"""

from __future__ import annotations

import argparse
import logging
import sys

from polymarket.clients.clob import ClobClient
from polymarket.clients.data_api import DataApiClient
from polymarket.clients.gamma import GammaClient
from polymarket.storage.repo import SqliteRepo

logger = logging.getLogger(__name__)


def _cmd_fetch_markets(args: argparse.Namespace) -> int:
    """從 CLOB 或 Gamma 抓市場。預設 CLOB，加 --active 用 Gamma 過濾活躍市場."""
    with SqliteRepo() as repo:
        if args.active:
            with GammaClient() as gamma, ClobClient() as clob:
                raw_list = gamma.list_markets(limit=args.limit, active=True, closed=False, order="volume24hr")
                # Gamma 回傳的是 dict，我們還是透過 CLOB 拉正式格式以確保 schema 一致
                count = 0
                for raw in raw_list:
                    cond = raw.get("conditionId") or raw.get("condition_id")
                    if not cond:
                        continue
                    try:
                        m = clob.get_market(cond)
                    except Exception as e:
                        logger.warning("skip %s: %s", cond, e)
                        continue
                    repo.upsert_market(m)
                    count += 1
                print(f"fetched {count} active markets; total_in_db={repo.count_markets()}")
        else:
            with ClobClient() as clob:
                markets, cursor = clob.get_markets()
                markets = markets[: args.limit]
                for m in markets:
                    repo.upsert_market(m)
                print(
                    f"fetched {len(markets)} markets; next_cursor={cursor!r}; "
                    f"total_in_db={repo.count_markets()}"
                )
    return 0


def _cmd_fetch_book(args: argparse.Namespace) -> int:
    with ClobClient() as clob, SqliteRepo() as repo:
        book = clob.get_book(args.token_id)
        repo.insert_book_snapshot(book)
        mid = book.mid_price()
        print(
            f"token_id={args.token_id} "
            f"bids={len(book.bids)} asks={len(book.asks)} "
            f"mid={mid} spread={book.spread()}"
        )
    return 0


def _cmd_fetch_trades(args: argparse.Namespace) -> int:
    """公開市場成交走 Data API（CLOB /trades 需認證且只返回用戶自己的交易）."""
    with DataApiClient() as data_api, SqliteRepo() as repo:
        trades = data_api.get_market_trades(market=args.condition_id, limit=args.limit)
        new_count, dup_count = repo.insert_trades(trades)
        print(
            f"fetched={len(trades)} new={new_count} dup={dup_count} "
            f"total_in_db={repo.count_trades(args.condition_id)}"
        )
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    with SqliteRepo() as repo:
        print(f"markets:         {repo.count_markets()}")
        print(f"book snapshots:  {repo.count_book_snapshots()}")
        print(f"trades:          {repo.count_trades()}")
    return 0


def _cmd_follower_stats(args: argparse.Namespace) -> int:
    """顯示 paper trading 帳本統計（每個 follower 分開）。"""
    from polymarket.followers import REGISTRY
    from polymarket.followers.paper_book import PaperBook

    with SqliteRepo() as repo:
        book = PaperBook(repo)

        # 先掃一次 resolve 新結算的市場
        try:
            resolve = book.scan_and_resolve()
            print(f"[resolve scan] resolved={resolve['resolved']} timeout={resolve['timeout']} open={resolve['still_open']}")
        except Exception as e:
            print(f"[resolve scan] failed: {e}")
        print()

        print("=== Per-follower summary ===")
        for name in list(REGISTRY.keys()) + [None]:
            label = name or "ALL"
            s = book.summary(follower_name=name)
            if s["total"] == 0:
                print(f"  {label:<20} (no trades)")
                continue
            print(
                f"  {label:<20} n={s['total']:<3} "
                f"open={s['open']:<3} closed={s['closed']:<3} "
                f"wr={s['win_rate']*100:>5.1f}% "
                f"pnl=${s['realized_pnl_usdc']:+,.2f} "
                f"pnl%={s['realized_pnl_pct']*100:+.2f}%"
            )

        # 近期決策摘要
        conn = repo._connect()
        print()
        print("=== Recent 10 follower decisions ===")
        for r in conn.execute(
            "SELECT decided_at, follower_name, source_tier, decision, reason, "
            "proposed_size_usdc "
            "FROM follower_decisions ORDER BY id DESC LIMIT 10"
        ):
            size = r["proposed_size_usdc"]
            size_s = f"${size:.0f}" if size is not None else "—"
            print(
                f"  {r['decided_at']} [{r['follower_name']}] {r['source_tier'] or '?'}"
                f" {r['decision']:<7} size={size_s}  {r['reason']}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polymarket.cli", description="Polymarket Phase 0 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("fetch-markets", help="抓市場列表並寫入 DB")
    m.add_argument("--limit", type=int, default=20)
    m.add_argument("--active", action="store_true", help="只抓活躍市場（Gamma 過濾）")
    m.set_defaults(func=_cmd_fetch_markets)

    b = sub.add_parser("fetch-book", help="抓某 token 的訂單簿快照")
    b.add_argument("--token-id", required=True)
    b.set_defaults(func=_cmd_fetch_book)

    t = sub.add_parser("fetch-trades", help="抓某市場的最近成交")
    t.add_argument("--condition-id", required=True)
    t.add_argument("--limit", type=int, default=100)
    t.set_defaults(func=_cmd_fetch_trades)

    s = sub.add_parser("stats", help="顯示本地 DB 計數")
    s.set_defaults(func=_cmd_stats)

    fs = sub.add_parser("follower-stats", help="Follower 與 paper trading 統計")
    fs.set_defaults(func=_cmd_follower_stats)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
