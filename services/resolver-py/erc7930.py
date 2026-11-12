import base58
from eth_utils import to_checksum_address

CASA_CHAIN_TYPE_BYTES: dict[str, bytes] = {
    "eip155": bytes.fromhex("0000"),
    "solana": bytes.fromhex("0002"),
    "bip122": bytes.fromhex("0001"),
    "near": bytes.fromhex("018d"),
}

REVERSE_CASA_CHAIN_TYPE: dict[bytes, str] = {v: k for k, v in CASA_CHAIN_TYPE_BYTES.items()}

EVM_FLAG = 0x8000_0000
COIN_TYPE_SOLANA = 501
COIN_TYPE_BIP122 = 0
COIN_TYPE_NEAR = 397

SOLANA_MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
BIP122_MAINNET_CAIP2 = "bip122:000000000019d6689c085ae165831e93"

CHAIN_SHORTHAND_MAP: dict[str, str] = {
    "ethereum": "eip155:1",
    "eth": "eip155:1",
    "base": "eip155:8453",
    "arbitrum": "eip155:42161",
    "arb": "eip155:42161",
    "optimism": "eip155:10",
    "op": "eip155:10",
    "polygon": "eip155:137",
    "pol": "eip155:137",
    "bsc": "eip155:56",
    "avax": "eip155:43114",
    "avalanche": "eip155:43114",
    "solana": SOLANA_MAINNET_CAIP2,
    "solana:mainnet": SOLANA_MAINNET_CAIP2,
    "sol": SOLANA_MAINNET_CAIP2,
    "bitcoin": BIP122_MAINNET_CAIP2,
    "btc": BIP122_MAINNET_CAIP2,
    "near": "near:testnet",
}


def canonicalize_chain_key(raw_chain: str) -> tuple[str, str, int]:
    """
    Mirrors parse_chain_key_checked() in contracts/registry/src/lib.rs.
    Returns (namespace, reference, coin_type).
    Raises ValueError("CHAIN_KEY_INVALID: ...") on invalid keys.
    """
    mapped = CHAIN_SHORTHAND_MAP.get(raw_chain.strip().lower(), raw_chain.strip())
    if ":" not in mapped:
        raise ValueError("CHAIN_KEY_INVALID: expected namespace:reference")

    ns_raw, rest = mapped.split(":", 1)
    ns = ns_raw.lower()
    if not rest:
        raise ValueError("CHAIN_KEY_INVALID: empty reference")

    if ns == "eip155":
        if not rest.isdigit():
            raise ValueError("CHAIN_KEY_INVALID: eip155 reference must be numeric")
        chain_id = int(rest)
        if chain_id <= 0 or chain_id >= EVM_FLAG:
            raise ValueError("CHAIN_KEY_INVALID: chainId out of range")
        return ns, rest, (EVM_FLAG | chain_id)

    if ns == "solana":
        if not (32 <= len(rest) <= 44) or not all(
            c.isalnum() or c in ("_", "-") for c in rest
        ):
            raise ValueError("CHAIN_KEY_INVALID: solana reference must be 32..44 base58 chars")
        return ns, rest, COIN_TYPE_SOLANA

    if ns == "bip122":
        if len(rest) not in (32, 64) or not all(c in "0123456789abcdefABCDEF" for c in rest):
            raise ValueError("CHAIN_KEY_INVALID: bip122 reference must be 32 or 64 hex chars")
        return ns, rest.lower(), COIN_TYPE_BIP122

    if ns == "near":
        if rest not in ("mainnet", "testnet"):
            raise ValueError("CHAIN_KEY_INVALID: near reference must be mainnet|testnet")
        return ns, rest, COIN_TYPE_NEAR

    raise ValueError(f"CHAIN_KEY_INVALID: unsupported namespace: {ns}")


def normalize_contract_address_bytes(chain_key: str, raw_address: bytes) -> bytes:
    """
    Safely normalizes on-chain Vec<u8> address bytes regardless of whether the frontend stored:
    1. A full ERC-7930 envelope (starts with 0x0001 + CASA chainType),
    2. An ASCII/UTF-8 string (e.g., b"0x71C7..." or Base58 string bytes), or
    3. Raw native address bytes (20 bytes for EVM, 32 bytes for Solana).
    """
    ns, _, _ = canonicalize_chain_key(chain_key)

    if len(raw_address) > 6 and raw_address[:2] == b"\x00\x01" and raw_address[2:4] in REVERSE_CASA_CHAIN_TYPE:
        ref_len = raw_address[4]
        addr_len_idx = 5 + ref_len
        if len(raw_address) > addr_len_idx:
            addr_len = raw_address[addr_len_idx]
            extracted = raw_address[addr_len_idx + 1 : addr_len_idx + 1 + addr_len]
            if len(extracted) == addr_len and addr_len > 0:
                raw_address = extracted

    if ns == "eip155" and len(raw_address) in (40, 42):
        try:
            text = raw_address.decode("ascii").strip()
            hex_part = text[2:] if text.startswith(("0x", "0X")) else text
            return bytes.fromhex(hex_part)
        except Exception:
            pass
    elif ns == "solana" and 32 < len(raw_address) <= 44:
        try:
            text = raw_address.decode("ascii").strip()
            decoded = base58.b58decode(text)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass

    return raw_address


def encode_erc7930(chain_key: str, raw_address: bytes) -> str:
    """
    Builds the canonical ERC-7930 Interoperable Address binary envelope:
      version (2 bytes: 0x0001)
      | chainType (2 bytes CASA code)
      | chainRefLen (1 byte)
      | chainRef (variable bytes)
      | addrLen (1 byte)
      | addr (raw native address bytes)
    Returns 0x-prefixed hex string.
    """
    ns, ref, _ = canonicalize_chain_key(chain_key)
    clean_addr = normalize_contract_address_bytes(chain_key, raw_address)
    version = b"\x00\x01"
    chain_type = CASA_CHAIN_TYPE_BYTES[ns]

    if ns == "eip155":
        chain_id = int(ref)
        byte_len = max(1, (chain_id.bit_length() + 7) // 8)
        chain_ref_bytes = chain_id.to_bytes(byte_len, byteorder="big")
    elif ns == "solana":
        chain_ref_bytes = base58.b58decode(ref)
    elif ns == "bip122":
        chain_ref_bytes = bytes.fromhex(ref)
    else:
        chain_ref_bytes = ref.encode("utf-8")

    envelope = (
        version
        + chain_type
        + len(chain_ref_bytes).to_bytes(1, "big")
        + chain_ref_bytes
        + len(clean_addr).to_bytes(1, "big")
        + clean_addr
    )
    return "0x" + envelope.hex()


def format_native_address(chain_key: str, raw_address: bytes) -> str:
    """Converts on-chain Vec<u8> bytes into standard human-readable wallet address."""
    ns, _, _ = canonicalize_chain_key(chain_key)
    clean_addr = normalize_contract_address_bytes(chain_key, raw_address)

    if ns == "eip155":
        if len(clean_addr) == 20:
            return to_checksum_address("0x" + clean_addr.hex())
        return "0x" + clean_addr.hex()
    if ns == "solana":
        return base58.b58encode(clean_addr).decode("utf-8")
    if ns == "near":
        return clean_addr.decode("utf-8", errors="replace")
    if ns == "bip122":
        if len(clean_addr) == 22 and clean_addr[:2] == b"\x00\x14":
            return base58.b58encode_check(b"\x00" + clean_addr[2:]).decode("utf-8")
        if len(clean_addr) == 20:
            return base58.b58encode_check(b"\x00" + clean_addr).decode("utf-8")
        try:
            text = clean_addr.decode("ascii")
            if text.startswith(("1", "3", "bc1", "tb1")):
                return text
        except Exception:
            pass
        return "0x" + clean_addr.hex()
    return "0x" + clean_addr.hex()


def address_string_to_bytes(chain_key: str, addr_str: str) -> bytes:
    """Converts a human-readable address string into the contract's Vec<u8> byte format."""
    ns, _, _ = canonicalize_chain_key(chain_key)
    clean = addr_str.strip()
    if ns == "eip155":
        hex_part = clean[2:] if clean.startswith(("0x", "0X")) else clean
        return bytes.fromhex(hex_part)
    if ns == "solana":
        return base58.b58decode(clean)
    if ns == "near":
        return clean.encode("utf-8")
    if ns == "bip122":
        if clean.startswith(("0x", "0X")):
            return bytes.fromhex(clean[2:])
        try:
            decoded = base58.b58decode_check(clean)
            if len(decoded) == 21:
                return b"\x00\x14" + decoded[1:]
        except Exception:
            pass
        return clean.encode("utf-8")
    raise ValueError(f"Unsupported namespace: {ns}")
