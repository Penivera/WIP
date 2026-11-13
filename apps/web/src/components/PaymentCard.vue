<script setup lang="ts">
import { chainName, type Payment } from "@unam/shared";
import { computed } from "vue";

const props = defineProps<{ payment: Payment; compact?: boolean }>();

const LABELS: Record<Payment["status"], string> = {
  PENDING_DEPOSIT: "Waiting for deposit",
  KNOWN_DEPOSIT_TX: "Deposit seen",
  PROCESSING: "Converting and delivering",
  SUCCESS: "Delivered",
  REFUNDED: "Refunded to sender",
  FAILED: "Failed",
  INCOMPLETE_DEPOSIT: "Deposit was too small",
  EXPIRED: "Expired unpaid",
};

const tone = computed(() => {
  const s = props.payment.status;
  if (s === "SUCCESS") return "ok";
  if (s === "FAILED" || s === "REFUNDED" || s === "INCOMPLETE_DEPOSIT") return "bad";
  if (s === "EXPIRED") return "";
  return "warn";
});
</script>

<template>
  <div>
    <div>
      <span class="badge" :class="tone">{{ LABELS[payment.status] }}</span>
      <span class="muted"> · {{ new Date(payment.createdAt).toLocaleString() }}</span>
    </div>
    <dl class="kv" style="margin-top: 8px">
      <dt>Sent</dt>
      <dd>{{ payment.amountIn }} {{ payment.originToken }} on {{ chainName(payment.originChain) }}</dd>
      <dt>{{ payment.received ? "Received" : "Expected" }}</dt>
      <dd>{{ payment.received ?? payment.estReceive }} {{ payment.destToken }} on {{ chainName(payment.destChain) }}</dd>
      <template v-if="payment.refundReason">
        <dt>Refund reason</dt>
        <dd>{{ payment.refundReason }}</dd>
      </template>
      <template v-if="payment.originTxUrl || payment.destinationTxUrl">
        <dt>Transactions</dt>
        <dd>
          <a v-if="payment.originTxUrl" :href="payment.originTxUrl" target="_blank" rel="noopener">deposit</a>
          <span v-if="payment.originTxUrl && payment.destinationTxUrl"> · </span>
          <a v-if="payment.destinationTxUrl" :href="payment.destinationTxUrl" target="_blank" rel="noopener">delivery</a>
        </dd>
      </template>
    </dl>
    <p v-if="payment.bankNote" class="note warn">{{ payment.bankNote }}</p>
    <p v-else-if="payment.bankPayout" class="note warn">
      Simulated bank payout: {{ payment.bankPayout.amount }} {{ payment.bankPayout.currency }} via
      {{ payment.bankPayout.provider }} at {{ payment.bankPayout.rate }}. No real bank transfer was made.
    </p>
    <p v-else-if="payment.payoutType === 'bank' && !compact" class="note warn">
      Bank payout is simulated and runs after the conversion succeeds.
    </p>
  </div>
</template>
