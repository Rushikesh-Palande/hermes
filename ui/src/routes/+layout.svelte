<script lang="ts">
	/*
	 * Root layout: header + nav + content area.
	 *
	 * Auth guard goes here once Phase 3.5 lands the JWT flow. For now
	 * dev-mode bypass on the API means every page works without login.
	 */
	import '../app.css';
	import { page } from '$app/state';

	interface Props {
		children?: import('svelte').Snippet;
	}
	let { children }: Props = $props();

	const navItems: Array<{ href: string; label: string }> = [
		{ href: '/', label: 'Overview' },
		{ href: '/devices', label: 'Devices' },
		{ href: '/events', label: 'Events' },
		{ href: '/config', label: 'Config' }
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname.startsWith(href);
	}
</script>

<div
	class="flex min-h-screen flex-col bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100"
>
	<header class="border-b border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
		<div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
			<a href="/" class="text-lg font-semibold tracking-tight">HERMES</a>
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
		</div>
	</header>

	<main class="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
		{@render children?.()}
	</main>

	<footer
		class="border-t border-neutral-200 py-3 text-center text-xs text-neutral-400 dark:border-neutral-800"
	>
		HERMES · pre-alpha
	</footer>
</div>
