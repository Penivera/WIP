import { createRouter, createWebHistory } from "vue-router";
import OwnerView from "./views/OwnerView.vue";
import PayView from "./views/PayView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: PayView },
    { path: "/pay/:name", component: PayView },
    { path: "/owner", component: OwnerView },
  ],
});
