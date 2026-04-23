"""一次性策略稽核腳本 — 從 freqtrade dryrun DB 抽出所有關鍵指標.

Usage:
    python scripts/audit_trades.py [path_to_trades.sqlite]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, pstdev


def main(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    closed = list(
        conn.execute(
            "SELECT id, pair, open_date, close_date, exit_reason, enter_tag, "
            "amount, stake_amount, open_rate, close_rate, leverage, "
            "stop_loss_pct, initial_stop_loss_pct, max_rate, min_rate, "
            "close_profit, close_profit_abs, fee_open, fee_close, "
            "is_short "
            "FROM trades WHERE is_open=0 ORDER BY close_date"
        )
    )
    open_trades = list(
        conn.execute("SELECT id, pair, open_date, enter_tag, amount, open_rate, leverage, is_short FROM trades WHERE is_open=1")
    )
    n = len(closed)
    if n == 0:
        return {"error": "no closed trades"}

    profits_pct = [t["close_profit"] for t in closed]
    profits_abs = [t["close_profit_abs"] for t in closed]
    wins = [p for p in profits_pct if p > 0]
    losses = [p for p in profits_pct if p < 0]

    # === Cumulative equity curve & drawdown ===
    initial_capital = 1000.0  # default per heartbeat.py
    eq = [initial_capital]
    timestamps = []
    for t in closed:
        eq.append(eq[-1] + t["close_profit_abs"])
        timestamps.append(t["close_date"])
    peak = eq[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    dd_curve = []
    for x in eq:
        peak = max(peak, x)
        dd = peak - x
        dd_pct = dd / peak * 100 if peak > 0 else 0
        dd_curve.append(dd_pct)
        max_dd_abs = max(max_dd_abs, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)

    # Consecutive losses
    cur = 0
    max_consec_loss = 0
    cur_w = 0
    max_consec_win = 0
    for p in profits_pct:
        if p < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
            cur_w = 0
        elif p > 0:
            cur_w += 1
            max_consec_win = max(max_consec_win, cur_w)
            cur = 0
        else:
            cur = 0
            cur_w = 0

    # By exit reason
    by_exit: dict[str, list] = defaultdict(list)
    for t in closed:
        by_exit[t["exit_reason"] or "unknown"].append(t)
    exit_summary = {}
    for k, ts in by_exit.items():
        ps = [t["close_profit"] for t in ts]
        abs_ps = [t["close_profit_abs"] for t in ts]
        exit_summary[k] = {
            "n": len(ts),
            "pnl_abs": sum(abs_ps),
            "avg_pct": mean(ps) * 100 if ps else 0,
            "win_rate": sum(1 for p in ps if p > 0) / len(ps) * 100,
        }

    # By pair
    by_pair: dict[str, list] = defaultdict(list)
    for t in closed:
        by_pair[t["pair"]].append(t)
    pair_summary = {}
    for k, ts in by_pair.items():
        ps = [t["close_profit"] for t in ts]
        abs_ps = [t["close_profit_abs"] for t in ts]
        wins_p = sum(1 for p in ps if p > 0)
        pair_summary[k] = {
            "n": len(ts),
            "pnl_abs": sum(abs_ps),
            "avg_pct": mean(ps) * 100 if ps else 0,
            "win_rate": wins_p / len(ps) * 100,
            "best": max(ps) * 100,
            "worst": min(ps) * 100,
        }

    # By enter tag
    by_tag: dict[str, list] = defaultdict(list)
    for t in closed:
        by_tag[t["enter_tag"] or "(none)"].append(t)
    tag_summary = {}
    for k, ts in by_tag.items():
        ps = [t["close_profit"] for t in ts]
        abs_ps = [t["close_profit_abs"] for t in ts]
        tag_summary[k] = {
            "n": len(ts),
            "pnl_abs": sum(abs_ps),
            "avg_pct": mean(ps) * 100 if ps else 0,
            "win_rate": sum(1 for p in ps if p > 0) / len(ps) * 100,
        }

    # Hold time distribution (minutes)
    hold_minutes = []
    for t in closed:
        try:
            o = datetime.fromisoformat(t["open_date"].split(".")[0])
            c = datetime.fromisoformat(t["close_date"].split(".")[0])
            hold_minutes.append((c - o).total_seconds() / 60.0)
        except Exception:
            pass

    # Stop-loss analysis: how often did stop fire vs design
    sl_trades = [t for t in closed if t["exit_reason"] in ("stoploss", "stoploss_on_exchange")]
    sl_pct_distribution = [t["stop_loss_pct"] * 100 if t["stop_loss_pct"] else 0 for t in sl_trades]
    initial_sl_distribution = [t["initial_stop_loss_pct"] * 100 if t["initial_stop_loss_pct"] else 0 for t in closed]

    # MFE / MAE proxy via max_rate / min_rate vs open
    mfe_vals = []
    mae_vals = []
    for t in closed:
        if not t["max_rate"] or not t["open_rate"]:
            continue
        if t["is_short"]:
            mfe = (t["open_rate"] - t["min_rate"]) / t["open_rate"] * 100 if t["min_rate"] else 0
            mae = (t["max_rate"] - t["open_rate"]) / t["open_rate"] * 100
        else:
            mfe = (t["max_rate"] - t["open_rate"]) / t["open_rate"] * 100
            mae = (t["open_rate"] - t["min_rate"]) / t["open_rate"] * 100 if t["min_rate"] else 0
        mfe_vals.append(mfe)
        mae_vals.append(mae)

    # Long vs short
    long_t = [t for t in closed if not t["is_short"]]
    short_t = [t for t in closed if t["is_short"]]

    def side_summary(ts):
        if not ts:
            return None
        ps = [t["close_profit"] for t in ts]
        return {
            "n": len(ts),
            "pnl_abs": sum(t["close_profit_abs"] for t in ts),
            "win_rate": sum(1 for p in ps if p > 0) / len(ps) * 100,
            "avg": mean(ps) * 100,
        }

    avg_win = mean(wins) * 100 if wins else 0
    avg_loss = mean(losses) * 100 if losses else 0
    rr = abs(avg_win / avg_loss) if avg_loss else 0
    win_rate = len(wins) / n * 100
    breakeven_wr = abs(avg_loss) / (abs(avg_loss) + avg_win) * 100 if (avg_loss and avg_win) else 0
    expected_value = (len(wins) / n * (avg_win or 0)) + (len(losses) / n * (avg_loss or 0))

    # Time range
    first_date = closed[0]["open_date"]
    last_date = closed[-1]["close_date"]
    try:
        days = (datetime.fromisoformat(last_date.split(".")[0]) - datetime.fromisoformat(first_date.split(".")[0])).days
    except Exception:
        days = 0

    # Profit factor
    gross_win = sum(t["close_profit_abs"] for t in closed if t["close_profit_abs"] > 0)
    gross_loss = abs(sum(t["close_profit_abs"] for t in closed if t["close_profit_abs"] < 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Average leverage actually used
    leverages = [t["leverage"] for t in closed if t["leverage"]]
    avg_leverage = mean(leverages) if leverages else 0
    avg_stake = mean(t["stake_amount"] for t in closed if t["stake_amount"])

    report = {
        "scope": {
            "n_closed": n,
            "n_open": len(open_trades),
            "first_open": first_date,
            "last_close": last_date,
            "duration_days": days,
            "trades_per_day": n / days if days else 0,
        },
        "headline": {
            "total_pnl_abs_usdc": sum(profits_abs),
            "win_rate_pct": win_rate,
            "expected_value_per_trade_pct": expected_value * 100,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_dd_pct,
            "max_drawdown_abs_usdc": max_dd_abs,
            "max_consecutive_losses": max_consec_loss,
            "max_consecutive_wins": max_consec_win,
        },
        "trade_economics": {
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "median_win_pct": median(wins) * 100 if wins else 0,
            "median_loss_pct": median(losses) * 100 if losses else 0,
            "rr_ratio": rr,
            "breakeven_win_rate_pct": breakeven_wr,
            "win_rate_vs_breakeven_gap_pct": win_rate - breakeven_wr,
            "best_trade_pct": max(profits_pct) * 100 if profits_pct else 0,
            "worst_trade_pct": min(profits_pct) * 100 if profits_pct else 0,
            "gross_win_usdc": gross_win,
            "gross_loss_usdc": gross_loss,
        },
        "execution": {
            "avg_leverage": avg_leverage,
            "avg_stake_usdc": avg_stake,
            "avg_initial_stop_loss_pct": mean(initial_sl_distribution) if initial_sl_distribution else 0,
            "stops_fired_pct": len(sl_trades) / n * 100,
            "median_hold_minutes": median(hold_minutes) if hold_minutes else 0,
            "p25_hold_minutes": sorted(hold_minutes)[len(hold_minutes) // 4] if hold_minutes else 0,
            "p75_hold_minutes": sorted(hold_minutes)[3 * len(hold_minutes) // 4] if hold_minutes else 0,
        },
        "mfe_mae": {
            "avg_mfe_pct": mean(mfe_vals) if mfe_vals else 0,
            "avg_mae_pct": mean(mae_vals) if mae_vals else 0,
            "mfe_to_realised_ratio": (mean(mfe_vals) / abs(avg_loss)) if (mfe_vals and avg_loss) else 0,
        },
        "long_vs_short": {
            "long": side_summary(long_t),
            "short": side_summary(short_t),
        },
        "by_exit_reason": dict(sorted(exit_summary.items(), key=lambda kv: -kv[1]["n"])),
        "by_pair": dict(sorted(pair_summary.items(), key=lambda kv: -kv[1]["n"])),
        "by_enter_tag": dict(sorted(tag_summary.items(), key=lambda kv: -kv[1]["n"])),
        "drawdown_curve_last_10": dd_curve[-10:],
    }
    return report


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "data" / "audit" / "trades.sqlite")
    report = main(db)
    print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
