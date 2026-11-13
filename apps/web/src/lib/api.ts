/**
 * Client for the Python resolver (services/resolver-py). It translates that
 * API's responses into the shapes the views use, and refuses the cases where
 * the resolver would hand back an address that is not safe to pay.
 */
import {
  chainByKey,
  chainByOneClick,
  chainName,
  formatUnits,
  normalizeName,
  parseUnits,
  CHAINS,
  type DestinationSummary,
  type Payment,
  type PaymentStatus,
  type ResolveErr,
  type ResolveErrorCode,
  type ResolveResponse,
  type SupportedAsset,
  type TokenInfo,
} from "@unam/shared";
import { settings } from "./config";
import { registry } from "./registry";

const BASE = settings.apiUrl;

/** The only provider label that means "this deposit address came from a live 1Click quote". */
const LIVE_PROVIDER = "near-intents-1click";

async function request(path: string, init?: RequestInit): Promise<{ status: number; body: any }> {
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch {
    throw new Error(`Cannot reach the resolver at ${BASE}. Is services/resolver-py running?`);
  }
  return { status: res.status, body: await res.json().catch(() => null) };
}

const fail = (error: ResolveErrorCode, message: string, supported?: SupportedAsset[]): ResolveErr => ({
  ok: false,
  error,
  message,
  ...(supported?.length ? { supported } : {}),
});

/** "USDC@eth" (the resolver's summary format) -> a supported-asset entry. */
function parseSupported(list: unknown): SupportedAsset[] {
  if (!Array.isArray(list)) return [];
  const out: SupportedAsset[] = [];
  for (const item of list) {
    const [token, blockchain] = String(item).split("@");
    const chain = blockchain ? chainByOneClick(blockchain) : undefined;
    if (token && chain) out.push({ chain: chain.key, chainName: chain.name, token });
  }
  return out;
}

/** "USDC@solana:5eykt..." -> { token, chain }. */
function splitAsset(text: unknown): { token: string; chain: string } {
  const s = String(text ?? "");
  const i = s.indexOf("@");
  return i < 0 ? { token: s, chain: "" } : { token: s.slice(0, i), chain: s.slice(i + 1) };
}

function toStatus(raw: unknown): { status: PaymentStatus; bankNote?: string } {
  const s = String(raw ?? "PENDING_DEPOSIT");
  if (s === "SETTLED_TO_BANK") return { status: "SUCCESS", bankNote: "Simulated bank payout settled. No real bank transfer was made." };
  if (s === "OFFRAMP_REJECTED_HELD_AS_USDC") {
    return { status: "SUCCESS", bankNote: "Simulated off-ramp rejected the payout, so the funds stay as USDC in the owner's wallet." };
  }
  const known: PaymentStatus[] = ["PENDING_DEPOSIT", "KNOWN_DEPOSIT_TX", "PROCESSING", "SUCCESS", "REFUNDED", "FAILED", "INCOMPLETE_DEPOSIT"];
  return { status: known.includes(s as PaymentStatus) ? (s as PaymentStatus) : "PENDING_DEPOSIT" };
}

interface SwapDetails {
  amountInFormatted?: string;
  amountOutFormatted?: string;
  refundReason?: string;
  originChainTxHashes?: Array<{ hash: string; explorerUrl: string }>;
  destinationChainTxHashes?: Array<{ hash: string; explorerUrl: string }>;
}

function applySwapDetails(p: Payment, d: SwapDetails | null | undefined): void {
  if (!d) return;
  const origin = d.originChainTxHashes?.[0];
  const dest = d.destinationChainTxHashes?.[0];
  if (origin) {
    p.originTxUrl = origin.explorerUrl;
    p.txHash ??= origin.hash;
  }
  if (dest) p.destinationTxUrl = dest.explorerUrl;
  if (d.refundReason) p.refundReason = d.refundReason;
  if (p.status === "SUCCESS" && d.amountOutFormatted) p.received = d.amountOutFormatted;
}

export interface ResolveQuery {
  name: string;
  chain: string;
  /** Token picked in the form. */
  token: TokenInfo;
  /** Decimal amount, e.g. "2.5". */
  amount: string;
  refundAddress: string;
}

export const api = {
  /** Routable tokens on the chains this app maps. */
  async tokens(): Promise<TokenInfo[]> {
    const { status, body } = await request("/v1/tokens");
    if (status !== 200 || !Array.isArray(body)) throw new Error("The resolver did not return a token list.");
    const order = new Map(CHAINS.map((c, i) => [c.key, i]));
    const out: TokenInfo[] = [];
    for (const t of body) {
      const chain = chainByOneClick(String(t.blockchain));
      if (!chain || !t.assetId) continue;
      out.push({
        chain: chain.key,
        chainName: chain.name,
        token: String(t.symbol),
        assetId: String(t.assetId),
        decimals: Number(t.decimals),
        priceUsd: typeof t.price === "number" ? t.price : undefined,
        contractAddress: t.contractAddress || undefined,
      });
    }
    return out.sort((a, b) => (order.get(a.chain) ?? 0) - (order.get(b.chain) ?? 0) || a.token.localeCompare(b.token));
  },

  async resolve(q: ResolveQuery): Promise<ResolveResponse> {
    const parsed = normalizeName(q.name, settings.suffix);
    if (!parsed.ok) return fail("NAME_INVALID", `Not a valid name: ${parsed.reason}.`);
    const name = parsed.name;

    let base: bigint;
    try {
      base = parseUnits(q.amount, q.token.decimals);
    } catch (err) {
      return fail("INVALID_REQUEST", (err as Error).message);
    }
    if (base <= 0n) return fail("INVALID_REQUEST", "Amount must be more than zero.");
    // The resolver substitutes a hard-coded refund address when none is given, so always send one.
    if (!q.refundAddress) return fail("INVALID_REQUEST", "Enter a refund address first.");

    const params = new URLSearchParams({
      name,
      chain: q.chain,
      token: q.token.assetId,
      amount: base.toString(),
      refund_to: q.refundAddress,
    });
    const { status, body } = await request(`/v1/resolve?${params}`);

    if (status !== 200) {
      const d = body?.detail ?? {};
      const code = String(d.error ?? "");
      const message = String(d.message ?? d.reason ?? code ?? `Request failed (${status}).`);
      if (code === "NAME_NOT_FOUND") return fail("NAME_NOT_FOUND", `"${name}" is not registered.`);
      if (code === "NO_RECORD") {
        return fail("NO_RECORD", `"${name}" has no wallet on ${chainName(q.chain)} and has not set a routed destination.`);
      }
      if (code === "NO_ROUTE") return fail("NO_ROUTE", message, parseSupported(d.supported));
      if (code === "UNVERIFIED_OR_STALE_RECORD") {
        return fail("NO_RECORD", `The wallet "${name}" connected on ${chainName(q.chain)} has an invalid or outdated proof, so it cannot be paid.`);
      }
      return fail(status >= 500 ? "UPSTREAM_ERROR" : "INVALID_REQUEST", message);
    }

    const r = body.resolved ?? {};
    if (body.mode === "direct") {
      return {
        ok: true,
        mode: "direct",
        name,
        chain: q.chain,
        token: q.token.token,
        address: String(r.address),
        destination: { type: "wallet", chain: q.chain, chainName: chainName(q.chain), token: q.token.token, address: String(r.address) },
        amountIn: q.amount,
        estReceive: { amount: q.amount, token: q.token.token },
        warnings: [`This is ${name}'s own wallet on ${chainName(q.chain)}.`],
      };
    }

    // Routed or bank. Without a live 1Click quote the resolver falls back to a
    // derived address that nothing sweeps, so never show that as payable.
    if (r.provider !== LIVE_PROVIDER || !r.depositAddress || !r.expiresAt) {
      return fail("NO_ROUTE", "The routing service did not return a live quote for this payment. Try again, or pick another token.");
    }
    const warnings: string[] = Array.isArray(r.warnings) ? r.warnings.map(String) : [];
    if (warnings.some((w) => w.startsWith("BELOW_MINIMUM")) || BigInt(r.minAmount ?? 0) > base) {
      return fail("AMOUNT_TOO_LOW", `Amount is too low to route. Send at least ${formatUnits(String(r.minAmount), q.token.decimals)} ${q.token.token}.`);
    }

    const dest = splitAsset(body.policy?.destinationAsset);
    const destChain = String(r.outChainId ?? dest.chain);
    const isBank = body.mode === "bank";
    const destination: DestinationSummary = {
      type: isBank ? "bank" : "crypto",
      chain: destChain,
      chainName: chainName(destChain),
      token: String(r.outToken ?? dest.token),
      ...(isBank ? { provider: String(body.policy?.payout?.provider ?? "") } : {}),
    };
    if (!isBank) {
      // The owner's address is on-chain, not in the resolver's answer; read it for display.
      try {
        destination.address = (await registry.getRecord(name))?.policy?.payout.address;
      } catch {
        /* display only */
      }
    }

    const shown = [
      `Send only ${q.token.token} on ${chainName(q.chain)} to this address.`,
      "This address is for this payment only. Do not save or reuse it.",
    ];
    if (r.depositMemo) shown.push("Include the memo, or the deposit will not be credited.");
    if (isBank) shown.push("Bank payout is simulated in this demo.");

    return {
      ok: true,
      mode: "routed",
      name,
      chain: q.chain,
      token: q.token.token,
      address: String(r.depositAddress),
      ...(r.depositMemo ? { memo: String(r.depositMemo) } : {}),
      destination,
      amountIn: q.amount,
      estReceive: { amount: String(r.estReceiveFormatted), token: destination.token },
      expiresAt: String(r.expiresAt),
      timeEstimateSec: Number(r.timeEstimateSeconds) || undefined,
      paymentId: String(r.depositAddress),
      warnings: shown,
    };
  },

  /** Tells the resolver (and 1Click) about the deposit transaction. */
  async reportDeposit(depositAddress: string, txHash: string, memo?: string): Promise<void> {
    await request("/v1/deposit/submit", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ depositAddress, txHash, ...(memo ? { memo } : {}) }),
    });
  },

  /** Refreshes a payment from the live swap status. */
  async refresh(p: Payment, depositAddress: string, memo?: string): Promise<Payment> {
    const q = new URLSearchParams({ depositAddress });
    if (memo) q.set("depositMemo", memo);
    const { status, body } = await request(`/v1/status?${q}`);
    if (status !== 200 || !body) return p;
    const next: Payment = { ...p, ...toStatus(body.status), updatedAt: new Date().toISOString() };
    applySwapDetails(next, body.swapDetails);
    return next;
  },

  /**
   * Payments the resolver has tracked for a name. It keeps them in memory, so
   * the list empties when the resolver restarts.
   */
  async payments(name: string): Promise<Array<Payment & { depositAddress: string }>> {
    const { status, body } = await request(`/v1/dashboard/${encodeURIComponent(name)}`);
    if (status !== 200 || !Array.isArray(body?.payments)) return [];
    const out = body.payments.map((e: any) => {
      const src = splitAsset(e.sourceAsset);
      const dst = splitAsset(e.destinationAsset);
      const p: Payment & { depositAddress: string } = {
        id: String(e.depositAddress),
        depositAddress: String(e.depositAddress),
        name,
        ...toStatus(e.status),
        originChain: chainByKey(src.chain)?.key ?? src.chain,
        originToken: src.token,
        amountIn: e.swapDetails?.amountInFormatted ?? "",
        destChain: dst.chain,
        destToken: dst.token,
        payoutType: e.mode === "bank" ? "bank" : "crypto",
        estReceive: String(e.estReceive ?? ""),
        txHash: e.txHash || undefined,
        expiresAt: "",
        createdAt: new Date(Number(e.createdAt) * 1000).toISOString(),
        updatedAt: new Date(Number(e.updatedAt) * 1000).toISOString(),
      };
      applySwapDetails(p, e.swapDetails);
      return p;
    });
    return out.sort((a: Payment, b: Payment) => b.createdAt.localeCompare(a.createdAt));
  },
};
