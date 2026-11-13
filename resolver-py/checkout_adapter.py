"""
Universal Name (.uni) — Multichain Stablecoin Checkout Adapter
Supports purchasing / registering domain names with stablecoins (USDC/USDT) across all supported blockchains:
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

import base64
import hashlib
import json
import logging
import time
from typing import Any
import httpx

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
            "name": "USD Coin (Testnet)",
            "decimals": 6,
            "contractAddress": "3e2210e1184b45b64c8a434c0a7e7b23cc04ea7eb7a6c3c32520d03d4afcb8af",
        },
        {
            "symbol": "USDT",
            "name": "Fake Test Token (usdt.fakes.testnet)",
            "decimals": 6,
            "contractAddress": "usdt.fakes.testnet",
        },
    ],
    "near:mainnet": [
        {
            "symbol": "USDC",
            "name": "USD Coin",
            "decimals": 6,
            "contractAddress": "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1",
        },
        {
            "symbol": "USDT",
            "name": "Tether USD",
            "decimals": 6,
            "contractAddress": "usdt.tether-token.near",
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


def _parse_ft_on_transfer_msg(raw_msg: str, default_owner: str) -> tuple[str, str]:
    """
    Mirrors FtOnTransferMsg::parse in contracts/registry/src/lib.rs.
    Returns (normalized_name, target_owner).
    """
    trimmed = raw_msg.strip()
    if not trimmed:
        raise ValueError("Empty ft_transfer_call msg")
    if trimmed.startswith("{"):
        data = json.loads(trimmed)
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON msg object")
        if "claim" in data and isinstance(data["claim"], dict):
            inner = data["claim"]
        elif "register" in data and isinstance(data["register"], dict):
            inner = data["register"]
        elif "name" in data:
            inner = data
        else:
            raise ValueError("JSON msg missing 'name', 'claim', or 'register' key")
        raw_name = str(inner.get("name") or "")
        owner = str(inner.get("owner") or default_owner).strip().lower()
        return validate_and_normalize_name(raw_name), owner

    return validate_and_normalize_name(trimmed), default_owner.strip().lower()


class CheckoutAdapter:
    def __init__(self, near_client: NearContractClient, cs_manager: ChainSignaturesManager):
        self.near_client = near_client
        self.cs_manager = cs_manager
        self.sessions: dict[str, dict[str, Any]] = {}
        self.used_tx_hashes: set[str] = set()
        self.in_flight_tx_hashes: set[str] = set()
        self.in_flight_sessions: set[str] = set()

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
        for tok in SUPPORTED_STABLECOINS.get(chain_id, []):
            if tok["symbol"].lower() == q or tok["contractAddress"].lower() == q:
                res = dict(tok)
                res["isStable"] = True
                res["priceUsd"] = 1.0
                return res
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

        record = await self.near_client.get_record(clean_name)
        available = record is None

        label = clean_name[:-4]
        usdc_contract = (
            "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
            if settings.NEAR_NETWORK == "mainnet"
            else "3e2210e1184b45b64c8a434c0a7e7b23cc04ea7eb7a6c3c32520d03d4afcb8af"
        )
        return {
            "name": clean_name,
            "label": label,
            "length": len(label),
            "available": available,
            "priceUsd": usd_price,
            "priceMicroUnits": str(micro_units),
            "settlementAsset": f"USDC on NEAR Protocol (nep141:{usdc_contract})",
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

        record = await self.near_client.get_record(clean_name)
        if record is not None:
            raise ValueError(f"Domain '{clean_name}' is already registered and not available.")

        micro_units, usd_price = calculate_price_micro(clean_name)
        decimals = asset_meta["decimals"]
        is_stable = asset_meta["isStable"]

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

        clean_owner = owner.strip().lower()
        chk_preimage = f"{clean_name}:{clean_owner}:{norm_chain}:{asset_meta['symbol']}".encode("utf-8")
        checkout_id = f"chk_{hashlib.sha256(chk_preimage).hexdigest()[:16]}"
        now = int(time.time())
        expires_at = now + 3600

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
            derived = self.cs_manager.derive_deposit_address(f"checkout:{clean_name}:{clean_owner}", norm_chain)
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
            "status": "PENDING_PAYMENT",
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

    async def verify_payment_on_chain(self, session: dict[str, Any], tx_hash: str) -> None:
        """
        Verifies on-chain payment finality (strictly final_execution_status == 'FINAL'),
        token contract, recipient, ft_transfer_call msg binding, and net retained amount on NEAR.
        Accepts only NEP-141 ft_transfer_call (rejecting plain ft_transfer).
        Rejects non-NEAR chains in live mode because automated cross-chain MPC sweeping is not enabled.
        """
        chain = session["chain"]
        expected_amount = int(session["amount"])
        expected_recipient = session["depositAddress"]
        token_contract = session["tokenContract"]
        expected_name = session["name"]
        expected_owner = session["owner"].strip().lower()

        if not chain.startswith("near:"):
            raise ValueError(
                f"PAYMENT_UNVERIFIED: Live cross-chain checkout settlement is not enabled for {chain} "
                "because automated MPC sweeping is not active. Use direct NEAR NEP-141 payment."
            )

        payload = {
            "jsonrpc": "2.0",
            "id": "unam-verify-tx",
            "method": "EXPERIMENTAL_tx_status",
            "params": {
                "tx_hash": tx_hash,
                "sender_account_id": session["owner"],
                "wait_until": "FINAL",
            },
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.NEAR_RPC_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("result") or {}
        final_exec = result.get("final_execution_status")
        if final_exec != "FINAL":
            raise ValueError(
                f"PAYMENT_UNVERIFIED: NEAR transaction has not reached FINAL execution (status={final_exec})."
            )

        status = result.get("status") or {}
        if "SuccessValue" not in status:
            raise ValueError("PAYMENT_UNVERIFIED: NEAR transaction did not succeed on-chain.")

        for outcome in result.get("receipts_outcome") or []:
            ostatus = (outcome.get("outcome") or {}).get("status") or {}
            if "Failure" in ostatus:
                raise ValueError("PAYMENT_UNVERIFIED: A receipt in the NEAR transaction failed.")

        tx_obj = result.get("transaction") or {}
        tx_signer = str(tx_obj.get("signer_id") or session["owner"]).strip().lower()
        if tx_obj.get("receiver_id") != token_contract:
            raise ValueError(
                f"PAYMENT_UNVERIFIED: Expected token contract '{token_contract}', got '{tx_obj.get('receiver_id')}'."
            )

        actions = tx_obj.get("actions") or []
        matched_transfer_call = False
        for act in actions:
            fn = act.get("FunctionCall")
            if not fn or fn.get("method_name") != "ft_transfer_call":
                continue
            raw_args = base64.b64decode(fn.get("args", "")).decode("utf-8")
            parsed_args = json.loads(raw_args)
            if (
                parsed_args.get("receiver_id") == expected_recipient
                and int(parsed_args.get("amount", "0")) >= expected_amount
            ):
                raw_msg = str(parsed_args.get("msg") or "")
                try:
                    msg_name, msg_owner = _parse_ft_on_transfer_msg(raw_msg, tx_signer)
                except Exception as exc:
                    raise ValueError(
                        f"PAYMENT_UNVERIFIED: Invalid ft_transfer_call msg '{raw_msg}': {exc}"
                    ) from exc
                if msg_name != expected_name or msg_owner != expected_owner:
                    raise ValueError(
                        f"PAYMENT_UNVERIFIED: ft_transfer_call msg target ({msg_name}, {msg_owner}) "
                        f"does not match checkout session ({expected_name}, {expected_owner})."
                    )
                matched_transfer_call = True
                break

        if not matched_transfer_call:
            raise ValueError(
                "PAYMENT_UNVERIFIED: Valid NEP-141 ft_transfer_call to checkout depositAddress with required amount and msg not found."
            )

        raw_sv = status.get("SuccessValue") or ""
        try:
            decoded_sv = base64.b64decode(raw_sv).decode("utf-8").strip()
            net_used = int(json.loads(decoded_sv))
        except Exception as exc:
            raise ValueError(
                "PAYMENT_UNVERIFIED: Could not decode net used amount from ft_transfer_call SuccessValue."
            ) from exc
        if net_used < expected_amount:
            raise ValueError(
                f"PAYMENT_UNVERIFIED: ft_transfer_call refunded tokens; net used {net_used} < required {expected_amount}."
            )

    async def submit_payment(
        self,
        checkout_id: str,
        tx_hash: str,
    ) -> dict[str, Any]:
        """
        Reserves both tx_hash and checkout_id before any awaited call to prevent concurrency races.
        In live mode (USE_MOCK_CONTRACT=false), verifies the FINAL NEP-141 ft_transfer_call transaction
        and confirms on-chain ownership via get_record() without calling execute_claim_for() a second time
        (since ft_on_transfer already claims atomically on-chain).
        """
        session = self.sessions.get(checkout_id)
        if not session:
            raise ValueError("Checkout session not found.")

        if session["status"] == "COMPLETED":
            return session

        clean_tx = tx_hash.strip()
        if not clean_tx or clean_tx.startswith("0xsim_") or len(clean_tx) < 16:
            raise ValueError("PAYMENT_UNVERIFIED: Invalid or simulated transaction hash.")

        tx_key = f"{session['chain']}:{clean_tx.lower()}"
        if tx_key in self.used_tx_hashes:
            raise ValueError("PAYMENT_REPLAYED: Transaction hash has already been used.")
        if tx_key in self.in_flight_tx_hashes:
            raise ValueError("PAYMENT_PROCESSING: Transaction hash is currently being processed.")
        if checkout_id in self.in_flight_sessions:
            raise ValueError("CHECKOUT_PROCESSING: Checkout session is currently being processed.")

        # Reserve both tx_key and checkout_id synchronously BEFORE the first await
        self.in_flight_tx_hashes.add(tx_key)
        self.in_flight_sessions.add(checkout_id)
        session["status"] = "PROCESSING"

        try:
            if not settings.USE_MOCK_CONTRACT:
                await self.verify_payment_on_chain(session, clean_tx)
                record = await self.near_client.get_record(session["name"])
                expected_owner = session["owner"].strip().lower()
                actual_owner = str((record or {}).get("owner") or "").strip().lower()
                if not record or actual_owner != expected_owner:
                    raise ValueError(
                        f"PAYMENT_UNVERIFIED: On-chain record for '{session['name']}' is not owned by '{session['owner']}' after ft_transfer_call."
                    )
                session["txHash"] = clean_tx
                payment_ref = f"{session['chain']}:{session['token']}:{clean_tx}"
                session["paymentRef"] = payment_ref
                result = {
                    "status": "SUCCESS",
                    "tx_hash": clean_tx,
                    "name": session["name"],
                    "owner": session["owner"],
                    "payment_ref": payment_ref,
                }
            else:
                session["txHash"] = clean_tx
                payment_ref = f"{session['chain']}:{session['token']}:{clean_tx}"
                session["paymentRef"] = payment_ref
                result = await self.near_client.execute_claim_for(
                    name=session["name"],
                    owner=session["owner"],
                    payment_ref=payment_ref,
                )
        except Exception:
            session["status"] = "PENDING_PAYMENT"
            raise
        finally:
            self.in_flight_tx_hashes.discard(tx_key)
            self.in_flight_sessions.discard(checkout_id)

        self.used_tx_hashes.add(tx_key)
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
        """
        Simulates checkout payment completion for controlled Testnet demo or mock environments.
        Strictly disabled on Mainnet or when ALLOW_DEMO_ENDPOINTS=false and USE_MOCK_CONTRACT=false.
        """
        if settings.NEAR_NETWORK == "mainnet" or (
            not settings.USE_MOCK_CONTRACT and not settings.ALLOW_DEMO_ENDPOINTS
        ):
            raise PermissionError(
                "SIMULATION_DISABLED_IN_LIVE_MODE: /v1/checkout/simulate is disabled outside controlled testnet/mock demo mode."
            )

        session = self.sessions.get(checkout_id)
        if not session:
            raise ValueError("Checkout session not found.")

        if event == "SUCCESS":
            fake_tx_hash = f"0xsim_{hashlib.sha256(checkout_id.encode()).hexdigest()[:32]}"
            payment_ref = f"sim:{session['chain']}:{session['token']}:{checkout_id}"
            result = await self.near_client.execute_claim_for(
                name=session["name"],
                owner=session["owner"],
                payment_ref=payment_ref,
            )
            session["status"] = "COMPLETED"
            session["txHash"] = fake_tx_hash
            session["paymentRef"] = payment_ref
            session["nearTxHash"] = result.get("tx_hash")
            session["completedAt"] = int(time.time())
            session["receipt"] = result
            return session

        session["status"] = event
        session["updatedAt"] = int(time.time())
        return session