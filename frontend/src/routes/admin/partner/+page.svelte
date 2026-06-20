<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import { getPartnerOverview, getChildBranding, updateChildBranding } from '$lib/api/partner';
	import type { ChildBranding, ChildTenant, PartnerOverview } from '$lib/types/partner';

	// RBAC: the backend gates every /api/partner endpoint to admin only and 403s
	// the rest. Wait for `auth.user` to resolve before redirecting so we don't
	// bounce before /me lands (the api-keys / billing pages do the same).
	const userLoaded = $derived(auth.user !== null);
	const allowed = $derived(auth.isAdmin);

	$effect(() => {
		if (userLoaded && !allowed) goto('/');
	});

	let overview = $state<PartnerOverview | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Narrowed away from null so the DataTable `body` snippet (a closure, which
	// loses the `{:else if overview}` narrowing) can read the children.
	const children = $derived(overview ? overview.children : []);

	const COLUMNS = [
		{ label: 'Tenant' },
		{ label: 'Slug' },
		{ label: 'Plan' },
		{ label: 'Brand name' }
	];

	async function load() {
		loading = true;
		error = null;
		try {
			overview = await getPartnerOverview();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load partner overview.';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		// Only fetch once the role is allowed (avoids a guaranteed 403 before the
		// redirect fires).
		if (userLoaded && allowed) load();
	});

	// Brand-edit modal state.
	let editingChild = $state<ChildTenant | null>(null);
	let brand = $state<ChildBranding | null>(null);
	let brandLoading = $state(false);
	let brandError = $state<string | null>(null);
	let saving = $state(false);

	const HEX_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
	const URL_RE = /^https?:\/\//i;

	async function openEdit(child: ChildTenant) {
		editingChild = child;
		brand = null;
		brandError = null;
		brandLoading = true;
		try {
			brand = await getChildBranding(child.id);
		} catch (e) {
			brandError = e instanceof Error ? e.message : 'Failed to load branding.';
		} finally {
			brandLoading = false;
		}
	}

	function closeEdit() {
		editingChild = null;
		brand = null;
	}

	async function saveBrand() {
		if (!editingChild || !brand) return;
		// Client-side validation mirrors the backend BrandConfig guards so a typo
		// surfaces inline instead of as a 422. The backend still validates.
		const b = brand;
		if (b.accent_color.trim() && !HEX_RE.test(b.accent_color.trim())) {
			toast('Accent color must be a 3- or 6-digit hex (e.g. #638cff)', 'error');
			return;
		}
		if (b.accent_strong_color.trim() && !HEX_RE.test(b.accent_strong_color.trim())) {
			toast('Strong accent color must be a 3- or 6-digit hex', 'error');
			return;
		}
		for (const [val, label] of [
			[b.logo_url, 'Logo URL'],
			[b.support_url, 'Support URL'],
			[b.legal_url, 'Legal URL']
		] as const) {
			if (val.trim() && !URL_RE.test(val.trim())) {
				toast(`${label} must be an http(s) URL`, 'error');
				return;
			}
		}
		saving = true;
		try {
			await updateChildBranding(editingChild.id, {
				product_name: b.product_name.trim(),
				logo_url: b.logo_url.trim(),
				accent_color: b.accent_color.trim(),
				accent_strong_color: b.accent_strong_color.trim(),
				support_url: b.support_url.trim(),
				legal_url: b.legal_url.trim()
			});
			toast(`Branding saved for ${editingChild.name}`, 'success');
			closeEdit();
			// Refresh the list so the brand-name column reflects the change.
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to save branding', 'error');
		} finally {
			saving = false;
		}
	}
</script>

<PageHeader title="Partner Admin">
	<p class="page-hint">
		Manage the branded child tenants this workspace administers as a partner / reseller. Each
		child is a separate tenant whose white-label branding (product name, logo, accent colors) you
		can view and push from here. You can only see and affect tenants linked to this workspace.
	</p>

	{#if loading}
		<p class="state" data-testid="partner-loading">Loading…</p>
	{:else if error}
		<div class="state error" data-testid="partner-error" role="alert">
			<p>{error}</p>
			<button type="button" class="btn-cancel" onclick={load}>Retry</button>
		</div>
	{:else if overview && !overview.is_partner}
		<div class="state" data-testid="partner-empty">
			<p>
				This workspace does not administer any child tenants yet. When a tenant is linked to this
				workspace as a partner, its children appear here.
			</p>
		</div>
	{:else if overview}
		<DataTable columns={COLUMNS} isEmpty={overview.children.length === 0} empty="No child tenants.">
			{#snippet body()}
				{#each children as child (child.id)}
					<tr
						class="clickable"
						onclick={(e) => {
							if (isRowOpenClick(e)) openEdit(child);
						}}
					>
						<td>
							<RowLink
								onclick={() => openEdit(child)}
								ariaLabel={`Edit branding for ${child.name}`}
							>
								{child.name}
							</RowLink>
						</td>
						<td class="mono">{child.slug}</td>
						<td>{child.plan}</td>
						<td>{child.product_name || '—'}</td>
					</tr>
				{/each}
			{/snippet}
		</DataTable>
	{/if}
</PageHeader>

<!-- Edit child branding modal -->
<Modal
	open={editingChild !== null}
	ariaLabel="Edit child branding"
	width="md"
	onclose={closeEdit}
>
	{#if editingChild}
		<h2>Branding — {editingChild.name}</h2>
		{#if brandLoading}
			<p class="state" data-testid="brand-loading">Loading branding…</p>
		{:else if brandError}
			<div class="state error" role="alert">
				<p>{brandError}</p>
				<button type="button" class="btn-cancel" onclick={() => openEdit(editingChild!)}>Retry</button>
			</div>
		{:else if brand}
			<form
				onsubmit={(e) => {
					e.preventDefault();
					saveBrand();
				}}
			>
				<label>
					<span>Product name</span>
					<input
						type="text"
						bind:value={brand.product_name}
						maxlength="120"
						placeholder="Accounts Payable"
					/>
				</label>
				<label>
					<span>Logo URL</span>
					<input type="url" bind:value={brand.logo_url} placeholder="https://cdn.example.com/logo.png" />
				</label>
				<div class="color-row">
					<label>
						<span>Accent color</span>
						<input type="text" bind:value={brand.accent_color} placeholder="#638cff" />
					</label>
					<label>
						<span>Strong accent</span>
						<input type="text" bind:value={brand.accent_strong_color} placeholder="#3f5fd6" />
					</label>
				</div>
				<label>
					<span>Support URL</span>
					<input type="url" bind:value={brand.support_url} placeholder="https://help.example.com" />
				</label>
				<label>
					<span>Legal URL</span>
					<input type="url" bind:value={brand.legal_url} placeholder="https://example.com/legal" />
				</label>
				<div class="modal-footer">
					<button type="button" class="btn-cancel" onclick={closeEdit}>Cancel</button>
					<button type="submit" class="btn-primary" disabled={saving}>
						{saving ? 'Saving…' : 'Save branding'}
					</button>
				</div>
			</form>
		{/if}
	{/if}
</Modal>

<style>
	.page-hint {
		margin: 0;
		color: var(--text-muted);
		font-size: 0.85rem;
		max-width: 720px;
	}

	.state {
		color: var(--text-muted);
		padding: 0.75rem 0;
	}

	.state.error {
		color: #f06464;
	}

	.mono {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.85rem;
	}

	.color-row {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.75rem;
	}
</style>
