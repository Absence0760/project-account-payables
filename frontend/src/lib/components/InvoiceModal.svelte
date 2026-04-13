<script lang="ts">
	import type { Invoice } from '$lib/types/invoice';
	import { INVOICE_STATUSES, STATUS_LABELS } from '$lib/types/invoice';
	import { invoiceStore } from '$lib/stores/invoices.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { adminStore } from '$lib/stores/admin.svelte';
	import { api } from '$lib/api';
	import { PUBLIC_API_URL } from '$env/static/public';
	import { toast } from '$lib/components/Toast.svelte';
	import type { ActiveSteps } from '$lib/stores/workflows.svelte';

	interface AuditEntry {
		id: string;
		actor_name: string | null;
		action: string;
		details: Record<string, unknown> | null;
		created_at: string;
	}

	const apiBase = PUBLIC_API_URL.replace(/\/+$/, '');
	const ACTION_LABELS: Record<string, string> = {
		'invoice.uploaded': 'Uploaded invoice',
		'invoice.submitted_for_review': 'Submitted for review',
		'invoice.approved': 'Approved',
		'invoice.rejected': 'Rejected',
		'invoice.resubmitted': 'Resubmitted for review',
		'invoice.assigned_for_review': 'Assigned for review',
		'invoice.erp_submitted': 'Sent to ERP',
		'invoice.extraction_dispatched': 'Extraction started',
		'invoice.extraction_reset': 'Extraction reset',
		'invoice.extraction_triggered': 'Extraction triggered manually',
		'invoice.extraction_completed': 'Extraction completed',
		'invoice.extraction_failed': 'Extraction failed',
		'invoice.completed': 'Marked complete',
	};

	let {
		invoice,
		onclose,
		activeSteps,
	}: {
		invoice: Invoice;
		onclose: () => void;
		activeSteps: ActiveSteps;
	} = $props();

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot, intentional */
	let vendor = $state(invoice.vendor);
	let invoice_number = $state(invoice.invoice_number);
	let amount = $state(invoice.amount);
	let due_date = $state(invoice.due_date);
	let status = $state(invoice.status);
	let po_number = $state(invoice.po_number);
	let description = $state(invoice.description);
	let vendor_address = $state(invoice.vendor_address ?? '');
	let vendor_tax_id = $state(invoice.vendor_tax_id ?? '');
	let ship_to_address = $state(invoice.ship_to_address ?? '');
	let tax_rate = $state(invoice.tax_rate);
	let payment_method = $state(invoice.payment_method ?? '');
	let gl_account = $state(invoice.gl_account ?? '');
	let cost_center = $state(invoice.cost_center ?? '');

	interface GLAccountOption { code: string; name: string; }
	let glAccounts = $state<GLAccountOption[]>([]);

	$effect(() => {
		loadGLAccounts();
	});

	async function loadGLAccounts() {
		try {
			glAccounts = await api.get<GLAccountOption[]>('/api/gl-accounts');
		} catch { /* non-critical */ }
	}
	let reference_number = $state(invoice.reference_number ?? '');
	/* eslint-enable svelte/state-referenced-locally */

	let fullscreen = $state(false);
	let showExportMenu = $state(false);
	let formPaneWidth = $state(480);
	let resizing = $state(false);

	function toggleFullscreen() {
		fullscreen = !fullscreen;
		formPaneWidth = fullscreen ? 480 : 600;
	}

	let resizeOverlay: HTMLDivElement | undefined;

	function startResize(e: MouseEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		e.stopPropagation();
		resizing = true;
		const startX = e.clientX;
		const startWidth = formPaneWidth;

		// Create a full-screen transparent overlay to capture all mouse events
		// This prevents the PDF iframe from stealing them
		const overlay = document.createElement('div');
		overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;cursor:col-resize;';
		document.body.appendChild(overlay);
		resizeOverlay = overlay;

		function onMove(ev: MouseEvent) {
			const delta = startX - ev.clientX;
			formPaneWidth = Math.max(300, Math.min(1200, startWidth + delta));
		}

		function onUp() {
			resizing = false;
			overlay.remove();
			resizeOverlay = undefined;
			window.removeEventListener('mousemove', onMove);
			window.removeEventListener('mouseup', onUp);
		}

		window.addEventListener('mousemove', onMove);
		window.addEventListener('mouseup', onUp);
	}

	let saving = $state(false);
	let submitting = $state(false);
	let deleting = $state(false);
	let confirmDelete = $state(false);
	let reviewing = $state(false);
	let showRejectForm = $state(false);
	let rejectReason = $state('');
	let selectedApproverId = $state('');

	// Whether to show the approver picker on submit
	let needsApproverSelect = $derived(
		status === 'new' &&
		activeSteps.approval &&
		activeSteps.approval_config?.approver_strategy === 'manual'
	);

	$effect(() => {
		if (needsApproverSelect) adminStore.fetchUsers();
	});

	let isClerkOnly = $derived(auth.isClerkOnly);
	let isDone = $derived(status === 'done' || status === 'sent_to_erp');
	let isExtracting = $derived(status === 'pending');
	let resettingExtraction = $state(false);

	async function handleResetExtraction() {
		resettingExtraction = true;
		try {
			await api.post(`/api/invoices/${invoice.id}/reset-extraction`, {});
			await invoiceStore.fetch();
			status = 'failed' as typeof status;
			extracting = false;
			extractionStatus = '';
			toast('Extraction reset — you can re-extract or edit manually', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reset failed', 'error');
		} finally {
			resettingExtraction = false;
		}
	}
	let isErpStatus = $derived(
		status === 'sending_to_erp' || status === 'sent_to_erp' || status === 'posted_in_erp' ||
		status === 'payment_scheduled' || status === 'paid'
	);
	let canRetryErp = $derived(status === 'failed' && !isClerkOnly && invoice.approved_by);
	let retryingErp = $state(false);
	let canExtract = $derived(
		(status === 'new' || status === 'failed') && invoice.file_url
	);
	let extracting = $state(false);
	let canDelete = $derived(
		!isClerkOnly && status !== 'done' && status !== 'sent_to_erp' && status !== 'sending_to_erp'
	);
	let isReadyForReview = $derived(status === 'ready_for_review');
	let canReview = $derived(isReadyForReview && !isClerkOnly && (
		!invoice.assigned_to_id || invoice.assigned_to_id === auth.user?.id
	));
	let canSubmitStatus = $derived(
		isClerkOnly
			? status === 'new'
			: status === 'new' || status === 'approved'
	);

	let submitLabel = $derived.by(() => {
		if (status === 'new' && activeSteps.approval) return 'Submit for Review';
		if (status === 'approved' && activeSteps.erp_export) return 'Send to ERP';
		return 'Mark Complete';
	});

	let missingFields = $derived.by(() => {
		const missing: string[] = [];
		if (!vendor.trim()) missing.push('Vendor');
		if (!invoice_number.trim()) missing.push('Invoice #');
		if (!amount || amount <= 0) missing.push('Amount');
		return missing;
	});

	let canSubmit = $derived(canSubmitStatus && missingFields.length === 0);

	async function save() {
		saving = true;
		try {
			await invoiceStore.update(invoice.id, {
				vendor,
				invoice_number,
				amount,
				due_date,
				status,
				po_number,
				description,
				vendor_address: vendor_address || null,
				vendor_tax_id: vendor_tax_id || null,
				ship_to_address: ship_to_address || null,
				tax_rate,
				payment_method: payment_method || null,
				reference_number: reference_number || null,
				gl_account: gl_account || null,
				cost_center: cost_center || null,
			});
			toast('Invoice saved', 'success');
			onclose();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			saving = false;
		}
	}

	async function submitDone() {
		submitting = true;
		try {
			// Save fields first, then mark complete
			await invoiceStore.update(invoice.id, {
				vendor,
				invoice_number,
				amount,
				due_date,
				po_number,
				description,
				vendor_address: vendor_address || null,
				vendor_tax_id: vendor_tax_id || null,
				ship_to_address: ship_to_address || null,
				tax_rate,
				payment_method: payment_method || null,
				reference_number: reference_number || null,
				gl_account: gl_account || null,
				cost_center: cost_center || null,
			});
			const result = await api.post<{ id: string; status: string }>(`/api/invoices/${invoice.id}/complete`, {});
			// If manually assigning an approver and invoice moved to ready_for_review
			if (selectedApproverId && result.status === 'ready_for_review') {
				await api.post(`/api/invoices/${invoice.id}/assign`, { user_id: selectedApproverId });
			}
			await invoiceStore.fetch();
			toast('Invoice submitted', 'success');
			onclose();
		} catch (err) {
			const msg = err instanceof Error ? err.message : 'Submit failed';
			// Don't toast field validation errors — the form highlights them already
			if (!msg.toLowerCase().includes('missing') && !msg.toLowerCase().includes('required field')) {
				toast(msg, 'error');
			}
		} finally {
			submitting = false;
		}
	}

	async function handleApprove() {
		reviewing = true;
		try {
			await api.post(`/api/invoices/${invoice.id}/approve`, {});
			await invoiceStore.fetch();
			toast('Invoice approved', 'success');
			onclose();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Approve failed', 'error');
		} finally {
			reviewing = false;
		}
	}

	async function handleReject() {
		if (!rejectReason.trim()) return;
		reviewing = true;
		try {
			await api.post(`/api/invoices/${invoice.id}/reject`, { reason: rejectReason.trim() });
			await invoiceStore.fetch();
			toast('Invoice rejected', 'success');
			onclose();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Reject failed', 'error');
		} finally {
			reviewing = false;
		}
	}

	async function handleRetryErp() {
		retryingErp = true;
		try {
			await api.post(`/api/invoices/${invoice.id}/retry-erp`, {});
			await invoiceStore.fetch();
			toast('ERP retry initiated', 'success');
			onclose();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Retry failed', 'error');
		} finally {
			retryingErp = false;
		}
	}

	let extractionStatus = $state('');

	async function handleExtract() {
		extracting = true;
		extractionStatus = 'Triggering extraction...';
		try {
			await api.post(`/api/invoices/${invoice.id}/extract`, {});
			extractionStatus = 'Extracting fields...';
			await pollForCompletion();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Extraction failed', 'error');
			extracting = false;
			extractionStatus = '';
		}
	}

	async function pollForCompletion() {
		const maxPolls = 30;
		const interval = 2000;

		for (let i = 0; i < maxPolls; i++) {
			await new Promise(r => setTimeout(r, interval));

			try {
				const updated = await api.get<import('$lib/types/invoice').Invoice>(`/api/invoices/${invoice.id}`);

				if (updated.status !== 'pending') {
					// Extraction finished — update the modal fields with extracted data
					vendor = updated.vendor || vendor;
					invoice_number = updated.invoice_number || invoice_number;
					amount = updated.amount || amount;
					due_date = updated.due_date || due_date;
					po_number = updated.po_number || po_number;
					description = updated.description || description;
					status = updated.status as typeof status;
					vendor_address = updated.vendor_address ?? vendor_address;
					vendor_tax_id = updated.vendor_tax_id ?? vendor_tax_id;
					ship_to_address = updated.ship_to_address ?? ship_to_address;
					tax_rate = updated.tax_rate ?? tax_rate;
					payment_method = updated.payment_method ?? payment_method;
					reference_number = updated.reference_number ?? reference_number;
					gl_account = updated.gl_account ?? gl_account;
					cost_center = updated.cost_center ?? cost_center;

					// Refresh the invoice list, audit log, and line items
					await invoiceStore.fetch();
					await loadLineItems();
					await loadExtractionConfidence();
					await loadAuditLog();

					if (updated.status === 'ready_for_review') {
						toast('Extraction complete — fields populated', 'success');
					} else if (updated.status === 'failed') {
						toast('Extraction failed — check the activity log', 'error');
					}

					extracting = false;
					extractionStatus = '';
					return;
				}

				extractionStatus = `Extracting fields... (${i + 1}s)`;
			} catch {
				// Poll failed — keep trying
			}
		}

		// Timeout
		extracting = false;
		extractionStatus = '';
		toast('Extraction is taking longer than expected. Refresh the page to check.', 'warning');
		await invoiceStore.fetch();
	}

	async function deleteInvoice() {
		deleting = true;
		try {
			await api.delete(`/api/invoices/${invoice.id}`);
			await invoiceStore.fetch();
			toast('Invoice deleted', 'success');
			onclose();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Delete failed', 'error');
		} finally {
			deleting = false;
			confirmDelete = false;
		}
	}

	async function downloadExport(format: string) {
		showExportMenu = false;
		try {
			const url = `/api/invoices/${invoice.id}/export?format=${format}`;
			if (format === 'json') {
				const data = await api.get(url);
				const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
				triggerDownload(blob, `invoice-${invoice.invoice_number || invoice.id}.json`);
			} else {
				// For XML/CSV, fetch raw response
				const { PUBLIC_API_URL } = await import('$env/static/public');
				const base = PUBLIC_API_URL.replace(/\/+$/, '');
				const token = localStorage.getItem('auth_token');
				const res = await fetch(`${base}${url}`, {
					headers: {
						...(token ? { Authorization: `Bearer ${token}` } : {}),
						'X-Tenant-Slug': document.location.hostname.split('.')[0],
					},
				});
				if (!res.ok) throw new Error(`Export failed: ${res.status}`);
				const blob = await res.blob();
				const ext = format === 'xml' ? 'xml' : 'csv';
				triggerDownload(blob, `invoice-${invoice.invoice_number || invoice.id}.${ext}`);
			}
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Export failed', 'error');
		}
	}

	function triggerDownload(blob: Blob, filename: string) {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	interface LineItem {
		id: string;
		line_number: number | null;
		item_code: string | null;
		description: string | null;
		quantity: number | null;
		unit_price: number | null;
		tax: number | null;
		total: number | null;
		gl_account: string | null;
	}

	let lineItems = $state<LineItem[]>([]);
	let lineItemsDirty = $state(false);
	let savingLines = $state(false);

	function updateLineItem(idx: number, field: string, value: unknown) {
		lineItems = lineItems.map((li, i) => i === idx ? { ...li, [field]: value } : li);
		lineItemsDirty = true;
	}

	function addLineItem() {
		lineItems = [...lineItems, {
			id: '',
			line_number: lineItems.length + 1,
			item_code: null,
			description: '',
			quantity: 1,
			unit_price: null,
			tax: null,
			total: null,
			gl_account: gl_account || null,
		}];
		lineItemsDirty = true;
	}

	function removeLineItem(idx: number) {
		lineItems = lineItems.filter((_, i) => i !== idx);
		lineItemsDirty = true;
	}

	async function saveLineItems() {
		savingLines = true;
		try {
			await api.put(`/api/invoices/${invoice.id}/line-items`, lineItems.map((li, idx) => ({
				line_number: idx + 1,
				item_code: li.item_code,
				description: li.description,
				quantity: li.quantity,
				unit_price: li.unit_price,
				tax: li.tax,
				total: li.total,
				gl_account: li.gl_account,
			})));
			lineItemsDirty = false;
			toast('Line items saved', 'success');
			await loadLineItems();
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Save failed', 'error');
		} finally {
			savingLines = false;
		}
	}

	let auditLog = $state<AuditEntry[]>([]);

	// Per-field confidence from extraction
	type FieldConfidence = Record<string, number>;
	let fieldConfidence = $state<FieldConfidence>({});
	let erpInfo = $derived.by(() => {
		const erpActions = auditLog.filter(e =>
			e.action.startsWith('invoice.erp_') || e.action.startsWith('invoice.completed')
		);
		if (erpActions.length === 0) return null;
		const latest = erpActions[erpActions.length - 1];
		return {
			erp_reference: (latest.details as Record<string, unknown>)?.erp_reference as string | undefined,
			erp_document_id: (latest.details as Record<string, unknown>)?.erp_document_id as string | undefined,
			last_error: (latest.details as Record<string, unknown>)?.error as string | undefined,
			action: latest.action,
			actor: latest.actor_name,
			time: latest.created_at,
		};
	});
	let auditLoading = $state(false);

	$effect(() => {
		loadAuditLog();
		loadExtractionConfidence();
		loadLineItems();
	});

	async function loadLineItems() {
		try {
			lineItems = await api.get<LineItem[]>(`/api/invoices/${invoice.id}/line-items`);
		} catch {
			// non-critical
		}
	}

	async function loadAuditLog() {
		auditLoading = true;
		try {
			auditLog = await api.get<AuditEntry[]>(`/api/invoices/${invoice.id}/audit-log`);
		} catch {
			// non-critical
		} finally {
			auditLoading = false;
		}
	}

	async function loadExtractionConfidence() {
		try {
			const results = await api.get<Array<{ raw_result: Record<string, unknown> | null }>>(`/api/invoices/${invoice.id}/extraction`);
			if (results.length === 0) return;
			const latest = results[0];
			const raw = latest.raw_result;
			if (!raw) return;

			const conf: FieldConfidence = {};
			for (const [key, val] of Object.entries(raw)) {
				if (val && typeof val === 'object' && 'confidence' in val) {
					conf[key] = (val as { confidence: number }).confidence;
				}
			}
			fieldConfidence = conf;
		} catch {
			// non-critical
		}
	}

	function confidenceLabel(score: number): string {
		if (score >= 0.95) return 'Very high';
		if (score >= 0.85) return 'High';
		if (score >= 0.7) return 'Medium';
		if (score >= 0.5) return 'Low';
		return 'Very low';
	}

	function confidenceColor(score: number): string {
		if (score >= 0.85) return '#1fa86a';
		if (score >= 0.7) return '#d4940a';
		return '#e04040';
	}

	function formatAuditDate(iso: string): string {
		if (!iso) return '';
		const d = new Date(iso);
		return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
			' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
	}

	function handleBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="backdrop" onclick={handleBackdrop}>
	<div class="modal" class:fullscreen role="dialog" aria-label="Edit invoice {invoice.invoice_number}">
		<header>
			<h2>Edit Invoice &mdash; {invoice.invoice_number}</h2>
			<div class="header-actions">
				<button class="icon-btn" onclick={toggleFullscreen} aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
					{#if fullscreen}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" />
							<line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" />
						</svg>
					{:else}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
							<line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
						</svg>
					{/if}
				</button>
				<button class="icon-btn close-btn" onclick={onclose} aria-label="Close">&times;</button>
			</div>
		</header>

		<div class="split" class:resizing>
			<div class="pdf-pane">
				{#if invoice.file_url}
					{@const isImage = /\.(png|jpg|jpeg|tiff?)$/i.test(invoice.file_url)}
					{#if isImage}
						<img src={`${apiBase}${invoice.file_url}`} alt="Invoice — {invoice.invoice_number}" class="invoice-image" />
					{:else}
						<iframe src={`${apiBase}${invoice.file_url}`} title="Invoice PDF — {invoice.invoice_number}"></iframe>
					{/if}
				{:else}
					<div class="no-pdf">
						<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="15" x2="15" y2="15" />
						</svg>
						<span>No PDF attached</span>
					</div>
				{/if}
			</div>

			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="resize-handle" onmousedown={startResize}></div>
			<div class="form-pane" style="width:{formPaneWidth}px">
				<form onsubmit={(e) => { e.preventDefault(); save(); }}>
					<div class="form-grid">
						<label class:field-error={canSubmitStatus && !vendor.trim()}>
							<span>Vendor <em class="required">*</em> {#if fieldConfidence.vendor_name}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.vendor_name)}" data-tip="{Math.round(fieldConfidence.vendor_name * 100)}% — {confidenceLabel(fieldConfidence.vendor_name)}"></span>{/if}</span>
							<input type="text" bind:value={vendor} required />
						</label>
						<label class:field-error={canSubmitStatus && !invoice_number.trim()}>
							<span>Invoice # <em class="required">*</em> {#if fieldConfidence.invoice_number}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.invoice_number)}" data-tip="{Math.round(fieldConfidence.invoice_number * 100)}% — {confidenceLabel(fieldConfidence.invoice_number)}"></span>{/if}</span>
							<input type="text" bind:value={invoice_number} required />
						</label>
						<label class:field-error={canSubmitStatus && (!amount || amount <= 0)}>
							<span>Amount <em class="required">*</em> {#if fieldConfidence.amount}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.amount)}" data-tip="{Math.round(fieldConfidence.amount * 100)}% — {confidenceLabel(fieldConfidence.amount)}"></span>{/if}</span>
							<input type="number" step="0.01" bind:value={amount} required />
						</label>
						<label>
							<span>Due Date {#if fieldConfidence.due_date}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.due_date)}" data-tip="{Math.round(fieldConfidence.due_date * 100)}% — {confidenceLabel(fieldConfidence.due_date)}"></span>{/if}</span>
							<input type="date" bind:value={due_date} />
						</label>
						<label>
							<span>PO Number {#if fieldConfidence.po_number}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.po_number)}" data-tip="{Math.round(fieldConfidence.po_number * 100)}% — {confidenceLabel(fieldConfidence.po_number)}"></span>{/if}</span>
							<input type="text" bind:value={po_number} />
						</label>
						{#if !isClerkOnly}
							<label>
								<span>Status</span>
								<select bind:value={status}>
									{#each INVOICE_STATUSES as s}
										<option value={s}>{STATUS_LABELS[s]}</option>
									{/each}
								</select>
							</label>
						{:else}
							<label>
								<span>Status</span>
								<input type="text" value={STATUS_LABELS[status]} disabled />
							</label>
						{/if}
						<label>
							<span>Reference # {#if fieldConfidence.reference_number}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.reference_number)}" data-tip="{Math.round(fieldConfidence.reference_number * 100)}% — {confidenceLabel(fieldConfidence.reference_number)}"></span>{/if}</span>
							<input type="text" bind:value={reference_number} />
						</label>
						<label>
							<span>Payment Method {#if fieldConfidence.payment_method}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.payment_method)}" data-tip="{Math.round(fieldConfidence.payment_method * 100)}% — {confidenceLabel(fieldConfidence.payment_method)}"></span>{/if}</span>
							<select bind:value={payment_method}>
								<option value="">—</option>
								<option value="ach">ACH</option>
								<option value="wire">Wire Transfer</option>
								<option value="check">Check</option>
								<option value="credit_card">Credit Card</option>
								<option value="other">Other</option>
							</select>
						</label>
						<label>
							<span>Tax Rate (%) {#if fieldConfidence.tax_rate}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.tax_rate)}" data-tip="{Math.round(fieldConfidence.tax_rate * 100)}% — {confidenceLabel(fieldConfidence.tax_rate)}"></span>{/if}</span>
							<input type="number" step="0.01" min="0" max="100" bind:value={tax_rate} />
						</label>
						<label>
							<span>Vendor Tax ID {#if fieldConfidence.vendor_tax_id}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.vendor_tax_id)}" data-tip="{Math.round(fieldConfidence.vendor_tax_id * 100)}% — {confidenceLabel(fieldConfidence.vendor_tax_id)}"></span>{/if}</span>
							<input type="text" bind:value={vendor_tax_id} placeholder="EIN / VAT #" />
						</label>
						<label class="full-width">
							<span>Vendor Address {#if fieldConfidence.vendor_address}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.vendor_address)}" data-tip="{Math.round(fieldConfidence.vendor_address * 100)}% — {confidenceLabel(fieldConfidence.vendor_address)}"></span>{/if}</span>
							<input type="text" bind:value={vendor_address} />
						</label>
						<label class="full-width">
							<span>Ship-to Address {#if fieldConfidence.ship_to_address}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.ship_to_address)}" data-tip="{Math.round(fieldConfidence.ship_to_address * 100)}% — {confidenceLabel(fieldConfidence.ship_to_address)}"></span>{/if}</span>
							<input type="text" bind:value={ship_to_address} />
						</label>
						<label class="full-width">
							<span>Description {#if fieldConfidence.description}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.description)}" data-tip="{Math.round(fieldConfidence.description * 100)}% — {confidenceLabel(fieldConfidence.description)}"></span>{/if}</span>
							<input type="text" bind:value={description} />
						</label>
						<label>
							<span>GL Account {#if fieldConfidence.suggested_gl_account}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.suggested_gl_account)}" data-tip="{Math.round(fieldConfidence.suggested_gl_account * 100)}% — {confidenceLabel(fieldConfidence.suggested_gl_account)}"></span>{/if}</span>
							{#if glAccounts.length > 0}
								<select bind:value={gl_account}>
									<option value="">— Select —</option>
									{#each glAccounts as acct}
										<option value={acct.code}>{acct.code} — {acct.name}</option>
									{/each}
								</select>
							{:else}
								<input type="text" bind:value={gl_account} placeholder="e.g. 6100" />
							{/if}
						</label>
						<label>
							<span>Cost Center {#if fieldConfidence.suggested_cost_center}<span class="confidence-dot" style="background:{confidenceColor(fieldConfidence.suggested_cost_center)}" data-tip="{Math.round(fieldConfidence.suggested_cost_center * 100)}% — {confidenceLabel(fieldConfidence.suggested_cost_center)}"></span>{/if}</span>
							<input type="text" bind:value={cost_center} placeholder="e.g. ADMIN" />
						</label>
					</div>

					<div class="line-items-section">
						<div class="line-items-header">
							<span class="line-items-title">Line Items</span>
							<button type="button" class="btn-add-line" onclick={addLineItem}>+ Add Line</button>
						</div>
						{#if lineItems.length > 0}
							<table class="line-items-table">
								<thead>
									<tr>
										<th>#</th>
										<th>Description</th>
										<th class="right">Qty</th>
										<th class="right">Unit Price</th>
										<th class="right">Tax</th>
										<th class="right">Total</th>
										<th>GL</th>
										<th></th>
									</tr>
								</thead>
								<tbody>
									{#each lineItems as li, idx}
										<tr>
											<td class="li-num">{idx + 1}</td>
											<td><input type="text" class="li-input" value={li.description ?? ''} oninput={(e) => updateLineItem(idx, 'description', e.currentTarget.value)} /></td>
											<td><input type="number" class="li-input right" step="0.01" value={li.quantity ?? ''} oninput={(e) => updateLineItem(idx, 'quantity', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} /></td>
											<td><input type="number" class="li-input right" step="0.01" value={li.unit_price ?? ''} oninput={(e) => updateLineItem(idx, 'unit_price', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} /></td>
											<td><input type="number" class="li-input right" step="0.01" value={li.tax ?? ''} oninput={(e) => updateLineItem(idx, 'tax', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} /></td>
											<td><input type="number" class="li-input right" step="0.01" value={li.total ?? ''} oninput={(e) => updateLineItem(idx, 'total', e.currentTarget.value ? parseFloat(e.currentTarget.value) : null)} /></td>
											<td>
											{#if glAccounts.length > 0}
												<select class="li-input li-gl" value={li.gl_account ?? ''} onchange={(e) => updateLineItem(idx, 'gl_account', e.currentTarget.value)}>
													<option value="">—</option>
													{#each glAccounts as acct}
														<option value={acct.code}>{acct.code}</option>
													{/each}
												</select>
											{:else}
												<input type="text" class="li-input li-gl" value={li.gl_account ?? ''} oninput={(e) => updateLineItem(idx, 'gl_account', e.currentTarget.value)} />
											{/if}
										</td>
											<td>
												<button type="button" class="li-delete" onclick={() => removeLineItem(idx)}>&times;</button>
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						{:else}
							<p class="line-items-empty">No line items. Click "+ Add Line" to add one.</p>
						{/if}
						{#if lineItemsDirty}
							<div class="line-items-actions">
								<button type="button" class="btn-save-lines" disabled={savingLines} onclick={saveLineItems}>
									{savingLines ? 'Saving...' : 'Save Line Items'}
								</button>
							</div>
						{/if}
					</div>

					{#if invoice.warnings?.filter(w => w.type !== 'missing_field').length}
						<div class="warnings-list">
							{#each invoice.warnings.filter(w => w.type !== 'missing_field') as w}
								<div class="warning-item {w.severity}">
									<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
										<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
									</svg>
									{w.message}
								</div>
							{/each}
						</div>
					{/if}

					{#if canSubmitStatus && missingFields.length > 0}
						<div class="validation-hint">Required: {missingFields.join(', ')}</div>
					{/if}

					{#if invoice.approved_by || invoice.rejected_by || invoice.assigned_to}
						<div class="meta-section">
							{#if invoice.assigned_to}
								<div class="meta-item">
									<span class="meta-label">Assigned to</span>
									<span class="meta-value">{invoice.assigned_to}</span>
								</div>
							{/if}
							{#if invoice.approved_by}
								<div class="meta-item">
									<span class="meta-label">Approved by</span>
									<span class="meta-value approved">{invoice.approved_by}</span>
									{#if invoice.approval_date}
										<span class="meta-date">{invoice.approval_date}</span>
									{/if}
								</div>
							{/if}
							{#if invoice.rejected_by}
								<div class="meta-item">
									<span class="meta-label">Rejected by</span>
									<span class="meta-value rejected">{invoice.rejected_by}</span>
								</div>
							{/if}
						</div>
					{/if}

					{#if isErpStatus || (status === 'failed' && erpInfo)}
						<div class="erp-section">
							<div class="erp-title">ERP Status</div>
							<div class="erp-details">
								{#if erpInfo?.erp_reference}
									<div class="erp-row">
										<span class="erp-label">ERP Reference</span>
										<code class="erp-value">{erpInfo.erp_reference}</code>
									</div>
								{/if}
								{#if erpInfo?.erp_document_id}
									<div class="erp-row">
										<span class="erp-label">Document ID</span>
										<code class="erp-value">{erpInfo.erp_document_id}</code>
									</div>
								{/if}
								{#if erpInfo?.last_error}
									<div class="erp-row erp-error">
										<span class="erp-label">Error</span>
										<span class="erp-value">{erpInfo.last_error}</span>
									</div>
								{/if}
							</div>
							{#if canRetryErp}
								<button type="button" class="btn-retry-erp" disabled={retryingErp} onclick={handleRetryErp}>
									<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
									{retryingErp ? 'Retrying...' : 'Retry ERP Send'}
								</button>
							{/if}
						</div>
					{/if}

					{#if auditLog.length > 0}
						<div class="activity-section">
							<div class="activity-title">Activity</div>
							<div class="activity-list">
								{#each auditLog as entry}
									<div class="activity-item">
										<div class="activity-dot"></div>
										<div class="activity-content">
											<span class="activity-action">{ACTION_LABELS[entry.action] ?? entry.action}</span>
											{#if entry.actor_name}
												<span class="activity-actor">by {entry.actor_name}</span>
											{/if}
											{#if entry.details?.reason}
												<span class="activity-detail">— {entry.details.reason}</span>
											{/if}
											{#if entry.action === 'invoice.extraction_completed' && entry.details?.confidence}
												<span class="activity-detail">— {entry.details.method} ({Math.round((entry.details.confidence as number) * 100)}% confidence)</span>
											{/if}
											{#if entry.action === 'invoice.extraction_completed' && entry.details?.gl_suggested}
												<span class="activity-detail">GL suggested: {entry.details.gl_suggested}</span>
											{/if}
											{#if entry.action === 'invoice.extraction_completed' && entry.details?.vendor_action === 'created'}
												<span class="activity-detail">New vendor created (unverified)</span>
											{/if}
											{#if entry.action === 'invoice.extraction_failed' && entry.details?.error}
												<span class="activity-detail error">— {entry.details.error}</span>
											{/if}
										</div>
										<span class="activity-time">{formatAuditDate(entry.created_at)}</span>
									</div>
								{/each}
							</div>
						</div>
					{/if}

					{#if canReview}
						<div class="review-section">
							<div class="review-title">Review</div>
							{#if showRejectForm}
								<div class="reject-form">
									<textarea
										class="reject-input"
										placeholder="Reason for rejection..."
										bind:value={rejectReason}
										rows="2"
									></textarea>
									<div class="reject-actions">
										<button type="button" class="btn-cancel-sm" onclick={() => { showRejectForm = false; rejectReason = ''; }}>Cancel</button>
										<button type="button" class="btn-reject" disabled={reviewing || !rejectReason.trim()} onclick={handleReject}>
											{reviewing ? 'Rejecting...' : 'Confirm Reject'}
										</button>
									</div>
								</div>
							{:else}
								<div class="review-actions">
									<button type="button" class="btn-approve" disabled={reviewing} onclick={handleApprove}>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
										{reviewing ? 'Approving...' : 'Approve'}
									</button>
									<button type="button" class="btn-reject-outline" disabled={reviewing} onclick={() => (showRejectForm = true)}>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
										Reject
									</button>
								</div>
							{/if}
						</div>
					{:else if isReadyForReview && invoice.assigned_to}
						<div class="review-section">
							<p class="review-assigned-hint">Assigned to <strong>{invoice.assigned_to}</strong> for review.</p>
						</div>
					{/if}

					<footer>
						<div class="footer-right">
							<button type="button" class="btn-cancel" onclick={onclose}>Cancel</button>
							{#if canExtract || extracting}
								<button type="button" class="btn-extract" disabled={extracting} onclick={handleExtract}>
									{#if extracting}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" stroke-dasharray="31.4" stroke-dashoffset="10"><animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite"/></circle></svg>
										{extractionStatus || 'Extracting...'}
									{:else}
										<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
										{status === 'failed' ? 'Re-extract' : 'Extract'}
									{/if}
								</button>
							{/if}
							{#if isExtracting && !extracting}
								<button type="button" class="btn-reset" disabled={resettingExtraction} onclick={handleResetExtraction}>
									{resettingExtraction ? 'Resetting...' : 'Reset'}
								</button>
							{/if}
							{#if !isDone}
								<button type="submit" class="btn-save" disabled={saving}>
									{saving ? 'Saving...' : 'Save'}
								</button>
							{/if}
							{#if canSubmit}
								{#if needsApproverSelect}
									<select class="approver-select" bind:value={selectedApproverId}>
										<option value="">Approver...</option>
										{#each adminStore.users.filter(u => u.is_active && u.id !== auth.user?.id) as user}
											<option value={user.id}>{user.full_name}</option>
										{/each}
									</select>
								{/if}
								<button type="button" class="btn-submit" disabled={submitting || (needsApproverSelect && !selectedApproverId)} onclick={submitDone}>
									{submitting ? 'Submitting...' : submitLabel}
								</button>
							{/if}
						</div>
					</footer>
				</form>
			</div>
		</div>
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: grid;
		place-items: center;
		z-index: 100;
		backdrop-filter: blur(2px);
	}

	.modal {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		width: min(1300px, 95vw);
		height: min(800px, 92vh);
		display: flex;
		flex-direction: column;
		box-shadow: 0 16px 48px rgba(0, 0, 0, 0.3);
		overflow: hidden;
		transition: all 0.2s ease;
	}

	.modal.fullscreen {
		width: 100vw;
		height: 100vh;
		border-radius: 0;
		border: none;
	}


	header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		flex-shrink: 0;
	}

	h2 {
		margin: 0;
		font-size: 1.05rem;
		font-weight: 600;
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	.icon-btn {
		background: none;
		border: 1px solid transparent;
		border-radius: 4px;
		cursor: pointer;
		color: var(--text-muted);
		padding: 6px;
		display: grid;
		place-items: center;
	}

	.icon-btn:hover {
		color: var(--text);
		background: var(--bg);
		border-color: var(--border);
	}

	.close-btn {
		font-size: 1.4rem;
		line-height: 1;
		padding: 4px 6px;
	}

	/* --- Split pane --- */

	.split {
		display: flex;
		flex: 1;
		min-height: 0;
	}

	.split.resizing {
		user-select: none;
		cursor: col-resize;
	}

	/* --- PDF pane --- */

	.pdf-pane {
		flex: 1;
		border-right: 1px solid var(--border);
		background: #1a1a24;
		display: flex;
	}

	.pdf-pane iframe {
		width: 100%;
		height: 100%;
		border: none;
	}

	.pdf-pane .invoice-image {
		width: 100%;
		height: 100%;
		object-fit: contain;
	}

	.no-pdf {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 12px;
		color: var(--text-muted);
		font-size: 0.9rem;
	}

	/* --- Form pane --- */

	.form-pane {
		flex-shrink: 0;
		overflow-y: auto;
		overflow-x: hidden;
		display: flex;
		flex-direction: column;
	}

	.resize-handle {
		width: 5px;
		cursor: col-resize;
		background: transparent;
		flex-shrink: 0;
		position: relative;
		z-index: 2;
	}

	.resize-handle:hover,
	.resize-handle:active {
		background: var(--accent);
		opacity: 0.3;
	}

	form {
		padding: 20px;
		display: flex;
		flex-direction: column;
		flex: 1;
		min-width: 0;
	}

	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 14px;
		min-width: 0;
	}

	.full-width {
		grid-column: 1 / -1;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		min-width: 0;
	}

	label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	input,
	select {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.88rem;
		color: var(--text);
		font-family: inherit;
		min-width: 0;
		width: 100%;
		box-sizing: border-box;
	}

	input:focus,
	select:focus {
		outline: none;
		border-color: var(--accent);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}

	footer {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		padding-top: 18px;
		border-top: 1px solid var(--border);
		margin-top: auto;
	}

	.footer-left {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.footer-right {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
	}

	.btn-cancel,
	.btn-save,
	.btn-submit {
		padding: 8px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid var(--border);
		font-family: inherit;
		white-space: nowrap;
	}

	.btn-cancel {
		background: var(--surface);
		color: var(--text-muted);
	}

	.btn-cancel:hover {
		background: var(--bg);
	}

	.btn-delete-outline {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		white-space: nowrap;
		background: var(--surface);
		color: var(--text-muted);
		border: 1px solid var(--border);
		transition: all 0.15s;
	}

	.btn-delete-outline:hover {
		border-color: #e04040;
		color: #e04040;
	}

	.btn-delete-outline.armed {
		border-color: #e04040;
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
	}

	.btn-delete-outline:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* --- Line items --- */

	.line-items-section {
		margin-top: 14px;
	}

	.line-items-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 8px;
	}

	.line-items-title {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
	}

	.btn-add-line {
		padding: 3px 10px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.75rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-add-line:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.line-items-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8rem;
		border: 1px solid var(--border);
		border-radius: 6px;
		overflow: hidden;
	}

	.line-items-table th {
		background: var(--bg);
		padding: 8px 8px;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		text-align: left;
		white-space: nowrap;
	}

	.line-items-table td {
		padding: 3px 4px;
		border-bottom: 1px solid var(--border);
		color: var(--text);
	}

	.line-items-table tr:last-child td {
		border-bottom: none;
	}

	.line-items-table .right {
		text-align: right;
	}

	.li-num {
		text-align: center;
		color: var(--text-muted);
		font-size: 0.75rem;
		width: 28px;
	}

	.li-input {
		width: 100%;
		min-width: 0;
		box-sizing: border-box;
		padding: 5px 7px;
		border: 1px solid transparent;
		border-radius: 4px;
		background: transparent;
		font-size: 0.8rem;
		color: var(--text);
		font-family: inherit;
	}

	.li-input:focus {
		outline: none;
		border-color: var(--accent);
		background: var(--bg);
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.1);
	}

	.li-input:hover:not(:focus) {
		border-color: var(--border);
		background: var(--bg);
	}

	.li-input.right {
		text-align: right;
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
	}

	.li-gl {
		min-width: 50px;
	}

	/* Description column gets more space */
	.line-items-table th:nth-child(2),
	.line-items-table td:nth-child(2) {
		width: 35%;
	}

	/* Number columns get fixed min width */
	.line-items-table th:nth-child(3),
	.line-items-table td:nth-child(3),
	.line-items-table th:nth-child(4),
	.line-items-table td:nth-child(4),
	.line-items-table th:nth-child(5),
	.line-items-table td:nth-child(5),
	.line-items-table th:nth-child(6),
	.line-items-table td:nth-child(6) {
		width: 12%;
	}

	.li-delete {
		background: none;
		border: none;
		color: var(--text-muted);
		cursor: pointer;
		font-size: 1rem;
		padding: 0 4px;
		line-height: 1;
	}

	.li-delete:hover {
		color: #e04040;
	}

	.line-items-empty {
		font-size: 0.8rem;
		color: var(--text-muted);
		text-align: center;
		padding: 12px;
		margin: 0;
	}

	.line-items-actions {
		display: flex;
		justify-content: flex-start;
		margin-top: 8px;
	}

	.btn-save-lines {
		padding: 5px 14px;
		border-radius: 4px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.8rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-save-lines:hover:not(:disabled) {
		opacity: 0.85;
	}

	.btn-save-lines:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.confidence-dot {
		display: inline-block;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		margin-left: 4px;
		vertical-align: middle;
		cursor: help;
		position: relative;
		/* Expand hover area */
		padding: 4px;
		margin: -4px;
		margin-left: 0;
		background-clip: content-box;
	}

	.confidence-dot::after {
		content: attr(data-tip);
		position: absolute;
		bottom: calc(100% + 6px);
		left: 50%;
		transform: translateX(-50%);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 4px 8px;
		font-size: 0.72rem;
		font-weight: 500;
		color: var(--text);
		white-space: nowrap;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
		pointer-events: none;
		opacity: 0;
		transition: opacity 0.15s;
		z-index: 10;
	}

	.confidence-dot:hover::after {
		opacity: 1;
	}

	.btn-reset {
		padding: 8px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid #d4940a;
		font-family: inherit;
		white-space: nowrap;
		background: var(--surface);
		color: #d4940a;
	}

	.btn-reset:hover:not(:disabled) {
		background: rgba(212, 148, 10, 0.1);
	}

	.btn-reset:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.btn-extract {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid var(--border);
		font-family: inherit;
		white-space: nowrap;
		background: var(--surface);
		color: var(--text-muted);
	}

	.btn-extract:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}

	.btn-extract:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.btn-save {
		background: var(--accent);
		color: #fff;
		border-color: var(--accent);
	}

	.btn-save:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-save:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.btn-submit {
		background: #1fa86a;
		color: #fff;
		border-color: #1fa86a;
	}

	.btn-submit:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-submit:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.approver-select {
		padding: 8px 10px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-size: 0.82rem;
		font-family: inherit;
		max-width: 140px;
	}

	.approver-select:focus {
		outline: none;
		border-color: var(--accent);
	}

	/* Export dropdown */
	.export-wrapper {
		position: relative;
	}

	.btn-export {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding: 8px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-family: inherit;
	}

	.btn-export:hover {
		border-color: var(--accent);
		color: var(--accent);
	}

	.export-menu {
		position: absolute;
		bottom: 100%;
		left: 0;
		margin-bottom: 4px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
		overflow: hidden;
		z-index: 10;
	}

	.export-menu button {
		display: block;
		width: 100%;
		padding: 8px 20px;
		border: none;
		background: none;
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		text-align: left;
		font-family: inherit;
	}

	.export-menu button:hover {
		background: rgba(99, 140, 255, 0.1);
		color: var(--accent);
	}

	.warnings-list {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 10px;
	}

	.warning-item {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.8rem;
		padding: 6px 10px;
		border-radius: 5px;
	}

	.warning-item.error {
		background: rgba(224, 64, 64, 0.1);
		color: #e04040;
	}

	.warning-item.warning {
		background: rgba(212, 148, 10, 0.1);
		color: #d4940a;
	}

	.warning-item.info {
		background: rgba(99, 140, 255, 0.1);
		color: #638cff;
	}

	.required {
		color: #e04040;
		font-style: normal;
	}

	.field-error input,
	.field-error select {
		border-color: #e04040;
	}

	.field-error span {
		color: #e04040;
	}

	.validation-hint {
		font-size: 0.8rem;
		color: #d4940a;
		margin-top: 8px;
	}

	/* --- Approval/assignment meta --- */

	.meta-section {
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin-top: 12px;
		padding: 10px 12px;
		background: var(--bg);
		border-radius: 6px;
		border: 1px solid var(--border);
	}

	.meta-item {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 0.8rem;
	}

	.meta-label {
		color: var(--text-muted);
		min-width: 80px;
	}

	.meta-value {
		font-weight: 500;
		color: var(--text);
	}

	.meta-value.approved {
		color: #1fa86a;
	}

	.meta-value.rejected {
		color: #e04040;
	}

	.meta-date {
		color: var(--text-muted);
		font-size: 0.75rem;
	}

	/* --- Activity log --- */

	.activity-section {
		margin-top: 14px;
	}

	.activity-title {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 8px;
	}

	.activity-list {
		display: flex;
		flex-direction: column;
		gap: 0;
		border-left: 2px solid var(--border);
		margin-left: 4px;
		padding-left: 12px;
	}

	.activity-item {
		display: flex;
		align-items: flex-start;
		gap: 6px;
		padding: 5px 0;
		position: relative;
		font-size: 0.78rem;
	}

	.activity-dot {
		position: absolute;
		left: -17px;
		top: 10px;
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--text-muted);
	}

	.activity-content {
		flex: 1;
		min-width: 0;
	}

	.activity-action {
		color: var(--text);
		font-weight: 500;
	}

	.activity-actor {
		color: var(--text-muted);
	}

	.activity-detail {
		color: var(--text-muted);
		display: block;
	}

	.activity-detail.error {
		color: #e04040;
		font-style: italic;
	}

	.activity-time {
		color: var(--text-muted);
		font-size: 0.72rem;
		white-space: nowrap;
		flex-shrink: 0;
	}

	/* --- ERP status section --- */

	.erp-section {
		margin-top: 14px;
		padding: 12px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
	}

	.erp-title {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 8px;
	}

	.erp-details {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.erp-row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 0.8rem;
	}

	.erp-label {
		color: var(--text-muted);
		min-width: 80px;
	}

	.erp-value {
		font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
		font-size: 0.78rem;
		color: var(--text);
	}

	.erp-error .erp-value {
		color: #e04040;
		font-family: inherit;
	}

	.btn-retry-erp {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		margin-top: 10px;
		padding: 6px 14px;
		border-radius: 4px;
		border: 1px solid var(--accent);
		background: var(--surface);
		color: var(--accent);
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-retry-erp:hover:not(:disabled) {
		background: rgba(99, 140, 255, 0.08);
	}

	.btn-retry-erp:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	/* --- Review section --- */

	.review-section {
		margin-top: 14px;
		padding: 12px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
	}

	.review-title {
		font-size: 0.72rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		margin-bottom: 10px;
	}

	.review-actions {
		display: flex;
		gap: 8px;
	}

	.btn-approve {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 18px;
		border-radius: 4px;
		border: none;
		background: #1fa86a;
		color: #fff;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		flex: 1;
		justify-content: center;
	}

	.btn-approve:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-approve:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.btn-reject-outline {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 8px 18px;
		border-radius: 4px;
		border: 1px solid #e04040;
		background: var(--surface);
		color: #e04040;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
		flex: 1;
		justify-content: center;
	}

	.btn-reject-outline:hover:not(:disabled) {
		background: rgba(224, 64, 64, 0.1);
	}

	.btn-reject-outline:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.reject-form {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.reject-input {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 0.85rem;
		color: var(--text);
		font-family: inherit;
		resize: vertical;
		width: 100%;
		box-sizing: border-box;
	}

	.reject-input:focus {
		outline: none;
		border-color: #e04040;
		box-shadow: 0 0 0 2px rgba(224, 64, 64, 0.15);
	}

	.reject-actions {
		display: flex;
		gap: 8px;
		justify-content: flex-end;
	}

	.btn-cancel-sm {
		padding: 6px 12px;
		border-radius: 4px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text-muted);
		font-size: 0.82rem;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-cancel-sm:hover {
		background: var(--bg);
	}

	.btn-reject {
		padding: 6px 14px;
		border-radius: 4px;
		border: none;
		background: #e04040;
		color: #fff;
		font-size: 0.82rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	.btn-reject:hover:not(:disabled) {
		opacity: 0.9;
	}

	.btn-reject:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.review-assigned-hint {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0;
	}

	.save-error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 8px 12px;
		border-radius: 4px;
		font-size: 0.82rem;
		margin-top: 8px;
	}

	/* --- Responsive: stack on narrow screens --- */

	@media (max-width: 768px) {
		.split {
			flex-direction: column;
		}

		.pdf-pane {
			border-right: none;
			border-bottom: 1px solid var(--border);
			height: 45%;
			flex: none;
		}

		.form-pane {
			width: 100% !important;
		}

		.resize-handle {
			display: none;
		}
	}
</style>
