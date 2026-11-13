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
import PaymentCard from "../components/PaymentCard.vue";
import { api, type CheckoutChain, type CheckoutSession } from "../lib/api";
import { connectEvm, hasEvmWallet, signEvmMessage } from "../lib/evm";
import { settings } from "../lib/config";
import { changeDelayMs, connectNear, disconnectNear, explainError, initNear, nearAccount, registryWrite } from "../lib/near";
import { nav } from "../lib/nav";
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

const payChains = ref<CheckoutChain[]>([]);
const payChain = ref("eip155:8453");
const payToken = ref("USDC");
const payTokens = computed(() => payChains.value.find((c) => c.chainId === payChain.value)?.tokens ?? []);
const checkout = ref<CheckoutSession | null>(null);
/** Outcome of the last claim, checked against the contract rather than the resolver's word. */
const claimOutcome = ref<{ onChain: boolean; ref: string } | null>(null);

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

const screen = nav.current;
let timer: number | undefined;

function manage(n: string) {
  selected.value = n;
  nav.go("name");
}

/** Clears the last success or error message when moving to another card. */
watch(screen, () => {
  notice.value = "";
  error.value = "";
});

watch(payChain, () => {
  if (!payTokens.value.some((t) => t.symbol === payToken.value)) payToken.value = payTokens.value[0]?.symbol ?? "";
});

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

/** Step 1: check the name is free and get the resolver's price for it. */
const claim = () =>
  run("claim", async () => {
    const n = nameCheck.value;
    if (!n?.ok) throw new Error(n ? `Not a valid name: ${n.reason}.` : "Enter a name.");
    if (!nearAccount.value) throw new Error("Connect your NEAR wallet first.");
    if (await registry.getRecord(n.name)) throw new Error("That name is already taken.");
    claimOutcome.value = null;
    checkout.value = await api.checkoutSession({
      name: n.name,
      owner: nearAccount.value,
      chain: payChain.value,
      token: payToken.value,
    });
    nav.go("claim");
  });

/**
 * Step 2: complete the checkout. Real cross-chain payment is not offered: the
 * resolver's deposit address is not swept by anything, so funds sent to it
 * would be lost. The demo path asks the resolver to claim without payment,
 * then checks the contract to see whether the name really was claimed.
 */
const completeClaim = () =>
  run("complete", async () => {
    const session = checkout.value;
    const n = normalizeName(session?.name ?? "", settings.suffix);
    if (!session || !n.ok) throw new Error("Start the claim again.");
    const done = await api.checkoutSimulate(session.checkoutId);
    const onChain = (await registry.getRecord(n.name))?.owner === nearAccount.value;
    claimOutcome.value = { onChain, ref: done.nearTxHash ?? "" };
    if (onChain) {
      newName.value = "";
      await loadNames();
    }
  });

/** Applies a waiting destination change through the resolver's admin key, then re-reads the contract. */
const approveNow = () =>
  run("apply", async () => {
    await api.approvePolicy(selected.value);
    await loadSelected();
    if (record.value?.pending) {
      throw new Error("The resolver accepted the request, but the change is still waiting on-chain. It needs the contract admin's key to apply it early.");
    }
  }, "Change applied.");

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
      await registryWrite.setRecord(selected.value, walletChain.value, account, record.value?.version ?? 1, (m) =>
        signEvmMessage(account, m),
      );
      await loadSelected();
    },
    "Wallet connected to the name.",
  );

onMounted(async () => {
  try {
    [changeDelayMs.value, tokens.value, payChains.value] = await Promise.all([
      registry.changeDelayMs(),
      api.tokens().catch(() => []),
      api.checkoutChains().catch(() => []),
    ]);
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
  <p v-if="loadError" class="note bad" style="margin: 0 0 12px">{{ loadError }}</p>

  <!-- Card: wallet, owned names, claim -->
  <div v-if="screen === 'names'">
    <p class="lead">Claim a name and choose what you receive, whatever people send.</p>

    <template v-if="nearAccount">
      <p>
        <span class="mono">{{ nearAccount }}</span>
        <span class="muted"> · {{ settings.network }} · </span>
        <a href="#" @click.prevent="disconnectNear">Disconnect</a>
      </p>

      <h2 style="margin-top: 14px">Your names</h2>
      <p v-if="!names.length" class="muted">This account owns no names yet.</p>
      <div v-else class="menu" style="margin-top: 0">
        <button v-for="n in names" :key="n" type="button" @click="manage(n)">
          <span>{{ n }}</span><span class="chev">›</span>
        </button>
      </div>

      <form style="margin-top: 16px" @submit.prevent="claim">
        <h2>Claim a name</h2>
        <label for="un-new">3 to 32 characters: a-z, 0-9 and hyphens</label>
        <input id="un-new" v-model.trim="newName" placeholder="peni" autocomplete="off" autocapitalize="none" />
        <p v-if="nameCheck && !nameCheck.ok" class="muted" style="margin-top: 6px">Not valid: {{ nameCheck.reason }}.</p>
        <div class="row">
          <div>
            <label for="un-pchain">Pay from</label>
            <select id="un-pchain" v-model="payChain">
              <option v-for="c in payChains" :key="c.chainId" :value="c.chainId">{{ c.chainName }}</option>
            </select>
          </div>
          <div>
            <label for="un-ptoken">Token</label>
            <select id="un-ptoken" v-model="payToken">
              <option v-for="t in payTokens" :key="t.symbol" :value="t.symbol">{{ t.symbol }}</option>
            </select>
          </div>
        </div>
        <div class="actions">
          <button type="submit" class="btn block" :disabled="!nameCheck?.ok || !payToken || busy === 'claim'">
            {{ busy === "claim" ? "Checking…" : "See price" }}
          </button>
        </div>
      </form>
    </template>
    <div v-else class="actions">
      <button type="button" class="btn block" @click="run('connect', connectNear)">Connect NEAR wallet</button>
    </div>
  </div>

  <!-- Card: price and completion of a claim -->
  <div v-else-if="screen === 'claim'">
    <template v-if="checkout">
      <h2>{{ checkout.name }}</h2>
      <dl class="kv">
        <dt>Owner</dt>
        <dd class="mono">{{ checkout.owner }}</dd>
        <dt>Price</dt>
        <dd>${{ checkout.priceUsd.toFixed(2) }}</dd>
        <template v-if="checkout.slippageBufferUsd">
          <dt>Buffer</dt>
          <dd>${{ checkout.slippageBufferUsd.toFixed(2) }} for cross-chain execution</dd>
        </template>
        <dt>Total</dt>
        <dd>{{ checkout.amountFormatted }} on {{ checkout.chainName }}</dd>
      </dl>

      <template v-if="!claimOutcome">
        <p class="note warn">
          Demo only. Paying from another chain is not switched on here, because nothing collects funds from the
          resolver's deposit address yet. This button asks the resolver to claim the name without a payment.
        </p>
        <div class="actions">
          <button type="button" class="btn block" :disabled="busy === 'complete'" @click="completeClaim">
            {{ busy === "complete" ? "Claiming…" : "Claim without payment (demo)" }}
          </button>
        </div>
      </template>
      <template v-else-if="claimOutcome.onChain">
        <p class="note ok">Claimed. The contract now lists {{ checkout.name }} under your account.</p>
        <div class="actions">
          <button type="button" class="btn block" @click="manage(checkout.name.replace(settings.suffix, ''))">Manage this name</button>
        </div>
      </template>
      <p v-else class="note bad">
        Not claimed on-chain. The resolver reported success<span v-if="claimOutcome.ref"> ({{ claimOutcome.ref }})</span>,
        but the contract has no record of this name for your account. The resolver is in mock mode or has no key
        allowed to claim, so nothing was written.
      </p>
    </template>
    <p v-else class="muted">Enter a name on the previous card first.</p>
  </div>

  <!-- Card: one name -->
  <div v-else-if="screen === 'name'">
    <template v-if="selected && record">
      <h2>{{ selected }}</h2>
      <dl class="kv">
        <dt>You receive</dt>
        <dd v-if="record.policy">
          {{ record.policy.payout.token }} on {{ chainName(record.policy.payout.chain) }}
          <span v-if="record.policy.payout.type === 'bank'"> → bank (simulated)</span>
        </dd>
        <dd v-else class="muted">Not set</dd>
        <dt>Direct wallets</dt>
        <dd>{{ Object.keys(record.records).length || "None" }}</dd>
      </dl>
      <p v-if="record.pending" class="note warn">A destination change is waiting to be applied.</p>
      <div class="menu">
        <button type="button" @click="nav.go('destination')">
          <span>What you receive<small>Token, chain and address for routed payments</small></span><span class="chev">›</span>
        </button>
        <button type="button" @click="nav.go('wallets')">
          <span>Direct wallets<small>Get paid as-is on chains where you have a wallet</small></span><span class="chev">›</span>
        </button>
        <button type="button" @click="nav.go('payments')">
          <span>Incoming payments<small>{{ payments.length }} tracked</small></span><span class="chev">›</span>
        </button>
      </div>
    </template>
    <p v-else-if="selected" class="note warn" style="margin-top: 0">The contract returned no record for “{{ selected }}”.</p>
    <p v-else class="muted">Pick a name first.</p>
  </div>

  <!-- Card: routed destination -->
  <div v-else-if="screen === 'destination'">
    <template v-if="selected && record">
      <dl v-if="record.policy" class="kv">
        <dt>Current</dt>
        <dd>
          {{ record.policy.payout.token }} on {{ chainName(record.policy.payout.chain) }}
          <span v-if="record.policy.payout.type === 'bank'">, then simulated bank payout via {{ record.policy.payout.provider }}</span>
        </dd>
        <dt>Address</dt>
        <dd class="mono">{{ record.policy.payout.address }}</dd>
        <dt>Route all</dt>
        <dd>{{ record.policy.force_default ? "Yes" : "No, direct wallets are paid as-is" }}</dd>
      </dl>
      <p v-else class="muted">No destination yet. Without one, only chains with a direct wallet can pay {{ selected }}.</p>

      <div v-if="record.pending" class="note warn">
        Waiting:
        {{ record.pending.policy ? `${record.pending.policy.payout.token} on ${chainName(record.pending.policy.payout.chain)}` : "clear the destination" }}.
        <template v-if="pendingReady">
          It can be applied now.
          <button type="button" class="btn small" style="margin-left: 6px" :disabled="busy === 'apply'" @click="applyPending">Apply</button>
        </template>
        <template v-else>
          Ready at {{ new Date(record.pending.effective_at_ms).toLocaleString() }}.
          <button type="button" class="btn secondary small" style="margin-left: 6px" :disabled="busy === 'apply'" @click="approveNow">
            Apply now (admin)
          </button>
        </template>
      </div>
      <p v-else-if="changeDelayMs" class="muted" style="margin-top: 10px">
        Changes take effect {{ Math.round(changeDelayMs / 60000) }} minutes after saving, then need applying.
      </p>

      <form @submit.prevent="saveDestination">
        <div class="row">
          <div>
            <label for="un-dchain">Receive on</label>
            <select id="un-dchain" v-model="destChain">
              <option v-for="c in CHAINS" :key="c.key" :value="c.key">{{ c.name }}</option>
            </select>
          </div>
          <div>
            <label for="un-dtoken">Token</label>
            <select id="un-dtoken" v-model="destToken">
              <option v-for="t in destTokens" :key="t.assetId" :value="t.token">{{ t.token }}</option>
            </select>
          </div>
        </div>

        <label for="un-ptype">Payout</label>
        <select id="un-ptype" v-model="payoutType">
          <option value="crypto">To my wallet</option>
          <option value="bank">To my bank (simulated)</option>
        </select>

        <label for="un-daddr">
          {{ payoutType === "bank" ? "Provider's deposit address (stands in for a real off-ramp)" : "Your address" }}
          on {{ chainName(destChain) }}
        </label>
        <input id="un-daddr" v-model.trim="destAddress" class="mono" autocomplete="off" />
        <p v-if="destAddress && !destAddressOk" class="muted" style="margin-top: 6px">Not a valid {{ chainName(destChain) }} address.</p>

        <div v-if="payoutType === 'bank'" class="row">
          <div>
            <label for="un-prov">Provider ID</label>
            <input id="un-prov" v-model.trim="bankProvider" autocomplete="off" />
          </div>
          <div>
            <label for="un-pref">Reference (no bank details)</label>
            <input id="un-pref" v-model.trim="bankReference" placeholder="ref-001" autocomplete="off" />
          </div>
        </div>

        <label><input v-model="forceDefault" type="checkbox" />Route every payment here, even where I have a direct wallet</label>

        <div class="actions">
          <button type="submit" class="btn block" :disabled="!destAddressOk || !destTokenInfo || busy === 'policy'">
            {{ busy === "policy" ? "Confirm in your wallet…" : "Save destination" }}
          </button>
        </div>
      </form>
    </template>
    <p v-else class="muted">Pick a name first.</p>
  </div>

  <!-- Card: direct wallets -->
  <div v-else-if="screen === 'wallets'">
    <template v-if="selected && record">
      <p class="lead">A connected wallet is paid directly on its chain, with no conversion.</p>
      <ul class="plain">
        <li v-for="(addr, key) in record.records" :key="key">
          {{ chainName(String(key)) }}<br /><span class="mono">{{ addr }}</span>
        </li>
        <li v-if="!Object.keys(record.records).length" class="muted">None connected.</li>
      </ul>
      <label for="un-wchain">Add an EVM wallet on</label>
      <select id="un-wchain" v-model="walletChain">
        <option v-for="c in evmChains" :key="c.key" :value="c.key">{{ c.name }}</option>
      </select>
      <div class="actions">
        <button type="button" class="btn block" :disabled="!hasEvmWallet() || busy === 'wallet'" @click="addWallet">
          {{ busy === "wallet" ? "Sign, then confirm…" : "Connect and sign" }}
        </button>
      </div>
      <p v-if="!hasEvmWallet()" class="muted" style="margin-top: 8px">No EVM wallet found in this browser.</p>
    </template>
    <p v-else class="muted">Pick a name first.</p>
  </div>

  <!-- Card: incoming payments -->
  <div v-else-if="screen === 'payments'">
    <p v-if="!selected" class="muted">Pick a name first.</p>
    <p v-else-if="!payments.length" class="muted">
      No routed payments tracked for {{ selected }}. Direct payments go straight to your wallet and are not listed, and
      the resolver forgets this list when it restarts.
    </p>
    <ul v-else class="plain">
      <li v-for="p in payments" :key="p.id"><PaymentCard :payment="p" compact /></li>
    </ul>
  </div>

  <p v-if="error" class="note bad">{{ error }}</p>
  <p v-if="notice" class="note ok">{{ notice }}</p>
</template>
