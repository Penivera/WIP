/**
 * Mapping between the registry contract's JSON shapes (contracts/registry/src/lib.rs)
 * and the off-chain record shape. Everything contract-specific is here, so a
 * contract change touches this file and the two adapters that call it.
 *
 * Contract methods used:
 *   claim({name}) -> string
 *   set_record({name, chain, address: number[], proof: number[]})
 *   set_policy({name, policy: RawPolicy | null}) -> effective_at_ms
 *   apply_pending_policy({name})
 *   get_record({name}) -> RawRecord | null
 *   names_of({account, skip, limit}) -> string[]
 *   change_delay_ms() -> number
 */
import { addressToBytes, bytesToAddress } from "./address.js";
import { normalizeChainKey } from "./chains.js";
import type { NameRecord, Payout, PendingPolicy, Policy } from "./types.js";

export interface RawChainRecord {
  address: number[];
  proof: number[];
  set_at_ms: number;
  generation: number;
  coin_type: number;
}

/** serde's default enum encoding: `{"Asset": {...}}` or `{"Payout": {...}}`. */
export type RawDestination =
  | { Asset: { asset: string; chain: string; address: number[] } }
  | { Payout: { provider: string; reference: string } };

export interface RawPolicy {
  force_default: boolean;
  default: RawDestination | null;
  payout: RawDestination | null;
}

export type RawPendingKind = { Set: RawPolicy } | "Clear";

export interface RawRecord {
  owner: string;
  generation: number;
  chains: Record<string, RawChainRecord>;
  policy: RawPolicy | null;
  pending: { effective_at_ms: number; kind: RawPendingKind } | null;
}

export const CONTRACT_METHODS = {
  claim: "claim",
  setRecord: "set_record",
  setPolicy: "set_policy",
  applyPolicy: "apply_pending_policy",
  getRecord: "get_record",
  namesOf: "names_of",
  changeDelay: "change_delay_ms",
} as const;

/** Looks up a token symbol from a 1Click asset ID. Falls back to the ID itself. */
export type SymbolOf = (assetId: string) => string | undefined;

/**
 * Off-chain payout -> contract policy.
 *   crypto: default = Asset(owner's address),          payout = null
 *   bank:   default = Asset(provider deposit address), payout = Payout(provider, reference)
 * The contract's `asset` field holds the 1Click asset ID.
 */
export function toRawPolicy(forceDefault: boolean, payout: Payout): RawPolicy {
  const chain = normalizeChainKey(payout.chain);
  return {
    force_default: forceDefault,
    default: {
      Asset: { asset: payout.assetId, chain, address: addressToBytes(chain, payout.address) },
    },
    payout:
      payout.type === "bank"
        ? { Payout: { provider: payout.provider, reference: payout.reference } }
        : null,
  };
}

/** Contract policy -> off-chain policy. Returns null for shapes this app cannot route. */
export function fromRawPolicy(raw: RawPolicy | null, symbolOf?: SymbolOf): Policy | null {
  if (!raw || !raw.default || !("Asset" in raw.default)) return null;
  const { asset, chain, address } = raw.default.Asset;
  const base = {
    chain,
    token: symbolOf?.(asset) ?? asset,
    assetId: asset,
    address: bytesToAddress(chain, address),
  };
  const payout: Payout =
    raw.payout && "Payout" in raw.payout
      ? {
          type: "bank",
          ...base,
          provider: raw.payout.Payout.provider,
          reference: raw.payout.Payout.reference,
        }
      : { type: "crypto", ...base };
  return { force_default: raw.force_default, payout };
}

export function fromRawRecord(name: string, raw: RawRecord, symbolOf?: SymbolOf): NameRecord {
  const records: Record<string, string> = {};
  for (const [chain, rec] of Object.entries(raw.chains)) {
    records[chain] = bytesToAddress(chain, rec.address);
  }
  let pending: PendingPolicy | null = null;
  if (raw.pending) {
    const kind = raw.pending.kind;
    pending = {
      effective_at_ms: raw.pending.effective_at_ms,
      policy: kind === "Clear" ? null : fromRawPolicy(kind.Set, symbolOf),
    };
  }
  return {
    name,
    owner: raw.owner,
    records,
    policy: fromRawPolicy(raw.policy, symbolOf),
    pending,
    version: raw.generation,
  };
}

/** Arguments for `connect_chain`. The contract requires a non-empty proof. */
export function toConnectChainArgs(name: string, chain: string, address: string, proof: Uint8Array) {
  const key = normalizeChainKey(chain);
  return { name, chain: key, address: addressToBytes(key, address), proof: Array.from(proof) };
}

/**
 * Message a wallet signs to prove it controls the address being connected.
 * The contract stores the signature but does not check it, and defines no
 * format, so this wording is ours until the contract developer fixes one.
 */
export function proofMessage(name: string, chain: string, address: string): string {
  return `Universal Name: connect ${address} on ${normalizeChainKey(chain)} to "${name}"`;
}

/** Contract panics look like `CODE` or `CODE: detail`, wrapped in runtime text. */
export function parseContractError(text: string): { code: string; detail: string } | null {
  const m = /\b([A-Z][A-Z_]{3,})(?::\s*([^"'\\]*))?/.exec(text);
  if (!m || !m[1]) return null;
  return { code: m[1], detail: (m[2] ?? "").trim() };
}
