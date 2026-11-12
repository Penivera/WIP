import base64
import json
import logging
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
                raise RuntimeError(f"NEAR RPC error: {data.get('error')}")
            raw_bytes = bytes(data["result"]["result"])
            decoded = raw_bytes.decode("utf-8")
            return json.loads(decoded) if decoded else None

    async def get_record(self, normalized_name: str) -> dict | None:
        if not settings.USE_MOCK_CONTRACT:
            try:
                # Directly return whatever the blockchain says, even if it is None (null)
                return await self.call_view_method("get_record", {"name": normalized_name})
            except Exception as exc:
                logger.error(f"RPC get_record({normalized_name}) failed: {exc}")
                # Return None on network failure instead of masking it with mock data
                return None

        # Only reach here if USE_MOCK_CONTRACT is explicitly true
        return MOCK_CONTRACT_RECORDS.get(normalized_name)