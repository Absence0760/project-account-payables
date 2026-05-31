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
	};

	let { columns, header, body, empty = 'No items.', isEmpty = false, colspan }: Props = $props();

	const emptySpan = $derived(colspan ?? columns?.length ?? 1);
</script>

<div class="grid-container">
	<table>
		<thead>
			{#if header}
				{@render header()}
			{:else if columns}
				<tr>
					{#each columns as col}
						<th class={col.class ?? null}>{col.label ?? ''}</th>
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
