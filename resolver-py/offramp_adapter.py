import time
import uuid

from erc7930 import SOLANA_MAINNET_CAIP2, address_string_to_bytes

DEFAULT_SOL_DEPOSIT_ADDR = "HN7cABqLq46Es1jh92dQQisAq662SmxELLLsHHe4YWrH"
DEFAULT_EVM_DEPOSIT_ADDR = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"

# Sample addresses from README/MOCK_CONTRACT_RECORDS that nobody on the team controls.
# Live 1Click quotes must not route real funds to these sample addresses.
UNCONTROLLED_SAMPLE_ADDRESSES: set[str] = {
    DEFAULT_SOL_DEPOSIT_ADDR.lower(),
    DEFAULT_EVM_DEPOSIT_ADDR.lower(),
}

# Known USDC token identifiers (symbols, contract addresses, and 1Click assetIds) per chain
SUPPORTED_BANK_USDC_BY_CHAIN: dict[str, set[str]] = {
    SOLANA_MAINNET_CAIP2: {
        "usdc",
        "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
        f"{SOLANA_MAINNET_CAIP2.lower()}/token:epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v",
        "nep141:sol-5ce3bf3a31af18be40ba30f721101b4341690186.omft.near",
    },
    "eip155:1": {
        "usdc",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "nep141:eth-0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.omft.near",
    },
    "eip155:8453": {
        "usdc",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "eip155:8453/erc20:0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near",
    },
    "eip155:42161": {
        "usdc",
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "eip155:42161/erc20:0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "nep141:arb-0xaf88d065e77c8cc2239327c5edb3a432268e5831.omft.near",
    },
    "eip155:10": {
        "usdc",
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
        "eip155:10/erc20:0x0b2c639c533813f4aa9d7837caf62653d097ff85",
        "nep141:op-0x0b2c639c533813f4aa9d7837caf62653d097ff85.omft.near",
    },
    "eip155:137": {
        "usdc",
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        "eip155:137/erc20:0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        "nep141:pol-0x3c499c542cef5e3811e1192ce70d8cc03d5c3359.omft.near",
    },
}

PROVIDER_SANDBOX_DB: dict[str, dict] = {
    "prov_ref_demo_001": {
        "provider": "sphere-sandbox",
        "kycStatus": "VERIFIED",  # Can toggle to "LAPSED" to demonstrate Spec Failure Path E
        "currency": "USD",
        "bankMasked": "Chase Checking ••4210",
        "supportedTokens": ["USDC"],
        "supportedChains": set(SUPPORTED_BANK_USDC_BY_CHAIN.keys()),
        "virtualSolanaChain": SOLANA_MAINNET_CAIP2,
        "virtualSolanaUsdcAddress": DEFAULT_SOL_DEPOSIT_ADDR,
    },
    "ref-001": {
        "provider": "demo-offramp",
        "kycStatus": "VERIFIED",
        "currency": "USD",
        "bankMasked": "Demo Bank Checking ••4210",
        "supportedTokens": ["USDC"],
        "supportedChains": set(SUPPORTED_BANK_USDC_BY_CHAIN.keys()),
        "virtualSolanaChain": SOLANA_MAINNET_CAIP2,
        "virtualSolanaUsdcAddress": DEFAULT_SOL_DEPOSIT_ADDR,
    },
}


def _is_valid_chain_address(chain_key: str, address: str) -> bool:
    try:
        raw = address_string_to_bytes(chain_key, address)
    except Exception:
        return False
    if chain_key.startswith("eip155:"):
        return len(raw) == 20 and raw != b"\x00" * 20 and raw != b"\x99" * 20
    if chain_key.startswith("solana:"):
        return len(raw) == 32 and raw != b"\x00" * 32
    return len(raw) > 0


class OffRampAdapter:
    def get_provider_ref(self, ref: str, provider: str) -> dict | None:
        entry = PROVIDER_SANDBOX_DB.get(ref)
        if not entry or entry.get("provider") != provider:
            return None
        return entry

    def set_kyc_status(self, ref: str, status: str) -> None:
        if ref in PROVIDER_SANDBOX_DB:
            PROVIDER_SANDBOX_DB[ref]["kycStatus"] = status

    def is_supported_bank_asset(self, chain_key: str, asset_str: str) -> bool:
        allowed = SUPPORTED_BANK_USDC_BY_CHAIN.get(chain_key)
        if not allowed:
            return False
        return asset_str.strip().lower() in allowed

    def is_uncontrolled_sample_address(self, address: str) -> bool:
        return address.strip().lower() in UNCONTROLLED_SAMPLE_ADDRESSES

    def validate_bank_destination(
        self,
        ref: str,
        provider: str,
        chain_key: str,
        asset_str: str,
        address: str,
    ) -> tuple[bool, str]:
        """
        Validates that:
        1. The destination chain is supported for bank off-ramp settlement.
        2. The destination asset on that chain is USDC.
        3. The deposit address in policy.default.Asset is a valid non-zero address for that chain
           (and if the provider entry specifies an explicit allowedDepositAddresses set for that chain, matches it).
        Returns (is_verified, status_reason) or raises ValueError on unsupported chain/token/address.
        """
        if chain_key not in SUPPORTED_BANK_USDC_BY_CHAIN:
            raise ValueError(
                f"UNSUPPORTED_BANK_DESTINATION: Chain '{chain_key}' is not supported for bank payout."
            )
        if not self.is_supported_bank_asset(chain_key, asset_str):
            raise ValueError(
                f"UNSUPPORTED_BANK_TOKEN: Bank payout requires USDC on '{chain_key}', got '{asset_str}'."
            )
        if not _is_valid_chain_address(chain_key, address):
            raise ValueError(
                f"UNSUPPORTED_BANK_DESTINATION: Invalid deposit address '{address}' for {chain_key}."
            )

        entry = self.get_provider_ref(ref, provider)
        if not entry:
            return False, "PROVIDER_OR_REFERENCE_UNVERIFIED"

        supported_chains = entry.get("supportedChains") or set(SUPPORTED_BANK_USDC_BY_CHAIN.keys())
        if chain_key not in supported_chains:
            raise ValueError(
                f"UNSUPPORTED_BANK_DESTINATION: Provider '{provider}' does not support chain {chain_key}."
            )

        explicit_addrs = (entry.get("allowedDepositAddresses") or {}).get(chain_key)
        if explicit_addrs and address.strip().lower() not in {a.lower() for a in explicit_addrs}:
            raise ValueError(
                f"UNSUPPORTED_BANK_DESTINATION: Address '{address}' is not an authorized deposit address for '{provider}' on {chain_key}."
            )

        if entry.get("kycStatus") != "VERIFIED":
            return False, "KYC_LAPSED"

        return True, "VERIFIED"

    def execute_payout(
        self,
        ref: str,
        usdc_amount: str,
        owner_fallback_wallet: str,
        provider: str,
        dest_chain: str | None = None,
        dest_token: str = "USDC",
    ) -> dict:
        entry = self.get_provider_ref(ref, provider)
        supported_tokens = (entry or {}).get("supportedTokens", ["USDC"])
        chain_ok = True
        if dest_chain:
            chain_ok = (
                dest_chain in SUPPORTED_BANK_USDC_BY_CHAIN
                and _is_valid_chain_address(dest_chain, owner_fallback_wallet)
            )

        if (
            not entry
            or entry.get("kycStatus") != "VERIFIED"
            or dest_token.upper() not in supported_tokens
            or not chain_ok
        ):
            # Failure Path E: Off-ramp rejects -> funds stay as USDC in settlement wallet, flagged for action
            return {
                "payoutId": f"po_{uuid.uuid4().hex[:8]}",
                "status": "OFFRAMP_REJECTED_HELD_AS_USDC",
                "flaggedForAction": True,
                "reason": "Recipient verification lapsed, provider mismatch, or limit exceeded",
                "settlementWallet": owner_fallback_wallet,
                "usdcAmount": usdc_amount,
                "updatedAt": int(time.time()),
            }
        return {
            "payoutId": f"po_{uuid.uuid4().hex[:8]}",
            "provider": entry["provider"],
            "status": "SETTLED_TO_BANK",
            "simulated": True,
            "flaggedForAction": False,
            "currency": entry["currency"],
            "bankMasked": entry["bankMasked"],
            "usdcAmount": usdc_amount,
            "settledAt": int(time.time()),
        }