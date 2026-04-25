<script lang="ts">
	/*
	 * Device detail page — live graph + device metadata.
	 *
	 * Drilldown from /devices: shows the device's name, status, and a
	 * uPlot chart streaming /api/live_stream/{id} in real time. Operator
	 * can change the window size and toggle individual sensors.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, ApiError } from '$lib/api';
	import LiveChart from '$lib/LiveChart.svelte';
	import type { DeviceOffsetsOut, DeviceOut, SensorOffsetOut } from '$lib/types';

	const WINDOW_OPTIONS = [
		{ label: '1 s', seconds: 1 },
		{ label: '6 s', seconds: 6 },
		{ label: '12 s', seconds: 12 }
	];

	const ALL_SENSORS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] as const;

	let device = $state<DeviceOut | null>(null);
	let loadError = $state<string | null>(null);
	let windowSeconds = $state(6);
	let visibleSensors = $state(new Set<number>(ALL_SENSORS));

	// Per-sensor offsets (calibration). The form values are bound to a
	// dict keyed by sensor_id; saving writes them via the bulk PUT so
	// missing sensors get cleared.
	let offsets = $state<Record<number, number>>({});
	let offsetsLoading = $state(true);
	let offsetsError = $state<string | null>(null);
	let offsetsSaving = $state(false);
	let offsetsSaved = $state(false);

	const deviceId = $derived(Number(page.params.device_id));

	async function loadDevice() {
		loadError = null;
		try {
			device = await api.get<DeviceOut>(`/api/devices/${deviceId}`);
		} catch (e) {
			device = null;
			loadError = e instanceof ApiError ? e.detail : 'request failed';
		}
	}

	function offsetsFromList(list: SensorOffsetOut[]): Record<number, number> {
		const out: Record<number, number> = {};
		for (const row of list) {
			out[row.sensor_id] = row.offset_value;
		}
		return out;
	}

	async function loadOffsets() {
		offsetsLoading = true;
		offsetsError = null;
		try {
			const body = await api.get<DeviceOffsetsOut>(
				`/api/devices/${deviceId}/offsets`
			);
			offsets = offsetsFromList(body.offsets);
		} catch (e) {
			offsetsError = e instanceof ApiError ? e.detail : 'load failed';
		} finally {
			offsetsLoading = false;
		}
	}

	async function saveOffsets() {
		offsetsSaving = true;
		offsetsError = null;
		offsetsSaved = false;
		// Build the bulk-PUT payload; drop zero-valued entries so the
		// server resets their rows (matches "no override" semantics).
		const body: Record<string, number> = {};
		for (const [sid, v] of Object.entries(offsets)) {
			if (typeof v === 'number' && v !== 0) {
				body[sid] = v;
			}
		}
		try {
			const resp = await api.put<DeviceOffsetsOut>(
				`/api/devices/${deviceId}/offsets`,
				{ offsets: body }
			);
			offsets = offsetsFromList(resp.offsets);
			offsetsSaved = true;
			setTimeout(() => (offsetsSaved = false), 2000);
		} catch (e) {
			offsetsError = e instanceof ApiError ? e.detail : 'save failed';
		} finally {
			offsetsSaving = false;
		}
	}

	function clearOffset(sid: number) {
		offsets = { ...offsets, [sid]: 0 };
	}

	function toggleSensor(sid: number) {
		const next = new Set(visibleSensors);
		if (next.has(sid)) next.delete(sid);
		else next.add(sid);
		visibleSensors = next;
	}

	function selectAll() {
		visibleSensors = new Set(ALL_SENSORS);
	}

	function selectNone() {
		visibleSensors = new Set();
	}

	onMount(() => {
		void loadDevice();
		void loadOffsets();
	});
</script>

<svelte:head>
	<title>HERMES · Device {deviceId}</title>
</svelte:head>

<div class="mb-4">
	<button
		type="button"
		onclick={() => goto('/devices')}
		class="text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
	>
		← All devices
	</button>
</div>

{#if loadError}
	<p
		class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-400"
	>
		{loadError}
	</p>
{:else if !device}
	<p class="text-sm text-neutral-500">Loading device…</p>
{:else}
	<header class="mb-6 flex items-baseline justify-between">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">
				{device.name}
				<span class="ml-2 font-mono text-base text-neutral-400">#{device.device_id}</span>
			</h1>
			<p class="mt-1 text-sm text-neutral-500">
				{device.protocol.toUpperCase()} · topic
				<span class="font-mono">{device.topic ?? '(default)'}</span>
			</p>
		</div>
		<div>
			{#if device.is_active}
				<span
					class="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-400"
				>
					active
				</span>
			{:else}
				<span
					class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
				>
					disabled
				</span>
			{/if}
		</div>
	</header>

	<section
		class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
			<div class="flex items-center gap-2 text-sm">
				<span class="text-neutral-500">Window</span>
				<div
					class="flex overflow-hidden rounded-md border border-neutral-300 dark:border-neutral-700"
				>
					{#each WINDOW_OPTIONS as opt (opt.seconds)}
						<button
							type="button"
							onclick={() => (windowSeconds = opt.seconds)}
							class="px-3 py-1 transition-colors"
							class:bg-neutral-900={windowSeconds === opt.seconds}
							class:text-white={windowSeconds === opt.seconds}
							class:dark:bg-neutral-100={windowSeconds === opt.seconds}
							class:dark:text-neutral-900={windowSeconds === opt.seconds}
							class:hover:bg-neutral-100={windowSeconds !== opt.seconds}
							class:dark:hover:bg-neutral-800={windowSeconds !== opt.seconds}
						>
							{opt.label}
						</button>
					{/each}
				</div>
			</div>

			<div class="flex items-center gap-2 text-sm">
				<span class="text-neutral-500">Sensors</span>
				<button
					type="button"
					onclick={selectAll}
					class="text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
				>
					all
				</button>
				<span class="text-neutral-300">·</span>
				<button
					type="button"
					onclick={selectNone}
					class="text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
				>
					none
				</button>
			</div>
		</div>

		<div class="mb-3 flex flex-wrap gap-1">
			{#each ALL_SENSORS as sid (sid)}
				{@const active = visibleSensors.has(sid)}
				<button
					type="button"
					onclick={() => toggleSensor(sid)}
					class="rounded-md border px-2 py-0.5 font-mono text-xs transition-colors"
					class:border-neutral-900={active}
					class:bg-neutral-900={active}
					class:text-white={active}
					class:dark:border-neutral-100={active}
					class:dark:bg-neutral-100={active}
					class:dark:text-neutral-900={active}
					class:border-neutral-300={!active}
					class:dark:border-neutral-700={!active}
					class:text-neutral-500={!active}
				>
					S{sid}
				</button>
			{/each}
		</div>

		<LiveChart
			{deviceId}
			{windowSeconds}
			{visibleSensors}
		/>
	</section>

	<section
		class="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<header class="mb-3 flex items-baseline justify-between">
			<div>
				<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">
					Sensor offsets
				</h2>
				<p class="mt-1 text-xs text-neutral-500">
					Zero-point calibration. Ingest applies <code>corrected = raw − offset</code>
					to every sample, hot-reloaded the moment you save.
				</p>
			</div>
			<div class="flex items-center gap-3">
				{#if offsetsSaved}
					<span class="text-xs text-green-600 dark:text-green-400">Saved.</span>
				{/if}
				<button
					type="button"
					onclick={saveOffsets}
					disabled={offsetsSaving || offsetsLoading}
					class="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
				>
					{offsetsSaving ? 'Saving…' : 'Save offsets'}
				</button>
			</div>
		</header>

		{#if offsetsError}
			<p class="mb-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-400">
				{offsetsError}
			</p>
		{/if}

		{#if offsetsLoading}
			<p class="text-sm text-neutral-500">Loading…</p>
		{:else}
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
				{#each ALL_SENSORS as sid (sid)}
					<label class="flex flex-col gap-1 text-sm">
						<span class="font-mono text-xs text-neutral-500">S{sid}</span>
						<div class="flex items-center gap-1">
							<input
								type="number"
								step="0.01"
								bind:value={offsets[sid]}
								class="w-full rounded-md border border-neutral-300 bg-white px-2 py-1 font-mono dark:border-neutral-700 dark:bg-neutral-950"
							/>
							{#if offsets[sid]}
								<button
									type="button"
									onclick={() => clearOffset(sid)}
									title="Reset to 0"
									class="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
								>
									×
								</button>
							{/if}
						</div>
					</label>
				{/each}
			</div>
		{/if}
	</section>
{/if}
