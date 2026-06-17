<script lang="ts">
	import type { DiscountTier } from '$lib/types/discounts';

	type Props = {
		/** Sliding-scale tiers (e.g. 2% by day 10, 1% by day 20). */
		tiers: DiscountTier[];
		/** Optionally highlight the accepted tier (matched on `days`). */
		acceptedDays?: number | null;
	};

	let { tiers, acceptedDays = null }: Props = $props();

	// Show soonest-deadline (usually richest) tiers first.
	let sorted = $derived([...tiers].sort((a, b) => a.days - b.days));
</script>

{#if sorted.length === 0}
	<span class="tier-none">—</span>
{:else}
	<div class="tier-bar">
		{#each sorted as tier (tier.days)}
			<span class="tier-chip" class:accepted={acceptedDays === tier.days} title="{tier.percent}% if paid within {tier.days} days">
				{tier.percent}% / {tier.days}d
			</span>
		{/each}
	</div>
{/if}

<style>
	.tier-bar {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 4px;
	}
	.tier-chip {
		display: inline-flex;
		align-items: center;
		padding: 2px 8px;
		border-radius: 10px;
		background: rgba(31, 168, 106, 0.1);
		color: #1fa86a;
		font-size: 0.74rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.tier-chip.accepted {
		background: #1fa86a;
		color: #fff;
	}
	.tier-none {
		color: var(--text-muted);
	}
</style>
