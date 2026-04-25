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

echo "[1/5] Preflight..."
if ! python3 scripts/preflight_check.py; then
    echo "ERROR: preflight failed — aborting redeploy."
    exit 1
fi

echo "[2/5] git pull..."
git pull origin main 2>&1 | tail -3

echo "[3/5] Recreating $SERVICE..."
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
echo "[4/5] Waiting ${WAIT}s for $SERVICE to settle..."
sleep "$WAIT"

# Post-redeploy fix-ups by service
echo "[5/5] Post-deploy fix-ups..."

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
echo "=== $SERVICE redeploy complete ==="
docker compose -f docker-compose.prod.yml ps "$SERVICE"
