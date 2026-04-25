<script lang="ts">
	/*
	 * Sessions page (gap 5 — alpha.19).
	 *
	 * Operator-driven session lifecycle: see what's running, start a
	 * fresh session against a package, stop the running one, and
	 * inspect the audit log for any session.
	 *
	 * UX shape:
	 *   - "Active" panel at the top: the global session + any LOCAL
	 *     children. Each row carries a Stop button.
	 *   - "Start session" form: scope (global/local) + package picker +
	 *     optional device + optional notes + record_raw_samples toggle.
	 *   - "Recent sessions" list: closed sessions newest first. Each
	 *     row links to the per-session detail page (./sessions/[id]).
	 *
	 * Important UX guarantees:
	 *   - Starting a global session while one is active returns 409.
	 *     We surface that as a clear error, not a generic failure —
	 *     the operator must explicitly stop the running one first.
	 *   - The form auto-hides device picker when scope=global and
	 *     auto-shows it when scope=local.
	 *   - The package picker shows is_locked / is_default / parent
	 *     metadata so the operator can pick correctly.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type {
		CurrentSessionsOut,
		PackageOut,
		SessionOut,
		SessionScope,
		SessionStart
	} from '$lib/types';

	let current = $state<CurrentSessionsOut>({ global_session: null, local_sessions: [] });
	let recent = $state<SessionOut[]>([]);
	let packages = $state<PackageOut[]>([]);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);

	// Start-form state.
	let formScope = $state<SessionScope>('global');
	let formPackageId = $state<string>('');
	let formDeviceId = $state<number | null>(null);
	let formNotes = $state('');
	let formRecordRaw = $state(false);
	let formError = $state<string | null>(null);
	let isSubmitting = $state(false);

	// Per-row stop state — keyed by session_id.
	let stoppingId = $state<string | null>(null);
	let rowError = $state<{ id: string; message: string } | null>(null);

	async function reload() {
		isLoading = true;
		loadError = null;
		try {
			[current, recent, packages] = await Promise.all([
				api.get<CurrentSessionsOut>('/api/sessions/current'),
				api.get<SessionOut[]>('/api/sessions?active=false&limit=20'),
				api.get<PackageOut[]>('/api/packages')
			]);
			// Pick a sensible default for the package dropdown if none
			// is set yet — the default package or the newest unlocked.
			if (!formPackageId && packages.length > 0) {
				const def = packages.find((p) => p.is_default);
				const unlocked = packages.find((p) => !p.is_locked);
				const fallback = packages[0];
				formPackageId =
					def?.package_id ?? unlocked?.package_id ?? fallback?.package_id ?? '';
			}
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	async function startSession(event: SubmitEvent) {
		event.preventDefault();
		if (!formPackageId) {
			formError = 'pick a package';
			return;
		}
		if (formScope === 'local' && formDeviceId === null) {
			formError = 'local sessions need a device id';
			return;
		}

		const payload: SessionStart = {
			scope: formScope,
			package_id: formPackageId
		};
		if (formScope === 'local' && formDeviceId !== null) {
			payload.device_id = formDeviceId;
		}
		if (formNotes.trim()) {
			payload.notes = formNotes.trim();
		}
		if (formRecordRaw) {
			payload.record_raw_samples = true;
		}

		isSubmitting = true;
		formError = null;
		try {
			await api.post<SessionOut>('/api/sessions', payload);
			formNotes = '';
			formDeviceId = null;
			formRecordRaw = false;
			await reload();
		} catch (e) {
			formError = e instanceof ApiError ? e.detail : 'request failed';
		} finally {
			isSubmitting = false;
		}
	}

	async function stopSession(s: SessionOut) {
		const reason = window.prompt('Optional reason (will be logged):', '');
		// Empty string is fine — it just won't be stored.
		stoppingId = s.session_id;
		rowError = null;
		try {
			await api.post<SessionOut>(
				`/api/sessions/${s.session_id}/stop`,
				reason ? { ended_reason: reason } : {}
			);
			await reload();
		} catch (e) {
			rowError = {
				id: s.session_id,
				message: e instanceof ApiError ? e.detail : 'stop failed'
			};
		} finally {
			stoppingId = null;
		}
	}

	function packageLabel(p: PackageOut): string {
		const tags: string[] = [];
		if (p.is_default) tags.push('default');
		if (p.is_locked) tags.push('locked');
		const suffix = tags.length ? ` (${tags.join(', ')})` : '';
		return `${p.name}${suffix}`;
	}

	function packageNameFor(packageId: string): string {
		const pkg = packages.find((p) => p.package_id === packageId);
		return pkg ? pkg.name : packageId.slice(0, 8) + '…';
	}

	function durationLabel(started: string, ended: string | null): string {
		const startMs = new Date(started).getTime();
		const endMs = ended ? new Date(ended).getTime() : Date.now();
		const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
		if (seconds < 60) return `${seconds}s`;
		const mins = Math.floor(seconds / 60);
		if (mins < 60) return `${mins}m ${seconds % 60}s`;
		const hours = Math.floor(mins / 60);
		return `${hours}h ${mins % 60}m`;
	}

	function shortId(id: string): string {
		return id.slice(0, 8);
	}

	onMount(reload);
</script>

<svelte:head>
	<title>HERMES · Sessions</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Sessions</h1>
		<p class="mt-1 text-sm text-neutral-500">
			A session binds detected events to the configuration package that was active when they fired.
			At most one global session is active at a time; LOCAL sessions override the global per device.
		</p>
	</div>
</header>

{#if loadError}
	<div
		class="mb-6 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
	>
		{loadError}
	</div>
{/if}

<!-- Active sessions -->
<section
	class="mb-6 rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
>
	<header class="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
		<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">Active</h2>
	</header>
	{#if isLoading}
		<p class="p-4 text-sm text-neutral-500">Loading…</p>
	{:else if current.global_session === null && current.local_sessions.length === 0}
		<p class="p-4 text-sm text-neutral-500">
			No active sessions. Start one below to begin attributing events.
		</p>
	{:else}
		<table class="w-full text-sm">
			<thead
				class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
			>
				<tr>
					<th class="px-4 py-2">Scope</th>
					<th class="px-4 py-2">Device</th>
					<th class="px-4 py-2">Package</th>
					<th class="px-4 py-2">Started</th>
					<th class="px-4 py-2">Duration</th>
					<th class="px-4 py-2 text-right">Actions</th>
				</tr>
			</thead>
			<tbody>
				{#if current.global_session}
					{@const s = current.global_session}
					<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
						<td class="px-4 py-2"><span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-900/40 dark:text-blue-400">global</span></td>
						<td class="px-4 py-2 text-neutral-500">—</td>
						<td class="px-4 py-2"><a href={`/sessions/${s.session_id}`} class="hover:underline">{packageNameFor(s.package_id)}</a></td>
						<td class="px-4 py-2 text-xs text-neutral-500">{new Date(s.started_at).toLocaleString()}</td>
						<td class="px-4 py-2 font-mono text-xs">{durationLabel(s.started_at, null)}</td>
						<td class="px-4 py-2 text-right">
							<button
								type="button"
								onclick={() => stopSession(s)}
								disabled={stoppingId === s.session_id}
								class="rounded-md bg-red-600 px-2 py-1 text-xs text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-400"
							>
								{stoppingId === s.session_id ? 'Stopping…' : 'Stop'}
							</button>
							{#if rowError && rowError.id === s.session_id}
								<p class="mt-1 text-xs text-red-600 dark:text-red-400">{rowError.message}</p>
							{/if}
						</td>
					</tr>
				{/if}
				{#each current.local_sessions as s (s.session_id)}
					<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
						<td class="px-4 py-2"><span class="rounded-full bg-purple-100 px-2 py-0.5 text-xs text-purple-800 dark:bg-purple-900/40 dark:text-purple-400">local</span></td>
						<td class="px-4 py-2 font-mono">{s.device_id}</td>
						<td class="px-4 py-2"><a href={`/sessions/${s.session_id}`} class="hover:underline">{packageNameFor(s.package_id)}</a></td>
						<td class="px-4 py-2 text-xs text-neutral-500">{new Date(s.started_at).toLocaleString()}</td>
						<td class="px-4 py-2 font-mono text-xs">{durationLabel(s.started_at, null)}</td>
						<td class="px-4 py-2 text-right">
							<button
								type="button"
								onclick={() => stopSession(s)}
								disabled={stoppingId === s.session_id}
								class="rounded-md bg-red-600 px-2 py-1 text-xs text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-400"
							>
								{stoppingId === s.session_id ? 'Stopping…' : 'Stop'}
							</button>
							{#if rowError && rowError.id === s.session_id}
								<p class="mt-1 text-xs text-red-600 dark:text-red-400">{rowError.message}</p>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>

<!-- Start new session -->
<section
	class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
>
	<h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-500">Start session</h2>
	<form onsubmit={startSession} class="grid grid-cols-1 items-end gap-3 sm:grid-cols-3">
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Scope</span>
			<select
				bind:value={formScope}
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			>
				<option value="global">global (system-wide)</option>
				<option value="local">local (per-device override)</option>
			</select>
		</label>

		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Package</span>
			<select
				bind:value={formPackageId}
				required
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			>
				{#each packages as p (p.package_id)}
					<option value={p.package_id}>{packageLabel(p)}</option>
				{/each}
			</select>
		</label>

		{#if formScope === 'local'}
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Device ID (1–999)</span>
				<input
					type="number"
					min="1"
					max="999"
					bind:value={formDeviceId}
					required
					class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
				/>
			</label>
		{/if}

		<label class="flex flex-col gap-1 text-sm sm:col-span-2">
			<span class="text-neutral-500">Notes (optional)</span>
			<input
				type="text"
				bind:value={formNotes}
				maxlength="2000"
				placeholder="What is this session for?"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>

		<label class="flex items-center gap-2 text-sm">
			<input
				type="checkbox"
				bind:checked={formRecordRaw}
				class="h-4 w-4 rounded border-neutral-300 dark:border-neutral-700"
			/>
			<span>Record raw samples</span>
		</label>

		<div class="sm:col-span-3">
			<button
				type="submit"
				disabled={isSubmitting}
				class="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white transition-colors hover:bg-neutral-700 disabled:cursor-not-allowed disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
			>
				{isSubmitting ? 'Starting…' : 'Start session'}
			</button>
		</div>
	</form>
	{#if formError}
		<p class="mt-3 text-sm text-red-600 dark:text-red-400">{formError}</p>
	{/if}
</section>

<!-- Recent sessions -->
<section
	class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
>
	<header class="border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
		<h2 class="text-sm font-medium uppercase tracking-wide text-neutral-500">Recent (closed)</h2>
	</header>
	{#if isLoading}
		<p class="p-4 text-sm text-neutral-500">Loading…</p>
	{:else if recent.length === 0}
		<p class="p-4 text-sm text-neutral-500">No closed sessions yet.</p>
	{:else}
		<table class="w-full text-sm">
			<thead
				class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
			>
				<tr>
					<th class="px-4 py-2">ID</th>
					<th class="px-4 py-2">Scope</th>
					<th class="px-4 py-2">Device</th>
					<th class="px-4 py-2">Package</th>
					<th class="px-4 py-2">Started</th>
					<th class="px-4 py-2">Ended</th>
					<th class="px-4 py-2">Duration</th>
					<th class="px-4 py-2">Reason</th>
				</tr>
			</thead>
			<tbody>
				{#each recent as s (s.session_id)}
					<tr class="border-t border-neutral-100 dark:border-neutral-800/50">
						<td class="px-4 py-2"><a href={`/sessions/${s.session_id}`} class="font-mono text-xs text-neutral-600 hover:underline dark:text-neutral-400">{shortId(s.session_id)}</a></td>
						<td class="px-4 py-2">
							{#if s.scope === 'global'}
								<span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800 dark:bg-blue-900/40 dark:text-blue-400">global</span>
							{:else}
								<span class="rounded-full bg-purple-100 px-2 py-0.5 text-xs text-purple-800 dark:bg-purple-900/40 dark:text-purple-400">local</span>
							{/if}
						</td>
						<td class="px-4 py-2 font-mono">{s.device_id ?? '—'}</td>
						<td class="px-4 py-2">{packageNameFor(s.package_id)}</td>
						<td class="px-4 py-2 text-xs text-neutral-500">{new Date(s.started_at).toLocaleString()}</td>
						<td class="px-4 py-2 text-xs text-neutral-500">{s.ended_at ? new Date(s.ended_at).toLocaleString() : '—'}</td>
						<td class="px-4 py-2 font-mono text-xs">{durationLabel(s.started_at, s.ended_at)}</td>
						<td class="px-4 py-2 text-xs text-neutral-500">{s.ended_reason ?? '—'}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>
