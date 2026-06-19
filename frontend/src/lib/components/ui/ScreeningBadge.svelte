<script lang="ts">
	// Small pill for a vendor's sanctions-screening status and/or risk level.
	// Pass `screening` (clear=green, review=amber, match=red, unscreened=grey)
	// and/or `risk` (low=grey, medium=amber, high/critical=red). `blocked`
	// forces the screening pill red with a "Blocked" label. Shared by the
	// vendor list cell and the detail modal — don't hand-roll these pills.
	import {
		SCREENING_STATUS_LABELS,
		RISK_LEVEL_LABELS,
		type ScreeningStatus,
		type RiskLevel
	} from '$lib/types/vendor';

	let {
		screening,
		risk,
		blocked = false
	}: {
		screening?: ScreeningStatus;
		risk?: RiskLevel;
		blocked?: boolean;
	} = $props();

	// Map status/level to a tone class shared across both pills.
	function screeningTone(s: ScreeningStatus, isBlocked: boolean): string {
		if (isBlocked) return 'red';
		if (s === 'clear') return 'green';
		if (s === 'review') return 'amber';
		if (s === 'match') return 'red';
		return 'grey';
	}

	function riskTone(r: RiskLevel): string {
		if (r === 'low') return 'grey';
		if (r === 'medium') return 'amber';
		if (r === 'high' || r === 'critical') return 'red';
		return 'grey';
	}
</script>

{#if screening}
	<span class="screen-badge {screeningTone(screening, blocked)}">
		{blocked ? 'Blocked' : SCREENING_STATUS_LABELS[screening]}
	</span>
{/if}
{#if risk && risk !== 'unknown'}
	<span class="screen-badge risk {riskTone(risk)}" title="Risk: {RISK_LEVEL_LABELS[risk]}">
		{RISK_LEVEL_LABELS[risk]} risk
	</span>
{/if}

<style>
	.screen-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 0.72rem;
		font-weight: 500;
		white-space: nowrap;
	}
	.screen-badge.risk {
		font-size: 0.68rem;
	}
	.screen-badge.green {
		background: rgba(31, 168, 106, 0.12);
		color: #1fa86a;
	}
	.screen-badge.amber {
		background: rgba(212, 148, 10, 0.12);
		color: #d4940a;
	}
	/* WCAG 1.4.3 — #e04040 on the 12%-tint-over-bg was 4.05:1 (fail);
	   #f06464 lifts it to 5.46:1. green (5.34) + amber (6.12) already pass. */
	.screen-badge.red {
		background: rgba(224, 64, 64, 0.12);
		color: #f06464;
	}
	.screen-badge.grey {
		background: var(--bg);
		color: var(--text-muted);
	}
</style>
