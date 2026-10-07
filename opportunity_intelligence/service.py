#!/usr/bin/env python3
"""
OMEGA Opportunity Intelligence — owned digital service asset.

A dependency-free local HTTP service that packages OMEGA's fail-closed
opportunity evaluation into reusable endpoints. It is built and testable now,
but it is NOT publicly deployed or monetized until the user provisions a
hosting/payment identity. No wallet, private key, or paid call is required to
run it locally.
"""
import json
import ipaddress
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
OMEGA = BASE.parents[1]
sys.path.insert(0, str(OMEGA))
import agent_terms_gate as gate_mod  # noqa: E402

SNAPSHOT = OMEGA.parent.parent / "hidden_files" / "blackhole_money_flow_latest.json"
MAX_BODY = 64_000


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def evaluate_opportunity(payload):
    """Fail-closed opportunity decision. Estimates are never revenue."""
    capital = _num(payload.get("capital_required"))
    reward = _num(payload.get("reward_usd"))
    probability = _num(payload.get("probability"))
    risk = _num(payload.get("risk"), 0.65)
    automation = _num(payload.get("automation_level"))
    status = str(payload.get("status") or "")
    proof = str(payload.get("payment_proof") or "").upper()
    verification = str(payload.get("verification") or "").upper()
    vetoes = []
    if capital > 0:
        vetoes.append("CAPITAL_REQUIRED_ZERO_CAPITAL_MODE")
    if status.startswith(("blocked", "rejected", "watchlist")):
        vetoes.append(f"STATUS_{status.upper()}")
    if status.startswith("human") or "user_publish" in status or "manual" in status:
        vetoes.append("NOT_AUTONOMOUSLY_SUBMITTABLE")
    if "NONE" in proof or "NOT_VERIFIED" in proof or "PLATFORM_LISTING_ONLY" in proof:
        vetoes.append("PAYMENT_NOT_INDEPENDENTLY_VERIFIED")
    if verification in ("UNVERIFIED", "NONE", ""):
        vetoes.append("SOURCE_NOT_VERIFIED")
    if automation < 0.60:
        vetoes.append("AUTOMATION_LEVEL_TOO_LOW")
    ev = reward * probability
    score = round(max(0.0, min(100.0,
        probability * 18 + min(22.0, ev / 25.0) + (1 - risk) * 18 +
        automation * 15 + (28 if "ESCROW_VERIFIED" in proof else 8 if "OFFICIAL" in proof else 0))), 2)
    verdict = "EXECUTE_AUTONOMOUSLY" if not vetoes and score >= 65 else "REJECT_FOR_AUTONOMOUS_EXECUTION"
    return {
        "verdict": verdict,
        "score": score,
        "vetoes": vetoes,
        "estimated_ev_usd": round(ev, 2),
        "money_state": "ESTIMATED_ONLY_NOT_REVENUE",
        "rule": "Only verified payment proof can become REALIZED/SETTLED in Treasury.",
    }


def evaluate_agent_terms(payload):
    gate, reasons = gate_mod.evaluate_agent_details(payload)
    return {
        "gate": gate,
        "reasons": reasons,
        "terms_fingerprint": gate_mod.terms_fingerprint(payload),
        "submission_allowed": gate == "REVIEW_DELIVERABLE_BUILD",
        "rule": "A clean gate still requires a built and QA-passed deliverable plus a fresh gate before submission.",
    }


def money_flow_latest():
    if not SNAPSHOT.exists():
        return {"status": "UNAVAILABLE", "reason": "money-flow snapshot not built yet"}
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {
        "status": "OK",
        "sources": data.get("sources"),
        "top_chains": (data.get("top_chains") or [])[:10],
        "stablecoins": data.get("stablecoins"),
        "global_market": data.get("global_market"),
        "disclaimer": "Market and TVL signals only. Not demand proof, not an opportunity, and not revenue.",
    }

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_https_url(url):
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("https_public_url_required")
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except Exception:
        raise ValueError("host_resolution_failed")
    ips = set()
    benchmark_net = ipaddress.ip_network("198.18.0.0/15")
    allow_benchmark_dns = os.environ.get("OMEGA_X402_ALLOW_BENCHMARK_DNS") == "1"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        ips.add(ip)
        if ip in benchmark_net and allow_benchmark_dns:
            continue
        if not ip.is_global:
            raise ValueError("non_public_host_blocked")
    return parsed.geturl(), sorted(str(x) for x in ips)


def _fetch_public_https(url):
    safe_url, resolved_ips = _public_https_url(url)
    req = urllib.request.Request(safe_url, method="GET", headers={
        "User-Agent": "OMEGA-x402-Readiness/1.0", "Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=15) as resp:
            return {"status": resp.status, "headers": dict(resp.headers), "body": resp.read(65536), "resolved_ips": resolved_ips}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": dict(e.headers or {}), "body": e.read(65536), "resolved_ips": resolved_ips}


def _find_accepts(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("accepts"), list):
            return obj["accepts"], obj
        for v in obj.values():
            found, root = _find_accepts(v)
            if found is not None:
                return found, root
    if isinstance(obj, list):
        for v in obj:
            found, root = _find_accepts(v)
            if found is not None:
                return found, root
    return None, None


def x402_readiness(url, fetcher=None):
    """Inspect an unpaid public HTTPS endpoint for x402 readiness.

    This never sends payment, signs anything, or follows redirects. SSRF-prone
    non-public hosts are blocked before fetching.
    """
    fetcher = fetcher or _fetch_public_https
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("https_public_url_required")
    fetched = fetcher(url)
    status = fetched.get("status")
    body = fetched.get("body") or b""
    if isinstance(body, str):
        body = body.encode()
    payload = None
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        payload = None
    accepts, root = _find_accepts(payload) if payload is not None else (None, None)
    headers = {str(k).lower(): str(v) for k, v in (fetched.get("headers") or {}).items()}
    checks = []
    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    check("reachable", status is not None and status < 500, f"http_status={status}")
    check("unpaid_returns_402", status == 402, f"http_status={status}")
    check("payment_requirements_present", bool(accepts), "accepts list found" if accepts else "no accepts list found")
    check("payment_header_or_body", bool(accepts) or "payment-required" in headers, "payment requirements in JSON body or PAYMENT-REQUIRED header")
    summaries = []
    supported_network = False
    supported_asset = False
    payto_valid = False
    for a in accepts or []:
        network = a.get("network")
        asset = a.get("asset") or a.get("currency")
        payto = a.get("payTo") or a.get("recipient")
        if network in ("eip155:8453", "base"):
            supported_network = True
        if str(asset).lower() == BASE_USDC.lower():
            supported_asset = True
        if payto and ADDRESS_RE.match(str(payto)):
            payto_valid = True
        summaries.append({"scheme": a.get("scheme"), "network": network, "asset": asset,
                          "amount_base_units": a.get("amount") or a.get("maxAmountRequired"), "payTo": payto})
    check("base_network_supported", supported_network, "expects eip155:8453/Base")
    check("base_usdc_supported", supported_asset, f"expects {BASE_USDC}")
    check("payto_address_valid", payto_valid, "valid EVM payTo required")
    has_bazaar = bool(root and isinstance(root.get("extensions"), dict) and "bazaar" in root["extensions"])
    check("bazaar_metadata_present", has_bazaar, "extensions.bazaar improves discovery")
    core_ok = all(c["ok"] for c in checks if c["name"] not in ("bazaar_metadata_present",))
    verdict = "READY_FOR_PAID_TEST_REVIEW" if core_ok else ("X402_NEEDS_FIX" if status == 402 or accepts else "NOT_X402_OR_UNREACHABLE")
    return {"url": url, "http_status": status, "verdict": verdict, "checks": checks,
            "payment_requirements": summaries,
            "bazaar_metadata_present": has_bazaar,
            "rule": "Readiness is not a sale or payment. A genuine settled customer payment is required before revenue."}


RPC_ENDPOINTS = {
    "ethereum": "https://ethereum-rpc.publicnode.com",
}
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _rpc(network, method, params):
    endpoint = RPC_ENDPOINTS.get(network or "ethereum")
    if not endpoint:
        raise ValueError("unsupported_network")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json", "User-Agent": "OMEGA-Opportunity-Intelligence/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RuntimeError("rpc_error")
    return data.get("result")


def _address(value):
    value = str(value or "")
    if not ADDRESS_RE.match(value):
        raise ValueError("invalid_evm_address")
    return value


def _decode_uint(value):
    return int(value or "0x0", 16)


def _decode_string(value):
    raw = bytes.fromhex((value or "0x")[2:])
    if len(raw) >= 64:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64:64 + length].decode("utf-8", "replace")
    return raw.rstrip(b"\x00").decode("utf-8", "replace")


def _eth_call(network, to, data):
    return _rpc(network, "eth_call", [{"to": to, "data": data}, "latest"])


def chain_block_number(network="ethereum"):
    result = _rpc(network, "eth_blockNumber", [])
    return {"network": network, "result": result, "block_number": _decode_uint(result)}


def erc20_balance(address, contract, network="ethereum"):
    address = _address(address)
    contract = _address(contract)
    padded = address[2:].rjust(64, "0")
    balance = _decode_uint(_eth_call(network, contract, "0x70a08231" + padded))
    decimals = _decode_uint(_eth_call(network, contract, "0x313ce567"))
    symbol = _decode_string(_eth_call(network, contract, "0x95d89b41"))
    name = _decode_string(_eth_call(network, contract, "0x06fdde03"))
    return {"network": network, "account": address, "contract_address": contract,
            "balance": str(balance), "decimals": decimals, "symbol": symbol, "name": name}


def live_balance(address, tokens=None, network="ethereum"):
    address = _address(address)
    native = _rpc(network, "eth_getBalance", [address, "latest"])
    token_rows = []
    for contract in list(tokens or [])[:20]:
        token_rows.append(erc20_balance(address, contract, network))
    return {"network": network, "address": address, "eth_balance": native,
            "eth_balance_wei": str(_decode_uint(native)), "tokens": token_rows}


ROUTES = {
    ("GET", "/health"): lambda payload: {"status": "OK", "service": "omega-opportunity-intelligence", "monetization": "UNAVAILABLE_NOT_LAUNCHED"},
    ("POST", "/v1/evaluate-opportunity"): evaluate_opportunity,
    ("POST", "/v1/evaluate-agent-terms"): evaluate_agent_terms,
    ("POST", "/v1/x402/readiness"): lambda payload: x402_readiness(payload.get("url")),
    ("GET", "/v1/money-flow/latest"): lambda payload: money_flow_latest(),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep local service quiet
        return
    def _send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if path == "/v1/chain/block-number":
                return self._send(200, chain_block_number(query.get("network", "ethereum")))
            if path == "/v1/chain/erc20-balance":
                return self._send(200, erc20_balance(query.get("address"), query.get("contract"), query.get("network", "ethereum")))
            if path == "/v1/chain/live-balance":
                tokens = [x for x in (query.get("tokens") or "").split(",") if x]
                return self._send(200, live_balance(query.get("address"), tokens, query.get("network", "ethereum")))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(502, {"error": type(e).__name__})
        fn = ROUTES.get(("GET", path))
        if not fn:
            return self._send(404, {"error": "not_found"})
        try:
            return self._send(200, fn({}))
        except Exception as e:
            return self._send(500, {"error": type(e).__name__})
    def do_POST(self):
        fn = ROUTES.get(("POST", self.path))
        if not fn:
            return self._send(404, {"error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "body_too_large"})
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                return self._send(400, {"error": "json_object_required"})
            return self._send(200, fn(payload))
        except Exception as e:
            return self._send(400, {"error": type(e).__name__})


def main():
    import argparse
    p = argparse.ArgumentParser(prog="opportunity_intelligence")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"OMEGA Opportunity Intelligence listening on http://{a.host}:{a.port}")
    print("Monetization: UNAVAILABLE_NOT_LAUNCHED (no payment identity configured)")
    server.serve_forever()


if __name__ == "__main__":
    main()
