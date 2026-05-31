<script lang="ts">
	import type { Snippet } from 'svelte';

	type Props = {
		/** Controls visibility. Defaults to true so `{#if open}<Modal>` reads naturally. */
		open?: boolean;
		/** Maps to the dialog's `aria-label` — the e2e suite selects modals by this. */
		ariaLabel: string;
		/** Convenience title rendered as `<h2>`. Omit and use the `header` snippet for
		 *  custom heading markup (e.g. the invoice modal's `.line-items-title`). */
		title?: string;
		/** Width preset: sm = 440px, md = 480px (default), lg = 820px. */
		width?: 'sm' | 'md' | 'lg';
		onclose: () => void;
		/** Replaces the default `<h2>{title}</h2>` heading. */
		header?: Snippet;
		/** Body content. Pages keep their own `<form>` + `.modal-footer` here so
		 *  form-submit semantics stay intact. */
		children: Snippet;
	};

	let { open = true, ariaLabel, title, width = 'md', onclose, header, children }: Props = $props();

	function onBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	function onKey(e: KeyboardEvent) {
		if (open && e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={onKey} />

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={onBackdrop}>
		<div
			class="modal"
			class:modal-sm={width === 'sm'}
			class:modal-lg={width === 'lg'}
			role="dialog"
			aria-modal="true"
			aria-label={ariaLabel}
		>
			{#if header}
				{@render header()}
			{:else if title}
				<h2>{title}</h2>
			{/if}
			{@render children()}
		</div>
	</div>
{/if}
