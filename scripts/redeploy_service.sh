#!/bin/bash
# ============================================================
# Single-service hot redeploy — R64
#
# Usage:  bash scripts/redeploy_service.sh <service> [--rebuild]
#
# Wraps the patterns we kept running manually 5+ times this week:
#   1. preflight_check (catches the silent-break compose issues fast)
#   2. git pull (idempotent — no-op if VPS already in sync)
#   3. compose up (with --build only if --rebuild flag passed; defaults
#      to skip build since most deploys are config-only)
#   4. wait for service to settle
#   5. if api: nginx reload (cures the inevitable 502 on upstream cache)
#   6. if freqtrade: post-up curl /start (R60 autostart safety net)
#
# Safe to run any number of times — every step is idempotent.
# ============================================================
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <service> [--rebuild]"
    echo "Services: api, freqtrade, supertrend-cron, web, telegram-bot,"
    echo "          smart-money, smart-money-shadow, nginx"
    exit 2
fi

SERVICE="$1"
REBUILD_FLAG=""
if [ "${2:-}" = "--rebuild" ]; then
    REBUILD_FLAG="--build"
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Hot redeploy: $SERVICE ==="

echo "[1/6] Preflight..."
if ! python3 scripts/preflight_check.py; then
    echo "ERROR: preflight failed — aborting redeploy."
    exit 1
fi

echo "[2/6] git pull..."
git pull origin main 2>&1 | tail -3

echo "[3/6] Recreating $SERVICE..."
docker compose -f docker-compose.prod.yml up -d --no-deps $REBUILD_FLAG \
    --force-recreate "$SERVICE" 2>&1 | tail -5

# Settle wait scaled by what just changed
case "$SERVICE" in
    freqtrade)
        # Strategy module load + warmup + pairlist refresh takes longest
        WAIT=60 ;;
    supertrend-cron)
        WAIT=15 ;;
    *)
        WAIT=10 ;;
esac
echo "[4/6] Waiting ${WAIT}s for $SERVICE to settle..."
sleep "$WAIT"

# Post-redeploy fix-ups by service
echo "[5/6] Post-deploy fix-ups..."

# API recreate → nginx upstream DNS cache stale → 502 until reload.
# We've manually done this every API redeploy this week — bake it in.
if [ "$SERVICE" = "api" ] || [ "$SERVICE" = "web" ]; then
    echo "  Reloading nginx upstream DNS..."
    docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload \
        2>/dev/null || echo "  nginx reload failed — service may have 502 briefly"
fi

# Freqtrade recreate → bot defaults to stopped (R60). Issue /start.
if [ "$SERVICE" = "freqtrade" ]; then
    if [ -f .env ]; then
        FT_USER=$(grep -E '^FT_USER=' .env | cut -d= -f2- || echo freqtrade)
        FT_PASS=$(grep -E '^FT_PASS=' .env | cut -d= -f2- || echo freqtrade)
    fi
    FT_USER=${FT_USER:-freqtrade}
    FT_PASS=${FT_PASS:-freqtrade}
    state=$(curl -sf -u "${FT_USER}:${FT_PASS}" \
        "http://127.0.0.1:8080/api/v1/show_config" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))' \
        2>/dev/null || echo "unknown")
    case "$state" in
        running)  echo "  freqtrade bot: running ✓" ;;
        stopped)
            echo "  freqtrade bot: stopped — issuing /start..."
            curl -sf -u "${FT_USER}:${FT_PASS}" \
                -X POST "http://127.0.0.1:8080/api/v1/start" > /dev/null \
                && echo "  freqtrade bot: started ✓" \
                || echo "  /start failed — check container logs" ;;
        *)        echo "  freqtrade bot: state=$state (likely still booting)" ;;
    esac
fi

echo ""
echo "[6/6] Post-deploy verification (R109)..."
# Run verify_deploy.sh to catch silent-failure-class issues immediately
# (R104 incident: code shipped but guards.* not importable in freqtrade
# container went undetected for a week. verify_deploy probes the actual
# container runtime — if it FAILS here we want to know in red text +
# Telegram, not the next time someone opens the dashboard).
#
# Skip the verification only when REDEPLOY_SKIP_VERIFY=1 is set
# (e.g. nginx-only redeploy where API may briefly be down anyway).
if [ "${REDEPLOY_SKIP_VERIFY:-0}" = "1" ]; then
    echo "  REDEPLOY_SKIP_VERIFY=1 — skipping verify_deploy.sh"
elif [ -x scripts/verify_deploy.sh ]; then
    set +e
    bash scripts/verify_deploy.sh > /tmp/verify_deploy.log 2>&1
    verify_rc=$?
    set -e
    if [ "$verify_rc" -eq 0 ]; then
        echo "  ✓ verify_deploy passed"
    else
        echo ""
        echo "  ✗ verify_deploy FAILED — output:"
        cat /tmp/verify_deploy.log | sed 's/^/    /'
        # Telegram alert if creds are present (best-effort, don't crash redeploy)
        if [ -f .env ]; then
            TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' .env | cut -d= -f2- || true)
            TG_CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2- || true)
            if [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ]; then
                MSG="🚨 R109: redeploy of \`$SERVICE\` left the system FAILING verify_deploy. Check VPS for tail of /tmp/verify_deploy.log. Possible R104-class silent failure if guard import or env mismatch is reported."
                curl -sS -m 10 \
                    "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                    -d "chat_id=${TG_CHAT}" --data-urlencode "text=${MSG}" \
                    > /dev/null 2>&1 || echo "    (telegram alert send failed)"
            fi
        fi
        echo ""
        echo "  ⚠️  Service redeploy succeeded but verification flagged issues."
        echo "     Manual review required. Common causes:"
        echo "       - guard import failure (R104) → check freqtrade container can import guards.pipeline"
        echo "       - env mismatch (R94) → check api/freqtrade SUPERTREND_* envs match"
        echo "       - GUARDS_NEVER_FIRED — see incident_2026-04-26_silent_guards_failure.md"
        # Non-zero exit so CI / cron wrapper notice
        exit 3
    fi
else
    echo "  scripts/verify_deploy.sh not executable — skipping (run chmod +x)"
fi

echo ""
echo "=== $SERVICE redeploy complete ==="
docker compose -f docker-compose.prod.yml ps "$SERVICE"
