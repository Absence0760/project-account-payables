import { describe, expect, it } from 'vitest';
import { DEFAULT_APPROVAL_CONFIG, DEFAULT_STEP_CONFIGS } from './workflow';
import type { ApprovalStepConfig } from './workflow';

/**
 * Drift guard on the builder's approval-step defaults.
 *
 * `require_segregation` (approver ≠ uploader) is the classic AP fraud control
 * and the BACKEND's default: `schemas/workflow.py` declares
 * `require_segregation: bool = True` and `services/approval_chain.py` reads
 * `.get("require_segregation", True)`, so an absent key is safe. The frontend
 * default used to write an explicit `false`, which is a real value — every
 * workflow created through the UI shipped with self-approval enabled, and with
 * no toggle anywhere it could neither be seen nor re-enabled. Template-created
 * workflows (`POST /api/workflows/from-template`) were never affected, which is
 * why it stayed hidden.
 *
 * The builder now renders a visible switch for it, so turning it off is a
 * deliberate act — but the DEFAULT must never be weaker than the backend's.
 */
describe('DEFAULT_APPROVAL_CONFIG', () => {
	it('requires segregation of duties by default', () => {
		expect(DEFAULT_APPROVAL_CONFIG.require_segregation).toBe(true);
	});

	it('carries it through the step-config factory the builder actually calls', () => {
		const cfg = DEFAULT_STEP_CONFIGS.approval() as ApprovalStepConfig;
		expect(cfg.require_segregation).toBe(true);
	});

	it('gives the factory a fresh config each call (no shared mutable arrays)', () => {
		const a = DEFAULT_STEP_CONFIGS.approval() as ApprovalStepConfig;
		const b = DEFAULT_STEP_CONFIGS.approval() as ApprovalStepConfig;
		expect(a).not.toBe(b);
		expect(a.approver_ids).not.toBe(b.approver_ids);
		expect(a.approval_chain).not.toBe(b.approval_chain);
	});

	it('leaves the amount thresholds unset — a new approval step gates nothing away', () => {
		expect(DEFAULT_APPROVAL_CONFIG.auto_approve_below).toBeNull();
		expect(DEFAULT_APPROVAL_CONFIG.require_cfo_above).toBeNull();
		expect(DEFAULT_APPROVAL_CONFIG.max_invoice_amount).toBeNull();
	});
});
