<script lang="ts">
	/*
	 * Settings landing page (gap 8 — alpha.22).
	 *
	 * Read-only operator dashboard built on /api/system-tunables. Surfaces:
	 *   - Live system state (version, ingest mode, shard topology,
	 *     active session counts, recording status, device counts per
	 *     protocol).
	 *   - Boot-time runtime knobs with a clear "live editable" /
	 *     "via /api/config" / "needs restart" badge per row.
	 *
	 * Routes that ARE writable today:
	 *   - /config       — Type A/B/C/D + mode-switching thresholds
	 *   - /mqtt-brokers — broker registry
	 *   - /sessions     — start/stop sessions
	 *   - /devices      — per-device CRUD (incl. modbus_config)
	 *
	 * The page links to all four. Settings that need a service restart
	 * have their exact env-var name + systemd command shown so the
	 * operator can copy-paste into a terminal.
	 */
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { SystemTunablesOut, TunableEditable } from '$lib/types';

	let data = $state<SystemTunablesOut | null>(null);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);

	async function reload() {
		isLoading = true;
		loadError = null;
		try {
			data = await api.get<SystemTunablesOut>('/api/system-tunables');
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	function badgeClass(editable: TunableEditable): string {
		switch (editable) {
			case 'live':
				return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400';
			case 'via_other_route':
				return 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-400';
			case 'restart':
				return 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-300';
		}
	}

	function badgeLabel(editable: TunableEditable): string {
		switch (editable) {
			case 'live':
				return 'live';
			case 'via_other_route':
				return 'editable elsewhere';
			case 'restart':
				return 'restart needed';
		}
	}

	function formatValue(value: unknown): string {
		if (value === null || value === undefined) return '—';
		if (typeof value === 'boolean') return value ? 'true' : 'false';
		return String(value);
	}

	const linkedPages: Array<{ href: string; label: string; description: string }> = [
		{
			href: '/config',
			label: 'Detection thresholds',
			description: 'Type A/B/C/D + mode-switching parameters (live edits)'
		},
		{
			href: '/mqtt-brokers',
			label: 'MQTT brokers',
			description: 'Broker registry; restart hermes-ingest after activating a different row'
		},
		{
			href: '/sessions',
			label: 'Sessions',
			description: 'Start/stop sessions; toggle record_raw_samples'
		},
		{
			href: '/devices',
			label: 'Devices',
			description: 'Per-device CRUD + Modbus modbus_config'
		}
	];

	onMount(reload);
</script>

<svelte:head>
	<title>HERMES · Settings</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Settings</h1>
		<p class="mt-1 text-sm text-neutral-500">
			Read-only view of the live system state and boot-time tunable values.
			Linked pages below cover the editable surfaces.
		</p>
	</div>
	<button
		type="button"
		onclick={reload}
		class="rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:bg-neutral-800"
	>
		Refresh
	</button>
</header>

{#if loadError}
	<div
		class="mb-6 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
	>
		{loadError}
	</div>
{/if}

<!-- Live state -->
{#if !isLoading && data}
	<section
		class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-500">
			System state
		</h2>
		<dl class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
			<div>
				<dt class="text-xs uppercase text-neutral-500">Version</dt>
				<dd class="mt-0.5 font-mono">{data.state.version}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Ingest mode</dt>
				<dd class="mt-0.5 font-mono">{data.state.ingest_mode}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Shard topology</dt>
				<dd class="mt-0.5 font-mono">
					{data.state.shard_index} of {data.state.shard_count}
				</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Log format</dt>
				<dd class="mt-0.5 font-mono">{data.state.log_format}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Dev mode</dt>
				<dd class="mt-0.5">
					{#if data.state.dev_mode}
						<span class="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-300">enabled</span>
					{:else}
						<span class="text-neutral-500">off</span>
					{/if}
				</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Active GLOBAL session</dt>
				<dd class="mt-0.5">
					{#if data.state.active_global_session_id}
						<a class="font-mono text-xs hover:underline" href={`/sessions/${data.state.active_global_session_id}`}>
							{data.state.active_global_session_id.slice(0, 8)}…
						</a>
					{:else}
						<span class="text-neutral-500">none</span>
					{/if}
				</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Active LOCAL sessions</dt>
				<dd class="mt-0.5">{data.state.active_local_session_count}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Sessions recording</dt>
				<dd class="mt-0.5">
					{data.state.sessions_recording_count}
					{#if data.state.sessions_recording_count > 0}
						<span class="ml-2 rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-400">archive on</span>
					{/if}
				</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">MQTT devices active</dt>
				<dd class="mt-0.5">{data.state.mqtt_devices_active}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Modbus devices active</dt>
				<dd class="mt-0.5">{data.state.modbus_devices_active}</dd>
			</div>
		</dl>
	</section>
{:else}
	<p class="text-sm text-neutral-500">Loading…</p>
{/if}

<!-- Editable elsewhere -->
<section
	class="mb-6 rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
>
	<header class="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
		<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">
			Editable from other pages
		</h2>
	</header>
	<ul class="divide-y divide-neutral-100 dark:divide-neutral-800/50">
		{#each linkedPages as p (p.href)}
			<li class="flex items-baseline gap-3 p-3 text-sm">
				<a href={p.href} class="font-medium hover:underline">{p.label}</a>
				<span class="text-xs text-neutral-500">{p.description}</span>
			</li>
		{/each}
	</ul>
</section>

<!-- Boot-time tunables -->
{#if !isLoading && data}
	<section
		class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
	>
		<header class="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
			<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">
				Boot-time tunables
			</h2>
		</header>
		<table class="w-full text-sm">
			<thead
				class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
			>
				<tr>
					<th class="px-4 py-2">Key</th>
					<th class="px-4 py-2">Value</th>
					<th class="px-4 py-2">Editability</th>
					<th class="px-4 py-2">Description / How to change</th>
				</tr>
			</thead>
			<tbody>
				{#each data.tunables as row (row.key)}
					<tr class="border-t border-neutral-100 align-top dark:border-neutral-800/50">
						<td class="px-4 py-2 font-mono text-xs">{row.key}</td>
						<td class="px-4 py-2 font-mono text-xs">{formatValue(row.value)}</td>
						<td class="px-4 py-2">
							<span class={`rounded-full px-2 py-0.5 text-xs ${badgeClass(row.editable)}`}>
								{badgeLabel(row.editable)}
							</span>
						</td>
						<td class="px-4 py-2 text-xs text-neutral-700 dark:text-neutral-300">
							{row.description}
							{#if row.edit_hint}
								<div class="mt-1 font-mono text-[11px] text-neutral-500">{row.edit_hint}</div>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</section>
{/if}
