/**
 * Whole-row click helpers for clickable list rows.
 *
 * A list row's primary cell carries a real `<RowLink>` (button/anchor) that is
 * the accessible, keyboard-focusable "open" control. On top of that we make the
 * *whole row* clickable for pointer users by putting an `onclick` on the `<tr>`.
 *
 * The catch: a row also contains controls that must NOT open the row — the
 * bulk-select checkbox, per-row action buttons (Edit/Delete/Void/…), and any
 * link. `isRowOpenClick` is the guard that keeps the row-open handler from
 * firing when the click landed on one of those. It mirrors the Gmail/Linear
 * pattern (click anywhere on the row to open, except the interactive bits).
 */

/** Selector for elements/cells that should swallow a row click rather than
 *  trigger row-open: any interactive control, plus the checkbox + actions cells
 *  (which use the shared `.checkbox-col` / `.actions` classes). */
const ROW_GUARD_SELECTOR = 'button, a, input, label, select, textarea, .checkbox-col, .actions';

/**
 * True when a click on a clickable row should open it — i.e. the click did not
 * originate on an interactive control or in the checkbox / actions columns.
 */
export function isRowOpenClick(e: MouseEvent): boolean {
	const target = e.target as Element | null;
	return !target?.closest(ROW_GUARD_SELECTOR);
}
