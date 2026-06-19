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

	// Tax-form (W-9 / W-8) upload — applies live (it's a document, not a routing target).
	let taxFormType = $state('w9');
	let taxFormFile = $state<File | null>(null);
	let taxFormSaving = $state(false);
	let taxFormErr = $state('');
	let taxFormMsg = $state('');
	let downloadingForm = $state(false);

	const info = $derived(portalCompany.info);
	const pending = $derived(portalCompany.info?.pending_change ?? null);
	const taxForm = $derived(portalCompany.taxForm);

	const FORM_TYPE_LABEL: Record<string, string> = {
		w9: 'W-9 (US)',
		w8: 'W-8 (foreign)'
	};

	async function load() {
		await portalCompany.fetchCompany();
		const i = portalCompany.info;
		if (i) {
			email = i.email ?? '';
			phone = i.phone ?? '';
			address = i.address ?? '';
		}
		try {
			await portalCompany.fetchTaxForm();
			if (portalCompany.taxForm) taxFormType = portalCompany.taxForm.suggested_form_type;
		} catch {
			// Non-fatal — the rest of the page still renders.
		}
	}

	function onTaxFormFile(e: Event) {
		const target = e.target as HTMLInputElement;
		taxFormFile = target.files && target.files.length ? target.files[0] : null;
	}

	async function submitTaxForm(e: Event) {
		e.preventDefault();
		if (!taxFormFile) return;
		taxFormSaving = true;
		taxFormErr = '';
		taxFormMsg = '';
		try {
			await portalCompany.uploadTaxForm(taxFormFile, taxFormType);
			taxFormFile = null;
			taxFormMsg = 'Tax form uploaded.';
		} catch (err) {
			taxFormErr = err instanceof Error ? err.message : 'Upload failed';
		} finally {
			taxFormSaving = false;
		}
	}

	async function downloadTaxForm() {
		downloadingForm = true;
		taxFormErr = '';
		try {
			const blob = await portalCompany.downloadTaxForm();
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `tax-form-${taxForm?.form_type ?? 'document'}`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
		} catch (err) {
			taxFormErr = err instanceof Error ? err.message : 'Download failed';
		} finally {
			downloadingForm = false;
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

	<!-- Tax forms: W-9 (US) / W-8 (foreign) — uploaded live -->
	<section class="card">
		<h2>Tax forms (W-9 / W-8)</h2>
		<p class="note">
			Upload your signed <strong>W-9</strong> (US suppliers) or
			<strong>W-8</strong> (foreign suppliers) so your customer can meet their 1099 / withholding
			obligations. PDF, PNG, JPEG, or TIFF.
			{#if taxForm?.on_file}
				<span class="tag">
					{FORM_TYPE_LABEL[taxForm.form_type ?? ''] ?? 'On file'}
					{#if taxForm.received_date}· received {taxForm.received_date}{/if}
				</span>
			{/if}
		</p>
		{#if taxFormErr}<div class="error">{taxFormErr}</div>{/if}
		{#if taxFormMsg}<div class="message">{taxFormMsg}</div>{/if}

		{#if taxForm?.on_file}
			<p class="note">
				A form is on file. You can replace it by uploading a new one below.
				<button
					type="button"
					class="btn-link"
					onclick={downloadTaxForm}
					disabled={downloadingForm}
				>
					{downloadingForm ? 'Preparing…' : 'Download current form'}
				</button>
			</p>
		{/if}

		<form onsubmit={submitTaxForm}>
			<label>
				Form type
				<select bind:value={taxFormType}>
					<option value="w9">W-9 (US)</option>
					<option value="w8">W-8 (foreign)</option>
				</select>
			</label>
			<label>
				Signed form
				<input
					type="file"
					accept="application/pdf,image/png,image/jpeg,image/tiff"
					onchange={onTaxFormFile}
				/>
			</label>
			<button type="submit" class="btn-primary" disabled={taxFormSaving || !taxFormFile}>
				{taxFormSaving ? 'Uploading…' : taxForm?.on_file ? 'Replace tax form' : 'Upload tax form'}
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
	.btn-link {
		background: none;
		border: none;
		color: var(--accent);
		cursor: pointer;
		padding: 0;
		font-size: inherit;
		text-decoration: underline;
	}
	.btn-link:disabled {
		opacity: 0.55;
		cursor: default;
	}
	select {
		padding: 8px 10px;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--bg);
		color: var(--text);
		font-size: 0.9rem;
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
