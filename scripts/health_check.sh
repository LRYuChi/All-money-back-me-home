#!/bin/bash
# ============================================================
# scripts/health_check.sh — Prod monitoring snapshot
#
# Per user's iteration-monitoring rule (R-meta): every /loop cycle
# starts with this. Returns exit 0 if healthy, exit 1 if degraded.
#
# Aggregates R68 /api/supertrend/operations into a 1-screen status.
# Designed to be the FIRST command run each iteration.
#
# Usage:
#   bash scripts/health_check.sh                  # SSH to VPS + check
#   bash scripts/health_check.sh --local          # check localhost (dev)
#   bash scripts/health_check.sh --quiet          # exit code only, no output
# ============================================================
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@187.127.100.77}"
LOCAL=0
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --quiet) QUIET=1 ;;
    esac
done

if [ "$LOCAL" = "1" ]; then
    BASE="http://localhost"
    SHELL_PREFIX=""
else
    BASE="http://localhost"
    SHELL_PREFIX="ssh -o ConnectTimeout=10 ${VPS_HOST}"
fi

# Fetch /operations via the chosen shell
TMP_RESP=$(mktemp)
trap "rm -f $TMP_RESP" EXIT

if [ "$LOCAL" = "1" ]; then
    if ! curl -sf -m 15 "$BASE/api/supertrend/operations" > "$TMP_RESP" 2>/dev/null; then
        [ "$QUIET" = "0" ] && echo "✗ HEALTH CHECK FAILED — /operations unreachable on localhost"
        exit 1
    fi
else
    if ! $SHELL_PREFIX "curl -sf -m 15 $BASE/api/supertrend/operations" > "$TMP_RESP" 2>/dev/null; then
        [ "$QUIET" = "0" ] && echo "✗ HEALTH CHECK FAILED — /operations unreachable via $VPS_HOST"
        exit 1
    fi
fi

# Parse + verdict via Python
python3 << PY
import json, sys

with open("$TMP_RESP") as f:
    d = json.load(f)

bot = d.get("bot", {})
wl = d.get("whitelist", {})
pipe = d.get("pipeline", {})
errs = d.get("errors", {})
alerts = d.get("alerts", [])
status = d.get("status", "unknown")

problems = []
if bot.get("state") != "running":
    problems.append(f"bot.state={bot.get('state')}")
if (wl.get("n_pairs") or 0) <= 0:
    problems.append(f"whitelist empty/unknown (n_pairs={wl.get('n_pairs')})")
if not pipe.get("journal_ok"):
    problems.append("journal not OK")
n_evals = (pipe.get("evaluations") or {}).get("n_evaluations") or 0
if n_evals == 0:
    problems.append("0 evaluations in window")
if errs:
    problems.append(f"errors: {list(errs.keys())}")

quiet = ${QUIET}

if not quiet:
    icon = "✅" if not problems and status == "ok" else "⚠️"
    print(f"{icon} HEALTH: status={status}  alerts={len(alerts)}")
    print(f"  bot         : state={bot.get('state'):<8} dry_run={bot.get('dry_run')}  strategy={bot.get('strategy')}")
    print(f"  whitelist   : {wl.get('n_pairs')} pairs")
    print(f"  pipeline    : journal_ok={pipe.get('journal_ok')}  n_evaluations={n_evals}  recent_trades={pipe.get('recent_trades')}")
    perf = d.get("performance", {})
    print(f"  performance : trades_7d={perf.get('n_trades')}  pnl_usd={perf.get('sum_pnl_usd')}")
    if alerts:
        print("  active alerts:")
        for a in alerts:
            head = a.split("—", 1)[0].strip()
            print(f"    - {head}")
    if problems:
        print(f"  ⚠ problems: {', '.join(problems)}")

# Exit non-zero if PROBLEMS (alerts alone don't fail — they're known degraded states)
if problems:
    sys.exit(1)
sys.exit(0)
PY
PY_EXIT=$?
exit $PY_EXIT
