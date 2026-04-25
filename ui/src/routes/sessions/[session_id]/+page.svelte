<script lang="ts">
	/*
	 * Per-session detail page (gap 5).
	 *
	 * Shows:
	 *   - Header with session id, scope, status (active / closed),
	 *     device (LOCAL only), package name with link to /api/packages/{id}.
	 *   - Lifecycle metadata: started_at, ended_at, started_by,
	 *     ended_reason, notes, record_raw_samples flag.
	 *   - Audit log timeline (session_logs rows in ascending order)
	 *     with the event type, actor, ts, and details JSON pretty-printed.
	 *
	 * If the session is still active, a Stop button is shown at the
	 * top — same flow as the index page. Stopping reloads to show the
	 * timeline's new STOP entry.
	 */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import type { PackageOut, SessionLogOut, SessionOut } from '$lib/types';

	let session = $state<SessionOut | null>(null);
	let logs = $state<SessionLogOut[]>([]);
	let pkg = $state<PackageOut | null>(null);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);
	let isStopping = $state(false);
	let stopError = $state<string | null>(null);

	const sessionId = $derived(page.params.session_id);

	async function reload() {
		isLoading = true;
		loadError = null;
		try {
			const [s, lg] = await Promise.all([
				api.get<SessionOut>(`/api/sessions/${sessionId}`),
				api.get<SessionLogOut[]>(`/api/sessions/${sessionId}/logs?order=asc`)
			]);
			session = s;
			logs = lg;
			// Best-effort package fetch — fine if it 404s (e.g. operator
			// archived it later); we just render the raw uuid.
			try {
				pkg = await api.get<PackageOut>(`/api/packages/${s.package_id}`);
			} catch {
				pkg = null;
			}
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	async function stopSession() {
		if (session === null) return;
		const reason = window.prompt('Optional reason (will be logged):', '');
		isStopping = true;
		stopError = null;
		try {
			await api.post<SessionOut>(
				`/api/sessions/${session.session_id}/stop`,
				reason ? { ended_reason: reason } : {}
			);
			await reload();
		} catch (e) {
			stopError = e instanceof ApiError ? e.detail : 'stop failed';
		} finally {
			isStopping = false;
		}
	}

	onMount(reload);

	// Map a session-log event name to a Tailwind colour class set.
	// Inline class:* directives don't accept slashes in the name
	// (Svelte parses them as token boundaries), so we render a single
	// pre-composed string instead. Tailwind's JIT picks them up because
	// each literal string here is statically present in the source.
	function logChipClass(event: SessionLogOut['event']): string {
		switch (event) {
			case 'start':
				return 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400';
			case 'stop':
				return 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-400';
			case 'pause':
			case 'reconfigure':
				return 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-400';
			case 'resume':
				return 'bg-neutral-200 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-300';
			case 'error':
				return 'bg-rose-200 text-rose-900 dark:bg-rose-900/40 dark:text-rose-300';
			default:
				return 'bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300';
		}
	}
</script>

<svelte:head>
	<title>HERMES · Session detail</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between">
	<div>
		<a href="/sessions" class="text-xs text-neutral-500 hover:underline">← all sessions</a>
		<h1 class="mt-1 text-2xl font-semibold tracking-tight">Session detail</h1>
	</div>
</header>

{#if isLoading}
	<p class="text-sm text-neutral-500">Loading…</p>
{:else if loadError || session === null}
	<div
		class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
	>
		{loadError ?? 'session not found'}
	</div>
{:else}
	<section
		class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
	>
		<div class="flex items-baseline justify-between">
			<div>
				<p class="font-mono text-xs text-neutral-500">{session.session_id}</p>
				<p class="mt-1 flex items-center gap-2">
					{#if session.scope === 'global'}
						<span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-900/40 dark:text-blue-400">global</span>
					{:else}
						<span class="rounded-full bg-purple-100 px-2 py-0.5 text-xs text-purple-800 dark:bg-purple-900/40 dark:text-purple-400">local · device {session.device_id}</span>
					{/if}
					{#if session.ended_at}
						<span class="rounded-full bg-neutral-200 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">closed</span>
					{:else}
						<span class="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-400">active</span>
					{/if}
				</p>
			</div>
			{#if !session.ended_at}
				<button
					type="button"
					onclick={stopSession}
					disabled={isStopping}
					class="rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-400"
				>
					{isStopping ? 'Stopping…' : 'Stop session'}
				</button>
			{/if}
		</div>
		{#if stopError}
			<p class="mt-2 text-sm text-red-600 dark:text-red-400">{stopError}</p>
		{/if}

		<dl class="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
			<div>
				<dt class="text-xs uppercase text-neutral-500">Started</dt>
				<dd class="mt-0.5">{new Date(session.started_at).toLocaleString()}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Ended</dt>
				<dd class="mt-0.5">{session.ended_at ? new Date(session.ended_at).toLocaleString() : '—'}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Started by</dt>
				<dd class="mt-0.5">{session.started_by ?? '—'}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Ended reason</dt>
				<dd class="mt-0.5">{session.ended_reason ?? '—'}</dd>
			</div>
			<div class="sm:col-span-2">
				<dt class="text-xs uppercase text-neutral-500">Notes</dt>
				<dd class="mt-0.5 whitespace-pre-wrap">{session.notes ?? '—'}</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Package</dt>
				<dd class="mt-0.5">
					<span class="font-mono text-xs">{session.package_id}</span>
					{#if pkg}
						<span class="ml-2 text-neutral-500">({pkg.name}{pkg.is_locked ? ', locked' : ''})</span>
					{/if}
				</dd>
			</div>
			<div>
				<dt class="text-xs uppercase text-neutral-500">Record raw samples</dt>
				<dd class="mt-0.5">{session.record_raw_samples ? 'yes' : 'no'}</dd>
			</div>
			{#if session.parent_session_id}
				<div class="sm:col-span-2">
					<dt class="text-xs uppercase text-neutral-500">Parent session</dt>
					<dd class="mt-0.5"><a href={`/sessions/${session.parent_session_id}`} class="font-mono text-xs hover:underline">{session.parent_session_id}</a></dd>
				</div>
			{/if}
		</dl>
	</section>

	<section
		class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
	>
		<header class="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
			<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">Audit log</h2>
		</header>
		{#if logs.length === 0}
			<p class="p-4 text-sm text-neutral-500">No log entries yet.</p>
		{:else}
			<ol class="divide-y divide-neutral-100 dark:divide-neutral-800/50">
				{#each logs as l (l.log_id)}
					<li class="flex items-start gap-3 p-3 text-sm">
						<span class={['mt-0.5 inline-block rounded-full px-2 py-0.5 text-xs', logChipClass(l.event)]}>
							{l.event}
						</span>
						<div class="flex-1">
							<p class="text-xs text-neutral-500">
								{new Date(l.ts).toLocaleString()} · {l.actor ?? '—'}
							</p>
							{#if l.details}
								<pre class="mt-1 overflow-x-auto rounded bg-neutral-50 p-2 text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">{JSON.stringify(l.details, null, 2)}</pre>
							{/if}
						</div>
					</li>
				{/each}
			</ol>
		{/if}
	</section>
{/if}
