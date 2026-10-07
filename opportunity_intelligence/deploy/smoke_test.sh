#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 service.py --host 127.0.0.1 --port 8765 &
PID=$!
trap 'kill $PID >/dev/null 2>&1 || true' EXIT
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8765/health >/tmp/omega_health.json; then break; fi
  sleep 0.3
done
cat /tmp/omega_health.json
curl -fsS -X POST http://127.0.0.1:8765/v1/evaluate-opportunity \
  -H 'Content-Type: application/json' \
  -d '{"capital_required":0,"reward_usd":25,"probability":0.4,"risk":0.3,"automation_level":0.9,"status":"verified","payment_proof":"ESCROW_VERIFIED","verification":"OFFICIAL"}'
echo
curl -fsS -X POST http://127.0.0.1:8765/v1/evaluate-opportunity \
  -H 'Content-Type: application/json' \
  -d '{"capital_required":10,"reward_usd":100,"probability":0.9,"risk":0.1,"automation_level":0.9,"status":"verified","payment_proof":"ESCROW_VERIFIED","verification":"OFFICIAL"}'
echo
