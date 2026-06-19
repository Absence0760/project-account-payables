<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { notificationStore } from '$lib/stores/notifications.svelte';
	import {
		EVENT_ORDER,
		EVENT_LABELS,
		type NotificationEventType,
		type NotificationPrefs,
	} from '$lib/types/notification';

	interface EnrollResponse {
		secret: string;
		provisioning_uri: string;
		qr_code_data_url: string;
	}

	// Notification preferences
	let prefsLoaded = $state(false);
	let savingPrefs = $state(false);

	$effect(() => {
		if (!prefsLoaded) {
			void loadPrefs();
		}
	});

	async function loadPrefs() {
		try {
			await notificationStore.fetchPrefs();
		} catch {
			/* non-critical — keep the page usable */
		} finally {
			prefsLoaded = true;
		}
	}

	async function togglePref(event: NotificationEventType, channel: 'email' | 'in_app') {
		const prefs = notificationStore.prefs;
		if (!prefs) return;
		const current = prefs[event];
		const next: NotificationPrefs[NotificationEventType] = {
			...current,
			[channel]: !current[channel],
		};
		savingPrefs = true;
		try {
			await notificationStore.updatePrefs({ [event]: next });
		} catch {
			toast('Failed to update notification preferences', 'error');
		} finally {
			savingPrefs = false;
		}
	}

	let enrollment = $state<EnrollResponse | null>(null);
	let verifyCode = $state('');
	let disablePassword = $state('');
	let loading = $state(false);

	// Account editing
	let fullName = $state('');
	let savingProfile = $state(false);
	let currentPassword = $state('');
	let newPassword = $state('');
	let confirmPassword = $state('');
	let savingPassword = $state(false);

	$effect(() => {
		// Sync local edit field when the user loads / changes
		if (auth.user && !fullName) {
			fullName = auth.user.full_name;
		}
	});

	async function saveProfile() {
		if (!fullName.trim() || fullName === auth.user?.full_name) return;
		savingProfile = true;
		try {
			await api.patch('/api/auth/me', { full_name: fullName.trim() });
			await auth.fetchUser();
			toast('Profile updated', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to update profile', 'error');
		} finally {
			savingProfile = false;
		}
	}

	async function changePassword() {
		if (!currentPassword || !newPassword) return;
		if (newPassword !== confirmPassword) {
			toast('Passwords do not match', 'error');
			return;
		}
		savingPassword = true;
		try {
			await api.patch('/api/auth/me', {
				current_password: currentPassword,
				password: newPassword,
			});
			currentPassword = '';
			newPassword = '';
			confirmPassword = '';
			toast('Password updated', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to update password', 'error');
		} finally {
			savingPassword = false;
		}
	}

	async function startEnroll() {
		loading = true;
		try {
			enrollment = await api.post<EnrollResponse>('/api/auth/mfa/enroll', {});
			verifyCode = '';
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to start enrollment', 'error');
		} finally {
			loading = false;
		}
	}

	async function verifyEnroll() {
		if (!enrollment) return;
		loading = true;
		try {
			await api.post('/api/auth/mfa/enroll/verify', { code: verifyCode });
			await auth.fetchUser();
			enrollment = null;
			verifyCode = '';
			toast('Two-factor authentication enabled', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Verification failed', 'error');
		} finally {
			loading = false;
		}
	}

	async function disable() {
		loading = true;
		try {
			await api.post('/api/auth/mfa/disable', { password: disablePassword });
			await auth.fetchUser();
			disablePassword = '';
			toast('Two-factor authentication disabled', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to disable', 'error');
		} finally {
			loading = false;
		}
	}

	function cancelEnroll() {
		enrollment = null;
		verifyCode = '';
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Profile & Security</h1>
	</header>

	<div class="sections">
		<section class="card">
			<h2>Account</h2>
			<form
				onsubmit={(e) => {
					e.preventDefault();
					saveProfile();
				}}
			>
				<label>
					<span>Full name</span>
					<input type="text" bind:value={fullName} required autocomplete="name" />
				</label>
				<dl class="readonly">
					<dt>Email</dt>
					<dd>{auth.user?.email ?? '—'}</dd>
					<dt>Roles</dt>
					<dd>{auth.user?.roles.join(', ') || '—'}</dd>
				</dl>
				<div class="actions">
					<button
						type="submit"
						disabled={savingProfile || !fullName.trim() || fullName === auth.user?.full_name}
					>
						{savingProfile ? 'Saving...' : 'Save'}
					</button>
				</div>
			</form>
		</section>

		<section class="card">
			<h2>Password</h2>
			<p class="hint">
				Use a strong password unique to this account. After saving you'll stay
				signed in on this device but other sessions remain valid until they
				expire.
			</p>
			<form
				onsubmit={(e) => {
					e.preventDefault();
					changePassword();
				}}
			>
				<label>
					<span>Current password</span>
					<input
						type="password"
						bind:value={currentPassword}
						required
						autocomplete="current-password"
					/>
				</label>
				<label>
					<span>New password</span>
					<input
						type="password"
						bind:value={newPassword}
						required
						minlength="6"
						autocomplete="new-password"
					/>
				</label>
				<label>
					<span>Confirm new password</span>
					<input
						type="password"
						bind:value={confirmPassword}
						required
						minlength="6"
						autocomplete="new-password"
					/>
				</label>
				<div class="actions">
					<button
						type="submit"
						disabled={savingPassword || !currentPassword || !newPassword || newPassword !== confirmPassword}
					>
						{savingPassword ? 'Saving...' : 'Change password'}
					</button>
				</div>
			</form>
		</section>

		<section class="card">
			<h2>Two-factor authentication</h2>
			<p class="hint">
				Adds a second step at sign-in using an authenticator app (Google
				Authenticator, 1Password, Authy, etc.). If you can't access your
				authenticator, a one-time code can be emailed to your account.
			</p>

			{#if auth.user?.mfa_enabled}
				<div class="status enabled">
					<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
					Enabled
				</div>
				{#if auth.user?.mfa_required_by_org}
					<p class="hint">
						Your organization requires MFA, so disabling is not available.
					</p>
				{:else}
					<form
						onsubmit={(e) => {
							e.preventDefault();
							disable();
						}}
					>
						<label>
							<span>Enter your password to disable MFA</span>
							<input
								type="password"
								bind:value={disablePassword}
								required
								autocomplete="current-password"
							/>
						</label>
						<button type="submit" class="danger" disabled={loading || !disablePassword}>
							{loading ? 'Disabling...' : 'Disable two-factor'}
						</button>
					</form>
				{/if}
			{:else if enrollment}
				<div class="enroll">
					<p>
						<strong>Step 1.</strong> Scan this QR code with your authenticator app.
					</p>
					<img src={enrollment.qr_code_data_url} alt="MFA QR code" class="qr" />
					<details>
						<summary>Can't scan? Enter the secret manually</summary>
						<code class="secret">{enrollment.secret}</code>
					</details>
					<form
						onsubmit={(e) => {
							e.preventDefault();
							verifyEnroll();
						}}
					>
						<label>
							<span><strong>Step 2.</strong> Enter the 6-digit code your app shows</span>
							<input
								type="text"
								inputmode="numeric"
								pattern="[0-9]*"
								bind:value={verifyCode}
								maxlength="8"
								autocomplete="one-time-code"
								required
							/>
						</label>
						<div class="actions">
							<button type="button" class="secondary" onclick={cancelEnroll}>Cancel</button>
							<button type="submit" disabled={loading || verifyCode.length < 6}>
								{loading ? 'Verifying...' : 'Verify and enable'}
							</button>
						</div>
					</form>
				</div>
			{:else}
				<div class="status disabled">Not configured</div>
				{#if auth.user?.mfa_required_by_org}
					<p class="warn">
						Your organization requires MFA — please enroll now.
					</p>
				{/if}
				<button onclick={startEnroll} disabled={loading}>
					{loading ? 'Loading...' : 'Set up two-factor'}
				</button>
			{/if}
		</section>

		<section class="card">
			<h2>Notifications</h2>
			<p class="hint">
				Choose how you're notified about invoices assigned to you and the
				invoices you've uploaded. In-app notifications appear in the
				notification center; email is sent to {auth.user?.email ?? 'your address'}.
			</p>

			{#if !prefsLoaded}
				<p class="hint">Loading…</p>
			{:else if notificationStore.prefs}
				{@const prefs = notificationStore.prefs}
				<table class="prefs-table">
					<thead>
						<tr>
							<th>Event</th>
							<th class="center">In-app</th>
							<th class="center">Email</th>
						</tr>
					</thead>
					<tbody>
						{#each EVENT_ORDER as event (event)}
							<tr>
								<td>{EVENT_LABELS[event]}</td>
								<td class="center">
									<input
										type="checkbox"
										checked={prefs[event].in_app}
										disabled={savingPrefs}
										onchange={() => togglePref(event, 'in_app')}
										aria-label={`In-app notifications for ${EVENT_LABELS[event]}`}
									/>
								</td>
								<td class="center">
									<input
										type="checkbox"
										checked={prefs[event].email}
										disabled={savingPrefs}
										onchange={() => togglePref(event, 'email')}
										aria-label={`Email notifications for ${EVENT_LABELS[event]}`}
									/>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="warn">Could not load notification preferences.</p>
			{/if}
		</section>
	</div>
</div>

<style>
	.workspace {
		max-width: 1800px;
		margin: 0 auto;
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
		min-height: 100vh;
	}

	.toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.sections {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	h1 {
		margin: 0;
		font-size: 1.3rem;
		font-weight: 700;
	}

	h2 {
		margin: 0 0 12px;
		font-size: 1rem;
		font-weight: 600;
	}

	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 24px;
	}

	.hint {
		color: var(--text-muted);
		font-size: 0.85rem;
		margin: 0 0 16px;
	}

	.warn {
		color: #c47b00;
		font-size: 0.85rem;
		background: rgba(255, 165, 0, 0.08);
		border: 1px solid rgba(255, 165, 0, 0.3);
		padding: 8px 12px;
		border-radius: 4px;
		margin: 0 0 12px;
	}

	dl {
		display: grid;
		grid-template-columns: 120px 1fr;
		gap: 8px 16px;
		margin: 0;
	}

	dl.readonly {
		margin-top: 4px;
		padding: 12px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
	}

	dt {
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	dd {
		margin: 0;
		font-size: 0.9rem;
	}

	.status {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 4px 10px;
		border-radius: 4px;
		font-size: 0.82rem;
		font-weight: 500;
		margin-bottom: 12px;
	}

	.status.enabled {
		background: rgba(46, 160, 67, 0.12);
		color: #2ea043;
	}

	.status.disabled {
		background: var(--bg);
		color: var(--text-muted);
	}

	.enroll {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.qr {
		width: 200px;
		height: 200px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: #fff;
		padding: 8px;
		align-self: flex-start;
	}

	details summary {
		cursor: pointer;
		font-size: 0.85rem;
		color: var(--text-muted);
	}

	.secret {
		display: block;
		margin-top: 8px;
		padding: 8px 12px;
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		font-family: monospace;
		font-size: 0.85rem;
		word-break: break-all;
	}

	form {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin-top: 12px;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 0.85rem;
		color: var(--text-muted);
	}

	input {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 10px 12px;
		font-size: 0.9rem;
		color: var(--text);
		font-family: inherit;
	}

	input:focus {
		outline: none;
		border-color: var(--accent);
	}

	.actions {
		display: flex;
		gap: 8px;
	}

	button {
		padding: 9px 16px;
		border-radius: 4px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.88rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}

	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	button.secondary {
		background: transparent;
		border: 1px solid var(--border);
		color: var(--text);
	}

	button.danger {
		background: #e04040;
	}

	.prefs-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.88rem;
	}

	.prefs-table th {
		text-align: left;
		padding: 8px 10px;
		color: var(--text-muted);
		font-size: 0.78rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		border-bottom: 1px solid var(--border);
	}

	.prefs-table td {
		padding: 10px;
		border-bottom: 1px solid var(--border);
	}

	.prefs-table .center {
		text-align: center;
		width: 90px;
	}

	.prefs-table input[type='checkbox'] {
		width: auto;
		cursor: pointer;
	}
</style>
