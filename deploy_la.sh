#!/usr/bin/env bash
# Deploy tonpal → LA VPS (Bot API). See RpA/SYSTEM_CONFIG.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/vps_key}"
HOST="${TONPAL_HOST:-root@149.28.76.26}"
REMOTE="${TONPAL_REMOTE:-/root/tonpal}"

echo ">>> rsync $ROOT/ → $HOST:$REMOTE/"
rsync -avz \
  --exclude '.env' \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.cursor' \
  --exclude 'data/*.sqlite3' \
  --exclude '.groq_backup' \
  -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
  "$ROOT/" "$HOST:$REMOTE/"

echo ">>> pip + systemd on $HOST"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$HOST" bash -s <<REMOTE
set -e
pip3 install -q -r "$REMOTE/requirements.txt"
install -m 644 "$REMOTE/tonpal.service" /etc/systemd/system/tonpal.service
systemctl daemon-reload
systemctl enable tonpal 2>/dev/null || true
systemctl restart tonpal
sleep 1
systemctl --no-pager status tonpal || true
echo ">>> last logs:"
journalctl -u tonpal -n 25 --no-pager
REMOTE

echo ">>> done."
