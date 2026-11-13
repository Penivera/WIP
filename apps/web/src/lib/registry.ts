/**
 * Read side of the contract adapter: free view calls straight to NEAR RPC.
 * (The write side, signed by the owner's wallet, is in near.ts.)
 */
import {
  CONTRACT_METHODS,
  fromRawRecord,
  parseContractError,
  stripSuffix,
  type NameRecord,
  type RawRecord,
  type SymbolOf,
} from "@unam/shared";
import { settings } from "./config";

interface RpcResponse {
  result?: { result?: number[]; error?: string };
  error?: unknown;
}

async function view<T>(method: string, args: object): Promise<T> {
  let res: Response;
  try {
    res = await fetch(settings.rpcUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "unam-web",
        method: "query",
        params: {
          request_type: "call_function",
          // optimistic, so a change the owner just signed shows on the next read
          finality: "optimistic",
          account_id: settings.contractId,
          method_name: method,
          args_base64: btoa(JSON.stringify(args)),
        },
      }),
    });
  } catch {
    throw new Error("Cannot reach NEAR RPC. Check your connection and try again.");
  }
  if (!res.ok) throw new Error(`NEAR RPC returned HTTP ${res.status}.`);
  const body = (await res.json()) as RpcResponse;
  const failure = body.result?.error ?? (body.error ? JSON.stringify(body.error) : undefined);
  if (failure) {
    const parsed = parseContractError(failure);
    throw new Error(parsed ? `${parsed.code}${parsed.detail ? `: ${parsed.detail}` : ""}` : failure);
  }
  const bytes = body.result?.result;
  if (!bytes) throw new Error(`${method} returned no result.`);
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(bytes))) as T;
}

export const registry = {
  /** Returns null when the name is unclaimed. `name` is the bare, normalized name. */
  async getRecord(name: string, symbolOf?: SymbolOf): Promise<NameRecord | null> {
    const raw = await view<RawRecord | null>(CONTRACT_METHODS.getRecord, { name });
    return raw ? fromRawRecord(name, raw, symbolOf) : null;
  },

  async namesOf(account: string): Promise<string[]> {
    const names: string[] = [];
    const limit = 100; // the contract caps a page at 100
    for (let skip = 0; ; skip += limit) {
      const page = await view<string[]>(CONTRACT_METHODS.namesOf, { account, skip, limit });
      names.push(...page.map((n) => stripSuffix(n, settings.suffix)));
      if (page.length < limit) break;
    }
    return names;
  },

  changeDelayMs: () => view<number>(CONTRACT_METHODS.changeDelay, {}),
};
