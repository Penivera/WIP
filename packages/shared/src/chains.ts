/**
 * The one place that maps the contract's CAIP-2 chain keys to 1Click blockchain IDs.
 * The contract accepts only the eip155, solana, bip122 and near namespaces.
 */

export type Namespace = "eip155" | "solana" | "bip122" | "near";

export interface ChainInfo {
  /** CAIP-2 key, as stored in the contract. */
  key: string;
  namespace: Namespace;
  name: string;
  /** `blockchain` value in 1Click's /v0/tokens. */
  oneClick: string;
  /** EVM chain ID, for wallet calls. */
  evmChainId?: number;
  /** Minutes a routed deposit address stays usable; slow chains need longer. */
  quoteMinutes: number;
}

const evm = (id: number, name: string, oneClick: string): ChainInfo => ({
  key: `eip155:${id}`,
  namespace: "eip155",
  name,
  oneClick,
  evmChainId: id,
  quoteMinutes: 10,
});

export const CHAINS: readonly ChainInfo[] = [
  evm(1, "Ethereum", "eth"),
  evm(8453, "Base", "base"),
  evm(42161, "Arbitrum", "arb"),
  evm(10, "Optimism", "op"),
  evm(137, "Polygon", "pol"),
  evm(56, "BNB Chain", "bsc"),
  evm(43114, "Avalanche", "avax"),
  evm(100, "Gnosis", "gnosis"),
  {
    key: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
    namespace: "solana",
    name: "Solana",
    oneClick: "sol",
    quoteMinutes: 10,
  },
  { key: "near:mainnet", namespace: "near", name: "NEAR", oneClick: "near", quoteMinutes: 10 },
  {
    key: "bip122:000000000019d6689c085ae165831e93",
    namespace: "bip122",
    name: "Bitcoin",
    oneClick: "btc",
    quoteMinutes: 90,
  },
];

/** Lowercases the namespace, as the contract's `normalize_chain_key` does. */
export function normalizeChainKey(key: string): string {
  const i = key.indexOf(":");
  if (i < 0) return key;
  return key.slice(0, i).toLowerCase() + key.slice(i);
}

export function chainByKey(key: string): ChainInfo | undefined {
  const k = normalizeChainKey(key);
  return CHAINS.find((c) => c.key === k);
}

export function chainByOneClick(blockchain: string): ChainInfo | undefined {
  return CHAINS.find((c) => c.oneClick === blockchain);
}

export function namespaceOf(key: string): Namespace | undefined {
  const ns = key.split(":")[0]?.toLowerCase();
  return ns === "eip155" || ns === "solana" || ns === "bip122" || ns === "near" ? ns : undefined;
}

export function chainName(key: string): string {
  return chainByKey(key)?.name ?? key;
}
