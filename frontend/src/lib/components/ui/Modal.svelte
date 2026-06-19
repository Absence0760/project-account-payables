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

	// The dialog container; receives focus on open and bounds the focus trap.
	let dialogEl = $state<HTMLDivElement | null>(null);
	// The element focused before the dialog opened, so we can restore focus on
	// close (WCAG 2.4.3 Focus Order — focus returns to the trigger).
	let prevFocused: HTMLElement | null = null;

	function onBackdrop(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	// Visible, focusable descendants of the dialog, in DOM order.
	function focusable(): HTMLElement[] {
		if (!dialogEl) return [];
		const sel =
			'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
		return Array.from(dialogEl.querySelectorAll<HTMLElement>(sel)).filter(
			(el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement
		);
	}

	// WCAG 2.4.3: on open, move focus into the dialog (first focusable, else the
	// dialog container itself); remember where focus was so we can restore it.
	$effect(() => {
		if (!open) return;
		prevFocused = (document.activeElement as HTMLElement) ?? null;
		// Defer to the next microtask so the dialog content is in the DOM.
		queueMicrotask(() => {
			const items = focusable();
			(items[0] ?? dialogEl)?.focus();
		});
		// On close (effect re-run / unmount) restore focus to the trigger.
		return () => prevFocused?.focus?.();
	});

	function onKey(e: KeyboardEvent) {
		if (!open) return;
		if (e.key === 'Escape') {
			onclose();
			return;
		}
		// WCAG 2.1.2 No Keyboard Trap: keep Tab/Shift+Tab cycling within the
		// dialog (wrap-around) rather than escaping to the page behind it.
		if (e.key === 'Tab') {
			const items = focusable();
			if (items.length === 0) {
				// Nothing tabbable but the dialog itself — hold focus on it.
				e.preventDefault();
				dialogEl?.focus();
				return;
			}
			const first = items[0];
			const last = items[items.length - 1];
			const active = document.activeElement as HTMLElement;
			if (e.shiftKey && (active === first || !dialogEl?.contains(active))) {
				e.preventDefault();
				last.focus();
			} else if (!e.shiftKey && active === last) {
				e.preventDefault();
				first.focus();
			}
		}
	}
</script>

<svelte:window onkeydown={onKey} />

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
	<div class="backdrop" onclick={onBackdrop}>
		<div
			bind:this={dialogEl}
			class="modal"
			class:modal-sm={width === 'sm'}
			class:modal-lg={width === 'lg'}
			role="dialog"
			aria-modal="true"
			aria-label={ariaLabel}
			tabindex="-1"
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
