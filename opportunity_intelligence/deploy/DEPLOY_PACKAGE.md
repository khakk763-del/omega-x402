# OMEGA Opportunity Intelligence — Launch Package (Not Launched)

Status: PREPARED_NOT_LAUNCHED
Revenue: $0 until a real third-party payment settles and is reconciled in Treasury.

## What exists

- Local dependency-free Python service: `../service.py`
- Tests: `../test_service.py`
- Proposed prices: `../pricing.json` (not active)
- Endpoints: health, opportunity evaluation, agent-terms evaluation, x402 readiness inspection, money-flow snapshot, Ethereum read-only chain data.

## What OMEGA prepared

- Local run and smoke-test path.
- Environment template with placeholders only.
- Launch checklist separating system work from owner-only identity/payment work.

## Owner-only inputs required before any public launch

1. Hosting account approved by the user (for example the user's own Cloudflare/Fly/Render/VPS account). OMEGA will not create the account or accept its terms for the user.
2. Payment route chosen by the user:
   - PayAI x402: no facilitator API key needed within the free allowance, but a user-owned `payTo` address is required.
   - Coinbase CDP x402: user-owned CDP API credentials and `payTo`; Bazaar discovery only after a first settled payment.
3. Receive asset/network confirmed by the user, for example Base mainnet USDC (`eip155:8453`) to a user-owned EVM address. Do not reuse an Ethereum-mainnet address by assumption unless the user confirms it for Base too.
4. Public domain/URL and abuse controls: rate limits, request size limits, logging without secrets, and a kill switch.

## Launch sequence after owner inputs exist

1. Run the local smoke test in this folder.
2. Deploy the service to the user-approved host.
3. Configure facilitator/payTo from the user's own account or secure environment; never paste secrets in chat.
4. Verify unpaid requests return `402` with correct `accepts[]` on the chosen network/asset/payTo.
5. Do a testnet check if needed (not income).
6. Go live only with a real third-party buyer path. Self-payment is not income.
7. On settlement, record transaction hash, asset, network, amount, fees, and receiving address in OMEGA Treasury. Only then can revenue move from $0.

## Hard stops

- No wallet or key creation by OMEGA.
- No receive address invented by OMEGA.
- No counting catalog listing, 402 response, testnet payment, or self-payment as revenue.
- No public launch without rate limits and a way to disable the endpoint.
