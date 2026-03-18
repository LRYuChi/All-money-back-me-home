#!/bin/bash
# ============================================================
# Disaster Recovery Script
# One-command VPS recovery after crash/rebuild
# Usage: bash scripts/disaster_recovery.sh
# ============================================================
set -euo pipefail

echo "=== All Money Back Me Home — Disaster Recovery ==="
echo "Target: 187.127.100.77"

# 1. Check SSH access
echo "[1/6] Testing SSH..."
ssh -o ConnectTimeout=5 root@187.127.100.77 "echo OK" || {
    echo "ERROR: Cannot SSH to VPS. Check password/key."
    exit 1
}

# 2. Pull latest code
echo "[2/6] Pulling latest code..."
ssh root@187.127.100.77 "cd /opt/ambmh && git pull origin main"

# 3. Restore .env if missing
echo "[3/6] Checking .env..."
ssh root@187.127.100.77 '
if [ ! -f /opt/ambmh/.env ]; then
    echo "ERROR: .env missing! Restoring from backup..."
    # Minimal .env — user must fill in secrets
    cat > /opt/ambmh/.env << EOF
NEXT_PUBLIC_SUPABASE_URL=https://tafsrggtrnkelaqrjebh.supabase.co
SUPABASE_URL=https://tafsrggtrnkelaqrjebh.supabase.co
API_BASE_URL=http://api:8000
CORS_ORIGINS=http://187.127.100.77
INITIAL_CAPITAL=300.0
LOG_LEVEL=INFO
FRED_API_KEY=08b56172e3e44a8a78b96231d168a55a
# FILL IN: NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY
# FILL IN: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
EOF
    echo "WARNING: .env restored with partial values. Fill in secrets!"
else
    echo ".env exists"
fi
'

# 4. Restore Freqtrade config
echo "[4/6] Checking Freqtrade config..."
ssh root@187.127.100.77 '
if [ ! -f /opt/ambmh/freqtrade/config/config.json ]; then
    echo "Freqtrade config missing — will be recreated on next deploy"
fi
'

# 5. Rebuild and start
echo "[5/6] Rebuilding Docker containers..."
ssh root@187.127.100.77 "cd /opt/ambmh && docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d"

# 6. Verify
echo "[6/6] Health check..."
sleep 30
ssh root@187.127.100.77 '
echo "Containers:"
docker ps --format "  {{.Names}}: {{.Status}}"
echo ""
echo "Endpoints:"
curl -so /dev/null -w "  Web: %{http_code}\n" http://localhost/
curl -so /dev/null -w "  API: %{http_code}\n" http://localhost/api/dashboard
curl -so /dev/null -w "  FT:  %{http_code}\n" http://localhost:8080/api/v1/ping -u freqtrade:freqtrade
'

echo ""
echo "=== Recovery Complete ==="
echo "Next steps:"
echo "  1. Verify .env has all secrets"
echo "  2. Start Freqtrade: curl -X POST http://187.127.100.77:8080/api/v1/start -u freqtrade:freqtrade"
echo "  3. Check Telegram bot is connected"
