#!/usr/bin/env python3
"""Walk-Forward Optimization (WFO) for TAHZANCrypto.

Dynamic parameter optimization with:
- 8-segment rolling walk-forward
- 60/40 In-Sample / Out-of-Sample split per segment
- Z-Score overfitting detection
- WFO Efficiency Ratio calculation

Usage:
    source .venv/bin/activate
    python scripts/wfo_optimizer.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# =============================================
# Configuration
# =============================================
STRATEGY = "TAHZANCrypto"
TIMEFRAME = "1h"
NUM_SEGMENTS = 8
IS_RATIO = 0.60  # 60% In-Sample
OOS_RATIO = 0.40  # 40% Out-of-Sample

# Total date range
START_DATE = datetime(2024, 3, 25)
END_DATE = datetime(2026, 3, 17)
TOTAL_DAYS = (END_DATE - START_DATE).days  # ~721 days

# Hyperopt settings
HYPEROPT_EPOCHS = 100  # Per segment (keep low for speed)
HYPEROPT_LOSS = "SharpeHyperOptLoss"

# Configs
CONFIGS = [
    "-c", "config/freqtrade/config_dry.json",
    "-c", "config/freqtrade/config_secrets.json",
]

# Z-Score threshold
Z_SCORE_THRESHOLD = -2.0  # Below this = overfitting


def date_to_str(dt: datetime) -> str:
    """Convert datetime to Freqtrade timerange format."""
    return dt.strftime("%Y%m%d")


def build_segments():
    """Build 8 WFO segments with IS/OOS splits."""
    segment_days = TOTAL_DAYS // NUM_SEGMENTS
    is_days = int(segment_days * IS_RATIO)
    oos_days = segment_days - is_days

    segments = []
    for i in range(NUM_SEGMENTS):
        seg_start = START_DATE + timedelta(days=i * segment_days)
        is_end = seg_start + timedelta(days=is_days)
        oos_end = seg_start + timedelta(days=segment_days)

        # Don't exceed total range
        if oos_end > END_DATE:
            oos_end = END_DATE

        segments.append({
            "segment": i + 1,
            "is_start": seg_start,
            "is_end": is_end,
            "oos_start": is_end,
            "oos_end": oos_end,
            "is_range": f"{date_to_str(seg_start)}-{date_to_str(is_end)}",
            "oos_range": f"{date_to_str(is_end)}-{date_to_str(oos_end)}",
        })

    return segments


def run_hyperopt(timerange: str, segment_id: int) -> dict | None:
    """Run Freqtrade hyperopt on a timerange and return best parameters."""
    print(f"  [Hyperopt] Range: {timerange}, Epochs: {HYPEROPT_EPOCHS}")

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

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        print(f"  [Hyperopt] FAILED: {result.stderr[-200:]}")
        return None

    # Parse best result from hyperopt output
    # Look for the results file
    results_dir = PROJECT_ROOT / "user_data" / "hyperopt_results"
    if not results_dir.exists():
        return None

    # Find latest hyperopt result
    result_files = sorted(results_dir.glob("*.fthypt"), key=os.path.getmtime, reverse=True)
    if not result_files:
        return None

    # Read best params from the result file
    best_params = _parse_hyperopt_results(result_files[0])
    return best_params


def _parse_hyperopt_results(filepath: Path) -> dict | None:
    """Parse Freqtrade hyperopt results file (.fthypt is JSONL)."""
    best_loss = float("inf")
    best_params = None

    with open(filepath) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                loss = entry.get("loss", float("inf"))
                if loss < best_loss:
                    best_loss = loss
                    best_params = entry.get("params_details", {})
            except json.JSONDecodeError:
                continue

    return best_params


def run_backtest(timerange: str, params: dict | None = None) -> dict:
    """Run Freqtrade backtesting and return metrics."""
    cmd = [
        "freqtrade", "backtesting",
        "--strategy", STRATEGY,
        "--timeframe", TIMEFRAME,
        "--timerange", timerange,
        "--strategy-path", "strategies/",
        *CONFIGS,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr

    # Parse key metrics from output
    metrics = {
        "profit_pct": _extract_metric(output, "Total profit %"),
        "sharpe": _extract_metric(output, "Sharpe"),
        "profit_factor": _extract_metric(output, "Profit factor"),
        "trades": _extract_metric(output, "Total/Daily Avg Trades"),
        "drawdown_pct": _extract_metric(output, "Max % of account underwater"),
        "win_rate": _extract_metric(output, "Win%"),
    }
    return metrics


def _extract_metric(output: str, metric_name: str) -> float:
    """Extract a numeric metric from Freqtrade output."""
    for line in output.split("\n"):
        if metric_name in line:
            # Find the value part (after the │ separator)
            parts = line.split("│")
            if len(parts) >= 3:
                value_str = parts[-2].strip()
                # Clean and parse
                value_str = value_str.replace("%", "").replace("USDT", "").strip()
                # Handle "X / Y" format (e.g., "75 / 0.1")
                if "/" in value_str:
                    value_str = value_str.split("/")[0].strip()
                try:
                    return float(value_str)
                except ValueError:
                    pass
    return 0.0


def write_params_file(params: dict):
    """Write hyperopt parameters to a JSON file for Freqtrade to pick up."""
    # Freqtrade reads params from strategy_name.json in the strategy directory
    params_path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"

    # Format params for Freqtrade
    ft_params = {"params": {}}
    if "buy" in params:
        ft_params["params"]["buy"] = params["buy"]
    if "sell" in params:
        ft_params["params"]["sell"] = params["sell"]

    with open(params_path, "w") as f:
        json.dump(ft_params, f, indent=2)


def clear_params_file():
    """Remove parameter override file."""
    params_path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"
    if params_path.exists():
        params_path.unlink()


def calculate_z_score(is_returns: list[float], oos_return: float) -> float:
    """Calculate Z-Score for overfitting detection.

    Z = (OOS_return - mean(IS_returns)) / std(IS_returns)
    Z > -2.0 → robust (OOS not significantly worse than IS)
    Z < -2.0 → overfitting detected
    """
    if len(is_returns) < 2:
        return 0.0
    is_mean = np.mean(is_returns)
    is_std = np.std(is_returns, ddof=1)
    if is_std < 1e-10:
        return 0.0
    return (oos_return - is_mean) / is_std


def main():
    print("=" * 70)
    print("TAHZAN WFO Optimizer")
    print(f"Strategy: {STRATEGY}")
    print(f"Segments: {NUM_SEGMENTS}, IS/OOS split: {IS_RATIO*100:.0f}/{OOS_RATIO*100:.0f}")
    print(f"Date range: {START_DATE.date()} → {END_DATE.date()} ({TOTAL_DAYS} days)")
    print(f"Hyperopt epochs per segment: {HYPEROPT_EPOCHS}")
    print(f"Z-Score threshold: {Z_SCORE_THRESHOLD}")
    print("=" * 70)

    segments = build_segments()
    results = []
    all_is_returns = []

    for seg in segments:
        print(f"\n{'='*50}")
        print(f"Segment {seg['segment']}/{NUM_SEGMENTS}")
        print(f"  IS:  {seg['is_start'].date()} → {seg['is_end'].date()} ({seg['is_range']})")
        print(f"  OOS: {seg['oos_start'].date()} → {seg['oos_end'].date()} ({seg['oos_range']})")
        print(f"{'='*50}")

        # Step 1: Hyperopt on IS data
        print("\n[Step 1] Running Hyperopt on In-Sample data...")
        best_params = run_hyperopt(seg["is_range"], seg["segment"])

        if best_params:
            print(f"  Best params found: {json.dumps(best_params, indent=2)[:200]}...")
            write_params_file(best_params)
        else:
            print("  No params found, using defaults")

        # Step 2: Backtest on IS data (with best params)
        print("\n[Step 2] Backtesting In-Sample...")
        is_metrics = run_backtest(seg["is_range"])
        print(f"  IS Profit: {is_metrics['profit_pct']:.2f}%  Sharpe: {is_metrics['sharpe']:.2f}")
        all_is_returns.append(is_metrics["profit_pct"])

        # Step 3: Backtest on OOS data (with same params)
        print("\n[Step 3] Backtesting Out-of-Sample...")
        oos_metrics = run_backtest(seg["oos_range"])
        print(f"  OOS Profit: {oos_metrics['profit_pct']:.2f}%  Sharpe: {oos_metrics['sharpe']:.2f}")

        # Step 4: Calculate Z-Score
        z_score = calculate_z_score(all_is_returns, oos_metrics["profit_pct"])
        robust = z_score > Z_SCORE_THRESHOLD

        # Clean up params file for next segment
        clear_params_file()

        seg_result = {
            "segment": seg["segment"],
            "is_range": seg["is_range"],
            "oos_range": seg["oos_range"],
            "is_profit": is_metrics["profit_pct"],
            "oos_profit": oos_metrics["profit_pct"],
            "is_sharpe": is_metrics["sharpe"],
            "oos_sharpe": oos_metrics["sharpe"],
            "is_trades": is_metrics["trades"],
            "oos_trades": oos_metrics["trades"],
            "z_score": z_score,
            "robust": robust,
            "params": best_params,
        }
        results.append(seg_result)

        status = "ROBUST" if robust else "OVERFIT"
        print(f"\n  Z-Score: {z_score:.2f} → [{status}]")

    # =============================================
    # Final WFO Report
    # =============================================
    print("\n" + "=" * 70)
    print("WFO OPTIMIZATION REPORT")
    print("=" * 70)

    print(f"\n{'Seg':>3} | {'IS Range':>20} | {'IS Profit':>10} | {'OOS Profit':>10} | {'Z-Score':>8} | {'Status':>8}")
    print("-" * 70)

    oos_profits = []
    is_profits = []
    robust_count = 0

    for r in results:
        status = "ROBUST" if r["robust"] else "OVERFIT"
        print(f"{r['segment']:>3} | {r['is_range']:>20} | {r['is_profit']:>9.2f}% | {r['oos_profit']:>9.2f}% | {r['z_score']:>7.2f} | {status:>8}")
        oos_profits.append(r["oos_profit"])
        is_profits.append(r["is_profit"])
        if r["robust"]:
            robust_count += 1

    # WFO Efficiency Ratio
    avg_is = np.mean(is_profits) if is_profits else 0
    avg_oos = np.mean(oos_profits) if oos_profits else 0
    wfo_er = avg_oos / avg_is if avg_is != 0 else 0

    print(f"\n{'='*70}")
    print(f"Avg IS Profit:  {avg_is:.2f}%")
    print(f"Avg OOS Profit: {avg_oos:.2f}%")
    print(f"WFO Efficiency Ratio: {wfo_er:.2f} {'(GOOD ≥0.5)' if wfo_er >= 0.5 else '(WARN <0.5)'}")
    print(f"Robust segments: {robust_count}/{NUM_SEGMENTS}")
    print(f"Aggregate OOS Profit: {sum(oos_profits):.2f}%")
    print(f"{'='*70}")

    # Save results
    output_path = PROJECT_ROOT / "data" / "reports" / "wfo_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "strategy": STRATEGY,
            "segments": NUM_SEGMENTS,
            "is_oos_ratio": f"{IS_RATIO}/{OOS_RATIO}",
            "z_score_threshold": Z_SCORE_THRESHOLD,
            "wfo_efficiency_ratio": wfo_er,
            "avg_is_profit": avg_is,
            "avg_oos_profit": avg_oos,
            "robust_segments": robust_count,
            "results": results,
        }, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
