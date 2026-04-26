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

# ─── Check 0: working-tree consistency (R113) ──────────────────────────
# R104 + R112 both incidents had the same root cause: prod
# strategies/supertrend.py md5 didn't match the git commit content
# because git reset --hard left the working tree stale (still unclear
# whether it's a docker-volume cache or git refspec issue). The fix is
# defensive — explicitly diff the file md5 vs the committed content
# md5 BEFORE running any other check. Catches "deployed but not really"
# in 2 seconds.
echo "[0/6] working-tree consistency (R113 — deploy lie detector)"
if [ -f /opt/ambmh/strategies/supertrend.py ]; then
    fs_md5=$(md5sum /opt/ambmh/strategies/supertrend.py | awk '{print $1}')
    git_md5=$(cd /opt/ambmh && git show HEAD:strategies/supertrend.py | md5sum | awk '{print $1}')
    if [ "$fs_md5" = "$git_md5" ]; then
        ok "strategies/supertrend.py md5 matches HEAD"
    else
        fail "strategies/supertrend.py md5 ≠ HEAD content (fs=$fs_md5, git=$git_md5) — stale working tree, deploy is a LIE"
        echo "    Fix: cd /opt/ambmh && git stash 2>/dev/null; git reset --hard origin/main"
    fi
else
    ok "(strategies/supertrend.py not on this host)"
fi

# ─── Check 0b: strategy file mtime vs freqtrade container start ────────
# R118: R113 (md5 lie detector) catches "git pulled but working tree
# stale". This catches the OTHER half: working tree is current but
# the container hasn't restarted to pick up the new code. strategy.py
# is mounted into freqtrade container — file changes are visible on
# filesystem but Python module already imported won't reload until
# container restart. Symptom would be identical to R104 (deploy looks
# done, prod runs old code).
echo
echo "[0b/6] strategies/supertrend.py mtime ≤ freqtrade container start (R118)"
file_mtime_iso=$(run "stat -c %y /opt/ambmh/strategies/supertrend.py 2>/dev/null | cut -d. -f1" 2>/dev/null || echo "")
container_start_iso=$(run "docker inspect -f '{{.State.StartedAt}}' ambmh-freqtrade-1 2>/dev/null" 2>/dev/null | cut -d. -f1)
if [ -z "$file_mtime_iso" ] || [ -z "$container_start_iso" ]; then
    yellow "  could not read mtime (file=$file_mtime_iso) or container start (=$container_start_iso) — skipping"
else
    file_epoch=$(date -d "$file_mtime_iso" +%s 2>/dev/null || echo 0)
    container_epoch=$(date -d "${container_start_iso/T/ }" +%s 2>/dev/null || echo 0)
    if [ "$file_epoch" -le "$container_epoch" ]; then
        ok "strategy mtime ($file_mtime_iso) ≤ container start ($container_start_iso)"
    else
        diff_min=$(( (file_epoch - container_epoch) / 60 ))
        fail "strategy.py modified ${diff_min}min AFTER freqtrade container start — container running OLD code"
        echo "    Fix: cd /opt/ambmh && docker compose -f docker-compose.prod.yml up -d --force-recreate freqtrade"
    fi
fi

# ─── Check 1: container health ──────────────────────────────────────────
echo
echo "[1/6] Container health"
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
echo "[2/6] freqtrade container can import guards (R104 fix)"
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

# ─── Check 2b: R115 backtest detection NOT mis-firing in prod ──────────
# R115 module-level _IS_BACKTEST detects backtest mode at module load
# from sys.argv. Risk: if freqtrade ever launches with backtest-like
# argv (future version, diagnostic flag, etc), prod trade mode would
# be mis-detected as backtest → journal writes ALL silently skipped →
# operator sees "0 trades" thinking strategy stuck when actually it's
# trading but not recording. Guard against this with explicit verify.
echo
echo "[2b/6] R115 backtest detection NOT mis-firing in prod"
r115_check=$(run "docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/strategies && python3 -c \"from supertrend import _IS_BACKTEST; print(_IS_BACKTEST)\" 2>&1'" 2>/dev/null || echo "EXEC_FAILED")
if [ "$r115_check" = "False" ]; then
    ok "_IS_BACKTEST=False (prod trade mode, journal writes ENABLED)"
elif [ "$r115_check" = "True" ]; then
    fail "_IS_BACKTEST=True in prod freqtrade container — journal writes DISABLED, will look like 0 trades. Check freqtrade entrypoint sys.argv."
else
    fail "could not read _IS_BACKTEST: $r115_check"
fi

# ─── Check 3: env consistency between api and freqtrade (R94 caveat) ────
echo
echo "[3/6] env consistency: api vs freqtrade SUPERTREND_*"
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

# ─── Check 4: /operations alerts + nginx 502 auto-recover ─────────────
# R113: when multiple containers recreate at once, nginx upstream DNS
# cache stays pointing to the old container IP → 502. We've hit this 3
# times now (R104 incident, R112 verify, this session). Auto-detect and
# reload nginx in-line so verify doesn't FAIL just because of stale DNS.
echo
echo "[4/6] /api/supertrend/operations alert check (auto-recover from nginx 502)"
nginx_status=$(run "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://localhost/api/supertrend/operations" 2>/dev/null || echo "000")
if [ "$nginx_status" = "502" ] || [ "$nginx_status" = "504" ]; then
    yellow "  nginx returned $nginx_status — auto-reloading upstream DNS"
    run "docker compose -f $COMPOSE_FILE exec -T nginx nginx -s reload" >/dev/null 2>&1 || true
    sleep 3
    nginx_status=$(run "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://localhost/api/supertrend/operations" 2>/dev/null || echo "000")
    if [ "$nginx_status" = "200" ]; then
        ok "nginx recovered after reload (was $nginx_status before)"
    else
        fail "nginx still returns $nginx_status after reload — manual investigation needed"
    fi
fi
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
echo "[5/6] freqtrade-side guards state (authoritative)"
state=$(run "docker exec ambmh-freqtrade-1 sh -c 'cd /freqtrade/user_data/strategies && python3 -c \"from guards.pipeline import get_state_summary; import json; print(json.dumps(get_state_summary(), default=str))\"'" 2>/dev/null || echo "{}")
if echo "$state" | grep -q "consecutive_losses"; then
    ok "freqtrade guards state readable: $state"
else
    fail "could not read freqtrade-side guards state"
fi

# ─── Check 6: scripts/*.sh +x permission audit (R124) ──────────────────
# R122 (polymarket_pipeline.sh)、R123 (4 個 scripts)、R124 (wfo_segment_v1.sh)
# 這三輪都是同一根因：git-tracked mode = 100644 → cron `timeout 240 /path/script.sh`
# 直接 exec 失敗 (Permission denied)，但 cron 不 alert，log 也只記一行。
# Defensive: 每次 deploy 後審查所有 scripts/*.sh 必須 +x，缺的列出來。
echo
echo "[6/6] scripts/*.sh +x permission audit (R122/R123/R124 pattern)"
nox=$(run "cd /opt/ambmh && find scripts -name '*.sh' -type f ! -perm -u+x 2>/dev/null" 2>/dev/null || echo "")
if [ -z "$nox" ]; then
    ok "all scripts/*.sh have +x permission"
else
    fail "scripts missing +x (cron will silently fail with 'Permission denied'):"
    echo "$nox" | sed 's/^/    /'
    echo "    Fix: chmod +x <file> && git update-index --chmod=+x <file> && git commit"
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
