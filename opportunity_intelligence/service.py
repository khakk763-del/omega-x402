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

import concurrent.futures  # noqa: E402 - module-level (digest fetchers)
import threading  # noqa: E402 - module-level (fetch cache lock)
import time as _time  # noqa: E402 - module-level (fetch cache)

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
    # mainnet.base.org 403s/rate-limits unauthenticated eth_call batches
    # from some egress IPs; publicnode's Base endpoint answers cleanly.
    "base": "https://base-rpc.publicnode.com",
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


# =====================================================================
# Bazaar utility SKUs (2026-10-08): cheap high-demand data calls for the
# x402 sell-side. Sources are all public + free + keyless (DexScreener,
# Open-Meteo + geocoding, DefiLlama yields, Hacker News Firebase DB).
# Every fetch is hard-timeouted (<=10s), cached briefly, and never
# recurses into HTML scraping. Read-only data signals — never revenue.
# =====================================================================
DS_BASE = "https://api.dexscreener.com"
METEO_BASE = "https://api.open-meteo.com/v1/forecast"
METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
LLAMA_YIELDS = "https://yields.llama.fi/pools"
HN_BASE = "https://hacker-news.firebaseio.com/v0"

_bazaar_cache: dict[str, tuple[float, object]] = {}
_bazaar_cache_lock = threading.Lock()


def _fetch_json_http(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": "OMEGA-x402-Bazaar/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(20_000_000).decode("utf-8", "replace"))


def _cached_fetch(key: str, ttl: float, producer):
    now = _time.time()
    with _bazaar_cache_lock:
        hit = _bazaar_cache.get(key)
    if hit and now - hit[0] <= ttl:
        return hit[1]
    val = producer()
    with _bazaar_cache_lock:
        _bazaar_cache[key] = (now, val)
    if len(_bazaar_cache) > 200:  # crude cap; entries are tiny
        with _bazaar_cache_lock:
            _bazaar_cache.clear()
    return val


def _ds_pair_row(p: dict) -> dict:
    tx = p.get("txns") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "pair_address": p.get("pairAddress"),
        "chain": p.get("chainId"),
        "dex": p.get("dexId"),
        "base_token": (p.get("baseToken") or {}).get("symbol"),
        "base_token_address": (p.get("baseToken") or {}).get("address"),
        "quote_token": (p.get("quoteToken") or {}).get("symbol"),
        "price_usd": _num(p.get("priceUsd")),
        "price_native": _num(p.get("priceNative")),
        "liquidity_usd": _num((p.get("liquidity") or {}).get("usd")),
        "fdv_usd": _num(p.get("fdv")),
        "market_cap_usd": _num(p.get("marketCap")),
        "volume_h24": _num(vol.get("h24")),
        "volume_h1": _num(vol.get("h1")),
        "txns_h24": {"buys": (tx.get("h24") or {}).get("buys"),
                     "sells": (tx.get("h24") or {}).get("sells")},
        "price_change_h1_pct": _num(chg.get("h1")),
        "price_change_h6_pct": _num(chg.get("h6")),
        "price_change_h24_pct": _num(chg.get("h24")),
        "pair_created_at": p.get("pairCreatedAt"),
    }


def token_price(addresses: str):
    """Token price + market metrics for up to 3 comma-separated token contract
    addresses (any chain DexScreener covers), best-liquidity pair each.
    Source: DexScreener public API."""
    addrs = [a.strip() for a in str(addresses or "").split(",") if a.strip()][:3]
    if not addrs:
        raise ValueError("addresses_required")
    for a in addrs:
        if not ADDRESS_RE.match(a):
            raise ValueError("invalid_evm_address")
    by_addr: dict[str, list] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_fetch_json_http,
                          f"{DS_BASE}/token-pairs/v1/base/{a}"): a for a in addrs}
        for f in concurrent.futures.as_completed(futs):
            try:
                ps = f.result()
            except Exception:
                ps = None
            by_addr[futs[f]] = ps or []
    pairs = [p for a in addrs for p in by_addr.get(a, [])]
    best: dict[str, dict] = {}
    order: list[str] = []
    for p in pairs:
        base = ((p.get("baseToken") or {}).get("address") or "").lower()
        if base not in best:
            order.append(base)
        cur = best.get(base)
        if cur is None or (p.get("liquidity") or {}).get("usd", 0) > \
                (cur.get("liquidity") or {}).get("usd", 0):
            best[base] = p
    tokens = []
    for base in order:
        row = _ds_pair_row(best[base])
        if row.get("base_token_address"):
            tokens.append(row)
    return {"tokens": tokens, "count": len(tokens), "source": "dexscreener",
            "as_of": int(_time.time())}


def trending_tokens(chain: str = "base", limit: int = 10):
    """Trending/boosted tokens (DexScreener token boosts), optionally filtered
    to one chain ('base' default; 'any' for all chains)."""
    limit = max(1, min(int(limit or 10), 25))
    chain = (chain or "base").strip().lower()

    def _produce():
        boosts = _fetch_json_http(f"{DS_BASE}/token-boosts/top/v1") or []
        addrs: list[str] = []
        for b in boosts:
            if chain == "any" or (b.get("chainId") or "").lower() == chain:
                addrs.append(b.get("tokenAddress"))
            if len(addrs) >= limit:
                break
        boosts = [b for b in boosts if b.get("tokenAddress") in set(addrs)]
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futs = {}
            for b in boosts:
                a, bc = b.get("tokenAddress"), (b.get("chainId") or "base")
                if a:
                    futs[ex.submit(
                        _fetch_json_http,
                        f"{DS_BASE}/token-pairs/v1/{bc.lower()}/{a}")] = a
            for f in concurrent.futures.as_completed(futs):
                try:
                    pairs = f.result()
                except Exception:
                    pairs = None
                if pairs:
                    best_pair = max(pairs,
                                    key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
                    if best_pair.get("pairAddress"):
                        rows.append(_ds_pair_row(best_pair))
        rows.sort(key=lambda r: r.get("volume_h24") or 0, reverse=True)
        return {"trending": rows, "count": len(rows), "chain": chain,
                "source": "dexscreener-boosts", "as_of": int(_time.time())}
    return _cached_fetch(f"trend:{chain}:{limit}", 60, _produce)


WMO_CODES = {0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
             45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle",
             53: "Moderate drizzle", 55: "Dense drizzle", 56: "Light freezing drizzle",
             57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain",
             65: "Heavy rain", 66: "Light freezing rain", 67: "Heavy freezing rain",
             71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
             77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
             82: "Violent rain showers", 85: "Slight snow showers",
             86: "Heavy snow showers", 95: "Thunderstorm",
             96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"}


def weather_now(city: str):
    """Current weather + 3-day outlook for a city name (Open-Meteo, free)."""
    city = (city or "").strip()
    if not city:
        raise ValueError("city_required")
    qs = urllib.parse.urlencode({"name": city, "count": 1,
                                 "language": "en", "format": "json"})
    geo = _cached_fetch(f"geo:{city.lower()}", 3600,
                        lambda: _fetch_json_http(f"{METEO_GEO}?{qs}"))
    results = (geo or {}).get("results") or []
    if not results:
        raise ValueError("city_not_found")
    place = results[0]
    lat, lon = place["latitude"], place["longitude"]
    qs2 = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "weather_code,wind_speed_10m,wind_direction_10m,pressure_msl",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                 "precipitation_probability_max",
        "timezone": "auto", "forecast_days": 4})
    wx = _cached_fetch(f"wx:{round(lat,3)}:{round(lon,3)}", 300,
                       lambda: _fetch_json_http(f"{METEO_BASE}?{qs2}"))
    cur = wx.get("current") or {}
    daily = wx.get("daily") or {}
    days = []
    for i, d in enumerate(daily.get("time") or []):
        if i == 0:
            continue  # forecast starts tomorrow; current covers today
        days.append({
            "date": d,
            "max_c": (daily.get("temperature_2m_max") or [None])[i],
            "min_c": (daily.get("temperature_2m_min") or [None])[i],
            "condition": WMO_CODES.get((daily.get("weather_code") or [None])[i],
                                       "Unknown"),
            "precip_probability_max_pct":
                (daily.get("precipitation_probability_max") or [None])[i],
        })
        if len(days) >= 3:
            break

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "city": place.get("name"),
        "country": place.get("country"),
        "latitude": lat, "longitude": lon,
        "timezone": wx.get("timezone"),
        "current": {
            "time": cur.get("time"),
            "temperature_c": _num(cur.get("temperature_2m")),
            "apparent_temperature_c": _num(cur.get("apparent_temperature")),
            "humidity_pct": _num(cur.get("relative_humidity_2m")),
            "wind_speed_kmh": _num(cur.get("wind_speed_10m")),
            "wind_direction_deg": _num(cur.get("wind_direction_10m")),
            "pressure_hpa": _num(cur.get("pressure_msl")),
            "condition": WMO_CODES.get(cur.get("weather_code"), "Unknown"),
            "weather_code": cur.get("weather_code"),
        },
        "forecast_days": days,
        "source": "open-meteo",
    }


def defi_yields(min_tvl: float = 1_000_000, limit: int = 10, chain: str = ""):
    """Top DeFi yield pools by APY (DefiLlama yields, public). Filters:
    min TVL (default $1M), optional single chain. Signals only."""

    def _produce():
        data = _fetch_json_http(LLAMA_YIELDS)
        pools = data.get("data") if isinstance(data, dict) else data
        pools = pools or []
        rows = []
        for p in pools:
            tvl = p.get("tvlUsd") or 0
            apy = p.get("apy")
            if tvl < float(min_tvl or 0) or apy is None or apy <= 0 or apy > 150:
                continue
            if p.get("outlier"):
                continue
            if chain and (p.get("chain") or "").lower() != chain.lower():
                continue
            rows.append({
                "project": p.get("project"),
                "chain": p.get("chain"),
                "symbol": p.get("symbol"),
                "pool_id": p.get("pool"),
                "apy_total_pct": round(float(apy), 4),
                "apy_base_pct": (round(float(p["apyBase"]), 4)
                                 if p.get("apyBase") is not None else None),
                "apy_reward_pct": (round(float(p["apyReward"]), 4)
                                   if p.get("apyReward") is not None else None),
                "tvl_usd": float(tvl),
                "stablecoin": bool(p.get("stablecoin")),
                "il_risk": p.get("ilRisk"),
                "exposure": p.get("exposure"),
                "apy_mean_30d_pct": p.get("apyMean30d"),
            })
        rows.sort(key=lambda r: (r["apy_total_pct"], r["tvl_usd"]), reverse=True)
        rows = rows[: max(1, min(int(limit or 10), 25))]
        return rows

    pools = _cached_fetch(
        f"yields:{min_tvl}:{limit}:{chain.lower()}", 300, _produce)
    return {"pools": pools, "count": len(pools),
            "filters": {"min_tvl_usd": float(min_tvl or 0),
                        "chain": chain.lower() or None},
            "source": "defillama-yields", "as_of": int(_time.time()),
            "disclaimer": "Yield snapshots are market signals only, not "
                          "financial advice and not revenue."}


def news_digest(topic: str = "crypto", limit: int = 10):
    """Headline digest. topic=crypto -> DexScreener profiled-token snapshots
    (project, headline, website); topic=tech -> Hacker News top stories."""
    limit = max(1, min(int(limit or 10), 10))
    topic = (topic or "crypto").strip().lower()
    if topic == "tech":
        def _produce():
            ids = _fetch_json_http(f"{HN_BASE}/topstories.json") or []
            stories = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                futs = [ex.submit(_fetch_json_http, f"{HN_BASE}/item/{i}.json")
                        for i in ids[:limit]]
                for f in concurrent.futures.as_completed(futs):
                    try:
                        it = f.result()
                    except Exception:
                        continue
                    if it and it.get("title"):
                        stories.append({
                            "title": it.get("title"),
                            "url": it.get("url") or
                                   f"https://news.ycombinator.com/item?id={it.get('id')}",
                            "source": "hacker-news",
                            "score": it.get("score"),
                            "author": it.get("by"),
                            "published_at": it.get("time"),
                        })
            return {"items": stories[:limit], "count": len(stories[:limit]),
                    "topic": "tech", "source": "hacker-news"}
        return _cached_fetch(f"news:tech:{limit}", 300, _produce)

    def _crypto():
        profiles = _fetch_json_http(f"{DS_BASE}/token-profiles/latest/v1") or []
        items = []
        for pr in profiles[:limit]:
            site = None
            for link in pr.get("links") or []:
                if link.get("type") == "website" or link.get("label") == "Website":
                    site = link.get("url")
                    break
            if not site and pr.get("links"):
                site = pr["links"][0].get("url")
            items.append({
                "project": pr.get("name") or pr.get("description"),
                "chain": pr.get("chainId"),
                "token_address": pr.get("tokenAddress"),
                "headline": (pr.get("description") or "")[:280],
                "website": site or pr.get("url"),
                "source": "dexscreener-profiles",
            })
        return {"items": items, "count": len(items), "topic": "crypto",
                "source": "dexscreener-profiles",
                "note": "Project snapshots from DexScreener token profiles "
                        "(editorial-grade data, not scraped headlines)."}
    return _cached_fetch(f"news:crypto:{limit}", 300, _crypto)


# =====================================================================
# DD SKUs (2026-10-09): verdict-grade due-diligence snapshots an agent
# buyer pays for because one call replaces several integrations.
# Upstreams (all free + keyless, hard-timeouted, cached briefly):
#   GoPlus token_security (api.gopluslabs.io, no key) — honeypot/tax/
#   proxy/open-source/owner/holder fields; Honeypot.is v2 simulation;
#   Blockscout v2 (base/ethereum) — source verification + proxy/impl;
#   ENSIdeas — reverse ENS; DefiLlama coins — price cross-check.
# Every function returns live data or raises -> the app maps that to an
# honest 502. Flags are facts about the contract/market, never buy/sell
# advice and never a safety guarantee.
# =====================================================================
GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"
HONEYPOT_BASE = "https://api.honeypot.is/v2"
BLOCKSCOUT_BASES = {
    "base": "https://base.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
}
ENSIDEAS_BASE = "https://api.ensideas.com/ens/resolve"
LLAMA_COINS = "https://coins.llama.fi/prices/current"
DD_CHAIN_IDS = {"base": "8453", "ethereum": "1"}


def _dd_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _goplus_token_security(chain: str, address: str):
    chain_id = DD_CHAIN_IDS[chain]
    url = (f"{GOPLUS_BASE}/token_security/{chain_id}"
           f"?contract_addresses={address}")
    data = _fetch_json_http(url, timeout=8.0)
    if (data or {}).get("code") != 1 or not (data or {}).get("result"):
        raise RuntimeError("goplus_no_token_security_result")
    row = (data["result"].get(address.lower())
           or next(iter(data["result"].values())))
    if not row:
        raise RuntimeError("goplus_no_token_security_result")
    return row


def _honeypot_sim(address: str, chain: str):
    url = f"{HONEYPOT_BASE}/IsHoneypot?address={address}&chainID={DD_CHAIN_IDS[chain]}"
    data = _fetch_json_http(url, timeout=8.0)
    if not isinstance(data, dict) or "honeypotResult" not in data:
        raise RuntimeError("honeypot_no_result")
    return data


def _blockscout(chain: str, path: str):
    url = f"{BLOCKSCOUT_BASES[chain]}{path}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OMEGA-x402-DD/1.0",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read(4_000_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _owner_control(sec: dict) -> str:
    """Classify owner control from GoPlus fields (factual, not a verdict)."""
    owner = str(sec.get("owner_address") or "").lower()
    zero = ("", "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead")
    if owner in zero:
        return "renounced_or_no_owner"
    return "owner_retained"


def score_token_risk(sec: dict, market: dict | None,
                     pair_age_days: float | None) -> tuple[int, list]:
    """Deterministic risk score 0 (lowest observed risk) .. 100 (highest)
    plus factual flags. Transparent heuristic over GoPlus security fields
    and DexScreener market metrics. A low score is NOT a safety guarantee
    and NOT investment advice; it summarises observable on-chain flags."""
    score = 0
    flags: list[dict] = []

    def add(points, code, detail):
        nonlocal score
        score += points
        flags.append({"flag": code, "detail": detail, "severity_points": points})

    if str(sec.get("is_honeypot")) == "1":
        add(60, "honeypot_flag", "GoPlus flags this token as a honeypot")
    buy_tax = _dd_num(sec.get("buy_tax"))
    sell_tax = _dd_num(sec.get("sell_tax"))
    if buy_tax is None or sell_tax is None:
        add(5, "tax_unknown", "buy/sell tax not reported by GoPlus")
    else:
        hi = max(buy_tax or 0, sell_tax or 0)
        if hi >= 0.25:
            add(30, "extreme_tax", f"max(buy,sell) tax {hi:.0%} >= 25%")
        elif hi >= 0.10:
            add(18, "high_tax", f"max(buy,sell) tax {hi:.0%} >= 10%")
        elif hi >= 0.05:
            add(8, "moderate_tax", f"max(buy,sell) tax {hi:.0%} >= 5%")
    if str(sec.get("is_open_source")) != "1":
        add(15, "source_not_open",
            "contract source not verified/open per GoPlus")
    if str(sec.get("cannot_buy")) == "1":
        add(20, "buying_disabled", "GoPlus reports buys are disabled")
    if str(sec.get("honeypot_with_same_creator")) == "1":
        add(12, "creator_honeypot_history",
            "creator previously deployed a honeypot token (GoPlus)")
    if _owner_control(sec) == "owner_retained":
        add(5, "owner_retained",
            "a non-zero owner address retains control")
    holders = sec.get("holders") or []
    top10 = 0.0
    for h in holders[:10]:
        p = _dd_num(h.get("percent"))
        if p is not None:
            top10 += p
    if holders and top10 >= 0.90:
        add(20, "extreme_holder_concentration",
            f"top-10 GoPlus holders hold {top10:.0%} of supply")
    elif holders and top10 >= 0.70:
        add(12, "high_holder_concentration",
            f"top-10 GoPlus holders hold {top10:.0%} of supply")
    elif holders and top10 >= 0.50:
        add(6, "moderate_holder_concentration",
            f"top-10 GoPlus holders hold {top10:.0%} of supply")
    if str(sec.get("is_proxy")) == "1":
        add(4, "proxy_upgradeable",
            "proxy pattern: logic can be upgraded by its admin")
    if market is None:
        add(8, "no_dex_market", "no liquid DexScreener pair found")
    else:
        liq = market.get("liquidity_usd") or 0
        if liq < 10_000:
            add(18, "very_low_liquidity", f"best-pair liquidity ${liq:,.0f}")
        elif liq < 100_000:
            add(10, "low_liquidity", f"best-pair liquidity ${liq:,.0f}")
        elif liq < 1_000_000:
            add(4, "moderate_liquidity", f"best-pair liquidity ${liq:,.0f}")
        if pair_age_days is not None and pair_age_days < 7:
            add(8, "very_new_pair", f"best pair is {pair_age_days:.1f} days old")
    return max(0, min(100, score)), flags


def _market_row_for(chain: str, address: str):
    """Best-liquidity DexScreener pair row for one token (or None)."""
    pairs = _fetch_json_http(
        f"{DS_BASE}/token-pairs/v1/{chain}/{address}", timeout=8.0) or []
    mine = [p for p in pairs
            if ((p.get("baseToken") or {}).get("address") or "").lower()
            == address.lower()]
    if not mine:
        return None
    best = max(mine, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    return _ds_pair_row(best)


def _pair_age_days(row):
    ts = (row or {}).get("pair_created_at")
    if not ts:
        return None
    return max(0.0, (_time.time() * 1000 - float(ts)) / 86_400_000)


def token_due_diligence(address: str, chain: str = "base"):
    """N1 — wallet/token due-diligence snapshot: GoPlus security fields +
    DexScreener market metrics + DefiLlama price cross-check, composed
    into one scored verdict."""
    address = _address(address)
    chain = (chain or "base").strip().lower()
    if chain not in DD_CHAIN_IDS:
        raise ValueError("unsupported_chain")

    def _produce():
        sec = _goplus_token_security(chain, address)
        market = _market_row_for(chain, address)
        llama = _fetch_json_http(
            f"{LLAMA_COINS}/{chain}:{address}", timeout=8.0)
        coin = ((llama or {}).get("coins") or {}).get(f"{chain}:{address}") or {}
        holders = sec.get("holders") or []
        top10_share = 0.0
        for h in holders[:10]:
            p = _dd_num(h.get("percent"))
            if p is not None:
                top10_share += p
        age = _pair_age_days(market)
        score, flags = score_token_risk(sec, market, age)
        return {
            "address": address, "chain": chain,
            "token": {"name": sec.get("token_name"),
                      "symbol": sec.get("token_symbol"),
                      "total_supply": sec.get("total_supply"),
                      "holder_count": sec.get("holder_count")},
            "risk_score": score,
            "risk_level": ("low" if score < 25 else
                           "moderate" if score < 50 else
                           "high" if score < 75 else "extreme"),
            "risk_flags": flags,
            "security": {
                "is_honeypot": str(sec.get("is_honeypot")) == "1",
                "buy_tax": _dd_num(sec.get("buy_tax")),
                "sell_tax": _dd_num(sec.get("sell_tax")),
                "cannot_buy": str(sec.get("cannot_buy")) == "1",
                "is_open_source": str(sec.get("is_open_source")) == "1",
                "is_proxy": str(sec.get("is_proxy")) == "1",
                "owner_address": sec.get("owner_address"),
                "owner_control": _owner_control(sec),
                "creator_address": sec.get("creator_address"),
                "creator_percent": _dd_num(sec.get("creator_percent")),
                "honeypot_with_same_creator":
                    str(sec.get("honeypot_with_same_creator")) == "1",
                "in_trust_list": str(sec.get("trust_list")) == "1",
                "in_cex": str(sec.get("is_in_cex")) == "1",
            },
            "holders": {
                "top10_share_pct": round(top10_share * 100, 4)
                if holders else None,
                "top_holders": [{
                    "address": h.get("address"),
                    "share_pct": round((_dd_num(h.get("percent")) or 0) * 100, 4),
                    "is_contract": str(h.get("is_contract")) == "1",
                    "is_locked": str(h.get("is_locked")) == "1",
                    "tag": h.get("tag") or None,
                } for h in holders[:10]],
            },
            "market": market,
            "pair_age_days": (round(age, 2) if age is not None else None),
            "llama_price_usd": coin.get("price"),
            "sources": ["goplus-token-security", "dexscreener",
                        "defillama-coins"],
            "as_of": int(_time.time()),
            "disclaimer": "Observable on-chain flags and market metrics "
                          "only; a low score is not a safety guarantee and "
                          "this is not investment advice.",
        }
    return _cached_fetch(f"ddtoken:{chain}:{address.lower()}", 120, _produce)


def contract_risk(address: str, chain: str = "base"):
    """N2 — contract risk flags: GoPlus security + Honeypot.is live
    simulation + Blockscout source/proxy verification, normalised into a
    plain-language flag list."""
    address = _address(address)
    chain = (chain or "base").strip().lower()
    if chain not in DD_CHAIN_IDS:
        raise ValueError("unsupported_chain")

    def _produce():
        sec = _goplus_token_security(chain, address)
        errors: list[str] = []
        sim = None
        try:
            sim = _honeypot_sim(address, chain)
        except Exception:
            errors.append("honeypot_simulation_unavailable")
        contract = None
        try:
            contract = _blockscout(
                chain, f"/api/v2/smart-contracts/{address}")
        except Exception:
            errors.append("blockscout_contract_lookup_unavailable")

        implementations = []
        is_proxy = str(sec.get("is_proxy")) == "1"
        if isinstance(contract, dict):
            for impl in contract.get("implementations") or []:
                implementations.append({
                    "name": impl.get("name"),
                    "address": impl.get("address_hash")})
            if contract.get("is_proxy"):
                is_proxy = True
        sim_result = (sim or {}).get("simulationResult") or {}
        sim_ok = bool((sim or {}).get("simulationSuccess"))
        is_honeypot = None
        if sim is not None:
            is_honeypot = bool(((sim.get("honeypotResult") or {})
                                .get("isHoneypot")))

        flags: list[dict] = []

        def add(level, code, detail):
            flags.append({"level": level, "flag": code, "detail": detail})

        if isinstance(contract, dict):
            verified = bool(contract.get("is_verified"))
            add("info" if verified else "warn", "source_verification",
                "source code verified on Blockscout" if verified
                else "source code NOT verified on Blockscout")
        elif str(sec.get("is_open_source")) == "1":
            add("info", "source_verification",
                "source open per GoPlus (Blockscout lookup unavailable)")
        if is_proxy:
            add("warn", "proxy_upgradeable",
                "proxy pattern"
                + (f" with implementation(s): "
                   f"{', '.join(i['name'] or i['address'] or '' for i in implementations)}"
                   if implementations else "")
                + " — logic can be upgraded by its admin")
        if _owner_control(sec) == "owner_retained":
            add("warn", "owner_retained",
                f"owner {sec.get('owner_address')} retains control")
        else:
            add("info", "ownership_renounced",
                "no live owner address (renounced or none)")
        if is_honeypot:
            add("critical", "honeypot",
                "Honeypot.is simulation: token is a honeypot")
        if sim is not None and sim_ok and not is_honeypot:
            add("info", "honeypot_simulation_passed",
                "Honeypot.is buy/sell simulation succeeded, not a honeypot")
        for tax_field, label in (("buy_tax", "buy"), ("sell_tax", "sell")):
            t = _dd_num(sec.get(tax_field))
            if t is not None and t >= 0.10:
                add("warn", f"{label}_tax_high",
                    f"{label} tax {t:.0%} >= 10%")
        if str(sec.get("cannot_buy")) == "1":
            add("critical", "buying_disabled",
                "GoPlus reports buys are disabled")
        hp_flags = (sim or {}).get("flags") or []
        for f in hp_flags:
            if isinstance(f, dict) and f.get("description"):
                add("warn", "honeypot_is_flag", str(f["description"]))

        blockscout_summary = None
        if isinstance(contract, dict):
            blockscout_summary = {
                "is_verified": bool(contract.get("is_verified")),
                "name": contract.get("name"),
                "compiler_version": contract.get("compiler_version"),
                "is_proxy": bool(contract.get("is_proxy")),
                "implementations": implementations,
            }
        return {
            "address": address, "chain": chain,
            "verified_source": (bool(contract.get("is_verified"))
                                if isinstance(contract, dict) else None),
            "is_proxy": is_proxy,
            "implementations": implementations,
            "owner_control": _owner_control(sec),
            "owner_address": sec.get("owner_address"),
            "honeypot": {
                "is_honeypot": is_honeypot,
                "simulation_success": sim_ok if sim is not None else None,
                "buy_tax": sim_result.get("buyTax"),
                "sell_tax": sim_result.get("sellTax"),
                "transfer_tax": sim_result.get("transferTax"),
                "flags": [f.get("description") for f in hp_flags
                          if isinstance(f, dict)],
            },
            "goplus": {
                "is_honeypot": str(sec.get("is_honeypot")) == "1",
                "buy_tax": _dd_num(sec.get("buy_tax")),
                "sell_tax": _dd_num(sec.get("sell_tax")),
                "is_open_source": str(sec.get("is_open_source")) == "1",
                "is_proxy": str(sec.get("is_proxy")) == "1",
                "cannot_buy": str(sec.get("cannot_buy")) == "1",
            },
            "blockscout": blockscout_summary,
            "risk_flags": flags,
            "sources": ["goplus-token-security", "honeypot-is",
                        "blockscout"],
            "source_errors": errors,
            "as_of": int(_time.time()),
            "disclaimer": "Observable contract flags from public security "
                          "APIs; absence of flags is not a safety guarantee "
                          "and this is not investment advice.",
        }
    return _cached_fetch(f"ddcontract:{chain}:{address.lower()}", 120, _produce)


def address_intel(address: str):
    """N4 — ENS / address intelligence: ENS reverse resolution,
    Blockscout account facts (contract-vs-EOA, creation, tx count),
    GoPlus address-risk signals, and the address's Base-USDC balance
    line via the existing erc20_balance fetcher."""
    address = _address(address)

    def _produce():
        errors: list[str] = []
        ens = None
        try:
            ens = _fetch_json_http(f"{ENSIDEAS_BASE}/{address}", timeout=8.0)
        except Exception:
            errors.append("ens_resolution_unavailable")
        acct = None
        for bs_chain in ("ethereum", "base"):
            try:
                acct = _blockscout(bs_chain, f"/api/v2/addresses/{address}")
                if isinstance(acct, dict):
                    break
            except Exception:
                acct = None
        if not isinstance(acct, dict):
            errors.append("blockscout_address_lookup_unavailable")
        goplus = None
        try:
            data = _fetch_json_http(
                f"{GOPLUS_BASE}/address_security/{address}?chain_id=8453",
                timeout=8.0)
            goplus = (data or {}).get("result")
        except Exception:
            errors.append("goplus_address_security_unavailable")
        # Keyless public RPCs occasionally rate-limit a burst of
        # eth_calls; one short retry keeps the balance line reliable.
        usdc = None
        for _attempt in range(2):
            try:
                usdc = erc20_balance(address, BASE_USDC, "base")
                break
            except Exception:
                usdc = None
                _time.sleep(1.0)
        if usdc is None:
            try:
                usdc = erc20_balance(address, BASE_USDC, "ethereum")
            except Exception:
                errors.append("usdc_balance_unavailable")

        # Blockscout v2 address payloads do not reliably expose an
        # is_contract boolean, so derive contract-vs-EOA from creation
        # evidence (a creation tx means deployed code) and GoPlus.
        is_contract = None
        if isinstance(acct, dict):
            if acct.get("is_contract") is not None:
                is_contract = bool(acct.get("is_contract"))
            elif acct.get("creation_transaction_hash") \
                    or acct.get("creator_address_hash"):
                is_contract = True
            elif acct.get("has_token_transfers") is not None \
                    and acct.get("creation_status") == "success" \
                    and not (acct.get("creation_transaction_hash")
                             or acct.get("creator_address_hash")):
                is_contract = False
        elif isinstance(goplus, dict) and goplus.get("contract_address"):
            is_contract = str(goplus.get("contract_address")) == "1"

        risk_signals = {}
        if isinstance(goplus, dict):
            for k, v in goplus.items():
                if k in ("contract_address",):
                    continue
                risk_signals[k] = (str(v) == "1")
        risk_flag_count = sum(1 for v in risk_signals.values() if v)

        creation = None
        if isinstance(acct, dict):
            creation = {
                "creator_address": (acct.get("creator_address_hash")
                                    or acct.get("creator_address")),
                "creation_tx": (acct.get("creation_transaction_hash")
                                or acct.get("creation_tx_hash")),
            }
        return {
            "address": address,
            "ens": ({
                "name": ens.get("name"),
                "display_name": ens.get("displayName"),
                "avatar": ens.get("avatar"),
            } if isinstance(ens, dict) else None),
            "is_contract": is_contract,
            "account": ({
                "name": acct.get("name"),
                "is_verified": bool(acct.get("is_verified")),
                "tx_count": (acct.get("transactions_count")
                             if acct.get("transactions_count") is not None
                             else acct.get("tx_count")),
                "token_transfers_count": acct.get("token_transfers_count"),
                "coin_balance_wei": acct.get("coin_balance"),
                "exchange_rate_usd": acct.get("exchange_rate"),
            } if isinstance(acct, dict) else None),
            "creation": creation,
            "goplus_risk_signals": risk_signals or None,
            "goplus_risk_flag_count": risk_flag_count,
            "usdc_balance_base": ({
                "balance": usdc.get("balance"),
                "decimals": usdc.get("decimals"),
                "symbol": usdc.get("symbol"),
            } if isinstance(usdc, dict) else None),
            "sources": ["ensideas", "blockscout", "goplus-address-security",
                        "base-rpc"],
            "source_errors": errors,
            "as_of": int(_time.time()),
            "disclaimer": "Public counterparty-screening signals only; "
                          "risk flags summarise GoPlus observations and are "
                          "not a judgement of any person.",
        }
    return _cached_fetch(f"addritel:{address.lower()}", 120, _produce)
