import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

/**
 * Source guard: a spec that reconfigures the tenant's SEEDED workflow
 * definition must clean up the version rows that creates.
 *
 * `PATCH /api/workflows/{id}` auto-snapshots the prior `steps_config` into
 * `workflow_versions` whenever the steps change. A spec that PATCHes the
 * seeded default and PATCHes it back therefore leaves two rows behind per
 * round trip — and they are invisible: the `/workflows` list shows
 * definitions, not versions, nothing else in the suite reads them, and no
 * teardown can sweep them by name because they hang off a SEEDED row, which
 * `deleteWorkflowsWhere`'s `is_default = false` seatbelt exists to protect
 * (decisions §127). The pile only grows: a local `e2e5` measured 0 → 5 → 9
 * across two suite runs, `e2e2` was at 14.
 *
 * `seededWorkflowVersions.ts` is the teardown. This guard is what stops the
 * NEXT spec re-introducing the leak, because the leak produces no failure at
 * all — just a tenant that drifts further from the seed every run. It has
 * already earned its keep: the backlog entry named two specs, and the scan
 * found a third — `admin/delete-safety.spec.ts` puts a throwaway user into
 * the seeded workflow's `approver_ids` and takes it back out again.
 *
 * Detection is source-level for the same reason
 * `meta/teardown-guard.spec.ts` is: the mutation goes out as a JSON body over
 * `page.request.patch`, so no compiler can see it. Comments are stripped
 * first so prose *about* the pattern does not trip it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const E2E_ROOT = join(HERE, '..');

/** The teardown a matching file has to reach for. */
const TEARDOWN = 'purgeSeededWorkflowVersions';

/** Files exempt from the scan, and why. Exactly two — widening this set is how
 *  the leak comes back. */
const EXEMPT = new Set([
	// The teardown itself.
	join('workflows', 'seededWorkflowVersions.ts'),
	// This file: its known-bad fixtures below are string literals, not
	// comments, so the detector correctly sees them.
	join('workflows', 'seeded-version-teardown-guard.spec.ts')
]);

/**
 * Strip `//` line and block comments so prose describing the anti-pattern is
 * not itself flagged. Crude by design — it can mangle a `//` inside a string
 * literal, which cannot produce a false NEGATIVE for the pattern searched for.
 */
export function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}

/** Resolves a definition out of the LIST — i.e. one it did not create, which
 *  on a seeded tenant is the seeded default. */
const PICKS_EXISTING = /\.find\(\s*\(\s*\w+\s*\)\s*=>\s*\w+\.is_(?:active|default)\b/;

/** Sends a `steps` payload on a PATCH — the only body shape that snapshots a
 *  version. `is_active`-only PATCHes (which several sibling specs make against
 *  the seeded default) write nothing and are correctly ignored. */
const PATCHES_STEPS = /(?:patchWorkflow|\.patch)\s*\([^;]*?\bsteps\s*:/;

/** Talks to the workflow-definition API at all — keeps an `is_default` pick
 *  over some OTHER resource (entities, say) out of the match. */
const TOUCHES_WORKFLOW_API = /\/api\/workflows\b/;

/** Does this file reconfigure a workflow definition it did not create? */
export function mutatesSeededWorkflow(source: string): boolean {
	const code = stripComments(source);
	return (
		TOUCHES_WORKFLOW_API.test(code) && PICKS_EXISTING.test(code) && PATCHES_STEPS.test(code)
	);
}

/** Does it clean the version rows that mutation writes? */
export function cleansSeededVersions(source: string): boolean {
	return stripComments(source).includes(TEARDOWN);
}

function typescriptFiles(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		if (entry.name === 'node_modules' || entry.name === '.auth') continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) found.push(...typescriptFiles(full));
		else if (entry.name.endsWith('.ts')) found.push(full);
	}
	return found;
}

function scan(): { candidates: string[]; offenders: string[] } {
	const candidates: string[] = [];
	const offenders: string[] = [];
	for (const file of typescriptFiles(E2E_ROOT)) {
		const rel = relative(E2E_ROOT, file);
		if (EXEMPT.has(rel)) continue;
		const source = readFileSync(file, 'utf8');
		if (!mutatesSeededWorkflow(source)) continue;
		candidates.push(rel);
		if (!cleansSeededVersions(source)) offenders.push(rel);
	}
	return { candidates, offenders };
}

test.describe('seeded workflow-version teardown discipline', () => {
	test('a spec that PATCHes an existing workflow steps payload cleans its versions', () => {
		const { candidates, offenders } = scan();

		// A vacuous pass is the failure mode this guard is most exposed to: if
		// the detector stops recognising the specs it was written for, the
		// offenders list is empty for the wrong reason. Pin both halves.
		expect(
			candidates,
			'the detector no longer recognises the three specs it was written for — ' +
				'fix the patterns rather than the expectation.'
		).toEqual(
			expect.arrayContaining([
				join('workflows', 'invoice-routing.spec.ts'),
				join('workflows', 'deactivation-snapshot.spec.ts'),
				join('admin', 'delete-safety.spec.ts')
			])
		);

		expect(
			offenders,
			'`PATCH /api/workflows/{id}` with `steps` auto-snapshots the prior ' +
				'`steps_config` into `workflow_versions`. On the SEEDED default those ' +
				'rows are unreachable by any name sweep — `deleteWorkflowsWhere` refuses ' +
				'`is_default` rows on purpose (decisions §127) — and nothing reads them, ' +
				'so they accumulate every run. Bracket the describe with ' +
				'`markSeededWorkflowVersions()` / `purgeSeededWorkflowVersions()` from ' +
				'`workflows/seededWorkflowVersions.ts`.'
		).toEqual([]);
	});

	test('the detector flags a known-bad file and clears the known-good near-misses', () => {
		// A clean scan over clean files proves nothing about the detector, so
		// exercise it against fixtures standing in for both outcomes.
		const bad = [
			"const active = wfs.find((w) => w.is_active);",
			"await page.request.patch(`${API_BASE}/api/workflows/${active.id}`, {",
			'\tdata: { steps: edited }',
			'});'
		].join('\n');
		expect(mutatesSeededWorkflow(bad)).toBe(true);
		expect(cleansSeededVersions(bad)).toBe(false);

		// Near-miss 1: activating the seeded default writes no version row —
		// `workflows/delete-safety` and `invoices/approval-chain-progress` both
		// do exactly this and must NOT be dragged in.
		const activateOnly = [
			"const defaultWf = before.find((w) => w.is_default)!;",
			"await patchWorkflow(page, defaultWf.id, { is_active: true });",
			"await page.request.get(`${API_BASE}/api/workflows`);"
		].join('\n');
		expect(mutatesSeededWorkflow(activateOnly)).toBe(false);

		// Near-miss 2: an `is_default` pick over a DIFFERENT resource.
		const entities = [
			"const defaultEntity = listed.find((e) => e.is_default);",
			"await page.request.patch(`${API_BASE}/api/entities/${defaultEntity.id}`, {",
			'\tdata: { steps: 3 }',
			'});'
		].join('\n');
		expect(mutatesSeededWorkflow(entities)).toBe(false);

		// Near-miss 3: a definition the spec CREATED is not a seeded row, so
		// its versions go with it through `deleteWorkflowsWhere`.
		const ownDefinition = [
			"const id = await createWorkflow(page, `${MARKER}Mine`);",
			"await patchWorkflow(page, id, { steps: edited });",
			"await page.request.get(`${API_BASE}/api/workflows`);"
		].join('\n');
		expect(mutatesSeededWorkflow(ownDefinition)).toBe(false);

		// And prose about the pattern is stripped before matching.
		const commentary = [
			'// wfs.find((w) => w.is_active) then patchWorkflow(page, id, { steps: x })',
			'/* against /api/workflows — this is what NOT to do. */',
			"await page.request.get(`${API_BASE}/api/workflows`);"
		].join('\n');
		expect(mutatesSeededWorkflow(commentary)).toBe(false);
	});
});
