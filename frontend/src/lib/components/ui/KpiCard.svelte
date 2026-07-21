<script lang="ts">
	type Props = {
		value: string | number;
		label: string;
		/** Tints the card + value: green for positive, red for attention. */
		highlight?: 'green' | 'red' | null;
		/**
		 * Optional secondary line under the label — a qualifier on the headline
		 * figure (e.g. money the headline deliberately excludes). Rendered
		 * smaller + muted so the primary value stays the thing you read first;
		 * omit it entirely when there is nothing to qualify.
		 */
		sub?: string | null;
	};

	let { value, label, highlight = null, sub = null }: Props = $props();
</script>

<div class="kpi" class:highlight-green={highlight === 'green'} class:highlight-red={highlight === 'red'}>
	<span class="kpi-value">{value}</span>
	<span class="kpi-label">{label}</span>
	{#if sub}
		<span class="kpi-sub">{sub}</span>
	{/if}
</div>
