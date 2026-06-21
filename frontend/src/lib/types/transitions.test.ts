import { describe, it, expect } from 'vitest';
import { VALID_TRANSITIONS, commonTransitions, type InvoiceStatus } from './invoice';

// The frontend transition map must mirror the backend workflow_engine
// VALID_TRANSITIONS for the user-selectable manual moves. Offering a target
// the backend rejects produces a guaranteed 409 on every selected row (the
// `new → rejected` / `approved → rejected` bug — `rejected` is only reachable
// from `ready_for_review`).
const BACKEND_TRANSITIONS: Record<string, string[]> = {
	new: ['pending', 'ready_for_review', 'approved', 'done'],
	pending: ['ready_for_review', 'approved', 'failed'],
	ready_for_review: ['approved', 'rejected'],
	approved: ['sending_to_erp', 'payment_scheduled', 'done'],
	rejected: ['ready_for_review', 'new'],
	sending_to_erp: ['sent_to_erp', 'failed'],
	sent_to_erp: ['posted_in_erp', 'done'],
	posted_in_erp: ['payment_scheduled', 'done'],
	payment_scheduled: ['paid', 'approved'],
	paid: ['done', 'approved'],
	done: [],
	failed: ['pending', 'sending_to_erp']
};

describe('VALID_TRANSITIONS', () => {
	it('never offers a target the backend would reject', () => {
		for (const [from, targets] of Object.entries(VALID_TRANSITIONS)) {
			const allowed = new Set(BACKEND_TRANSITIONS[from] ?? []);
			for (const to of targets) {
				expect(allowed.has(to), `${from} → ${to} is not a valid backend transition`).toBe(
					true
				);
			}
		}
	});

	it('does not offer rejected from new or approved (the 409 bug)', () => {
		expect(VALID_TRANSITIONS.new).not.toContain('rejected');
		expect(VALID_TRANSITIONS.approved).not.toContain('rejected');
	});

	it('still allows rejecting from ready_for_review', () => {
		expect(VALID_TRANSITIONS.ready_for_review).toContain('rejected');
	});
});

describe('commonTransitions', () => {
	it('returns the intersection of valid targets across a mixed selection', () => {
		// new and approved share only `done` now that `rejected` was removed.
		const common = commonTransitions(['new', 'approved'] as InvoiceStatus[]);
		expect(common).toEqual(['done']);
	});

	it('returns an empty list for an empty selection', () => {
		expect(commonTransitions([])).toEqual([]);
	});
});
