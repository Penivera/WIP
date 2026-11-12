import time
import uuid

from erc7930 import SOLANA_MAINNET_CAIP2

PROVIDER_SANDBOX_DB: dict[str, dict] = {
    "prov_ref_demo_001": {
        "provider": "sphere-sandbox",
        "kycStatus": "VERIFIED",  # Can toggle to "LAPSED" to demonstrate Spec Failure Path E
        "currency": "USD",
        "bankMasked": "Chase Checking •••• 4210",
        "virtualSolanaChain": SOLANA_MAINNET_CAIP2,
        "virtualSolanaUsdcAddress": "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH",
    }
}


class OffRampAdapter:
    def get_provider_ref(self, ref: str) -> dict | None:
        return PROVIDER_SANDBOX_DB.get(ref)

    def set_kyc_status(self, ref: str, status: str) -> None:
        if ref in PROVIDER_SANDBOX_DB:
            PROVIDER_SANDBOX_DB[ref]["kycStatus"] = status

    def execute_payout(self, ref: str, usdc_amount: str, owner_fallback_wallet: str) -> dict:
        entry = PROVIDER_SANDBOX_DB.get(ref)
        if not entry or entry.get("kycStatus") != "VERIFIED":
            # Failure Path E: Off-ramp rejects -> funds stay as USDC in owner's settlement wallet, flagged for action
            return {
                "payoutId": f"po_{uuid.uuid4().hex[:8]}",
                "status": "OFFRAMP_REJECTED_HELD_AS_USDC",
                "flaggedForAction": True,
                "reason": "Recipient verification lapsed or limit exceeded",
                "settlementWallet": owner_fallback_wallet,
                "usdcAmount": usdc_amount,
                "updatedAt": int(time.time()),
            }

        return {
            "payoutId": f"po_{uuid.uuid4().hex[:8]}",
            "provider": entry["provider"],
            "status": "SETTLED_TO_BANK",
            "flaggedForAction": False,
            "currency": entry["currency"],
            "bankMasked": entry["bankMasked"],
            "usdcAmount": usdc_amount,
            "settledAt": int(time.time()),
        }