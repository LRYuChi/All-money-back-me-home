"""Trade Store — persists paper trades and backtest results to Supabase.

Falls back to JSON file if Supabase is not configured, maintaining
backward compatibility with the existing hourly_scanner flow.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .supabase_client import get_supabase


_DATA_DIR = Path(os.environ["DATA_DIR"]) if "DATA_DIR" in os.environ else Path(__file__).resolve().parents[4] / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE = _DATA_DIR / "paper_trades.json"


class TradeStore:
    """Unified trade storage: Supabase-first, JSON-fallback."""

    def __init__(self) -> None:
        self._sb = get_supabase()

    @property
    def has_supabase(self) -> bool:
        return self._sb is not None

    # ------------------------------------------------------------------
    # Paper Trades
    # ------------------------------------------------------------------

    def save_open_trade(self, trade: dict) -> dict:
        """Save a newly opened paper trade."""
        if self._sb:
            row = {
                "symbol": trade["symbol"],
                "direction": trade["direction"],
                "strategy": trade.get("strategy", "bb_squeeze"),
                "status": "open",
                "entry_price": trade["entry_price"],
                "entry_time": trade.get("entry_time", datetime.now(timezone.utc).isoformat()),
                "stop_loss": trade.get("stop_loss"),
                "take_profit_levels": json.dumps(trade.get("take_profit_levels", [])),
                "position_size_usd": trade["position_size_usd"],
                "leverage": trade.get("leverage", 1),
                "confidence": trade.get("confidence", 0),
                "reason": trade.get("reason", ""),
                "source": trade.get("source", "scanner"),
            }
            result = self._sb.table("paper_trades").insert(row).execute()
            if result.data:
                trade["db_id"] = result.data[0]["id"]
            return trade

        # JSON fallback — handled by caller (hourly_scanner state)
        return trade

    def close_trade(self, trade_id: str, exit_data: dict) -> None:
        """Update a paper trade with exit data."""
        if self._sb:
            row = {
                "status": "closed",
                "exit_price": exit_data["exit_price"],
                "exit_time": exit_data.get("exit_time", datetime.now(timezone.utc).isoformat()),
                "exit_reason": exit_data["exit_reason"],
                "pnl_usd": exit_data["pnl_usd"],
                "pnl_pct": exit_data["pnl_pct"],
                "commission_paid": exit_data.get("commission_paid", 0),
                "duration_bars": exit_data.get("duration_bars"),
                "r_multiple": exit_data.get("r_multiple"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._sb.table("paper_trades").update(row).eq("id", trade_id).execute()

    def get_open_positions(self, source: str = "scanner") -> list[dict]:
        """Get all open paper trade positions."""
        if self._sb:
            result = (
                self._sb.table("paper_trades")
                .select("*")
                .eq("status", "open")
                .eq("source", source)
                .order("entry_time", desc=True)
                .execute()
            )
            return result.data or []

        return self._load_json_state().get("open_positions", [])

    def get_closed_trades(
        self, source: str = "scanner", limit: int = 100
    ) -> list[dict]:
        """Get closed paper trades."""
        if self._sb:
            result = (
                self._sb.table("paper_trades")
                .select("*")
                .eq("status", "closed")
                .eq("source", source)
                .order("exit_time", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []

        return self._load_json_state().get("closed_trades", [])

    def get_all_trades(self, source: str = "scanner") -> dict:
        """Get full paper trade state (for API compatibility)."""
        if self._sb:
            open_pos = self.get_open_positions(source)
            closed = self.get_closed_trades(source)
            snapshot = self.get_latest_capital_snapshot(source)

            initial_capital = float(os.environ.get("INITIAL_CAPITAL", "300.0"))
            # If no snapshot exists yet, capital = initial_capital (not 0)
            capital = snapshot.get("capital", initial_capital) if snapshot else initial_capital

            return {
                "capital": capital,
                "initial_capital": initial_capital,
                "open_positions": open_pos,
                "closed_trades": closed,
                "last_updated": snapshot.get("ts") if snapshot else None,
            }

        return self._load_json_state()

    # ------------------------------------------------------------------
    # Capital Snapshots
    # ------------------------------------------------------------------

    def save_capital_snapshot(
        self,
        capital: float,
        equity: float,
        unrealized_pnl: float = 0.0,
        open_positions: int = 0,
        source: str = "scanner",
    ) -> None:
        """Save a capital/equity snapshot."""
        if self._sb:
            self._sb.table("capital_snapshots").insert({
                "source": source,
                "capital": capital,
                "equity": equity,
                "unrealized_pnl": unrealized_pnl,
                "open_positions": open_positions,
                "ts": datetime.now(timezone.utc).isoformat(),
            }).execute()

    def get_latest_capital_snapshot(self, source: str = "scanner") -> dict | None:
        """Get the most recent capital snapshot."""
        if self._sb:
            result = (
                self._sb.table("capital_snapshots")
                .select("*")
                .eq("source", source)
                .order("ts", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        return None

    def get_equity_curve(
        self, source: str = "scanner", limit: int = 1000
    ) -> list[dict]:
        """Get equity curve data points."""
        if self._sb:
            result = (
                self._sb.table("capital_snapshots")
                .select("ts, capital, equity, unrealized_pnl")
                .eq("source", source)
                .order("ts", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        return []

    # ------------------------------------------------------------------
    # Backtest Results
    # ------------------------------------------------------------------

    def save_backtest_run(self, result: dict, config: dict) -> str | None:
        """Save a backtest run result. Returns the run ID."""
        if not self._sb:
            return None

        row = {
            "strategy": result.get("strategy", "B_SQUEEZE"),
            "symbol": result.get("symbol", config.get("symbol", "")),
            "timeframe": result.get("period", config.get("timeframe", "")),
            "initial_capital": config.get("initial_capital", 10000),
            "commission_rate": config.get("commission_rate", 0.001),
            "total_trades": result.get("total_trades", 0),
            "win_rate": result.get("win_rate", 0),
            "profit_factor": result.get("profit_factor", 0),
            "sharpe_ratio": result.get("sharpe_ratio", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "total_return": result.get("total_return", 0),
            "calmar_ratio": result.get("calmar_ratio", 0),
            "avg_r_multiple": result.get("avg_r_multiple", 0),
            "avg_trade_duration_bars": result.get("avg_trade_duration_bars", 0),
            "total_bars": len(result.get("equity_curve", [])),
            "result_json": json.dumps({
                "equity_curve": result.get("equity_curve", []),
                "trades": result.get("trades", []),
            }, default=str),
        }

        resp = self._sb.table("backtest_runs").insert(row).execute()
        if resp.data:
            return resp.data[0]["id"]
        return None

    def save_walk_forward_run(
        self, summary: dict, config: dict
    ) -> str | None:
        """Save a walk-forward validation result."""
        if not self._sb:
            return None

        row = {
            "strategy": config.get("strategy", "B_SQUEEZE"),
            "symbol": config.get("symbol", ""),
            "timeframe": config.get("timeframe", ""),
            "initial_capital": config.get("initial_capital", 10000),
            "commission_rate": config.get("commission_rate", 0.001),
            "total_trades": summary.get("total_trades", 0),
            "win_rate": summary.get("avg_win_rate", 0),
            "sharpe_ratio": summary.get("avg_sharpe", 0),
            "max_drawdown": summary.get("avg_max_drawdown", 0),
            "total_return": summary.get("avg_return", 0),
            "is_walk_forward": True,
            "walk_forward_summary": json.dumps(summary, default=str),
        }

        resp = self._sb.table("backtest_runs").insert(row).execute()
        if resp.data:
            return resp.data[0]["id"]
        return None

    def get_backtest_runs(
        self, symbol: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Get recent backtest runs."""
        if not self._sb:
            return []

        query = (
            self._sb.table("backtest_runs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if symbol:
            query = query.eq("symbol", symbol)

        result = query.execute()
        return result.data or []

    # ------------------------------------------------------------------
    # JSON fallback helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json_state() -> dict:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "capital": 0,
            "initial_capital": 0,
            "open_positions": [],
            "closed_trades": [],
            "scan_history": [],
        }
