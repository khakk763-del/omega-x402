#!/usr/bin/env python3
"""Base Sepolia settlement proof for the OMEGA x402 service.

Generates ONE ephemeral testnet payer keypair, persists ONLY its
address + a reference note in proof_payer.json (the key itself is
written to proof_payer_key.tmp, mode 600, and is expected to be
deleted after the proof; it is a zero-value testnet key).
Signs an EIP-3009 authorization with the x402 client SDK, pays the
local Sepolia instance, and reports balances/tx evidence.

Run: .venv/bin/python proof_sepolia.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

# VM egress quirk: inherited no_proxy has bracketed IPv6 that crashes httpx.
for _v in ("no_proxy", "NO_PROXY"):
    _vals = [p.strip("[]") for p in os.getenv(_v, "").split(",") if p.strip()]
    os.environ[_v] = ",".join(x for x in _vals if ":" not in x and x)

BASE = "http://127.0.0.1:8767"
ADDR_FILE = os.path.join(os.path.dirname(__file__), "proof_payer.json")
KEY_FILE = os.path.join(os.path.dirname(__file__), "proof_payer_key.tmp")
RPC = "https://base-sepolia-rpc.publicnode.com"
SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAY_TO = "0xCF3388dB231300f88285351154FbC17098A01bD2"


RPCS = ["https://sepolia.base.org", "https://base-sepolia-rpc.publicnode.com"]


def rpc(method, params):
    last = None
    for url in RPCS:
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "OMEGA-x402-proof/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read()).get("result")
        except Exception as e:  # try next public endpoint
            last = e
            time.sleep(2)
    raise RuntimeError(f"all RPCs failed: {last}")


def usdc_balance(addr):
    data = "0x70a08231" + addr[2:].rjust(64, "0")
    return int(rpc("eth_call", [{"to": SEPOLIA_USDC, "data": data}, "latest"]), 16)


def eth_balance(addr):
    return int(rpc("eth_getBalance", [addr, "latest"]), 16)


def main():
    from eth_account import Account

    if os.path.exists(KEY_FILE):
        acct = Account.from_key(open(KEY_FILE).read().strip())
        print("reusing existing test payer:", acct.address)
    else:
        acct = Account.create()
        with open(KEY_FILE, "w") as f:
            f.write(acct.key.hex())
        os.chmod(KEY_FILE, 0o600)
    meta = {"payer_address": acct.address, "created_for": "base-sepolia-x402-proof",
            "value": "zero — ephemeral testnet key", "created_at": int(time.time())}
    with open(ADDR_FILE, "w") as f:
        json.dump(meta, f, indent=2)
    print("PAYER_ADDRESS:", acct.address)
    print("payer USDC (base units):", usdc_balance(acct.address))
    print("payer ETH (wei):", eth_balance(acct.address))
    print("payTo USDC before (base units):", usdc_balance(PAY_TO))

    # Give the operator a window to fund from a public faucet if balance is 0.
    deadline = time.time() + int(os.getenv("PROOF_WAIT_SECONDS", "0"))
    while time.time() < deadline:
        bal = usdc_balance(acct.address)
        if bal >= 50000:
            print("funded:", bal)
            break
        time.sleep(10)

    import asyncio

    async def go():
        import httpx
        from x402.client import x402Client
        from x402.mechanisms.evm.exact import ExactEvmScheme

        from x402.http.x402_http_client import x402HTTPClient
        from x402.mechanisms.evm.signers import EthAccountSigner

        signer = EthAccountSigner(acct)
        client = x402Client()
        client.register("eip155:84532", ExactEvmScheme(signer))
        xhttp = x402HTTPClient(client)
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(BASE + "/v1/evaluate-opportunity",
                                json={"capital_required": 0, "reward_usd": 500, "probability": 0.4})
            print("unpaid status:", r.status_code)
            assert r.status_code == 402
            payment_required = xhttp.get_payment_required_response(
                lambda n: r.headers.get(n), r.content)
            payload = await xhttp.create_payment_payload(payment_required)
            sig_header = xhttp.encode_payment_signature_header(payload)
            r2 = await http.post(BASE + "/v1/evaluate-opportunity",
                                 json={"capital_required": 0, "reward_usd": 500, "probability": 0.4},
                                 headers=sig_header)
            print("paid status:", r2.status_code)
            print("paid body:", r2.text[:500])
            pr = r2.headers.get("payment-response") or r2.headers.get("PAYMENT-RESPONSE")
            if pr:
                import base64
                try:
                    print("payment-response:", base64.b64decode(pr).decode()[:600])
                except Exception:
                    print("payment-response (raw):", pr[:600])

    asyncio.run(go())
    time.sleep(4)
    print("payer USDC after:", usdc_balance(acct.address))
    print("payTo USDC after:", usdc_balance(PAY_TO))


if __name__ == "__main__":
    main()
