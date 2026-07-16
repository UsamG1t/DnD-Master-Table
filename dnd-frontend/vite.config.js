import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// В dev-режиме все запросы к API проксируются на локальный backend,
// поэтому VITE_API_BASE можно не задавать. Для продакшен-сборки задайте
// VITE_API_BASE=https://ваш-сервер в .env.production.
const BACKEND = process.env.BACKEND_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/auth': BACKEND,
      '/admin': BACKEND,
      '/dnd': BACKEND,
      '/characters': BACKEND,
      '/games': { target: BACKEND, ws: true },
      '/rfc': BACKEND,
      '/health': BACKEND,
    },
  },
});
