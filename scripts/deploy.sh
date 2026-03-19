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

# 5. Restart services
echo "[4/5] Restarting services..."
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

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

# Freqtrade bots health check (may take longer to start)
echo "  Waiting for Freqtrade bots (10s)..."
sleep 10

for bot in "freqtrade-trend:8080" "freqtrade-scalp:8081"; do
    name="${bot%%:*}"
    port="${bot##*:}"
    if curl -sf -u freqtrade:freqtrade "http://localhost:${port}/api/v1/show_config" > /dev/null 2>&1; then
        echo "  ${name}: OK"
    else
        echo "  ${name}: STARTING (check logs: docker compose -f docker-compose.prod.yml logs ${name})"
    fi
done

echo ""
echo "=== Deploy complete! ==="
docker compose -f docker-compose.prod.yml ps
