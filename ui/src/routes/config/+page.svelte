<script lang="ts">
	/*
	 * Config page — Type A / B / C / D detector thresholds.
	 *
	 * Each tab edits the GLOBAL config and lists per-device + per-sensor
	 * overrides for that detector type. Saving anywhere hot-reloads the
	 * running engine (Phase 4b semantics) — no restart required.
	 *
	 * Override workflow (Phase 5b):
	 *   * "Save as device override" or "Save as sensor override" PUTs the
	 *     current form's values to the appropriate scope.
	 *   * The resolution walk at runtime is SENSOR → DEVICE → GLOBAL.
	 *   * Removing an override falls back to the next layer up.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type {
		DetectorTypeName,
		OverridesOut,
		TypeAConfig,
		TypeBConfig,
		TypeCConfig,
		TypeDConfig
	} from '$lib/types';

	type TabId = 'a' | 'b' | 'c' | 'd';
	let activeTab = $state<TabId>('a');

	let typeA = $state<TypeAConfig | null>(null);
	let typeB = $state<TypeBConfig | null>(null);
	let typeC = $state<TypeCConfig | null>(null);
	let typeD = $state<TypeDConfig | null>(null);

	// One OverridesOut per detector type, keyed by tab id.
	let overrides = $state<Record<TabId, OverridesOut>>({
		a: { devices: {}, sensors: [] },
		b: { devices: {}, sensors: [] },
		c: { devices: {}, sensors: [] },
		d: { devices: {}, sensors: [] }
	});

	let loadError = $state<string | null>(null);
	let saveError = $state<string | null>(null);
	let saveSuccess = $state(false);
	let isSaving = $state(false);

	const TAB_TO_TYPE: Record<TabId, DetectorTypeName> = {
		a: 'type_a',
		b: 'type_b',
		c: 'type_c',
		d: 'type_d'
	};

	const currentOverrides = $derived(overrides[activeTab]);

	async function loadAll() {
		loadError = null;
		try {
			const [a, b, c, d, oa, ob, oc, od] = await Promise.all([
				api.get<TypeAConfig>('/api/config/type_a'),
				api.get<TypeBConfig>('/api/config/type_b'),
				api.get<TypeCConfig>('/api/config/type_c'),
				api.get<TypeDConfig>('/api/config/type_d'),
				api.get<OverridesOut>('/api/config/type_a/overrides'),
				api.get<OverridesOut>('/api/config/type_b/overrides'),
				api.get<OverridesOut>('/api/config/type_c/overrides'),
				api.get<OverridesOut>('/api/config/type_d/overrides')
			]);
			typeA = a;
			typeB = b;
			typeC = c;
			typeD = d;
			overrides = { a: oa, b: ob, c: oc, d: od };
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		}
	}

	function flashSaved() {
		saveSuccess = true;
		setTimeout(() => (saveSuccess = false), 2000);
	}

	async function save<T>(path: string, payload: T) {
		isSaving = true;
		saveError = null;
		saveSuccess = false;
		try {
			await api.put(path, payload);
			flashSaved();
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

	function currentFormPayload(): unknown {
		switch (activeTab) {
			case 'a':
				return typeA;
			case 'b':
				return typeB;
			case 'c':
				return typeC;
			case 'd':
				return typeD;
		}
	}

	async function refreshOverrides(tab: TabId) {
		const type = TAB_TO_TYPE[tab];
		try {
			overrides[tab] = await api.get<OverridesOut>(`/api/config/${type}/overrides`);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'reload failed';
		}
	}

	async function saveAsDeviceOverride() {
		const payload = currentFormPayload();
		if (!payload) return;
		const raw = window.prompt('Device ID (1–999) for this override:');
		if (raw === null) return;
		const deviceId = Number(raw);
		if (!Number.isInteger(deviceId) || deviceId < 1 || deviceId > 999) {
			saveError = 'device_id must be an integer between 1 and 999';
			return;
		}
		const type = TAB_TO_TYPE[activeTab];
		isSaving = true;
		saveError = null;
		try {
			await api.put(`/api/config/${type}/overrides/device/${deviceId}`, payload);
			flashSaved();
			await refreshOverrides(activeTab);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'save failed';
		} finally {
			isSaving = false;
		}
	}

	async function saveAsSensorOverride() {
		const payload = currentFormPayload();
		if (!payload) return;
		const rawDevice = window.prompt('Device ID (1–999) for this override:');
		if (rawDevice === null) return;
		const rawSensor = window.prompt('Sensor ID (1–12) for this override:');
		if (rawSensor === null) return;
		const deviceId = Number(rawDevice);
		const sensorId = Number(rawSensor);
		if (
			!Number.isInteger(deviceId) ||
			deviceId < 1 ||
			deviceId > 999 ||
			!Number.isInteger(sensorId) ||
			sensorId < 1 ||
			sensorId > 12
		) {
			saveError = 'device_id 1–999 and sensor_id 1–12 required';
			return;
		}
		const type = TAB_TO_TYPE[activeTab];
		isSaving = true;
		saveError = null;
		try {
			await api.put(
				`/api/config/${type}/overrides/sensor/${deviceId}/${sensorId}`,
				payload
			);
			flashSaved();
			await refreshOverrides(activeTab);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'save failed';
		} finally {
			isSaving = false;
		}
	}

	async function deleteDeviceOverride(deviceId: number) {
		const type = TAB_TO_TYPE[activeTab];
		try {
			await api.del(`/api/config/${type}/overrides/device/${deviceId}`);
			await refreshOverrides(activeTab);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'delete failed';
		}
	}

	async function deleteSensorOverride(deviceId: number, sensorId: number) {
		const type = TAB_TO_TYPE[activeTab];
		try {
			await api.del(`/api/config/${type}/overrides/sensor/${deviceId}/${sensorId}`);
			await refreshOverrides(activeTab);
		} catch (e) {
			saveError = e instanceof ApiError ? e.detail : 'delete failed';
		}
	}

	function summariseConfig(c: Record<string, unknown>): string {
		// Pull the most identifying fields per detector type.
		const enabled = c.enabled === true ? 'on' : 'off';
		const parts: string[] = [enabled];
		for (const k of [
			'T1',
			'T2',
			'T3',
			'T4',
			'T5',
			'threshold_cv',
			'threshold_lower',
			'threshold_upper',
			'lower_threshold_pct',
			'upper_threshold_pct',
			'tolerance_pct'
		]) {
			if (k in c && typeof c[k] === 'number') {
				parts.push(`${k}=${c[k]}`);
			}
		}
		return parts.join(', ');
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

<section
	class="mt-6 rounded-lg border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900"
>
	<header class="mb-4 flex items-baseline justify-between">
		<div>
			<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">
				Overrides
			</h2>
			<p class="mt-1 text-xs text-neutral-500">
				Per-device and per-sensor overrides take precedence over the global
				config above (resolution: sensor → device → global).
			</p>
		</div>
		<div class="flex gap-2">
			<button
				type="button"
				onclick={saveAsDeviceOverride}
				disabled={isSaving}
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
			>
				+ Save as device override
			</button>
			<button
				type="button"
				onclick={saveAsSensorOverride}
				disabled={isSaving}
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
			>
				+ Save as sensor override
			</button>
		</div>
	</header>

	<div class="mb-4">
		<h3 class="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
			Per device
		</h3>
		{#if Object.keys(currentOverrides.devices).length === 0}
			<p class="text-xs text-neutral-400">No device overrides.</p>
		{:else}
			<table class="w-full text-sm">
				<thead
					class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
				>
					<tr>
						<th class="px-2 py-1">Device</th>
						<th class="px-2 py-1">Settings</th>
						<th class="px-2 py-1 text-right">Action</th>
					</tr>
				</thead>
				<tbody>
					{#each Object.entries(currentOverrides.devices) as [deviceId, cfg] (deviceId)}
						<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
							<td class="px-2 py-1 font-mono">{deviceId}</td>
							<td class="px-2 py-1 font-mono text-xs text-neutral-600 dark:text-neutral-400">
								{summariseConfig(cfg)}
							</td>
							<td class="px-2 py-1 text-right">
								<button
									type="button"
									onclick={() => deleteDeviceOverride(Number(deviceId))}
									class="text-xs text-red-600 underline hover:text-red-800 dark:text-red-400"
								>
									remove
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>

	<div>
		<h3 class="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
			Per sensor
		</h3>
		{#if currentOverrides.sensors.length === 0}
			<p class="text-xs text-neutral-400">No sensor overrides.</p>
		{:else}
			<table class="w-full text-sm">
				<thead
					class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
				>
					<tr>
						<th class="px-2 py-1">Device</th>
						<th class="px-2 py-1">Sensor</th>
						<th class="px-2 py-1">Settings</th>
						<th class="px-2 py-1 text-right">Action</th>
					</tr>
				</thead>
				<tbody>
					{#each currentOverrides.sensors as s (`${s.device_id}.${s.sensor_id}`)}
						<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
							<td class="px-2 py-1 font-mono">{s.device_id}</td>
							<td class="px-2 py-1 font-mono">{s.sensor_id}</td>
							<td class="px-2 py-1 font-mono text-xs text-neutral-600 dark:text-neutral-400">
								{summariseConfig(s.config)}
							</td>
							<td class="px-2 py-1 text-right">
								<button
									type="button"
									onclick={() => deleteSensorOverride(s.device_id, s.sensor_id)}
									class="text-xs text-red-600 underline hover:text-red-800 dark:text-red-400"
								>
									remove
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
</section>
