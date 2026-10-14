import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const BACKEND = "http://127.0.0.1:8000";

// The TradeSmart redirect URL points at the backend directly
// (http://127.0.0.1:8000/tradesmart/callback), which then sends the browser to
// http://127.0.0.1:5173/dashboard in dev. The /tradesmart proxy is kept as a
// harmless fallback; Location headers are passed through without rewriting.
// Always use 127.0.0.1 (not localhost) so the session cookie is shared.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      // ws: true carries the live tick socket (/api/ws/ticks) through the proxy.
      "/api": { target: BACKEND, changeOrigin: false, ws: true },
      "/tradesmart": { target: BACKEND, changeOrigin: false },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // The chart page (OpenAlgo Charts engine, indicators and drawing tools) is one
    // lazily loaded chunk of about 800 kB; the login and dashboard bundle stays small.
    chunkSizeWarningLimit: 900,
  },
});
