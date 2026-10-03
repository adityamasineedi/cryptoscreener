/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const devProxy = {
  "/api": "http://127.0.0.1:8000",
  "/ws": {
    target: "ws://127.0.0.1:8000",
    ws: true,
  },
} as const;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { ...devProxy },
  },
  // Same API/WS proxy as `vite` so `vite preview` (production build) works
  // against the local uvicorn backend without setting VITE_API_URL.
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: { ...devProxy },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
