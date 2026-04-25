"""Health check core — R74.

Pure parse + verdict functions for SUPERTREND /operations + SHADOW
/signal-health responses. Used by scripts/health_check.sh.

Separated from shell so the verdict logic is properly unit-testable
(scripts/health_check.sh just does the HTTP/SSH plumbing).

CLI:
    python -m scripts.health_check_core supertrend.json shadow.json [--quiet]

Reads pre-fetched JSON responses from disk + prints comprehensive
verdict. Exit 0 = healthy, Exit 1 = at least one hard problem.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def evaluate_supertrend(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute hard-problem list + summary for SUPERTREND /operations."""
    bot = payload.get("bot") or {}
    wl = payload.get("whitelist") or {}
    pipe = payload.get("pipeline") or {}
    perf = payload.get("performance") or {}
    errs = payload.get("errors") or {}
    alerts = payload.get("alerts") or []

    problems: list[str] = []
    if bot.get("state") != "running":
        problems.append(f"supertrend: bot.state={bot.get('state')}")
    if (wl.get("n_pairs") or 0) <= 0:
        problems.append(
            f"supertrend: whitelist empty (n_pairs={wl.get('n_pairs')})"
        )
    if not pipe.get("journal_ok"):
        problems.append("supertrend: journal not OK")
    n_evals = (pipe.get("evaluations") or {}).get("n_evaluations") or 0
    if n_evals == 0:
        problems.append("supertrend: 0 evaluations in window")
    if errs:
        problems.append(f"supertrend: errors {list(errs.keys())}")

    return {
        "namespace": "supertrend",
        "status": payload.get("status", "unknown"),
        "alerts": [str(a) for a in alerts],
        "problems": problems,
        "summary": {
            "bot_state": bot.get("state"),
            "dry_run": bot.get("dry_run"),
            "n_pairs": wl.get("n_pairs"),
            "n_evaluations": n_evals,
            "recent_trades": pipe.get("recent_trades"),
            "n_trades_7d": perf.get("n_trades"),
            "pnl_usd": perf.get("sum_pnl_usd"),
        },
    }


# Hard-problem alert names for SHADOW (these are the ones from R73 that
# warrant blocking the iteration's new work).
_SHADOW_HARD_PROBLEM_ALERTS = (
    "RED_PIPELINE",
    "ZERO_TRADEABLE_WALLETS",
    "ALL_SKIPPED_NO_PAPER",
)


def evaluate_shadow(payload: dict[str, Any]) -> dict[str, Any]:
    """Compute hard-problem list + summary for SHADOW /signal-health."""
    if not payload.get("configured", True):
        # Endpoint reports Supabase unconfigured — not a hard problem
        # in dev / when SHADOW isn't deployed. Return informational state.
        return {
            "namespace": "shadow",
            "status": "unconfigured",
            "alerts": [],
            "problems": [],
            "summary": {"configured": False},
        }

    health = payload.get("health", "unknown")
    alerts = payload.get("alerts") or []
    density = payload.get("density") or {}
    positions = payload.get("positions") or {}
    latency = payload.get("latency_24h") or {}
    skipped = payload.get("skipped_by_reason_24h") or {}

    problems: list[str] = []
    # health=red → hard
    if health == "red":
        problems.append(
            f"shadow: health=red ({payload.get('health_reason') or 'unknown'})"
        )
    # Specific alerts from R73 that we treat as hard
    for alert in alerts:
        for tag in _SHADOW_HARD_PROBLEM_ALERTS:
            if tag in str(alert):
                problems.append(f"shadow: {tag}")
                break   # one match per alert is enough

    return {
        "namespace": "shadow",
        "status": payload.get("status", health),
        "alerts": [str(a) for a in alerts],
        "problems": problems,
        "summary": {
            "health": health,
            "n_distinct_wallets": positions.get("distinct_wallets"),
            "long": positions.get("long"),
            "short": positions.get("short"),
            "p95_ms": latency.get("p95_ms"),
            "skip_reasons_top": dict(
                sorted(skipped.items(), key=lambda kv: kv[1], reverse=True)[:3]
            ),
            "density_1h_skipped": (density.get("1h") or {}).get("skipped"),
        },
    }


def render_report(eval_super: dict, eval_shadow: dict) -> str:
    """Format the dual-system verdict as one screen of text."""
    all_problems = eval_super["problems"] + eval_shadow["problems"]
    icon = "✅" if not all_problems else "⚠️"

    lines = [
        f"{icon} HEALTH: "
        f"supertrend={eval_super['status']} ({len(eval_super['alerts'])} alerts)  "
        f"shadow={eval_shadow['status']} ({len(eval_shadow['alerts'])} alerts)"
    ]

    # SUPERTREND block
    s = eval_super["summary"]
    lines.append("  --- SUPERTREND ---")
    lines.append(
        f"  bot         : state={s.get('bot_state'):<8} "
        f"dry_run={s.get('dry_run')}  pairs={s.get('n_pairs')}"
    )
    lines.append(
        f"  pipeline    : evals={s.get('n_evaluations')}  "
        f"recent_trades={s.get('recent_trades')}"
    )
    lines.append(
        f"  performance : trades_7d={s.get('n_trades_7d')}  "
        f"pnl_usd={s.get('pnl_usd')}"
    )
    if eval_super["alerts"]:
        lines.append("  alerts:")
        for a in eval_super["alerts"]:
            head = a.split("—", 1)[0].strip()
            lines.append(f"    - {head}")

    # SHADOW block
    sh = eval_shadow["summary"]
    lines.append("  --- SHADOW ---")
    if not sh.get("configured", True):
        lines.append("  (supabase unconfigured — SHADOW not active)")
    else:
        lines.append(
            f"  health      : {sh.get('health')}  "
            f"wallets={sh.get('n_distinct_wallets')}  "
            f"L={sh.get('long')} S={sh.get('short')}"
        )
        lines.append(
            f"  latency_p95 : {sh.get('p95_ms')}ms  "
            f"skipped_1h={sh.get('density_1h_skipped')}"
        )
        if sh.get("skip_reasons_top"):
            top_str = ", ".join(
                f"{k}={v}" for k, v in sh["skip_reasons_top"].items()
            )
            lines.append(f"  skip_top    : {top_str}")
        if eval_shadow["alerts"]:
            lines.append("  alerts:")
            for a in eval_shadow["alerts"]:
                head = a.split("—", 1)[0].strip()
                lines.append(f"    - {head}")

    if all_problems:
        lines.append(f"  ⚠ hard problems ({len(all_problems)}):")
        for p in all_problems:
            lines.append(f"    - {p}")

    return "\n".join(lines)


def has_hard_problems(eval_super: dict, eval_shadow: dict) -> bool:
    return bool(eval_super["problems"] or eval_shadow["problems"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate SUPERTREND + SHADOW health reports.",
    )
    parser.add_argument("supertrend_json", help="Path to /operations response JSON")
    parser.add_argument(
        "shadow_json", nargs="?",
        help="Path to /signal-health response JSON (optional; "
             "treated as unconfigured if missing/empty)",
    )
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    args = parser.parse_args(argv)

    try:
        with open(args.supertrend_json) as f:
            sup_payload = json.load(f)
    except Exception as e:
        if not args.quiet:
            print(f"✗ supertrend payload load failed: {e}")
        return 1

    sup_eval = evaluate_supertrend(sup_payload)

    if args.shadow_json:
        try:
            with open(args.shadow_json) as f:
                sh_payload = json.load(f)
            sh_eval = evaluate_shadow(sh_payload)
        except Exception:
            sh_eval = evaluate_shadow({"configured": False})
    else:
        sh_eval = evaluate_shadow({"configured": False})

    if not args.quiet:
        print(render_report(sup_eval, sh_eval))

    return 1 if has_hard_problems(sup_eval, sh_eval) else 0


if __name__ == "__main__":
    sys.exit(main())
