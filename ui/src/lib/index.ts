/*
 * SvelteKit's `$lib` re-export surface.
 *
 * Anything imported as `$lib/foo` resolves through this file. Keep the
 * surface narrow — the goal is one place to edit when you want to
 * stop exposing something globally.
 */

export * from './types.js';
export * from './api.js';
