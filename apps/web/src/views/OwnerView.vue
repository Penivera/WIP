<script setup lang="ts">
import {
  CHAINS,
  FINAL_STATUSES,
  chainName,
  isValidAddress,
  normalizeName,
  type NameRecord,
  type Payment,
  type Payout,
  type TokenInfo,
} from "@unam/shared";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import PaymentCard from "../components/PaymentCard.vue";
import { api } from "../lib/api";
import { connectEvm, hasEvmWallet, signEvmMessage } from "../lib/evm";
import { settings } from "../lib/config";
import { changeDelayMs, connectNear, disconnectNear, explainError, initNear, nearAccount, registryWrite } from "../lib/near";
import { registry } from "../lib/registry";

const tokens = ref<TokenInfo[]>([]);
const loadError = ref("");

const names = ref<string[]>([]);
const selected = ref("");
const record = ref<NameRecord | null>(null);
const payments = ref<Payment[]>([]);

const busy = ref("");
const error = ref("");
const notice = ref("");

// claim
const newName = ref("");
const nameCheck = computed(() => (newName.value ? normalizeName(newName.value, settings.suffix) : null));

// destination form
const destChain = ref("solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d");
const destToken = ref("USDC");
const destAddress = ref("");
const forceDefault = ref(false);
const payoutType = ref<"crypto" | "bank">("crypto");
const bankProvider = ref("demo-offramp");
const bankReference = ref("");

// direct wallet form
const walletChain = ref("eip155:8453");

const destTokens = computed(() => tokens.value.filter((t) => t.chain === destChain.value));
const destTokenInfo = computed(() => destTokens.value.find((t) => t.token === destToken.value));
const destAddressOk = computed(() => isValidAddress(destChain.value, destAddress.value));
const evmChains = CHAINS.filter((c) => c.namespace === "eip155");
const symbolOf = (assetId: string) => tokens.value.find((t) => t.assetId === assetId)?.token;
const now = ref(Date.now());
const pendingReady = computed(() => (record.value?.pending ? now.value >= record.value.pending.effective_at_ms : false));

let timer: number | undefined;

watch(destChain, () => {
  if (!destTokens.value.some((t) => t.token === destToken.value)) {
    destToken.value = destTokens.value.find((t) => t.token === "USDC")?.token ?? destTokens.value[0]?.token ?? "";
  }
});

async function loadNames() {
  if (!nearAccount.value) {
    names.value = [];
    selected.value = "";
    return;
  }
  names.value = await registry.namesOf(nearAccount.value);
  if (!names.value.includes(selected.value)) selected.value = names.value[0] ?? "";
}

async function loadSelected() {
  if (!selected.value) {
    record.value = null;
    payments.value = [];
    return;
  }
  [record.value, payments.value] = await Promise.all([
    registry.getRecord(selected.value, symbolOf),
    api.payments(selected.value).catch(() => []),
  ]);
}

watch(nearAccount, () => void run("", loadNames));
watch(selected, () => {
  const p = record.value?.name === selected.value ? record.value.policy : null;
  void run("", async () => {
    await loadSelected();
    const policy = record.value?.policy ?? p;
    if (policy) {
      destChain.value = policy.payout.chain;
      destToken.value = policy.payout.token;
      destAddress.value = policy.payout.address;
      forceDefault.value = policy.force_default;
      payoutType.value = policy.payout.type;
      if (policy.payout.type === "bank") {
        bankProvider.value = policy.payout.provider;
        bankReference.value = policy.payout.reference;
      }
    }
  });
});

/** Reloads tracked payments; unfinished ones are first re-checked against the live swap status. */
async function refreshPayments() {
  try {
    const list = await api.payments(selected.value);
    const open = list.filter((p) => !FINAL_STATUSES.includes(p.status)).slice(0, 5);
    await Promise.all(open.map((p) => api.refresh(p, p.depositAddress)));
    payments.value = open.length ? await api.payments(selected.value) : list;
  } catch {
    /* keep the last list */
  }
}

/** Runs an action with shared busy/error handling. */
async function run(label: string, fn: () => Promise<void>, success?: string) {
  if (label) {
    busy.value = label;
    notice.value = "";
  }
  error.value = "";
  try {
    await fn();
    if (success) notice.value = success;
  } catch (err) {
    error.value = explainError(err);
  } finally {
    if (label) busy.value = "";
  }
}

const claim = () =>
  run(
    "claim",
    async () => {
      const n = nameCheck.value;
      if (!n?.ok) throw new Error(n ? `Not a valid name: ${n.reason}.` : "Enter a name.");
      if (await registry.getRecord(n.name)) throw new Error("That name is already taken.");
      await registryWrite.claim(n.name);
      newName.value = "";
      await loadNames();
      selected.value = n.name;
    },
    "Name claimed.",
  );

const saveDestination = () =>
  run(
    "policy",
    async () => {
      const info = destTokenInfo.value;
      if (!info) throw new Error("Pick a token.");
      if (!destAddressOk.value) throw new Error(`That is not a valid ${chainName(destChain.value)} address.`);
      const base = { chain: destChain.value, token: info.token, assetId: info.assetId, address: destAddress.value };
      const payout: Payout =
        payoutType.value === "bank"
          ? { type: "bank", ...base, provider: bankProvider.value, reference: bankReference.value }
          : { type: "crypto", ...base };
      if (payout.type === "bank" && (!payout.provider || !payout.reference)) {
        throw new Error("Bank payout needs a provider and a reference.");
      }
      await registryWrite.setPolicy(selected.value, forceDefault.value, payout);
      await loadSelected();
    },
    changeDelayMs.value === 0
      ? "Destination saved on-chain."
      : "Change queued on-chain. It can be applied once the contract's delay has passed.",
  );

const applyPending = () =>
  run(
    "apply",
    async () => {
      await registryWrite.applyPolicy(selected.value);
      await loadSelected();
    },
    "Change applied.",
  );

const addWallet = () =>
  run(
    "wallet",
    async () => {
      const account = await connectEvm();
      await registryWrite.setRecord(selected.value, walletChain.value, account, (m) => signEvmMessage(account, m));
      await loadSelected();
    },
    "Wallet connected to the name.",
  );

onMounted(async () => {
  try {
    [changeDelayMs.value, tokens.value] = await Promise.all([registry.changeDelayMs(), api.tokens().catch(() => [])]);
    await initNear();
    await loadNames();
    if (!tokens.value.length) loadError.value = `Cannot reach the resolver at ${settings.apiUrl}, so the token list is empty.`;
  } catch (err) {
    loadError.value = (err as Error).message;
  }
  timer = window.setInterval(() => {
    now.value = Date.now();
    if (selected.value && !busy.value) void refreshPayments();
  }, 6000);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <h1>My names</h1>
  <p class="lead">Claim a name and choose what you receive, whatever people send.</p>

  <p v-if="loadError" class="note bad">{{ loadError }}</p>
  <p v-if="changeDelayMs" class="note warn">
    This contract delays destination changes by {{ Math.round(changeDelayMs / 60000) }} minutes. A saved change shows as
    waiting, then needs applying.
  </p>

  <section class="card">
    <h2>NEAR wallet</h2>
    <template v-if="nearAccount">
      <p>Connected as <span class="mono">{{ nearAccount }}</span> on {{ settings.network }}</p>
      <button type="button" class="secondary small" @click="disconnectNear">Disconnect</button>
    </template>
    <button v-else type="button" @click="run('connect', connectNear)">Connect NEAR wallet</button>
  </section>

  <p v-if="error" class="note bad">{{ error }}</p>
  <p v-if="notice" class="note ok">{{ notice }}</p>

  <template v-if="nearAccount">
    <form class="card" @submit.prevent="claim">
      <h2>Claim a name</h2>
      <label for="new">Name (3 to 32 characters: a-z, 0-9 and hyphens)</label>
      <input id="new" v-model.trim="newName" placeholder="peni" autocomplete="off" autocapitalize="none" />
      <p v-if="nameCheck && !nameCheck.ok" class="muted">Not valid: {{ nameCheck.reason }}.</p>
      <div class="actions">
        <button type="submit" :disabled="!nameCheck?.ok || busy === 'claim'">
          {{ busy === "claim" ? "Confirm in your wallet…" : "Claim" }}
        </button>
      </div>
    </form>

    <section class="card">
      <h2>Your names</h2>
      <p v-if="!names.length" class="muted">This account owns no names yet.</p>
      <template v-else>
        <label for="pick">Manage</label>
        <select id="pick" v-model="selected">
          <option v-for="n in names" :key="n" :value="n">{{ n }}</option>
        </select>
        <p class="muted">
          Pay link: <RouterLink :to="`/pay/${selected}`">/pay/{{ selected }}</RouterLink>
        </p>
      </template>
    </section>

    <template v-if="selected && record">
      <section class="card">
        <h2>What you receive</h2>
        <dl v-if="record.policy" class="kv">
          <dt>Current</dt>
          <dd>
            {{ record.policy.payout.token }} on {{ chainName(record.policy.payout.chain) }}
            <span v-if="record.policy.payout.type === 'bank'">, then simulated bank payout via {{ record.policy.payout.provider }}</span>
          </dd>
          <dt>Address</dt>
          <dd class="mono">{{ record.policy.payout.address }}</dd>
          <dt>Route everything</dt>
          <dd>{{ record.policy.force_default ? "Yes" : "No, connected wallets are paid directly" }}</dd>
        </dl>
        <p v-else class="muted">No routed destination yet. Without one, only chains with a connected wallet can pay you.</p>

        <div v-if="record.pending" class="note warn">
          A change is waiting:
          {{ record.pending.policy ? `${record.pending.policy.payout.token} on ${chainName(record.pending.policy.payout.chain)}` : "clear the destination" }}.
          <template v-if="pendingReady">
            It can be applied now.
            <button type="button" class="small" style="margin-left: 8px" :disabled="busy === 'apply'" @click="applyPending">Apply</button>
          </template>
          <template v-else>Ready at {{ new Date(record.pending.effective_at_ms).toLocaleString() }}.</template>
        </div>

        <form @submit.prevent="saveDestination">
          <div class="row">
            <div>
              <label for="dchain">Receive on</label>
              <select id="dchain" v-model="destChain">
                <option v-for="c in CHAINS" :key="c.key" :value="c.key">{{ c.name }}</option>
              </select>
            </div>
            <div>
              <label for="dtoken">Token</label>
              <select id="dtoken" v-model="destToken">
                <option v-for="t in destTokens" :key="t.assetId" :value="t.token">{{ t.token }}</option>
              </select>
            </div>
          </div>

          <label for="ptype">Payout</label>
          <select id="ptype" v-model="payoutType">
            <option value="crypto">To my wallet</option>
            <option value="bank">To my bank (simulated)</option>
          </select>

          <label for="daddr">
            {{ payoutType === "bank" ? "Provider's deposit address (stands in for a real off-ramp)" : "Your address" }}
            on {{ chainName(destChain) }}
          </label>
          <input id="daddr" v-model.trim="destAddress" class="mono" autocomplete="off" />
          <p v-if="destAddress && !destAddressOk" class="muted">Not a valid {{ chainName(destChain) }} address.</p>

          <div v-if="payoutType === 'bank'" class="row">
            <div>
              <label for="prov">Provider ID</label>
              <input id="prov" v-model.trim="bankProvider" autocomplete="off" />
            </div>
            <div>
              <label for="pref">Provider reference (no bank details)</label>
              <input id="pref" v-model.trim="bankReference" placeholder="ref-001" autocomplete="off" />
            </div>
          </div>

          <label><input v-model="forceDefault" type="checkbox" />Route every payment here, even on chains where I have a wallet</label>

          <div class="actions">
            <button type="submit" :disabled="!destAddressOk || !destTokenInfo || busy === 'policy'">
              {{ busy === "policy" ? "Confirm in your wallet…" : "Save destination" }}
            </button>
          </div>
        </form>
      </section>

      <section class="card">
        <h2>Direct wallets</h2>
        <p class="muted">A connected wallet is paid directly on its chain, with no conversion.</p>
        <ul class="plain">
          <li v-for="(addr, key) in record.records" :key="key">
            {{ chainName(String(key)) }}: <span class="mono">{{ addr }}</span>
          </li>
          <li v-if="!Object.keys(record.records).length" class="muted">None connected.</li>
        </ul>
        <label for="wchain">Add an EVM wallet on</label>
        <div class="row">
          <select id="wchain" v-model="walletChain">
            <option v-for="c in evmChains" :key="c.key" :value="c.key">{{ c.name }}</option>
          </select>
          <button type="button" class="secondary" style="flex: 0 0 auto" :disabled="!hasEvmWallet() || busy === 'wallet'" @click="addWallet">
            {{ busy === "wallet" ? "Sign, then confirm…" : "Connect and sign" }}
          </button>
        </div>
        <p v-if="!hasEvmWallet()" class="muted">No EVM wallet found in this browser.</p>
      </section>
    </template>
    <p v-else-if="selected && !record" class="note warn">
      The contract returned no record for “{{ selected }}”.
    </p>

    <section v-if="selected" class="card">
      <h2>Incoming payments</h2>
      <p v-if="!payments.length" class="muted">No routed payments tracked. Direct payments go straight to your wallet and are not listed, and the resolver forgets this list when it restarts.</p>
      <ul v-else class="plain">
        <li v-for="p in payments" :key="p.id"><PaymentCard :payment="p" compact /></li>
      </ul>
    </section>
  </template>
</template>
