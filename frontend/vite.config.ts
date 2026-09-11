import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The production build is served by the FastAPI app on a single port, which is
 * what the preview proxy and any container deployment expect. During `npm run
 * dev` the API is proxied so the browser only ever talks to one origin.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: false,
    allowedHosts: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/thumbs": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
});
