<script lang="ts">
	import ToolResultView from '$lib/components/assistant/ToolResultView.svelte';
	import CashPositionChart from '$lib/components/cash-flow/CashPositionChart.svelte';
	import type { ToolInvocation } from '$lib/types/assistant';
	import type { CashPositionResult } from '$lib/types/cashFlow';

	let { invocation }: { invocation: ToolInvocation } = $props();

	// The cash-position tool gets the dedicated running-balance chart; every
	// other tool (the five base assistant tools + the other cash-flow tools)
	// reuses the assistant's `ToolResultView` dispatcher unchanged.
	let position = $derived(
		invocation.tool === 'get_cash_position' && invocation.result && !invocation.error
			? (invocation.result as unknown as CashPositionResult)
			: null
	);
</script>

{#if position}
	<CashPositionChart result={position} />
{:else}
	<ToolResultView {invocation} />
{/if}
