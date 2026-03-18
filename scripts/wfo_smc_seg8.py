#!/usr/bin/env python3
"""Re-run WFO Segment 8 only for SMCTrend (after filelock install)."""

import json
import os
import subprocess
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

STRATEGY = "SMCTrend"
TIMEFRAME = "1h"
EPOCHS = 200
CONFIGS = ["-c", "config/freqtrade/config_dry.json", "-c", "config/freqtrade/config_secrets.json"]

IS_RANGE = "20251215-20260207"
OOS_RANGE = "20260207-20260317"

# Previous IS returns from segments 1-7
PREV_IS = [2.71, 22.15, 33.33, -7.29, 3.05, 40.40, 18.89]


def run_hyperopt(timerange):
    print(f"[Hyperopt] {timerange}, {EPOCHS} epochs...")
    cmd = [
        "freqtrade", "hyperopt", "--strategy", STRATEGY, "--timeframe", TIMEFRAME,
        "--timerange", timerange, "--hyperopt-loss", "SharpeHyperOptLoss",
        "--epochs", str(EPOCHS), "--strategy-path", "strategies/", "--spaces", "buy", "sell",
        *CONFIGS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr[-500:]}")
        return None

    results_dir = PROJECT_ROOT / "user_data" / "hyperopt_results"
    files = sorted(results_dir.glob("*.fthypt"), key=os.path.getmtime, reverse=True)
    if not files:
        return None

    best_loss, best_params = float("inf"), None
    with open(files[0]) as f:
        for line in f:
            try:
                e = json.loads(line.strip())
                if e.get("loss", float("inf")) < best_loss:
                    best_loss = e["loss"]
                    best_params = e.get("params_details", {})
            except (json.JSONDecodeError, KeyError):
                pass
    return best_params


def run_backtest(timerange):
    cmd = [
        "freqtrade", "backtesting", "--strategy", STRATEGY, "--timeframe", TIMEFRAME,
        "--timerange", timerange, "--strategy-path", "strategies/", *CONFIGS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = result.stdout + result.stderr

    def extract(name):
        for line in out.split("\n"):
            if name in line:
                parts = line.split("│")
                if len(parts) >= 3:
                    v = parts[-2].strip().replace("%", "").replace("USDT", "").strip()
                    if "/" in v:
                        v = v.split("/")[0].strip()
                    try:
                        return float(v)
                    except ValueError:
                        pass
        return 0.0

    return {
        "profit": extract("Total profit %"),
        "sharpe": extract("Sharpe"),
        "pf": extract("Profit factor"),
        "trades": extract("Total/Daily Avg Trades"),
        "drawdown": extract("Max % of account underwater"),
    }


def main():
    print("=" * 60)
    print("SMCTrend WFO — Segment 8 Re-run")
    print(f"IS:  {IS_RANGE}  |  OOS: {OOS_RANGE}")
    print("=" * 60)

    # 1. Hyperopt
    params = run_hyperopt(IS_RANGE)
    if params:
        print(f"Best params: {list(params.get('buy', {}).keys())}")
        path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"
        with open(path, "w") as f:
            ft = {"params": {}}
            for s in ["buy", "sell"]:
                if s in params:
                    ft["params"][s] = params[s]
            json.dump(ft, f, indent=2)
    else:
        print("Hyperopt failed, using defaults")

    # 2. IS Backtest
    print("\n[IS Backtest]...")
    is_m = run_backtest(IS_RANGE)
    print(f"  IS: Profit={is_m['profit']:.2f}% Sharpe={is_m['sharpe']:.2f} PF={is_m['pf']:.2f} Trades={is_m['trades']:.0f}")

    # 3. OOS Backtest
    print("\n[OOS Backtest]...")
    oos_m = run_backtest(OOS_RANGE)
    print(f"  OOS: Profit={oos_m['profit']:.2f}% Sharpe={oos_m['sharpe']:.2f} PF={oos_m['pf']:.2f} Trades={oos_m['trades']:.0f}")

    # 4. Z-Score
    all_is = PREV_IS + [is_m["profit"]]
    mu = np.mean(all_is)
    sigma = np.std(all_is, ddof=1)
    z = (oos_m["profit"] - mu) / sigma if sigma > 1e-10 else 0
    robust = z > -2.0

    print(f"\n{'='*60}")
    print(f"Z-Score: {z:.2f} → {'ROBUST' if robust else 'OVERFIT'}")
    print(f"{'='*60}")

    # 5. Clean up
    path = PROJECT_ROOT / "strategies" / f"{STRATEGY}.json"
    if path.exists():
        path.unlink()

    # 6. Update WFO results
    wfo_path = PROJECT_ROOT / "data" / "reports" / "wfo_smc_results.json"
    if wfo_path.exists():
        with open(wfo_path) as f:
            wfo = json.load(f)
        # Update segment 8
        for r in wfo["results"]:
            if r["seg"] == 8:
                r["is_profit"] = is_m["profit"]
                r["oos_profit"] = oos_m["profit"]
                r["is_sharpe"] = is_m["sharpe"]
                r["oos_sharpe"] = oos_m["sharpe"]
                r["is_pf"] = is_m["pf"]
                r["oos_pf"] = oos_m["pf"]
                r["is_trades"] = is_m["trades"]
                r["oos_trades"] = oos_m["trades"]
                r["z_score"] = z
                r["robust"] = robust
                r["params"] = params
        # Recalculate aggregates
        is_profits = [r["is_profit"] for r in wfo["results"]]
        oos_profits = [r["oos_profit"] for r in wfo["results"]]
        wfo["avg_is"] = np.mean(is_profits)
        wfo["avg_oos"] = np.mean(oos_profits)
        wfo["aggregate_oos"] = sum(oos_profits)
        wfo["wfo_efficiency"] = wfo["avg_oos"] / wfo["avg_is"] if wfo["avg_is"] != 0 else 0
        wfo["robust_count"] = sum(1 for r in wfo["results"] if r["robust"])

        with open(wfo_path, "w") as f:
            json.dump(wfo, f, indent=2, default=str)

        print("\nUpdated WFO report:")
        print(f"  Avg IS:  {wfo['avg_is']:.2f}%")
        print(f"  Avg OOS: {wfo['avg_oos']:.2f}%")
        print(f"  WFO ER:  {wfo['wfo_efficiency']:.2f}")
        print(f"  Agg OOS: {wfo['aggregate_oos']:.2f}%")
        print(f"  Robust:  {wfo['robust_count']}/8")


if __name__ == "__main__":
    main()
