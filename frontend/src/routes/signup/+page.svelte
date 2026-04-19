<script lang="ts">
	import { api } from '$lib/api';
	import { onMount } from 'svelte';

	interface PublicConfig {
		hcaptcha_sitekey: string;
		tenant_url_template: string;
	}

	interface StartResponse {
		status: string;
		message: string;
	}

	interface SlugCheckResponse {
		slug: string;
		available: boolean;
		reason?: string | null;
	}

	let companyName = $state('');
	let slug = $state('');
	let adminName = $state('');
	let adminEmail = $state('');
	let captchaToken = $state<string | null>(null);
	let captchaSitekey = $state<string>('');
	// Host/port suffix shown after the slug input, derived from the backend
	// tenant_url_template (e.g. "http://{slug}.localhost:7777" → ".localhost:7777").
	let tenantUrlSuffix = $state<string>('.localhost:7777');
	let tenantExampleHost = $state<string>('your-slug.localhost:7777');

	let slugStatus = $state<'idle' | 'checking' | 'ok' | 'bad'>('idle');
	let slugError = $state<string | null>(null);

	let submitting = $state(false);
	let error = $state('');
	let successMessage = $state<string | null>(null);

	let slugCheckTimer: ReturnType<typeof setTimeout> | null = null;

	onMount(async () => {
		try {
			const cfg = await api.get<PublicConfig>('/api/public-config');
			captchaSitekey = cfg.hcaptcha_sitekey || '';
			if (captchaSitekey) loadHCaptcha();
			if (cfg.tenant_url_template) {
				// Strip protocol + split on {slug} to get just the host-suffix portion.
				const noProtocol = cfg.tenant_url_template.replace(/^https?:\/\//, '');
				const parts = noProtocol.split('{slug}');
				if (parts.length === 2) {
					tenantUrlSuffix = parts[1];
					tenantExampleHost = `your-slug${parts[1]}`;
				}
			}
		} catch {
			// Non-fatal — backend may be unreachable; form will surface errors on submit.
		}
	});

	function loadHCaptcha() {
		if (document.querySelector('script[data-hcaptcha]')) return;
		const s = document.createElement('script');
		s.src = 'https://hcaptcha.com/1/api.js';
		s.async = true;
		s.defer = true;
		s.setAttribute('data-hcaptcha', 'true');
		(window as any).hcaptchaCallback = (token: string) => {
			captchaToken = token;
		};
		(window as any).hcaptchaExpired = () => {
			captchaToken = null;
		};
		document.head.appendChild(s);
	}

	function onSlugInput() {
		slug = slug.toLowerCase().replace(/[^a-z0-9-]/g, '');
		slugError = null;
		slugStatus = 'idle';
		if (slugCheckTimer) clearTimeout(slugCheckTimer);
		if (slug.length < 3) return;
		slugStatus = 'checking';
		slugCheckTimer = setTimeout(checkSlug, 400);
	}

	async function checkSlug() {
		try {
			const res = await api.get<SlugCheckResponse>(
				`/api/signup/slug-check?slug=${encodeURIComponent(slug)}`
			);
			if (res.available) {
				slugStatus = 'ok';
				slugError = null;
			} else {
				slugStatus = 'bad';
				slugError = res.reason || 'Unavailable.';
			}
		} catch {
			slugStatus = 'idle';
		}
	}

	async function onSubmit(e: Event) {
		e.preventDefault();
		error = '';
		submitting = true;
		try {
			if (captchaSitekey && !captchaToken) {
				throw new Error('Please complete the captcha.');
			}
			const res = await api.post<StartResponse>('/api/signup/start', {
				company_name: companyName,
				slug,
				admin_name: adminName,
				admin_email: adminEmail,
				captcha_token: captchaToken,
			});
			successMessage = res.message;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Signup failed.';
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Sign up — Better AP</title>
</svelte:head>

<div class="page">
	{#if successMessage}
		<div class="card success">
			<h1>Check your email</h1>
			<p>{successMessage}</p>
			<p class="sub">Didn't receive it? Check spam, or <a href="/signup">try again</a>.</p>
		</div>
	{:else}
		<form class="card" onsubmit={onSubmit}>
			<h1>Create your workspace</h1>
			<p class="sub">Get your own AP workspace in under a minute.</p>

			{#if error}
				<div class="error">{error}</div>
			{/if}

			<label>
				<span>Company name</span>
				<input bind:value={companyName} required maxlength="255" autocomplete="organization" />
			</label>

			<label>
				<span>Workspace URL</span>
				<div class="slug-row">
					<input
						class="slug-input"
						bind:value={slug}
						oninput={onSlugInput}
						required
						minlength="3"
						maxlength="30"
						placeholder="acme"
						autocapitalize="off"
						autocorrect="off"
						spellcheck="false"
					/>
					<span class="slug-suffix">{tenantUrlSuffix}</span>
				</div>
				{#if slugStatus === 'checking'}
					<small class="hint">Checking availability…</small>
				{:else if slugStatus === 'ok'}
					<small class="hint ok">Available</small>
				{:else if slugStatus === 'bad'}
					<small class="hint bad">{slugError}</small>
				{:else}
					<small class="hint">3–30 lowercase letters, digits, hyphens. Starts with a letter.</small>
				{/if}
			</label>

			<label>
				<span>Your name</span>
				<input bind:value={adminName} required maxlength="255" autocomplete="name" />
			</label>

			<label>
				<span>Email</span>
				<input type="email" bind:value={adminEmail} required maxlength="320" autocomplete="email" />
			</label>

			{#if captchaSitekey}
				<div
					class="h-captcha"
					data-sitekey={captchaSitekey}
					data-callback="hcaptchaCallback"
					data-expired-callback="hcaptchaExpired"
				></div>
			{/if}

			<button type="submit" disabled={submitting || slugStatus === 'bad'}>
				{submitting ? 'Sending verification…' : 'Send verification email'}
			</button>

			<p class="footer">
				Already have a workspace? Visit <code>{tenantExampleHost}</code> to sign in.
			</p>
		</form>
	{/if}
</div>

<style>
	.page {
		min-height: 100vh;
		display: grid;
		place-items: center;
		background: var(--bg);
		padding: 40px 20px;
	}
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		padding: 40px 36px;
		width: min(480px, 92vw);
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	h1 {
		margin: 0;
		font-size: 1.3rem;
		font-weight: 700;
		color: var(--text);
	}
	.sub {
		margin: -8px 0 8px;
		font-size: 0.88rem;
		color: var(--text-muted);
	}
	.error {
		background: rgba(224, 64, 64, 0.1);
		border: 1px solid rgba(224, 64, 64, 0.3);
		color: #e04040;
		padding: 10px 14px;
		border-radius: 4px;
		font-size: 0.85rem;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	label span {
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
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
		box-shadow: 0 0 0 2px rgba(99, 140, 255, 0.15);
	}
	.slug-row {
		display: flex;
		align-items: center;
		border: 1px solid var(--border);
		border-radius: 4px;
		background: var(--bg);
	}
	.slug-input {
		border: none;
		background: transparent;
		flex: 1;
	}
	.slug-input:focus {
		box-shadow: none;
	}
	.slug-suffix {
		padding: 0 12px;
		color: var(--text-muted);
		font-size: 0.85rem;
	}
	.hint {
		font-size: 0.75rem;
		color: var(--text-muted);
	}
	.hint.ok {
		color: #2e9960;
	}
	.hint.bad {
		color: #e04040;
	}
	button {
		margin-top: 8px;
		padding: 10px;
		border-radius: 4px;
		border: none;
		background: var(--accent);
		color: #fff;
		font-size: 0.9rem;
		font-weight: 500;
		cursor: pointer;
		font-family: inherit;
	}
	button:hover:not(:disabled) {
		opacity: 0.9;
	}
	button:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.footer {
		margin: 16px 0 0;
		font-size: 0.8rem;
		color: var(--text-muted);
		text-align: center;
	}
	.footer code {
		background: var(--bg);
		padding: 2px 6px;
		border-radius: 3px;
	}
	.success h1 {
		color: var(--accent);
	}
</style>
