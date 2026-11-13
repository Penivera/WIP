/** Public settings, from Vite env with defaults for the testnet deployment. */
const env = import.meta.env;

export const settings = {
  apiUrl: (env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, ""),
  contractId: env.VITE_CONTRACT_ID ?? "unam-registry.testnet",
  network: (env.VITE_NEAR_NETWORK === "mainnet" ? "mainnet" : "testnet") as "testnet" | "mainnet",
  rpcUrl: env.VITE_NEAR_RPC_URL ?? "https://rpc.testnet.near.org",
  suffix: env.VITE_NAME_SUFFIX ?? ".uni",
};
