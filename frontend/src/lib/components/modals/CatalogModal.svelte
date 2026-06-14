<script lang="ts">
	import type { Catalog, CatalogItem, CatalogType } from '$lib/types/catalog';
	import { CATALOG_TYPES, CATALOG_TYPE_LABELS } from '$lib/types/catalog';
	import { auth } from '$lib/stores/auth.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Money from '$lib/components/ui/Money.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import {
		createCatalog,
		updateCatalog,
		createCatalogItem,
		deleteCatalogItem,
		type GlAccountOption,
		type VendorOption
	} from '$lib/api/catalogs';

	let {
		catalog,
		vendors,
		glAccounts,
		onclose,
		onsaved
	}: {
		// null → create mode; a Catalog → detail/edit mode.
		catalog: Catalog | null;
		vendors: VendorOption[];
		glAccounts: GlAccountOption[];
		onclose: () => void;
		onsaved: (c: Catalog) => void;
	} = $props();

	const isCreate = $derived(catalog === null);
	// create / update = admin / ap_manager.
	const canEdit = $derived(auth.isManager);

	/* eslint-disable svelte/state-referenced-locally -- modal receives a snapshot */
	let name = $state(catalog?.name ?? '');
	let catalog_type = $state<CatalogType>((catalog?.catalog_type as CatalogType) ?? 'internal');
	let vendor_id = $state(catalog?.vendor_id ?? '');
	let punchout_url = $state(catalog?.punchout_url ?? '');
	let is_active = $state(catalog?.is_active ?? true);
	let is_preferred = $state(catalog?.is_preferred ?? false);
	let description = $state(catalog?.description ?? '');
	// Live item list (edit mode only) — refreshed from the returned Catalog.
	let items = $state<CatalogItem[]>(catalog?.items ?? []);
	/* eslint-enable svelte/state-referenced-locally */

	let saving = $state(false);
	let confirmDeleteItemId = $state<string | null>(null);

	// New-item form (edit mode only).
	let newName = $state('');
	let newSku = $state('');
	let newPrice = $state<number | null>(null);
	let newCurrency = $state('USD');
	let newUom = $state('');
	let newCategory = $state('');
	let newVendorId = $state('');
	let newGlId = $state('');
	let addingItem = $state(false);

	function numOrNull(v: unknown): number | null {
		if (v === '' || v === null || v === undefined) return null;
		const n = parseFloat(String(v));
		return Number.isFinite(n) ? n : null;
	}

	function handleError(err: unknown, fallback: string) {
		toast(err instanceof Error ? err.message : fallback, 'error');
	}

	async function handleSave() {
		if (!name.trim()) return;
		saving = true;
		try {
			const payload = {
				name: name.trim(),
				catalog_type,
				vendor_id: vendor_id || null,
				punchout_url: catalog_type === 'punchout' ? punchout_url.trim() || null : null,
				is_active,
				is_preferred,
				description: description.trim() || null
			};
			const saved = isCreate
				? await createCatalog(payload)
				: await updateCatalog(catalog!.id, payload);
			items = saved.items;
			toast(isCreate ? 'Catalog created' : 'Catalog saved', 'success');
			onsaved(saved);
			if (isCreate) onclose();
		} catch (err) {
			handleError(err, isCreate ? 'Create failed' : 'Save failed');
		} finally {
			saving = false;
		}
	}

	async function handleAddItem() {
		if (!catalog || !newName.trim()) return;
		addingItem = true;
		try {
			const created = await createCatalogItem(catalog.id, {
				name: newName.trim(),
				sku: newSku.trim() || null,
				description: null,
				unit_price: newPrice,
				currency: newCurrency.trim() || 'USD',
				uom: newUom.trim() || null,
				vendor_id: newVendorId || null,
				gl_account_id: newGlId || null,
				category: newCategory.trim() || null,
				is_active: true
			});
			items = [...items, created];
			newName = '';
			newSku = '';
			newPrice = null;
			newUom = '';
			newCategory = '';
			newVendorId = '';
			newGlId = '';
			toast('Item added', 'success');
		} catch (err) {
			handleError(err, 'Could not add item');
		} finally {
			addingItem = false;
		}
	}

	async function handleDeleteItem(id: string) {
		if (confirmDeleteItemId !== id) {
			confirmDeleteItemId = id;
			return;
		}
		try {
			await deleteCatalogItem(id);
			items = items.filter((i) => i.id !== id);
			toast('Item removed', 'success');
		} catch (err) {
			handleError(err, 'Could not remove item');
		} finally {
			confirmDeleteItemId = null;
		}
	}

	const modalTitle = $derived(
		isCreate ? 'New Catalog' : canEdit ? `Edit Catalog — ${catalog!.name}` : `Catalog — ${catalog!.name}`
	);
	const ariaLabel = $derived(isCreate ? 'New catalog' : 'Catalog detail');
</script>

<Modal open {ariaLabel} title={modalTitle} width="lg" {onclose}>
	<form onsubmit={(e) => { e.preventDefault(); handleSave(); }}>
		<div class="form-grid">
			<label>
				<span>Name <em class="required">*</em></span>
				<input type="text" bind:value={name} required disabled={!canEdit} />
			</label>
			<label>
				<span>Type</span>
				<select bind:value={catalog_type} disabled={!canEdit}>
					{#each CATALOG_TYPES as t}
						<option value={t}>{CATALOG_TYPE_LABELS[t]}</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Vendor</span>
				<select bind:value={vendor_id} disabled={!canEdit}>
					<option value="">Select…</option>
					{#each vendors as v (v.id)}
						<option value={v.id}>{v.name}</option>
					{/each}
				</select>
			</label>
			{#if catalog_type === 'punchout'}
				<label>
					<span>Punch-out URL</span>
					<input
						type="url"
						bind:value={punchout_url}
						placeholder="https://supplier.example/punchout"
						disabled={!canEdit}
					/>
				</label>
			{/if}
			<label class="checkbox-label">
				<input type="checkbox" bind:checked={is_active} disabled={!canEdit} />
				<span>Active</span>
			</label>
			<label class="checkbox-label">
				<input type="checkbox" bind:checked={is_preferred} disabled={!canEdit} />
				<span>Preferred (guided buying)</span>
			</label>
			<label class="full-width">
				<span>Description</span>
				<textarea bind:value={description} rows="2" disabled={!canEdit}></textarea>
			</label>
		</div>

		{#if catalog_type === 'punchout'}
			<p class="hint">
				Punch-out is config-only — the URL is stored; live cXML/OCI round-trips are a future
				extension. Punch-out catalogs hold no internal items.
			</p>
		{/if}

		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={onclose}>Close</button>
			{#if canEdit}
				<button type="submit" class="btn-primary" disabled={saving}>
					{saving ? 'Saving…' : isCreate ? 'Create' : 'Save'}
				</button>
			{/if}
		</div>
	</form>

	<!-- Items section (edit mode + internal catalogs only) -->
	{#if !isCreate && catalog_type === 'internal'}
		<section class="items-section">
			<h3>Items <span class="muted">({items.length})</span></h3>
			{#if items.length === 0}
				<p class="muted">No items yet.</p>
			{:else}
				<table class="item-table">
					<thead>
						<tr>
							<th>SKU</th>
							<th>Name</th>
							<th>Category</th>
							<th class="right">Price</th>
							<th>UoM</th>
							{#if canEdit}<th></th>{/if}
						</tr>
					</thead>
					<tbody>
						{#each items as item (item.id)}
							<tr>
								<td class="mono">{item.sku ?? '—'}</td>
								<td>{item.name}</td>
								<td>{item.category ?? '—'}</td>
								<td class="right">
									{#if item.unit_price != null}
										<Money amount={item.unit_price} currency={item.currency} />
									{:else}
										—
									{/if}
								</td>
								<td>{item.uom ?? '—'}</td>
								{#if canEdit}
									<td class="actions">
										<RowAction
											variant="danger"
											armed={confirmDeleteItemId === item.id}
											onclick={() => handleDeleteItem(item.id)}
										>
											{confirmDeleteItemId === item.id ? 'Confirm' : 'Remove'}
										</RowAction>
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}

			{#if canEdit}
				<div class="add-item">
					<input class="grow" type="text" placeholder="Item name *" bind:value={newName} />
					<input type="text" placeholder="SKU" bind:value={newSku} />
					<input
						type="number"
						step="0.01"
						min="0"
						placeholder="Price"
						value={newPrice ?? ''}
						oninput={(e) => (newPrice = numOrNull(e.currentTarget.value))}
					/>
					<input class="cur" type="text" maxlength="3" placeholder="USD" bind:value={newCurrency} />
					<input class="uom" type="text" placeholder="UoM" bind:value={newUom} />
					<input type="text" placeholder="Category" bind:value={newCategory} />
					<select bind:value={newVendorId}>
						<option value="">Vendor…</option>
						{#each vendors as v (v.id)}
							<option value={v.id}>{v.name}</option>
						{/each}
					</select>
					<select bind:value={newGlId}>
						<option value="">GL…</option>
						{#each glAccounts as g (g.id)}
							<option value={g.id}>{g.code}</option>
						{/each}
					</select>
					<button
						type="button"
						class="btn-primary"
						disabled={addingItem || !newName.trim()}
						onclick={handleAddItem}
					>
						{addingItem ? 'Adding…' : 'Add'}
					</button>
				</div>
			{/if}
		</section>
	{/if}
</Modal>

<style>
	.form-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
	.form-grid label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.82rem;
		color: var(--text-muted);
	}
	.form-grid label.full-width {
		grid-column: 1 / -1;
	}
	.form-grid label.checkbox-label {
		flex-direction: row;
		align-items: center;
		gap: 8px;
	}
	.form-grid input,
	.form-grid select,
	.form-grid textarea {
		padding: 7px 9px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.88rem;
	}
	.form-grid input:disabled,
	.form-grid select:disabled,
	.form-grid textarea:disabled {
		opacity: 0.7;
		cursor: not-allowed;
	}

	.hint {
		margin: 10px 0 0;
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	.items-section {
		margin-top: 22px;
		border-top: 1px solid var(--border);
		padding-top: 16px;
	}
	.items-section h3 {
		margin: 0 0 10px;
		font-size: 0.95rem;
	}
	.muted {
		color: var(--text-muted);
		font-size: 0.82rem;
		font-weight: 400;
	}

	.item-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.84rem;
	}
	.item-table th,
	.item-table td {
		text-align: left;
		padding: 6px 8px;
		border-bottom: 1px solid var(--border);
	}
	.item-table th.right,
	.item-table td.right {
		text-align: right;
	}
	.item-table td.mono {
		font-family: var(--mono, monospace);
	}
	.item-table td.actions {
		display: flex;
		gap: 6px;
		white-space: nowrap;
	}

	.add-item {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 12px;
		align-items: center;
	}
	.add-item input,
	.add-item select {
		padding: 6px 8px;
		border-radius: 5px;
		border: 1px solid var(--border);
		background: var(--bg);
		color: var(--text);
		font-family: inherit;
		font-size: 0.82rem;
		width: 110px;
	}
	.add-item input.grow {
		flex: 1;
		min-width: 150px;
	}
	.add-item input.cur {
		width: 56px;
	}
	.add-item input.uom {
		width: 70px;
	}
</style>
