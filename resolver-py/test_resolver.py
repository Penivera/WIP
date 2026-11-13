import base64
import copy
import json

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
import pytest

from config import DEV_SIGNING_SEED_HEX, Settings, settings
from cs_manager import ChainSignaturesManager
from erc7930 import (
    BIP122_MAINNET_CAIP2,
    SOLANA_MAINNET_CAIP2,
    encode_erc7930,
    format_native_address,
)
from main import PAYMENT_DASHBOARD, _extract_policy_target, app, checkout_adapter, router
from near_client import MOCK_CONTRACT_RECORDS, NearContractClient, NearRpcError
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
    monkeypatch.setattr(settings, "NEAR_NETWORK", "testnet")
    monkeypatch.setattr(settings, "ALLOW_DEMO_ENDPOINTS", True)
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "")
    PROVIDER_SANDBOX_DB["prov_ref_demo_001"]["kycStatus"] = "VERIFIED"
    PROVIDER_SANDBOX_DB["ref-001"]["kycStatus"] = "VERIFIED"
    PAYMENT_DASHBOARD.clear()
    checkout_adapter.sessions.clear()
    checkout_adapter.used_tx_hashes.clear()
    checkout_adapter.in_flight_tx_hashes.clear()
    checkout_adapter.in_flight_sessions.clear()


# 1. LIVE RPC MODE (USE_MOCK_CONTRACT=false) TESTS
@pytest.mark.asyncio
async def test_live_testnet_mode_unclaimed_names_return_none_from_rpc(monkeypatch):
    """
    Directly tests the NearContractClient to prove that when USE_MOCK_CONTRACT=False,
    it queries the live unam-registry.testnet contract, receives null, and returns None.
    """
    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", False)

    live_client = NearContractClient()

    unclaimed_name = "unclaimed-random-12345.uni"
    record = await live_client.get_record(unclaimed_name)
    assert record is None, f"Expected None for {unclaimed_name} on live testnet, got {record}"

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
    flex = client.get(
        "/v1/resolve",
        params={"name": "routed.uni", "chain": "base", "token": "native", "live_quote": "false"},
    )
    assert flex.status_code == 200
    assert flex.json()["resolved"]["swapType"] == "FLEX_INPUT"
    assert verify_resolver_response_signature(flex.json(), flex.json()["signature"]) is True

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
    assert dry_erc20.json()["resolved"]["fee"]["totalFeeBps"] == 2


def test_resolution_step3_direct_connected_chains_and_shorthands():
    eth_resp = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eth", "token": "native"})
    assert eth_resp.status_code == 200
    eth_data = eth_resp.json()
    assert eth_data["mode"] == "direct"
    assert eth_data["resolved"]["address"] == "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
    assert eth_data["resolved"]["interoperableAddress"].startswith("0x00010000")
    assert eth_data["resolved"]["coinType"] == (0x8000_0000 | 1)
    assert verify_resolver_response_signature(eth_data, eth_data["signature"]) is True

    sol_resp = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "sol", "token": "SOL"})
    assert sol_resp.status_code == 200
    assert sol_resp.json()["resolved"]["address"] == "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
    assert sol_resp.json()["resolved"]["interoperableAddress"].startswith("0x00010002")

    near_resp = client.get("/v1/resolve", params={"name": "peni.uni", "chain": "near", "token": "native"})
    assert near_resp.status_code == 200
    assert near_resp.json()["resolved"]["address"] == "peni.testnet"
    assert near_resp.json()["resolved"]["interoperableAddress"].startswith("0x0001018d")


def test_resolution_step3_rejects_empty_invalid_or_stale_proofs_with_409(monkeypatch):
    stale_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    stale_rec["generation"] = 2
    stale_rec["chains"]["eip155:1"]["generation"] = 1
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", stale_rec)

    r1 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r1.status_code == 409
    assert r1.json()["detail"]["reason"] == "STALE_GENERATION_AFTER_TRANSFER"

    bad_sig_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    bad_sig_rec["generation"] = 1
    bad_sig_rec["chains"]["eip155:1"]["generation"] = 1
    bad_sig_rec["chains"]["eip155:1"]["proof"] = list(b"INVALID_SIG")
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", bad_sig_rec)

    r2 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["reason"] == "INVALID_SIGNATURE"

    empty_rec = copy.deepcopy(MOCK_CONTRACT_RECORDS["demo.uni"])
    empty_rec["chains"]["eip155:1"]["proof"] = []
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "demo.uni", empty_rec)

    r3 = client.get("/v1/resolve", params={"name": "demo.uni", "chain": "eip155:1"})
    assert r3.status_code == 409
    assert r3.json()["detail"]["reason"] == "EMPTY_PROOF"


def test_strict_cryptographic_eip191_and_solana_ed25519_proof_verification(monkeypatch):
    monkeypatch.setattr(settings, "PROOF_VERIFICATION_MODE", "strict")

    eth_acct = Account.create()
    eth_raw = bytes.fromhex(eth_acct.address[2:])
    eth_challenge = build_challenge_message("alice.uni", "alice.testnet", "eip155:1", eth_raw.hex(), 1)
    eth_sig = eth_acct.sign_message(encode_defunct(text=eth_challenge)).signature

    ok_eth, reason_eth = verify_chain_proof(
        "alice.uni", "alice.testnet", "eip155:1", eth_raw, bytes(eth_sig), 1, 1
    )
    assert ok_eth is True and reason_eth == "VERIFIED_EIP191_SECP256K1"

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
    resp = client.get(
        "/v1/resolve",
        params={"name": "bank.uni", "chain": "eip155:1", "token": "USDC", "amount": "50000000", "live_quote": "false"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "bank"
    dep_addr = data["resolved"]["depositAddress"]

    dash = client.get("/v1/dashboard/bank.uni")
    assert dash.status_code == 200
    assert len(dash.json()["payments"]) == 1
    assert dash.json()["payments"][0]["status"] == "PENDING_DEPOSIT"

    sub = client.post(
        "/v1/deposit/submit",
        json={"depositAddress": dep_addr, "txHash": "0xdeadbeef1234567890"},
    )
    assert sub.status_code == 200

    st = client.get("/v1/status", params={"depositAddress": dep_addr})
    assert st.status_code == 200
    assert "status" in st.json()

    sim_reject = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "OFFRAMP_REJECT"},
    )
    assert sim_reject.status_code == 200
    assert sim_reject.json()["status"] == "OFFRAMP_REJECTED_HELD_AS_USDC"
    assert sim_reject.json()["offrampReceipt"]["flaggedForAction"] is True

    sim_ok = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "SUCCESS"},
    )
    assert sim_ok.status_code == 200
    assert sim_ok.json()["status"] == "SETTLED_TO_BANK"
    assert sim_ok.json()["offrampReceipt"]["flaggedForAction"] is False

    sim_ref = client.post(
        "/v1/demo/transition",
        json={"deposit_address": dep_addr, "event": "REFUNDED"},
    )
    assert sim_ref.status_code == 200
    assert sim_ref.json()["status"] == "REFUNDED"

    sim_404 = client.post(
        "/v1/demo/transition",
        json={"deposit_address": "0x000000000000000000000000000000000000dead", "event": "SUCCESS"},
    )
    assert sim_404.status_code == 404


# 6. CHAIN SIGNATURES DERIVATION (/v1/cs/derive) & MPC SWEEP SECURITY
def test_chain_signatures_all_namespaces_and_sweep_security():
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

    with pytest.raises(PermissionError, match="UNAUTHORIZED_SWEEP_CALLER"):
        cs.request_mpc_sweep(
            caller_account="attacker.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=100.0,
        )

    with pytest.raises(PermissionError, match="UNAPPROVED_SWEEP_DESTINATION"):
        cs.request_mpc_sweep(
            caller_account="watcher.uni.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0xdeadbeef",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=100.0,
        )

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

    with pytest.raises(ValueError, match="SWEEP_LIMIT_EXCEEDED_FOR_NAME"):
        cs.request_mpc_sweep(
            caller_account="watcher.uni.testnet",
            name="peni.uni",
            chain_key="eip155:1",
            destination_address="0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
            allowed_destinations={"0x71C7656EC7ab88b098defB751B7401B5f6d8976F"},
            amount_usd=4500.0,
        )


# 7. BANK MODE (SOLANA & EVM), PROVIDER VALIDATION, UNSUPPORTED TOKENS/DESTINATIONS, MAINNET & LIVE FAILURE GUARDS
def test_bank_mode_solana_and_evm_destinations_provider_validation_and_rejections(monkeypatch):
    evm_addr_hex = "71C7656EC7ab88b098defB751B7401B5f6d8976F"
    evm_bank_record = {
        "owner": "evmbank.testnet",
        "generation": 1,
        "chains": {},
        "policy": {
            "force_default": True,
            "default": {
                "Asset": {
                    "asset": "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near",
                    "chain": "eip155:8453",
                    "address": list(bytes.fromhex(evm_addr_hex)),
                }
            },
            "payout": {
                "Payout": {
                    "provider": "demo-offramp",
                    "reference": "ref-001",
                }
            },
        },
        "pending": None,
    }

    # 1. EVM Bank Mode with valid 'demo-offramp' / 'ref-001'
    mode, dest_chain, dest_asset, recipient_addr, payout_meta = _extract_policy_target(
        evm_bank_record["policy"]
    )
    assert mode == "bank"
    assert dest_chain == "eip155:8453"
    assert recipient_addr.lower() == f"0x{evm_addr_hex}".lower()
    assert payout_meta["verified"] is True

    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "evmbank.uni", evm_bank_record)
    resp = client.get(
        "/v1/resolve",
        params={"name": "evmbank.uni", "chain": "eip155:1", "token": "USDC", "amount": "10000000", "live_quote": "false"},
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"]["outChainId"] == "eip155:8453"
    dep_addr = resp.json()["resolved"]["depositAddress"]

    sim_ok = client.post("/v1/demo/transition", json={"deposit_address": dep_addr, "event": "SUCCESS"})
    assert sim_ok.status_code == 200
    assert sim_ok.json()["status"] == "SETTLED_TO_BANK"

    # 2. Known reference ('prov_ref_demo_001') paired with wrong provider ('demo-offramp') -> rejected
    mismatch_record = copy.deepcopy(evm_bank_record)
    mismatch_record["policy"]["payout"]["Payout"] = {
        "provider": "demo-offramp",
        "reference": "prov_ref_demo_001",
    }
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "mismatchbank.uni", mismatch_record)

    resp_mis = client.get(
        "/v1/resolve",
        params={"name": "mismatchbank.uni", "chain": "eip155:1", "token": "USDC", "amount": "10000000", "live_quote": "false"},
    )
    assert resp_mis.status_code == 200
    assert any("OFFRAMP_REFERENCE_UNVERIFIED" in w for w in resp_mis.json()["resolved"]["warnings"])
    dep_mis = resp_mis.json()["resolved"]["depositAddress"]

    sim_mis = client.post("/v1/demo/transition", json={"deposit_address": dep_mis, "event": "SUCCESS"})
    assert sim_mis.status_code == 200
    assert sim_mis.json()["status"] == "OFFRAMP_REJECTED_HELD_AS_USDC"
    assert sim_mis.json()["offrampReceipt"]["flaggedForAction"] is True

    # 3. Rejection of unsupported bank token (e.g. ETH instead of USDC)
    bad_tok_record = copy.deepcopy(evm_bank_record)
    bad_tok_record["policy"]["default"]["Asset"]["asset"] = "nep141:base.omft.near"
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "badtokbank.uni", bad_tok_record)

    resp_bad_tok = client.get(
        "/v1/resolve",
        params={"name": "badtokbank.uni", "chain": "eip155:1", "token": "USDC", "amount": "10000000", "live_quote": "false"},
    )
    assert resp_bad_tok.status_code == 400
    assert "UNSUPPORTED_BANK_TOKEN" in resp_bad_tok.json()["detail"]["error"]

    # 4. Rejection of invalid bank destination address
    bad_dest_record = copy.deepcopy(evm_bank_record)
    bad_dest_record["policy"]["default"]["Asset"]["address"] = [0x99] * 20
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "baddestbank.uni", bad_dest_record)

    resp_bad_dest = client.get(
        "/v1/resolve",
        params={"name": "baddestbank.uni", "chain": "eip155:1", "token": "USDC", "amount": "10000000", "live_quote": "false"},
    )
    assert resp_bad_dest.status_code == 400
    assert "UNSUPPORTED_BANK_DESTINATION" in resp_bad_dest.json()["detail"]["error"]


def test_mainnet_config_rejects_dev_seed_permissive_proof_and_missing_registry():
    valid_prod_seed = "ab" * 32

    # 1. Missing explicit REGISTRY_CONTRACT_ID on mainnet -> rejected
    with pytest.raises(ValueError, match="REGISTRY_CONTRACT_ID"):
        Settings.from_env({"NEAR_NETWORK": "mainnet", "RESOLVER_SIGNING_SEED_HEX": valid_prod_seed})

    # 2. Dev seed (including uppercase hex representation) on mainnet -> rejected
    with pytest.raises(ValueError, match="RESOLVER_SIGNING_SEED_HEX"):
        Settings.from_env(
            {
                "NEAR_NETWORK": "mainnet",
                "REGISTRY_CONTRACT_ID": "registry.near",
                "RESOLVER_SIGNING_SEED_HEX": DEV_SIGNING_SEED_HEX.upper(),
            }
        )

    # 3. Permissive proof mode on mainnet -> rejected
    with pytest.raises(ValueError, match="PROOF_VERIFICATION_MODE"):
        Settings.from_env(
            {
                "NEAR_NETWORK": "mainnet",
                "REGISTRY_CONTRACT_ID": "registry.near",
                "RESOLVER_SIGNING_SEED_HEX": valid_prod_seed,
                "PROOF_VERIFICATION_MODE": "permissive",
            }
        )

    # 4. Valid mainnet configuration -> succeeds with strict proof mode, ALLOW_DEMO_ENDPOINTS=False, and v1.signer
    s = Settings.from_env(
        {
            "NEAR_NETWORK": "mainnet",
            "REGISTRY_CONTRACT_ID": "registry.near",
            "RESOLVER_SIGNING_SEED_HEX": valid_prod_seed,
        }
    )
    assert s.NEAR_NETWORK == "mainnet"
    assert s.NEAR_RPC_URL == "https://rpc.mainnet.near.org"
    assert s.MPC_SIGNER_CONTRACT_ID == "v1.signer"
    assert s.PROOF_VERIFICATION_MODE == "strict"
    assert s.ALLOW_DEMO_ENDPOINTS is False
    assert s.CS_WATCHER_ACCOUNT_ID == ""


@pytest.mark.asyncio
async def test_live_transaction_failure_raises_error_instead_of_mock_success(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", False)
    monkeypatch.setattr(settings, "ADMIN_PRIVATE_KEY", "ed25519:fakekey")

    class DummyProc:
        returncode = 1

        async def communicate(self):
            return b"", b"Transaction rejected by RPC"

    async def fake_subproc(*args, **kwargs):
        return DummyProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subproc)

    nc = NearContractClient()
    with pytest.raises(RuntimeError, match="Live claim_for failed"):
        await nc.execute_claim_for("failtest.uni", "alice.testnet", "ref_123")

    with pytest.raises(RuntimeError, match="Live admin_approve_pending_policy failed"):
        await nc.admin_approve_pending_policy("failtest.uni")


def test_rpc_503_admin_auth_testnet_demo_buttons_and_bank_quote_routing(monkeypatch):
    # 1. Sample uncontrolled bank.uni address blocks live 1Click quote creation
    resp_sample_bank = client.get(
        "/v1/resolve",
        params={"name": "bank.uni", "chain": "eip155:1", "token": "USDC", "amount": "50000000", "live_quote": "true"},
    )
    assert resp_sample_bank.status_code == 200
    b_sample = resp_sample_bank.json()
    assert b_sample["resolved"]["provider"] != "near-intents-1click"
    assert any("SANDBOX_SAMPLE_ADDRESS_BLOCKED" in w for w in b_sample["resolved"]["warnings"])

    # 2. User-configured controlled bank payout address in policy.default.Asset passes live_1click_quote=True
    user_controlled_evm_addr = "2527d02599ba641c19fea793cd0f167589a0f10d"
    user_bank_record = {
        "owner": "alice.testnet",
        "generation": 1,
        "chains": {},
        "policy": {
            "force_default": True,
            "default": {
                "Asset": {
                    "asset": "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near",
                    "chain": "eip155:8453",
                    "address": list(bytes.fromhex(user_controlled_evm_addr)),
                }
            },
            "payout": {
                "Payout": {
                    "provider": "demo-offramp",
                    "reference": "ref-001",
                }
            },
        },
        "pending": None,
    }
    monkeypatch.setitem(MOCK_CONTRACT_RECORDS, "userbank.uni", user_bank_record)

    captured_route_kwargs = {}
    orig_resolve_route = router.resolve_route

    async def mocked_resolve_route(**kwargs):
        captured_route_kwargs.update(kwargs)
        res = await orig_resolve_route(**{**kwargs, "live_1click_quote": False})
        res["provider"] = "near-intents-1click"
        res["expiresAt"] = "2026-11-14T12:00:00Z"
        return res

    monkeypatch.setattr(router, "resolve_route", mocked_resolve_route)

    resp_user_bank = client.get(
        "/v1/resolve",
        params={"name": "userbank.uni", "chain": "eip155:8453", "token": "native", "amount": "10000000000000000", "live_quote": "true"},
    )
    assert resp_user_bank.status_code == 200
    assert captured_route_kwargs.get("live_1click_quote") is True
    assert resp_user_bank.json()["resolved"]["provider"] == "near-intents-1click"

    monkeypatch.setattr(router, "resolve_route", orig_resolve_route)

    # 3. Controlled Testnet demo mode (NEAR_NETWORK=testnet, ALLOW_DEMO_ENDPOINTS=True, USE_MOCK_CONTRACT=False):
    #    UI demo buttons (/v1/checkout/simulate and /v1/admin/approve-policy) execute live CLI calls
    chk = client.post(
        "/v1/checkout/session",
        json={"name": "livedemo.uni", "owner": "alice.testnet", "chain": "base", "token": "USDC"},
    ).json()

    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", False)
    monkeypatch.setattr(settings, "NEAR_NETWORK", "testnet")
    monkeypatch.setattr(settings, "ALLOW_DEMO_ENDPOINTS", True)
    monkeypatch.setattr(settings, "ADMIN_PRIVATE_KEY", "ed25519:fakekey")

    class SuccessProc:
        returncode = 0

        async def communicate(self):
            return b"Transaction ID: 9xLiveTestnetTxHash111\n", b""

    async def fake_ok_subproc(*args, **kwargs):
        return SuccessProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_ok_subproc)

    sim_testnet_live = client.post(
        "/v1/checkout/simulate",
        json={"checkout_id": chk["checkoutId"], "event": "SUCCESS"},
    )
    assert sim_testnet_live.status_code == 200
    assert sim_testnet_live.json()["nearTxHash"] == "9xLiveTestnetTxHash111"

    approve_testnet_live = client.post("/v1/admin/approve-policy", json={"name": "peni.uni"})
    assert approve_testnet_live.status_code == 200
    assert approve_testnet_live.json()["tx_hash"] == "9xLiveTestnetTxHash111"

    # 4. When ALLOW_DEMO_ENDPOINTS=False (or on Mainnet), /v1/checkout/simulate is blocked with 403
    monkeypatch.setattr(settings, "ALLOW_DEMO_ENDPOINTS", False)
    sim_blocked = client.post(
        "/v1/checkout/simulate",
        json={"checkout_id": chk["checkoutId"], "event": "SUCCESS"},
    )
    assert sim_blocked.status_code == 403
    assert sim_blocked.json()["detail"]["error"] == "SIMULATION_DISABLED_IN_LIVE_MODE"

    # 5. Admin endpoints require X-Admin-Token when ADMIN_API_TOKEN is set or ALLOW_DEMO_ENDPOINTS=False
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "secret-admin-tok-123")
    unauth_admin = client.post("/v1/admin/approve-policy", json={"name": "peni.uni"})
    assert unauth_admin.status_code == 403
    assert unauth_admin.json()["detail"]["error"] == "ADMIN_UNAUTHORIZED"

    auth_admin = client.post(
        "/v1/admin/approve-policy",
        json={"name": "peni.uni"},
        headers={"X-Admin-Token": "secret-admin-tok-123"},
    )
    assert auth_admin.status_code == 200

    # 6. NEAR RPC outage propagates as HTTP 503 instead of 404 NAME_NOT_FOUND
    async def broken_rpc(*args, **kwargs):
        raise NearRpcError("RPC connection timed out")

    monkeypatch.setattr("near_client.NearContractClient.call_view_method", broken_rpc)
    rpc_fail_resolve = client.get("/v1/resolve", params={"name": "peni.uni", "chain": "eip155:1"})
    assert rpc_fail_resolve.status_code == 503
    assert rpc_fail_resolve.json()["detail"]["error"] == "NEAR_RPC_UNAVAILABLE"


def test_near_ft_transfer_call_finality_msg_binding_net_amount_and_concurrency_guard(monkeypatch):
    chk = client.post(
        "/v1/checkout/session",
        json={"name": "paidnear.uni", "owner": "alice.testnet", "chain": "near", "token": "USDC"},
    ).json()
    chk_id = chk["checkoutId"]

    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", False)
    monkeypatch.setattr(settings, "ADMIN_PRIVATE_KEY", "ed25519:fakekey")

    def make_rpc_tx_response(
        final_status: str = "FINAL",
        method_name: str = "ft_transfer_call",
        msg_str: str | None = '{"name":"paidnear.uni","owner":"alice.testnet"}',
        net_used_str: str = "5000000",
    ) -> dict:
        call_payload = {
            "receiver_id": settings.REGISTRY_CONTRACT_ID,
            "amount": "5000000",
        }
        if msg_str is not None:
            call_payload["msg"] = msg_str
        call_args_b64 = base64.b64encode(json.dumps(call_payload).encode("utf-8")).decode("utf-8")
        sv_b64 = base64.b64encode(json.dumps(net_used_str).encode("utf-8")).decode("utf-8")
        return {
            "result": {
                "final_execution_status": final_status,
                "status": {"SuccessValue": sv_b64},
                "transaction": {
                    "signer_id": "alice.testnet",
                    "receiver_id": chk["tokenContract"],
                    "actions": [
                        {
                            "FunctionCall": {
                                "method_name": method_name,
                                "args": call_args_b64,
                            }
                        }
                    ],
                },
                "receipts_outcome": [{"outcome": {"status": {"SuccessValue": ""}}}],
            }
        }

    captured_rpc_payloads = []
    current_mock_rpc_resp = make_rpc_tx_response()

    class DummyRpcResp:
        def raise_for_status(self):
            pass

        def json(self):
            return current_mock_rpc_resp

    async def fake_http_post(self, url, json=None, **kwargs):
        captured_rpc_payloads.append(json)
        return DummyRpcResp()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_http_post)

    # 1. Both EXECUTED_OPTIMISTIC and EXECUTED are rejected; strictly requires FINAL
    for non_final in ("EXECUTED_OPTIMISTIC", "EXECUTED"):
        current_mock_rpc_resp = make_rpc_tx_response(final_status=non_final)
        r_non_final = client.post(
            "/v1/checkout/submit",
            json={"checkout_id": chk_id, "tx_hash": "NearTxHashValidLength11111"},
        )
        assert r_non_final.status_code == 400
        assert "not reached FINAL execution" in r_non_final.json()["detail"]["error"]
        assert captured_rpc_payloads[-1]["params"]["wait_until"] == "FINAL"

    # 2. Ordinary ft_transfer (without ft_transfer_call callback & msg binding) is rejected
    current_mock_rpc_resp = make_rpc_tx_response(
        final_status="FINAL", method_name="ft_transfer", msg_str=None
    )
    r_plain_transfer = client.post(
        "/v1/checkout/submit",
        json={"checkout_id": chk_id, "tx_hash": "NearTxHashValidLength11111"},
    )
    assert r_plain_transfer.status_code == 400
    assert "ft_transfer_call" in r_plain_transfer.json()["detail"]["error"]

    # 3. Wrong msg domain name in ft_transfer_call rejected
    current_mock_rpc_resp = make_rpc_tx_response(
        final_status="FINAL", msg_str='{"name":"wrongdomain.uni","owner":"alice.testnet"}'
    )
    r_bad_msg = client.post(
        "/v1/checkout/submit",
        json={"checkout_id": chk_id, "tx_hash": "NearTxHashValidLength11111"},
    )
    assert r_bad_msg.status_code == 400
    assert "does not match checkout session" in r_bad_msg.json()["detail"]["error"]

    # 4. Refunded ft_transfer_call (net_used < expected_amount) rejected
    current_mock_rpc_resp = make_rpc_tx_response(
        final_status="FINAL", net_used_str="0"
    )
    r_refunded = client.post(
        "/v1/checkout/submit",
        json={"checkout_id": chk_id, "tx_hash": "NearTxHashValidLength11111"},
    )
    assert r_refunded.status_code == 400
    assert "refunded tokens" in r_refunded.json()["detail"]["error"]

    # 5. Valid FINAL ft_transfer_call verifies on-chain ownership via get_record() without calling execute_claim_for() a second time
    current_mock_rpc_resp = make_rpc_tx_response(
        final_status="FINAL",
        msg_str='{"name":"paidnear.uni","owner":"alice.testnet"}',
        net_used_str="5000000",
    )

    async def fake_get_record(name: str):
        return {"owner": "alice.testnet", "generation": 1, "chains": {}, "policy": None, "pending": None}

    async def fail_if_claim_for_called(*args, **kwargs):
        raise AssertionError("execute_claim_for must not be called after ft_transfer_call already claimed on-chain")

    monkeypatch.setattr("near_client.NearContractClient.get_record", lambda self, name: fake_get_record(name))
    monkeypatch.setattr("near_client.NearContractClient.execute_claim_for", fail_if_claim_for_called)

    r_ok = client.post(
        "/v1/checkout/submit",
        json={"checkout_id": chk_id, "tx_hash": "NearTxHashValidLength11111"},
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "COMPLETED"
    assert r_ok.json()["nearTxHash"] == "NearTxHashValidLength11111"