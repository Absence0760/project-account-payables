<script lang="ts">
	import type { SubscriptionStatus } from '$lib/types/billing';

	let { status }: { status: SubscriptionStatus } = $props();

	const LABELS: Record<SubscriptionStatus, string> = {
		trialing: 'Trialing',
		active: 'Active',
		past_due: 'Past Due',
		canceled: 'Canceled'
	};
</script>

<span class="sub-badge {status}">{LABELS[status]}</span>

<style>
	.sub-badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}

	/* Foreground tones chosen against the ~15%-tint-over-bg effective
	   background to clear WCAG 1.4.3 (≥4.5:1 for this small-bold text),
	   matching StatusBadge's calibrated palette. */
	.active {
		background: rgba(31, 168, 106, 0.15);
		color: #26b977;
	}

	.trialing {
		background: rgba(99, 140, 255, 0.15);
		color: #7d9bff;
	}

	.past_due {
		background: rgba(240, 70, 70, 0.15);
		color: #f06464;
	}

	.canceled {
		background: rgba(160, 160, 160, 0.15);
		color: #a8a8a8;
	}
</style>
