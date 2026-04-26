#!/usr/bin/env bash
# Verify deploy — R104 防呆 B
#
# After every redeploy, run this script to confirm:
#   1. Containers are healthy
#   2. freqtrade container can ACTUALLY import guards (R104 silent failure check)
#   3. API container's switchboard env matches freqtrade's (R94 caveat check)
#   4. /operations endpoint returns no GUARDS_NEVER_FIRED-class alerts
#
# Exit 0 → deploy verified
# Exit 1 → at least one check failed (script prints which)
#
# Usage:
#   ssh root@VPS "cd /opt/ambmh && bash scripts/verify_deploy.sh"
# or locally with VPS_HOST=root@1.2.3.4 prefix.

set -u
set -o pipefail

VPS_HOST="${VPS_HOST:-}"        # if empty, assume running ON the VPS
COMPOSE_FILE="${COMPOSE_FILE:-/opt/ambmh/docker-compose.prod.yml}"

# ─── helpers ────────────────────────────────────────────────────────────
red() { printf "\033[31m%s\033[0m\n" "$1"; }
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

run() {
    if [ -n "$VPS_HOST" ]; then
        ssh "$VPS_HOST" "$@"
    else
        eval "$@"
    fi
}

fail_count=0
fail() {
    red "  ✗ $1"
    fail_count=$((fail_count + 1))
}
ok() { green "  ✓ $1"; }

# ─── Check 1: container health ──────────────────────────────────────────
echo "[1/5] Container health"
ps_out=$(run "docker ps --format '{{.Names}}|{{.Status}}'" 2>/dev/null || true)
for svc in api freqtrade telegram-bot supertrend-cron smart-money smart-money-shadow; do
    line=$(echo "$ps_out" | grep "ambmh-${svc}-1" || true)
    if [ -z "$line" ]; then
        fail "${svc} not running"
    elif echo "$line" | grep -q "(healthy)"; then
        ok "${svc} healthy"
    elif echo "$line" | grep -qE "Up [0-9]+ (seconds|second)"; then
        yellow "  … ${svc} just started (health pending)"
    elif echo "$line" | grep -q "Up "; then
        ok "${svc} up (no healthcheck)"
    else
        fail "${svc} status: $(echo "$line" | cut -d'|' -f2)"
    fi
done

# ─── Check 2: freqtrade can import guards (R104 root cause check) ──────
echo
echo "[2/5] freqtrade container can import guards (R104 fix)"
guards_check=$(run "docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/strategies && python3 -c \"from guards.pipeline import create_default_pipeline; print(len(create_default_pipeline().guards))\" 2>&1'" 2>/dev/null || echo "EXEC_FAILED")
if echo "$guards_check" | grep -qE "^[0-9]+$"; then
    n=$(echo "$guards_check" | head -1)
    if [ "$n" -ge 9 ]; then
        ok "guards.pipeline imports + builds $n guards"
    else
        fail "expected ≥9 guards, got $n"
    fi
else
    fail "guards import FAILED — R104 silent-failure pattern: $guards_check"
fi

# ─── Check 3: env consistency between api and freqtrade (R94 caveat) ────
echo
echo "[3/5] env consistency: api vs freqtrade SUPERTREND_*"
mismatch=0
for var in SUPERTREND_DISABLE_CONFIRMED SUPERTREND_VOL_MULT SUPERTREND_KELLY_MODE \
           SUPERTREND_QUALITY_MIN SUPERTREND_REQUIRE_ATR_RISING \
           SUPERTREND_GUARDS_ENABLED SUPERTREND_GUARDS_REQUIRE_LOAD; do
    api_v=$(run "docker exec ambmh-api-1 sh -c 'printenv $var || echo MISSING'" 2>/dev/null | tr -d '\r')
    ft_v=$(run "docker exec ambmh-freqtrade-1 sh -c 'printenv $var || echo MISSING'" 2>/dev/null | tr -d '\r')
    if [ "$api_v" = "$ft_v" ]; then
        ok "$var = $api_v (consistent)"
    else
        fail "$var mismatch: api=$api_v, freqtrade=$ft_v"
        mismatch=$((mismatch + 1))
    fi
done

# ─── Check 4: /operations alerts ─────────────────────────────────────────
echo
echo "[4/5] /api/supertrend/operations alert check"
ops_alerts=$(run "docker exec ambmh-api-1 python3 -c \"
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/supertrend/operations')
d = json.loads(r.read())
for a in d.get('alerts', []):
    print(a)
\"" 2>/dev/null || echo "")
if [ -z "$ops_alerts" ]; then
    ok "no alerts"
else
    silent=$(echo "$ops_alerts" | grep -c "GUARDS_NEVER_FIRED" || true)
    if [ "$silent" -gt 0 ]; then
        fail "GUARDS_NEVER_FIRED alert active — guards may not be rejecting any entries"
    fi
    # NO_FIRES_24H is informational, not a deploy-blocker
    other=$(echo "$ops_alerts" | grep -vE "NO_FIRES_24H|EVAL_RATE_LOW" || true)
    if [ -n "$other" ]; then
        echo "  Non-blocking alerts present:"
        echo "$other" | sed 's/^/    /'
    fi
fi

# ─── Check 5: guards state from freqtrade (truth source) ─────────────────
echo
echo "[5/5] freqtrade-side guards state (authoritative)"
state=$(run "docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/strategies && python3 -c \"from guards.pipeline import get_state_summary; import json; print(json.dumps(get_state_summary(), default=str))\"'" 2>/dev/null || echo "{}")
if echo "$state" | grep -q "consecutive_losses"; then
    ok "freqtrade guards state readable: $state"
else
    fail "could not read freqtrade-side guards state"
fi

# ─── Summary ────────────────────────────────────────────────────────────
echo
if [ $fail_count -eq 0 ]; then
    green "✅ Deploy verification PASSED"
    exit 0
else
    red "❌ Deploy verification FAILED ($fail_count check(s))"
    echo
    yellow "Recovery:"
    echo "  - guards import fail → R104 fix not deployed; check git pull + restart freqtrade"
    echo "  - env mismatch → check docker-compose.prod.yml api section forwards SUPERTREND_* vars"
    echo "  - GUARDS_NEVER_FIRED → see incident_2026-04-26_silent_guards_failure.md"
    echo "  - container down → docker compose -f $COMPOSE_FILE up -d <service>"
    exit 1
fi
