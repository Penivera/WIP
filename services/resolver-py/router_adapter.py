from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import time
import httpx

from config import settings
from cs_manager import ChainSignaturesManager
from erc7930 import (
    BIP122_MAINNET_CAIP2,
    SOLANA_MAINNET_CAIP2,
    address_string_to_bytes,
    canonicalize_chain_key,
    encode_erc7930,
)

logger = logging.getLogger(__name__)

STABLECOIN_SYMBOLS = {"USDC", "USDT", "DAI", "USDE"}

# Maps canonical CAIP-2 chains to 1Click's OpenAPI 'blockchain' enum values
CAIP2_TO_ONECLICK_CHAIN: dict[str, str] = {
    "eip155:1": "eth",
    "eip155:8453": "base",
    "eip155:42161": "arb",
    "eip155:137": "pol",
    "eip155:10": "op",
    "eip155:56": "bsc",
    "eip155:43114": "avax",
    "eip155:100": "gnosis",
    "eip155:80094": "bera",
    "eip155:534352": "scroll",
    SOLANA_MAINNET_CAIP2: "sol",
    "near:mainnet": "near",
    "near:testnet": "near",
    BIP122_MAINNET_CAIP2: "btc",
}

NATIVE_SYMBOL_BY_ONECLICK_CHAIN: dict[str, str] = {
    "eth": "ETH",
    "base": "ETH",
    "arb": "ETH",
    "pol": "POL",
    "op": "ETH",
    "bsc": "BNB",
    "avax": "AVAX",
    "gnosis": "xDAI",
    "bera": "BERA",
    "scroll": "ETH",
    "sol": "SOL",
    "near": "wNEAR",
    "btc": "BTC",
}

# Valid non-burn refund addresses per 1Click blockchain namespace (used when caller omits refund_to)
VALID_DEFAULT_REFUND_BY_CHAIN: dict[str, str] = {
    "eth": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "base": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "arb": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "pol": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "op": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "bsc": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "avax": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "gnosis": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "bera": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "scroll": "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
    "sol": "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH",
    "near": "5880ad2b362620fadf759cbceb1cd5737ce8c6ed7fb8e9942881e6731f9247dd",
    "btc": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
}

KNOWN_ERC20_CONTRACTS: dict[str, str] = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",  # Ethereum USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",  # Ethereum USDT
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",  # Base USDC
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC",  # Arbitrum USDC
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v": "USDC",  # Solana USDC
}

FALLBACK_SUPPORTED_TOKENS: list[dict] = [
    {"blockchain": "eth", "symbol": "ETH", "assetId": "nep141:eth.omft.near", "decimals": 18, "price": 2050.0},
    {"blockchain": "eth", "symbol": "USDC", "assetId": "nep141:eth-0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.omft.near", "decimals": 6, "price": 1.0},
    {"blockchain": "base", "symbol": "ETH", "assetId": "nep141:base.omft.near", "decimals": 18, "price": 2050.0},
    {"blockchain": "base", "symbol": "USDC", "assetId": "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near", "decimals": 6, "price": 1.0},
    {"blockchain": "arb", "symbol": "ETH", "assetId": "nep141:arb.omft.near", "decimals": 18, "price": 2050.0},
    {"blockchain": "arb", "symbol": "USDC", "assetId": "nep141:arb-0xaf88d065e77c8cc2239327c5edb3a432268e5831.omft.near", "decimals": 6, "price": 1.0},
    {"blockchain": "sol", "symbol": "SOL", "assetId": "nep141:sol.omft.near", "decimals": 9, "price": 180.0},
    {"blockchain": "sol", "symbol": "USDC", "assetId": "nep141:sol-5ce3bf3a31af18be40ba30f721101b4341690186.omft.near", "decimals": 6, "price": 1.0},
    {"blockchain": "near", "symbol": "wNEAR", "assetId": "nep141:wrap.near", "decimals": 24, "price": 2.8},
    {"blockchain": "btc", "symbol": "BTC", "assetId": "nep141:btc.omft.near", "decimals": 8, "price": 85000.0},
]


def compute_fee_bps(token_symbol: str, requested_bps: int = 0) -> dict:
    """
    Computes fee schedule from config:
    - Floor: 2 bps on stablecoins, 10 bps on other tokens
    - Cap: 500 bps
    - Split: 60% integrator / 40% Aurora
    - Also notes 1Click's 25 bps unauthenticated fee if ONECLICK_API_KEY is empty.
    """
    sym = token_symbol.upper()
    floor_bps = (
        settings.FEE_STABLECOIN_FLOOR_BPS
        if sym in STABLECOIN_SYMBOLS
        else settings.FEE_OTHER_TOKEN_FLOOR_BPS
    )
    total_bps = min(max(requested_bps, floor_bps), settings.FEE_MAX_CAP_BPS)
    integrator_bps = round(total_bps * (settings.FEE_INTEGRATOR_SHARE_PCT / 100.0), 2)
    aurora_bps = round(total_bps * (settings.FEE_AURORA_SHARE_PCT / 100.0), 2)
    oneclick_platform_bps = 20 if settings.ONECLICK_API_KEY else 25
    return {
        "totalFeeBps": total_bps,
        "integratorFeeBps": integrator_bps,
        "auroraFeeBps": aurora_bps,
        "oneclickPlatformBps": oneclick_platform_bps,
        "split": f"{settings.FEE_INTEGRATOR_SHARE_PCT}/{settings.FEE_AURORA_SHARE_PCT}",
    }


def extract_token_symbol(raw_token: str, oneclick_chain: str) -> str:
    clean = raw_token.strip()
    if clean.lower() in ("native", ""):
        return NATIVE_SYMBOL_BY_ONECLICK_CHAIN.get(oneclick_chain, "ETH")
    if "/" in clean:
        clean = clean.split("/")[-1]
    if ":" in clean:
        clean = clean.split(":")[-1]
    if clean.lower() in KNOWN_ERC20_CONTRACTS:
        return KNOWN_ERC20_CONTRACTS[clean.lower()]
    return clean.upper()


class RouterAdapter:
    def __init__(self, cs_manager: ChainSignaturesManager) -> None:
        self.cs_manager = cs_manager
        self._tokens_cache: list[dict] = []
        self._cache_ts: float = 0.0

    def _headers(self) -> dict[str, str]:
        """Builds request headers per 1Click OpenAPI spec (X-API-Key recommended)."""
        headers = {"Content-Type": "application/json"}
        if settings.ONECLICK_API_KEY:
            headers["X-API-Key"] = settings.ONECLICK_API_KEY
        return headers

    async def get_supported_tokens(self) -> list[dict]:
        if self._tokens_cache and (time.time() - self._cache_ts < 300):
            return self._tokens_cache
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{settings.ONECLICK_BASE_URL}/v0/tokens",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                self._tokens_cache = resp.json()
                self._cache_ts = time.time()
                return self._tokens_cache
        except Exception as exc:
            logger.warning(f"GET /v0/tokens fell back to local catalog: {exc}")
            return FALLBACK_SUPPORTED_TOKENS

    async def find_token_entry(self, caip2_chain: str, raw_token: str) -> dict | None:
        ns, ref, _ = canonicalize_chain_key(caip2_chain)
        norm_chain = f"{ns}:{ref}"
        oc_chain = CAIP2_TO_ONECLICK_CHAIN.get(norm_chain)
        if not oc_chain:
            return None

        target_sym = extract_token_symbol(raw_token, oc_chain)
        tokens = await self.get_supported_tokens()

        for item in tokens:
            item_chain = str(item.get("blockchain", "")).lower()
            item_sym = str(item.get("symbol", "")).upper()
            item_id = str(item.get("assetId", ""))
            item_contract = str(item.get("contractAddress") or "").lower()
            if item_chain == oc_chain:
                if (
                    item_sym == target_sym
                    or item_id.lower() == raw_token.strip().lower()
                    or (item_contract and item_contract == raw_token.strip().lower())
                ):
                    return item
                if oc_chain == "near" and target_sym in ("NEAR", "WNEAR") and item_sym == "WNEAR":
                    return item
        return None

    async def supported_list_summary(self) -> list[str]:
        tokens = await self.get_supported_tokens()
        return [f"{t.get('symbol')}@{t.get('blockchain')}" for t in tokens[:25]]

    async def resolve_route(
        self,
        name: str,
        source_chain: str,
        source_token: str,
        dest_chain: str,
        dest_asset_str: str,
        recipient_address: str,
        amount_in: str | None = None,
        refund_to: str | None = None,
        dry: bool = False,
        live_1click_quote: bool = True,
    ) -> dict:
        """
        1. Validates that (source_chain, source_token) -> (dest_chain, dest_asset) is supported.
        2. Derives the deterministic Chain Signatures fallback address.
        3. Calls POST /v0/quote on 1Click API (using FLEX_INPUT when amount_in is omitted,
           or EXACT_INPUT when amount_in is provided) to get a live depositAddress & quote.
        """
        if amount_in is not None:
            clean_amt = amount_in.strip()
            if not clean_amt.isdigit() or int(clean_amt) <= 0:
                raise ValueError(
                    "AMOUNT_INVALID: amount must be a positive integer string in smallest base units"
                )
            amount_in = clean_amt

        src_entry = await self.find_token_entry(source_chain, source_token)
        dst_entry = await self.find_token_entry(dest_chain, dest_asset_str)

        if not src_entry or not dst_entry:
            return {
                "error": "NO_ROUTE",
                "supported": await self.supported_list_summary(),
            }

        ns, ref, _ = canonicalize_chain_key(source_chain)
        norm_source_chain = f"{ns}:{ref}"
        oc_source_chain = CAIP2_TO_ONECLICK_CHAIN[norm_source_chain]

        src_sym = str(src_entry.get("symbol", "TOKEN")).upper()
        dst_sym = str(dst_entry.get("symbol", "USDC")).upper()
        src_decimals = int(src_entry.get("decimals", 18))
        dst_decimals = int(dst_entry.get("decimals", 6))
        src_price = Decimal(str(src_entry.get("price") or "1.0"))
        dst_price = Decimal(str(dst_entry.get("price") or "1.0"))

        fee_info = compute_fee_bps(src_sym)

        safe_src_price = max(src_price, Decimal("0.0001"))
        safe_dst_price = max(dst_price, Decimal("0.0001"))
        src_scale = Decimal(10) ** src_decimals
        dst_scale = Decimal(10) ** dst_decimals

        min_amount_int = max(1, int((Decimal("1.0") / safe_src_price) * src_scale))
        min_amount_base = str(min_amount_int)

        default_10usd_int = max(1, int((Decimal("10.0") / safe_src_price) * src_scale))
        default_10usd_base = str(default_10usd_int)
        effective_amount_base = amount_in if amount_in else default_10usd_base
        swap_type = "EXACT_INPUT" if amount_in else "FLEX_INPUT"

        input_usd = (Decimal(effective_amount_base) / src_scale) * safe_src_price
        fee_multiplier = Decimal(10000 - fee_info["totalFeeBps"]) / Decimal(10000)
        net_usd = input_usd * fee_multiplier
        est_out_tokens = net_usd / safe_dst_price
        est_receive_base = str(max(0, int(est_out_tokens * dst_scale)))
        est_receive_formatted = f"{est_out_tokens:.6f}".rstrip("0").rstrip(".") or "0"

        cs_derived = self.cs_manager.derive_deposit_address(name, norm_source_chain)
        deposit_address = cs_derived["depositAddress"]
        interop_address = cs_derived["interoperableAddress"]
        deposit_memo = None
        time_estimate_sec = 45
        expires_at = None
        time_when_inactive = None
        oneclick_signature = None
        correlation_id = None
        provider_label = (
            "aurora-persistent-simulated"
            if settings.AURORA_API_KEY
            else "chain-signatures+near-intents"
        )

        valid_refund_to = refund_to or VALID_DEFAULT_REFUND_BY_CHAIN.get(
            oc_source_chain, deposit_address
        )

        is_below_min_request = bool(amount_in and int(amount_in) < min_amount_int)

        # Call live 1Click POST /v0/quote
        if live_1click_quote:
            deadline = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            quote_amount_str = default_10usd_base if is_below_min_request else effective_amount_base
            quote_payload = {
                "dry": dry,
                "swapType": swap_type,
                "slippageTolerance": 100,
                "originAsset": src_entry["assetId"],
                "depositType": "ORIGIN_CHAIN",
                "destinationAsset": dst_entry["assetId"],
                "amount": quote_amount_str,
                "refundTo": valid_refund_to,
                "refundType": "ORIGIN_CHAIN",
                "recipient": recipient_address,
                "recipientType": "DESTINATION_CHAIN",
                "deadline": deadline,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{settings.ONECLICK_BASE_URL}/v0/quote",
                        json=quote_payload,
                        headers=self._headers(),
                    )
                    # Note: 1Click returns 201 Created (or 200 OK) on success
                    if resp.status_code in (200, 201):
                        full_data = resp.json()
                        q_data = full_data.get("quote", full_data)
                        oneclick_signature = full_data.get("signature")
                        correlation_id = full_data.get("correlationId")
                        deposit_memo = q_data.get("depositMemo")
                        time_estimate_sec = int(q_data.get("timeEstimate", time_estimate_sec))
                        expires_at = q_data.get("deadline")
                        time_when_inactive = q_data.get("timeWhenInactive")
                        min_amount_base = str(q_data.get("minAmountIn", min_amount_base))
                        if not is_below_min_request:
                            est_receive_base = str(q_data.get("amountOut", est_receive_base))
                            est_receive_formatted = str(
                                q_data.get("amountOutFormatted", est_receive_formatted)
                            )
                        if not dry and q_data.get("depositAddress"):
                            deposit_address = q_data["depositAddress"]
                            try:
                                raw_dep = address_string_to_bytes(norm_source_chain, deposit_address)
                                interop_address = encode_erc7930(norm_source_chain, raw_dep)
                            except Exception:
                                pass
                        provider_label = "near-intents-1click"
                    else:
                        logger.warning(
                            f"1Click quote returned {resp.status_code} ({resp.text}); using CS fallback."
                        )
            except Exception as exc:
                logger.warning(f"1Click POST /v0/quote unreachable, using CS fallback: {exc}")

        warnings = [
            f"Send only {src_sym} on {norm_source_chain}. Minimum deposit: {min_amount_base} base units."
        ]
        if deposit_memo:
            warnings.append(f"MEMO_REQUIRED: You must include memo '{deposit_memo}' with your transfer.")
        if amount_in and int(amount_in) < int(min_amount_base):
            warnings.insert(
                0,
                f"BELOW_MINIMUM: Input amount {amount_in} is below route minimum {min_amount_base}.",
            )

        return {
            "depositAddress": deposit_address,
            "depositMemo": deposit_memo,
            "chainSignaturesFallbackAddress": cs_derived["depositAddress"],
            "interoperableAddress": interop_address,
            "swapType": swap_type,
            "sourceAssetId": src_entry["assetId"],
            "destinationAssetId": dst_entry["assetId"],
            "sourceSymbol": src_sym,
            "destSymbol": dst_sym,
            "minAmount": min_amount_base,
            "estReceive": est_receive_base,
            "estReceiveFormatted": est_receive_formatted,
            "timeEstimateSeconds": time_estimate_sec,
            "expiresAt": expires_at,
            "timeWhenInactive": time_when_inactive,
            "provider": provider_label,
            "fee": fee_info,
            "oneclickSignature": oneclick_signature,
            "correlationId": correlation_id,
            "warnings": warnings,
        }

    async def submit_deposit_tx(
        self,
        deposit_address: str,
        tx_hash: str,
        near_sender_account: str | None = None,
        memo: str | None = None,
    ) -> dict:
        """
        Wraps POST /v0/deposit/submit per 1Click OpenAPI spec.
        """
        payload: dict[str, str] = {
            "depositAddress": deposit_address,
            "txHash": tx_hash,
        }
        if near_sender_account:
            payload["nearSenderAccount"] = near_sender_account
        if memo:
            payload["memo"] = memo

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{settings.ONECLICK_BASE_URL}/v0/deposit/submit",
                json=payload,
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return {"error": "SUBMIT_DEPOSIT_FAILED", "status_code": resp.status_code, "details": resp.text}
            return resp.json()

    async def get_execution_status(
        self, deposit_address: str, deposit_memo: str | None = None
    ) -> dict:
        """
        Wraps GET /v0/status?depositAddress=...&depositMemo=... per 1Click OpenAPI spec.
        """
        params: dict[str, str] = {"depositAddress": deposit_address}
        if deposit_memo:
            params["depositMemo"] = deposit_memo

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{settings.ONECLICK_BASE_URL}/v0/status",
                params=params,
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return {"status": "PENDING_DEPOSIT", "details": resp.text}
            return resp.json()
