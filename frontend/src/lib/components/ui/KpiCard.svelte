<script lang="ts">
	type Props = {
		value: string | number;
		label: string;
		/** Tints the card + value: green for positive, red for attention. */
		highlight?: 'green' | 'red' | null;
	};

	let { value, label, highlight = null }: Props = $props();
</script>

<div class="kpi" class:highlight-green={highlight === 'green'} class:highlight-red={highlight === 'red'}>
	<span class="kpi-value">{value}</span>
	<span class="kpi-label">{label}</span>
</div>
