"""Polymarket storage repository.

Phase 0: SQLite（本地 data/polymarket.db）。
Phase 1+: Supabase PostgreSQL（同一份 schema.sql 相容）。

寫入為 UPSERT（markets、tokens）或 INSERT（order_book_snapshots、trades，idempotent on PK）。
查詢僅提供 Phase 0 需要的最小集合，後續階段再擴充。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from polymarket.config import SQLITE_PATH
from polymarket.models import Market, OrderBook, Trade

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(v: Decimal | float | int | None) -> float | None:
    return None if v is None else float(v)


class SqliteRepo:
    """本地 SQLite repository — Phase 0 預設."""

    def __init__(self, db_path: Path | str = SQLITE_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SqliteRepo":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        conn = self._connect()
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()

    # === Markets ===

    def upsert_market(self, market: Market) -> None:
        now = _now_iso()
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO markets (
                condition_id, question, market_slug, category, end_date_iso,
                active, closed, minimum_order_size, minimum_tick_size,
                maker_base_fee, taker_base_fee, raw_json, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
                question=excluded.question,
                market_slug=excluded.market_slug,
                category=excluded.category,
                end_date_iso=excluded.end_date_iso,
                active=excluded.active,
                closed=excluded.closed,
                minimum_order_size=excluded.minimum_order_size,
                minimum_tick_size=excluded.minimum_tick_size,
                maker_base_fee=excluded.maker_base_fee,
                taker_base_fee=excluded.taker_base_fee,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                market.condition_id,
                market.question,
                market.market_slug,
                market.category,
                market.end_date_iso.isoformat() if market.end_date_iso else None,
                int(market.active),
                int(market.closed),
                market.minimum_order_size,
                market.minimum_tick_size,
                market.maker_base_fee,
                market.taker_base_fee,
                market.model_dump_json(),
                now,
                now,
            ),
        )
        for tok in market.tokens:
            conn.execute(
                """
                INSERT INTO tokens (token_id, condition_id, outcome, price, winner, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    outcome=excluded.outcome,
                    price=excluded.price,
                    winner=excluded.winner,
                    fetched_at=excluded.fetched_at
                """,
                (
                    tok.token_id,
                    market.condition_id,
                    tok.outcome,
                    tok.price,
                    None if tok.winner is None else int(tok.winner),
                    now,
                ),
            )
        conn.commit()

    def count_markets(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) AS c FROM markets").fetchone()
        return int(row["c"])

    # === Order book snapshots ===

    def insert_book_snapshot(self, book: OrderBook) -> None:
        now = _now_iso()
        bb = book.best_bid()
        ba = book.best_ask()
        mid = book.mid_price()
        sp = book.spread()
        bid_depth = sum((lv.size for lv in sorted(book.bids, key=lambda x: -x.price)[:10]), Decimal(0))
        ask_depth = sum((lv.size for lv in sorted(book.asks, key=lambda x: x.price)[:10]), Decimal(0))

        conn = self._connect()
        conn.execute(
            """
            INSERT INTO order_book_snapshots (
                condition_id, token_id, hash, best_bid, best_ask, mid_price, spread,
                bid_depth_top10, ask_depth_top10, raw_json, snapshot_at, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book.market,
                book.asset_id,
                book.hash,
                _to_float(bb.price) if bb else None,
                _to_float(ba.price) if ba else None,
                _to_float(mid),
                _to_float(sp),
                _to_float(bid_depth),
                _to_float(ask_depth),
                book.model_dump_json(),
                book.timestamp.isoformat(),
                now,
            ),
        )
        conn.commit()

    def count_book_snapshots(self, token_id: str | None = None) -> int:
        conn = self._connect()
        if token_id:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM order_book_snapshots WHERE token_id=?",
                (token_id,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM order_book_snapshots").fetchone()
        return int(row["c"])

    # === Trades ===

    def insert_trade(self, trade: Trade) -> bool:
        """寫入單筆 trade。回傳 True=新寫入, False=已存在（idempotent）."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO trades (
                    id, condition_id, token_id, price, size, notional, side, status,
                    maker_address, taker_address, match_time, raw_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.id,
                    trade.market,
                    trade.asset_id or None,
                    float(trade.price),
                    float(trade.size),
                    float(trade.notional_usdc()),
                    trade.side,
                    trade.status,
                    trade.maker_address,
                    trade.taker_address,
                    trade.match_time.isoformat(),
                    json.dumps(trade.model_dump(mode="json"), ensure_ascii=False),
                    _now_iso(),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # PK conflict = 已存在
            return False

    def insert_trades(self, trades: list[Trade]) -> tuple[int, int]:
        """批次寫入，回傳 (新增, 已存在)."""
        new_count, dup_count = 0, 0
        for t in trades:
            if self.insert_trade(t):
                new_count += 1
            else:
                dup_count += 1
        return new_count, dup_count

    def count_trades(self, condition_id: str | None = None) -> int:
        conn = self._connect()
        if condition_id:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM trades WHERE condition_id=?",
                (condition_id,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM trades").fetchone()
        return int(row["c"])

    def recent_unique_wallets(self, hours: int = 24, limit: int = 500) -> list[str]:
        """從最近 trades 中取出錢包地址，依成交總額降序排序.

        鯨魚比高頻 bot 更可能是高總額錢包，這個排序讓每次 pipeline 都優先處理
        最有可能成為 whale 的候選。
        """
        conn = self._connect()
        cutoff = f"-{hours} hours"
        rows = conn.execute(
            """
            SELECT wallet, SUM(notional) AS total_notional
            FROM (
                SELECT taker_address AS wallet, notional FROM trades
                WHERE match_time >= datetime('now', ?) AND taker_address != ''
                UNION ALL
                SELECT maker_address AS wallet, notional FROM trades
                WHERE match_time >= datetime('now', ?) AND maker_address != ''
            )
            GROUP BY wallet
            ORDER BY total_notional DESC
            LIMIT ?
            """,
            (cutoff, cutoff, limit),
        ).fetchall()
        return [r["wallet"] for r in rows if r["wallet"]]

    # === Whale stats ===

    def upsert_whale_stats(self, stats: dict[str, Any]) -> str | None:
        """寫入錢包統計，並在 tier 變動時記錄 history。回傳 (from_tier) 若有變動，否則 None."""
        import json as _json

        now = _now_iso()
        conn = self._connect()
        prev = conn.execute(
            "SELECT tier FROM whale_stats WHERE wallet_address=?",
            (stats["wallet_address"],),
        ).fetchone()
        prev_tier = prev["tier"] if prev else None

        conn.execute(
            """
            INSERT INTO whale_stats (
                wallet_address, tier, trade_count_90d, win_rate, cumulative_pnl,
                avg_trade_size, segment_win_rates, stability_pass, resolved_count,
                last_trade_at, last_computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET
                tier=excluded.tier,
                trade_count_90d=excluded.trade_count_90d,
                win_rate=excluded.win_rate,
                cumulative_pnl=excluded.cumulative_pnl,
                avg_trade_size=excluded.avg_trade_size,
                segment_win_rates=excluded.segment_win_rates,
                stability_pass=excluded.stability_pass,
                resolved_count=excluded.resolved_count,
                last_trade_at=excluded.last_trade_at,
                last_computed_at=excluded.last_computed_at
            """,
            (
                stats["wallet_address"],
                stats["tier"],
                stats["trade_count_90d"],
                stats["win_rate"],
                stats["cumulative_pnl"],
                stats["avg_trade_size"],
                _json.dumps(stats.get("segment_win_rates", [])),
                int(stats.get("stability_pass", False)),
                stats.get("resolved_count", 0),
                stats.get("last_trade_at"),
                now,
            ),
        )

        new_tier = stats["tier"]
        if prev_tier != new_tier:
            reason = _tier_change_reason(prev_tier, new_tier, stats.get("stability_pass", False))
            conn.execute(
                """
                INSERT INTO whale_tier_history (wallet_address, from_tier, to_tier, changed_at, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (stats["wallet_address"], prev_tier, new_tier, now, reason),
            )
        conn.commit()
        return prev_tier if prev_tier != new_tier else None

    def get_whale_stats(self, wallet_address: str) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM whale_stats WHERE wallet_address=?", (wallet_address,)
        ).fetchone()
        return dict(row) if row else None

    def list_whales_by_tier(self, *tiers: str) -> list[dict]:
        if not tiers:
            tiers = ("A", "B", "C")
        conn = self._connect()
        placeholders = ",".join("?" * len(tiers))
        rows = conn.execute(
            f"SELECT * FROM whale_stats WHERE tier IN ({placeholders}) ORDER BY tier, cumulative_pnl DESC",
            tiers,
        ).fetchall()
        return [dict(r) for r in rows]

    def count_whales(self, tier: str | None = None) -> int:
        conn = self._connect()
        if tier:
            row = conn.execute("SELECT COUNT(*) AS c FROM whale_stats WHERE tier=?", (tier,)).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM whale_stats").fetchone()
        return int(row["c"])

    # === Whale trade alerts (idempotent) ===

    def record_alert(self, alert: dict[str, Any]) -> bool:
        """記錄一筆鯨魚推播。回傳 True=新記錄, False=已存在（不重複推播）."""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO whale_trade_alerts (
                    wallet_address, tx_hash, event_index, tier, condition_id,
                    market_question, side, outcome, size, price, notional,
                    match_time, alerted_at, telegram_sent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert["wallet_address"],
                    alert["tx_hash"],
                    alert["event_index"],
                    alert["tier"],
                    alert.get("condition_id"),
                    alert.get("market_question"),
                    alert.get("side"),
                    alert.get("outcome"),
                    alert.get("size"),
                    alert.get("price"),
                    alert.get("notional"),
                    alert.get("match_time"),
                    _now_iso(),
                    int(alert.get("telegram_sent", False)),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def count_alerts(self, wallet_address: str | None = None) -> int:
        conn = self._connect()
        if wallet_address:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM whale_trade_alerts WHERE wallet_address=?",
                (wallet_address,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM whale_trade_alerts").fetchone()
        return int(row["c"])

    def mark_alert_sent(
        self, wallet_address: str, tx_hash: str, event_index: int
    ) -> bool:
        """標記某筆 alert 為已成功推播。回傳 True=有更新，False=找不到.

        Pipeline 在 Telegram 送成功後呼叫；送失敗時不呼叫（留 0 供下次重試）.
        """
        conn = self._connect()
        cur = conn.execute(
            """
            UPDATE whale_trade_alerts SET telegram_sent = 1
            WHERE wallet_address=? AND tx_hash=? AND event_index=?
            """,
            (wallet_address, tx_hash, event_index),
        )
        conn.commit()
        return cur.rowcount > 0

    def get_unsent_alerts(
        self, *, hours: int = 24, limit: int = 100
    ) -> list[dict[str, Any]]:
        """取回 telegram_sent=0 且落在過去 N 小時內的 alerts，供 pipeline 重試.

        僅回傳 N 小時內的 alert；更久的不重試（避免推播過期資訊）。
        結果含市場 category（JOIN markets）便於重新格式化訊息。

        注意：alerted_at 以 Python isoformat（含 T 與 +00:00）存入，
        SQLite 內建 `datetime()` 使用空格分隔，直接比較會因格式差異失效。
        改用 `strftime('%Y-%m-%dT%H:%M:%S', ...)` 產生匹配前綴。
        """
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT
                a.wallet_address, a.tx_hash, a.event_index, a.tier, a.condition_id,
                a.market_question, a.side, a.outcome, a.size, a.price, a.notional,
                a.match_time, a.alerted_at,
                m.category AS market_category
            FROM whale_trade_alerts a
            LEFT JOIN markets m ON m.condition_id = a.condition_id
            WHERE a.telegram_sent = 0
              AND a.alerted_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
            ORDER BY a.alerted_at ASC
            LIMIT ?
            """,
            (f"-{hours} hours", limit),
        ).fetchall()
        return [dict(r) for r in rows]


    # === wallet_profiles (Phase 1.5+, append-only time-series) ===

    def insert_wallet_profile(self, profile_dict: dict[str, Any]) -> int:
        """Append 一筆 wallet_profile 紀錄（永不覆蓋）.

        Args:
            profile_dict: 來自 WalletProfile.to_db_dict()
        Returns:
            新插入的 row id
        """
        conn = self._connect()
        cur = conn.execute(
            """
            INSERT INTO wallet_profiles (
                wallet_address, scanner_version, scanned_at,
                passed_coarse_filter, coarse_filter_reasons,
                trade_count_90d, resolved_count, cumulative_pnl, avg_trade_size, win_rate,
                features_json, tier, archetypes_json, risk_flags_json,
                sample_size_warning, raw_features_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_dict["wallet_address"],
                profile_dict["scanner_version"],
                profile_dict["scanned_at"],
                profile_dict["passed_coarse_filter"],
                profile_dict["coarse_filter_reasons"],
                profile_dict.get("trade_count_90d"),
                profile_dict.get("resolved_count"),
                profile_dict.get("cumulative_pnl"),
                profile_dict.get("avg_trade_size"),
                profile_dict.get("win_rate"),
                profile_dict["features_json"],
                profile_dict["tier"],
                profile_dict["archetypes_json"],
                profile_dict["risk_flags_json"],
                profile_dict["sample_size_warning"],
                profile_dict["raw_features_json"],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    def get_latest_wallet_profile(
        self,
        wallet_address: str,
        scanner_version: str | None = None,
    ) -> dict | None:
        """取得錢包的最新 profile。可選指定 scanner_version."""
        conn = self._connect()
        if scanner_version:
            row = conn.execute(
                """
                SELECT * FROM wallet_profiles
                WHERE wallet_address = ? AND scanner_version = ?
                ORDER BY scanned_at DESC LIMIT 1
                """,
                (wallet_address, scanner_version),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM wallet_profiles
                WHERE wallet_address = ?
                ORDER BY scanned_at DESC LIMIT 1
                """,
                (wallet_address,),
            ).fetchone()
        return dict(row) if row else None

    def list_latest_wallet_profiles(
        self,
        *,
        tier: str | list[str] | None = None,
        scanner_version: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """每個錢包只取最新一筆。可選 tier 過濾與 version 過濾."""
        conn = self._connect()
        # 子查詢取每個 wallet 的最新 id
        sql = """
            SELECT wp.* FROM wallet_profiles wp
            INNER JOIN (
                SELECT wallet_address, MAX(scanned_at) AS latest
                FROM wallet_profiles
                {version_filter}
                GROUP BY wallet_address
            ) latest ON wp.wallet_address = latest.wallet_address
                    AND wp.scanned_at = latest.latest
            {tier_filter}
            ORDER BY wp.cumulative_pnl DESC
            LIMIT ?
        """
        version_filter = ""
        tier_filter = ""
        params: list[Any] = []

        if scanner_version:
            version_filter = "WHERE scanner_version = ?"
            params.append(scanner_version)

        if tier:
            tiers = [tier] if isinstance(tier, str) else list(tier)
            placeholders = ",".join("?" * len(tiers))
            tier_filter = f"WHERE wp.tier IN ({placeholders})"
            params.extend(tiers)

        params.append(limit)
        rows = conn.execute(
            sql.format(version_filter=version_filter, tier_filter=tier_filter), params
        ).fetchall()
        return [dict(r) for r in rows]

    def list_wallet_profile_history(
        self, wallet_address: str, *, limit: int = 30
    ) -> list[dict]:
        """單一錢包的 profile 時序（新→舊）."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM wallet_profiles WHERE wallet_address=? "
            "ORDER BY scanned_at DESC LIMIT ?",
            (wallet_address, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_wallet_profiles(self, *, scanner_version: str | None = None) -> int:
        conn = self._connect()
        if scanner_version:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM wallet_profiles WHERE scanner_version=?",
                (scanner_version,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM wallet_profiles").fetchone()
        return int(row["c"])

    # === Markets metadata helpers (Phase 1.5b: category lookup for scanner) ===

    def get_market_categories(self, condition_ids: list[str]) -> dict[str, str]:
        """Bulk lookup: condition_id → category. Missing IDs are absent from the dict.

        Used by the scanner to pre-build market_categories for category_specialization
        feature without N+1 queries.
        """
        if not condition_ids:
            return {}
        conn = self._connect()
        # SQLite has a default limit on parameters per query (~999). Chunk to be safe.
        result: dict[str, str] = {}
        chunk_size = 500
        for i in range(0, len(condition_ids), chunk_size):
            chunk = condition_ids[i : i + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT condition_id, category FROM markets WHERE condition_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                if r["category"]:
                    result[r["condition_id"]] = r["category"]
        return result


def _tier_change_reason(prev: str | None, new: str, stability_pass: bool) -> str:
    if prev is None:
        return "initial"
    tier_rank = {"A": 3, "B": 2, "C": 1, "volatile": 0, "excluded": -1}
    p = tier_rank.get(prev, -2)
    n = tier_rank.get(new, -2)
    if new == "volatile" and not stability_pass:
        return "stability_fail"
    if n > p:
        return "promoted"
    return "demoted"
