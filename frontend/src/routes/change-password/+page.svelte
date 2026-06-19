<script lang="ts">
	import { api } from '$lib/api';
	import { auth } from '$lib/stores/auth.svelte';
	import { goto } from '$app/navigation';

	interface UserResponse {
		id: string;
		email: string;
		full_name: string;
		must_change_password: boolean;
		roles: string[];
	}

	let currentPassword = $state('');
	let newPassword = $state('');
	let confirmPassword = $state('');
	let error = $state('');
	let submitting = $state(false);

	let strengthHints = $derived.by(() => {
		const hints: Array<{ label: string; ok: boolean }> = [];
		hints.push({ label: 'at least 12 characters', ok: newPassword.length >= 12 });
		hints.push({ label: 'an uppercase letter', ok: /[A-Z]/.test(newPassword) });
		hints.push({ label: 'a lowercase letter', ok: /[a-z]/.test(newPassword) });
		hints.push({ label: 'a digit', ok: /[0-9]/.test(newPassword) });
		return hints;
	});

	let allStrong = $derived(strengthHints.every((h) => h.ok));

	async function onSubmit(e: Event) {
		e.preventDefault();
		error = '';
		if (newPassword !== confirmPassword) {
			error = 'Passwords do not match.';
			return;
		}
		if (!allStrong) {
			error = 'Password does not meet the complexity requirements.';
			return;
		}
		submitting = true;
		try {
			await api.post<UserResponse>('/api/auth/change-password', {
				current_password: currentPassword,
				new_password: newPassword,
			});
			await auth.fetchUser();
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : 'Password change failed.';
		} finally {
			submitting = false;
		}
	}

	async function onLogout() {
		await auth.logout();
	}
</script>

<svelte:head>
	<title>Change password — Better AP</title>
</svelte:head>

<div class="page">
	<form class="card" onsubmit={onSubmit}>
		<h1>Set a new password</h1>
		<p class="sub">
			{#if auth.user?.must_change_password}
				You signed in with a temporary password. Choose a new one to continue.
			{:else}
				Update your password.
			{/if}
		</p>

		<div role="alert" aria-live="assertive">
			{#if error}
				<div class="error">{error}</div>
			{/if}
		</div>

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
				autocomplete="new-password"
				minlength="12"
			/>
		</label>

		<ul class="strength">
			{#each strengthHints as hint}
				<li class:ok={hint.ok}>{hint.ok ? '✓' : '·'} {hint.label}</li>
			{/each}
		</ul>

		<label>
			<span>Confirm new password</span>
			<input
				type="password"
				bind:value={confirmPassword}
				required
				autocomplete="new-password"
				minlength="12"
			/>
		</label>

		<button type="submit" disabled={submitting || !allStrong || newPassword !== confirmPassword}>
			{submitting ? 'Saving…' : 'Change password'}
		</button>

		<button type="button" class="secondary" onclick={onLogout}>Sign out</button>
	</form>
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
		width: min(440px, 92vw);
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	h1 {
		margin: 0;
		font-size: 1.25rem;
		font-weight: 700;
		color: var(--text);
	}
	.sub {
		margin: -4px 0 4px;
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
	.strength {
		margin: -4px 0 4px;
		padding: 0 0 0 4px;
		list-style: none;
		font-size: 0.78rem;
		color: var(--text-muted);
	}
	.strength li.ok {
		color: #2e9960;
	}
	button {
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
		opacity: 0.5;
		cursor: not-allowed;
	}
	button.secondary {
		background: transparent;
		border: 1px solid var(--border);
		color: var(--text-muted);
	}
</style>
