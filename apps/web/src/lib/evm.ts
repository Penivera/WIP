/** Minimal injected EVM wallet (EIP-1193) helper: connect, switch chain, send, sign. */
import { parseUnits } from "@unam/shared";

interface Eip1193 {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
}

function provider(): Eip1193 {
  const eth = (window as unknown as { ethereum?: Eip1193 }).ethereum;
  if (!eth) throw new Error("No EVM wallet found in this browser. Install one, or pay by copying the address.");
  return eth;
}

export const hasEvmWallet = () => Boolean((window as unknown as { ethereum?: unknown }).ethereum);

export async function connectEvm(): Promise<string> {
  const accounts = (await provider().request({ method: "eth_requestAccounts" })) as string[];
  if (!accounts[0]) throw new Error("The wallet returned no account.");
  return accounts[0];
}

async function switchChain(chainId: number): Promise<void> {
  const hex = "0x" + chainId.toString(16);
  const current = (await provider().request({ method: "eth_chainId" })) as string;
  if (current.toLowerCase() === hex) return;
  await provider().request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
}

const pad32 = (hex: string) => hex.replace(/^0x/, "").toLowerCase().padStart(64, "0");

/**
 * Sends `amount` of a token to `to` and returns the transaction hash.
 * With `contractAddress` it is an ERC-20 `transfer`; without, the chain's native coin.
 */
export async function sendEvmPayment(args: {
  from: string;
  to: string;
  chainId: number;
  amount: string;
  decimals: number;
  contractAddress?: string;
}): Promise<string> {
  await switchChain(args.chainId);
  const value = parseUnits(args.amount, args.decimals);
  const tx = args.contractAddress
    ? {
        from: args.from,
        to: args.contractAddress,
        // transfer(address,uint256)
        data: "0xa9059cbb" + pad32(args.to) + pad32(value.toString(16)),
      }
    : { from: args.from, to: args.to, value: "0x" + value.toString(16) };
  return (await provider().request({ method: "eth_sendTransaction", params: [tx] })) as string;
}

/** personal_sign; returns the 65-byte signature. */
export async function signEvmMessage(account: string, message: string): Promise<Uint8Array> {
  const hexMsg = "0x" + Array.from(new TextEncoder().encode(message), (b) => b.toString(16).padStart(2, "0")).join("");
  const sig = (await provider().request({ method: "personal_sign", params: [hexMsg, account] })) as string;
  const h = sig.replace(/^0x/, "");
  return Uint8Array.from(h.match(/.{2}/g) ?? [], (b) => parseInt(b, 16));
}
