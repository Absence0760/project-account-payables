<script lang="ts">
	import type { Snippet } from 'svelte';

	type Column = {
		/** Header text. Omit for the actions / checkbox column. */
		label?: string;
		/** Optional class on the `<th>` (e.g. `right`, `actions-col`, `checkbox-col`). */
		class?: string;
	};

	type Props = {
		/** Simple header row. Provide this OR the `header` snippet, not both. */
		columns?: Column[];
		/** Custom `<tr>…</tr>` header row for select-all checkboxes / sortable headers. */
		header?: Snippet;
		/** Renders the `<tr>` rows inside `<tbody>`. */
		body: Snippet;
		/** Shown as a single centred row when `isEmpty` is true. */
		empty?: string;
		isEmpty?: boolean;
		/** colspan for the empty row; defaults to the column count. */
		colspan?: number;
		/** `table-layout: fixed` — pair with explicit `<th>` widths in the page. */
		fixed?: boolean;
		/** Sticky header row that pins to the top of the viewport on scroll. */
		stickyHeader?: boolean;
	};

	let {
		columns,
		header,
		body,
		empty = 'No items.',
		isEmpty = false,
		colspan,
		fixed = false,
		stickyHeader = false
	}: Props = $props();

	const emptySpan = $derived(colspan ?? columns?.length ?? 1);
</script>

<div class="grid-container">
	<table class:fixed class:sticky-header={stickyHeader}>
		<thead>
			{#if header}
				{@render header()}
			{:else if columns}
				<tr>
					{#each columns as col}
						<!-- WCAG 1.3.1: scope ties each header to its column for AT. -->
						<th scope="col" class={col.class ?? null}>{col.label ?? ''}</th>
					{/each}
				</tr>
			{/if}
		</thead>
		<tbody>
			{#if isEmpty}
				<tr><td class="empty" colspan={emptySpan}>{empty}</td></tr>
			{:else}
				{@render body()}
			{/if}
		</tbody>
	</table>
</div>

<style>
	/* Opt-in layout refinements; the base table styling is global (app.css). */
	table.fixed {
		table-layout: fixed;
	}
	table.sticky-header thead {
		position: sticky;
		top: 0;
		z-index: 1;
	}
</style>
