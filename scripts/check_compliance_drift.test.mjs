#!/usr/bin/env node
// Tests for the compliance-drift detector. `node --test scripts/check_compliance_drift.test.mjs`
//
// The detector is heuristic, so the thing worth pinning is its *shape*: it must
// fire when a companion update is genuinely missing, and — more importantly —
// stay quiet when it is present. A guard that cries wolf gets switched off, and
// a switched-off guard protects nothing.

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
	COMPANION_PATHS,
	analyze,
	createdTablesInDiff,
	newOutboundHostsInDiff,
	personalDataColumnsInDiff,
} from './check_compliance_drift.mjs';

const MIGRATION = 'backend/alembic/versions/0099_add_contact.py';

const ADD_PII_COLUMN = `
--- a/${MIGRATION}
+++ b/${MIGRATION}
+def upgrade() -> None:
+    op.add_column("vendors", sa.Column("contact_email", sa.String(255), nullable=True))
`;

const CREATE_TABLE = `
+def upgrade() -> None:
+    op.create_table(
+        "vendor_contacts",
+        sa.Column("id", sa.Uuid(), primary_key=True),
+        sa.Column("email", sa.String(255), nullable=False),
+    )
`;

const NO_PII = `
+def upgrade() -> None:
+    op.add_column("invoices", sa.Column("po_number", sa.String(64), nullable=True))
`;

const diffMap = (map) => (path) => map[path] ?? '';

// ---------------------------------------------------------------------------
// Extractors
// ---------------------------------------------------------------------------

test('personalDataColumnsInDiff finds PII columns on added lines', () => {
	assert.deepEqual(personalDataColumnsInDiff(ADD_PII_COLUMN), ['contact_email']);
});

test('personalDataColumnsInDiff ignores non-PII columns', () => {
	assert.deepEqual(personalDataColumnsInDiff(NO_PII), []);
});

test('personalDataColumnsInDiff ignores REMOVED lines', () => {
	const removal = ADD_PII_COLUMN.split('\n')
		.map((l) => (l.startsWith('+') && !l.startsWith('+++') ? `-${l.slice(1)}` : l))
		.join('\n');
	assert.deepEqual(personalDataColumnsInDiff(removal), []);
});

test('personalDataColumnsInDiff ignores the +++ file header', () => {
	// The header names the migration path, which must never be read as a column.
	assert.deepEqual(personalDataColumnsInDiff('+++ b/backend/app/email_address.py\n'), []);
});

test('createdTablesInDiff finds op.create_table targets', () => {
	assert.deepEqual(createdTablesInDiff(CREATE_TABLE), ['vendor_contacts']);
});

test('newOutboundHostsInDiff flags an unknown host', () => {
	const diff = '+    BASE = "https://api.some-new-vendor.io/v1"\n';
	assert.deepEqual(newOutboundHostsInDiff(diff), ['api.some-new-vendor.io']);
});

test('newOutboundHostsInDiff ignores already-registered providers and subdomains', () => {
	const diff =
		'+    A = "https://api.stripe.com/v1"\n' +
		'+    B = "https://api.anthropic.com/v1/messages"\n' +
		'+    C = "https://s3.eu-west-1.amazonaws.com/bucket"\n';
	assert.deepEqual(newOutboundHostsInDiff(diff), []);
});

test('newOutboundHostsInDiff ignores private / loopback targets', () => {
	const diff = '+    DEV = "http://127.0.0.1:12112/merge"\n+    LAN = "http://192.168.1.5/x"\n';
	assert.deepEqual(newOutboundHostsInDiff(diff), []);
});

// ---------------------------------------------------------------------------
// analyze()
// ---------------------------------------------------------------------------

test('a PII column with no companion update reports export + erasure + ropa', () => {
	const findings = analyze([MIGRATION], diffMap({ [MIGRATION]: ADD_PII_COLUMN }));
	const rules = findings.map((f) => f.rule).sort();
	assert.deepEqual(rules, ['dsar-export', 'erasure', 'ropa']);
});

test('updating export + erasure + ropa silences those rules', () => {
	const files = [
		MIGRATION,
		COMPANION_PATHS.dsarExport,
		COMPANION_PATHS.erasure,
		COMPANION_PATHS.ropa,
	];
	const findings = analyze(files, diffMap({ [MIGRATION]: ADD_PII_COLUMN }));
	assert.deepEqual(findings, [], JSON.stringify(findings, null, 2));
});

test('a new table also asks about retention', () => {
	const findings = analyze([MIGRATION], diffMap({ [MIGRATION]: CREATE_TABLE }));
	assert.ok(findings.some((f) => f.rule === 'retention'));
});

test('a migration with no personal data produces no findings', () => {
	assert.deepEqual(analyze([MIGRATION], diffMap({ [MIGRATION]: NO_PII })), []);
});

test('a new outbound host asks about the sub-processor register', () => {
	const src = 'backend/app/services/enrichment_adapters/newco.py';
	const findings = analyze(
		[src],
		diffMap({ [src]: '+BASE = "https://api.newco.example.net/v2"\n' })
	);
	assert.deepEqual(
		findings.map((f) => f.rule),
		['sub-processors']
	);
});

test('updating the sub-processor register silences it', () => {
	const src = 'backend/app/services/enrichment_adapters/newco.py';
	const findings = analyze(
		[src, COMPANION_PATHS.subProcessors],
		diffMap({ [src]: '+BASE = "https://api.newco.example.net/v2"\n' })
	);
	assert.deepEqual(findings, []);
});

test('test files are not scanned for outbound hosts', () => {
	const testFile = 'backend/app/services/tests/test_thing.py';
	const findings = analyze(
		[testFile],
		diffMap({ [testFile]: '+BASE = "https://api.newco.example.net/v2"\n' })
	);
	assert.deepEqual(findings, []);
});

test('an unrelated change produces no findings', () => {
	const f = 'frontend/src/routes/invoices/+page.svelte';
	assert.deepEqual(analyze([f], diffMap({ [f]: '+  const x = 1;\n' })), []);
});
