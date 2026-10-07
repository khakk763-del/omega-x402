#!/usr/bin/env bash
# Fallback supervisor for environments without a systemd user bus
# (this VM runs as root with no user session). Starts the x402 service
# and the Serveo tunnel watchdog under nohup, logs to /tmp.
# When a real systemd user session exists, prefer the .service units:
#   systemctl --user enable --now omega-x402 omega-x402-tunnel
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

alive() { kill -0 "$1" 2>/dev/null; }

PID_FILE=/tmp/omega-x402.pid
TUN_FILE=/tmp/omega-x402-tunnel.pid

if [ -f "$PID_FILE" ] && alive "$(cat "$PID_FILE")"; then
  echo "service already running (pid $(cat "$PID_FILE"))"
else
  nohup .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8766 > /tmp/omega-x402.log 2>&1 &
  echo $! > "$PID_FILE"
  echo "service started (pid $(cat "$PID_FILE"))"
fi

for i in $(seq 1 15); do
  if curl -sS --max-time 3 http://127.0.0.1:8766/health > /dev/null 2>&1; then break; fi
  sleep 1
done

if [ -f "$TUN_FILE" ] && alive "$(cat "$TUN_FILE")"; then
  echo "tunnel already running (pid $(cat "$TUN_FILE"))"
else
  nohup ./tunnel_watchdog.sh > /tmp/omega-x402-watchdog.log 2>&1 &
  echo $! > "$TUN_FILE"
  echo "tunnel started (pid $(cat "$TUN_FILE"))"
fi

sleep 6
cat current_public_url.txt 2>/dev/null || true
curl -sS --max-time 5 http://127.0.0.1:8766/health || true
echo
