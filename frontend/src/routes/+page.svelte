<script lang="ts">
	import { api } from '$lib/api';

	interface StatusCount {
		status: string;
		count: number;
	}

	interface DashboardData {
		total_invoices: number;
		total_amount: number;
		status_counts: StatusCount[];
	}

	let data = $state<DashboardData | null>(null);
	let loading = $state(true);

	$effect(() => {
		api.get<DashboardData>('/api/dashboard').then((res) => {
			data = res;
			loading = false;
		}).catch(() => {
			loading = false;
		});
	});

	function statusLabel(s: string): string {
		const labels: Record<string, string> = {
			new: 'New',
			pending: 'Pending',
			ready_for_review: 'Ready for Review',
			failed: 'Failed',
			sent_to_erp: 'Sent to ERP',
		};
		return labels[s] || s;
	}

	function statusAccent(s: string): string {
		const accents: Record<string, string> = {
			new: 'accent-blue',
			pending: 'accent-yellow',
			ready_for_review: 'accent-green',
			failed: 'accent-red',
		};
		return accents[s] || '';
	}

	function fmt(n: number): string {
		return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
	}
</script>

<div class="dashboard">
	<h1>Dashboard</h1>

	{#if loading}
		<p class="loading">Loading...</p>
	{:else if data}
		<div class="kpi-grid">
			<div class="kpi-card">
				<span class="kpi-label">Total Invoices</span>
				<span class="kpi-value">{data.total_invoices}</span>
			</div>
			<div class="kpi-card">
				<span class="kpi-label">Total Amount</span>
				<span class="kpi-value">{fmt(data.total_amount)}</span>
			</div>
			{#each data.status_counts as sc}
				<div class="kpi-card {statusAccent(sc.status)}">
					<span class="kpi-label">{statusLabel(sc.status)}</span>
					<span class="kpi-value">{sc.count}</span>
				</div>
			{/each}
		</div>
	{/if}
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

	.loading {
		color: var(--text-muted);
		font-size: 0.9rem;
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
