#!/usr/bin/env node
// Compliance-drift detector — advisory guard against a change quietly
// invalidating the repo's GDPR/CCPA posture. Plain node, no deps.
//
// The failure mode this exists for: a migration adds a column holding personal
// data, and nothing else changes. The DSAR export
// (`services/privacy_export.py`) never learns to include it, the erasure path
// (`services/privacy_erasure.py`) never learns to redact it, and the RoPA
// (`docs/ropa.md`) keeps describing a data set that no longer matches reality.
// None of that fails a test — the app works perfectly. It is only wrong on the
// day someone exercises a data-subject right.
//
// Deliberately heuristic and advisory. It reads the PR diff and reports where
// a compliance-relevant change arrived without its companion update. It is
// meant to prompt a human check, not to be authoritative — so it runs in
// `warn` mode by default (see COMPLIANCE_DRIFT_MODE below) and every finding
// is phrased as "confirm", not "you broke it".
//
// Usage:
//   node scripts/check_compliance_drift.mjs
//   COMPLIANCE_DRIFT_MODE=fail node scripts/check_compliance_drift.mjs
//
// Modes:
//   warn (default) — print findings + a summary, always exit 0.
//   fail           — exit 1 when there are findings. Flip the workflow to this
//                    once the signal has proven itself low-noise.

import { execSync } from 'node:child_process';

const MODE = process.env.COMPLIANCE_DRIFT_MODE === 'fail' ? 'fail' : 'warn';

const baseRef = process.env.GITHUB_BASE_REF
	? `origin/${process.env.GITHUB_BASE_REF}`
	: 'origin/main';

// ---------------------------------------------------------------------------
// Repo map — the paths a compliance-relevant change is expected to touch.
// ---------------------------------------------------------------------------

export const COMPANION_PATHS = {
	dsarExport: 'backend/app/services/privacy_export.py',
	erasure: 'backend/app/services/privacy_erasure.py',
	ropa: 'docs/ropa.md',
	subProcessors: 'docs/sub-processors.md',
	retention: 'backend/docs/retention.md',
	privacyDoc: 'backend/docs/privacy.md',
};

const MIGRATIONS_PREFIX = 'backend/alembic/versions/';

// Column names that carry personal data in this schema. Matching on COLUMN
// names (not table names) is the higher-signal choice here: Alembic migrations
// name every column explicitly, and it is a new *column* — not a new table —
// that most often slips past the export/erasure paths.
export const PERSONAL_DATA_COLUMNS = [
	'email',
	'full_name',
	'first_name',
	'last_name',
	'display_name',
	'contact_name',
	'contact_email',
	'contact_phone',
	'phone',
	'address',
	'street',
	'postal_code',
	'tax_id',
	'vat_number',
	'national_id',
	'date_of_birth',
	'bank_details',
	'bank_account',
	'account_number',
	'routing_number',
	'iban',
	'card_last_four',
	'account_last4',
	'hashed_password',
	'password_hash',
	'ip_address',
	'user_agent',
	'w9_file_key',
	'beneficial_owner',
];

// Third-party hosts already declared in the sub-processor register (or which
// are unambiguously local/dev). A new outbound host outside this list is the
// prompt to check whether the register needs a row.
export const KNOWN_HOSTS = [
	'anthropic.com',
	'openai.com',
	'amazonaws.com',
	'aws.amazon.com',
	'stripe.com',
	'lithic.com',
	'nium.com',
	'moderntreasury.com',
	'increase.com',
	'column.com',
	'dwolla.com',
	'checkeeper.com',
	'merge.dev',
	'netsuite.com',
	'microsoftonline.com',
	'dynamics.com',
	'complyadvantage.com',
	'dowjones.com',
	'refinitiv.com',
	'dnb.com',
	'clearbit.com',
	'c2fo.com',
	'openexchangerates.org',
	'mailgun.net',
	'mailgun.com',
	'slack.com',
	'office.com',
	'microsoft.com',
	'tax1099.com',
	'peppol.eu',
	'github.com',
	'githubusercontent.com',
	'schemas.xmlsoap.org',
	'www.w3.org',
	'docs.oasis-open.org',
	'unece.org',
	'sveltekit.io',
	'svelte.dev',
	'fastapi.tiangolo.com',
	'localhost',
	'example.com',
	'example.org',
];

// ---------------------------------------------------------------------------
// Pure analysis — exported so the test can drive it without a git repo.
// ---------------------------------------------------------------------------

/** Personal-data columns added by a migration diff (added lines only). */
export function personalDataColumnsInDiff(diff) {
	const found = new Set();
	for (const line of diff.split('\n')) {
		if (!line.startsWith('+') || line.startsWith('+++')) continue;
		// sa.Column("email", ...) / op.add_column("vendors", sa.Column('tax_id', ...))
		for (const m of line.matchAll(/["']([a-z0-9_]+)["']/gi)) {
			const name = m[1].toLowerCase();
			if (PERSONAL_DATA_COLUMNS.includes(name)) found.add(name);
		}
	}
	return [...found].sort();
}

/**
 * Tables created by a migration diff (added lines only).
 *
 * Handles both layouts, because Alembic's autogenerate emits the second:
 *   op.create_table("vendor_contacts", ...)      -- name on the call line
 *   op.create_table(                             -- name on the next line
 *       "vendor_contacts",
 */
export function createdTablesInDiff(diff) {
	const found = new Set();
	let awaitingName = false;
	for (const line of diff.split('\n')) {
		if (!line.startsWith('+') || line.startsWith('+++')) continue;
		if (awaitingName) {
			const m = line.match(/["']([a-z0-9_]+)["']/i);
			if (m) {
				found.add(m[1].toLowerCase());
				awaitingName = false;
			}
			continue;
		}
		const sameLine = line.match(/op\.create_table\(\s*["']([a-z0-9_]+)["']/i);
		if (sameLine) {
			found.add(sameLine[1].toLowerCase());
		} else if (/op\.create_table\(\s*$/.test(line)) {
			awaitingName = true;
		}
	}
	return [...found].sort();
}

/** Outbound hosts newly referenced in added lines, minus the known register. */
export function newOutboundHostsInDiff(diff) {
	const found = new Set();
	for (const line of diff.split('\n')) {
		if (!line.startsWith('+') || line.startsWith('+++')) continue;
		for (const m of line.matchAll(/https?:\/\/([a-z0-9.-]+\.[a-z]{2,})/gi)) {
			const host = m[1].toLowerCase();
			if (host.startsWith('127.') || host.startsWith('10.') || host.startsWith('192.168.')) continue;
			if (KNOWN_HOSTS.some((known) => host === known || host.endsWith(`.${known}`))) continue;
			found.add(host);
		}
	}
	return [...found].sort();
}

/**
 * Given the changed-file list and a `diffFor(path)` reader, produce findings.
 * Pure: no git, no fs — the caller injects both.
 */
export function analyze(files, diffFor) {
	const touched = (p) => files.includes(p);
	const findings = [];

	const migrations = files.filter(
		(f) => f.startsWith(MIGRATIONS_PREFIX) && f.endsWith('.py')
	);

	for (const migration of migrations) {
		const diff = diffFor(migration);
		const columns = personalDataColumnsInDiff(diff);
		const tables = createdTablesInDiff(diff);
		if (columns.length === 0 && tables.length === 0) continue;

		const what = [
			columns.length ? `personal-data column(s): ${columns.join(', ')}` : null,
			tables.length ? `new table(s): ${tables.join(', ')}` : null,
		]
			.filter(Boolean)
			.join('; ');

		if (columns.length && !touched(COMPANION_PATHS.dsarExport)) {
			findings.push({
				rule: 'dsar-export',
				file: migration,
				detail:
					`Adds ${what}, but ${COMPANION_PATHS.dsarExport} was not updated. ` +
					`Confirm the DSAR export bundle still returns every field a subject is entitled to.`,
			});
		}
		if (columns.length && !touched(COMPANION_PATHS.erasure)) {
			findings.push({
				rule: 'erasure',
				file: migration,
				detail:
					`Adds ${what}, but ${COMPANION_PATHS.erasure} was not updated. ` +
					`Confirm right-to-erasure redacts the new field — an un-redacted column ` +
					`survives an erasure request silently.`,
			});
		}
		if (!touched(COMPANION_PATHS.ropa)) {
			findings.push({
				rule: 'ropa',
				file: migration,
				detail:
					`Adds ${what}, but ${COMPANION_PATHS.ropa} was not updated. ` +
					`The Record of Processing Activities should describe what is now stored and why.`,
			});
		}
		if (tables.length && !touched(COMPANION_PATHS.retention)) {
			findings.push({
				rule: 'retention',
				file: migration,
				detail:
					`Creates ${tables.join(', ')}, but ${COMPANION_PATHS.retention} was not updated. ` +
					`Confirm the new records fall under a retention window.`,
			});
		}
	}

	// New outbound hosts anywhere in backend/frontend source → sub-processor check.
	const sourceFiles = files.filter(
		(f) =>
			(f.startsWith('backend/app/') || f.startsWith('frontend/src/')) &&
			/\.(py|ts|svelte)$/.test(f) &&
			!f.includes('/tests/') &&
			!f.includes('.test.')
	);
	const hosts = new Set();
	for (const f of sourceFiles) {
		for (const host of newOutboundHostsInDiff(diffFor(f))) hosts.add(host);
	}
	if (hosts.size > 0 && !touched(COMPANION_PATHS.subProcessors)) {
		findings.push({
			rule: 'sub-processors',
			file: sourceFiles.join(', '),
			detail:
				`New outbound host(s) referenced: ${[...hosts].sort().join(', ')} — but ` +
				`${COMPANION_PATHS.subProcessors} was not updated. If customer data reaches ` +
				`that provider it needs a row in the sub-processor register (and, for GDPR, ` +
				`a DPA).`,
		});
	}

	return findings;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function changedFiles() {
	try {
		return execSync(`git diff --name-only ${baseRef}...HEAD`, { encoding: 'utf8' })
			.split('\n')
			.filter(Boolean);
	} catch {
		return [];
	}
}

function diffFor(path) {
	try {
		return execSync(`git diff ${baseRef}...HEAD -- "${path}"`, { encoding: 'utf8' });
	} catch {
		return '';
	}
}

function main() {
	const files = changedFiles();
	if (files.length === 0) {
		console.log(`No changed files vs ${baseRef} — skipping compliance-drift check.`);
		return 0;
	}

	const findings = analyze(files, diffFor);
	if (findings.length === 0) {
		console.log(`No compliance drift detected across ${files.length} changed file(s).`);
		return 0;
	}

	console.log(`\nCompliance-drift findings (${findings.length}) — mode: ${MODE}\n`);
	for (const f of findings) {
		console.log(`  [${f.rule}] ${f.file}`);
		console.log(`      ${f.detail}\n`);
	}
	console.log(
		'These are advisory. Either make the companion update, or confirm in the PR ' +
			'description why it is not needed.\n' +
			'Reference: docs/ropa.md, docs/sub-processors.md, backend/docs/privacy.md.'
	);

	return MODE === 'fail' ? 1 : 0;
}

// Only run the CLI when invoked directly, so the test can import the pure parts.
if (process.argv[1] && process.argv[1].endsWith('check_compliance_drift.mjs')) {
	process.exit(main());
}
