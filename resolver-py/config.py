import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    NEAR_RPC_URL: str = os.getenv("NEAR_RPC_URL", "https://rpc.testnet.near.org")
    REGISTRY_CONTRACT_ID: str = os.getenv("REGISTRY_CONTRACT_ID", "unam-registry.testnet")
    MPC_SIGNER_CONTRACT_ID: str = os.getenv("MPC_SIGNER_CONTRACT_ID", "v1.signer-prod.testnet")
    USE_MOCK_CONTRACT: bool = os.getenv("USE_MOCK_CONTRACT", "false").lower() == "true"
    PROOF_VERIFICATION_MODE: str = os.getenv("PROOF_VERIFICATION_MODE", "permissive")

    AURORA_INTENTS_BASE_URL: str = os.getenv(
        "AURORA_INTENTS_BASE_URL", "https://api.intents.aurora.dev/v1"
    )
    ONECLICK_BASE_URL: str = os.getenv("ONECLICK_BASE_URL", "https://1click.chaindefuser.com")

    RESOLVER_SIGNING_SEED_HEX: str = os.getenv(
        "RESOLVER_SIGNING_SEED_HEX",
        "756e6976657273616c2d6e616d652d6861636b6174686f6e2d323032362d6b79",
    )
    AURORA_API_KEY: str = os.getenv("AURORA_API_KEY", "")
    ONECLICK_API_KEY: str = os.getenv("ONECLICK_API_KEY", "")

    FEE_INTEGRATOR_SHARE_PCT: int = int(os.getenv("FEE_INTEGRATOR_SHARE_PCT", "60"))
    FEE_AURORA_SHARE_PCT: int = int(os.getenv("FEE_AURORA_SHARE_PCT", "40"))
    FEE_STABLECOIN_FLOOR_BPS: int = int(os.getenv("FEE_STABLECOIN_FLOOR_BPS", "2"))
    FEE_OTHER_TOKEN_FLOOR_BPS: int = int(os.getenv("FEE_OTHER_TOKEN_FLOOR_BPS", "10"))
    FEE_MAX_CAP_BPS: int = int(os.getenv("FEE_MAX_CAP_BPS", "500"))

    CS_SWEEP_MAX_USD_PER_NAME: float = float(os.getenv("CS_SWEEP_MAX_USD_PER_NAME", "5000.0"))
    CS_MIN_ATTACHED_DEPOSIT_YOCTO: str = os.getenv("CS_MIN_ATTACHED_DEPOSIT_YOCTO", "1")
    CS_MAX_ATTACHED_DEPOSIT_NEAR: float = float(os.getenv("CS_MAX_ATTACHED_DEPOSIT_NEAR", "0.65"))

    ADMIN_PUBLIC_KEY: str = os.getenv("ADMIN_PUBLIC_KEY", "")
    ADMIN_PRIVATE_KEY: str = os.getenv("ADMIN_PRIVATE_KEY", "")


settings = Settings()
