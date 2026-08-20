/**
 * Editing a webhook step's headers as key/value ROWS while they persist as an
 * OBJECT.
 *
 * The two shapes are not interchangeable, and that mismatch is a bug the
 * workflow builder shipped: `headerRows` was a pure `$derived` round-trip
 * through `config.headers`, so "+ Add header" appended `['', '']`, the
 * projection dropped it (an object cannot hold a blank key), the config came
 * back byte-identical, and the row re-derived away. Nothing appeared — a
 * webhook header could not be added through the UI at all. The same round trip
 * made clearing an existing header's NAME delete the whole row mid-edit,
 * including the value the user had not touched.
 *
 * A blank-key row is a legitimate INTERMEDIATE state of the editor. So the rows
 * are local state, and only their projection goes down to the config.
 *
 * Pure — no `$state`, no DOM — so it lives in `utils/` and is unit-tested.
 */

/** One editable header row. A blank key means "being typed", not "delete". */
export type HeaderRow = [key: string, value: string];

/** Seed editable rows from a persisted headers object. */
export function headersToRows(headers: Record<string, string> | undefined | null): HeaderRow[] {
	return Object.entries(headers ?? {});
}

/**
 * Project rows back to the persisted object.
 *
 * Blank-key rows are dropped (they have no name to persist yet) and keys are
 * trimmed. A later row with the same key wins, matching plain object-literal
 * semantics.
 */
export function rowsToHeaders(rows: readonly HeaderRow[]): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [key, value] of rows) {
		const name = key.trim();
		if (name) out[name] = value;
	}
	return out;
}

/**
 * An order-independent identity for a headers object.
 *
 * Used to answer "do these rows still describe the incoming config?" — the
 * question that decides whether to re-seed the editor. It must ignore key ORDER
 * because the persisted object's order is whatever the server/JSON round trip
 * produced; comparing raw `JSON.stringify` would re-seed on every keystroke and
 * throw away the row being typed.
 */
export function headersIdentity(headers: Record<string, string> | undefined | null): string {
	return JSON.stringify(
		Object.entries(headers ?? {}).sort(([a], [b]) => a.localeCompare(b))
	);
}

/**
 * Do these rows already describe `headers`?
 *
 * `true` → keep the local rows (they carry in-progress edits the object cannot
 * represent). `false` → the config changed underneath us (a different step was
 * selected, a version restored), so re-seed.
 */
export function rowsMatchHeaders(
	rows: readonly HeaderRow[],
	headers: Record<string, string> | undefined | null
): boolean {
	return headersIdentity(rowsToHeaders(rows)) === headersIdentity(headers);
}
