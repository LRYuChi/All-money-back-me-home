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

# 4. Run database migrations (if Supabase CLI available)
if command -v supabase &> /dev/null; then
    echo "[3/5] Running database migrations..."
    supabase db push || echo "  Migration skipped (check Supabase config)"
else
    echo "[3/5] Supabase CLI not found — skipping migrations"
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
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans

# After web/api get recreated their IPs change; nginx's upstream DNS cache
# becomes stale → 502 until next resolve. Force nginx to refresh by reloading
# (cheap, sub-second; doesn't restart the whole container).
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

echo ""
echo "=== Deploy complete! ==="
docker compose -f docker-compose.prod.yml ps
