from collections.abc import Mapping
import os
from dotenv import load_dotenv

load_dotenv()

DEV_SIGNING_SEED_HEX = "756e6976657273616c2d6e616d652d6861636b6174686f6e2d323032362d6b79"
DEV_SIGNING_SEED_BYTES = bytes.fromhex(DEV_SIGNING_SEED_HEX)


class Settings:
    NEAR_NETWORK: str
    NEAR_RPC_URL: str
    REGISTRY_CONTRACT_ID: str
    MPC_SIGNER_CONTRACT_ID: str
    CS_WATCHER_ACCOUNT_ID: str
    USE_MOCK_CONTRACT: bool
    ALLOW_DEMO_ENDPOINTS: bool
    PROOF_VERIFICATION_MODE: str
    AURORA_INTENTS_BASE_URL: str
    ONECLICK_BASE_URL: str
    RESOLVER_SIGNING_SEED_HEX: str
    AURORA_API_KEY: str
    ONECLICK_API_KEY: str
    FEE_INTEGRATOR_SHARE_PCT: int
    FEE_AURORA_SHARE_PCT: int
    FEE_STABLECOIN_FLOOR_BPS: int
    FEE_OTHER_TOKEN_FLOOR_BPS: int
    FEE_MAX_CAP_BPS: int
    CS_SWEEP_MAX_USD_PER_NAME: float
    CS_MIN_ATTACHED_DEPOSIT_YOCTO: str
    CS_MAX_ATTACHED_DEPOSIT_NEAR: float
    ADMIN_PUBLIC_KEY: str
    ADMIN_PRIVATE_KEY: str
    ADMIN_API_TOKEN: str
    EVM_RPC_URLS_JSON: str

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        src = env if env is not None else os.environ

        raw_network = (src.get("NEAR_NETWORK") or "testnet").strip().lower()
        if raw_network not in ("testnet", "mainnet"):
            raise ValueError(f"NEAR_NETWORK must be 'testnet' or 'mainnet', got '{raw_network}'")
        self.NEAR_NETWORK = raw_network

        # 1. RPC URL
        raw_rpc = (src.get("NEAR_RPC_URL") or "").strip()
        if not raw_rpc:
            self.NEAR_RPC_URL = (
                "https://rpc.mainnet.near.org"
                if raw_network == "mainnet"
                else "https://rpc.testnet.near.org"
            )
        else:
            if raw_network == "mainnet" and "testnet" in raw_rpc.lower():
                raise ValueError(f"NEAR_RPC_URL '{raw_rpc}' cannot be a testnet URL on mainnet")
            if raw_network == "testnet" and "mainnet" in raw_rpc.lower():
                raise ValueError(f"NEAR_RPC_URL '{raw_rpc}' cannot be a mainnet URL on testnet")
            self.NEAR_RPC_URL = raw_rpc

        # 2. Registry Contract ID (require explicit non-testnet account on mainnet)
        raw_registry = (src.get("REGISTRY_CONTRACT_ID") or "").strip()
        if raw_network == "mainnet":
            if not raw_registry or raw_registry.endswith(".testnet"):
                raise ValueError(
                    "REGISTRY_CONTRACT_ID must be explicitly set to a deployed mainnet account when NEAR_NETWORK=mainnet"
                )
            self.REGISTRY_CONTRACT_ID = raw_registry
        else:
            if raw_registry.endswith(".near"):
                raise ValueError(
                    f"REGISTRY_CONTRACT_ID '{raw_registry}' is a mainnet account but NEAR_NETWORK=testnet"
                )
            self.REGISTRY_CONTRACT_ID = raw_registry or "unam-registry.testnet"

        # 3. MPC Signer Contract ID
        raw_signer = (src.get("MPC_SIGNER_CONTRACT_ID") or "").strip()
        if not raw_signer:
            self.MPC_SIGNER_CONTRACT_ID = (
                "v1.signer" if raw_network == "mainnet" else "v1.signer-prod.testnet"
            )
        else:
            if raw_network == "mainnet" and "testnet" in raw_signer.lower():
                raise ValueError(
                    f"MPC_SIGNER_CONTRACT_ID '{raw_signer}' cannot be a testnet contract on mainnet"
                )
            if raw_network == "testnet" and raw_signer == "v1.signer":
                raise ValueError(
                    "MPC_SIGNER_CONTRACT_ID 'v1.signer' is for mainnet, but NEAR_NETWORK=testnet"
                )
            self.MPC_SIGNER_CONTRACT_ID = raw_signer

        # 4. Watcher Account ID
        raw_watcher = (src.get("CS_WATCHER_ACCOUNT_ID") or "").strip()
        if raw_network == "mainnet":
            if raw_watcher.endswith(".testnet"):
                raise ValueError(
                    f"CS_WATCHER_ACCOUNT_ID '{raw_watcher}' cannot be a testnet account on mainnet"
                )
            self.CS_WATCHER_ACCOUNT_ID = raw_watcher
        else:
            if raw_watcher.endswith(".near"):
                raise ValueError(
                    f"CS_WATCHER_ACCOUNT_ID '{raw_watcher}' cannot be a mainnet account on testnet"
                )
            self.CS_WATCHER_ACCOUNT_ID = raw_watcher or "watcher.uni.testnet"

        # 5. Mock Contract, Controlled Testnet Demo Mode & Proof Verification Mode
        self.USE_MOCK_CONTRACT = (src.get("USE_MOCK_CONTRACT") or "false").strip().lower() == "true"
        if raw_network == "mainnet" and self.USE_MOCK_CONTRACT:
            raise ValueError("USE_MOCK_CONTRACT cannot be true when NEAR_NETWORK=mainnet")

        raw_allow_demo = (src.get("ALLOW_DEMO_ENDPOINTS") or "").strip().lower()
        if raw_network == "mainnet":
            if raw_allow_demo == "true":
                raise ValueError("ALLOW_DEMO_ENDPOINTS cannot be true when NEAR_NETWORK=mainnet")
            self.ALLOW_DEMO_ENDPOINTS = False
        else:
            self.ALLOW_DEMO_ENDPOINTS = (raw_allow_demo or "true") == "true"

        raw_proof_mode = (src.get("PROOF_VERIFICATION_MODE") or "").strip().lower()
        if raw_proof_mode and raw_proof_mode not in ("strict", "permissive"):
            raise ValueError(
                f"PROOF_VERIFICATION_MODE must be 'strict' or 'permissive', got '{raw_proof_mode}'"
            )
        if raw_network == "mainnet":
            if raw_proof_mode == "permissive":
                raise ValueError(
                    "PROOF_VERIFICATION_MODE cannot be 'permissive' when NEAR_NETWORK=mainnet"
                )
            self.PROOF_VERIFICATION_MODE = raw_proof_mode or "strict"
        else:
            self.PROOF_VERIFICATION_MODE = raw_proof_mode or "permissive"

        # 6. Resolver Ed25519 Response-Signing Seed
        raw_seed = (src.get("RESOLVER_SIGNING_SEED_HEX") or DEV_SIGNING_SEED_HEX).strip()
        clean_seed = raw_seed[2:] if raw_seed.startswith(("0x", "0X")) else raw_seed
        try:
            seed_bytes = bytes.fromhex(clean_seed)
        except ValueError as exc:
            raise ValueError("RESOLVER_SIGNING_SEED_HEX must be valid hex") from exc
        if len(seed_bytes) != 32:
            raise ValueError(
                f"RESOLVER_SIGNING_SEED_HEX must decode to exactly 32 bytes, got {len(seed_bytes)}"
            )
        if raw_network == "mainnet" and seed_bytes == DEV_SIGNING_SEED_BYTES:
            raise ValueError(
                "RESOLVER_SIGNING_SEED_HEX must not use the known development seed when NEAR_NETWORK=mainnet"
            )
        self.RESOLVER_SIGNING_SEED_HEX = seed_bytes.hex()

        # 7. External API, Fee & Auth Settings
        self.AURORA_INTENTS_BASE_URL = (
            src.get("AURORA_INTENTS_BASE_URL") or "https://api.intents.aurora.dev/v1"
        )
        self.ONECLICK_BASE_URL = src.get("ONECLICK_BASE_URL") or "https://1click.chaindefuser.com"
        self.AURORA_API_KEY = src.get("AURORA_API_KEY") or ""
        self.ONECLICK_API_KEY = src.get("ONECLICK_API_KEY") or ""

        self.FEE_INTEGRATOR_SHARE_PCT = int(src.get("FEE_INTEGRATOR_SHARE_PCT") or "60")
        self.FEE_AURORA_SHARE_PCT = int(src.get("FEE_AURORA_SHARE_PCT") or "40")
        self.FEE_STABLECOIN_FLOOR_BPS = int(src.get("FEE_STABLECOIN_FLOOR_BPS") or "2")
        self.FEE_OTHER_TOKEN_FLOOR_BPS = int(src.get("FEE_OTHER_TOKEN_FLOOR_BPS") or "10")
        self.FEE_MAX_CAP_BPS = int(src.get("FEE_MAX_CAP_BPS") or "500")

        self.CS_SWEEP_MAX_USD_PER_NAME = float(src.get("CS_SWEEP_MAX_USD_PER_NAME") or "5000.0")
        self.CS_MIN_ATTACHED_DEPOSIT_YOCTO = src.get("CS_MIN_ATTACHED_DEPOSIT_YOCTO") or "1"
        self.CS_MAX_ATTACHED_DEPOSIT_NEAR = float(src.get("CS_MAX_ATTACHED_DEPOSIT_NEAR") or "0.65")

        self.ADMIN_PUBLIC_KEY = src.get("ADMIN_PUBLIC_KEY") or ""
        self.ADMIN_PRIVATE_KEY = src.get("ADMIN_PRIVATE_KEY") or ""
        self.ADMIN_API_TOKEN = (src.get("ADMIN_API_TOKEN") or "").strip()
        self.EVM_RPC_URLS_JSON = (src.get("EVM_RPC_URLS_JSON") or "{}").strip()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        return cls(env)


settings = Settings.from_env()
