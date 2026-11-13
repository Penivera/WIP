<script setup lang="ts">
import {
  CHAINS,
  FINAL_STATUSES,
  chainByKey,
  isValidAddress,
  type Payment,
  type ResolveResponse,
  type TokenInfo,
} from "@unam/shared";
import QRCode from "qrcode";
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import PaymentCard from "../components/PaymentCard.vue";
import { api } from "../lib/api";
import { connectEvm, hasEvmWallet, sendEvmPayment } from "../lib/evm";
import { nav } from "../lib/nav";

const props = defineProps<{ initialName: string }>();

const name = ref(props.initialName);
const chain = ref("eip155:8453");
const token = ref("USDC");
const amount = ref("");
const refundAddress = ref("");

const tokens = ref<TokenInfo[]>([]);
const loadError = ref("");

const busy = ref(false);
const error = ref("");
const result = ref<ResolveResponse | null>(null);
const qr = ref("");
const copied = ref(false);

const evmAccount = ref("");
const sending = ref(false);
const manualTx = ref("");
const payment = ref<Payment | null>(null);

const now = ref(Date.now());
let clock: number | undefined;
let poll: number | undefined;

const screen = nav.current;
const chainInfo = computed(() => chainByKey(chain.value));
const chainTokens = computed(() => tokens.value.filter((t) => t.chain === chain.value));
const tokenInfo = computed(() => chainTokens.value.find((t) => t.token === token.value));
const isEvm = computed(() => chainInfo.value?.namespace === "eip155");

const ok = computed(() => (result.value?.ok ? result.value : null));
const failed = computed(() => (result.value && !result.value.ok ? result.value : null));

const secondsLeft = computed(() => {
  const at = ok.value?.expiresAt;
  if (!at) return null;
  return Math.max(0, Math.floor((Date.parse(at) - now.value) / 1000));
});
const expired = computed(() => secondsLeft.value === 0 && !payment.value?.txHash);
const countdown = computed(() => {
  const s = secondsLeft.value ?? 0;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
});
const done = computed(() => (payment.value ? FINAL_STATUSES.includes(payment.value.status) : false));
const refundOk = computed(() => isValidAddress(chain.value, refundAddress.value));
const canSend = computed(() => Boolean(ok.value?.address) && !expired.value && !payment.value?.txHash);
const tracked = computed(() => Boolean(payment.value && (payment.value.txHash || payment.value.status !== "PENDING_DEPOSIT")));

watch(chain, () => {
  if (!chainTokens.value.some((t) => t.token === token.value)) {
    token.value = chainTokens.value.find((t) => t.token === "USDC")?.token ?? chainTokens.value[0]?.token ?? "";
  }
  // A refund address belongs to one kind of chain. Drop it if it can't be used
  // on the new chain, and bring the connected wallet back when it can.
  if (refundAddress.value && !refundOk.value) refundAddress.value = "";
  if (!refundAddress.value && isEvm.value && evmAccount.value) refundAddress.value = evmAccount.value;
  reset();
});
watch([name, token, amount, refundAddress], reset);

/** Clears the last answer. The cards that showed it are no longer reachable. */
function reset() {
  result.value = null;
  payment.value = null;
  error.value = "";
  qr.value = "";
  manualTx.value = "";
  stopPolling();
  if (screen.value === "pay") nav.dropForward();
}

function stopPolling() {
  if (poll) window.clearInterval(poll);
  poll = undefined;
}

function startPolling() {
  stopPolling();
  const tick = async () => {
    const r = ok.value;
    if (!payment.value || !r?.address) return;
    try {
      payment.value = await api.refresh(payment.value, r.address, r.memo);
      if (FINAL_STATUSES.includes(payment.value.status)) stopPolling();
    } catch {
      /* keep trying */
    }
  };
  poll = window.setInterval(tick, 5000);
}

/** Records the deposit transaction, tells the resolver, and moves to the status card. */
async function recordDeposit(txHash: string) {
  const r = ok.value;
  if (!payment.value || !r?.address) return;
  payment.value = { ...payment.value, txHash, status: "KNOWN_DEPOSIT_TX" };
  nav.go("pay-status");
  await api.reportDeposit(r.address, txHash, r.memo);
}

async function connect() {
  error.value = "";
  try {
    evmAccount.value = await connectEvm();
    if (isEvm.value && !refundAddress.value) refundAddress.value = evmAccount.value;
  } catch (err) {
    error.value = (err as Error).message;
  }
}

async function getDetails() {
  const onDetails = screen.value === "pay-details";
  result.value = null;
  payment.value = null;
  error.value = "";
  qr.value = "";
  manualTx.value = "";
  stopPolling();
  busy.value = true;
  try {
    const info = tokenInfo.value;
    if (!info) throw new Error("Pick a token.");
    const r = await api.resolve({
      name: name.value,
      chain: chain.value,
      token: info,
      amount: amount.value,
      refundAddress: refundAddress.value,
    });
    result.value = r;
    if (r.ok && r.address) {
      qr.value = await QRCode.toDataURL(r.address, { margin: 1, width: 176 });
      if (r.mode === "routed" && r.expiresAt) {
        const at = new Date().toISOString();
        payment.value = {
          id: r.address,
          name: r.name,
          status: "PENDING_DEPOSIT",
          originChain: r.chain,
          originToken: r.token,
          amountIn: r.amountIn,
          destChain: r.destination.chain,
          destToken: r.destination.token,
          payoutType: r.destination.type === "bank" ? "bank" : "crypto",
          estReceive: r.estReceive.amount,
          expiresAt: r.expiresAt,
          createdAt: at,
          updatedAt: at,
        };
        startPolling();
      }
    }
    if (!onDetails) nav.go("pay-details");
  } catch (err) {
    error.value = (err as Error).message;
  } finally {
    busy.value = false;
  }
}

async function copyAddress() {
  if (!ok.value?.address) return;
  await navigator.clipboard.writeText(ok.value.address);
  copied.value = true;
  window.setTimeout(() => (copied.value = false), 1500);
}

async function payWithWallet() {
  const r = ok.value;
  const info = tokenInfo.value;
  const evmChainId = chainInfo.value?.evmChainId;
  if (!r?.address || !evmChainId) return;
  if (!canSend.value) return; // never send to an expired address
  error.value = "";
  sending.value = true;
  try {
    if (!evmAccount.value) evmAccount.value = await connectEvm();
    if (!info) throw new Error("This token is not in the routing list, so send it from your wallet app instead.");
    const txHash = await sendEvmPayment({
      from: evmAccount.value,
      to: r.address,
      chainId: evmChainId,
      amount: r.amountIn,
      decimals: info.decimals,
      contractAddress: info.contractAddress,
    });
    manualTx.value = txHash;
    await recordDeposit(txHash);
  } catch (err) {
    error.value = (err as { message?: string }).message ?? "The wallet rejected the transaction.";
  } finally {
    sending.value = false;
  }
}

async function reportManual() {
  if (!manualTx.value.trim()) return;
  error.value = "";
  try {
    await recordDeposit(manualTx.value.trim());
  } catch (err) {
    error.value = (err as Error).message;
  }
}

onMounted(async () => {
  clock = window.setInterval(() => (now.value = Date.now()), 1000);
  try {
    tokens.value = await api.tokens();
  } catch (err) {
    loadError.value = (err as Error).message;
  }
});
onUnmounted(() => {
  if (clock) window.clearInterval(clock);
  stopPolling();
});
</script>

<template>
  <!-- Card 1: what to pay -->
  <form v-if="screen === 'pay'" @submit.prevent="getDetails">
    <p class="lead">Send from the chain and token you have. The owner decides what they receive.</p>
    <p v-if="loadError" class="note bad">{{ loadError }}</p>

    <label for="un-name">Name</label>
    <input id="un-name" v-model.trim="name" placeholder="peni" autocomplete="off" autocapitalize="none" required />

    <div class="row">
      <div>
        <label for="un-chain">You send from</label>
        <select id="un-chain" v-model="chain">
          <option v-for="c in CHAINS" :key="c.key" :value="c.key">{{ c.name }}</option>
        </select>
      </div>
      <div>
        <label for="un-token">Token</label>
        <select id="un-token" v-model="token">
          <option v-for="t in chainTokens" :key="t.assetId" :value="t.token">{{ t.token }}</option>
        </select>
      </div>
    </div>

    <label for="un-amount">Amount</label>
    <input id="un-amount" v-model.trim="amount" inputmode="decimal" placeholder="2.00" required />

    <label for="un-refund">Refund address on {{ chainInfo?.name }}, used if the payment can't complete</label>
    <input id="un-refund" v-model.trim="refundAddress" class="mono" placeholder="Your own address on this chain" autocomplete="off" required />
    <p v-if="refundAddress && !refundOk" class="muted" style="margin-top: 6px">
      Not a valid {{ chainInfo?.name }} address.
    </p>
    <div v-if="isEvm && hasEvmWallet() && !evmAccount" class="actions" style="margin-top: 8px">
      <button type="button" class="btn secondary small" @click="connect">Use my wallet's address</button>
    </div>

    <div class="actions">
      <button type="submit" class="btn block" :disabled="busy || !name || !amount || !refundOk || !tokenInfo">
        {{ busy ? "Checking…" : "Get payment details" }}
      </button>
    </div>
    <p v-if="error" class="note bad">{{ error }}</p>
  </form>

  <!-- Card 2: where to send it, or why it can't be paid -->
  <div v-else-if="screen === 'pay-details'">
    <template v-if="failed">
      <h2>
        <template v-if="failed.error === 'NAME_NOT_FOUND'">Name not found</template>
        <template v-else-if="failed.error === 'NO_RECORD'">Can't be paid on {{ chainInfo?.name }}</template>
        <template v-else-if="failed.error === 'NO_ROUTE'">No route for this payment</template>
        <template v-else-if="failed.error === 'AMOUNT_TOO_LOW'">Amount too low</template>
        <template v-else>Can't get payment details</template>
      </h2>
      <p class="note bad" style="margin-top: 0">{{ failed.message }}</p>
      <template v-if="failed.supported?.length">
        <p class="muted" style="margin: 12px 0 6px">What works instead:</p>
        <p>
          <span v-for="s in failed.supported.slice(0, 30)" :key="s.chain + s.token" class="badge" style="margin: 0 6px 6px 0">
            {{ s.token === "any" ? `any token on ${s.chainName}` : `${s.token} on ${s.chainName}` }}
          </span>
        </p>
      </template>
      <div class="actions">
        <button type="button" class="btn secondary" @click="nav.back()">Change details</button>
      </div>
    </template>

    <template v-else-if="ok">
      <p style="margin-bottom: 10px">
        <span class="badge">{{ ok.mode === "direct" ? "Direct to owner's wallet" : "Routed" }}</span>
      </p>
      <dl class="kv">
        <dt>You send</dt>
        <dd>{{ ok.amountIn }} {{ ok.token }} on {{ chainInfo?.name }}</dd>
        <dt>{{ ok.name }} gets</dt>
        <dd>
          about {{ ok.estReceive.amount }} {{ ok.estReceive.token }} on {{ ok.destination.chainName }}
          <span v-if="ok.minReceive" class="muted">(at least {{ ok.minReceive }})</span>
        </dd>
        <template v-if="ok.destination.type === 'bank'">
          <dt>Destination</dt>
          <dd>Bank payout via {{ ok.destination.provider }} (simulated)</dd>
        </template>
        <template v-else-if="ok.destination.address && ok.mode === 'routed'">
          <dt>Destination</dt>
          <dd class="mono">{{ ok.destination.address }}</dd>
        </template>
        <template v-if="ok.timeEstimateSec">
          <dt>Usually takes</dt>
          <dd>about {{ ok.timeEstimateSec }}s after your deposit confirms</dd>
        </template>
      </dl>

      <template v-if="ok.address && !expired">
        <div class="address">
          <span class="mono">{{ ok.address }}</span>
          <button type="button" class="btn secondary small" @click="copyAddress">{{ copied ? "Copied" : "Copy" }}</button>
        </div>
        <p v-if="ok.memo" class="note warn">Memo (required): <span class="mono">{{ ok.memo }}</span></p>
        <img v-if="qr" :src="qr" class="qr" width="176" height="176" alt="QR code of the payment address" />
        <p v-if="secondsLeft !== null && !payment?.txHash" class="note warn">
          This address expires in {{ countdown }}. Send before then.
        </p>
      </template>
      <p v-else-if="expired" class="note bad">This address has expired. Do not send to it.</p>

      <ul class="hints">
        <li v-for="w in ok.warnings" :key="w">{{ w }}</li>
      </ul>

      <div v-if="ok.address" class="actions">
        <button v-if="expired" type="button" class="btn block" :disabled="busy" @click="getDetails">
          {{ busy ? "Checking…" : "Get new details" }}
        </button>
        <button v-else-if="isEvm && hasEvmWallet()" type="button" class="btn block" :disabled="!canSend || sending" @click="payWithWallet">
          {{ sending ? "Confirm in your wallet…" : `Pay ${ok.amountIn} ${ok.token} with wallet` }}
        </button>
        <button v-if="tracked" type="button" class="btn secondary block" @click="nav.go('pay-status')">View payment status</button>
      </div>

      <template v-if="payment && !payment.txHash && !expired">
        <label for="un-tx">Paid another way? Paste the transaction hash</label>
        <div class="row">
          <input id="un-tx" v-model.trim="manualTx" class="mono" placeholder="0x…" autocomplete="off" />
          <button type="button" class="btn secondary" style="flex: 0 0 auto" :disabled="!manualTx" @click="reportManual">Submit</button>
        </div>
      </template>
      <p v-if="error" class="note bad">{{ error }}</p>
    </template>

    <p v-else class="muted">{{ busy ? "Checking…" : "No payment details yet." }}</p>
  </div>

  <!-- Card 3: follow the payment -->
  <div v-else-if="screen === 'pay-status'">
    <template v-if="payment">
      <PaymentCard :payment="payment" />
      <p v-if="!done" class="muted" style="margin-top: 12px">
        Checking every few seconds. You can close this; the payment continues.
      </p>
      <div v-else class="actions">
        <button type="button" class="btn secondary" @click="amount = ''; nav.go('pay')">Make another payment</button>
      </div>
    </template>
    <p v-else class="muted">No payment in progress.</p>
  </div>
</template>
