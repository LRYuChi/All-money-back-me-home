"""PaperBook — 紙上跟單帳本.

職責：
    1. 接收 FollowerDecision + AlertContext → 寫入 paper_trades（進場）
    2. 掃描 open paper_trades + 市場狀態 → auto-resolve 已結算市場
    3. 查詢 follower 表現統計（整體 PnL, win rate, per-tier, per-wallet 來源）

結算邏輯（v0）：
    - 市場 closed=1 且有 winner token 資訊 → 依此筆 side/outcome 判斷輸贏
    - BUY YES + YES won → profit = (1.0 - entry_price) × size
    - BUY YES + NO won → loss = entry_price × size
    - SELL YES (等同 BUY NO) 類推

    超過 90 天仍未結算 → exit_reason = 'timeout_90d'，以當前市場 mid_price 估值結算

不做事：
    - 不真實下單
    - 不調整 wallet balance (paper balance 由外部管理)
    - 不追蹤鯨魚是否也出場 (v1 再考慮「鯨魚先出 → 我也出」的鏡像退出)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


# 紙上初始資金 (配合 pre_registered.yaml 的 capital_ladder 第 1 階 $50, 但紙上模擬 $1000 方便累計統計)
PAPER_INITIAL_CAPITAL_USDC = 1000.0
TIMEOUT_DAYS = 90


@dataclass
class PaperTradeEntry:
    """準備寫入 paper_trades 的資料."""

    follower_name: str
    source_wallet: str
    source_tier: str
    condition_id: str
    token_id: str
    market_question: str
    market_category: str
    outcome: str
    side: str
    entry_price: float
    entry_size: float         # token 數量
    entry_notional: float     # USDC
    entry_time: datetime


class PaperBook:
    """純函式 / 純操作 DB 的抽象. 不持有可變狀態."""

    def __init__(self, repo: Any) -> None:  # SqliteRepo
        self._repo = repo

    # === 紀錄決策（含 skip/veto）===

    def record_decision(self, decision: Any, source: Any, paper_trade_id: int | None = None) -> int:
        """把 FollowerDecision 寫入 follower_decisions. 回傳 row id."""
        data = decision.to_db_dict(source, paper_trade_id=paper_trade_id)
        conn = self._repo._connect()
        cur = conn.execute(
            """
            INSERT INTO follower_decisions (
                follower_name, follower_version, decided_at,
                source_wallet, source_tx_hash, source_event_index, source_tier,
                decision, reason, proposed_stake_pct, proposed_size_usdc, paper_trade_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["follower_name"],
                data["follower_version"],
                data["decided_at"],
                data["source_wallet"],
                data["source_tx_hash"],
                data["source_event_index"],
                data["source_tier"],
                data["decision"],
                data["reason"],
                data["proposed_stake_pct"],
                data["proposed_size_usdc"],
                data["paper_trade_id"],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    # === 進場 ===

    def enter_paper_trade(self, entry: PaperTradeEntry) -> int:
        """寫入 paper_trades. 回傳 row id. 永不失敗（例外由呼叫方處理）."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._repo._connect()
        cur = conn.execute(
            """
            INSERT INTO paper_trades (
                follower_name, source_wallet, source_tier,
                condition_id, token_id, market_question, market_category,
                outcome, side,
                entry_price, entry_size, entry_notional, entry_time,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                entry.follower_name,
                entry.source_wallet,
                entry.source_tier,
                entry.condition_id,
                entry.token_id,
                entry.market_question,
                entry.market_category,
                entry.outcome,
                entry.side,
                entry.entry_price,
                entry.entry_size,
                entry.entry_notional,
                entry.entry_time.isoformat(),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)

    def has_open_position(self, follower_name: str, source_wallet: str, condition_id: str) -> bool:
        """避免 duplicate 進場：同 follower + 同來源錢包 + 同市場已開倉."""
        conn = self._repo._connect()
        row = conn.execute(
            "SELECT 1 FROM paper_trades "
            "WHERE follower_name=? AND source_wallet=? AND condition_id=? "
            "AND status='open' LIMIT 1",
            (follower_name, source_wallet, condition_id),
        ).fetchone()
        return row is not None

    # === 退場 / 結算 ===

    def scan_and_resolve(self, now: datetime | None = None) -> dict[str, int]:
        """掃描所有 open paper_trades, 對已結算市場自動結算.

        Returns:
            {"resolved": N, "timeout": M, "still_open": K}
        """
        now = now or datetime.now(timezone.utc)
        conn = self._repo._connect()
        stats = {"resolved": 0, "timeout": 0, "still_open": 0}

        rows = conn.execute(
            """
            SELECT pt.id, pt.condition_id, pt.token_id, pt.outcome, pt.side,
                   pt.entry_price, pt.entry_size, pt.entry_notional, pt.entry_time,
                   m.closed AS market_closed, t.price AS token_final_price, t.winner
            FROM paper_trades pt
            LEFT JOIN markets m ON m.condition_id = pt.condition_id
            LEFT JOIN tokens t  ON t.token_id = pt.token_id
            WHERE pt.status = 'open'
            """
        ).fetchall()

        for r in rows:
            entry_time = datetime.fromisoformat(r["entry_time"].replace("Z", "+00:00"))
            age_days = (now - entry_time).days

            market_closed = bool(r["market_closed"])
            token_final_price = r["token_final_price"]
            winner = r["winner"]

            exit_price = None
            exit_reason = None

            # (1) 市場已結算
            if market_closed and winner is not None:
                # winner=1 代表此 token 贏了
                if int(winner) == 1:
                    exit_price = 1.0
                    exit_reason = "market_resolved_win"
                else:
                    exit_price = 0.0
                    exit_reason = "market_resolved_loss"
            # (2) 超時
            elif age_days >= TIMEOUT_DAYS:
                # 以最後已知價格（若有）結算，否則以 entry 價（損益歸零）
                exit_price = float(token_final_price) if token_final_price is not None else r["entry_price"]
                exit_reason = "timeout_90d"

            if exit_price is None:
                stats["still_open"] += 1
                continue

            # 計算 PnL（對 buy 方向）
            # BUY YES @ entry_price, exit @ exit_price → PnL per token = (exit_price - entry_price)
            # SELL YES @ entry_price, exit @ exit_price → PnL per token = (entry_price - exit_price)
            if r["side"] == "BUY":
                pnl_per_token = exit_price - r["entry_price"]
            else:  # SELL
                pnl_per_token = r["entry_price"] - exit_price

            realized_pnl = pnl_per_token * r["entry_size"]
            realized_pnl_pct = realized_pnl / r["entry_notional"] if r["entry_notional"] > 0 else 0.0
            exit_notional = exit_price * r["entry_size"]

            conn.execute(
                """
                UPDATE paper_trades SET
                    exit_price=?, exit_size=?, exit_notional=?, exit_time=?,
                    exit_reason=?, realized_pnl=?, realized_pnl_pct=?,
                    status='closed', updated_at=?
                WHERE id=?
                """,
                (
                    exit_price, r["entry_size"], exit_notional, now.isoformat(),
                    exit_reason, realized_pnl, realized_pnl_pct, now.isoformat(), r["id"],
                ),
            )

            if exit_reason == "timeout_90d":
                stats["timeout"] += 1
            else:
                stats["resolved"] += 1

        conn.commit()
        return stats

    # === 統計 ===

    def summary(self, follower_name: str | None = None) -> dict[str, Any]:
        conn = self._repo._connect()
        where_clauses = []
        params: list[Any] = []
        if follower_name:
            where_clauses.append("follower_name = ?")
            params.append(follower_name)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 整體
        row = conn.execute(
            f"SELECT COUNT(*) AS n, "
            f"SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_n, "
            f"SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_n, "
            f"SUM(CASE WHEN status='closed' THEN realized_pnl ELSE 0 END) AS pnl, "
            f"SUM(CASE WHEN status='closed' AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
            f"SUM(CASE WHEN status='closed' THEN entry_notional ELSE 0 END) AS total_stake "
            f"FROM paper_trades {where_sql}",
            params,
        ).fetchone()

        n = int(row["n"] or 0)
        closed = int(row["closed_n"] or 0)
        wins = int(row["wins"] or 0)
        pnl = float(row["pnl"] or 0)
        stake = float(row["total_stake"] or 0)

        return {
            "total": n,
            "open": int(row["open_n"] or 0),
            "closed": closed,
            "wins": wins,
            "losses": closed - wins,
            "win_rate": wins / closed if closed else 0.0,
            "realized_pnl_usdc": pnl,
            "realized_pnl_pct": pnl / stake if stake else 0.0,
            "total_stake": stake,
        }
