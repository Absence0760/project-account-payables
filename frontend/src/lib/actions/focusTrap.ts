import type { Action } from 'svelte/action';

interface FocusTrapParams {
	/** Invoked on Escape pressed while focus is within the trapped element. */
	onEscape?: () => void;
}

const FOCUSABLE =
	'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Traps keyboard focus inside `node` while it is mounted, then restores focus to
 * the element that was focused before it mounted. Implements WCAG 2.1.2 (No
 * Keyboard Trap — Tab / Shift+Tab wrap within the dialog instead of escaping to
 * the page behind it) and 2.4.3 (Focus Order — focus returns to the trigger on
 * close). The node should carry `tabindex="-1"` so it can hold focus itself when
 * it has no focusable descendants.
 *
 * This is the single shared implementation used by `ui/Modal.svelte` AND the
 * feature dialogs that pre-date it and still hand-roll their own shell
 * (`InvoiceModal`, `RunDetailModal`, `BulkRecodeGLModal`, the supplier-portal
 * discount-accept dialog) — so every dialog gets identical focus management
 * without a risky structural rewrite of those e2e-load-bearing modals.
 */
export const focusTrap: Action<HTMLElement, FocusTrapParams | undefined> = (node, params) => {
	let onEscape = params?.onEscape;
	// Where focus was before the dialog opened — restored on destroy (2.4.3).
	const prevFocused = (document.activeElement as HTMLElement) ?? null;

	// Visible, focusable descendants in DOM order.
	function focusable(): HTMLElement[] {
		return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
			(el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement
		);
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			onEscape?.();
			return;
		}
		if (e.key !== 'Tab') return;
		const items = focusable();
		if (items.length === 0) {
			// Nothing tabbable but the dialog itself — hold focus on it.
			e.preventDefault();
			node.focus();
			return;
		}
		const first = items[0];
		const last = items[items.length - 1];
		const active = document.activeElement as HTMLElement;
		if (e.shiftKey && (active === first || !node.contains(active))) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && active === last) {
			e.preventDefault();
			first.focus();
		}
	}

	// Move focus into the dialog once its content is in the DOM (next microtask).
	queueMicrotask(() => {
		const items = focusable();
		(items[0] ?? node).focus();
	});
	node.addEventListener('keydown', onKey);

	return {
		update(p?: FocusTrapParams) {
			onEscape = p?.onEscape;
		},
		destroy() {
			node.removeEventListener('keydown', onKey);
			prevFocused?.focus?.();
		},
	};
};
