<script lang="ts">
	/*
	 * Overview page — quick health snapshot + entry tiles for the
	 * three feature pages. Loads on every nav-home click; cheap.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type { DeviceOut, EventOut, HealthResponse } from '$lib/types';

	let health = $state<HealthResponse | null>(null);
	let healthError = $state<string | null>(null);
	let deviceCount = $state<number | null>(null);
	let recentEventCount = $state<number | null>(null);
	let recentEventError = $state<string | null>(null);

	onMount(async () => {
		try {
			health = await api.get<HealthResponse>('/api/health');
		} catch (e) {
			healthError = e instanceof Error ? e.message : 'unknown';
		}
		try {
			const devices = await api.get<DeviceOut[]>('/api/devices');
			deviceCount = devices.length;
		} catch {
			deviceCount = null;
		}
		try {
			const events = await api.get<EventOut[]>('/api/events?limit=100');
			recentEventCount = events.length;
		} catch (e) {
			recentEventError = e instanceof ApiError ? e.detail : 'unavailable';
		}
	});
</script>

<svelte:head>
	<title>HERMES · Overview</title>
</svelte:head>

<header class="mb-8">
	<h1 class="text-2xl font-semibold tracking-tight">Overview</h1>
	<p class="mt-1 text-sm text-neutral-500">
		System health and quick access to device, event, and config screens.
	</p>
</header>

<div class="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
	<div
		class="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<div class="text-xs uppercase tracking-wide text-neutral-500">API</div>
		<div class="mt-2 font-mono text-sm">
			{#if healthError}
				<span class="text-red-600 dark:text-red-400">unreachable</span>
			{:else if health}
				<span class="text-green-600 dark:text-green-400">{health.status}</span>
				<span class="ml-2 text-neutral-400">v{health.version}</span>
			{:else}
				checking…
			{/if}
		</div>
	</div>

	<div
		class="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<div class="text-xs uppercase tracking-wide text-neutral-500">Devices</div>
		<div class="mt-2 font-mono text-sm">
			{deviceCount ?? '—'}
		</div>
	</div>

	<div
		class="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<div class="text-xs uppercase tracking-wide text-neutral-500">Recent events</div>
		<div class="mt-2 font-mono text-sm">
			{#if recentEventError}
				<span class="text-red-600 dark:text-red-400">{recentEventError}</span>
			{:else}
				{recentEventCount ?? '—'}
			{/if}
		</div>
	</div>
</div>

<div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
	<a
		href="/devices"
		class="rounded-lg border border-neutral-200 bg-white p-4 transition-colors hover:border-neutral-400 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-600"
	>
		<h2 class="font-medium">Devices</h2>
		<p class="mt-1 text-sm text-neutral-500">Add, rename, and disable physical data sources.</p>
	</a>
	<a
		href="/events"
		class="rounded-lg border border-neutral-200 bg-white p-4 transition-colors hover:border-neutral-400 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-600"
	>
		<h2 class="font-medium">Events</h2>
		<p class="mt-1 text-sm text-neutral-500">Browse detected events with their ±9 s sample windows.</p>
	</a>
	<a
		href="/config"
		class="rounded-lg border border-neutral-200 bg-white p-4 transition-colors hover:border-neutral-400 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-600"
	>
		<h2 class="font-medium">Config</h2>
		<p class="mt-1 text-sm text-neutral-500">Tune Type A/B/C/D detector thresholds. Hot-reloads.</p>
	</a>
</div>
