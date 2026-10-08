# OMEGA Opportunity Intelligence — x402 service (Phase 1 + Phase 2 facilitator)

Wraps the tested OMEGA screener (`../opportunity_intelligence/service.py`)
behind the official `x402` Python middleware (v2.25.0). Each priced call
settles USDC on Base at call time to the payTo wallet — no employer, no
pitch, no review queue.

## Facilitator selection (Phase 2)

The facilitator is chosen at startup from environment variables:

| Env state | Facilitator | Auth |
|---|---|---|
| `CDP_API_KEY_ID` **and** `CDP_API_KEY_SECRET` set | CDP Facilitator `https://api.cdp.coinbase.com/platform/v2/x402` | Official `cdp-sdk` (`cdp.x402.create_facilitator_config`) generates a fresh CDP JWT per verify/settle/supported call |
| otherwise | `OMEGA_X402_FACILITATOR_URL` (default `https://facilitator.payai.network`) | none (Phase 1) |

Why CDP: the CDP Facilitator indexes an endpoint in the **CDP Bazaar**
after it settles a payment for an endpoint advertising Bazaar metadata —
settling through PayAI does not index anything. First 1,000 onchain
facilitator transactions/month are free ($0.001 each after that;
verification is always free).

Key rules:

- `CDP_API_KEY_ID` is a UUID (visible in the CDP portal, not a secret).
- `CDP_API_KEY_SECRET` is a **secret**. It lives in Khalid's Secure Vault
  and is pasted by Khalid himself into the Render dashboard env vars.
  It is never committed, never baked into the Docker image, never logged,
  and never sent in chat.
- If the CDP env vars are set but `cdp-sdk` is missing, the app fails
  closed at startup with a clear error instead of silently downgrading
  to PayAI.
- `/health` reports the active facilitator (`facilitator` +
  `facilitator_url`); `/.well-known/x402` and the OpenAPI
  `x-payment-info` advertise the same active facilitator URL.

Phase 2 activation (Khalid's steps, once):

1. CDP portal → create a free Secret API key (done 2026-10-08,
   key name `omega-x402`).
2. Render dashboard → `omega-x402` service → Environment → add
   `CDP_API_KEY_ID` and `CDP_API_KEY_SECRET` (`sync: false` entries are
   already declared in `render.yaml`) → save. Render redeploys
   automatically on the push; env changes trigger their own deploy.
3. Verify: `GET /health` shows `"facilitator": "cdp"`.

Remaining gate after activation: Bazaar indexing happens only after the
**first real buyer payment settles via CDP**. A key alone does not index
anything and does not create demand.

## Endpoints

| Route | Price | What it does |
|---|---|---|
| GET /health | free | liveness + config echo |
| POST /v1/evaluate-opportunity | $0.05 | fail-closed opportunity screen (vetoes, score, EV) |
| POST /v1/evaluate-agent-terms | $0.03 | agent-terms gate + terms fingerprint |
| POST /v1/x402/readiness | $0.01 | x402 readiness audit of a public HTTPS endpoint |
| POST /v1/csv-json/convert | $0.01 | CSV↔JSON conversion (mode csv2json/json2csv) |
| GET /v1/agent/readiness?url= | $0.005 | public x402-readiness probe (SSRF-safe, 5s, never pays) |
| GET /v1/money-flow/latest | $0.02 | money-flow snapshot (signals only) |
| GET /v1/crypto/trending?chain&limit | $0.01 | DexScreener boosted/trending tokens, best pair each |
| GET /v1/defi/yields?min_tvl&limit&chain | $0.01 | top DeFi yields by APY (DefiLlama, TVL-filtered) |
| GET /v1/crypto/token-price?addresses= | $0.005 | token price + metrics for ≤3 token addresses |
| GET /v1/news/digest?topic&limit | $0.005 | crypto token-profile digest or Hacker News top stories |
| GET /v1/weather/current?city= | $0.003 | current weather + 3-day forecast (Open-Meteo) |
| GET /v1/chain/erc20-balance | $0.003 | ERC-20 balance/decimals/symbol (read-only) |
| GET /v1/chain/block-number | $0.001 | EVM block number (read-only) |

### Bazaar utility SKUs (2026-10-08 scale-up)

Five cheap utility/data calls aimed at the proven CDP Bazaar demand band
($0.001–$0.03). All are strictly read-only and served from public,
keyless sources:

- **Token price & trending** — DexScreener public API
  (`token-pairs/v1/{chain}/{address}`, `token-boosts/top/v1`).
- **Weather** — Open-Meteo forecast + geocoding (no key).
- **DeFi yields** — DefiLlama `yields.llama.fi/pools` (TVL ≥ $1M default,
  outlier pools excluded; ~11 MB upstream payload, cached 5 min).
- **News digest** — DexScreener token profiles (crypto) or the Hacker
  News Firebase API (tech). CryptoCompare/CMC news APIs require keys and
  are deliberately not used (zero-capital rule).
- All fetchers use hard timeouts (≤10 s), a small in-memory cache, and
  return honest upstream errors (502) rather than fabricating data.

## Config (.env, mode 600)

- `OMEGA_PAY_TO` — TaskMarket burner wallet (agent 98359)
- `OMEGA_X402_NETWORK` — `eip155:8453` (Base mainnet); `eip155:84532` for the Sepolia proof
- `OMEGA_X402_FACILITATOR_URL` — `https://facilitator.payai.network` (Phase 1 fallback, no API key; ignored when CDP keys are set)
- `CDP_API_KEY_ID` — CDP Secret API key ID (UUID); with `CDP_API_KEY_SECRET` enables the Phase 2 CDP Facilitator
- `CDP_API_KEY_SECRET` — CDP Secret API key secret (**secret**: Render dashboard / Secure Vault only, never in git or chat)
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
