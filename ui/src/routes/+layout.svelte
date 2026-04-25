<script lang="ts">
	/*
	 * Root layout: header + nav + content area + auth guard.
	 *
	 * Auth model (Phase 3.5):
	 *   * Login page (/login) does its own thing — no nav, no guard.
	 *   * Every other page requires a token in localStorage; if there
	 *     isn't one the layout sends the user to /login with a `next=`
	 *     hint so we can return them after a successful sign-in.
	 *   * Dev-mode bypass on the backend still works without a token,
	 *     but the UI is opinionated and pushes everyone through the
	 *     login flow regardless. Toggle ``HERMES_DEV_MODE`` AND open
	 *     the dashboard from the login page in that case — or skip the
	 *     UI and call the API with curl.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import '../app.css';
	import { clearStoredToken, getStoredToken } from '$lib/api';

	interface Props {
		children?: import('svelte').Snippet;
	}
	let { children }: Props = $props();

	const navItems: Array<{ href: string; label: string }> = [
		{ href: '/', label: 'Overview' },
		{ href: '/devices', label: 'Devices' },
		{ href: '/events', label: 'Events' },
		{ href: '/config', label: 'Config' },
		{ href: '/mqtt-brokers', label: 'MQTT' }
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}

	const isLoginPage = $derived(page.url.pathname.startsWith('/login'));

	// Tracked so the "Sign out" button only renders when there's
	// actually a token to drop. Updated on mount + after route changes.
	let hasToken = $state(false);

	function refreshTokenState() {
		hasToken = getStoredToken() !== null;
	}

	onMount(refreshTokenState);

	$effect(() => {
		// Re-read on every page navigation so a fresh login or signout
		// flips the header state immediately.
		void page.url.pathname;
		refreshTokenState();

		if (!isLoginPage && !getStoredToken()) {
			void goto(`/login?next=${encodeURIComponent(page.url.pathname)}`);
		}
	});

	async function signOut() {
		clearStoredToken();
		hasToken = false;
		// Best-effort POST so the server can extend audit logs in the
		// future; ignore failure.
		try {
			await fetch('/api/auth/logout', { method: 'POST' });
		} catch {
			/* ignore */
		}
		await goto('/login');
	}
</script>

<div
	class="flex min-h-screen flex-col bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100"
>
	{#if !isLoginPage}
		<header
			class="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
		>
			<div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
				<a href="/" class="text-lg font-semibold tracking-tight">HERMES</a>
				<div class="flex items-center gap-4">
					<nav class="flex gap-1">
						{#each navItems as item (item.href)}
							<a
								href={item.href}
								class="rounded-md px-3 py-1.5 text-sm transition-colors"
								class:bg-neutral-200={isActive(item.href)}
								class:dark:bg-neutral-800={isActive(item.href)}
								class:text-neutral-900={isActive(item.href)}
								class:dark:text-neutral-100={isActive(item.href)}
								class:text-neutral-600={!isActive(item.href)}
								class:dark:text-neutral-400={!isActive(item.href)}
								class:hover:bg-neutral-100={!isActive(item.href)}
								class:dark:hover:bg-neutral-800={!isActive(item.href)}
							>
								{item.label}
							</a>
						{/each}
					</nav>
					{#if hasToken}
						<button
							type="button"
							onclick={signOut}
							class="text-xs text-neutral-500 underline hover:text-neutral-900 dark:hover:text-neutral-100"
						>
							Sign out
						</button>
					{/if}
				</div>
			</div>
		</header>
	{/if}

	<main class="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
		{@render children?.()}
	</main>

	<footer
		class="border-t border-neutral-200 py-3 text-center text-xs text-neutral-400 dark:border-neutral-800"
	>
		HERMES · pre-alpha
	</footer>
</div>
