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
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "decimals": 18,
            "contractAddress": "0x6b175474e89094c44da98b954eedeac495271d0f",
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
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "decimals": 18,
            "contractAddress": "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",
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
        {
            "symbol": "DAI",
            "name": "Dai Stablecoin",
            "decimals": 18,
            "contractAddress": "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",
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

# Cross-chain assets that can be routed via Intent to settle on NEAR in supported stablecoins
INTENT_ROUTABLE_ASSETS: dict[str, list[dict[str, Any]]] = {
    "eip155:1": [
        {"symbol": "ETH", "name": "Ethereum", "decimals": 18, "contractAddress": "native", "priceUsd": 2050.0},
    ],
    "eip155:8453": [
        {"symbol": "ETH", "name": "Ethereum", "decimals": 18, "contractAddress": "native", "priceUsd": 2050.0},
    ],
    "eip155:42161": [
        {"symbol": "ETH", "name": "Ethereum", "decimals": 18, "contractAddress": "native", "priceUsd": 2050.0},
    ],
    "eip155:10": [
        {"symbol": "ETH", "name": "Ethereum", "decimals": 18, "contractAddress": "native", "priceUsd": 2050.0},
    ],
    "eip155:137": [
        {"symbol": "POL", "name": "Polygon Ecosystem Token", "decimals": 18, "contractAddress": "native", "priceUsd": 0.45},
    ],
    "eip155:56": [
        {"symbol": "BNB", "name": "BNB", "decimals": 18, "contractAddress": "native", "priceUsd": 620.0},
    ],
    "eip155:43114": [
        {"symbol": "AVAX", "name": "Avalanche", "decimals": 18, "contractAddress": "native", "priceUsd": 32.0},
    ],
    SOLANA_MAINNET_CAIP2: [
        {"symbol": "SOL", "name": "Solana", "decimals": 9, "contractAddress": "native", "priceUsd": 180.0},
    ],
    "near:testnet": [
        {"symbol": "NEAR", "name": "NEAR", "decimals": 24, "contractAddress": "wrap.testnet", "priceUsd": 2.80},
    ],
    "near:mainnet": [
        {"symbol": "NEAR", "name": "NEAR", "decimals": 24, "contractAddress": "wrap.near", "priceUsd": 2.80},
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

    def find_payment_asset(self, chain_id: str, token_query: str) -> dict[str, Any] | None:
        """Finds matching stablecoin or intent-routable asset metadata by symbol or address."""
        q = token_query.strip().lower()
        # 1. Check supported stablecoins
        for tok in SUPPORTED_STABLECOINS.get(chain_id, []):
            if tok["symbol"].lower() == q or tok["contractAddress"].lower() == q:
                res = dict(tok)
                res["isStable"] = True
                res["priceUsd"] = 1.0
                return res

        # 2. Check intent-routable assets
        for tok in INTENT_ROUTABLE_ASSETS.get(chain_id, []):
            if tok["symbol"].lower() == q or tok["contractAddress"].lower() == q:
                res = dict(tok)
                res["isStable"] = False
                return res

        return None

    def find_stablecoin(self, chain_id: str, token_query: str) -> dict[str, Any] | None:
        """Finds matching stablecoin metadata by symbol or contract address."""
        asset = self.find_payment_asset(chain_id, token_query)
        if asset and asset.get("isStable"):
            return asset
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
            "settlementAsset": "USDC on NEAR Protocol (nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e3613760)",
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
        for stablecoin or intent-routable asset payment from any supported chain.
        """
        clean_name = validate_and_normalize_name(name)
        ns, ref, _ = canonicalize_chain_key(chain)
        norm_chain = f"{ns}:{ref}"

        if norm_chain not in SUPPORTED_STABLECOINS and norm_chain not in INTENT_ROUTABLE_ASSETS:
            raise ValueError(
                f"Unsupported payment chain '{norm_chain}'. Supported chains: {list(SUPPORTED_STABLECOINS.keys())}"
            )

        asset_meta = self.find_payment_asset(norm_chain, token)
        if not asset_meta:
            supported_syms = [t["symbol"] for t in SUPPORTED_STABLECOINS.get(norm_chain, [])]
            intent_syms = [t["symbol"] for t in INTENT_ROUTABLE_ASSETS.get(norm_chain, [])]
            raise ValueError(
                f"Unsupported payment asset '{token}' on {norm_chain}. "
                f"Accepted stablecoins: {supported_syms}; or intent-routable assets: {intent_syms}"
            )

        # Ensure name is available
        record = await self.near_client.get_record(clean_name)
        if record is not None:
            raise ValueError(f"Domain '{clean_name}' is already registered and not available.")

        micro_units, usd_price = calculate_price_micro(clean_name)
        decimals = asset_meta["decimals"]
        is_stable = asset_meta["isStable"]

        # Slippage & cross-chain execution buffer:
        # - Native NEAR stables: 0.0 (direct 1:1 settlement, no swap slippage)
        # - Foreign cross-chain stables: $1.00 (absorbs bridge fees & solver execution variance)
        # - Volatile intent-swap assets: $1.50 (absorbs DEX swap slippage & market drift)
        if norm_chain.startswith("near:") and is_stable:
            buffer_usd = 0.0
        elif is_stable:
            buffer_usd = 1.0
        else:
            buffer_usd = 1.5

        total_billed_usd = round(usd_price + buffer_usd, 2)

        if is_stable:
            settlement_type = "DIRECT_STABLECOIN"
            billed_micro = int(round(total_billed_usd * 1_000_000))
            amount_base = billed_micro if decimals == 6 else int(billed_micro * 10 ** (decimals - 6))
            amount_formatted = f"{total_billed_usd:.2f} {asset_meta['symbol']}"
        else:
            settlement_type = "INTENT_SWAP_SETTLEMENT"
            token_price = asset_meta.get("priceUsd", 1.0)
            token_qty = round(total_billed_usd / token_price, 6)
            amount_base = int(round(token_qty * 10**decimals))
            amount_formatted = f"{token_qty} {asset_meta['symbol']}"

        # Generate deterministic checkout session ID
        chk_seed = f"{clean_name}:{owner}:{norm_chain}:{asset_meta['symbol']}"
        checkout_id = f"chk_{hashlib.sha256(chk_seed.encode()).hexdigest()[:16]}"
        now = int(time.time())
        expires_at = now + 3600  # 1 hour expiry

        # Derive deposit address for foreign chains, or contract address for NEAR
        deposit_memo = None
        if norm_chain.startswith("near:"):
            deposit_address = settings.REGISTRY_CONTRACT_ID
            deposit_memo = f'{{"name":"{clean_name}"}}'
            if is_stable:
                instructions = (
                    f"Call ft_transfer_call on {asset_meta['contractAddress']} to "
                    f"'{deposit_address}' with amount {amount_base} and msg '{deposit_memo}'."
                )
            else:
                instructions = (
                    f"Transfer {amount_formatted} to '{deposit_address}'. "
                    f"The Intent Router will swap to USDC on NEAR and register {clean_name} for {owner}."
                )
        else:
            # Deterministically derive the Chain Signatures deposit address bound to (name, chain)
            derived = self.cs_manager.derive_deposit_address(clean_name, norm_chain)
            deposit_address = derived["depositAddress"]
            if is_stable:
                instructions = (
                    f"Send exactly {amount_formatted} to "
                    f"{deposit_address} on {FRIENDLY_CHAIN_NAMES.get(norm_chain, norm_chain)}. "
                    f"Includes ${buffer_usd:.2f} buffer for cross-chain execution; any surplus is refunded. "
                    f"Domain {clean_name} will be claimed automatically for {owner} upon receipt."
                )
            else:
                instructions = (
                    f"Send {amount_formatted} (~${total_billed_usd:.2f} USD) to "
                    f"{deposit_address} on {FRIENDLY_CHAIN_NAMES.get(norm_chain, norm_chain)}. "
                    f"Includes ${buffer_usd:.2f} slippage buffer. "
                    f"The Intent Router will swap and settle {usd_price:.2f} USDC on NEAR to claim {clean_name} for {owner}."
                )

        session = {
            "checkoutId": checkout_id,
            "name": clean_name,
            "owner": owner,
            "chain": norm_chain,
            "chainName": FRIENDLY_CHAIN_NAMES.get(norm_chain, norm_chain),
            "token": asset_meta["symbol"],
            "tokenContract": asset_meta["contractAddress"],
            "tokenDecimals": decimals,
            "isStable": is_stable,
            "settlementType": settlement_type,
            "settlementAsset": "USDC (NEAR Protocol)",
            "settlementAmountMicro": str(micro_units),
            "priceUsd": usd_price,
            "slippageBufferUsd": buffer_usd,
            "totalBilledUsd": total_billed_usd,
            "amount": str(amount_base),
            "amountFormatted": amount_formatted,
            "depositAddress": deposit_address,
            "depositMemo": deposit_memo,
            "refundTo": refund_to,
            "status": "PENDING_PAYMENT",  # PENDING_PAYMENT, PROCESSING, COMPLETED, FAILED, REFUNDED
            "paymentRef": f"{norm_chain}:{asset_meta['symbol']}:{checkout_id}",
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
