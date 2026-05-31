<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		/** Rendered as `<h1>` — the e2e suite selects pages by this heading. */
		title: string;
		/** Right-aligned toolbar buttons (e.g. `+ Upload`, SearchBox). */
		actions?: Snippet;
		/** Page body — filters, table, pagination, etc. */
		children: Snippet;
	};

	let { title, actions, children }: Props = $props();
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>{title}</h1>
		{#if actions}
			<div class="toolbar-actions">{@render actions()}</div>
		{/if}
	</header>
	{@render children()}
</div>
