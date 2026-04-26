#!/usr/bin/env bash
# LIVE pre-flight check — final verification before flipping SUPERTREND_LIVE=1
#
# Run this AFTER verify_deploy.sh passes. It adds 8 LIVE-specific checks
# on top: stricter env requirements, OKX credentials sanity, account
# balance floor, history sufficiency, and confirmation that the bot has
# actually demonstrated entry capability in dry-run.
#
# Exit 0 → safe to set SUPERTREND_LIVE=1 + recreate freqtrade
# Exit 1 → at least one blocker; do NOT go live
#
# Usage (must run on VPS):
#   cd /opt/ambmh && bash scripts/live_preflight.sh
#
# DESIGN PHILOSOPHY:
#   The default for every check is conservative — we'd rather block a
#   legitimate go-live than permit one that will burn money. Every fail
#   message tells the operator what to do to recover.

set -u
set -o pipefail

# ─── colours ─────────────────────────────────────────────────────────────
red() { printf "\033[31m%s\033[0m\n" "$1"; }
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

fail_count=0
fail() { red "  ✗ $1"; fail_count=$((fail_count + 1)); }
ok() { green "  ✓ $1"; }
warn() { yellow "  ! $1"; }

REQUIRED_BALANCE_USDC="${REQUIRED_BALANCE_USDC:-200}"
REQUIRED_DRYRUN_FIRES_7D="${REQUIRED_DRYRUN_FIRES_7D:-3}"

echo "════════════════════════════════════════════════════"
echo "  LIVE pre-flight check"
echo "  (run AFTER verify_deploy.sh passes)"
echo "════════════════════════════════════════════════════"
echo

# ─── Pre-check: verify_deploy must pass first ─────────────────────────
echo "[0/8] Run verify_deploy.sh first (dependency)"
if bash "$(dirname "$0")/verify_deploy.sh" >/tmp/preflight_verify.log 2>&1; then
    ok "verify_deploy passed"
else
    fail "verify_deploy FAILED — fix that first, then re-run"
    echo "  Last 10 lines of verify_deploy output:"
    tail -10 /tmp/preflight_verify.log | sed 's/^/    /'
    echo
fi

# ─── Check 1: GUARDS_REQUIRE_LOAD = 1 (R105 fail-closed) ───────────────
echo "[1/8] SUPERTREND_GUARDS_REQUIRE_LOAD = 1 (R105 LIVE mandate)"
require_load=$(docker exec ambmh-freqtrade-1 sh -c 'printenv SUPERTREND_GUARDS_REQUIRE_LOAD || echo 0' 2>/dev/null | tr -d '\r')
if [ "$require_load" = "1" ]; then
    ok "fail-closed mode active — guards import failure will block entries"
else
    fail "GUARDS_REQUIRE_LOAD=$require_load — LIVE must be 1 to prevent R104-class silent failure"
    echo "    Fix: echo 'SUPERTREND_GUARDS_REQUIRE_LOAD=1' >> /opt/ambmh/.env"
    echo "         docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade api"
fi

# ─── Check 2: GUARDS_ENABLED = 1 ─────────────────────────────────────────
echo
echo "[2/8] SUPERTREND_GUARDS_ENABLED = 1"
guards_enabled=$(docker exec ambmh-freqtrade-1 sh -c 'printenv SUPERTREND_GUARDS_ENABLED || echo 1' 2>/dev/null | tr -d '\r')
if [ "$guards_enabled" = "1" ]; then
    ok "guards enabled"
else
    fail "GUARDS_ENABLED=$guards_enabled — must be 1 in LIVE"
fi

# ─── Check 3: OKX credentials present ────────────────────────────────────
echo
echo "[3/8] OKX API credentials present + non-default"
secrets_path="/opt/ambmh/config/freqtrade/config_secrets.json"
if [ ! -f "$secrets_path" ]; then
    fail "$secrets_path missing"
else
    # Look for placeholder strings that indicate fake creds
    bad_patterns="YOUR_API_KEY|YOUR_SECRET|REPLACE_ME|CHANGE_ME|placeholder"
    if grep -qE "$bad_patterns" "$secrets_path"; then
        fail "$secrets_path contains placeholder values"
    else
        # Confirm key/secret/password fields are non-empty
        for field in key secret password; do
            v=$(python3 -c "import json; d=json.load(open('$secrets_path'))['exchange']; print(d.get('$field') or '')" 2>/dev/null)
            if [ -z "$v" ]; then
                fail "exchange.$field is empty in $secrets_path"
            else
                ok "exchange.$field present (len=${#v})"
            fi
        done
    fi
fi

# ─── Check 4: account balance ≥ floor ────────────────────────────────────
echo
echo "[4/8] account balance ≥ \$${REQUIRED_BALANCE_USDC}"
# freqtrade REST 需要 basic auth (FT_USER / FT_PASS) — 從 api container env 拿
balance_json=$(docker exec ambmh-api-1 python3 -c "
import urllib.request, json, os, base64
ft_user = os.environ.get('FT_USER', 'freqtrade')
ft_pass = os.environ.get('FT_PASS', 'freqtrade')
auth = base64.b64encode(f'{ft_user}:{ft_pass}'.encode()).decode()
try:
    req = urllib.request.Request(
        'http://freqtrade:8080/api/v1/balance',
        headers={'Authorization': f'Basic {auth}'},
    )
    r = urllib.request.urlopen(req, timeout=5)
    print(r.read().decode())
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null)
balance=$(echo "$balance_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total', 0))" 2>/dev/null || echo 0)
if [ -z "$balance" ] || [ "$balance" = "0" ]; then
    fail "could not read balance (got: $balance_json)"
else
    # Compare as float via python
    enough=$(python3 -c "print(1 if float($balance) >= float($REQUIRED_BALANCE_USDC) else 0)")
    if [ "$enough" = "1" ]; then
        ok "balance \$$balance ≥ floor \$$REQUIRED_BALANCE_USDC"
    else
        fail "balance \$$balance < floor \$$REQUIRED_BALANCE_USDC — fund OKX account first"
    fi
fi

# ─── Check 5: guard state clean (no stale pause from earlier sessions) ──
echo
echo "[5/8] guards state clean (no stale pause/streak/loss)"
state=$(docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/strategies && python3 -c "from guards.pipeline import get_state_summary; import json; print(json.dumps(get_state_summary(), default=str))"' 2>/dev/null || echo '{}')
paused=$(echo "$state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('paused_until', 0))" 2>/dev/null || echo 0)
streak=$(echo "$state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('consecutive_losses', 0))" 2>/dev/null || echo 0)
daily_loss=$(echo "$state" | python3 -c "import sys,json; print(json.load(sys.stdin).get('daily_loss', 0))" 2>/dev/null || echo 0)

now_ts=$(date -u +%s)
if [ "$paused" != "0" ] && [ "$(echo "$paused > $now_ts" | bc 2>/dev/null)" = "1" ]; then
    fail "ConsecutiveLossGuard is PAUSED until $paused (now: $now_ts) — wait or reset state"
    echo "    Reset: docker exec ambmh-freqtrade-1 rm /freqtrade/user_data/shared_data/guard_state.json"
    echo "           docker compose -f docker-compose.prod.yml restart freqtrade"
elif [ "$streak" != "0" ]; then
    warn "consecutive_losses=$streak (not paused yet, but ${streak}/5 toward pause)"
else
    ok "no pause, streak=0, daily_loss=\$$daily_loss"
fi

# ─── Check 6: dry-run has demonstrated entry capability ─────────────────
echo
echo "[6/8] dry-run history shows ≥${REQUIRED_DRYRUN_FIRES_7D} entries in last 7d"
ops=$(docker exec ambmh-api-1 python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/supertrend/operations?perf_window_days=7')
d = json.loads(r.read())
print(d['performance'].get('n_trades', 0))
" 2>/dev/null || echo 0)
if [ -z "$ops" ] || [ "$ops" -lt "$REQUIRED_DRYRUN_FIRES_7D" ]; then
    fail "only $ops entries in last 7d (need ≥$REQUIRED_DRYRUN_FIRES_7D to prove strategy actually fires)"
    echo "    If strategy is signal-rare (R89 ~1.5/month/5pairs), wait for more dry-run history."
    echo "    Going LIVE without proven entry capability risks a silent strategy that never trades."
else
    ok "$ops entries in last 7d — strategy has demonstrated entry capability"
fi

# ─── Check 7: SUPERTREND_LIVE currently 0 (sanity — about to flip to 1) ─
echo
echo "[7/8] SUPERTREND_LIVE currently 0 (sanity — flip happens AFTER preflight)"
live_now=$(docker exec ambmh-freqtrade-1 sh -c 'printenv SUPERTREND_LIVE || echo 0' 2>/dev/null | tr -d '\r')
if [ "$live_now" = "0" ]; then
    ok "SUPERTREND_LIVE=0 (dry-run) — preflight check valid"
elif [ "$live_now" = "1" ]; then
    warn "SUPERTREND_LIVE already = 1 — bot is ALREADY LIVE. preflight is moot."
else
    warn "SUPERTREND_LIVE='$live_now' (unexpected value)"
fi

# ─── Check 8: dry_run flag inside freqtrade matches env ─────────────────
echo
echo "[8/8] freqtrade /show_config dry_run reflects SUPERTREND_LIVE"
dry=$(docker exec ambmh-api-1 python3 -c "
import urllib.request, json, os, base64
ft_user = os.environ.get('FT_USER', 'freqtrade')
ft_pass = os.environ.get('FT_PASS', 'freqtrade')
auth = base64.b64encode(f'{ft_user}:{ft_pass}'.encode()).decode()
try:
    req = urllib.request.Request(
        'http://freqtrade:8080/api/v1/show_config',
        headers={'Authorization': f'Basic {auth}'},
    )
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    print('dry_run' if d.get('dry_run') else 'live')
except Exception as e:
    print(f'error:{e}')
" 2>/dev/null)
if [ "$live_now" = "0" ] && [ "$dry" = "dry_run" ]; then
    ok "freqtrade reports dry_run (consistent with SUPERTREND_LIVE=0)"
elif [ "$live_now" = "1" ] && [ "$dry" = "live" ]; then
    ok "freqtrade reports live (consistent with SUPERTREND_LIVE=1)"
else
    fail "inconsistency: SUPERTREND_LIVE=$live_now but freqtrade reports $dry"
fi

# ─── Summary ────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════"
if [ $fail_count -eq 0 ]; then
    green "✅ LIVE pre-flight PASSED"
    echo
    yellow "To go LIVE:"
    echo "  1. Edit /opt/ambmh/.env: SUPERTREND_LIVE=1"
    echo "  2. docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade"
    echo "  3. Watch /api/supertrend/operations for the first real trade"
    echo "  4. Verify TG alerts include 🟢/🔴 (real entry) not just 🛡️ (rejection)"
    echo
    yellow "Rollback (if anything goes wrong):"
    echo "  echo 'SUPERTREND_LIVE=0' >> /opt/ambmh/.env"
    echo "  # remove duplicate SUPERTREND_LIVE lines from .env afterwards"
    echo "  docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade"
    exit 0
else
    red "❌ LIVE pre-flight FAILED ($fail_count blocker(s))"
    echo
    yellow "DO NOT set SUPERTREND_LIVE=1 until all checks pass."
    yellow "Each ✗ above includes a fix command."
    exit 1
fi
