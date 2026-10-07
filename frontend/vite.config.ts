import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

const apiTarget = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8001";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) }
  },
  server: {
    proxy: {
      "/api/v1": apiTarget,
      "/health": apiTarget,
      // Pages not moved yet open in the legacy UI, served by the same API.
      "/ui": apiTarget,
      "/static": apiTarget,
      // The dossier's graph is read from the legacy JSON route.
      "/api/investigations": apiTarget
    }
  },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] }
});
