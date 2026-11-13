import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// `vite build` builds the demo page. `vite build --mode embed` builds one
// script, dist-embed/universal-name.js, that defines `window.UniversalName`.
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: { port: 5173 },
  ...(mode === "embed"
    ? {
        define: { "process.env.NODE_ENV": JSON.stringify("production") },
        build: {
          outDir: "dist-embed",
          lib: {
            entry: "src/lib-entry.ts",
            name: "UniversalName",
            formats: ["iife"] as const,
            fileName: () => "universal-name.js",
          },
        },
      }
    : {}),
}));
