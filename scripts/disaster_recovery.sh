#!/bin/bash
# ============================================================
# Disaster Recovery Script
# One-command VPS recovery after crash/rebuild
# Usage: bash scripts/disaster_recovery.sh
# ============================================================
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@187.127.100.77}"

echo "=== All Money Back Me Home — Disaster Recovery ==="
echo "Target: ${VPS_HOST}"

# 1. Check SSH access
echo "[1/6] Testing SSH..."
ssh -o ConnectTimeout=5 "${VPS_HOST}" "echo OK" || {
    echo "ERROR: Cannot SSH to VPS. Check password/key."
    exit 1
}

# 2. Pull latest code
echo "[2/6] Pulling latest code..."
ssh "${VPS_HOST}" "cd /opt/ambmh && git pull origin main"

# 3. Check .env exists (DO NOT create with hardcoded secrets)
echo "[3/6] Checking .env..."
ssh "${VPS_HOST}" '
if [ ! -f /opt/ambmh/.env ]; then
    echo "ERROR: .env missing!"
    echo "Copy .env.example and fill in secrets manually:"
    echo "  cd /opt/ambmh && cp .env.example .env && nano .env"
    exit 1
else
    echo ".env exists"
fi
'

# 4. Check Freqtrade config
echo "[4/6] Checking Freqtrade config..."
ssh "${VPS_HOST}" '
if [ ! -f /opt/ambmh/config/freqtrade/config_secrets.json ]; then
    echo "WARNING: config_secrets.json missing — Freqtrade will not connect to exchange"
    echo "Create it manually: cp config/freqtrade/config_secrets.json.example config/freqtrade/config_secrets.json"
fi
'

# 5. Rebuild and start
echo "[5/6] Rebuilding Docker containers..."
ssh "${VPS_HOST}" "cd /opt/ambmh && docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d"

# 6. Verify
echo "[6/6] Health check (waiting 30s for services to start)..."
sleep 30
ssh "${VPS_HOST}" '
echo "Containers:"
docker ps --format "  {{.Names}}: {{.Status}}"
echo ""
echo "Endpoints:"
curl -so /dev/null -w "  Web: %{http_code}\n" http://localhost/ 2>/dev/null || echo "  Web: FAILED"
curl -so /dev/null -w "  API: %{http_code}\n" http://localhost/api/dashboard 2>/dev/null || echo "  API: FAILED"
curl -so /dev/null -w "  FT:  %{http_code}\n" -u "${FT_USER:-freqtrade}:${FT_PASS:-freqtrade}" http://localhost:8080/api/v1/ping 2>/dev/null || echo "  FT:  FAILED"
'

echo ""
echo "=== Recovery Complete ==="
echo "Next steps:"
echo "  1. Verify .env has all secrets (nano /opt/ambmh/.env)"
echo "  2. Verify config_secrets.json has exchange credentials"
echo "  3. Check Telegram bot: docker logs ambmh-telegram-bot-1 --tail 20"
