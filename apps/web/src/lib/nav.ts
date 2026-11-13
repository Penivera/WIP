/**
 * In-memory navigation for the widget. It never touches the host page's URL,
 * so it is safe to embed. Works like browser history: back, forward, and
 * going somewhere new drops the forward entries.
 */
import { computed, reactive } from "vue";

export type Screen =
  | "pay"
  | "pay-details"
  | "pay-status"
  | "names"
  | "claim"
  | "name"
  | "destination"
  | "wallets"
  | "payments";

const state = reactive({ stack: ["pay"] as Screen[], index: 0 });

export const nav = {
  current: computed<Screen>(() => state.stack[state.index] ?? "pay"),
  canBack: computed(() => state.index > 0),
  canForward: computed(() => state.index < state.stack.length - 1),

  go(screen: Screen) {
    if (state.stack[state.index] === screen) return;
    state.stack.splice(state.index + 1);
    state.stack.push(screen);
    state.index = state.stack.length - 1;
  },
  back() {
    if (state.index > 0) state.index--;
  },
  forward() {
    if (state.index < state.stack.length - 1) state.index++;
  },
  /** Forgets the screens ahead, e.g. when the data they showed is gone. */
  dropForward() {
    state.stack.splice(state.index + 1);
  },
  reset(screen: Screen) {
    state.stack.splice(0, state.stack.length, screen);
    state.index = 0;
  },
};

export const isPayFlow = computed(() => nav.current.value.startsWith("pay"));
