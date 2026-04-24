// svelte.config.js
//
// SvelteKit configuration.
//
// Using adapter-node so the UI ships as a small Node server sitting
// behind nginx on the Pi. SSR + SPA shared per-route by SvelteKit's
// defaults; we'll pick per-route modes in Phase 5 (dashboard = CSR for
// live charts, device list = SSR for fast first paint).

import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
    preprocess: vitePreprocess(),
    kit: {
        adapter: adapter({
            out: 'build',
            precompress: true,
            envPrefix: 'HERMES_UI_',
        }),
        alias: {
            $lib: 'src/lib',
        },
        // CSP baseline. Tightened further once auth + SSE endpoints are
        // wired in Phase 5.
        csp: {
            mode: 'auto',
            directives: {
                'default-src': ['self'],
                'connect-src': ['self'],
                'img-src': ['self', 'data:'],
                'font-src': ['self'],
                'style-src': ['self', 'unsafe-inline'],
            },
        },
    },
};

export default config;
