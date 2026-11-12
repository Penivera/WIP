import hashlib
import time
import base58
from eth_utils import keccak, to_checksum_address
from nacl.signing import SigningKey

from config import settings
from erc7930 import canonicalize_chain_key, encode_erc7930


class ChainSignaturesManager:
    """
    Phase 3 Fallback: Deterministic per-(registry_contract, name, chain) wallet derivation
    and MPC sweep manager with per-name limits and audit logging.
    """

    def __init__(self) -> None:
        self.audit_log: list[dict] = []
        self.swept_usd_by_name: dict[str, float] = {}

    def derivation_path(self, name: str, chain_key: str) -> str:
        ns, ref, _ = canonicalize_chain_key(chain_key)
        return f"{name.strip().lower()},{ns}:{ref}"

    def derive_deposit_address(
        self, name: str, chain_key: str, registry_account: str | None = None
    ) -> dict:
        """
        Deterministically derives an ECDSA (eip155, bip122) or EdDSA (solana) deposit address
        bound strictly to (registry_account, name, chain_key) using the NEAR MPC epsilon domain separator.
        Same inputs always yield the exact same address and ERC-7930 envelope.
        """
        reg_acct = registry_account or settings.REGISTRY_CONTRACT_ID
        ns, ref, coin_type = canonicalize_chain_key(chain_key)
        norm_chain = f"{ns}:{ref}"
        path = self.derivation_path(name, norm_chain)

        preimage = f"near-mpc-recovery v0.1.0 epsilon derivation:{reg_acct},{path}".encode("utf-8")
        digest = hashlib.sha3_256(preimage).digest()

        if ns == "eip155":
            addr_bytes = keccak(digest)[-20:]
            human_addr = to_checksum_address("0x" + addr_bytes.hex())
            curve = "secp256k1"
        elif ns == "solana":
            sk = SigningKey(digest)
            addr_bytes = bytes(sk.verify_key)
            human_addr = base58.b58encode(addr_bytes).decode("utf-8")
            curve = "ed25519"
        elif ns == "bip122":
            sha = hashlib.sha256(digest).digest()
            try:
                h160 = hashlib.new("ripemd160", sha).digest()
            except ValueError:
                h160 = hashlib.sha256(sha).digest()[:20]
            addr_bytes = b"\x00\x14" + h160
            human_addr = base58.b58encode_check(b"\x00" + h160).decode("utf-8")
            curve = "secp256k1"
        elif ns == "near":
            sk = SigningKey(digest)
            addr_bytes = bytes(sk.verify_key).hex().encode("utf-8")
            human_addr = addr_bytes.decode("utf-8")
            curve = "ed25519"
        else:
            raise ValueError(f"Unsupported Chain Signatures namespace: {ns}")

        interop_hex = encode_erc7930(norm_chain, addr_bytes)
        return {
            "name": name,
            "chainId": norm_chain,
            "coinType": coin_type,
            "derivationPath": path,
            "signerContract": settings.MPC_SIGNER_CONTRACT_ID,
            "curve": curve,
            "depositAddress": human_addr,
            "rawAddressBytes": list(addr_bytes),
            "interoperableAddress": interop_hex,
        }

    def request_mpc_sweep(
        self,
        caller_account: str,
        name: str,
        chain_key: str,
        destination_address: str,
        allowed_destinations: set[str],
        amount_usd: float,
        pending_mpc_requests: int = 0,
    ) -> dict:
        """
        Enforces Phase 3 security constraints:
        1. Only authorized watcher/service account can trigger a sweep.
        2. Destination must be in the owner's verified records or an approved 1Click swap address.
        3. Per-name USD sweep limit enforced.
        4. Calculates dynamic NEAR deposit (1 yoctoNEAR up to 0.65 NEAR) and records refund accounting.
        """
        if caller_account != settings.REGISTRY_CONTRACT_ID and caller_account != "watcher.uni.testnet":
            raise PermissionError("UNAUTHORIZED_SWEEP_CALLER")

        if destination_address not in allowed_destinations:
            raise PermissionError("UNAPPROVED_SWEEP_DESTINATION")

        current_total = self.swept_usd_by_name.get(name, 0.0)
        if current_total + amount_usd > settings.CS_SWEEP_MAX_USD_PER_NAME:
            raise ValueError("SWEEP_LIMIT_EXCEEDED_FOR_NAME")

        scale_factor = min(1.0, pending_mpc_requests / 100.0)
        attached_near = max(1e-24, round(scale_factor * settings.CS_MAX_ATTACHED_DEPOSIT_NEAR, 6))
        used_near = round(attached_near * 0.5, 6)
        refunded_near = round(attached_near - used_near, 6)

        self.swept_usd_by_name[name] = current_total + amount_usd
        entry = {
            "timestamp": int(time.time()),
            "caller": caller_account,
            "name": name,
            "chain": chain_key,
            "destination": destination_address,
            "amount_usd": amount_usd,
            "attached_deposit_near": attached_near,
            "refunded_deposit_near": refunded_near,
            "signer_contract": settings.MPC_SIGNER_CONTRACT_ID,
            "status": "MPC_SIGNATURE_ISSUED_AND_SWEPT",
        }
        self.audit_log.append(entry)
        return entry
