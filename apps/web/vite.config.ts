/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// AIEB_API_BASE_URL lets local dev/tests point at a real hosted API instance
// without hardcoding a URL into the bundle (spec 35: no scoring recomputed
// client-side, all numbers come from the API/analysis package as-is).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
