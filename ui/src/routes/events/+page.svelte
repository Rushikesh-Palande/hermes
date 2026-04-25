<script lang="ts">
	/*
	 * Events page — paginated, filterable history of detected events.
	 *
	 * Filters compose with AND on the server. Time-range fields submit
	 * ISO timestamps; an empty value means unbounded on that side.
	 * Click a row to expand it and lazy-load the ±9 s window samples.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type { EventOut, EventType, EventWindowOut } from '$lib/types';

	const eventTypes: EventType[] = ['A', 'B', 'C', 'D', 'BREAK'];

	let events = $state<EventOut[]>([]);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);

	let filterDeviceId = $state<number | null>(null);
	let filterSensorId = $state<number | null>(null);
	let filterEventType = $state<EventType | ''>('');
	let filterAfter = $state('');
	let filterBefore = $state('');
	let limit = $state(100);
	let offset = $state(0);

	// Expanded event windows, keyed by event_id.
	let expandedId = $state<number | null>(null);
	let expandedWindow = $state<EventWindowOut | null>(null);
	let expandedError = $state<string | null>(null);

	function appendFilters(params: URLSearchParams): void {
		if (filterDeviceId !== null) params.set('device_id', String(filterDeviceId));
		if (filterSensorId !== null) params.set('sensor_id', String(filterSensorId));
		if (filterEventType) params.set('event_type', filterEventType);
		if (filterAfter) params.set('after', new Date(filterAfter).toISOString());
		if (filterBefore) params.set('before', new Date(filterBefore).toISOString());
	}

	function buildQueryString(): string {
		const params = new URLSearchParams();
		appendFilters(params);
		params.set('limit', String(limit));
		params.set('offset', String(offset));
		return params.toString();
	}

	const exportCsvHref = $derived.by(() => {
		const params = new URLSearchParams();
		appendFilters(params);
		params.set('format', 'csv');
		return `/api/events/export?${params.toString()}`;
	});

	const exportNdjsonHref = $derived.by(() => {
		const params = new URLSearchParams();
		appendFilters(params);
		params.set('format', 'ndjson');
		return `/api/events/export?${params.toString()}`;
	});

	async function loadEvents() {
		isLoading = true;
		loadError = null;
		try {
			events = await api.get<EventOut[]>(`/api/events?${buildQueryString()}`);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	function applyFilters(event: SubmitEvent) {
		event.preventDefault();
		offset = 0;
		void loadEvents();
	}

	function nextPage() {
		offset += limit;
		void loadEvents();
	}

	function prevPage() {
		offset = Math.max(0, offset - limit);
		void loadEvents();
	}

	async function expand(eventId: number) {
		if (expandedId === eventId) {
			expandedId = null;
			expandedWindow = null;
			return;
		}
		expandedId = eventId;
		expandedWindow = null;
		expandedError = null;
		try {
			expandedWindow = await api.get<EventWindowOut>(`/api/events/${eventId}/window`);
		} catch (e) {
			expandedError = e instanceof ApiError ? e.detail : 'window unavailable';
		}
	}

	function shortMetadata(md: Record<string, unknown>): string {
		const interesting = ['cv_percent', 'avg_T2', 'avg_T3', 'avg_T5', 'trigger_value'];
		const parts: string[] = [];
		for (const key of interesting) {
			if (key in md && typeof md[key] === 'number') {
				parts.push(`${key}=${(md[key] as number).toFixed(2)}`);
			}
		}
		return parts.join(', ');
	}

	onMount(loadEvents);
</script>

<svelte:head>
	<title>HERMES · Events</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Events</h1>
		<p class="mt-1 text-sm text-neutral-500">
			Detected events with their ±9 s sample windows. Newest first.
		</p>
	</div>
	<div class="flex gap-2 text-sm">
		<a
			href={exportCsvHref}
			class="rounded-md border border-neutral-300 px-3 py-1.5 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
			title="Download CSV with current filters (limit/offset ignored)"
		>
			Export CSV
		</a>
		<a
			href={exportNdjsonHref}
			class="rounded-md border border-neutral-300 px-3 py-1.5 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
			title="Download NDJSON with current filters (limit/offset ignored)"
		>
			Export NDJSON
		</a>
	</div>
</header>

<form
	onsubmit={applyFilters}
	class="mb-6 grid grid-cols-2 gap-3 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900 sm:grid-cols-6"
>
	<label class="flex flex-col gap-1 text-sm">
		<span class="text-neutral-500">Device</span>
		<input
			type="number"
			min="1"
			max="999"
			bind:value={filterDeviceId}
			class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
		/>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span class="text-neutral-500">Sensor</span>
		<input
			type="number"
			min="1"
			max="12"
			bind:value={filterSensorId}
			class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
		/>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span class="text-neutral-500">Type</span>
		<select
			bind:value={filterEventType}
			class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
		>
			<option value="">any</option>
			{#each eventTypes as t (t)}
				<option value={t}>{t}</option>
			{/each}
		</select>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span class="text-neutral-500">After</span>
		<input
			type="datetime-local"
			bind:value={filterAfter}
			class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
		/>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span class="text-neutral-500">Before</span>
		<input
			type="datetime-local"
			bind:value={filterBefore}
			class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
		/>
	</label>
	<div class="flex items-end">
		<button
			type="submit"
			class="w-full rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white transition-colors hover:bg-neutral-700 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
		>
			Apply
		</button>
	</div>
</form>

<section
	class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
>
	{#if isLoading}
		<p class="p-4 text-sm text-neutral-500">Loading…</p>
	{:else if loadError}
		<p class="p-4 text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if events.length === 0}
		<p class="p-4 text-sm text-neutral-500">No events match the current filters.</p>
	{:else}
		<table class="w-full text-sm">
			<thead
				class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
			>
				<tr>
					<th class="px-4 py-2">When</th>
					<th class="px-4 py-2">Type</th>
					<th class="px-4 py-2">Device</th>
					<th class="px-4 py-2">Sensor</th>
					<th class="px-4 py-2">Trigger</th>
					<th class="px-4 py-2">Metadata</th>
				</tr>
			</thead>
			<tbody>
				{#each events as ev (ev.event_id)}
					<tr
						class="cursor-pointer border-t border-neutral-100 hover:bg-neutral-50 dark:border-neutral-800/50 dark:hover:bg-neutral-800/40"
						onclick={() => expand(ev.event_id)}
					>
						<td class="px-4 py-2 font-mono text-xs">
							{new Date(ev.triggered_at).toLocaleString()}
						</td>
						<td class="px-4 py-2">
							<span
								class="rounded bg-neutral-100 px-2 py-0.5 font-mono text-xs dark:bg-neutral-800"
							>
								{ev.event_type}
							</span>
						</td>
						<td class="px-4 py-2 font-mono">{ev.device_id}</td>
						<td class="px-4 py-2 font-mono">{ev.sensor_id}</td>
						<td class="px-4 py-2 font-mono">{ev.triggered_value.toFixed(2)}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-500">
							{shortMetadata(ev.metadata)}
						</td>
					</tr>
					{#if expandedId === ev.event_id}
						<tr class="bg-neutral-50 dark:bg-neutral-800/30">
							<td colspan="6" class="px-4 py-3">
								{#if expandedError}
									<p class="text-sm text-red-600 dark:text-red-400">{expandedError}</p>
								{:else if !expandedWindow}
									<p class="text-sm text-neutral-500">Loading window…</p>
								{:else}
									<div class="text-xs">
										<div class="mb-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
											<div><span class="text-neutral-500">Window:</span> {new Date(expandedWindow.start_ts).toLocaleTimeString()} → {new Date(expandedWindow.end_ts).toLocaleTimeString()}</div>
											<div><span class="text-neutral-500">Samples:</span> {expandedWindow.sample_count}</div>
											<div><span class="text-neutral-500">Rate:</span> {expandedWindow.sample_rate_hz} Hz</div>
											<div><span class="text-neutral-500">Encoding:</span> {expandedWindow.encoding}</div>
										</div>
										<details>
											<summary class="cursor-pointer text-neutral-500">Metadata</summary>
											<pre
												class="mt-2 overflow-x-auto rounded bg-white p-2 font-mono dark:bg-neutral-950"
											>{JSON.stringify(ev.metadata, null, 2)}</pre>
										</details>
									</div>
								{/if}
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	{/if}
</section>

{#if !isLoading && !loadError}
	<div class="mt-4 flex items-center justify-between text-sm text-neutral-500">
		<div>
			{events.length === 0
				? 'no rows'
				: `rows ${offset + 1}–${offset + events.length}`}
		</div>
		<div class="flex gap-2">
			<button
				type="button"
				onclick={prevPage}
				disabled={offset === 0}
				class="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
			>
				Prev
			</button>
			<button
				type="button"
				onclick={nextPage}
				disabled={events.length < limit}
				class="rounded border border-neutral-300 px-3 py-1 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700"
			>
				Next
			</button>
		</div>
	</div>
{/if}
