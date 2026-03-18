#!/usr/bin/env python3
"""Walk-Forward Optimization (WFO) for SMCTrend.

Dynamic parameter optimization with:
- 8-segment rolling walk-forward
- 60/40 In-Sample / Out-of-Sample split per segment
- Z-Score overfitting detection
- WFO Efficiency Ratio calculation

Usage:
    source .venv/bin/activate
    python scripts/wfo_smc.py
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# =============================================
# Configuration
# =============================================
STRATEGY = "SMCTrend"
TIMEFRAME = "1h"
NUM_SEGMENTS = 8
IS_RATIO = 0.60
OOS_RATIO = 0.40

START_DATE = datetime(2024, 3, 25)
END_DATE = datetime(2026, 3, 17)
TOTAL_DAYS = (END_DATE - START_DATE).days

HYPEROPT_EPOCHS = 200
HYPEROPT_LOSS = "SharpeHyperOptLoss"

CONFIGS = [
    "-c", "config/freqtrade/config_dry.json",
    "-c", "config/freqtrade/config_secrets.json",
]

Z_SCORE_THRESHOLD = -2.0


def date_str(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def build_segments():
    seg_days = TOTAL_DAYS // NUM_SEGMENTS
    is_days = int(seg_days * IS_RATIO)
    segments = []
    for i in range(NUM_SEGMENTS):
        s = START_DATE + timedelta(days=i * seg_days)
        is_end = s + timedelta(days=is_days)
        oos_end = min(s + timedelta(days=seg_days), END_DATE)
        segments.append({
            "seg": i + 1,
            "is_range": f"{date_str(s)}-{date_str(is_end)}",
            "oos_range": f"{date_str(is_end)}-{date_str(oos_end)}",
            "is_start": s, "is_end": is_end,
            "oos_start": is_end, "oos_end": oos_end,
        })
    return segments


def run_hyperopt(timerange: str) -> dict | None:
    """Run hyperopt and return best params."""
    print(f"  [Hyperopt] {timerange}, {HYPEROPT_EPOCHS} epochs...")
    cmd = [
        "freqtrade", "hyperopt",
        "--strategy", STRATEGY,
        "--timeframe", TIMEFRAME,
        "--timerange", timerange,
        "--hyperopt-loss", HYPEROPT_LOSS,
        "--epochs", str(HYPEROPT_EPOCHS),
        "--strategy-path", "strategies/",
        "--spaces", "buy", "sell",
        *CONFIGS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        # Check if it's just a "no trades" issue
        if "No trades" in result.stderr or "No trades" in result.stdout:
            print("  [Hyperopt] No trades found in this segment")
            return None
        print(f"  [Hyperopt] Error: {result.stderr[-300:]}")
        return None

    # Parse best from .fthypt file
    results_dir = PROJECT_ROOT / "user_data" / "hyperopt_results"
    fthypt_files = sorted(results_dir.glob("*.fthypt"), key=os.path.getmtime, reverse=True)
    if not fthypt_files:
        return None

    best_loss = float("inf")
    best_params = None
    with open(fthypt_files[0]) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                loss = entry.get("loss", float("inf"))
                if loss < best_loss:
                    best_loss = loss
                    best_params = entry.get("params_details", {})
            except (json.JSONDecodeError, KeyError):
                continue
    return best_params


def run_backtest(timerange: str) -> dict:
    """Run backtest, return metrics."""
    cmd = [
        "freqtrade", "backtesting",
        "--strategy", STRATEGY,
        "--timeframe", TIMEFRAME,
        "--timerange", timerange,
        "--strategy-path", "strategies/",
        *CONFIGS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = result.stdout + result.stderr
    return {
        "profit_pct": _extract(out, "Total profit %"),
        "sharpe": _extract(out, "Sharpe"),
        "profit_factor": _extract(out, "Profit factor"),
        "trades": _extract(out, "Total/Daily Avg Trades"),
        "drawdown": _extract(out, "Max % of account underwater"),
        "win_rate": _extract_win_rate(out),
    }


def _extract(output: str, name: str) -> float:
    for line in output.split("\n"):
        if name in line:
            parts = line.split("│")
            if len(parts) >= 3:
                val = parts[-2].strip().replace("%", "").replace("USDT", "").strip()
                if "/" in val:
                    val = val.split("/")[0].strip()
                try:
                    return float(val)
                except ValueError:
                    pass
    return 0.0


def _extract_win_rate(output: str) -> float:
    """Extract win rate from STRATEGY SUMMARY line."""
    for line in output.split("\n"):
        if STRATEGY in line and "│" in line:
            parts = line.split("│")
            for part in parts:
                part = part.strip()
                # Look for pattern like "62     0   325  16.0"
                if "." in part and len(part.split()) >= 4:
                    try:
                        return float(part.split()[-1])
                    except ValueError:
                        pass
    return 0.0


def write_params(params: dict):
    path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"
    ft_params = {"params": {}}
    for space in ["buy", "sell"]:
        if space in params:
            ft_params["params"][space] = params[space]
    with open(path, "w") as f:
        json.dump(ft_params, f, indent=2)


def clear_params():
    path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"
    if path.exists():
        path.unlink()


def z_score(is_returns: list[float], oos_val: float) -> float:
    if len(is_returns) < 2:
        return 0.0
    mu = np.mean(is_returns)
    sigma = np.std(is_returns, ddof=1)
    if sigma < 1e-10:
        return 0.0
    return (oos_val - mu) / sigma


def main():
    print("=" * 70)
    print(f"WFO Optimizer — {STRATEGY}")
    print(f"Segments: {NUM_SEGMENTS} | Split: {IS_RATIO*100:.0f}/{OOS_RATIO*100:.0f}")
    print(f"Range: {START_DATE.date()} → {END_DATE.date()} ({TOTAL_DAYS}d)")
    print(f"Hyperopt: {HYPEROPT_EPOCHS} epochs/seg | Loss: {HYPEROPT_LOSS}")
    print("=" * 70)

    segments = build_segments()
    results = []
    all_is = []

    for seg in segments:
        print(f"\n{'='*60}")
        print(f"Segment {seg['seg']}/{NUM_SEGMENTS}")
        print(f"  IS:  {seg['is_start'].date()} → {seg['is_end'].date()}")
        print(f"  OOS: {seg['oos_start'].date()} → {seg['oos_end'].date()}")
        print(f"{'='*60}")

        # 1. Hyperopt on IS
        params = run_hyperopt(seg["is_range"])
        if params:
            print(f"  Found params: {list(params.get('buy', {}).keys())}")
            write_params(params)
        else:
            print("  Using default params")

        # 2. Backtest IS
        print("  [IS Backtest]...")
        is_m = run_backtest(seg["is_range"])
        print(f"  IS: Profit={is_m['profit_pct']:.2f}% Sharpe={is_m['sharpe']:.2f} PF={is_m['profit_factor']:.2f} Trades={is_m['trades']:.0f}")
        all_is.append(is_m["profit_pct"])

        # 3. Backtest OOS (same params)
        print("  [OOS Backtest]...")
        oos_m = run_backtest(seg["oos_range"])
        print(f"  OOS: Profit={oos_m['profit_pct']:.2f}% Sharpe={oos_m['sharpe']:.2f} PF={oos_m['profit_factor']:.2f} Trades={oos_m['trades']:.0f}")

        # 4. Z-Score
        z = z_score(all_is, oos_m["profit_pct"])
        robust = z > Z_SCORE_THRESHOLD
        status = "ROBUST" if robust else "OVERFIT"
        print(f"  Z-Score: {z:.2f} → [{status}]")

        clear_params()

        results.append({
            "seg": seg["seg"],
            "is_range": seg["is_range"],
            "oos_range": seg["oos_range"],
            "is_profit": is_m["profit_pct"],
            "oos_profit": oos_m["profit_pct"],
            "is_sharpe": is_m["sharpe"],
            "oos_sharpe": oos_m["sharpe"],
            "is_pf": is_m["profit_factor"],
            "oos_pf": oos_m["profit_factor"],
            "is_trades": is_m["trades"],
            "oos_trades": oos_m["trades"],
            "z_score": z,
            "robust": robust,
            "params": params,
        })

    # =============================================
    # Final Report
    # =============================================
    print("\n" + "=" * 80)
    print("WFO OPTIMIZATION REPORT — SMCTrend")
    print("=" * 80)

    header = f"{'Seg':>3} | {'IS Range':>20} | {'IS Profit':>10} | {'OOS Profit':>10} | {'IS PF':>6} | {'OOS PF':>6} | {'Z':>6} | {'Status':>7}"
    print(f"\n{header}")
    print("-" * 80)

    oos_profits = []
    is_profits = []
    robust_n = 0

    for r in results:
        st = "ROBUST" if r["robust"] else "OVERFIT"
        print(f"{r['seg']:>3} | {r['is_range']:>20} | {r['is_profit']:>9.2f}% | {r['oos_profit']:>9.2f}% | {r['is_pf']:>5.2f} | {r['oos_pf']:>5.2f} | {r['z_score']:>5.2f} | {st:>7}")
        oos_profits.append(r["oos_profit"])
        is_profits.append(r["is_profit"])
        if r["robust"]:
            robust_n += 1

    avg_is = np.mean(is_profits)
    avg_oos = np.mean(oos_profits)
    wfo_er = avg_oos / avg_is if avg_is != 0 else 0

    print(f"\n{'='*80}")
    print(f"Avg IS Profit:      {avg_is:>8.2f}%")
    print(f"Avg OOS Profit:     {avg_oos:>8.2f}%")
    print(f"WFO Efficiency:     {wfo_er:>8.2f} {'✓ GOOD' if wfo_er >= 0.5 else '⚠ WARN'}")
    print(f"Robust segments:    {robust_n}/{NUM_SEGMENTS}")
    print(f"Aggregate OOS:      {sum(oos_profits):>8.2f}%")
    print(f"{'='*80}")

    # Save
    out_path = PROJECT_ROOT / "data" / "reports" / "wfo_smc_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "strategy": STRATEGY,
            "segments": NUM_SEGMENTS,
            "split": f"{IS_RATIO}/{OOS_RATIO}",
            "epochs": HYPEROPT_EPOCHS,
            "z_threshold": Z_SCORE_THRESHOLD,
            "wfo_efficiency": wfo_er,
            "avg_is": avg_is,
            "avg_oos": avg_oos,
            "robust_count": robust_n,
            "aggregate_oos": sum(oos_profits),
            "results": results,
        }, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
