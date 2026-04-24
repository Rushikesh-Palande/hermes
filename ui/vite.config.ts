// vite.config.ts
//
// Vite dev + build pipeline for the SvelteKit UI.
//
// In development the dev server proxies /api → FastAPI on :8080 so
// relative fetches from the browser hit the backend without CORS
// headaches. In production, nginx does the same routing; the UI code
// is identical.

import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
    plugins: [sveltekit()],
    server: {
        port: 5173,
        strictPort: true,
        proxy: {
            '/api': {
                target: 'http://localhost:8080',
                changeOrigin: false,
                // SSE streams need long-lived connections.
                ws: true,
            },
        },
    },
    build: {
        // Tight budget keeps the initial bundle small on the Pi's LAN.
        chunkSizeWarningLimit: 200,
        sourcemap: true,
    },
});
