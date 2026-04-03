<script lang="ts">
	import type { InvoiceStatus } from '$lib/types/invoice';
	import { STATUS_LABELS } from '$lib/types/invoice';

	let { status }: { status: InvoiceStatus } = $props();
</script>

<span class="badge {status}">{STATUS_LABELS[status]}</span>

<style>
	.badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		white-space: nowrap;
	}

	.new {
		background: rgba(99, 140, 255, 0.15);
		color: #638cff;
	}

	.pending {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.ready_for_review {
		background: rgba(50, 200, 130, 0.15);
		color: #1fa86a;
	}

	.failed {
		background: rgba(240, 70, 70, 0.15);
		color: #e04040;
	}

	.sent_to_erp {
		background: rgba(140, 100, 240, 0.15);
		color: #8c64f0;
	}
</style>
