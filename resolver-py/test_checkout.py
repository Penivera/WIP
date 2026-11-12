from fastapi.testclient import TestClient
import pytest

from config import settings
from erc7930 import SOLANA_MAINNET_CAIP2
from main import app, checkout_adapter
from near_client import MOCK_CONTRACT_RECORDS

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_mock_state(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_CONTRACT", True)
    checkout_adapter.sessions.clear()


def test_checkout_chains_catalog():
    resp = client.get("/v1/checkout/chains")
    assert resp.status_code == 200
    catalog = resp.json()

    chain_ids = [entry["chainId"] for entry in catalog]
    expected_chains = [
        "eip155:1",      # Ethereum
        "eip155:8453",   # Base
        "eip155:42161",  # Arbitrum
        "eip155:10",     # Optimism
        "eip155:137",    # Polygon
        "eip155:56",     # BSC
        "eip155:43114",  # Avalanche
        SOLANA_MAINNET_CAIP2,  # Solana
        "near:testnet",  # NEAR Testnet
        "near:mainnet",  # NEAR Mainnet
    ]
    for exp in expected_chains:
        assert exp in chain_ids, f"Expected {exp} in supported chains catalog"

    # Verify each chain has at least one stablecoin (USDC or USDT)
    for entry in catalog:
        symbols = [t["symbol"] for t in entry["tokens"]]
        assert any(s in symbols for s in ["USDC", "USDT"]), f"No stables found on {entry['chainId']}"


def test_pricing_and_availability_tiered():
    # 3-char name: $50
    resp_3 = client.get("/v1/checkout/pricing?name=cat.uni")
    assert resp_3.status_code == 200
    data_3 = resp_3.json()
    assert data_3["length"] == 3
    assert data_3["priceUsd"] == 50.0
    assert data_3["priceMicroUnits"] == "50000000"

    # 4-char name: $20
    resp_4 = client.get("/v1/checkout/pricing?name=moon.uni")
    assert resp_4.status_code == 200
    data_4 = resp_4.json()
    assert data_4["length"] == 4
    assert data_4["priceUsd"] == 20.0
    assert data_4["priceMicroUnits"] == "20000000"

    # 5+ char name: $5
    resp_5 = client.get("/v1/checkout/pricing?name=stellar.uni")
    assert resp_5.status_code == 200
    data_5 = resp_5.json()
    assert data_5["length"] == 7
    assert data_5["priceUsd"] == 5.0
    assert data_5["priceMicroUnits"] == "5000000"

    # Already claimed name: peni.uni
    resp_taken = client.get("/v1/checkout/pricing?name=peni.uni")
    assert resp_taken.status_code == 200
    assert resp_taken.json()["available"] is False


@pytest.mark.parametrize(
    "chain_key,expected_chain_id",
    [
        ("eip155:1", "eip155:1"),
        ("base", "eip155:8453"),
        ("eip155:42161", "eip155:42161"),
        ("eip155:10", "eip155:10"),
        ("polygon", "eip155:137"),
        ("eip155:56", "eip155:56"),
        ("avax", "eip155:43114"),
        ("solana", SOLANA_MAINNET_CAIP2),
        ("near", "near:testnet"),
    ],
)
def test_create_checkout_session_all_chains_usdc(chain_key: str, expected_chain_id: str):
    req_body = {
        "name": f"user-{chain_key.replace(':', '-')}.uni",
        "owner": "buyer.testnet",
        "chain": chain_key,
        "token": "USDC",
        "refund_to": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    }
    resp = client.post("/v1/checkout/session", json=req_body)
    assert resp.status_code == 200, f"Failed for {chain_key}: {resp.text}"
    session = resp.json()

    assert session["chain"] == expected_chain_id
    assert session["token"] == "USDC"
    assert session["status"] == "PENDING_PAYMENT"
    assert session["depositAddress"] is not None
    assert session["priceUsd"] == 5.0  # 5+ chars name costs $5
    assert "instructions" in session
    assert session["checkoutId"].startswith("chk_")


@pytest.mark.parametrize(
    "chain_key",
    ["eip155:1", "eip155:42161", "eip155:137", "solana", "near"],
)
def test_create_checkout_session_usdt(chain_key: str):
    req_body = {
        "name": f"tether-{chain_key.replace(':', '-')}.uni",
        "owner": "tetherbuyer.testnet",
        "chain": chain_key,
        "token": "USDT",
    }
    resp = client.post("/v1/checkout/session", json=req_body)
    assert resp.status_code == 200, f"Failed for USDT on {chain_key}: {resp.text}"
    session = resp.json()
    assert session["token"] == "USDT"
    assert session["status"] == "PENDING_PAYMENT"


def test_checkout_rejects_unsupported_tokens_and_chains():
    # Reject non-supported token (e.g. DOGE, SHIB)
    resp = client.post(
        "/v1/checkout/session",
        json={
            "name": "dogeholder.uni",
            "owner": "doge.testnet",
            "chain": "eip155:1",
            "token": "DOGE",
        },
    )
    assert resp.status_code == 400
    assert "Unsupported payment asset" in resp.json()["detail"]["message"]

    # Reject unsupported chain with unknown namespace
    resp_cosmo = client.post(
        "/v1/checkout/session",
        json={
            "name": "cosmonaut.uni",
            "owner": "cosmo.testnet",
            "chain": "cosmos:cosmoshub-4",
            "token": "USDC",
        },
    )
    assert resp_cosmo.status_code == 400
    assert "CHAIN_KEY_INVALID" in resp_cosmo.json()["detail"]["message"]

    # Reject unsupported chain with valid namespace but not in catalog
    resp_evm = client.post(
        "/v1/checkout/session",
        json={
            "name": "evmrunner.uni",
            "owner": "evm.testnet",
            "chain": "eip155:999999",
            "token": "USDC",
        },
    )
    assert resp_evm.status_code == 400
    assert "Unsupported payment chain" in resp_evm.json()["detail"]["message"]


def test_create_checkout_session_intent_routed_assets():
    # 1. Pay with ETH on Base
    resp_eth = client.post(
        "/v1/checkout/session",
        json={
            "name": "ethpayer.uni",
            "owner": "ethfan.testnet",
            "chain": "base",
            "token": "ETH",
        },
    )
    assert resp_eth.status_code == 200
    sess_eth = resp_eth.json()
    assert sess_eth["token"] == "ETH"
    assert sess_eth["isStable"] is False
    assert sess_eth["settlementType"] == "INTENT_SWAP_SETTLEMENT"
    assert "USDC (NEAR Protocol)" in sess_eth["settlementAsset"]
    assert "Intent Router will swap" in sess_eth["instructions"]

    # 2. Pay with SOL on Solana
    resp_sol = client.post(
        "/v1/checkout/session",
        json={
            "name": "solpayer.uni",
            "owner": "solfan.testnet",
            "chain": "solana",
            "token": "SOL",
        },
    )
    assert resp_sol.status_code == 200
    sess_sol = resp_sol.json()
    assert sess_sol["token"] == "SOL"
    assert sess_sol["isStable"] is False
    assert sess_sol["settlementType"] == "INTENT_SWAP_SETTLEMENT"

    # 3. Pay with DAI on Ethereum
    resp_dai = client.post(
        "/v1/checkout/session",
        json={
            "name": "daipayer.uni",
            "owner": "daifan.testnet",
            "chain": "eip155:1",
            "token": "DAI",
        },
    )
    assert resp_dai.status_code == 200
    sess_dai = resp_dai.json()
    assert sess_dai["token"] == "DAI"
    assert sess_dai["isStable"] is True


def test_checkout_rejects_taken_domain():
    resp = client.post(
        "/v1/checkout/session",
        json={
            "name": "peni.uni",
            "owner": "impostor.testnet",
            "chain": "base",
            "token": "USDC",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "NAME_TAKEN"


def test_checkout_settlement_and_lifecycle():
    domain = "checkout-demo.uni"
    owner = "demobuyer.testnet"

    # 1. Create checkout session on Base
    resp_create = client.post(
        "/v1/checkout/session",
        json={
            "name": domain,
            "owner": owner,
            "chain": "base",
            "token": "USDC",
        },
    )
    assert resp_create.status_code == 200
    session = resp_create.json()
    chk_id = session["checkoutId"]
    assert session["status"] == "PENDING_PAYMENT"

    # 2. Query session
    resp_get = client.get(f"/v1/checkout/session/{chk_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["status"] == "PENDING_PAYMENT"

    # 3. Submit foreign-chain payment transaction hash
    tx_hash = "0x8849f2b314a5160e1d89b027150c95ef2294125b2d713c77d6baad19c35b801a"
    resp_submit = client.post(
        "/v1/checkout/submit",
        json={
            "checkout_id": chk_id,
            "tx_hash": tx_hash,
        },
    )
    assert resp_submit.status_code == 200
    completed = resp_submit.json()
    assert completed["status"] == "COMPLETED"
    assert completed["nearTxHash"] is not None
    assert completed["completedAt"] is not None

    # 4. Verify session reflects COMPLETED on subsequent lookup
    resp_get_after = client.get(f"/v1/checkout/session/{chk_id}")
    assert resp_get_after.status_code == 200
    assert resp_get_after.json()["status"] == "COMPLETED"

    # 5. Domain is now registered
    resp_pricing = client.get(f"/v1/checkout/pricing?name={domain}")
    assert resp_pricing.status_code == 200
    assert resp_pricing.json()["available"] is False


def test_checkout_simulation_reject():
    domain = "sim-reject.uni"
    resp_create = client.post(
        "/v1/checkout/session",
        json={
            "name": domain,
            "owner": "someone.testnet",
            "chain": "solana",
            "token": "USDC",
        },
    )
    session = resp_create.json()
    chk_id = session["checkoutId"]

    resp_sim = client.post(
        "/v1/checkout/simulate",
        json={
            "checkout_id": chk_id,
            "event": "FAILED",
        },
    )
    assert resp_sim.status_code == 200
    assert resp_sim.json()["status"] == "FAILED"
