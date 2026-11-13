<script setup lang="ts">
/**
 * Address field with a wallet button inside it.
 *
 *   empty                      -> "Connect wallet"
 *   typing, not yet valid      -> shrinks to "+"
 *   a valid address is entered -> hidden, until it stops being valid
 *
 * The button only appears for chains whose wallet this app can talk to
 * (EVM chains through an injected wallet such as MetaMask).
 */
import { isValidAddress, namespaceOf } from "@unam/shared";
import { computed, ref } from "vue";
import { connectEvm, hasEvmWallet } from "../lib/evm";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    /** CAIP-2 key of the chain the address is for. */
    chain: string;
    id: string;
    placeholder?: string;
    required?: boolean;
    /** Set false where the address is not the user's own (e.g. a provider's). */
    wallet?: boolean;
  }>(),
  { placeholder: "", required: false, wallet: true },
);
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const connecting = ref(false);
const error = ref("");
const input = ref<HTMLInputElement | null>(null);

const canConnect = computed(() => props.wallet && namespaceOf(props.chain) === "eip155" && hasEvmWallet());
const state = computed<"full" | "mini" | "hidden">(() => {
  if (!canConnect.value) return "hidden";
  if (!props.modelValue) return "full";
  return isValidAddress(props.chain, props.modelValue) ? "hidden" : "mini";
});

async function connect() {
  error.value = "";
  connecting.value = true;
  try {
    emit("update:modelValue", await connectEvm());
  } catch (err) {
    error.value = (err as { message?: string }).message ?? "The wallet did not connect.";
  } finally {
    connecting.value = false;
    input.value?.focus();
  }
}
</script>

<template>
  <div class="field" :class="state">
    <input
      :id="id"
      ref="input"
      class="mono"
      :value="modelValue"
      :placeholder="placeholder"
      :required="required"
      autocomplete="off"
      spellcheck="false"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value.trim())"
    />
    <!-- mousedown.prevent keeps the input focused, so the button is still there when the click lands -->
    <button
      v-if="state !== 'hidden'"
      type="button"
      class="inl"
      :disabled="connecting"
      :aria-label="state === 'mini' ? 'Use my wallet\'s address instead' : undefined"
      :title="state === 'mini' ? 'Use my wallet\'s address instead' : undefined"
      @mousedown.prevent
      @click="connect"
    >
      {{ connecting ? "…" : state === "mini" ? "+" : "Connect wallet" }}
    </button>
  </div>
  <p v-if="error" class="muted" style="margin-top: 6px">{{ error }}</p>
</template>
