"""BacktestReport 輸出格式化 — human-readable + JSON."""
from __future__ import annotations

import json
import statistics
from typing import Any

from smart_money.backtest.validator import BacktestReport, GateDecision


def format_report(report: BacktestReport) -> str:
    """Pretty-print a single cutoff report."""
    lines = []
    lines.append(f"=== Backtest @ cutoff={report.cutoff.date()} "
                 f"(forward {report.forward_months}mo, top {report.top_n}) ===")

    if not report.algo_results:
        lines.append("  ⚠ no eligible wallets to backtest")
        return "\n".join(lines)

    lines.append(f"\n--- Algorithm Top {report.top_n} ---")
    lines.append(f"{'#':>3}  {'addr':<44}  {'score':>7}  {'fwd_pnl':>12}  {'trades':>7}  blow?")
    for r in report.algo_results:
        tag = "💥" if r.blown_up else "  "
        lines.append(f"{r.rank_at_cutoff:>3}  {r.address:<44}  "
                     f"{r.score_at_cutoff:>7.4f}  {r.forward_pnl:>+12.2f}  "
                     f"{r.forward_trades:>7}  {tag}")

    algo_mean = statistics.mean(r.forward_pnl for r in report.algo_results)
    naive_mean = (statistics.mean(r.forward_pnl for r in report.naive_results)
                   if report.naive_results else 0.0)

    lines.append("\n--- Summary ---")
    lines.append(f"  algo   median={report.algo_median_pnl:+.2f}  mean={algo_mean:+.2f}  "
                 f"blowups={report.algo_blowup_rate:.0%}")
    lines.append(f"  naive  median={report.naive_median_pnl:+.2f}  mean={naive_mean:+.2f}")
    edge = report.algo_median_pnl - report.naive_median_pnl
    lines.append(f"  edge   algo − naive = {edge:+.2f}")
    if report.btc_buyhold_return is not None:
        lines.append(f"  BTC buy-hold over window: {report.btc_buyhold_return:+.1%}")
    else:
        lines.append("  BTC buy-hold: (insufficient data)")

    return "\n".join(lines)


def format_gate_decision(decision: GateDecision) -> str:
    badge = "✅ PASS" if decision.passed else "❌ FAIL"
    lines = [f"\n=== GATE: {badge} ==="]
    for r in decision.reasons:
        lines.append(f"  • {r}")
    if decision.metrics:
        lines.append("  metrics:")
        for k, v in decision.metrics.items():
            if isinstance(v, float):
                lines.append(f"    {k:<20} = {v:+.4f}")
            else:
                lines.append(f"    {k:<20} = {v}")
    return "\n".join(lines)


def report_to_dict(report: BacktestReport) -> dict[str, Any]:
    return {
        "cutoff": report.cutoff.isoformat(),
        "forward_months": report.forward_months,
        "top_n": report.top_n,
        "algo_median_pnl": report.algo_median_pnl,
        "naive_median_pnl": report.naive_median_pnl,
        "algo_blowup_rate": report.algo_blowup_rate,
        "btc_buyhold_return": report.btc_buyhold_return,
        "algo": [
            {
                "rank": r.rank_at_cutoff,
                "address": r.address,
                "score": r.score_at_cutoff,
                "forward_pnl": r.forward_pnl,
                "forward_trades": r.forward_trades,
                "forward_max_dd": r.forward_max_dd,
                "blown_up": r.blown_up,
            }
            for r in report.algo_results
        ],
    }


def report_to_json(report: BacktestReport, *, indent: int = 2) -> str:
    return json.dumps(report_to_dict(report), indent=indent)


__all__ = [
    "format_gate_decision",
    "format_report",
    "report_to_dict",
    "report_to_json",
]
