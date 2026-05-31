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

<div class="toast-container">
	{#each toasts as t (t.id)}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="toast {t.type}" onclick={() => dismiss(t.id)} onkeydown={() => {}}>
			<span class="toast-icon">
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
		</div>
	{/each}
</div>

<style>
	.toast-container {
		position: fixed;
		bottom: 16px;
		right: 16px;
		z-index: 9999;
		display: flex;
		flex-direction: column-reverse;
		gap: 8px;
		pointer-events: none;
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
		cursor: pointer;
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
		border: 1px solid #e04040;
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
	}
</style>
