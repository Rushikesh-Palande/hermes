<script lang="ts">
    /*
     * Temporary landing page. Demonstrates that the build pipeline is
     * working end-to-end (Vite + Svelte 5 + Tailwind). Replaced by the
     * real login / dashboard router in Phase 5.
     */
    import { onMount } from 'svelte';

    let apiHealth = $state<'unknown' | 'ok' | 'error'>('unknown');

    onMount(async () => {
        // Vite dev server proxies /api → FastAPI on :8080. In production
        // nginx does the equivalent.
        try {
            const res = await fetch('/api/health');
            apiHealth = res.ok ? 'ok' : 'error';
        } catch {
            apiHealth = 'error';
        }
    });
</script>

<svelte:head>
    <title>HERMES · pre-alpha</title>
</svelte:head>

<section class="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-8">
    <header>
        <h1 class="text-3xl font-semibold tracking-tight">HERMES</h1>
        <p class="text-neutral-500 dark:text-neutral-400">
            Sensor telemetry + event detection platform · pre-alpha scaffold
        </p>
    </header>

    <div
        class="rounded-lg border border-neutral-200 bg-white p-4 text-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
        <dl class="grid grid-cols-2 gap-2">
            <dt class="text-neutral-500">API health</dt>
            <dd class="font-mono">
                {#if apiHealth === 'unknown'}
                    checking…
                {:else if apiHealth === 'ok'}
                    <span class="text-green-600 dark:text-green-400">ok</span>
                {:else}
                    <span class="text-red-600 dark:text-red-400">unreachable</span>
                {/if}
            </dd>
            <dt class="text-neutral-500">Phase</dt>
            <dd class="font-mono">1 — foundation</dd>
        </dl>
    </div>

    <footer class="text-xs text-neutral-400">
        See <code>CHANGELOG.md</code> for what ships in the next release.
    </footer>
</section>
