<script lang="ts">
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import DataTable from '$lib/components/ui/DataTable.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import RowLink from '$lib/components/ui/RowLink.svelte';
	import RowAction from '$lib/components/ui/RowAction.svelte';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { isRowOpenClick } from '$lib/utils/rowNav';
	import {
		getPartnerOverview,
		getChildBranding,
		updateChildBranding,
		mintLinkCode,
		attachChild,
		provisionChild,
		detachChild
	} from '$lib/api/partner';
	import type {
		ChildBranding,
		ChildTenant,
		PartnerOverview,
		ProvisionedChild
	} from '$lib/types/partner';

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
		{ label: 'Brand name' },
		{ label: '', class: 'actions-col' }
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

	// ── Link-code mint (this workspace consents to being a child) ──────────────
	let mintedCode = $state<string | null>(null);
	let mintExpiry = $state<number | null>(null);
	let minting = $state(false);

	async function mintCode() {
		minting = true;
		try {
			const res = await mintLinkCode();
			mintedCode = res.link_code;
			mintExpiry = res.expires_in_minutes;
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to mint link code', 'error');
		} finally {
			minting = false;
		}
	}

	async function copyCode() {
		if (!mintedCode) return;
		try {
			await navigator.clipboard.writeText(mintedCode);
			toast('Link code copied', 'success');
		} catch {
			toast('Copy failed — select and copy the code manually', 'error');
		}
	}

	// ── Attach a consenting child (redeem a code its admin minted) ─────────────
	let showAttach = $state(false);
	let attachCodeInput = $state('');
	let attaching = $state(false);

	function openAttach() {
		attachCodeInput = '';
		showAttach = true;
	}

	async function submitAttach() {
		const code = attachCodeInput.trim();
		if (!code) {
			toast('Paste the link code the child tenant gave you', 'error');
			return;
		}
		attaching = true;
		try {
			const child = await attachChild(code);
			toast(`Attached ${child.name}`, 'success');
			showAttach = false;
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to attach child', 'error');
		} finally {
			attaching = false;
		}
	}

	// ── Provision a brand-NEW child tenant under this partner ───────────────────
	let showProvision = $state(false);
	let provName = $state('');
	let provSlug = $state('');
	let provEmail = $state('');
	let provisioning = $state(false);
	// The temp credential is returned exactly once — held only until the result
	// dialog is dismissed, then dropped (never re-fetchable).
	let provisioned = $state<ProvisionedChild | null>(null);

	const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;
	const EMAIL_RE = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/;

	function openProvision() {
		provName = '';
		provSlug = '';
		provEmail = '';
		provisioned = null;
		showProvision = true;
	}

	async function submitProvision() {
		const name = provName.trim();
		const slug = provSlug.trim().toLowerCase();
		const email = provEmail.trim();
		if (!name) {
			toast('Enter a company name', 'error');
			return;
		}
		if (!SLUG_RE.test(slug)) {
			toast('Slug must be lowercase letters, digits, and hyphens (e.g. acme-eu)', 'error');
			return;
		}
		if (!EMAIL_RE.test(email)) {
			toast('Enter a valid admin email address', 'error');
			return;
		}
		provisioning = true;
		try {
			const child = await provisionChild({ name, slug, admin_email: email });
			provisioned = child;
			toast(`Provisioned ${child.name}`, 'success');
			// Refresh the children list so the new tenant appears immediately.
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to provision child tenant', 'error');
		} finally {
			provisioning = false;
		}
	}

	async function copyTempPassword() {
		if (!provisioned) return;
		try {
			await navigator.clipboard.writeText(provisioned.temp_password);
			toast('Temporary password copied', 'success');
		} catch {
			toast('Copy failed — select and copy the password manually', 'error');
		}
	}

	function closeProvision() {
		showProvision = false;
		provisioned = null;
	}

	// ── Detach a child (armed two-click confirm) ───────────────────────────────
	let confirmDetachId = $state<string | null>(null);
	let detaching = $state(false);

	async function handleDetach(child: ChildTenant) {
		if (confirmDetachId !== child.id) {
			confirmDetachId = child.id;
			return;
		}
		detaching = true;
		try {
			await detachChild(child.id);
			toast(`Detached ${child.name}`, 'success');
			confirmDetachId = null;
			await load();
		} catch (e) {
			toast(e instanceof Error ? e.message : 'Failed to detach child', 'error');
		} finally {
			detaching = false;
		}
	}
</script>

<!-- Un-arm a pending Detach when clicking outside any row action. -->
<svelte:window
	onclick={(e) => {
		if (confirmDetachId && !(e.target as HTMLElement)?.closest?.('.row-action')) {
			confirmDetachId = null;
		}
	}}
/>

<PageHeader title="Partner Admin">
	{#snippet actions()}
		<button
			type="button"
			class="btn-cancel"
			onclick={openProvision}
			data-testid="provision-child-btn"
		>
			+ Create child tenant
		</button>
		<button type="button" class="btn-primary" onclick={openAttach} data-testid="attach-child-btn">
			+ Attach child
		</button>
	{/snippet}

	<p class="page-hint">
		Manage the branded child tenants this workspace administers as a partner / reseller. Each
		child is a separate tenant whose white-label branding (product name, logo, accent colors) you
		can view and push from here. You can only see and affect tenants linked to this workspace.
	</p>

	<!-- Link-code panel: this workspace consents to being attached AS a child. A
	     partner then redeems the code to link us under their account. -->
	<section class="link-code-panel" data-testid="link-code-panel">
		<h2>Join a partner</h2>
		<p class="panel-hint">
			Want another workspace to manage this one as a partner / reseller? Generate a single-use
			link code and give it to them — handing over the code is how you consent. They redeem it to
			attach this workspace as their child. A code expires shortly and can be used once.
		</p>
		<div class="link-code-actions">
			<button type="button" class="btn-cancel" onclick={mintCode} disabled={minting}>
				{minting ? 'Generating…' : 'Generate link code'}
			</button>
		</div>
		{#if mintedCode}
			<div class="minted" data-testid="minted-link-code">
				<code class="code-value">{mintedCode}</code>
				<button type="button" class="btn-cancel copy-btn" onclick={copyCode}>Copy</button>
				{#if mintExpiry !== null}
					<span class="expiry">Expires in {mintExpiry} min</span>
				{/if}
			</div>
		{/if}
	</section>

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
						<td class="actions">
							<RowAction
								variant="danger"
								armed={confirmDetachId === child.id}
								disabled={detaching}
								onclick={() => handleDetach(child)}
								ariaLabel={`Detach ${child.name}`}
							>
								{confirmDetachId === child.id ? 'Confirm detach' : 'Detach'}
							</RowAction>
						</td>
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

<!-- Attach a consenting child by redeeming the code its admin minted -->
<Modal
	open={showAttach}
	ariaLabel="Attach child tenant"
	title="Attach a child tenant"
	width="sm"
	onclose={() => (showAttach = false)}
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			submitAttach();
		}}
	>
		<p class="modal-hint">
			Paste the single-use link code the child tenant's admin generated for you. They must have
			generated it from their own Partner Admin page — that's their consent to being managed here.
		</p>
		<label>
			<span>Link code <em class="required">*</em></span>
			<input
				type="text"
				bind:value={attachCodeInput}
				placeholder="paste the code…"
				data-testid="attach-code-input"
			/>
		</label>
		<div class="modal-footer">
			<button type="button" class="btn-cancel" onclick={() => (showAttach = false)}>Cancel</button>
			<button type="submit" class="btn-primary" disabled={attaching}>
				{attaching ? 'Attaching…' : 'Attach'}
			</button>
		</div>
	</form>
</Modal>

<!-- Provision a brand-new child tenant already parented to this partner -->
<Modal
	open={showProvision}
	ariaLabel="Create child tenant"
	title="Create a child tenant"
	width="sm"
	onclose={closeProvision}
>
	{#if provisioned}
		<!-- Result: the one-time temp credentials. Shown once; dropped on close. -->
		<div class="provisioned-result" data-testid="provisioned-result">
			<p class="modal-hint">
				<strong>{provisioned.name}</strong> ({provisioned.slug}) is ready. Give the new admin these
				first-login credentials — the temporary password is shown <strong>only once</strong> and can't
				be retrieved later.
			</p>
			<dl class="cred">
				<dt>Admin email</dt>
				<dd class="mono">{provisioned.admin_email}</dd>
				<dt>Temporary password</dt>
				<dd class="mono pw">
					<code class="code-value">{provisioned.temp_password}</code>
					<button type="button" class="btn-cancel copy-btn" onclick={copyTempPassword}>Copy</button>
				</dd>
			</dl>
			<p class="panel-hint">The admin will be required to change it on first login.</p>
			<div class="modal-footer">
				<button type="button" class="btn-primary" onclick={closeProvision}>Done</button>
			</div>
		</div>
	{:else}
		<form
			onsubmit={(e) => {
				e.preventDefault();
				submitProvision();
			}}
		>
			<p class="modal-hint">
				Spin up a brand-new tenant already linked under this workspace as a partner / reseller. It
				gets its own subdomain and database; you can then push its white-label branding from here.
			</p>
			<label>
				<span>Company name <em class="required">*</em></span>
				<input
					type="text"
					bind:value={provName}
					maxlength="200"
					placeholder="Acme Europe"
					data-testid="provision-name-input"
				/>
			</label>
			<label>
				<span>Slug <em class="required">*</em></span>
				<input
					type="text"
					bind:value={provSlug}
					maxlength="63"
					placeholder="acme-eu"
					data-testid="provision-slug-input"
				/>
			</label>
			<label>
				<span>Admin email <em class="required">*</em></span>
				<input
					type="email"
					bind:value={provEmail}
					placeholder="admin@acme.eu"
					data-testid="provision-email-input"
				/>
			</label>
			<div class="modal-footer">
				<button type="button" class="btn-cancel" onclick={closeProvision}>Cancel</button>
				<button type="submit" class="btn-primary" disabled={provisioning}>
					{provisioning ? 'Creating…' : 'Create tenant'}
				</button>
			</div>
		</form>
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

	.link-code-panel {
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 1rem 1.25rem;
		background: var(--surface);
	}

	.link-code-panel h2 {
		margin: 0 0 0.25rem;
		font-size: 1rem;
	}

	.panel-hint {
		margin: 0 0 0.75rem;
		color: var(--text-muted);
		font-size: 0.82rem;
		max-width: 640px;
	}

	.link-code-actions {
		display: flex;
		gap: 0.5rem;
	}

	.minted {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.75rem;
		flex-wrap: wrap;
	}

	.code-value {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.78rem;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.4rem 0.6rem;
		max-width: 100%;
		overflow-wrap: anywhere;
	}

	.expiry {
		color: var(--text-muted);
		font-size: 0.78rem;
	}

	.modal-hint {
		margin: 0 0 0.75rem;
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.cred {
		display: grid;
		grid-template-columns: max-content 1fr;
		gap: 0.4rem 0.9rem;
		align-items: center;
		margin: 0 0 0.5rem;
	}

	.cred dt {
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.cred dd {
		margin: 0;
		font-size: 0.85rem;
	}

	.cred dd.pw {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.copy-btn {
		flex: none;
	}
</style>
