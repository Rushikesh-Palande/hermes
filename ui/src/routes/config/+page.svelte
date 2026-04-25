<script lang="ts">
	/*
	 * Config page — Type A / B / C / D detector thresholds.
	 *
	 * Each tab is a separate form bound to the corresponding GET / PUT
	 * endpoint under /api/config. Saving hot-reloads the running engine
	 * (Phase 4b) — the operator does NOT need to restart anything.
	 *
	 * Per-device / per-sensor overrides land in Phase 4c. This page
	 * edits the GLOBAL config only.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type { TypeAConfig, TypeBConfig, TypeCConfig, TypeDConfig } from '$lib/types';

	type TabId = 'a' | 'b' | 'c' | 'd';
	let activeTab = $state<TabId>('a');

	let typeA = $state<TypeAConfig | null>(null);
	let typeB = $state<TypeBConfig | null>(null);
	let typeC = $state<TypeCConfig | null>(null);
	let typeD = $state<TypeDConfig | null>(null);

	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let saveSuccess = $state(false);
	let isSaving = $state(false);

	async function loadAll() {
		loadError = null;
		try {
			[typeA, typeB, typeC, typeD] = await Promise.all([
				api.get<TypeAConfig>('/api/config/type_a'),
				api.get<TypeBConfig>('/api/config/type_b'),
				api.get<TypeCConfig>('/api/config/type_c'),
				api.get<TypeDConfig>('/api/config/type_d')
			]);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		}
	}

	async function save<T>(path: string, payload: T) {
		isSaving = true;
		saveError = null;
		saveSuccess = false;
		try {
			await api.put(path, payload);
			saveSuccess = true;
			setTimeout(() => (saveSuccess = false), 2000);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'save failed';
		} finally {
			isSaving = false;
		}
	}

	async function saveTypeA(event: SubmitEvent) {
		event.preventDefault();
		if (!typeA) return;
		await save('/api/config/type_a', typeA);
	}

	async function saveTypeB(event: SubmitEvent) {
		event.preventDefault();
		if (!typeB) return;
		await save('/api/config/type_b', typeB);
	}

	async function saveTypeC(event: SubmitEvent) {
		event.preventDefault();
		if (!typeC) return;
		await save('/api/config/type_c', typeC);
	}

	async function saveTypeD(event: SubmitEvent) {
		event.preventDefault();
		if (!typeD) return;
		await save('/api/config/type_d', typeD);
	}

	onMount(loadAll);

	const tabs: Array<{ id: TabId; label: string; subtitle: string }> = [
		{ id: 'a', label: 'Type A', subtitle: 'Variance / CV%' },
		{ id: 'b', label: 'Type B', subtitle: 'Post-window deviation' },
		{ id: 'c', label: 'Type C', subtitle: 'Range on avg_T3' },
		{ id: 'd', label: 'Type D', subtitle: 'Two-stage avg vs avg_T5' }
	];
</script>

<svelte:head>
	<title>HERMES · Config</title>
</svelte:head>

<header class="mb-6">
	<h1 class="text-2xl font-semibold tracking-tight">Detector configuration</h1>
	<p class="mt-1 text-sm text-neutral-500">
		Saving applies immediately to the running engine — no restart required.
	</p>
</header>

<nav class="mb-6 flex gap-1 border-b border-neutral-200 dark:border-neutral-800">
	{#each tabs as tab (tab.id)}
		<button
			type="button"
			onclick={() => (activeTab = tab.id)}
			class="border-b-2 px-4 py-2 text-sm transition-colors"
			class:border-neutral-900={activeTab === tab.id}
			class:dark:border-neutral-100={activeTab === tab.id}
			class:font-medium={activeTab === tab.id}
			class:border-transparent={activeTab !== tab.id}
			class:text-neutral-500={activeTab !== tab.id}
		>
			{tab.label}
			<span class="ml-2 text-xs text-neutral-400">{tab.subtitle}</span>
		</button>
	{/each}
</nav>

{#if loadError}
	<p class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-400">
		{loadError}
	</p>
{/if}

{#if saveError}
	<p class="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-400">
		{saveError}
	</p>
{/if}
{#if saveSuccess}
	<p class="mb-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-700 dark:border-green-900/50 dark:bg-green-900/20 dark:text-green-400">
		Saved. Detectors reset on next sample.
	</p>
{/if}

<section class="rounded-lg border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900">
	{#if activeTab === 'a' && typeA}
		<form onsubmit={saveTypeA} class="grid grid-cols-1 gap-4 sm:grid-cols-2">
			<label class="flex items-center gap-2 text-sm sm:col-span-2">
				<input type="checkbox" bind:checked={typeA.enabled} />
				<span>Enabled</span>
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">T1 (window seconds)</span>
				<input type="number" step="0.1" min="0.1" bind:value={typeA.T1} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">CV% threshold</span>
				<input type="number" step="0.1" min="0" bind:value={typeA.threshold_cv} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Debounce (s)</span>
				<input type="number" step="0.1" min="0" bind:value={typeA.debounce_seconds} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Init fill ratio</span>
				<input type="number" step="0.05" min="0.05" max="1" bind:value={typeA.init_fill_ratio} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<div class="sm:col-span-2 flex justify-end">
				<button type="submit" disabled={isSaving} class="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300">
					{isSaving ? 'Saving…' : 'Save Type A'}
				</button>
			</div>
		</form>
	{:else if activeTab === 'b' && typeB}
		<form onsubmit={saveTypeB} class="grid grid-cols-1 gap-4 sm:grid-cols-2">
			<label class="flex items-center gap-2 text-sm sm:col-span-2">
				<input type="checkbox" bind:checked={typeB.enabled} />
				<span>Enabled</span>
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">T2 (window seconds)</span>
				<input type="number" step="0.1" min="0.1" bind:value={typeB.T2} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Debounce (s)</span>
				<input type="number" step="0.1" min="0" bind:value={typeB.debounce_seconds} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Lower tolerance %</span>
				<input type="number" step="0.1" min="0" bind:value={typeB.lower_threshold_pct} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Upper tolerance %</span>
				<input type="number" step="0.1" min="0" bind:value={typeB.upper_threshold_pct} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<div class="sm:col-span-2 flex justify-end">
				<button type="submit" disabled={isSaving} class="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300">
					{isSaving ? 'Saving…' : 'Save Type B'}
				</button>
			</div>
		</form>
	{:else if activeTab === 'c' && typeC}
		<form onsubmit={saveTypeC} class="grid grid-cols-1 gap-4 sm:grid-cols-2">
			<label class="flex items-center gap-2 text-sm sm:col-span-2">
				<input type="checkbox" bind:checked={typeC.enabled} />
				<span>Enabled</span>
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">T3 (window seconds)</span>
				<input type="number" step="0.1" min="0.1" bind:value={typeC.T3} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Debounce (s)</span>
				<input type="number" step="0.1" min="0" bind:value={typeC.debounce_seconds} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Lower threshold</span>
				<input type="number" step="0.1" bind:value={typeC.threshold_lower} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Upper threshold</span>
				<input type="number" step="0.1" bind:value={typeC.threshold_upper} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<p class="sm:col-span-2 text-xs text-neutral-500">
				Thresholds are absolute sensor units (not percentages). Lower must be strictly less than upper.
			</p>
			<div class="sm:col-span-2 flex justify-end">
				<button type="submit" disabled={isSaving} class="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300">
					{isSaving ? 'Saving…' : 'Save Type C'}
				</button>
			</div>
		</form>
	{:else if activeTab === 'd' && typeD}
		<form onsubmit={saveTypeD} class="grid grid-cols-1 gap-4 sm:grid-cols-2">
			<label class="flex items-center gap-2 text-sm sm:col-span-2">
				<input type="checkbox" bind:checked={typeD.enabled} />
				<span>Enabled</span>
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">T4 (Stage 1 window seconds)</span>
				<input type="number" step="0.1" min="0.1" bind:value={typeD.T4} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">T5 (Stage 3 buckets)</span>
				<input type="number" step="1" min="1" bind:value={typeD.T5} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Tolerance %</span>
				<input type="number" step="0.1" min="0" bind:value={typeD.tolerance_pct} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Debounce (s)</span>
				<input type="number" step="0.1" min="0" bind:value={typeD.debounce_seconds} class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950" />
			</label>
			<p class="sm:col-span-2 text-xs text-neutral-500">
				Type D fires when avg_T3 (from Type C) leaves a symmetric band around avg_T5. Total warmup ≈ T4 + T5 seconds.
			</p>
			<div class="sm:col-span-2 flex justify-end">
				<button type="submit" disabled={isSaving} class="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300">
					{isSaving ? 'Saving…' : 'Save Type D'}
				</button>
			</div>
		</form>
	{:else}
		<p class="text-sm text-neutral-500">Loading…</p>
	{/if}
</section>
