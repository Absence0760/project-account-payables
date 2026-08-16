<script lang="ts" module>
	export type ToastType = 'success' | 'error' | 'info' | 'warning';

	export interface ToastMessage {
		id: number;
		type: ToastType;
		text: string;
	}

	let toasts = $state<ToastMessage[]>([]);
	let nextId = 0;

	export function toast(text: string, type: ToastType = 'info', duration = 5000) {
		const id = nextId++;
		toasts.push({ id, type, text });
		setTimeout(() => dismiss(id), duration);
	}

	export function dismiss(id: number) {
		toasts = toasts.filter((t) => t.id !== id);
	}
</script>

<script lang="ts">
</script>

<!--
	WCAG 4.1.3 Status Messages: toasts are announced without moving focus.
	Errors go in an assertive region (interrupt); everything else in a polite
	region. Two stacked containers keep the live-region semantics clean — a
	single region toggling aria-live is unreliable across screen readers.
	Each toast carries a real <button> dismiss (keyboard-operable), not a div.
-->
<div class="toast-region" role="region" aria-label="Notifications">
	<div class="toast-container" aria-live="assertive">
		{#each toasts.filter((t) => t.type === 'error') as t (t.id)}
			{@render toastItem(t)}
		{/each}
	</div>
	<div class="toast-container" aria-live="polite">
		{#each toasts.filter((t) => t.type !== 'error') as t (t.id)}
			{@render toastItem(t)}
		{/each}
	</div>
</div>

{#snippet toastItem(t: ToastMessage)}
	<div class="toast {t.type}">
		<span class="toast-icon" aria-hidden="true">
			{#if t.type === 'success'}
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>
			{:else if t.type === 'error'}
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
			{:else if t.type === 'warning'}
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
			{:else}
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
			{/if}
		</span>
		<span class="toast-text">{t.text}</span>
		<button
			type="button"
			class="toast-dismiss"
			aria-label="Dismiss notification"
			onclick={() => dismiss(t.id)}
		>
			<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
		</button>
	</div>
{/snippet}

<style>
	/* Both live containers stack in the same fixed corner; the region wrapper
	   just groups them for the landmark. */
	.toast-region {
		position: fixed;
		bottom: 16px;
		right: 16px;
		z-index: 9999;
		display: flex;
		flex-direction: column;
		gap: 8px;
		pointer-events: none;
	}

	.toast-container {
		display: flex;
		flex-direction: column-reverse;
		gap: 8px;
	}

	.toast {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 18px;
		border-radius: 8px;
		font-size: 0.88rem;
		font-family: inherit;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
		pointer-events: auto;
		animation: slide-in 0.25s ease;
		max-width: 420px;
	}

	@keyframes slide-in {
		from {
			transform: translateY(20px);
			opacity: 0;
		}
		to {
			transform: translateY(0);
			opacity: 1;
		}
	}

	.toast.success {
		background: #132a1e;
		border: 1px solid #1fa86a;
		color: #4ade80;
	}

	.toast.error {
		background: #2a1313;
		border: 1px solid var(--danger);
		color: #f87171;
	}

	.toast.warning {
		background: #2a2213;
		border: 1px solid #d4940a;
		color: #fbbf24;
	}

	.toast.info {
		background: #131a2a;
		border: 1px solid #638cff;
		color: #93b4ff;
	}

	.toast-icon {
		flex-shrink: 0;
		display: grid;
		place-items: center;
	}

	.toast-text {
		line-height: 1.4;
		flex: 1;
	}

	.toast-dismiss {
		flex-shrink: 0;
		display: grid;
		place-items: center;
		width: 22px;
		height: 22px;
		padding: 0;
		border: none;
		border-radius: 5px;
		background: transparent;
		color: inherit;
		opacity: 0.75;
		cursor: pointer;
		transition: opacity 0.12s, background 0.12s;
	}

	.toast-dismiss:hover {
		opacity: 1;
		background: rgba(255, 255, 255, 0.1);
	}

	/* WCAG 2.3.3 Animation from Interactions — kill the slide-in when the user
	   asks for reduced motion (also covered globally in app.css, belt + braces). */
	@media (prefers-reduced-motion: reduce) {
		.toast {
			animation: none;
		}
	}
</style>
