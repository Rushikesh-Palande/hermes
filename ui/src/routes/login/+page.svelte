<script lang="ts">
	/*
	 * Login page — two-step OTP flow.
	 *
	 * Step 1: operator enters their email; we POST /api/auth/otp/request.
	 *         The server is silent on whether the address is allowlisted
	 *         (always 204) so we don't gate the UI on it either — we
	 *         move to step 2 unconditionally and let the verify endpoint
	 *         decide.
	 *
	 * Step 2: operator enters the code; we POST /api/auth/otp/verify and
	 *         on success store the JWT in localStorage and navigate
	 *         home.
	 */
	import { goto } from '$app/navigation';
	import { api, ApiError, setStoredToken } from '$lib/api';

	type Step = 'email' | 'code';

	let step = $state<Step>('email');
	let email = $state('');
	let code = $state('');
	let loading = $state(false);
	let errorMessage = $state<string | null>(null);

	interface TokenResponse {
		access_token: string;
		token_type: string;
		expires_in: number;
	}

	async function requestOtp(event: SubmitEvent) {
		event.preventDefault();
		errorMessage = null;
		loading = true;
		try {
			await api.post('/api/auth/otp/request', { email });
			step = 'code';
		} catch (e) {
			errorMessage = e instanceof ApiError ? e.detail : 'request failed';
		} finally {
			loading = false;
		}
	}

	async function verifyOtp(event: SubmitEvent) {
		event.preventDefault();
		errorMessage = null;
		loading = true;
		try {
			const resp = await api.post<TokenResponse>('/api/auth/otp/verify', {
				email,
				otp: code
			});
			setStoredToken(resp.access_token);
			await goto('/');
		} catch (e) {
			errorMessage = e instanceof ApiError ? e.detail : 'verify failed';
		} finally {
			loading = false;
		}
	}

	function backToEmail() {
		step = 'email';
		code = '';
		errorMessage = null;
	}
</script>

<svelte:head>
	<title>HERMES · Login</title>
</svelte:head>

<section class="mx-auto flex min-h-[60vh] max-w-sm flex-col justify-center gap-6 p-6">
	<header>
		<h1 class="text-2xl font-semibold tracking-tight">Sign in</h1>
		<p class="mt-1 text-sm text-neutral-500">
			{#if step === 'email'}
				Enter your email to receive a 6-digit login code.
			{:else}
				Enter the 6-digit code we sent to <span class="font-mono">{email}</span>.
			{/if}
		</p>
	</header>

	{#if errorMessage}
		<p
			class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-900/20 dark:text-red-400"
		>
			{errorMessage}
		</p>
	{/if}

	{#if step === 'email'}
		<form onsubmit={requestOtp} class="flex flex-col gap-3">
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">Email</span>
				<input
					type="email"
					autocomplete="email"
					bind:value={email}
					required
					class="rounded-md border border-neutral-300 bg-white px-3 py-2 dark:border-neutral-700 dark:bg-neutral-950"
				/>
			</label>
			<button
				type="submit"
				disabled={loading || !email}
				class="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
			>
				{loading ? 'Sending…' : 'Send code'}
			</button>
		</form>
	{:else}
		<form onsubmit={verifyOtp} class="flex flex-col gap-3">
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-neutral-500">6-digit code</span>
				<input
					type="text"
					inputmode="numeric"
					pattern="\d{6}"
					maxlength="6"
					autocomplete="one-time-code"
					bind:value={code}
					required
					class="rounded-md border border-neutral-300 bg-white px-3 py-2 font-mono text-lg tracking-[0.3em] dark:border-neutral-700 dark:bg-neutral-950"
				/>
			</label>
			<button
				type="submit"
				disabled={loading || code.length !== 6}
				class="rounded-md bg-neutral-900 px-3 py-2 text-sm text-white hover:bg-neutral-700 disabled:bg-neutral-400 dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-300"
			>
				{loading ? 'Signing in…' : 'Sign in'}
			</button>
			<button
				type="button"
				onclick={backToEmail}
				class="text-xs text-neutral-500 underline hover:text-neutral-900 dark:hover:text-neutral-100"
			>
				← change email
			</button>
		</form>
	{/if}
</section>
