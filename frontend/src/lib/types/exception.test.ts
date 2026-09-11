import { describe, expect, it } from 'vitest';
import { en } from '$lib/i18n/locales/en';
import {
	EXCEPTION_TYPES,
	EXCEPTION_TYPE_LABEL_KEYS,
	exceptionTypeFallback,
	exceptionTypeLabelKey
} from './exception';

/**
 * Drift guard: the frontend's exception-type roster and its ENGLISH labels must
 * equal the backend's, which owns both.
 *
 * Two halves, and the second is the one that matters:
 *
 *  1. The roster — `exception_lifecycle.EXCEPTION_TYPES`. `Exception.exception_type`
 *     is a plain `String(50)`, so a type raised with no entry here renders
 *     through the tolerant fallback (de-underscored raw key) rather than a
 *     label. Silent, and in the wrong direction: two of these types are the
 *     ones that block a payment run.
 *  2. The WORDING — `api/exceptions.py::EXCEPTION_TYPE_LABELS`. The queue still
 *     renders the server's `type_label` off that map while the agent decision
 *     log renders `EXCEPTION_TYPE_LABEL_KEYS`, so if the two disagree in
 *     English the same exception reads differently on two tabs of one page.
 *     That is exactly the defect this module was added to fix (the log printed
 *     `po mismatch` where the queue said `PO Mismatch`), so it ships with the
 *     equality asserted rather than observed once.
 *
 * Reads the real Python through Vite's `import.meta.glob`, the way
 * `notification.roster.test.ts` does — the frontend deliberately carries no
 * `@types/node`, so `node:fs` would run under vitest and fail `pnpm check`.
 */

const RAW = import.meta.glob(
	[
		'../../../../backend/app/api/exceptions.py',
		'../../../../backend/app/services/exception_lifecycle.py'
	],
	{ query: '?raw', import: 'default', eager: true }
) as Record<string, string>;

function source(suffix: string): string {
	const hit = Object.entries(RAW).find(([path]) => path.endsWith(suffix));
	expect(hit, `${suffix} not found through import.meta.glob`).toBeDefined();
	return hit![1];
}

/** The `EXCEPTION_TYPES` tuple in declaration order. */
function backendRoster(): string[] {
	const py = source('exception_lifecycle.py');
	const block = /EXCEPTION_TYPES:\s*tuple\[str, \.\.\.\]\s*=\s*\(([\s\S]*?)\n\)/.exec(py);
	expect(block, 'EXCEPTION_TYPES tuple not found — did it move or change shape?').not.toBeNull();
	return [...block![1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
}

/** The `EXCEPTION_TYPE_LABELS` map, type → English label, in declaration order. */
function backendLabels(): Record<string, string> {
	const py = source('api/exceptions.py');
	const block = /EXCEPTION_TYPE_LABELS\s*=\s*\{([\s\S]*?)\n\}/.exec(py);
	expect(block, 'EXCEPTION_TYPE_LABELS map not found — did it move or change shape?').not.toBeNull();
	const out: Record<string, string> = {};
	for (const m of block![1].matchAll(/"([a-z0-9_]+)":\s*"([^"]*)"/g)) out[m[1]] = m[2];
	return out;
}

describe('exception-type taxonomy', () => {
	it('carries the backend roster, in the backend order', () => {
		const roster = backendRoster();
		expect(roster.length, 'parsed an empty roster — the regex stopped matching').toBeGreaterThan(
			10
		);
		expect([...EXCEPTION_TYPES]).toEqual(roster);
	});

	it('labels every type the backend labels, and nothing it does not', () => {
		expect(Object.keys(EXCEPTION_TYPE_LABEL_KEYS).sort()).toEqual(
			Object.keys(backendLabels()).sort()
		);
	});

	it('reads byte-identically to the backend label in English', () => {
		// The queue renders the server's `type_label`; the agent log renders the
		// key. A divergence here is one exception wearing two names in one page.
		const labels = backendLabels();
		const enRecord = en as Record<string, string>;
		for (const type of EXCEPTION_TYPES) {
			expect(enRecord[EXCEPTION_TYPE_LABEL_KEYS[type]], `${type} label drifted`).toBe(
				labels[type]
			);
		}
	});

	it('resolves every key in the English catalogue', () => {
		for (const key of Object.values(EXCEPTION_TYPE_LABEL_KEYS)) {
			expect(en, `${key} is missing from en.ts`).toHaveProperty(key);
		}
	});

	it('returns null for a type this build has no wording for', () => {
		// A historical row can carry a type this frontend predates. The caller
		// then renders the server's label, or the de-underscored raw key — never
		// an empty cell.
		expect(exceptionTypeLabelKey('duplicate')).toBe('exceptions.type.duplicate');
		expect(exceptionTypeLabelKey('some_future_type')).toBeNull();
		expect(exceptionTypeFallback('some_future_type')).toBe('some future type');
	});
});
