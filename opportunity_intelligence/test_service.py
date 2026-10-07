import json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import service

class OpportunityIntelligenceTests(unittest.TestCase):
    def test_capital_is_vetoed(self):
        r = service.evaluate_opportunity({"capital_required": 1, "reward_usd": 100, "probability": .5, "risk": .2, "automation_level": .9, "status": "eligible_verified", "payment_proof": "ESCROW_VERIFIED", "verification": "OFFICIAL"})
        self.assertEqual(r["verdict"], "REJECT_FOR_AUTONOMOUS_EXECUTION")
        self.assertTrue(any("CAPITAL" in v for v in r["vetoes"]))
        self.assertEqual(r["money_state"], "ESTIMATED_ONLY_NOT_REVENUE")
    def test_clean_candidate_can_pass(self):
        r = service.evaluate_opportunity({"capital_required": 0, "reward_usd": 500, "probability": .4, "risk": .3, "automation_level": .9, "status": "eligible_verified", "payment_proof": "ESCROW_VERIFIED", "verification": "OFFICIAL"})
        self.assertEqual(r["verdict"], "EXECUTE_AUTONOMOUSLY")
    def test_agent_terms_reject_personal_email(self):
        r = service.evaluate_agent_terms({"agentAccess":"AGENT_ALLOWED","description":"Research report","eligibility":[{"optional":False,"question":"What email did you use?"}]})
        self.assertEqual(r["gate"], "REJECT_AUTONOMOUS")
        self.assertFalse(r["submission_allowed"])
    def test_money_flow_has_disclaimer(self):
        r = service.money_flow_latest()
        if r.get("status") == "OK":
            self.assertIn("Not demand proof", r["disclaimer"])
    def test_abi_decoders(self):
        self.assertEqual(service._decode_uint("0x2a"), 42)
        raw = (32).to_bytes(32, "big") + (4).to_bytes(32, "big") + b"USDC" + b"\x00" * 28
        self.assertEqual(service._decode_string("0x" + raw.hex()), "USDC")
    def test_erc20_balance_with_mocked_rpc(self):
        old = service._rpc
        try:
            def fake(network, method, params):
                if method == "eth_blockNumber": return "0x10"
                data = params[0]["data"]
                if data.startswith("0x70a08231"): return "0x" + (1234567).to_bytes(32, "big").hex()
                if data == "0x313ce567": return "0x" + (6).to_bytes(32, "big").hex()
                if data == "0x95d89b41":
                    raw = (32).to_bytes(32, "big") + (4).to_bytes(32, "big") + b"USDC" + b"\x00" * 28
                    return "0x" + raw.hex()
                if data == "0x06fdde03":
                    raw = (32).to_bytes(32, "big") + (8).to_bytes(32, "big") + b"USD Coin" + b"\x00" * 24
                    return "0x" + raw.hex()
                raise AssertionError(data)
            service._rpc = fake
            r = service.erc20_balance("0x0000000000000000000000000000000000000001", "0x0000000000000000000000000000000000000002")
            self.assertEqual(r["balance"], "1234567")
            self.assertEqual(r["symbol"], "USDC")
            self.assertEqual(service.chain_block_number()["block_number"], 16)
        finally:
            service._rpc = old
    def test_x402_readiness_ready(self):
        body = json.dumps({"accepts":[{"scheme":"exact","network":"eip155:8453","asset":service.BASE_USDC,"amount":"3000","payTo":"0x0000000000000000000000000000000000000001"}],"extensions":{"bazaar":{}}}).encode()
        r = service.x402_readiness("https://example.com/paid", fetcher=lambda url: {"status":402,"headers":{},"body":body})
        self.assertEqual(r["verdict"], "READY_FOR_PAID_TEST_REVIEW")
        self.assertTrue(r["bazaar_metadata_present"])
    def test_x402_readiness_not_x402(self):
        r = service.x402_readiness("https://example.com/free", fetcher=lambda url: {"status":200,"headers":{},"body":b"{}"})
        self.assertEqual(r["verdict"], "NOT_X402_OR_UNREACHABLE")
    def test_x402_rejects_non_https(self):
        with self.assertRaises(ValueError):
            service.x402_readiness("http://example.com/paid", fetcher=lambda url: {"status":402,"headers":{},"body":b"{}"})

if __name__ == "__main__": unittest.main()
