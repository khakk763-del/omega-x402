# OMEGA Opportunity Intelligence

An owned, sellable digital service asset built from OMEGA's verification logic. It evaluates whether a Web3/agent-work opportunity is actually executable under zero-capital, terms, payment-proof, and automation gates.

## Status

- Local service: **BUILT**
- Tests: see `test_service.py`
- Public deployment: **UNAVAILABLE_NOT_LAUNCHED**
- Payment collection: **UNAVAILABLE_NO_PAYMENT_IDENTITY**
- Revenue: **$0** until a real customer payment is verified in Treasury

## Endpoints

- `GET /health`
- `POST /v1/evaluate-opportunity`
- `POST /v1/evaluate-agent-terms`
- `POST /v1/x402/readiness` with `{"url":"https://..."}` — unpaid public endpoint inspection; never pays or signs
- `GET /v1/money-flow/latest`
- `GET /v1/chain/block-number?network=ethereum`
- `GET /v1/chain/erc20-balance?address=0x...&contract=0x...&network=ethereum`
- `GET /v1/chain/live-balance?address=0x...&tokens=0x...,0x...&network=ethereum`

## Run locally

```bash
python3 service.py --host 127.0.0.1 --port 8765
```

## Monetization boundary

A public paid launch still needs a user-approved hosting target and a user-owned payment route (for example an x402 facilitator plus a public receive address on the correct network, or a conventional checkout owned by the user). OMEGA will not create a wallet, invent a receive address, or claim sales before verified payment proof exists.
