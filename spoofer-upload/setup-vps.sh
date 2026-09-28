#!/usr/bin/env bash
# One-shot Spoofer server setup for a fresh Ubuntu VPS.
# Run as root, from inside the app folder (where server.py lives).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "==> Installing system packages (python, ffmpeg)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip ffmpeg

echo "==> Setting up Python environment…"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Ask for a login password if .env doesn't already have one.
if [ ! -f .env ]; then
  echo ""
  read -rp "Choose a LOGIN PASSWORD for your site: " PW
  cat > .env <<EOF
APP_PASSWORD=${PW}
PORT=9102
BIND=0.0.0.0
GIF_FPS=15
GIF_WIDTH=480
EOF
  echo "Saved password to .env"
fi

echo "==> Installing the always-on service…"
cat > /etc/systemd/system/spoofer.service <<EOF
[Unit]
Description=Spoofer
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/gunicorn --workers 2 --threads 4 --timeout 600 --bind 0.0.0.0:9102 server:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now spoofer

echo "==> Opening the firewall…"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp   >/dev/null 2>&1 || true
  ufw allow 9102/tcp >/dev/null 2>&1 || true
fi

IP="$(curl -fsS ifconfig.me 2>/dev/null || echo YOUR_SERVER_IP)"
echo ""
echo "======================================================"
echo "  DONE. Your Spoofer is live at:"
echo "     http://${IP}:9102"
echo "  Log in with the password you chose."
echo "======================================================"
echo ""
echo "Useful later:"
echo "  systemctl restart spoofer   # restart it"
echo "  systemctl status spoofer    # check it's running"
echo "  journalctl -u spoofer -e    # view logs"
