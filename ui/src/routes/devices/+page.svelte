<script lang="ts">
	/*
	 * Devices page — list, create, soft-disable.
	 *
	 * Uses /api/devices. Backed by the existing CRUD endpoints from
	 * Phase 3a. Delete is intentionally NOT exposed here: legacy lesson
	 * is that operators occasionally fat-finger a delete and lose event
	 * history. They flip is_active=false instead, which preserves the FK.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type { DeviceIn, DeviceOut } from '$lib/types';

	let devices = $state<DeviceOut[]>([]);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);

	let newDeviceId = $state<number | null>(null);
	let newDeviceName = $state('');
	let newTopic = $state('');
	let createError = $state<string | null>(null);
	let isSubmitting = $state(false);

	async function loadDevices() {
		isLoading = true;
		loadError = null;
		try {
			devices = await api.get<DeviceOut[]>('/api/devices');
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	async function createDevice(event: SubmitEvent) {
		event.preventDefault();
		if (newDeviceId === null || !newDeviceName.trim()) return;

		const payload: DeviceIn = {
			device_id: newDeviceId,
			name: newDeviceName.trim()
		};
		if (newTopic.trim()) {
			payload.topic = newTopic.trim();
		}

		isSubmitting = true;
		createError = null;
		try {
			const created = await api.post<DeviceOut>('/api/devices', payload);
			devices = [...devices, created].sort((a, b) => a.device_id - b.device_id);
			newDeviceId = null;
			newDeviceName = '';
			newTopic = '';
		} catch (e) {
			createError = e instanceof ApiError ? e.detail : 'request failed';
		} finally {
			isSubmitting = false;
		}
	}

	async function toggleActive(device: DeviceOut) {
		try {
			const updated = await api.patch<DeviceOut>(`/api/devices/${device.device_id}`, {
				is_active: !device.is_active
			});
			devices = devices.map((d) => (d.device_id === updated.device_id ? updated : d));
		} catch (e) {
			loadError = e instanceof ApiError ? e.detail : 'patch failed';
		}
	}

	onMount(loadDevices);
</script>

<svelte:head>
	<title>HERMES · Devices</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Devices</h1>
		<p class="mt-1 text-sm text-neutral-500">
			Each device is one MQTT-publishing data source (typically a 12-channel STM32).
		</p>
	</div>
</header>

<section
	class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
>
	<h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-500">
		Add device
	</h2>
	<form onsubmit={createDevice} class="grid grid-cols-1 items-end gap-3 sm:grid-cols-4">
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">ID (1–999)</span>
			<input
				type="number"
				min="1"
				max="999"
				bind:value={newDeviceId}
				required
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Name</span>
			<input
				type="text"
				bind:value={newDeviceName}
				required
				maxlength="120"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">MQTT topic (optional)</span>
			<input
				type="text"
				bind:value={newTopic}
				placeholder="stm32/adc"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<button
			type="submit"
			disabled={isSubmitting}
			class="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white transition-colors hover:bg-neutral-700 disabled:cursor-not-allowed disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
		>
			{isSubmitting ? 'Creating…' : 'Create'}
		</button>
	</form>
	{#if createError}
		<p class="mt-3 text-sm text-red-600 dark:text-red-400">{createError}</p>
	{/if}
</section>

<section class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
	{#if isLoading}
		<p class="p-4 text-sm text-neutral-500">Loading…</p>
	{:else if loadError}
		<p class="p-4 text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if devices.length === 0}
		<p class="p-4 text-sm text-neutral-500">
			No devices yet. Add one above to start receiving MQTT samples.
		</p>
	{:else}
		<table class="w-full text-sm">
			<thead class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800">
				<tr>
					<th class="px-4 py-2">ID</th>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Topic</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2 text-right">Actions</th>
				</tr>
			</thead>
			<tbody>
				{#each devices as d (d.device_id)}
					<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
						<td class="px-4 py-2 font-mono">{d.device_id}</td>
						<td class="px-4 py-2">
							<a
								href={`/devices/${d.device_id}`}
								class="text-neutral-900 hover:underline dark:text-neutral-100"
							>
								{d.name}
							</a>
						</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-500">
							{d.topic ?? '(default)'}
						</td>
						<td class="px-4 py-2">
							{#if d.is_active}
								<span class="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-400">active</span>
							{:else}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">disabled</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-right">
							<a
								href={`/devices/${d.device_id}`}
								class="mr-3 text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
							>
								Live
							</a>
							<button
								type="button"
								onclick={() => toggleActive(d)}
								class="text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
							>
								{d.is_active ? 'Disable' : 'Enable'}
							</button>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>
