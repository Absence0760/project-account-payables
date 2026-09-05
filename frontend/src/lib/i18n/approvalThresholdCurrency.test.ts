import { describe, test, expect } from 'vitest';
import { interpolate } from './interpolate';
import { SUPPORTED_LOCALES } from './locale';
import { CATALOGUE_LOADERS } from './catalogues';
import builderSource from '../../routes/workflows/[id]/+page.svelte?raw';
import organizationSource from '../../routes/organization/+page.svelte?raw';
import matrixEditorSource from '../components/modals/ApprovalMatrixEditor.svelte?raw';

/**
 * The approval money thresholds are denominated in the org's REPORTING
 * currency, and the UI has to say which one.
 *
 * `auto_approve_below`, `require_cfo_above`, `max_invoice_amount`, the approval
 * matrix's per-level `min_amount` / `max_amount` bands and
 * `payments.cfo_approval_above` are bare numbers on a JSONB config. The backend
 * converts each invoice into the org's reporting currency before comparing them
 * (`approval_chain.reporting_gate_amount` / `payment_controls.cfo_approval_decision`).
 *
 * Every one of these labels used to read `($)`, hardcoded, in all six locales —
 * so an admin on a GBP-reporting tenant typing `10000` into "Require CFO
 * approval above ($)" had no way to know whether that meant pounds or dollars.
 * Before the backend change the number was merely ambiguous; now it has a
 * definite meaning the operator could not see, which is worse.
 *
 * The code is resolved at runtime from `orgCurrency` (the same store `/cfo` and
 * `/discounts` read) and interpolated as `{currency}`. Hardcoding a default
 * here would be the same defect one layer up.
 *
 * Fails pre-fix: every asserted message contained `($)` and no `{currency}`.
 */

/** Labels + hints whose money figure is in the reporting currency. */
const DENOMINATED_KEYS = [
	'approvalMatrix.maxAmount',
	'approvalMatrix.minAmount',
	'org.payments.cfoThreshold',
	'workflows.builder.approval.autoApproveBelow',
	'workflows.builder.approval.autoApproveBelowHint',
	'workflows.builder.approval.matrixHint',
	'workflows.builder.approval.maxInvoiceAmount',
	'workflows.builder.approval.maxInvoiceAmountHint',
	'workflows.builder.approval.requireCfoAbove',
	'workflows.builder.approval.requireCfoAboveHint',
] as const;

/**
 * Every place one of those messages is rendered, as raw source. `?raw` (rather
 * than `node:fs`) keeps this runnable under the plain-Node vitest config
 * without pulling `@types/node` into `svelte-check`'s program.
 */
const RENDER_SITES: Record<string, string> = {
	'workflows/[id]/+page.svelte': builderSource,
	'organization/+page.svelte': organizationSource,
	'ApprovalMatrixEditor.svelte': matrixEditorSource,
};

describe('approval thresholds name their currency', () => {
	for (const loc of SUPPORTED_LOCALES) {
		test(`${loc}: every denominated message carries {currency} and no hardcoded sign`, async () => {
			const dict = (await CATALOGUE_LOADERS[loc]()) as Record<string, string>;
			for (const key of DENOMINATED_KEYS) {
				const value = dict[key];
				expect(value, `${loc} is missing ${key}`).toBeTruthy();
				expect(value, `${loc}/${key} does not name its currency`).toContain('{currency}');
				// A hardcoded sign is exactly the bug: it asserts a denomination
				// nobody resolved. Full-width `＄` / `（$）` count too (ja).
				expect(value, `${loc}/${key} hardcodes a currency sign`).not.toMatch(/[$＄€£¥]/);
			}
		});
	}

	test('the code is substituted, not left as a literal placeholder', () => {
		expect(interpolate('Require CFO approval above ({currency})', { currency: 'GBP' })).toBe(
			'Require CFO approval above (GBP)',
		);
		// A GBP-reporting tenant never sees a dollar sign on these fields.
		expect(interpolate('Min amount ({currency})', { currency: 'JPY' })).toBe('Min amount (JPY)');
	});

	for (const [rel, src] of Object.entries(RENDER_SITES)) {
		test(`${rel}: resolves the code from orgCurrency, never a hardcoded default`, () => {
			// Every m('<denominated key>' … call in this file passes `currency`.
			for (const key of DENOMINATED_KEYS) {
				const calls = [...src.matchAll(new RegExp(`m\\('${key.replace('.', '\\.')}'([^)]*)\\)`, 'g'))];
				for (const call of calls) {
					expect(call[1], `${rel} renders ${key} without a currency param`).toContain(
						'currency: orgCurrency.currency',
					);
				}
			}
			// …and the store is actually loaded, so the code isn't stuck on the
			// platform default for the life of the page.
			if (src.includes('orgCurrency.currency')) {
				expect(src, `${rel} never calls orgCurrency.ensureLoaded()`).toContain(
					'orgCurrency.ensureLoaded()',
				);
			}
		});
	}
});
