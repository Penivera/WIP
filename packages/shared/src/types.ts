/** Shapes used by the off-chain code. The contract's own shapes live in contract-map.ts. */

export interface CryptoPayout {
  type: "crypto";
  /** CAIP-2 chain key, e.g. "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d". */
  chain: string;
  /** Token symbol for display, e.g. "USDC". */
  token: string;
  /** 1Click asset ID, taken from /v0/tokens. This is what is stored on-chain. */
  assetId: string;
  /** Destination address as text. */
  address: string;
}

export interface BankPayout extends Omit<CryptoPayout, "type"> {
  type: "bank";
  /** Off-ramp provider ID. */
  provider: string;
  /** Opaque provider reference. No bank details are stored on-chain. */
  reference: string;
  /** Payout currency. Not stored on-chain; supplied by the provider lookup. */
  currency?: string;
}

export type Payout = CryptoPayout | BankPayout;

export interface Policy {
  force_default: boolean;
  payout: Payout;
}

export interface PendingPolicy {
  /** Milliseconds since epoch when the queued change can be applied. */
  effective_at_ms: number;
  /** The queued policy, or null if the change clears the policy. */
  policy: Policy | null;
}

export interface NameRecord {
  /** Bare name without suffix, e.g. "peni". */
  name: string;
  owner: string;
  /** One wallet per chain: CAIP-2 chain key -> address as text. */
  records: Record<string, string>;
  policy: Policy | null;
  /** Policy change waiting for the contract's delay, if any. */
  pending: PendingPolicy | null;
  version: number;
}

export type ResolveErrorCode =
  | "INVALID_REQUEST"
  | "NAME_INVALID"
  | "NAME_NOT_FOUND"
  | "NO_RECORD"
  | "NO_ROUTE"
  | "AMOUNT_TOO_LOW"
  | "UPSTREAM_ERROR";

export interface SupportedAsset {
  chain: string;
  chainName: string;
  token: string;
}

export interface DestinationSummary {
  type: "wallet" | "crypto" | "bank";
  chain: string;
  chainName: string;
  /** Token the owner receives. For a direct wallet this is whatever was sent. */
  token: string;
  /** Owner's address. Omitted for bank payouts, where the address belongs to the provider. */
  address?: string;
  provider?: string;
  currency?: string;
}

export interface ResolveOk {
  ok: true;
  mode: "direct" | "routed";
  name: string;
  chain: string;
  token: string;
  /** Address the sender pays. Null only when live quotes are disabled (dry run). */
  address: string | null;
  /** Memo that must accompany the deposit, when the chain needs one. */
  memo?: string;
  destination: DestinationSummary;
  amountIn: string;
  estReceive: { amount: string; token: string; usd?: string };
  minReceive?: string;
  /** ISO time after which the address must not be used. Routed mode only. */
  expiresAt?: string;
  timeEstimateSec?: number;
  /** ID for tracking this payment (the deposit address). Routed mode with a live quote only. */
  paymentId?: string;
  warnings: string[];
}

export interface ResolveErr {
  ok: false;
  error: ResolveErrorCode;
  message: string;
  /** What the name can accept, where that helps the sender pick again. */
  supported?: SupportedAsset[];
}

export type ResolveResponse = ResolveOk | ResolveErr;

/** 1Click statuses, plus EXPIRED for quotes nobody paid. */
export type PaymentStatus =
  | "PENDING_DEPOSIT"
  | "KNOWN_DEPOSIT_TX"
  | "PROCESSING"
  | "SUCCESS"
  | "REFUNDED"
  | "FAILED"
  | "INCOMPLETE_DEPOSIT"
  | "EXPIRED";

export const FINAL_STATUSES: readonly PaymentStatus[] = [
  "SUCCESS",
  "REFUNDED",
  "FAILED",
  "INCOMPLETE_DEPOSIT",
  "EXPIRED",
];

export interface BankPayoutResult {
  /** Always true: the bank leg is a labelled simulation. */
  simulated: true;
  provider: string;
  reference: string;
  currency: string;
  amount: string;
  rate: string;
  status: "PAID";
  at: string;
}

export interface Payment {
  id: string;
  name: string;
  status: PaymentStatus;
  originChain: string;
  originToken: string;
  amountIn: string;
  destChain: string;
  destToken: string;
  payoutType: "crypto" | "bank";
  estReceive: string;
  received?: string;
  txHash?: string;
  originTxUrl?: string;
  destinationTxUrl?: string;
  refundReason?: string;
  bankPayout?: BankPayoutResult;
  /** Plain-language outcome of the bank leg, when the backend reports one. */
  bankNote?: string;
  expiresAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface TokenInfo {
  chain: string;
  chainName: string;
  token: string;
  assetId: string;
  decimals: number;
  priceUsd?: number;
  /** Token contract on its chain. Absent for a chain's native coin. */
  contractAddress?: string;
}
