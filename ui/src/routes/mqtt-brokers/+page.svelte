<script lang="ts">
	/*
	 * MQTT brokers page (gap 4 — alpha.18).
	 *
	 * Operator-managed registry of MQTT broker connection info. The
	 * partial unique index on the table guarantees at most one row is
	 * active at a time; this page exposes the activate/deactivate flow.
	 *
	 * Password handling — the API NEVER returns the plaintext (only a
	 * has_password boolean). On the form:
	 *
	 *   - Create: typing a password stores it (encrypted on the server).
	 *   - Edit:   leaving the field blank is "leave the stored password
	 *             alone". Typing a new value replaces it. Clicking the
	 *             "Clear" button sends an empty string, which clears it.
	 *
	 * Live broker switchover is NOT yet wired — flipping the active row
	 * does NOT reconnect a running hermes-ingest. The operator must
	 * restart the service after activating a different broker. The
	 * help banner says so explicitly so nobody is surprised.
	 */
	import { onMount } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import type { MqttBrokerIn, MqttBrokerOut, MqttBrokerPatch } from '$lib/types';

	let brokers = $state<MqttBrokerOut[]>([]);
	let loadError = $state<string | null>(null);
	let isLoading = $state(true);

	// "Add broker" form state.
	let newHost = $state('');
	let newPort = $state(1883);
	let newUsername = $state('');
	let newPassword = $state('');
	let newUseTls = $state(false);
	let newIsActive = $state(true);
	let createError = $state<string | null>(null);
	let isSubmitting = $state(false);

	// Per-row "edit password" state — keyed by broker_id.
	let editingPasswordFor = $state<number | null>(null);
	let passwordDraft = $state('');
	let rowError = $state<{ id: number; message: string } | null>(null);

	async function loadBrokers() {
		isLoading = true;
		loadError = null;
		try {
			brokers = await api.get<MqttBrokerOut[]>('/api/mqtt-brokers');
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'unknown';
		} finally {
			isLoading = false;
		}
	}

	async function createBroker(event: SubmitEvent) {
		event.preventDefault();
		if (!newHost.trim()) return;

		const payload: MqttBrokerIn = {
			host: newHost.trim(),
			port: newPort,
			username: newUsername.trim() ? newUsername.trim() : null,
			password: newPassword ? newPassword : null,
			use_tls: newUseTls,
			is_active: newIsActive
		};

		isSubmitting = true;
		createError = null;
		try {
			await api.post<MqttBrokerOut>('/api/mqtt-brokers', payload);
			// Refresh the whole list because creating an active broker
			// deactivates every other row server-side.
			await loadBrokers();
			newHost = '';
			newPort = 1883;
			newUsername = '';
			newPassword = '';
			newUseTls = false;
			newIsActive = true;
		} catch (e) {
			createError = e instanceof ApiError ? e.detail : 'request failed';
		} finally {
			isSubmitting = false;
		}
	}

	async function activate(broker: MqttBrokerOut) {
		try {
			await api.post<MqttBrokerOut>(`/api/mqtt-brokers/${broker.broker_id}/activate`, {});
			await loadBrokers();
		} catch (e) {
			rowError = {
				id: broker.broker_id,
				message: e instanceof ApiError ? e.detail : 'activate failed'
			};
		}
	}

	async function deactivate(broker: MqttBrokerOut) {
		try {
			await api.patch<MqttBrokerOut>(`/api/mqtt-brokers/${broker.broker_id}`, {
				is_active: false
			});
			await loadBrokers();
		} catch (e) {
			rowError = {
				id: broker.broker_id,
				message: e instanceof ApiError ? e.detail : 'deactivate failed'
			};
		}
	}

	async function clearPassword(broker: MqttBrokerOut) {
		try {
			const patch: MqttBrokerPatch = { password: '' };
			await api.patch<MqttBrokerOut>(`/api/mqtt-brokers/${broker.broker_id}`, patch);
			await loadBrokers();
		} catch (e) {
			rowError = {
				id: broker.broker_id,
				message: e instanceof ApiError ? e.detail : 'clear failed'
			};
		}
	}

	function openPasswordEditor(broker: MqttBrokerOut) {
		editingPasswordFor = broker.broker_id;
		passwordDraft = '';
		rowError = null;
	}

	async function savePasswordEditor() {
		const targetId = editingPasswordFor;
		if (targetId === null || !passwordDraft) {
			editingPasswordFor = null;
			passwordDraft = '';
			return;
		}
		try {
			await api.patch<MqttBrokerOut>(`/api/mqtt-brokers/${targetId}`, {
				password: passwordDraft
			});
			editingPasswordFor = null;
			passwordDraft = '';
			await loadBrokers();
		} catch (e) {
			rowError = {
				id: targetId,
				message: e instanceof ApiError ? e.detail : 'save failed'
			};
		}
	}

	function cancelPasswordEditor() {
		editingPasswordFor = null;
		passwordDraft = '';
	}

	async function deleteBroker(broker: MqttBrokerOut) {
		if (!confirm(`Delete broker ${broker.host}:${broker.port}?`)) return;
		try {
			await api.del<void>(`/api/mqtt-brokers/${broker.broker_id}`);
			await loadBrokers();
		} catch (e) {
			rowError = {
				id: broker.broker_id,
				message: e instanceof ApiError ? e.detail : 'delete failed'
			};
		}
	}

	onMount(loadBrokers);
</script>

<svelte:head>
	<title>HERMES · MQTT brokers</title>
</svelte:head>

<header class="mb-6 flex items-baseline justify-between">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">MQTT brokers</h1>
		<p class="mt-1 text-sm text-neutral-500">
			Connection details for the upstream MQTT broker(s) that publish STM32 telemetry.
			At most one row is active at a time.
		</p>
	</div>
</header>

<aside
	class="mb-6 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
>
	<strong>Heads up:</strong>
	flipping the active broker here does <em>not</em> reconnect a running
	<code>hermes-ingest</code>. After activating a different broker, run
	<code class="font-mono">systemctl restart hermes-ingest</code>
	on the host. Live re-connection lands in a follow-up release.
</aside>

<section
	class="mb-6 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
>
	<h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-neutral-500">Add broker</h2>
	<form onsubmit={createBroker} class="grid grid-cols-1 items-end gap-3 sm:grid-cols-3">
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Host</span>
			<input
				type="text"
				bind:value={newHost}
				required
				maxlength="255"
				placeholder="broker.example.com"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Port</span>
			<input
				type="number"
				min="1"
				max="65535"
				bind:value={newPort}
				required
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Username (optional)</span>
			<input
				type="text"
				bind:value={newUsername}
				maxlength="255"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-neutral-500">Password (optional)</span>
			<input
				type="password"
				bind:value={newPassword}
				maxlength="255"
				autocomplete="new-password"
				class="rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-950"
			/>
		</label>
		<label class="flex items-center gap-2 text-sm">
			<input
				type="checkbox"
				bind:checked={newUseTls}
				class="h-4 w-4 rounded border-neutral-300 dark:border-neutral-700"
			/>
			<span>Use TLS</span>
		</label>
		<label class="flex items-center gap-2 text-sm">
			<input
				type="checkbox"
				bind:checked={newIsActive}
				class="h-4 w-4 rounded border-neutral-300 dark:border-neutral-700"
			/>
			<span>Set as active</span>
		</label>
		<div class="sm:col-span-3">
			<button
				type="submit"
				disabled={isSubmitting}
				class="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white transition-colors hover:bg-neutral-700 disabled:cursor-not-allowed disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
			>
				{isSubmitting ? 'Creating…' : 'Create broker'}
			</button>
		</div>
	</form>
	{#if createError}
		<p class="mt-3 text-sm text-red-600 dark:text-red-400">{createError}</p>
	{/if}
</section>

<section
	class="rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
>
	{#if isLoading}
		<p class="p-4 text-sm text-neutral-500">Loading…</p>
	{:else if loadError}
		<p class="p-4 text-sm text-red-600 dark:text-red-400">{loadError}</p>
	{:else if brokers.length === 0}
		<p class="p-4 text-sm text-neutral-500">
			No brokers configured. Add one above; the ingest service will use it on next start.
		</p>
	{:else}
		<table class="w-full text-sm">
			<thead
				class="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500 dark:border-neutral-800"
			>
				<tr>
					<th class="px-4 py-2">Host</th>
					<th class="px-4 py-2">Port</th>
					<th class="px-4 py-2">Auth</th>
					<th class="px-4 py-2">TLS</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2 text-right">Actions</th>
				</tr>
			</thead>
			<tbody>
				{#each brokers as b (b.broker_id)}
					<tr class="border-t border-neutral-100 align-top dark:border-neutral-800/50">
						<td class="px-4 py-2 font-mono">{b.host}</td>
						<td class="px-4 py-2 font-mono">{b.port}</td>
						<td class="px-4 py-2 text-xs text-neutral-500">
							{#if b.username && b.has_password}
								<code class="font-mono">{b.username}</code> / ●●●●●●
							{:else if b.username}
								<code class="font-mono">{b.username}</code> / (no password)
							{:else}
								<span>(anonymous)</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-xs">
							{b.use_tls ? 'yes' : 'no'}
						</td>
						<td class="px-4 py-2">
							{#if b.is_active}
								<span
									class="rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-800 dark:bg-green-900/40 dark:text-green-400"
								>active</span>
							{:else}
								<span
									class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
								>inactive</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-right">
							{#if editingPasswordFor === b.broker_id}
								<div class="flex justify-end gap-2">
									<input
										type="password"
										bind:value={passwordDraft}
										placeholder="new password"
										autocomplete="new-password"
										class="w-40 rounded-md border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-950"
									/>
									<button
										type="button"
										onclick={savePasswordEditor}
										class="rounded-md bg-neutral-900 px-2 py-1 text-xs text-white hover:bg-neutral-700 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
									>Save</button>
									<button
										type="button"
										onclick={cancelPasswordEditor}
										class="rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
									>Cancel</button>
								</div>
							{:else}
								{#if b.is_active}
									<button
										type="button"
										onclick={() => deactivate(b)}
										class="mr-3 text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
									>Deactivate</button>
								{:else}
									<button
										type="button"
										onclick={() => activate(b)}
										class="mr-3 text-xs text-green-700 underline hover:text-green-900 dark:text-green-400 dark:hover:text-green-300"
									>Activate</button>
								{/if}
								<button
									type="button"
									onclick={() => openPasswordEditor(b)}
									class="mr-3 text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
								>Set password</button>
								{#if b.has_password}
									<button
										type="button"
										onclick={() => clearPassword(b)}
										class="mr-3 text-xs text-neutral-600 underline hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
									>Clear password</button>
								{/if}
								<button
									type="button"
									onclick={() => deleteBroker(b)}
									class="text-xs text-red-600 underline hover:text-red-800 dark:text-red-400 dark:hover:text-red-300"
								>Delete</button>
							{/if}
							{#if rowError && rowError.id === b.broker_id}
								<p class="mt-1 text-xs text-red-600 dark:text-red-400">{rowError.message}</p>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</section>
