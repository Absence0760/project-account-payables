<script lang="ts">
	import { portalCompany } from '$lib/stores/portalCompany.svelte';
	import { onMount } from 'svelte';

	let email = $state('');
	let phone = $state('');
	let address = $state('');
	let contactSaving = $state(false);
	let contactMsg = $state('');
	let contactErr = $state('');

	// Bank-detail change fields (staged, not applied live).
	let bankName = $state('');
	let accountNumber = $state('');
	let routingNumber = $state('');
	let bankSaving = $state(false);
	let bankErr = $state('');

	// Tax-ID change (staged, not applied live).
	let taxId = $state('');
	let taxSaving = $state(false);
	let taxErr = $state('');

	const info = $derived(portalCompany.info);
	const pending = $derived(portalCompany.info?.pending_change ?? null);

	async function load() {
		await portalCompany.fetchCompany();
		const i = portalCompany.info;
		if (i) {
			email = i.email ?? '';
			phone = i.phone ?? '';
			address = i.address ?? '';
		}
	}

	async function saveContact(e: Event) {
		e.preventDefault();
		contactSaving = true;
		contactMsg = '';
		contactErr = '';
		try {
			await portalCompany.updateContact({ email, phone, address });
			contactMsg = 'Saved.';
		} catch (err) {
			contactErr = err instanceof Error ? err.message : 'Save failed';
		} finally {
			contactSaving = false;
		}
	}

	async function submitBankChange(e: Event) {
		e.preventDefault();
		bankSaving = true;
		bankErr = '';
		try {
			await portalCompany.requestBankChange({
				bank_name: bankName,
				account_number: accountNumber,
				routing_number: routingNumber,
			});
			accountNumber = '';
			routingNumber = '';
			bankName = '';
		} catch (err) {
			bankErr = err instanceof Error ? err.message : 'Request failed';
		} finally {
			bankSaving = false;
		}
	}

	async function submitTaxChange(e: Event) {
		e.preventDefault();
		taxSaving = true;
		taxErr = '';
		try {
			await portalCompany.requestTaxIdChange(taxId);
			taxId = '';
		} catch (err) {
			taxErr = err instanceof Error ? err.message : 'Request failed';
		} finally {
			taxSaving = false;
		}
	}

	onMount(load);
</script>

<div class="page">
	<header>
		<h1>Company Info</h1>
		{#if info}<p class="sub">{info.name}</p>{/if}
	</header>

	{#if pending}
		<div class="banner">
			A change to your <strong>{pending.change_type.replace('_', ' ')}</strong> is pending AP
			approval. It takes effect once your customer approves it.
		</div>
	{/if}

	<!-- Live-apply contact fields -->
	<section class="card">
		<h2>Contact details</h2>
		<p class="note">These save immediately.</p>
		{#if contactErr}<div class="error">{contactErr}</div>{/if}
		{#if contactMsg}<div class="message">{contactMsg}</div>{/if}
		<form onsubmit={saveContact}>
			<label>
				Email
				<input type="email" bind:value={email} />
			</label>
			<label>
				Phone
				<input type="text" bind:value={phone} />
			</label>
			<label>
				Address
				<input type="text" bind:value={address} />
			</label>
			<button type="submit" class="btn-primary" disabled={contactSaving}>
				{contactSaving ? 'Saving…' : 'Save contact details'}
			</button>
		</form>
	</section>

	<!-- Staged: bank details -->
	<section class="card">
		<h2>Bank details</h2>
		<p class="note">
			Bank-detail changes require your customer's AP team to approve them before they take
			effect — a fraud-prevention step. Your current details are not shown here.
			{#if info?.has_bank_details}<span class="tag">On file</span>{/if}
		</p>
		{#if bankErr}<div class="error">{bankErr}</div>{/if}
		<form onsubmit={submitBankChange}>
			<label>
				Bank name
				<input type="text" bind:value={bankName} />
			</label>
			<label>
				Account number
				<input type="text" bind:value={accountNumber} autocomplete="off" />
			</label>
			<label>
				Routing number
				<input type="text" bind:value={routingNumber} autocomplete="off" />
			</label>
			<button
				type="submit"
				class="btn-primary"
				disabled={bankSaving || !!pending || !accountNumber}
			>
				{bankSaving ? 'Submitting…' : 'Request bank-detail change'}
			</button>
		</form>
	</section>

	<!-- Staged: tax ID -->
	<section class="card">
		<h2>Tax ID</h2>
		<p class="note">
			Tax-ID changes also require AP approval (they re-key 1099 reporting).
			{#if info?.tax_id_last4}<span class="tag">Ending {info.tax_id_last4}</span>{/if}
		</p>
		{#if taxErr}<div class="error">{taxErr}</div>{/if}
		<form onsubmit={submitTaxChange}>
			<label>
				New tax ID
				<input type="text" bind:value={taxId} autocomplete="off" />
			</label>
			<button type="submit" class="btn-primary" disabled={taxSaving || !!pending || !taxId}>
				{taxSaving ? 'Submitting…' : 'Request tax-ID change'}
			</button>
		</form>
	</section>
</div>

<style>
	.page {
		max-width: 700px;
		margin: 0 auto;
	}
	header {
		margin-bottom: 20px;
	}
	h1 {
		margin: 0;
		font-size: 1.25rem;
	}
	.sub {
		margin: 4px 0 0;
		color: var(--text-muted);
		font-size: 0.9rem;
	}
	.banner {
		background: rgba(214, 158, 46, 0.12);
		border: 1px solid rgba(214, 158, 46, 0.4);
		color: #9a6a00;
		padding: 10px 14px;
		border-radius: 4px;
		margin-bottom: 16px;
		font-size: 0.9rem;
	}
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 18px 20px;
		margin-bottom: 18px;
	}
	h2 {
		margin: 0 0 4px;
		font-size: 1rem;
	}
	.note {
		margin: 0 0 14px;
		color: var(--text-muted);
		font-size: 0.82rem;
	}
	.tag {
		display: inline-block;
		margin-left: 6px;
		padding: 1px 8px;
		border-radius: 999px;
		background: var(--bg);
		border: 1px solid var(--border);
		font-size: 0.72rem;
	}
	form {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	input {
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--bg);
		color: var(--text);
		font-size: 0.9rem;
	}
	.btn-primary {
		align-self: flex-start;
		padding: 8px 16px;
		border: none;
		border-radius: 4px;
		background: var(--accent);
		color: #fff;
		font-size: 0.85rem;
		cursor: pointer;
	}
	.btn-primary:disabled {
		opacity: 0.55;
		cursor: default;
	}
	.error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 8px 12px;
		border-radius: 4px;
		margin-bottom: 10px;
		font-size: 0.85rem;
	}
	.message {
		background: rgba(40, 160, 80, 0.12);
		border: 1px solid rgba(40, 160, 80, 0.35);
		color: #1f7a44;
		padding: 8px 12px;
		border-radius: 4px;
		margin-bottom: 10px;
		font-size: 0.85rem;
	}
</style>
