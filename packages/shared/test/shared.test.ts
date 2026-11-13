import { describe, expect, it } from "vitest";
import {
  addressToBytes,
  bytesToAddress,
  formatUnits,
  fromRawRecord,
  normalizeName,
  parseContractError,
  parseUnits,
  toRawPolicy,
  type Payout,
  type RawRecord,
} from "../src/index.js";

const SOL = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d";
const SOL_ADDR = "13QkxhNMrTPxoCkRdYdJ65tFuwXPhL5gLS2Z5Nr6gjRK";
const EVM_ADDR = "0x2527d02599ba641c19fea793cd0f167589a0f10d";
const USDC_SOL = "nep141:sol-5ce3bf3a31af18be40ba30f721101b4341690186.omft.near";

describe("normalizeName (same cases as the contract's unit tests)", () => {
  it("accepts and folds", () => {
    expect(normalizeName("peni")).toEqual({ ok: true, name: "peni" });
    expect(normalizeName("Peni.Uni")).toEqual({ ok: true, name: "peni" });
    expect(normalizeName("  peni  ")).toEqual({ ok: true, name: "peni" });
    expect(normalizeName("UPPER")).toEqual({ ok: true, name: "upper" });
  });
  it("rejects", () => {
    for (const bad of ["ab", "-peni", "peni-", "pe--ni", "p ni", "admin", "", "a".repeat(33), "péni"]) {
      expect(normalizeName(bad).ok, bad).toBe(false);
    }
  });
});

describe("address codecs", () => {
  it("round-trips EVM, Solana and NEAR", () => {
    const evm = addressToBytes("eip155:1", EVM_ADDR);
    expect(evm).toHaveLength(20);
    expect(bytesToAddress("eip155:1", evm)).toBe(EVM_ADDR);

    const sol = addressToBytes(SOL, SOL_ADDR);
    expect(sol).toHaveLength(32);
    expect(bytesToAddress(SOL, sol)).toBe(SOL_ADDR);

    const near = addressToBytes("near:mainnet", "alice.near");
    expect(bytesToAddress("near:mainnet", near)).toBe("alice.near");
  });
  it("keeps leading zero bytes in base58", () => {
    const sys = "11111111111111111111111111111111"; // Solana system program: 32 zero bytes
    expect(addressToBytes(SOL, sys)).toEqual(new Array(32).fill(0));
    expect(bytesToAddress(SOL, new Array(32).fill(0))).toBe(sys);
  });
  it("rejects wrong lengths", () => {
    expect(() => addressToBytes("eip155:1", "0x1234")).toThrow();
    expect(() => addressToBytes(SOL, EVM_ADDR)).toThrow();
    expect(() => addressToBytes("cosmos:hub", "x")).toThrow();
  });
});

describe("policy and record mapping", () => {
  const crypto: Payout = { type: "crypto", chain: SOL, token: "USDC", assetId: USDC_SOL, address: SOL_ADDR };
  const bank: Payout = { ...crypto, type: "bank", provider: "demo-offramp", reference: "ref-1" };

  it("builds the contract's policy shape", () => {
    const raw = toRawPolicy(false, crypto);
    expect(raw.payout).toBeNull();
    expect(raw.default).toEqual({
      Asset: { asset: USDC_SOL, chain: SOL, address: addressToBytes(SOL, SOL_ADDR) },
    });
    expect(toRawPolicy(true, bank).payout).toEqual({
      Payout: { provider: "demo-offramp", reference: "ref-1" },
    });
  });

  it("reshapes a contract record", () => {
    const raw: RawRecord = {
      owner: "someone.testnet",
      generation: 3,
      chains: {
        "eip155:1": {
          address: addressToBytes("eip155:1", EVM_ADDR),
          proof: [1],
          set_at_ms: 1,
          generation: 3,
          coin_type: 2147483649,
        },
      },
      policy: toRawPolicy(true, bank),
      pending: { effective_at_ms: 99, kind: "Clear" },
    };
    const rec = fromRawRecord("peni", raw, (id) => (id === USDC_SOL ? "USDC" : undefined));
    expect(rec).toEqual({
      name: "peni",
      owner: "someone.testnet",
      records: { "eip155:1": EVM_ADDR },
      policy: { force_default: true, payout: bank },
      pending: { effective_at_ms: 99, policy: null },
      version: 3,
    });
  });
});

describe("amounts and errors", () => {
  it("converts without floats", () => {
    expect(parseUnits("2", 6)).toBe(2000000n);
    expect(parseUnits("0.000001", 6)).toBe(1n);
    expect(parseUnits("1.5", 18)).toBe(1500000000000000000n);
    expect(() => parseUnits("1.2345678", 6)).toThrow();
    expect(() => parseUnits("-1", 6)).toThrow();
    expect(formatUnits("1761799", 6)).toBe("1.761799");
    expect(formatUnits(2000000n, 6)).toBe("2");
  });
  it("reads contract panic codes", () => {
    expect(parseContractError("Smart contract panicked: NAME_INVALID: reserved name")).toEqual({
      code: "NAME_INVALID",
      detail: "reserved name",
    });
    expect(parseContractError("Smart contract panicked: NAME_TAKEN")?.code).toBe("NAME_TAKEN");
  });
});
