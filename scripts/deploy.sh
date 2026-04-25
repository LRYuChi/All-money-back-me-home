#!/bin/bash
# ============================================================
# All Money Back Me Home — Deploy Script
# Run: bash scripts/deploy.sh
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== AMBMH Deploy ==="
echo "Directory: $PROJECT_DIR"

# 1. Pull latest code
echo "[1/5] Pulling latest code..."
git pull origin main

# 2. Check .env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy from .env.example and configure."
    exit 1
fi

# 3. Build images
echo "[2/5] Building Docker images..."
docker compose -f docker-compose.prod.yml build

# 4. Run database migrations.
#
# Three-tier fallback:
#   1. supabase CLI if available (team's preferred path)
#   2. psycopg inside already-running smart-money container (zero extra deps,
#      just needs DATABASE_URL set in compose env)
#   3. skip + warn (first-ever deploy, smart-money not yet up)
#
# All migration files in supabase/migrations/*.sql MUST be idempotent
# (create table if not exists / add column if not exists); they are replayed
# on every deploy without harm.
if command -v supabase &> /dev/null; then
    echo "[3/5] Running database migrations via supabase CLI..."
    supabase db push || echo "  Migration skipped (check Supabase config)"
elif docker compose -f docker-compose.prod.yml ps smart-money 2>/dev/null | grep -q " Up "; then
    echo "[3/5] Running database migrations via smart-money container..."
    migration_count=0
    migration_failed=0
    for f in supabase/migrations/*.sql; do
        [ -f "$f" ] || continue
        migration_count=$((migration_count + 1))
        echo "  applying $(basename "$f")..."
        if ! cat "$f" | docker compose -f docker-compose.prod.yml exec -T smart-money python -c '
import sys, os, psycopg
sql = sys.stdin.read()
url = os.environ.get("DATABASE_URL", "")
if not url:
    print("ERR: DATABASE_URL not set in container", file=sys.stderr)
    sys.exit(1)
with psycopg.connect(url, autocommit=True) as c, c.cursor() as cur:
    cur.execute(sql)
'; then
            echo "    FAILED — continuing"
            migration_failed=$((migration_failed + 1))
        fi
    done
    echo "  migrations: $migration_count attempted, $migration_failed failed"
else
    echo "[3/5] No supabase CLI and smart-money container not running —"
    echo "      skipping migrations. Next deploy will pick them up once"
    echo "      smart-money comes online."
fi

# 5. Apply changes without full teardown.
#
# `up -d --build` lets docker compose reconcile: only services whose image
# OR compose definition changed get recreated. Unchanged services keep running.
#
# Why this matters: Supertrend (freqtrade container) is signal-rare — every
# full restart wipes its in-memory signal history. We used to `down && up -d`
# which disturbed freqtrade on every unrelated deploy and cost trades.
echo "[4/5] Applying changes (reconcile, only recreate what changed)..."
# --wait blocks until all services reach their healthy state (or timeout).
# This guarantees nginx reload below sees the *final* upstream IPs.
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans --wait --wait-timeout 120

# After web/api get recreated their IPs change; nginx's upstream DNS cache
# becomes stale → 502 until next resolve. Force nginx to refresh.
# `nginx -s reload` is sub-second and keeps the container alive.
echo "  Reloading nginx to refresh upstream DNS..."
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload 2>/dev/null || \
    docker compose -f docker-compose.prod.yml restart nginx

# 6. Health check
echo "[5/5] Waiting for services..."
sleep 5

if curl -sf http://localhost/health > /dev/null 2>&1; then
    echo "  API health: OK"
else
    echo "  API health: FAILED (checking logs...)"
    docker compose -f docker-compose.prod.yml logs --tail=20 api
fi

if curl -sf http://localhost > /dev/null 2>&1; then
    echo "  Web: OK"
else
    echo "  Web: FAILED (checking logs...)"
    docker compose -f docker-compose.prod.yml logs --tail=20 web
fi

# Freqtrade health — single container on 127.0.0.1:8080
# (legacy two-bot trend/scalp setup retired 2026-Q2)
echo "  Checking freqtrade..."
sleep 3

if [ -f .env ]; then
    FT_USER=$(grep -E '^FT_USER=' .env | cut -d= -f2- || echo freqtrade)
    FT_PASS=$(grep -E '^FT_PASS=' .env | cut -d= -f2- || echo freqtrade)
fi
FT_USER=${FT_USER:-freqtrade}
FT_PASS=${FT_PASS:-freqtrade}

if curl -sf -u "${FT_USER}:${FT_PASS}" "http://127.0.0.1:8080/api/v1/ping" > /dev/null 2>&1; then
    echo "  freqtrade: OK"
else
    echo "  freqtrade: STARTING (docker logs ambmh-freqtrade-1)"
fi

# R60: ensure freqtrade bot is in "running" state, not just "container up".
#
# CRITICAL: config_dry.json sets initial_state="running" but freqtrade's
# runtime ignores it (verified 2026-04-25 on VPS — Configuration.from_files
# loads the field, but Worker still boots into stopped). This curl /start
# is the SOLE reliable mechanism for getting the bot scanning after a
# container recreate. Without it, container Up + bot stopped = silent zero
# trading, no signals fired, journal empty.
#
# Also handles: operator manually stopped the bot via UI/API; that state
# survives container recreation, so we re-affirm at every deploy.
echo "  Verifying freqtrade bot state..."
sleep 2
ft_state=$(curl -sf -u "${FT_USER}:${FT_PASS}" \
    "http://127.0.0.1:8080/api/v1/show_config" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))' \
    2>/dev/null || echo "unknown")
case "$ft_state" in
    running)
        echo "  freqtrade bot: running ✓"
        ;;
    stopped)
        echo "  freqtrade bot: stopped — issuing /start..."
        curl -sf -u "${FT_USER}:${FT_PASS}" \
            -X POST "http://127.0.0.1:8080/api/v1/start" > /dev/null \
            && echo "  freqtrade bot: started ✓" \
            || echo "  freqtrade bot: /start failed (check container logs)"
        ;;
    *)
        echo "  freqtrade bot: state=$ft_state — not querying (likely still booting)"
        ;;
esac

echo ""
echo "=== Deploy complete! ==="
docker compose -f docker-compose.prod.yml ps
