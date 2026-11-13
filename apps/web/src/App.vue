<script setup lang="ts">
import { RouterLink, RouterView } from "vue-router";
</script>

<template>
  <header class="top">
    <RouterLink to="/" class="brand">Universal Name</RouterLink>
    <nav>
      <RouterLink to="/">Pay</RouterLink>
      <RouterLink to="/owner">My names</RouterLink>
    </nav>
  </header>
  <main>
    <RouterView />
  </main>
  <footer class="foot">
    Contract on NEAR testnet. Routed payments use NEAR Intents on mainnet with real funds. Bank payout is simulated.
  </footer>
</template>
