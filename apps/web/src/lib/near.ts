/**
 * Write side of the contract adapter: the owner's NEAR wallet signs the change
 * methods. Reads go through the backend. Contract shapes come from @unam/shared.
 */
import { NearConnector } from "@hot-labs/near-connect";
import {
  CONTRACT_METHODS,
  parseContractError,
  proofMessage,
  toConnectChainArgs,
  toRawPolicy,
  type Payout,
} from "@unam/shared";
import { ref } from "vue";
import { settings } from "./config";

const GAS = "30000000000000"; // 30 TGas per call
const NO_DEPOSIT = "0";

export const nearAccount = ref<string | null>(null);

let connector: NearConnector | undefined;
/** Contract's policy delay; set by the owner view once it has read it. */
export const changeDelayMs = ref<number | null>(null);

export async function initNear(): Promise<void> {
  if (connector) return;
  connector = new NearConnector({
    network: settings.network,
    features: settings.network === "testnet" ? { testnet: true } : {},
  });
  connector.on("wallet:signIn", (e) => {
    nearAccount.value = e.accounts[0]?.accountId ?? null;
  });
  connector.on("wallet:signOut", () => {
    nearAccount.value = null;
  });
  try {
    const { accounts } = await connector.getConnectedWallet();
    nearAccount.value = accounts[0]?.accountId ?? null;
  } catch {
    nearAccount.value = null; // nobody signed in yet
  }
}

export async function connectNear(): Promise<void> {
  if (!connector) throw new Error("NEAR wallet is not ready yet.");
  await connector.connect();
}

export async function disconnectNear(): Promise<void> {
  await connector?.disconnect();
  nearAccount.value = null;
}

/** Turns a wallet or contract failure into a message a person can act on. */
export function explainError(err: unknown): string {
  const text = err instanceof Error ? err.message : typeof err === "string" ? err : JSON.stringify(err);
  const parsed = parseContractError(text);
  const known: Record<string, string> = {
    NAME_TAKEN: "That name is already taken.",
    NAME_INVALID: "That name is not allowed",
    NAME_NOT_FOUND: "The contract has no record for that name.",
    NOT_OWNER: "The connected NEAR account does not own this name.",
    POLICY_INVALID: "The contract rejected the destination",
    ADDRESS_INVALID: "The contract rejected the address",
    CHAIN_KEY_INVALID: "The contract does not support that chain",
    CHANGE_PENDING: "The change delay has not passed yet.",
    NO_PENDING_CHANGE: "There is no pending change to apply.",
  };
  if (parsed && known[parsed.code]) {
    const base = known[parsed.code]!;
    return base.endsWith(".") ? base : `${base}${parsed.detail ? `: ${parsed.detail}` : ""}.`;
  }
  return text;
}

async function send(calls: Array<{ method: string; args: object }>): Promise<void> {
  if (!connector) throw new Error("NEAR wallet is not ready yet.");
  if (!nearAccount.value) throw new Error("Connect your NEAR wallet first.");
  const wallet = await connector.wallet();
  await wallet.signAndSendTransaction({
    signerId: nearAccount.value,
    receiverId: settings.contractId,
    actions: calls.map((c) => ({
      type: "FunctionCall" as const,
      params: { methodName: c.method, args: c.args, gas: GAS, deposit: NO_DEPOSIT },
    })),
  });
}

export const registryWrite = {
  claim: (name: string) => send([{ method: CONTRACT_METHODS.claim, args: { name } }]),

  /**
   * Queues the policy. If the contract has no change delay, the same transaction
   * also applies it; otherwise it stays pending until `applyPolicy` is called
   * after the delay.
   */
  setPolicy(name: string, forceDefault: boolean, payout: Payout): Promise<void> {
    const calls: Array<{ method: string; args: object }> = [
      { method: CONTRACT_METHODS.setPolicy, args: { name, policy: toRawPolicy(forceDefault, payout) } },
    ];
    if (changeDelayMs.value === 0) calls.push({ method: CONTRACT_METHODS.applyPolicy, args: { name } });
    return send(calls);
  },

  applyPolicy: (name: string) => send([{ method: CONTRACT_METHODS.applyPolicy, args: { name } }]),

  /**
   * Connects a wallet on a chain. `sign` asks that wallet to sign the proof
   * message; `generation` is the name's current record generation.
   */
  async setRecord(
    name: string,
    chain: string,
    address: string,
    generation: number,
    sign: (message: string) => Promise<Uint8Array>,
  ): Promise<void> {
    if (!nearAccount.value) throw new Error("Connect your NEAR wallet first.");
    const proof = await sign(
      proofMessage({ fullName: name + settings.suffix, owner: nearAccount.value, chain, address, generation }),
    );
    await send([{ method: CONTRACT_METHODS.setRecord, args: toConnectChainArgs(name, chain, address, proof) }]);
  },
};
