#!/usr/bin/env bash
# Public-tunnel watchdog (zero spend, no account).
#
# Why not Cloudflare: this VM blocks raw TCP/QUIC egress (only HTTP(S)
# via the proxy works), and the local DNS stub breaks SRV lookups, so
# cloudflared cannot reach the Cloudflare edge from here (verified
# 2026-10-07). SSH tunnels through the HTTP proxy (CONNECT to port 443)
# DO work. Serveo is primary (no session expiry while connected);
# Pinggy free sessions expire after 60 minutes, so Serveo wins.
#
# The watchdog keeps an ssh reverse tunnel alive, extracts the current
# public URL into current_public_url.txt, and health-checks it.
# The subdomain changes whenever the tunnel restarts; a stable URL
# still needs a PaaS deploy or a named-tunnel account (see README).
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL_FILE="$DIR/current_public_url.txt"
LOG=/tmp/omega-x402-tunnel.log
SSH_LOG=/tmp/omega-x402-serveo.log

start_tunnel() {
  pkill -f 'ssh .*serveo.net' 2>/dev/null || true
  sleep 1
  nohup ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o ProxyCommand="nc -X connect -x hatch-egress-proxy:3128 %h %p" \
    -p 443 -R 80:127.0.0.1:8766 serveo.net > "$SSH_LOG" 2>&1 &
  echo $!
}

extract_url() {
  grep -oE 'https://[a-z0-9-]+\.serveousercontent\.com' "$SSH_LOG" 2>/dev/null | tail -1
}

echo "[watchdog] starting $(date -u +%FT%TZ)" >> "$LOG"
while true; do
  if ! pgrep -f 'ssh .*serveo.net' > /dev/null 2>&1; then
    echo "[watchdog] tunnel down, restarting $(date -u +%FT%TZ)" >> "$LOG"
    start_tunnel > /dev/null
    sleep 8
  fi
  url="$(extract_url || true)"
  if [ -n "${url:-}" ]; then
    echo "$url" > "$URL_FILE"
    if ! curl -sS --max-time 12 "$url/health" > /dev/null 2>&1; then
      echo "[watchdog] public health check failed for $url, restarting tunnel" >> "$LOG"
      pkill -f 'ssh .*serveo.net' 2>/dev/null || true
      sleep 2
    fi
  fi
  sleep 60
done
