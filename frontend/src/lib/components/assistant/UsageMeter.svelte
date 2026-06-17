<script lang="ts">
	import type { UsageResponse } from '$lib/types/assistant';

	let { usage }: { usage: UsageResponse | null } = $props();

	// Budget 0 = unlimited — show the running total but no progress bar.
	let unlimited = $derived(!usage || usage.budget <= 0);
	let pct = $derived(
		usage && usage.budget > 0 ? Math.min(100, (usage.total_tokens / usage.budget) * 100) : 0
	);
	let near = $derived(pct >= 80 && pct < 100);
	let over = $derived(pct >= 100);

	const nf = new Intl.NumberFormat();
	function fmt(n: number): string {
		return nf.format(n);
	}
</script>

{#if usage}
	<div class="usage-meter" data-testid="usage-meter" class:over class:near>
		<div class="usage-head">
			<span class="usage-label">AI usage ({usage.period})</span>
			<span class="usage-figs">
				{#if unlimited}
					{fmt(usage.total_tokens)} tokens
				{:else}
					{fmt(usage.total_tokens)} / {fmt(usage.budget)} tokens
				{/if}
			</span>
		</div>
		{#if !unlimited}
			<div class="usage-track" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin="0" aria-valuemax="100">
				<div class="usage-fill" style="width:{pct}%"></div>
			</div>
			{#if over}
				<span class="usage-note">Monthly AI budget reached.</span>
			{:else if near}
				<span class="usage-note">{fmt(usage.remaining)} tokens remaining.</span>
			{/if}
		{:else}
			<span class="usage-note muted">Unlimited budget</span>
		{/if}
	</div>
{/if}

<style>
	.usage-meter {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.78rem;
	}
	.usage-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 8px;
	}
	.usage-label {
		font-weight: 600;
		color: var(--text-muted);
	}
	.usage-figs {
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
	}
	.usage-track {
		height: 6px;
		background: rgba(128, 128, 128, 0.15);
		border-radius: 3px;
		overflow: hidden;
	}
	.usage-fill {
		height: 100%;
		background: var(--accent);
		transition: width 0.3s ease;
	}
	.usage-meter.near .usage-fill {
		background: #d4940a;
	}
	.usage-meter.over .usage-fill {
		background: #e04040;
	}
	.usage-note {
		font-size: 0.72rem;
		color: #d4940a;
	}
	.usage-meter.over .usage-note {
		color: #e04040;
	}
	.usage-note.muted {
		color: var(--text-muted);
	}
</style>
