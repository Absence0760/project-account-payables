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
		/* On the modal's lighter --surface the 15% tint composites to #232b44,
		   where #638cff is only 4.48:1 (fails WCAG 1.4.3). #7d9bff lifts it to
		   5.18:1 on surface / 5.72:1 on --bg. */
		color: #7d9bff;
	}

	.pending {
		background: rgba(255, 180, 50, 0.15);
		color: #d4940a;
	}

	.ready_for_review {
		background: rgba(50, 200, 130, 0.15);
		/* #1fa86a is only 4.33:1 on the green 15%-tint-over-surface (#1c3431);
		   #26b977 lifts it to 5.37:1 (WCAG 1.4.3). */
		color: #26b977;
	}

	/* WCAG 1.4.3 — these badges are small but bold uppercase, treated as normal
	   text (need ≥4.5:1). Foreground brightened against the ~15%-tint-over-bg
	   effective background: failed #e04040→#f06464 (3.86:1→5.21:1),
	   sent_to_erp #8c64f0→#a585f5 (3.99:1→5.59:1). The passing tones
	   (new 4.98, pending 5.46, ready_for_review 4.84, done 5.08) are unchanged. */
	.failed {
		background: rgba(240, 70, 70, 0.15);
		color: #f06464;
	}

	.sent_to_erp {
		background: rgba(140, 100, 240, 0.15);
		color: #a585f5;
	}

	.done {
		background: rgba(31, 168, 106, 0.15);
		color: #26b977;
	}
</style>
