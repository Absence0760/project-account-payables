<script lang="ts">
	import { invoiceStore } from '$lib/stores/invoices.svelte';

	let total = $derived(invoiceStore.all.length);
	let newCount = $derived(invoiceStore.all.filter((i) => i.status === 'new').length);
	let pendingCount = $derived(invoiceStore.all.filter((i) => i.status === 'pending').length);
	let reviewCount = $derived(invoiceStore.all.filter((i) => i.status === 'ready_for_review').length);
	let failedCount = $derived(invoiceStore.all.filter((i) => i.status === 'failed').length);
	let totalAmount = $derived(
		invoiceStore.all.reduce((sum, inv) => sum + inv.amount, 0)
	);

	function fmt(n: number): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
	}
</script>

<div class="dashboard">
	<h1>Dashboard</h1>

	<div class="kpi-grid">
		<div class="kpi-card">
			<span class="kpi-label">Total Invoices</span>
			<span class="kpi-value">{total}</span>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Total Amount</span>
			<span class="kpi-value">{fmt(totalAmount)}</span>
		</div>
		<div class="kpi-card accent-blue">
			<span class="kpi-label">New</span>
			<span class="kpi-value">{newCount}</span>
		</div>
		<div class="kpi-card accent-yellow">
			<span class="kpi-label">Pending</span>
			<span class="kpi-value">{pendingCount}</span>
		</div>
		<div class="kpi-card accent-green">
			<span class="kpi-label">Ready for Review</span>
			<span class="kpi-value">{reviewCount}</span>
		</div>
		<div class="kpi-card accent-red">
			<span class="kpi-label">Failed</span>
			<span class="kpi-value">{failedCount}</span>
		</div>
	</div>
</div>

<style>
	.dashboard {
		padding: 24px 20px;
		max-width: 1280px;
	}

	h1 {
		margin: 0 0 24px;
		font-size: 1.4rem;
		font-weight: 700;
		color: var(--text);
	}

	.kpi-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 14px;
	}

	.kpi-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 20px;
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.kpi-label {
		font-size: 0.78rem;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
	}

	.kpi-value {
		font-size: 1.6rem;
		font-weight: 700;
		color: var(--text);
	}

	.accent-blue { border-left: 3px solid #638cff; }
	.accent-yellow { border-left: 3px solid #d4940a; }
	.accent-green { border-left: 3px solid #1fa86a; }
	.accent-red { border-left: 3px solid #e04040; }
</style>
