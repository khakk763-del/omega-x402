#!/usr/bin/env python3
"""
OMEGA Opportunity Intelligence — x402 sell-side service (Phase 1).

Wraps the existing dependency-free screener
(../opportunity_intelligence/service.py) behind the official `x402`
Python middleware so each priced call settles USDC on Base at call
time to the TaskMarket burner wallet (payTo). No employer, no pitch,
no review queue.

Facilitator: PayAI (https://facilitator.payai.network), no API key.
Network:     eip155:8453 (Base mainnet) by default;
             set OMEGA_X402_NETWORK=eip155:84532 for the Sepolia proof run.

Kill switch: OMEGA_KILL_SWITCH=1 -> paid routes return 503, /health stays up.
Rate limit:  OMEGA_RATE_LIMIT_PER_MIN (per-client-IP, in-memory, default 60).
Body cap:    OMEGA_MAX_BODY_BYTES (default 64000, matches the screener).

Secrets: none are required for Phase 1. Nothing signs anything server-side.
"""
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv(Path(__file__).with_name(".env"))

# VM egress quirk: the inherited no_proxy contains bracketed IPv6 entries
# that crash httpx's URL parsing, while direct egress is blocked. Keep the
# proxy but sanitize no_proxy to plain hosts httpx can parse.
for _v in ("no_proxy", "NO_PROXY"):
    _vals = [p.strip("[]") for p in os.getenv(_v, "").split(",") if p.strip()]
    os.environ[_v] = ",".join(x for x in _vals if ":" not in x and x)

# ---- import the existing, tested screener logic (unchanged) ----
SVC_DIR = Path(__file__).resolve().parent
OMEGA_ASSETS = SVC_DIR.parent
sys.path.insert(0, str(OMEGA_ASSETS / "opportunity_intelligence"))
sys.path.insert(0, str(OMEGA_ASSETS))
import service as screener  # noqa: E402

# ---- config ----
PAY_TO = os.getenv("OMEGA_PAY_TO", "0xCF3388dB231300f88285351154FbC17098A01bD2")
NETWORK = os.getenv("OMEGA_X402_NETWORK", "eip155:8453")
FACILITATOR_URL = os.getenv("OMEGA_X402_FACILITATOR_URL", "https://facilitator.payai.network")
KILL_SWITCH = os.getenv("OMEGA_KILL_SWITCH", "0") == "1"
RATE_LIMIT_PER_MIN = int(os.getenv("OMEGA_RATE_LIMIT_PER_MIN", "60"))
MAX_BODY_BYTES = int(os.getenv("OMEGA_MAX_BODY_BYTES", "64000"))

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption  # noqa: E402
from x402.http.middleware.fastapi import PaymentMiddlewareASGI  # noqa: E402
from x402.http.types import RouteConfig  # noqa: E402
from x402.mechanisms.evm.exact import ExactEvmServerScheme  # noqa: E402
from x402.server import x402ResourceServer  # noqa: E402
from x402.extensions.bazaar import declare_discovery_extension  # noqa: E402
from x402.extensions.bazaar.resource_service import OutputConfig  # noqa: E402


# ---- Bazaar discovery extensions (x402 v2, CDP/x402scan shapes) ----
# `declare_discovery_extension` returns {"bazaar": {...}} — that return value
# IS the extensions fragment; it must be used directly, never re-wrapped in an
# outer {"bazaar": ...} (the old code double-nested it, which discovery
# catalogs reject as schema-invalid).
#
# GET routes use query extensions (no body_type) so the 402 advertises
# `queryParams`, not a JSON body; POST routes keep body_type="json".
#
# Every priced route declares BOTH an output example (info.output) and an
# output schema (schema.properties.output.properties.example), matching the
# `OutputConfig(example=..., schema=...)` contract. Without the schema, CDP
# Bazaar and x402scan index the resource as non-invocable-unknown-output.

_EVM_ADDRESS_PATTERN = "^0x[a-fA-F0-9]{40}$"


def _bazaar_body(input_example: dict, input_schema: dict,
                 output_example: dict, output_schema: dict) -> dict:
    """Discovery extension for POST routes (JSON request body)."""
    return declare_discovery_extension(
        input=input_example,
        input_schema=input_schema,
        body_type="json",
        output=OutputConfig(example=output_example, schema=output_schema),
    )


def _bazaar_query(input_example: dict, input_schema: dict,
                  output_example: dict, output_schema: dict) -> dict:
    """Discovery extension for GET routes (query parameters)."""
    return declare_discovery_extension(
        input=input_example,
        input_schema=input_schema,
        output=OutputConfig(example=output_example, schema=output_schema),
    )


def _opt(price: str):
    return PaymentOption(scheme="exact", pay_to=PAY_TO, price=price, network=NETWORK)


# ---- request models (mirror the screener's payloads) ----
class EvaluateOpportunityIn(BaseModel):
    capital_required: float | None = None
    reward_usd: float | None = None
    probability: float | None = None
    risk: float | None = None
    automation_level: float | None = None
    status: str | None = None
    payment_proof: str | None = None
    verification: str | None = None


class EvaluateTermsIn(BaseModel):
    agentAccess: str | None = None
    description: str | None = None
    eligibility: list | None = None
    requirements: list | None = None
    flags: list | None = None


class ReadinessIn(BaseModel):
    url: str


# ---- rate limiting (in-memory token window per client IP) ----
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MIN:
            return True
        q.append(now)
        return False


app = FastAPI(title="OMEGA Opportunity Intelligence", version="1.0.0")

# ---- x402 payment gate ----
facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
resource_server = x402ResourceServer(facilitator)
resource_server.register(NETWORK, ExactEvmServerScheme())

routes = {
    "POST /v1/evaluate-opportunity": RouteConfig(
        accepts=_opt("$0.05"),
        mime_type="application/json",
        description="Fail-closed opportunity screen: vetoes, 0-100 score and expected value for one candidate task. Estimates only, never revenue.",
        service_name="OMEGA Opportunity Intel",
        tags=["screening", "agents", "risk"],
        extensions=_bazaar_body(
            {"capital_required": 0, "reward_usd": 500, "probability": 0.4, "risk": 0.3,
             "automation_level": 0.9, "status": "eligible_verified",
             "payment_proof": "ESCROW_VERIFIED", "verification": "OFFICIAL"},
            {"type": "object", "properties": {
                "capital_required": {"type": "number"}, "reward_usd": {"type": "number"},
                "probability": {"type": "number"}, "risk": {"type": "number"},
                "automation_level": {"type": "number"}, "status": {"type": "string"},
                "payment_proof": {"type": "string"}, "verification": {"type": "string"}}},
            {"verdict": "EXECUTE_AUTONOMOUSLY", "score": 87.2, "vetoes": [],
             "estimated_ev_usd": 200.0, "money_state": "ESTIMATED_ONLY_NOT_REVENUE",
             "rule": "Only verified payment proof can become REALIZED/SETTLED in Treasury."},
            {"properties": {
                "verdict": {"type": "string"},
                "score": {"type": "number"},
                "vetoes": {"type": "array", "items": {"type": "string"}},
                "estimated_ev_usd": {"type": "number"},
                "money_state": {"type": "string"},
                "rule": {"type": "string"}},
             "required": ["verdict", "score", "vetoes", "estimated_ev_usd",
                          "money_state", "rule"]},
        ),
    ),
    "POST /v1/evaluate-agent-terms": RouteConfig(
        accepts=_opt("$0.03"),
        mime_type="application/json",
        description="Agent-terms gate: decides whether a task's terms allow autonomous agent submission, with reasons and a terms fingerprint.",
        service_name="OMEGA Opportunity Intel",
        tags=["terms", "agents", "compliance"],
        extensions=_bazaar_body(
            {"agentAccess": "AGENT_ALLOWED", "description": "Research report", "eligibility": []},
            {"type": "object", "properties": {
                "agentAccess": {"type": "string"}, "description": {"type": "string"},
                "eligibility": {"type": "array"}, "requirements": {"type": "array"},
                "flags": {"type": "array"}}},
            {"gate": "REVIEW_DELIVERABLE_BUILD", "reasons": [],
             "terms_fingerprint": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15",
             "submission_allowed": True,
             "rule": "A clean gate still requires a built and QA-passed deliverable plus a fresh gate before submission."},
            {"properties": {
                "gate": {"type": "string"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "terms_fingerprint": {"type": "string"},
                "submission_allowed": {"type": "boolean"},
                "rule": {"type": "string"}},
             "required": ["gate", "reasons", "terms_fingerprint",
                          "submission_allowed", "rule"]},
        ),
    ),
    "POST /v1/x402/readiness": RouteConfig(
        accepts=_opt("$0.01"),
        mime_type="application/json",
        description="x402 readiness inspection of a public HTTPS endpoint: checks 402 shape, Base network, USDC asset, payTo and Bazaar metadata. Never sends payment.",
        service_name="OMEGA Opportunity Intel",
        tags=["x402", "audit", "base"],
        extensions=_bazaar_body(
            {"url": "https://example.com/paid-resource"},
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            {"url": "https://example.com/paid-resource", "http_status": 402,
             "verdict": "READY_FOR_PAID_TEST_REVIEW",
             "checks": [{"name": "unpaid_returns_402", "ok": True, "detail": "http_status=402"}],
             "payment_requirements": [
                 {"scheme": "exact", "network": "eip155:8453",
                  "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                  "amount_base_units": "10000",
                  "payTo": "0x0000000000000000000000000000000000000000"}],
             "bazaar_metadata_present": True,
             "rule": "Readiness is not a sale or payment. A genuine settled customer payment is required before revenue."},
            {"properties": {
                "url": {"type": "string"},
                "http_status": {"type": "number"},
                "verdict": {"type": "string"},
                "checks": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "detail": {"type": "string"}},
                    "required": ["name", "ok", "detail"]}},
                "payment_requirements": {"type": "array", "items": {"type": "object"}},
                "bazaar_metadata_present": {"type": "boolean"},
                "rule": {"type": "string"}},
             "required": ["url", "http_status", "verdict", "checks",
                          "payment_requirements", "bazaar_metadata_present", "rule"]},
        ),
    ),
    "GET /v1/money-flow/latest": RouteConfig(
        accepts=_opt("$0.02"),
        mime_type="application/json",
        description="Latest money-flow snapshot: stablecoin and chain TVL signals with sources. Market signals only, not demand proof or revenue.",
        service_name="OMEGA Opportunity Intel",
        tags=["money-flow", "stablecoins", "data"],
        extensions=_bazaar_query(
            {},
            {"type": "object", "properties": {}},
            {"status": "OK", "sources": [], "top_chains": [], "stablecoins": {},
             "global_market": {},
             "disclaimer": "Market and TVL signals only. Not demand proof, not an opportunity, and not revenue."},
            {"properties": {
                "status": {"type": "string"},
                "sources": {"type": ["array", "object", "null"]},
                "top_chains": {"type": "array"},
                "stablecoins": {"type": "object"},
                "global_market": {"type": ["object", "null"]},
                "disclaimer": {"type": "string"}},
             "required": ["status", "sources", "top_chains", "stablecoins", "disclaimer"]},
        ),
    ),
    "GET /v1/chain/block-number": RouteConfig(
        accepts=_opt("$0.003"),
        mime_type="application/json",
        description="Current block number for a supported EVM network (read-only chain call).",
        service_name="OMEGA Opportunity Intel",
        tags=["chain", "evm", "data"],
        extensions=_bazaar_query(
            {"network": "ethereum"},
            {"type": "object", "properties": {
                "network": {"type": "string", "enum": ["ethereum"]}}},
            {"network": "ethereum", "result": "0x1715f90", "block_number": 24207248},
            {"properties": {
                "network": {"type": "string"},
                "result": {"type": "string"},
                "block_number": {"type": "integer"}},
             "required": ["network", "result", "block_number"]},
        ),
    ),
    "GET /v1/chain/erc20-balance": RouteConfig(
        accepts=_opt("$0.003"),
        mime_type="application/json",
        description="ERC-20 balance, decimals, symbol and name for an address and token contract on a supported EVM network (read-only chain call).",
        service_name="OMEGA Opportunity Intel",
        tags=["chain", "erc20", "data"],
        extensions=_bazaar_query(
            {"network": "ethereum", "address": "0x0000000000000000000000000000000000000000",
             "contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"},
            {"type": "object", "properties": {
                "network": {"type": "string", "enum": ["ethereum"]},
                "address": {"type": "string", "pattern": _EVM_ADDRESS_PATTERN},
                "contract": {"type": "string", "pattern": _EVM_ADDRESS_PATTERN}},
             "required": ["address", "contract"]},
            {"network": "ethereum",
             "account": "0x0000000000000000000000000000000000000000",
             "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
             "balance": "0", "decimals": 6, "symbol": "USDC", "name": "USD Coin"},
            {"properties": {
                "network": {"type": "string"},
                "account": {"type": "string"},
                "contract_address": {"type": "string"},
                "balance": {"type": "string"},
                "decimals": {"type": "integer"},
                "symbol": {"type": "string"},
                "name": {"type": "string"}},
             "required": ["network", "account", "contract_address", "balance",
                          "decimals", "symbol", "name"]},
        ),
    ),
}

app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=resource_server)


# ---- abuse controls: kill switch + rate limit + body cap (run before payment gate) ----
@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/v1/") and path != "/health":
        if KILL_SWITCH:
            return JSONResponse(status_code=503, content={"error": "service_paused"})
        ip = request.client.host if request.client else "unknown"
        if _rate_limited(ip):
            return JSONResponse(status_code=429, content={"error": "rate_limited"})
        clen = request.headers.get("content-length")
        if clen and int(clen) > MAX_BODY_BYTES:
            return JSONResponse(status_code=413, content={"error": "body_too_large"})
    return await call_next(request)


# ---- handlers (delegate to the tested screener) ----
@app.get("/health")
async def health():
    return {"status": "OK", "service": "omega-opportunity-intelligence",
            "network": NETWORK, "payTo": PAY_TO, "kill_switch": KILL_SWITCH}


@app.post("/v1/evaluate-opportunity")
async def evaluate_opportunity(body: EvaluateOpportunityIn):
    return screener.evaluate_opportunity(body.model_dump(exclude_none=True))


@app.post("/v1/evaluate-agent-terms")
async def evaluate_agent_terms(body: EvaluateTermsIn):
    return screener.evaluate_agent_terms(body.model_dump(exclude_none=True))


@app.post("/v1/x402/readiness")
async def x402_readiness(body: ReadinessIn):
    try:
        return screener.x402_readiness(body.url)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/v1/money-flow/latest")
async def money_flow_latest():
    return screener.money_flow_latest()


@app.get("/v1/chain/block-number")
async def chain_block_number(network: str = "ethereum"):
    return screener.chain_block_number(network)


@app.get("/v1/chain/erc20-balance")
async def chain_erc20_balance(address: str, contract: str, network: str = "ethereum"):
    try:
        return screener.erc20_balance(address, contract, network)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8766")))
