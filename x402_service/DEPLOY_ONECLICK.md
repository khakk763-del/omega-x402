# OMEGA x402 — Render one-click deploy guide

**Status:** deploy-ready (2026-10-07). Nothing here spends money; the
Render plan is **free**. Income is still **$0 REALIZED** — this deploy
only creates the stable public URL; it does not create demand.

## What gets deployed

- Service name: `omega-x402` → expected URL **https://omega-x402.onrender.com**
- Runtime: Docker, build context = repo root (`income_assets/`),
  Dockerfile `x402_service/Dockerfile`, health check `/health`
- Env (from `render.yaml`, no secrets): `OMEGA_PAY_TO` (public Base
  receive address of the TaskMarket burner wallet — an address, not a
  key), `OMEGA_X402_NETWORK=eip155:8453`,
  `OMEGA_X402_FACILITATOR_URL=https://facilitator.payai.network`
- **No private keys, tokens, or `.env` are in the repo or the image**
  (see the security sweep in `x402_phase1_build_2026-10-07.md`).
  Never add a private key as an env var for this service.

## Prerequisite (done by Muse, one time)

The GitHub repo you connect must have these at its **root**:
`render.yaml`, `x402_service/`, `opportunity_intelligence/`
(i.e. the repo root is the `income_assets/` folder). `render.yaml` at
the root is the canonical Blueprint; the copy inside `x402_service/`
is a mirror.

## Click sequence (Khalid signed in to his free Render account)

1. Render dashboard → **New** → **Web Service** (if Render offers
   **New → Blueprint** for a repo containing `render.yaml`, that is
   the same one-click path — use it).
2. **Connect GitHub** (your account) → select the income-assets repo →
   grant Render access to that repo only.
3. Render reads **`render.yaml`** from the repo root and pre-fills:
   name `omega-x402`, runtime Docker, Dockerfile path
   `./x402_service/Dockerfile`, context `.`, health check `/health`,
   env vars. Do not change the Dockerfile path or context.
4. Confirm the plan is **Free** (change it if Render pre-selects a
   paid plan — never enter a card).
5. **Create Web Service** (Blueprint: **Apply**).
6. Wait for the first Docker build. The service URL appears in the
   dashboard: **https://omega-x402.onrender.com**

## Post-deploy checks (Muse runs these, ~2 minutes)

```bash
curl -s https://omega-x402.onrender.com/health
# expect 200 {"status":"OK","service":"omega-opportunity-intelligence",
#               "network":"eip155:8453","payTo":"0xCF33…1bD2", ...}

curl -si -X POST https://omega-x402.onrender.com/v1/x402/readiness \
  -H 'content-type: application/json' -d '{"url":"https://example.com"}'
# expect HTTP 402 with a PAYMENT-REQUIRED header (this is correct:
# unpaid calls are challenged, never served free)
```

If `/health` is not 200, stop and read the Render build/runtime logs
before changing anything — do not redeploy blindly.

## After the URL is stable

1. Muse registers every priced route on **x402scan** against
   `https://omega-x402.onrender.com` (the registration form needs the
   live stable URL; the rotating Serveo tunnel URL was deliberately
   not registered).
2. Phase 2 (later, optional): Khalid creates a free CDP portal API key
   → facilitator switches from PayAI to CDP → first mainnet settle
   auto-indexes the service in the CDP Bazaar. Keys go to the secure
   store, never into this repo or chat.

## Free-tier caveats (expected behaviour, not bugs)

- **Spin-down:** free web services sleep after ~15 minutes idle. The
  next request wakes them — a cold start of roughly 30–60 s, during
  which requests may hang or return a loading/5xx response. Paid calls
  that hit a sleeping service can fail once; a retry after wake
  succeeds. This is the known cost of the free plan.
- Render's own health checks do not keep a free service awake.
- Until this deploy exists, the Serveo tunnel URL in
  `current_public_url.txt` (rotates on restart, watchdog-refreshed)
  is the interim public endpoint.

## Rollback / kill switch

- Set env `OMEGA_KILL_SWITCH=1` in the Render dashboard → priced
  routes return 503, `/health` stays up.
- The burner wallet holds no funds by design; payouts settle to it
  per call and are withdrawn to Khalid's confirmed Base address via
  the TaskMarket withdrawal flow.
