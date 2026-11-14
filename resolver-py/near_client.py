import asyncio
import base64
import json
import logging
import re
import time
import uuid
import httpx

from config import settings
from erc7930 import (
    COIN_TYPE_NEAR,
    COIN_TYPE_SOLANA,
    EVM_FLAG,
    SOLANA_MAINNET_CAIP2,
    address_string_to_bytes,
)

logger = logging.getLogger(__name__)

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class NearRpcError(RuntimeError):
    """Raised when the NEAR RPC cannot be reached or returns an upstream error in live mode."""


MOCK_CONTRACT_RECORDS: dict[str, dict] = {
    # 1. peni.uni: Core Mode 1 (ETH, Solana, NEAR connected) + Optional Mode 2 policy (force_default=False)
    "peni.uni": {
        "owner": "peni.testnet",
        "generation": 1,
        "chains": {
            "eip155:1": {
                "address": list(
                    address_string_to_bytes(
                        "eip155:1", "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
                    )
                ),
                "proof": [1, 2, 3],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": EVM_FLAG | 1,
            },
            SOLANA_MAINNET_CAIP2: {
                "address": list(
                    address_string_to_bytes(
                        SOLANA_MAINNET_CAIP2, "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
                    )
                ),
                "proof": [4, 5, 6],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": COIN_TYPE_SOLANA,
            },
            "near:testnet": {
                "address": list(b"peni.testnet"),
                "proof": [7, 8, 9],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": COIN_TYPE_NEAR,
            },
        },
        "policy": {
            "force_default": False,
            "default": {
                "Asset": {
                    "asset": f"{SOLANA_MAINNET_CAIP2}/token:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "chain": SOLANA_MAINNET_CAIP2,
                    "address": list(
                        address_string_to_bytes(
                            SOLANA_MAINNET_CAIP2, "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
                        )
                    ),
                }
            },
            "payout": None,
        },
        "pending": None,
    },
    # 2. demo.uni: Strict Mode 1 (ETH & Solana connected, no receive policy -> unconnected chain returns NO_RECORD)
    "demo.uni": {
        "owner": "demo.testnet",
        "generation": 1,
        "chains": {
            "eip155:1": {
                "address": list(
                    address_string_to_bytes(
                        "eip155:1", "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
                    )
                ),
                "proof": [1, 2, 3],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": EVM_FLAG | 1,
            },
            SOLANA_MAINNET_CAIP2: {
                "address": list(
                    address_string_to_bytes(
                        SOLANA_MAINNET_CAIP2, "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
                    )
                ),
                "proof": [4, 5, 6],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": COIN_TYPE_SOLANA,
            },
        },
        "policy": None,
        "pending": None,
    },
    # 3. routed.uni: Mode 2 Preferred Asset forced (force_default=True -> always routes to USDC on Solana)
    "routed.uni": {
        "owner": "freelancer.testnet",
        "generation": 1,
        "chains": {
            "eip155:1": {
                "address": list(
                    address_string_to_bytes(
                        "eip155:1", "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
                    )
                ),
                "proof": [1, 2, 3],
                "set_at_ms": 1762732800000,
                "generation": 1,
                "coin_type": EVM_FLAG | 1,
            }
        },
        "policy": {
            "force_default": True,
            "default": {
                "Asset": {
                    "asset": f"{SOLANA_MAINNET_CAIP2}/token:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "chain": SOLANA_MAINNET_CAIP2,
                    "address": list(
                        address_string_to_bytes(
                            SOLANA_MAINNET_CAIP2, "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
                        )
                    ),
                }
            },
            "payout": None,
        },
        "pending": None,
    },
    # 4. bank.uni: Mode 3 Bank Payout (routes to USDC on Solana + licensed off-ramp payout)
    "bank.uni": {
        "owner": "merchant.testnet",
        "generation": 1,
        "chains": {},
        "policy": {
            "force_default": True,
            "default": {
                "Asset": {
                    "asset": f"{SOLANA_MAINNET_CAIP2}/token:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "chain": SOLANA_MAINNET_CAIP2,
                    "address": list(
                        address_string_to_bytes(
                            SOLANA_MAINNET_CAIP2, "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
                        )
                    ),
                }
            },
            "payout": {
                "Payout": {
                    "provider": "sphere-sandbox",
                    "reference": "prov_ref_demo_001",
                }
            },
        },
        "pending": None,
    },
}


class NearContractClient:
    def _redact_cli_output(self, text: str) -> str:
        if settings.ADMIN_PRIVATE_KEY and settings.ADMIN_PRIVATE_KEY in text:
            text = text.replace(settings.ADMIN_PRIVATE_KEY, "[REDACTED_PRIVATE_KEY]")
        return text

    def _extract_tx_hash(self, out_text: str, err_text: str) -> str:
        combined = ANSI_ESCAPE_RE.sub("", f"{out_text}\n{err_text}")
        tx_match = re.search(r"Transaction ID:\s*([A-Za-z0-9]+)", combined)
        if tx_match:
            return tx_match.group(1)
        explorer_match = re.search(r"transactions/([A-Za-z0-9]+)", combined)
        if explorer_match:
            return explorer_match.group(1)
        return f"tx_{int(time.time())}"

    async def call_view_method(self, method_name: str, args: dict) -> dict | list | bool | str | None:
        args_b64 = base64.b64encode(json.dumps(args).encode("utf-8")).decode("utf-8")
        payload = {
            "jsonrpc": "2.0",
            "id": "unam-resolver",
            "method": "query",
            "params": {
                "request_type": "call_function",
                "finality": "optimistic",
                "account_id": settings.REGISTRY_CONTRACT_ID,
                "method_name": method_name,
                "args_base64": args_b64,
            },
        }
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(settings.NEAR_RPC_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data or "result" not in data or "result" not in data["result"]:
                raise NearRpcError(f"NEAR RPC error: {data.get('error')}")
            raw_bytes = bytes(data["result"]["result"])
            decoded = raw_bytes.decode("utf-8")
            return json.loads(decoded) if decoded else None

    async def get_record(self, normalized_name: str) -> dict | None:
        if not settings.USE_MOCK_CONTRACT:
            try:
                return await self.call_view_method("get_record", {"name": normalized_name})
            except Exception as exc:
                logger.error(f"RPC get_record({normalized_name}) failed: {exc}")
                raise NearRpcError(
                    f"NEAR RPC unavailable while querying {normalized_name}: {exc}"
                ) from exc

        return MOCK_CONTRACT_RECORDS.get(normalized_name)

    async def get_pricing_config(self) -> dict:
        if not settings.USE_MOCK_CONTRACT:
            try:
                res = await self.call_view_method("get_pricing_config", {})
                if isinstance(res, dict):
                    return res
                raise NearRpcError("Empty pricing config from NEAR RPC")
            except Exception as exc:
                logger.error(f"RPC get_pricing_config() failed: {exc}")
                raise NearRpcError(
                    f"NEAR RPC unavailable while querying pricing config: {exc}"
                ) from exc

        default_usdc = (
            "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
            if settings.NEAR_NETWORK == "mainnet"
            else "3e2210e1184b45b64c8a434c0a7e7b23cc04ea7eb7a6c3c32520d03d4afcb8af"
        )
        return {
            "payment_token": default_usdc,
            "accepted_tokens": [],
            "relayer": settings.REGISTRY_CONTRACT_ID,
            "price_3_char": "50000000",
            "price_4_char": "20000000",
            "price_5_plus_char": "5000000",
        }

    async def price_for_name(self, normalized_name: str) -> int:
        if not settings.USE_MOCK_CONTRACT:
            try:
                val = await self.call_view_method("price_for_name", {"name": normalized_name})
                if val is not None:
                    return int(val)
                raise NearRpcError("Empty price_for_name result from NEAR RPC")
            except Exception as exc:
                logger.error(f"RPC price_for_name({normalized_name}) failed: {exc}")
                raise NearRpcError(
                    f"NEAR RPC unavailable while querying price for {normalized_name}: {exc}"
                ) from exc

        label = normalized_name[:-4] if normalized_name.endswith(".uni") else normalized_name
        if len(label) == 3:
            return 50_000_000
        elif len(label) == 4:
            return 20_000_000
        else:
            return 5_000_000

    async def execute_claim_for(self, name: str, owner: str, payment_ref: str) -> dict:
        """
        Executes claim_for on the registry contract via the authorized relayer.
        When USE_MOCK_CONTRACT is False, signs and broadcasts using near-cli-rs on settings.NEAR_NETWORK
        and raises RuntimeError on failure (never falls back to simulated success in live mode).
        """
        if not settings.USE_MOCK_CONTRACT:
            if not settings.ADMIN_PRIVATE_KEY:
                raise RuntimeError("ADMIN_PRIVATE_KEY is required when USE_MOCK_CONTRACT=false")
            cmd = [
                "near",
                "contract",
                "call-function",
                "as-transaction",
                settings.REGISTRY_CONTRACT_ID,
                "claim_for",
                "json-args",
                json.dumps({"name": name, "owner": owner, "payment_ref": payment_ref}),
                "prepaid-gas",
                "100.0 Tgas",
                "attached-deposit",
                "0 NEAR",
                "sign-as",
                settings.REGISTRY_CONTRACT_ID,
                "network-config",
                settings.NEAR_NETWORK,
                "sign-with-plaintext-private-key",
                settings.ADMIN_PRIVATE_KEY,
                "send",
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
            except Exception as exc:
                logger.error(f"Error calling live claim_for: {exc}")
                raise RuntimeError(f"Error executing live claim_for: {exc}") from exc

            out_text = self._redact_cli_output(stdout.decode())
            err_text = self._redact_cli_output(stderr.decode())
            if proc.returncode == 0:
                tx_hash = self._extract_tx_hash(out_text, err_text)
                logger.info(f"Live claim_for succeeded for {name}: Tx {tx_hash}")
                return {
                    "status": "SUCCESS",
                    "tx_hash": tx_hash,
                    "name": name,
                    "owner": owner,
                    "payment_ref": payment_ref,
                }
            logger.warning(
                f"Live claim_for command returned {proc.returncode}: {out_text} {err_text}"
            )
            raise RuntimeError(
                f"Live claim_for failed with exit code {proc.returncode}: {err_text or out_text}"
            )

        # Mock registration (only when USE_MOCK_CONTRACT is explicitly True)
        MOCK_CONTRACT_RECORDS[name] = {
            "owner": owner,
            "generation": 1,
            "chains": {},
            "policy": None,
            "pending": None,
        }
        return {
            "status": "SUCCESS",
            "tx_hash": f"sim_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            "name": name,
            "owner": owner,
            "payment_ref": payment_ref,
        }

    async def admin_approve_pending_policy(self, name: str) -> dict:
        """
        Admin approves a queued destination/policy change, bypassing the time delay.
        When USE_MOCK_CONTRACT is False, executes on settings.NEAR_NETWORK and raises RuntimeError on failure.
        """
        clean_name = name.strip().lower()
        if not settings.USE_MOCK_CONTRACT:
            if not settings.ADMIN_PRIVATE_KEY:
                raise RuntimeError("ADMIN_PRIVATE_KEY is required when USE_MOCK_CONTRACT=false")
            cmd = [
                "near",
                "contract",
                "call-function",
                "as-transaction",
                settings.REGISTRY_CONTRACT_ID,
                "admin_approve_pending_policy",
                "json-args",
                json.dumps({"name": clean_name}),
                "prepaid-gas",
                "100.0 Tgas",
                "attached-deposit",
                "0 NEAR",
                "sign-as",
                settings.REGISTRY_CONTRACT_ID,
                "network-config",
                settings.NEAR_NETWORK,
                "sign-with-plaintext-private-key",
                settings.ADMIN_PRIVATE_KEY,
                "send",
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
            except Exception as exc:
                logger.error(f"Error calling live admin_approve: {exc}")
                raise RuntimeError(f"Error executing live admin_approve: {exc}") from exc

            out_text = self._redact_cli_output(stdout.decode())
            err_text = self._redact_cli_output(stderr.decode())
            if proc.returncode == 0:
                tx_hash = self._extract_tx_hash(out_text, err_text)
                logger.info(
                    f"Live admin_approve_pending_policy succeeded for {clean_name}: Tx {tx_hash}"
                )
                return {
                    "status": "APPROVED",
                    "tx_hash": tx_hash,
                    "name": clean_name,
                }
            logger.warning(f"Live admin_approve returned {proc.returncode}: {out_text} {err_text}")
            raise RuntimeError(
                f"Live admin_approve_pending_policy failed with exit code {proc.returncode}: {err_text or out_text}"
            )

        # Mock mode (only when USE_MOCK_CONTRACT is explicitly True)
        rec = MOCK_CONTRACT_RECORDS.get(clean_name)
        if not rec or not rec.get("pending"):
            raise ValueError("NO_PENDING_CHANGE")
        pending = rec.pop("pending")
        rec["policy"] = pending.get("policy")
        return {
            "status": "APPROVED",
            "tx_hash": f"sim_approve_{int(time.time())}",
            "name": clean_name,
        }