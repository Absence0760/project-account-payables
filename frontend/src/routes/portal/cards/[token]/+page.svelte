<script lang="ts">
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import { formatMoney } from '$lib/utils/money';

	interface RevealResponse {
		last_four: string | null;
		amount_limit: number;
		currency: string;
		expires_at: string | null;
		pan: string | null;
		cvv: string | null;
		warning?: string;
	}

	let loading = $state(true);
	let error = $state<{ status: number; message: string } | null>(null);
	let card = $state<RevealResponse | null>(null);
	let copied = $state<'pan' | 'cvv' | null>(null);

	const token = $derived($page.params.token ?? '');

	$effect(() => {
		if (!token) return;
		load();
	});

	async function load() {
		loading = true;
		error = null;
		try {
			card = await api.get<RevealResponse>(`/api/portal/cards/${token}`);
		} catch (err) {
			const e = err as { status?: number; message?: string; detail?: string } | null;
			error = {
				status: e?.status ?? 0,
				message: e?.detail ?? e?.message ?? 'Could not load card details'
			};
		} finally {
			loading = false;
		}
	}

	async function copy(field: 'pan' | 'cvv', value: string | null) {
		if (!value) return;
		await navigator.clipboard.writeText(value);
		copied = field;
		setTimeout(() => {
			if (copied === field) copied = null;
		}, 1500);
	}

	function formatExpires(iso: string | null): string {
		if (!iso) return '—';
		const d = new Date(iso);
		const mm = String(d.getMonth() + 1).padStart(2, '0');
		const yy = String(d.getFullYear()).slice(-2);
		return `${mm}/${yy}`;
	}

	function formatAmount(amount: number, currency: string): string {
		return formatMoney(amount, { currency });
	}
</script>

<div class="reveal-page">
	<div class="reveal-card">
		<div class="brand">Virtual Card</div>

		{#if loading}
			<p class="state">Loading…</p>
		{:else if error}
			<div class="state error">
				<h1>
					{#if error.status === 410}
						This link is no longer valid
					{:else if error.status === 404}
						Link not recognised
					{:else}
						Something went wrong
					{/if}
				</h1>
				<p>{error.message}</p>
				<p class="hint">
					Single-use links expire 7 days after issuance and can only be opened once. If
					you've already saved the card, you don't need to revisit this page. Otherwise,
					reply to the email and we'll re-issue.
				</p>
			</div>
		{:else if card}
			<p class="lede">
				{formatAmount(card.amount_limit, card.currency)} authorised. Use this card on the
				vendor portal you'd normally bill us through.
			</p>

			{#if card.warning}
				<p class="warning">{card.warning}</p>
			{/if}

			<dl class="fields">
				<div class="field">
					<dt>Card number</dt>
					<dd>
						<span class="value mono">
							{card.pan ?? `•••• •••• •••• ${card.last_four ?? '••••'}`}
						</span>
						{#if card.pan}
							<button class="copy-btn" onclick={() => copy('pan', card?.pan ?? null)}>
								{copied === 'pan' ? 'Copied!' : 'Copy'}
							</button>
						{/if}
					</dd>
				</div>

				<div class="field">
					<dt>CVV</dt>
					<dd>
						<span class="value mono">{card.cvv ?? '•••'}</span>
						{#if card.cvv}
							<button class="copy-btn" onclick={() => copy('cvv', card?.cvv ?? null)}>
								{copied === 'cvv' ? 'Copied!' : 'Copy'}
							</button>
						{/if}
					</dd>
				</div>

				<div class="field">
					<dt>Expires</dt>
					<dd><span class="value mono">{formatExpires(card.expires_at)}</span></dd>
				</div>
			</dl>

			<p class="hint">
				This page won't open again — the link is single-use. If you accidentally close it
				before saving the card, reply to the email and we'll re-issue.
			</p>
		{/if}
	</div>
</div>

<style>
	.reveal-page {
		min-height: 100vh;
		display: grid;
		place-items: center;
		padding: 24px;
		background: var(--bg);
	}

	.reveal-card {
		width: min(520px, 100%);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 12px;
		padding: 28px 32px;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
	}

	.brand {
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-muted);
		margin-bottom: 6px;
	}

	.state {
		margin-top: 16px;
		font-size: 0.95rem;
		color: var(--text);
	}

	.state.error h1 {
		font-size: 1.2rem;
		margin: 0 0 8px;
		color: #e04040;
	}

	.lede {
		margin: 8px 0 22px;
		color: var(--text);
		font-size: 0.95rem;
	}

	.warning {
		padding: 10px 12px;
		background: rgba(212, 148, 10, 0.1);
		border: 1px solid rgba(212, 148, 10, 0.3);
		border-radius: 6px;
		color: #d4940a;
		font-size: 0.85rem;
		margin: 0 0 16px;
	}

	.fields {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin: 0 0 22px;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	dt {
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin: 0;
	}

	dd {
		margin: 0;
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.value {
		font-size: 1.1rem;
		font-weight: 600;
		color: var(--text);
		letter-spacing: 0.06em;
	}

	.mono {
		font-family: 'SF Mono', 'Cascadia Code', monospace;
	}

	.copy-btn {
		padding: 4px 10px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text-muted);
		border-radius: 4px;
		font-size: 0.78rem;
		cursor: pointer;
		font-family: inherit;
	}

	.copy-btn:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.hint {
		font-size: 0.78rem;
		color: var(--text-muted);
		margin: 0;
		line-height: 1.5;
	}
</style>
