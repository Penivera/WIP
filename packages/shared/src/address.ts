/**
 * Text <-> byte-array address codecs. The contract stores addresses as bytes
 * and checks only the length per namespace:
 *   eip155 = 20 bytes, solana = 32 bytes, near = 2..64 UTF-8 bytes, bip122 = 20..80 bytes.
 * Bitcoin addresses are stored as their UTF-8 text, which fits the 20..80 rule.
 */
import { namespaceOf } from "./chains.js";

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

export function base58Decode(text: string): Uint8Array {
  let n = 0n;
  for (const ch of text) {
    const v = B58.indexOf(ch);
    if (v < 0) throw new Error("invalid base58 character");
    n = n * 58n + BigInt(v);
  }
  const bytes: number[] = [];
  while (n > 0n) {
    bytes.unshift(Number(n & 0xffn));
    n >>= 8n;
  }
  for (const ch of text) {
    if (ch !== "1") break;
    bytes.unshift(0);
  }
  return Uint8Array.from(bytes);
}

export function base58Encode(bytes: Uint8Array): string {
  let n = 0n;
  for (const b of bytes) n = (n << 8n) | BigInt(b);
  let out = "";
  while (n > 0n) {
    out = B58[Number(n % 58n)] + out;
    n /= 58n;
  }
  for (const b of bytes) {
    if (b !== 0) break;
    out = "1" + out;
  }
  return out;
}

function hexDecode(text: string): Uint8Array {
  const h = text.startsWith("0x") || text.startsWith("0X") ? text.slice(2) : text;
  if (h.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(h)) throw new Error("invalid hex");
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function hexEncode(bytes: Uint8Array): string {
  return "0x" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Converts a text address into the bytes the contract expects. Throws if malformed. */
export function addressToBytes(chain: string, address: string): number[] {
  const ns = namespaceOf(chain);
  const text = address.trim();
  let bytes: Uint8Array;
  switch (ns) {
    case "eip155":
      bytes = hexDecode(text);
      if (bytes.length !== 20) throw new Error("EVM address must be 20 bytes");
      break;
    case "solana":
      bytes = base58Decode(text);
      if (bytes.length !== 32) throw new Error("Solana address must be 32 bytes");
      break;
    case "near":
      bytes = new TextEncoder().encode(text);
      if (bytes.length < 2 || bytes.length > 64 || !/^[A-Za-z0-9._-]+$/.test(text)) {
        throw new Error("invalid NEAR account ID");
      }
      break;
    case "bip122":
      bytes = new TextEncoder().encode(text);
      if (bytes.length < 20 || bytes.length > 80) throw new Error("invalid Bitcoin address");
      break;
    default:
      throw new Error(`unsupported chain: ${chain}`);
  }
  return Array.from(bytes);
}

/** Converts address bytes from the contract back to text. */
export function bytesToAddress(chain: string, bytes: ArrayLike<number>): string {
  const ns = namespaceOf(chain);
  const u8 = Uint8Array.from(bytes);
  switch (ns) {
    case "eip155":
      return hexEncode(u8);
    case "solana":
      return base58Encode(u8);
    case "near":
    case "bip122":
      return new TextDecoder().decode(u8);
    default:
      throw new Error(`unsupported chain: ${chain}`);
  }
}

export function isValidAddress(chain: string, address: string): boolean {
  try {
    addressToBytes(chain, address);
    return true;
  } catch {
    return false;
  }
}
