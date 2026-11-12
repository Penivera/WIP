"""
Universal Name (.uni) — Multichain Stablecoin Checkout Adapter

Supports purchasing / registering domain names with stablecoins (USDC/USDT)
across all supported blockchains:
- Ethereum (eip155:1)
- Base (eip155:8453)
- Arbitrum (eip155:42161)
- Optimism (eip155:10)
- Polygon (eip155:137)
- BNB Smart Chain (eip155:56)
- Avalanche (eip155:43114)
- Solana (solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d)
- NEAR Protocol (near:testnet / near:mainnet)
"""

from dataclasses import dataclass
import hashlib
import logging
import time
from typing import Any
import uuid

from config import settings
from cs_manager import ChainSignaturesManager
from erc7930 import (
    SOLANA_MAINNET_CAIP2,
    canonicalize_chain_key,
)
from near_client import NearContractClient
from proofs import validate_and_normalize_name

logger = logging.getLogger(__name__)

# Standard 6-decimal micro-unit prices
PRICE_3_CHAR_MICRO = 50_000_000   # $50.00
PRICE_4_CHAR_MICRO = 20_000_000   # $20.00
PRICE_5_PLUS_MICRO = 5_000_000    # $5.00

# Canonical catalog of supported stablecoins per blockchain
SUPPORTED_STABLECOINS: dict[str, list[dict[str, Any]]] = {
    "eip155:1": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        },
    ],
    "eip155:8453": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        },
    ],
    "eip155:42161": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        },
    ],
    "eip155:10": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
        },
    ],
    "eip155:137": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
        },
    ],
    "eip155:56": [
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 18,
            "contractAddress": "0x55d398326f99059ff775485246999027b3197955",
        },
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 18,
            "contractAddress": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        },
    ],
    "eip155:43114": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",
        },
    ],
    SOLANA_MAINNET_CAIP2: [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        },
    ],
    "near:testnet": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e3613760",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "usdt.fakes.testnet",
        },
    ],
    "near:mainnet": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e3613760",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "dac17f958d2ee523a2206206994597c13d831ec7",
        },
    ],
}

FRIENDLY_CHAIN_NAMES: dict[str, str] = {
    "eip155:1": "Ethereum",
    "eip155:8453": "Base",
    "eip155:42161": "Arbitrum One",
    "eip155:10": "Optimism",
    "eip155:137": "Polygon",
    "eip155:56": "BNB Smart Chain",
    "eip155:43114": "Avalanche C-Chain",
    SOLANA_MAINNET_CAIP2: "Solana",
    "near:testnet": "NEAR Testnet",
    "near:mainnet": "NEAR Mainnet",
}


def calculate_price_micro(name: str) -> tuple[int, float]:
    """
    Computes length-tiered micro-units and USD price.
    3 chars = $50 (50_000_000 micro-units)
    4 chars = $20 (20_000_000 micro-units)
    5+ chars = $5  (5_000_000 micro-units)
    """
    clean = name.strip().lower()
    if clean.endswith(".uni"):
        label = clean[:-4]
    else:
        label = clean

    length = len(label)
    if length == 3:
        micro = PRICE_3_CHAR_MICRO
        usd = 50.0
    elif length == 4:
        micro = PRICE_4_CHAR_MICRO
        usd = 20.0
    else:
        micro = PRICE_5_PLUS_MICRO
        usd = 5.0
    return micro, usd


class CheckoutAdapter:
    def __init__(self, near_client: NearContractClient, cs_manager: ChainSignaturesManager):
        self.near_client = near_client
        self.cs_manager = cs_manager
        self.sessions: dict[str, dict[str, Any]] = {}

    def get_supported_stables_catalog(self) -> list[dict[str, Any]]:
        """Returns the structured list of all supported chains and accepted stablecoins."""
        catalog = []
        for chain_id, tokens in SUPPORTED_STABLECOINS.items():
            catalog.append({
                "chainId": chain_id,
                "chainName": FRIENDLY_CHAIN_NAMES.get(chain_id, chain_id),
                "tokens": tokens,
            })
        return catalog

    def find_stablecoin(self, chain_id: str, token_query: str) -> dict[str, Any] | None:
        """Finds matching stablecoin metadata by symbol or contract address."""
        tokens = SUPPORTED_STABLECOINS.get(chain_id, [])
        q = token_query.strip().lower()
        for tok in tokens:
            if tok["symbol"].lower() == q or tok["contractAddress"].lower() == q:
                return tok
        return None

    async def get_pricing_and_availability(self, name: str) -> dict[str, Any]:
        """Returns pricing, availability status, and all supported payment channels."""
        clean_name = validate_and_normalize_name(name)
        micro_units, usd_price = calculate_price_micro(clean_name)

        # Check availability on contract
        record = await self.near_client.get_record(clean_name)
        available = record is None

        label = clean_name[:-4]
        return {
            "name": clean_name,
            "label": label,
            "length": len(label),
            "available": available,
            "priceUsd": usd_price,
            "priceMicroUnits": str(micro_units),
            "supportedChains": self.get_supported_stables_catalog(),
        }

    async def create_checkout_session(
        self,
        name: str,
        owner: str,
        chain: str,
        token: str,
        refund_to: str | None = None,
    ) -> dict[str, Any]:
        """
        Creates a new domain checkout session with a deposit address
        for stablecoin payment from any supported chain.
        """
        clean_name = validate_and_normalize_name(name)
        ns, ref, _ = canonicalize_chain_key(chain)
        norm_chain = f"{ns}:{ref}"

        if norm_chain not in SUPPORTED_STABLECOINS:
            raise ValueError(
                f"Unsupported payment chain '{norm_chain}'. Supported chains: {list(SUPPORTED_STABLECOINS.keys())}"
            )

        stable_meta = self.find_stablecoin(norm_chain, token)
        if not stable_meta:
            supported_syms = [t["symbol"] for t in SUPPORTED_STABLECOINS[norm_chain]]
            raise ValueError(
                f"Unsupported stablecoin '{token}' on {norm_chain}. Accepted stablecoins: {supported_syms}"
            )

        # Ensure name is available
        record = await self.near_client.get_record(clean_name)
        if record is not None:
            raise ValueError(f"Domain '{clean_name}' is already registered and not available.")

        micro_units, usd_price = calculate_price_micro(clean_name)
        decimals = stable_meta["decimals"]

        # Scale amount to token decimals (18 for BSC, 6 for others)
        if decimals == 18:
            amount_base = micro_units * 10**12
        else:
            amount_base = micro_units

        # Generate checkout session ID
        checkout_id = f"chk_{uuid.uuid4().hex[:16]}"
        now = int(time.time())
        expires_at = now + 3600  # 1 hour expiry

        # Derive deposit address for foreign chains, or contract address for NEAR
        deposit_memo = None
        if norm_chain.startswith("near:"):
            deposit_address = settings.REGISTRY_CONTRACT_ID
            deposit_memo = f'{{"name":"{clean_name}"}}'
            instructions = (
                f"Call ft_transfer_call on {stable_meta['contractAddress']} to "
                f"'{deposit_address}' with amount {amount_base} and msg '{deposit_memo}'."
            )
        else:
            # Deterministic Chain Signatures deposit address tied to this checkout
            derived = self.cs_manager.derive_deposit_address(f"checkout-{checkout_id}", norm_chain)
            deposit_address = derived["depositAddress"]
            instructions = (
                f"Send exactly {usd_price:.2f} {stable_meta['symbol']} to "
                f"{deposit_address} on {FRIENDLY_CHAIN_NAMES.get(norm_chain, norm_chain)}. "
                f"Domain {clean_name} will be claimed automatically for {owner} upon receipt."
            )

        session = {
            "checkoutId": checkout_id,
            "name": clean_name,
            "owner": owner,
            "chain": norm_chain,
            "chainName": FRIENDLY_CHAIN_NAMES.get(norm_chain, norm_chain),
            "token": stable_meta["symbol"],
            "tokenContract": stable_meta["contractAddress"],
            "tokenDecimals": decimals,
            "priceUsd": usd_price,
            "amount": str(amount_base),
            "amountFormatted": f"{usd_price:.2f} {stable_meta['symbol']}",
            "depositAddress": deposit_address,
            "depositMemo": deposit_memo,
            "refundTo": refund_to,
            "status": "PENDING_PAYMENT",  # PENDING_PAYMENT, PROCESSING, COMPLETED, FAILED, REFUNDED
            "paymentRef": f"{norm_chain}:{stable_meta['symbol']}:{checkout_id}",
            "nearTxHash": None,
            "instructions": instructions,
            "createdAt": now,
            "expiresAt": expires_at,
            "completedAt": None,
        }

        self.sessions[checkout_id] = session
        return session

    def get_checkout_session(self, checkout_id: str) -> dict[str, Any] | None:
        return self.sessions.get(checkout_id)

    async def submit_payment(
        self,
        checkout_id: str,
        tx_hash: str,
    ) -> dict[str, Any]:
        """
        Submits proof of payment on the foreign chain, executes
        claim_for on the NEAR contract via the relayer, and finalizes registration.
        """
        session = self.sessions.get(checkout_id)
        if not session:
            raise ValueError("Checkout session not found.")

        if session["status"] == "COMPLETED":
            return session

        session["status"] = "PROCESSING"
        session["txHash"] = tx_hash
        payment_ref = f"{session['chain']}:{session['token']}:{tx_hash}"
        session["paymentRef"] = payment_ref

        # Settle domain claim on the NEAR registry contract
        result = await self.near_client.execute_claim_for(
            name=session["name"],
            owner=session["owner"],
            payment_ref=payment_ref,
        )

        session["status"] = "COMPLETED"
        session["nearTxHash"] = result.get("tx_hash")
        session["completedAt"] = int(time.time())
        session["receipt"] = result
        return session

    async def simulate_payment(
        self,
        checkout_id: str,
        event: str = "SUCCESS",
    ) -> dict[str, Any]:
        """Simulates checkout payment completion for demo or test environments."""
        session = self.sessions.get(checkout_id)
        if not session:
            raise ValueError("Checkout session not found.")

        if event == "SUCCESS":
            fake_tx_hash = f"0xsim_{hashlib.sha256(checkout_id.encode()).hexdigest()[:32]}"
            return await self.submit_payment(checkout_id, fake_tx_hash)
        else:
            session["status"] = event
            session["updatedAt"] = int(time.time())
            return session
