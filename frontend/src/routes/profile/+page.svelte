<script lang="ts">
	import { untrack } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/components/ui/Toast.svelte';
	import { SUPPORTED_LOCALES, LOCALE_LABELS, type Locale } from '$lib/i18n/locale';
	import { currentLocale, setLocale, m } from '$lib/i18n/store.svelte';
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
		// Seed the local edit field once the user record lands (and again if the
		// account itself changes). `fullName` is read through `untrack`: a tracked
		// read would make this effect depend on the very state it writes, so
		// backspacing the input to empty re-fired it and instantly re-filled the
		// field with the stored name — the user could never clear it to retype.
		// Depending on `auth.user` alone keeps the seed while leaving the field
		// entirely under the user's control once it exists.
		const u = auth.user;
		if (u && !untrack(() => fullName)) {
			fullName = u.full_name;
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
			// Adding TOTP to an account that already has a passkey is a factor
			// change, so the server wants a step-up. This card has no password
			// field (and an SSO-only account has no password anyway), so the
			// passkey the account already holds is the proof.
			const proof = hasPasskey ? await auth.passkeyStepUp('totp_enroll') : {};
			enrollment = await api.post<EnrollResponse>('/api/auth/mfa/enroll', proof);
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

	/** Turning MFA off with a passkey rather than the password — the only route
	 * open to an SSO-only account, which has no password to re-type. */
	async function disableWithPasskey() {
		await disable(await auth.passkeyStepUp('totp_disable'));
	}

	async function disable(proof: StepUpProof = { password: disablePassword }) {
		loading = true;
		try {
			await api.post('/api/auth/mfa/disable', proof);
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

	// --- Passkeys (WebAuthn) — an additional MFA factor ----------------------
	import { isWebAuthnSupported } from '$lib/webauthn';
	import type { ActiveSession, Passkey, StepUpProof } from '$lib/stores/auth.svelte';
	import { formatDate } from '$lib/utils/time';

	let passkeys = $state<Passkey[] | null>(null);
	let passkeyName = $state('');
	let passkeyPassword = $state('');
	let registeringPasskey = $state(false);
	let passkeysLoaded = $state(false);
	let passkeysError = $state(false);
	let passkeysBusy = $state(false);
	const webAuthnOk = isWebAuthnSupported();

	$effect(() => {
		if (!passkeysLoaded) {
			void loadPasskeys();
		}
	});

	async function loadPasskeys() {
		try {
			passkeys = await auth.listPasskeys();
			passkeysError = false;
		} catch {
			// Keep the list unknown (`null`), never an empty array. An empty array
			// is a *claim* — "this account has no passkeys" — and the step-up
			// decision below is built on it, so a failed fetch used to both tell
			// the user their passkeys were gone and disarm the proof the server
			// still demands, turning every Add/Remove into an opaque 400.
			passkeys = null;
			passkeysError = true;
		} finally {
			passkeysLoaded = true;
		}
	}

	/** Retry wrapper — `loadPasskeys` can't reset `passkeysLoaded` itself (the
	 * mount `$effect` keys off it and would re-fire), so the busy state carries
	 * the feedback instead. Mirrors `retrySessions` below. */
	async function retryPasskeys() {
		passkeysBusy = true;
		try {
			await loadPasskeys();
		} finally {
			passkeysBusy = false;
		}
	}

	// Adding a factor to an account that ALREADY has one is a step-up
	// operation server-side — otherwise a stolen session could bind an
	// attacker's authenticator. Mirror that here so the form asks for the
	// password only when it's actually required.
	// Fails CLOSED on an unknown list: if the fetch failed we cannot rule out a
	// live factor, and the server will demand a proof regardless — so ask for
	// one rather than submit without and collect an opaque 400.
	const needsPasskeyStepUp = $derived(
		Boolean(auth.user?.mfa_enabled) || (passkeys?.length ?? 0) > 0 || passkeysError,
	);
	// A registered passkey is itself a step-up credential. That matters most for
	// an SSO-only account: no password, no authenticator code, so without this
	// its passkey is the only thing it can be challenged on — and factor
	// management would otherwise be closed to it entirely.
	// `passkeysError` counts here too: the step-up ceremony's options come from
	// the server (`POST /auth/mfa/step-up/passkey`), not from this list, so a
	// failed list fetch is no reason to withhold the one credential an SSO-only
	// account has. If there really is no passkey the server refuses — which is
	// the honest answer, not a bypass.
	const hasPasskey = $derived(((passkeys?.length ?? 0) > 0 || passkeysError) && webAuthnOk);
	const canStepUp = $derived(!needsPasskeyStepUp || Boolean(passkeyPassword) || hasPasskey);

	/** Whichever proof the user has actually offered. A typed password wins (it
	 * needs no device interaction); otherwise fall back to the passkey prompt. */
	async function passkeyCardProof(
		operation: 'passkey_register' | 'passkey_delete',
	): Promise<StepUpProof> {
		if (!needsPasskeyStepUp) return {};
		if (passkeyPassword) return { password: passkeyPassword };
		return auth.passkeyStepUp(operation);
	}

	async function addPasskey() {
		registeringPasskey = true;
		try {
			const proof = await passkeyCardProof('passkey_register');
			await auth.registerPasskey(passkeyName.trim() || 'Passkey', proof);
			passkeyName = '';
			passkeyPassword = '';
			await loadPasskeys();
			toast('Passkey added', 'success');
		} catch (err) {
			// A user cancelling the browser prompt throws too — show a soft message.
			toast(err instanceof Error ? err.message : 'Failed to add passkey', 'error');
		} finally {
			registeringPasskey = false;
		}
	}

	async function removePasskey(id: string) {
		try {
			// Removing a passkey always needs the step-up — the passkey itself is
			// a live factor, so the backend refuses a bare-session delete.
			await auth.deletePasskey(id, await passkeyCardProof('passkey_delete'));
			passkeyPassword = '';
			await loadPasskeys();
			toast('Passkey removed', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to remove passkey', 'error');
		}
	}

	// --- Active sessions ----------------------------------------------------
	// The recovery path for a session you don't recognise. Revocation is armed
	// two-click (the same pattern as the API-key row action) rather than a
	// confirm dialog — signing a device out is reversible by signing back in,
	// but it shouldn't happen on a stray click either.

	let sessions = $state<ActiveSession[] | null>(null);
	let sessionsLoaded = $state(false);
	// A failed fetch must NOT read as "no other sessions are signed in" — on a
	// security screen that reassurance would be a lie, and the user would stop
	// looking for the session they came here to kill.
	let sessionsError = $state(false);
	let armedSessionId = $state<string | null>(null);
	let armedRevokeOthers = $state(false);
	let sessionBusy = $state(false);

	$effect(() => {
		if (!sessionsLoaded) {
			void loadSessions();
		}
	});

	async function loadSessions() {
		try {
			sessions = await auth.listSessions();
			sessionsError = false;
		} catch {
			sessions = null;
			sessionsError = true;
		} finally {
			sessionsLoaded = true;
		}
	}

	/** Retry wrapper — `loadSessions` can't reset `sessionsLoaded` itself (the
	 * mount `$effect` keys off it and would re-fire), so the busy state carries
	 * the feedback instead. */
	async function retrySessions() {
		sessionBusy = true;
		try {
			await loadSessions();
		} finally {
			sessionBusy = false;
		}
	}

	const otherSessionCount = $derived((sessions ?? []).filter((s) => !s.current).length);

	async function revokeSession(id: string) {
		sessionBusy = true;
		try {
			await auth.revokeSession(id);
			armedSessionId = null;
			await loadSessions();
			toast('Signed that device out', 'success');
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to sign that device out', 'error');
		} finally {
			sessionBusy = false;
		}
	}

	async function revokeOtherSessions() {
		sessionBusy = true;
		try {
			const revoked = await auth.revokeOtherSessions();
			armedRevokeOthers = false;
			await loadSessions();
			toast(
				revoked === 1 ? 'Signed 1 other session out' : `Signed ${revoked} other sessions out`,
				'success',
			);
		} catch (err) {
			toast(err instanceof Error ? err.message : 'Failed to sign other sessions out', 'error');
		} finally {
			sessionBusy = false;
		}
	}

	function sessionLabel(s: ActiveSession): string {
		return s.device ?? 'Unrecognised device';
	}

	function sessionDetail(s: ActiveSession): string {
		const parts = [
			`Signed in ${formatDate(s.created_at, '—', {
				month: 'short',
				day: 'numeric',
				hour: 'numeric',
				minute: '2-digit',
			})}`,
		];
		if (s.ip) parts.push(s.ip);
		if (s.method) parts.push(s.method);
		return parts.join(' · ');
	}

	// Display-language picker. `currentLocale()` reads the reactive i18n rune,
	// so this stays in sync if the locale is changed elsewhere. The choice is
	// persisted to localStorage by `setLocale` (device-scoped, not account-roamed).
	const activeLocale = $derived(currentLocale());

	async function onLocaleChange(e: Event) {
		const value = (e.currentTarget as HTMLSelectElement).value as Locale;
		await setLocale(value);
	}
</script>

<div class="workspace">
	<header class="toolbar">
		<h1>Profile & Security</h1>
	</header>

	<div class="sections">
		<section class="card">
			<h2>{m('profile.language.heading')}</h2>
			<p class="hint">{m('profile.language.hint')}</p>
			<label>
				<span>{m('profile.language.label')}</span>
				<select
					value={activeLocale}
					onchange={onLocaleChange}
					aria-label={m('profile.language.label')}
				>
					{#each SUPPORTED_LOCALES as loc (loc)}
						<option value={loc}>{LOCALE_LABELS[loc]}</option>
					{/each}
				</select>
			</label>
		</section>

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
								autocomplete="current-password"
							/>
						</label>
						<div class="actions">
							{#if hasPasskey}
								<!-- An SSO-only account has no password to type; its
								     registered passkey is the proof instead. -->
								<button
									type="button"
									class="secondary"
									disabled={loading}
									onclick={disableWithPasskey}
								>
									Confirm with a passkey
								</button>
							{/if}
							<button
								type="submit"
								class="danger"
								disabled={loading || !disablePassword}
							>
								{loading ? 'Disabling...' : 'Disable two-factor'}
							</button>
						</div>
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
			<h2>Passkeys</h2>
			<p class="hint">
				Sign in with a passkey — Touch ID, Face ID, Windows Hello, or a hardware
				security key — instead of typing a code. Passkeys are a second factor
				alongside (or in place of) an authenticator app.
			</p>

			{#if !webAuthnOk}
				<div class="status disabled">This browser doesn't support passkeys.</div>
			{:else}
				{#if needsPasskeyStepUp}
					<!-- One field for both operations: the backend requires a step-up
					     to add a factor to an account that already has one, and always
					     requires one to remove a passkey. -->
					<label>
						<span>Confirm your password to add or remove a passkey</span>
						<input
							type="password"
							bind:value={passkeyPassword}
							autocomplete="current-password"
						/>
					</label>
					{#if hasPasskey}
						<p class="hint">
							Leave this blank to confirm with one of your existing passkeys
							instead — the only option if you sign in with SSO and have no
							password.
						</p>
					{/if}
				{/if}

				{#if passkeys && passkeys.length > 0}
					<ul class="entry-list">
						{#each passkeys as pk (pk.id)}
							<li>
								<div class="entry-meta">
									<span class="entry-name">{pk.name}</span>
									{#if pk.last_used_at}
										<span class="entry-sub">
											Last used {formatDate(pk.last_used_at, '—', {
												year: 'numeric',
												month: 'numeric',
												day: 'numeric'
											})}
										</span>
									{:else}
										<span class="entry-sub">Never used</span>
									{/if}
								</div>
								<button
									type="button"
									class="danger small"
									disabled={!canStepUp}
									onclick={() => removePasskey(pk.id)}
								>
									Remove
								</button>
							</li>
						{/each}
					</ul>
				{:else if passkeysError}
					<p class="warn">
						Couldn't load your passkeys, so we can't say which ones are registered.
					</p>
					<div class="actions">
						<button
							type="button"
							class="secondary"
							disabled={passkeysBusy}
							onclick={retryPasskeys}
						>
							{passkeysBusy ? 'Retrying…' : 'Try again'}
						</button>
					</div>
				{:else if passkeysLoaded}
					<div class="status disabled">No passkeys yet</div>
				{/if}

				<form
					onsubmit={(e) => {
						e.preventDefault();
						addPasskey();
					}}
				>
					<label>
						<span>Passkey name (optional)</span>
						<input
							type="text"
							bind:value={passkeyName}
							maxlength="120"
							placeholder="e.g. MacBook Touch ID"
						/>
					</label>
					<div class="actions">
						<button
							type="submit"
							disabled={registeringPasskey || !canStepUp}
						>
							{registeringPasskey ? 'Waiting for passkey…' : 'Add a passkey'}
						</button>
					</div>
				</form>
			{/if}
		</section>

		<section class="card">
			<h2>Signed-in devices</h2>
			<p class="hint">
				Every browser or app currently signed in to your account. If you don't
				recognise one — or you signed in on a device you no longer have — sign
				it out here. It stops working immediately.
			</p>

			{#if !sessionsLoaded}
				<p class="hint">Loading…</p>
			{:else if sessionsError}
				<p class="warn">
					Couldn't load your sessions, so we can't say what's signed in right now.
				</p>
				<div class="actions">
					<button type="button" class="secondary" disabled={sessionBusy} onclick={retrySessions}>
						{sessionBusy ? 'Retrying…' : 'Try again'}
					</button>
				</div>
			{:else if sessions && sessions.length > 0}
				<ul class="entry-list">
					{#each sessions as s (s.id)}
						<li>
							<div class="entry-meta">
								<span class="entry-name">
									{sessionLabel(s)}
									{#if s.current}<span class="badge">This device</span>{/if}
								</span>
								<span class="entry-sub">{sessionDetail(s)}</span>
							</div>
							{#if !s.current}
								<button
									type="button"
									class="danger small"
									disabled={sessionBusy}
									onclick={() => {
										if (armedSessionId === s.id) {
											revokeSession(s.id);
										} else {
											armedSessionId = s.id;
										}
									}}
								>
									{armedSessionId === s.id ? 'Confirm sign out' : 'Sign out'}
								</button>
							{/if}
						</li>
					{/each}
				</ul>

				{#if otherSessionCount > 0}
					<div class="actions">
						<button
							type="button"
							class="danger"
							disabled={sessionBusy}
							onclick={() => {
								if (armedRevokeOthers) {
									revokeOtherSessions();
								} else {
									armedRevokeOthers = true;
								}
							}}
						>
							{armedRevokeOthers
								? `Confirm — sign out ${otherSessionCount} other ${otherSessionCount === 1 ? 'session' : 'sessions'}`
								: 'Sign out everywhere else'}
						</button>
					</div>
				{/if}
			{:else}
				<div class="status disabled">No other sessions are signed in.</div>
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
		color: var(--warning-on-tint);
		font-size: 0.85rem;
		background: var(--warning-tint);
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
		background: var(--success-tint);
		color: var(--success-on-tint);
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
		background: var(--accent-strong);
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

	button.small {
		padding: 5px 12px;
		font-size: 0.8rem;
	}

	.entry-list {
		list-style: none;
		margin: 0 0 16px;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	.entry-list li {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 10px 14px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--bg);
	}

	.entry-meta {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.entry-name {
		font-weight: 600;
		color: var(--text);
	}

	.entry-sub {
		font-size: 0.78rem;
		color: var(--text-muted);
	}

	.badge {
		margin-left: 8px;
		padding: 1px 8px;
		border-radius: 999px;
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--accent);
		background: color-mix(in srgb, var(--accent) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
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
