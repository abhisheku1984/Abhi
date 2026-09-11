import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// The app is served behind a proxied preview host (https://<port>-<sandbox>.e2b.app).
// allowedHosts: true accepts that host; the /api + /ws proxies keep the browser
// from ever needing to reach the backend directly.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    host: '0.0.0.0',
    port: Number(process.env.PORT ?? 5173),
    strictPort: false,
    allowedHosts: true,
    cors: true,
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: process.env.VITE_WS_TARGET ?? 'ws://127.0.0.1:8000', ws: true, changeOrigin: true },
      '/media': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  preview: { host: '0.0.0.0', port: Number(process.env.PORT ?? 4173), allowedHosts: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}', 'src/**/*.test.{ts,tsx}'],
  },
} as any);
