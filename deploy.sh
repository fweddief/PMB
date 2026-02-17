#!/bin/bash
# ============================================================
# deploy.sh — Deploy Polymarket BTC 15-min trader to DigitalOcean
# ============================================================
# Prerequisites:
#   1. Create a DigitalOcean droplet:
#      - Region: Amsterdam (AMS3)
#      - Image: Ubuntu 24.04
#      - Size: Basic $6/mo (1 vCPU, 1GB RAM)
#      - Auth: SSH key (paste your ~/.ssh/id_*.pub) or password
#   2. Copy the droplet IP address
#   3. Run: bash deploy.sh <droplet-ip>
# ============================================================

set -euo pipefail

IP="${1:-}"
if [ -z "$IP" ]; then
    echo "Usage: bash deploy.sh <droplet-ip>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
REMOTE_USER="root"
REMOTE="$REMOTE_USER@$IP"
BOT_DIR="/root/bot"
SERVICE_NAME="polymarket-bot"

# Check .env exists locally
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    echo "The .env file contains your API keys and must exist in the project root."
    exit 1
fi

echo "==> Deploying to $IP..."

# Step 1: Copy .env to server (secrets never go in git)
echo "==> Uploading .env file..."
scp -o StrictHostKeyChecking=accept-new "$ENV_FILE" "$REMOTE:/tmp/bot.env"

# Step 2: SSH in and set everything up
echo "==> Setting up server..."
ssh -o StrictHostKeyChecking=accept-new "$REMOTE" bash -s "$BOT_DIR" "$SERVICE_NAME" <<'REMOTE_SCRIPT'
BOT_DIR="$1"
SERVICE_NAME="$2"
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "--- Installing system packages ---"
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3-pip git > /dev/null

# Clone or pull the repo
if [ -d "$BOT_DIR" ]; then
    echo "--- Updating existing repo ---"
    cd "$BOT_DIR"
    git pull --ff-only
else
    echo "--- Cloning repo ---"
    git clone https://github.com/fweddief/PMB.git "$BOT_DIR"
    cd "$BOT_DIR"
fi

# Set up venv
echo "--- Setting up Python venv ---"
if [ ! -d "$BOT_DIR/venv" ]; then
    python3.12 -m venv "$BOT_DIR/venv"
fi
"$BOT_DIR/venv/bin/pip" install --upgrade pip -q
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" -q

# Move .env into place
echo "--- Installing .env ---"
mv /tmp/bot.env "$BOT_DIR/.env"
chmod 600 "$BOT_DIR/.env"

# Create systemd service
echo "--- Creating systemd service ---"
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Polymarket BTC 15-min Momentum Trader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
Environment=PYTHONPATH=${BOT_DIR}/src
ExecStart=${BOT_DIR}/venv/bin/python src/services/arb_scanner_15m.py --live --budget 10
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
echo "--- Starting service ---"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "--- Done! ---"
systemctl status "$SERVICE_NAME" --no-pager || true
REMOTE_SCRIPT

echo ""
echo "============================================================"
echo " DEPLOYED SUCCESSFULLY"
echo "============================================================"
echo ""
echo " Monitor commands:"
echo "   ssh $REMOTE journalctl -u $SERVICE_NAME -f          # live logs"
echo "   ssh $REMOTE systemctl status $SERVICE_NAME           # status"
echo "   ssh $REMOTE systemctl stop $SERVICE_NAME             # stop"
echo "   ssh $REMOTE systemctl start $SERVICE_NAME            # start"
echo ""
echo " Update bot later:"
echo "   ssh $REMOTE \"cd $BOT_DIR && git pull && systemctl restart $SERVICE_NAME\""
echo ""
echo " Reboot server (bot auto-starts):"
echo "   ssh $REMOTE reboot"
echo "============================================================"
