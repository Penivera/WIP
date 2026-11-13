/** Public settings: Vite env defaults, overridable by the embedding page. */
const env = import.meta.env;

export interface Settings {
  /** Python resolver base URL. */
  apiUrl: string;
  /** Registry contract account. */
  contractId: string;
  network: "testnet" | "mainnet";
  rpcUrl: string;
  suffix: string;
}

export const settings: Settings = {
  apiUrl: (env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, ""),
  contractId: env.VITE_CONTRACT_ID ?? "unam-registry.testnet",
  network: env.VITE_NEAR_NETWORK === "mainnet" ? "mainnet" : "testnet",
  rpcUrl: env.VITE_NEAR_RPC_URL ?? "https://rpc.testnet.near.org",
  suffix: env.VITE_NAME_SUFFIX ?? ".uni",
};

export function configure(overrides: Partial<Settings>): void {
  Object.assign(settings, overrides);
  settings.apiUrl = settings.apiUrl.replace(/\/$/, "");
}
