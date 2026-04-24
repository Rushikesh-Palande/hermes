<script lang="ts">
    /*
     * Root layout for every page.
     *
     * Loads the global stylesheet exactly once and provides the slot
     * into which the current route renders. Auth guard, nav sidebar,
     * and user menu land here once those features are implemented
     * (Phase 5).
     */
    import '../app.css';

    interface Props {
        children?: import('svelte').Snippet;
    }
    let { children }: Props = $props();
</script>

<main class="min-h-full">
    {@render children?.()}
</main>
