#!/bin/bash
# ============================================================
# All Money Back Me Home — VPS Initial Setup (Ubuntu 22.04+)
# Run: bash scripts/setup-server.sh
# ============================================================
set -euo pipefail

echo "=== AMBMH Server Setup ==="

# 1. System updates
echo "[1/6] Updating system..."
apt-get update && apt-get upgrade -y

# 2. Install Docker
echo "[2/6] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    usermod -aG docker "$USER"
    echo "  Docker installed. Re-login for group changes."
else
    echo "  Docker already installed."
fi

# 3. Install Docker Compose plugin
echo "[3/6] Checking Docker Compose..."
if ! docker compose version &> /dev/null; then
    apt-get install -y docker-compose-plugin
fi
docker compose version

# 4. Firewall (UFW)
echo "[4/6] Configuring firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable
ufw status

# 5. Create project directory
echo "[5/6] Setting up project directory..."
mkdir -p /opt/ambmh
mkdir -p /var/log/ambmh

# 6. SSL with Certbot (optional)
echo "[6/6] Install Certbot (run manually when domain is ready):"
echo "  apt install certbot"
echo "  certbot certonly --standalone -d your-domain.com"
echo ""
echo "=== Setup complete! ==="
echo "Next steps:"
echo "  1. cd /opt/ambmh"
echo "  2. git clone <your-repo> ."
echo "  3. cp .env.example .env && nano .env"
echo "  4. bash scripts/deploy.sh"
