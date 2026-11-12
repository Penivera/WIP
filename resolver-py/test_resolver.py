import copy
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
import pytest

from config import settings
from cs_manager import ChainSignaturesManager
from erc7930 import (
    BIP122_MAINNET_CAIP2,
    SOLANA_MAINNET_CAIP2,
    encode_erc7930,
    format_native_address,
)
from main import PAYMENT_DASHBOARD, app
from near_client import MOCK_CONTRACT_RECORDS
from offramp_adapter import PROVIDER_SANDBOX_DB
from proofs import (
    build_challenge_message,
    verify_chain_proof,
    verify_resolver_response_signature,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state_and_use_mock_by_default(monkeypatch):
    """
    Defaults unit tests to MOCK_CONTRACT_RECORDS while resetting mutable sandbox state
    between tests. Individual tests can flip settings.USE_MOCK_CONTRACT = False to test live RPC.
    """
    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", True)
    PROVIDER_SANDBOX_DB["prov_ref_demo_001"]["kycStatus"] = "VERIFIED"
    PAYMENT_DASHBOARD.clear()


# 1. LIVE RPC MODE (USE_MOCK_CONTRACT=false) TESTS

@pytest.mark.asyncio
async def test_live_testnet_mode_unclaimed_names_return_none_from_rpc(monkeypatch):
    """
    Directly tests the NearContractClient to prove that when USE_MOCK_CONTRACT=False,
    it queries the live unam-registry.testnet contract, receives null, and returns None.
    """
    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", False)
    
    from near_client import NearContractClient
    live_client = NearContractClient()
    
    # Use a guaranteed random unclaimed name (which is now not live!)
    unclaimed_name = "unclaimed-random-12345.uni"
    record = await live_client.get_record(unclaimed_name)
    assert record is None, f"Expected None for {unclaimed_name} on live testnet, got {record}"

    # Input validation must still catch bad names/chains with 400 before hitting RPC
    bad_name = client.get("/v1/resolve", params={"name": "admin.uni", "chain": "eip155:1"})
    assert bad_name.status_code == 400
    assert "NAME_INVALID" in bad_name.json()["detail"]["error"]

    bad_chain = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "invalidchain"})
    assert bad_chain.status_code == 400
    assert "CHAIN_KEY_INVALID" in bad_chain.json()["detail"]["error"]


# 2. TOKENS CATALOG ENDPOINT (/v1/tokens)

def test_get_supported_tokens_endpoint():
    resp = client.get("/v1/tokens")
    assert resp.status_code == 200
    tokens = resp.json()
    assert isinstance(tokens, list) and len(tokens) > 0
    assert all("assetId" in t and "symbol" in t and "blockchain" in t for t in tokens[:5])


# 3. INPUT VALIDATION EDGE CASES (NAME, CHAIN, AMOUNT -> 400)

@pytest.mark.parametrize(
    "bad_name",
    [
        "ab.uni",                        # Too short (<3 chars)
        f"{'a' * 33}.uni",               # Too long (>32 chars)
        "-start.uni",                    # Leading hyphen
        "end-.uni",                      # Trailing hyphen
        "double--dash.uni",              # Consecutive hyphens
        "bad_char.uni",                  # Underscore not allowed
        "p\u0435ni.uni",                 # Cyrillic 'е' homograph attack
        "admin.uni",                     # Reserved name
        "near.uni",                      # Reserved name
        "uni.uni",                       # Reserved name
        "null.uni",                      # Reserved name
    ],
)
def test_name_validation_rejects_all_invalid_and_reserved_names(bad_name: str):
    resp = client.get("/v1/resolve", params={"name": bad_name, "chain": "eip155:1"})
    assert resp.status_code == 400
    assert "NAME_INVALID" in resp.json()["detail"]["error"]

    dash_resp = client.get(f"/v1/dashboard/{bad_name}")
    assert dash_resp.status_code == 400

    cs_resp = client.get("/v1/cs/derive", params={"name": bad_name, "chain": "eip155:1"})
    assert cs_resp.status_code == 400


@pytest.mark.parametrize(
    "bad_chain",
    [
        "no-colon",
        "eip155:",
        "eip155:abc",
        "eip155:0",
        "eip155:2147483648",
        "solana:short",
        "bip122:nothex",
        "near:devnet",
        "cosmos:cosmoshub-4",
    ],
)
def test_chain_validation_rejects_all_invalid_chains(bad_chain: str):
    resp = client.get("/v1/resolve", params={"name": "demo.uni", "chain": bad_chain})
    assert resp.status_code == 400
    assert "CHAIN_KEY_INVALID" in resp.json()["detail"]["error"]

    cs_resp = client.get("/v1/cs/derive", params={"name": "demo.uni", "chain": bad_chain})
    assert cs_resp.status_code == 400


@pytest.mark.parametrize("bad_amount", ["abc", "0.01", "0", "-100", "10.5"])
def test_amount_validation_rejects_non_integer_or_non_positive_amounts(bad_amount: str):
    resp = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "eip155:8453", "token": "native", "amount": bad_amount},
    )
    assert resp.status_code == 400
    assert "AMOUNT_INVALID" in resp.json()["detail"]["error"]


# 4. 5-STEP RESOLUTION FLOWS & CRYPTOGRAPHIC PROOFS

def test_resolution_step1_unknown_name_returns_name_not_found():
    resp = client.get("/v1/resolve", params={"name": "ghost.uni", "chain": "eip155:1"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NAME_NOT_FOUND"


def test_resolution_step2_force_default_flex_exact_dry_and_erc20_contract_token():
    # 1. Omitted amount -> FLEX_INPUT
    flex = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "base", "token": "native", "live_quote": "false"},
    )
    assert flex.status_code == 200
    assert flex.json()["resolved"]["swapType"] == "FLEX_INPUT"
    assert verify_resolver_response_signature(flex.json(), flex.json()["signature"]) is True

    # 2. Explicit amount -> EXACT_INPUT with live 1Click quote
    exact = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "eip155:8453", "token": "native", "amount": "1000000000000000"},
    )
    assert exact.status_code == 200
    data = exact.json()
    assert data["mode"] == "routed"
    assert data["resolved"]["swapType"] == "EXACT_INPUT"
    assert data["policy"]["destinationAsset"].startswith("USDC@solana:")
    assert data["resolved"]["outToken"] == "USDC"
    assert data["resolved"]["depositAddress"].startswith("0x")
    assert verify_resolver_response_signature(data, data["signature"]) is True

    # 3. Dry quote + ERC-20 contract address token (Base USDC)
    dry_erc20 = client.get(
        "/v1/resolve",
        params={
            "name": "routed.uni",
            "chain": "eip155:8453",
            "token": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "amount": "10000000",
            "dry": "true",
            "live_quote": "false",
        },
    )
    assert dry_erc20.status_code == 200
    assert dry_erc20.json()["policy"]["sourceAsset"] == "USDC@eip155:8453"
    assert dry_erc20.json()["resolved"]["fee"]["totalFeeBps"] == 2  # Stablecoin 2 bps floor


def test_resolution_step3_direct_connected_chains_and_shorthands():
    # Ethereum direct
    eth_resp = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eth", "token": "native"})
    assert eth_resp.status_code == 200
    eth_data = eth_resp.json()
    assert eth_data["mode"] == "direct"
    assert eth_data["resolved"]["address"] == "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
    assert eth_data["resolved"]["interoperableAddress"].startswith("0x00010000")
    assert eth_data["resolved"]["coinType"] == (0x8000_0000 | 1)
    assert verify_resolver_response_signature(eth_data, eth_data["signature"]) is True

    # Solana direct
    sol_resp = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "sol", "token": "SOL"})
    assert sol_resp.status_code == 200
    assert sol_resp.json()["resolved"]["address"] == "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
    assert sol_resp.json()["resolved"]["interoperableAddress"].startswith("0x00010002")

    # NEAR direct
    near_resp = client.get("/v1/resolve", params={"name": "peni.uni", "chain": "near", "token": "native"})
    assert near_resp.status_code == 200
    assert near_resp.json()["resolved"]["address"] == "peni.testnet"
    assert near_resp.json()["resolved"]["interoperableAddress"].startswith("0x0001018d")


def test_resolution_step3_rejects_empty_invalid_or_stale_proofs_with_409(monkeypatch):
    # 1. Stale generation after transfer
    stale_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    stale_rec["generation"] = 2
    stale_rec["chains"]["eip155:1"]["generation"] = 1
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", stale_rec)

    r1 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r1.status_code == 409
    assert r1.json()["detail"]["reason"] == "STALE_GENERATION_AFTER_TRANSFER"

    # 2. Invalid signature marker
    bad_sig_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    bad_sig_rec["generation"] = 1
    bad_sig_rec["chains"]["eip155:1"]["generation"] = 1
    bad_sig_rec["chains"]["eip155:1"]["proof"] = list(b"INVALID_SIG")
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", bad_sig_rec)

    r2 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["reason"] == "INVALID_SIGNATURE"

    # 3. Empty proof
    empty_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    empty_rec["chains"]["eip155:1"]["proof"] = []
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", empty_rec)

    r3 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r3.status_code == 409
    assert r3.json()["detail"]["reason"] == "EMPTY_PROOF"


def test_strict_cryptographic_eip191_and_solana_ed25519_proof_verification(monkeypatch):
    monkeypatch.setattr(settings, "PROOF_VERIFICATION_MODE", "strict")

    # 1. Real Ethereum EIP-191 secp256k1 signature
    eth_acct = Account.create()
    eth_raw = bytes.fromhex(eth_acct.address[2:])
    eth_challenge = build_challenge_message("alice.uni", "alice.testnet", "eip155:1", eth_raw.hex(), 1)
    eth_sig = eth_acct.sign_message(encode_defunct(text=eth_challenge)).signature

    ok_eth, reason_eth = verify_chain_proof(
        "alice.uni", "alice.testnet", "eip155:1", eth_raw, bytes(eth_sig), 1, 1
    )
    assert ok_eth is True and reason_eth == "VERIFIED_EIP191_SECP256K1"

    # 2. Real Solana Ed25519 signature
    sol_sk = SigningKey.generate()
    sol_raw = bytes(sol_sk.verify_key)
    sol_challenge = build_challenge_message(
        "alice.uni", "alice.testnet", SOLANA_MAINNET_CAIP2, sol_raw.hex(), 1
    )
    sol_sig = sol_sk.sign(sol_challenge.encode("utf-8")).signature

    ok_sol, reason_sol = verify_chain_proof(
        "alice.uni", "alice.testnet", SOLANA_MAINNET_CAIP2, sol_raw, bytes(sol_sig), 1, 1
    )
    assert ok_sol is True and reason_sol == "VERIFIED_SOLANA_ED25519"


def test_frontend_vec_u8_encoding_variants_normalize_identically():
    eth_str = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
    raw_20 = bytes.fromhex(eth_str[2:])
    ascii_42 = eth_str.encode("ascii")
    erc7930_env = bytes.fromhex(encode_erc7930("eip155:1", raw_20)[2:])

    assert format_native_address("eip155:1", raw_20) == eth_str
    assert format_native_address("eip155:1", ascii_42) == eth_str
    assert format_native_address("eip155:1", erc7930_env) == eth_str
    assert encode_erc7930("eip155:1", ascii_42) == encode_erc7930("eip155:1", raw_20)
    assert encode_erc7930("eip155:1", erc7930_env) == encode_erc7930("eip155:1", raw_20)

    sol_str = "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
    sol_ascii = sol_str.encode("ascii")
    assert format_native_address(SOLANA_MAINNET_CAIP2, sol_ascii) == sol_str


def test_resolution_step4_fallback_policy_and_unsupported_token_no_route():
    ok_resp = client.get(
        "/v1/resolve",
        params={
            "name": "peni.uni",
            "chain": BIP122_MAINNET_CAIP2,
            "token": "BTC",
            "live_quote": "false",
        },
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["mode"] == "routed"

    bad_resp = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "eip155:1", "token": "FAKETOKEN123"},
    )
    assert bad_resp.status_code == 404
    assert bad_resp.json()["detail"]["error"] == "NO_ROUTE"
    assert len(bad_resp.json()["detail"]["supported"]) > 0


def test_resolution_step5_unconnected_chain_returns_honest_no_record():
    resp = client.get(
        "/v1/resolve",
        params={"name": "demo.uni", "chain": BIP122_MAINNET_CAIP2},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "NO_RECORD"
    assert "no wallet connected" in detail["message"]


def test_below_minimum_warning_before_signing():
    resp = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "eip155:8453", "token": "native", "amount": "100", "live_quote": "false"},
    )
    assert resp.status_code == 200
    warnings = resp.json()["resolved"]["warnings"]
    assert any("BELOW_MINIMUM" in w for w in warnings)
    assert resp.json()["resolved"]["estReceive"] == "0"

# 5. BANK MODE, DEPOSIT SUBMIT, STATUS, DASHBOARD & DEMO TRANSITIONS

def test_end_to_end_bank_mode_deposit_submit_status_dashboard_and_transitions():
    # 1. Resolve bank.uni
    resp = client.get(
        "/v1/resolve",
        params={"name": "bank.uni", "chain": "eip155:1", "token": "USDC", "amount": "50000000", "live_quote": "false"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "bank"
    dep_addr = data["resolved"]["depositAddress"]

    # 2. Check dashboard shows PENDING_DEPOSIT
    dash = client.get("/v1/dashboard/bank.uni")
    assert dash.status_code == 200
    assert len(dash.json()["payments"]) == 1
    assert dash.json()["payments"][0]["status"] == "PENDING_DEPOSIT"

    # 3. Submit deposit tx hash
    sub = client.post(
        "/v1/deposit/submit",
        json={"depositAddress": dep_addr, "txHash": "0xdeadbeef1234567890"},
    )
    assert sub.status_code == 200

    # 4. Poll status endpoint
    st = client.get("/v1/status", params={"depositAddress": dep_addr})
    assert st.status_code == 200
    assert "status" in st.json()

    # 5. Simulate Failure Path E: Off-ramp rejects -> held as USDC, flagged for action
    sim_reject = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "OFFRAMP_REJECT"},
    )
    assert sim_reject.status_code == 200
    assert sim_reject.json()["status"] == "OFFRAMP_REJECTED_HELD_AS_USDC"
    assert sim_reject.json()["offrampReceipt"]["flaggedForAction"] is True

    # 6. Simulate SUCCESS -> settled to bank
    sim_ok = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "SUCCESS"},
    )
    assert sim_ok.status_code == 200
    assert sim_ok.json()["status"] == "SETTLED_TO_BANK"
    assert sim_ok.json()["offrampReceipt"]["flaggedForAction"] is False

    # 7. Simulate REFUNDED
    sim_ref = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "REFUNDED"},
    )
    assert sim_ref.status_code == 200
    assert sim_ref.json()["status"] == "REFUNDED"

    # 8. Unknown deposit address returns 404
    sim_404 = client.post(
        "/v1/demo/transition",
        json={"deposit_address": "0x000000000000000000000000000000000000dead", "event": "SUCCESS"},
    )
    assert sim_404.status_code == 404


# 6. CHAIN SIGNATURES DERIVATION (/v1/cs/derive) & MPC SWEEP SECURITY

def test_chain_signatures_all_namespaces_and_sweep_security():
    # Test /v1/cs/derive across all 4 namespaces
    for chain, expected_prefix in [
        ("eip155:1", "0x"),
        (SOLANA_MAINNET_CAIP2, ""),
        (BIP122_MAINNET_CAIP2, "1"),
        ("near:testnet", ""),
    ]:
        r = client.get("/v1/cs/derive", params={"name": "peni.uni", "chain": chain})
        assert r.status_code == 200
        body = r.json()
        assert body["depositAddress"].startswith(expected_prefix)
        assert body["interoperableAddress"].startswith("0x0001")

    cs = ChainSignaturesManager()
    d1 = cs.derive_deposit_address("peni.uni", "eip155:1", "unam-registry.testnet")
    d2 = cs.derive_deposit_address("PENI.UNI", "eip155:1", "unam-registry.testnet")
    assert d1["depositAddress"] == d2["depositAddress"]

    # Unauthorized caller rejected
    with pytest.raises(PermissionError, match="UNAUTHORIZED_SWEEP_CALLER"):
        cs.request_mpc_sweep(
            caller_account="attacker.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=100.0,
        )

    # Unapproved destination rejected
    with pytest.raises(PermissionError, match="UNAPPROVED_SWEEP_DESTINATION"):
        cs.request_mpc_sweep(
            caller_account="watcher.uni.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0xdeadbeef",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=100.0,
        )

    # Authorized sweep succeeds and logs audit entry
    sweep_ok = cs.request_mpc_sweep(
        caller_account="watcher.uni.testnet",
        name="peni.uni",
        chain_key="eip155:1",
        destination_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
        allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
        amount_usd=1000.0,
        pending_mpc_requests=50,
    )
    assert sweep_ok["status"] == "MPC_SIGNATURE_ISSUED_AND_SWEPT"

    # Per-name USD limit exceeded rejected
    with pytest.raises(ValueError, match="SWEEP_LIMIT_EXCEEDED_FOR_NAME"):
        cs.request_mpc_sweep(
            caller_account="watcher.uni.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=4500.0,
        )
