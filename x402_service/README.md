# OMEGA Opportunity Intelligence — x402 service (Phase 1)

Wraps the tested OMEGA screener (`../opportunity_intelligence/service.py`)
behind the official `x402` Python middleware (v2.25.0). Each priced call
settles USDC on Base at call time to the payTo wallet — no employer, no
pitch, no review queue.

## Endpoints

| Route | Price | What it does |
|---|---|---|
| GET /health | free | liveness + config echo |
| POST /v1/evaluate-opportunity | $0.05 | fail-closed opportunity screen (vetoes, score, EV) |
| POST /v1/evaluate-agent-terms | $0.03 | agent-terms gate + terms fingerprint |
| POST /v1/x402/readiness | $0.01 | x402 readiness audit of a public HTTPS endpoint |
| POST /v1/csv-json/convert | $0.01 | CSV↔JSON conversion (mode csv2json/json2csv), returns converted string + row count |
| GET /v1/agent/readiness?url= | $0.005 | public x402-readiness probe of a third-party HTTPS URL (SSRF-safe, 5s timeout, never pays) |
| GET /v1/money-flow/latest | $0.02 | money-flow snapshot (signals only) |
| GET /v1/chain/block-number | $0.003 | EVM block number (read-only) |
| GET /v1/chain/erc20-balance | $0.003 | ERC-20 balance/decimals/symbol (read-only) |

## Config (.env, mode 600)

- `OMEGA_PAY_TO` — TaskMarket burner wallet (agent 98359)
- `OMEGA_X402_NETWORK` — `eip155:8453` (Base mainnet); `eip155:84532` for the Sepolia proof
- `OMEGA_X402_FACILITATOR_URL` — `https://facilitator.payai.network` (Phase 1, no API key)
- `OMEGA_KILL_SWITCH=1` — paid routes return 503, /health stays up
- `OMEGA_RATE_LIMIT_PER_MIN` — per-IP request cap (default 60)
- `OMEGA_MAX_BODY_BYTES` — request size cap (default 64000)

## Run

```bash
cd ~/workspace/goals/1000-2000/files/OMEGA/income_assets/x402_service
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8766
# or the whole stack (service + public tunnel watchdog, no systemd needed):
./run_stack.sh
# with systemd (user session that has a bus):
#   systemctl --user enable --now omega-x402 omega-x402-tunnel
```

## Public URL

Live since 2026-10-07 via a Serveo SSH tunnel through the HTTP proxy
(`tunnel_watchdog.sh` keeps it alive; cloudflared cannot reach the
Cloudflare edge from this VM — raw TCP/QUIC egress is intercepted and
the local DNS stub breaks SRV; see the build report's "Public hosting
outcome" section). The current URL is always in `current_public_url.txt`
and **changes whenever the tunnel restarts**. A stable listable URL
needs one free PaaS signup by Khalid (Render recommended; `Dockerfile`
+ `render.yaml` are ready). x402scan listing waits for that stable URL.

## Proof status

- 402 challenge shape verified locally for mainnet config and Sepolia config
  (scheme `exact`, network CAIP-2, Base USDC asset, payTo burner, Bazaar
  discovery extensions present).
- Sepolia settlement proof (`proof_sepolia.py`): payer is an ephemeral
  testnet keypair (address in `proof_payer.json`). The full loop ran
  402 → signed EIP-3009 → facilitator verify, which correctly re-challenged
  because the payer holds 0 testnet USDC. **Blocked step:** funding the
  payer from faucet.circle.com is reCAPTCHA-gated (human step); no
  sanctioned programmatic testnet funding exists through the TaskMarket
  CLI keystore tooling (mainnet only). Once the payer holds ≥0.05 Sepolia
  USDC, `PROOF_WAIT_SECONDS=0 .venv/bin/python proof_sepolia.py` completes
  the settle → 200 leg with tx evidence.

## Treasury watch

```bash
export NODE_USE_ENV_PROXY=1 NODE_OPTIONS=--use-env-proxy
~/workspace/.npm-global/bin/taskmarket wallet balance
```

Income counts only when USDC settles in the burner wallet; from there it
withdraws to Khalid's confirmed Base address (`0x8b77…43dC`) via the
registered withdrawal address.
