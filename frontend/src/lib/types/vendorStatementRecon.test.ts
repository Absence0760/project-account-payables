import { describe, expect, it } from 'vitest';
import {
	RECON_SOURCE_FORMAT_LABELS,
	formatExtractionConfidence,
	isMachineRead,
	sourceStatementFilename
} from './vendorStatementRecon';

// The vendor-statement provenance surface is the only place a reviewer learns
// that a run's lines were MACHINE-READ off a PDF rather than typed or parsed
// from a CSV — so its display helpers are pinned here. They sit on the far side
// of a network boundary (the adapter's own reported confidence), which is why
// they are defensive rather than trusting.

describe('formatExtractionConfidence', () => {
	it('renders a 0..1 confidence as a whole percentage', () => {
		expect(formatExtractionConfidence(0.6)).toBe('60%');
		expect(formatExtractionConfidence(0)).toBe('0%');
		expect(formatExtractionConfidence(1)).toBe('100%');
	});

	it('rounds rather than truncating', () => {
		expect(formatExtractionConfidence(0.955)).toBe('96%');
		expect(formatExtractionConfidence(0.844)).toBe('84%');
	});

	it('clamps an out-of-range figure instead of showing nonsense', () => {
		// A provider reporting 0..100 instead of 0..1 must not render "14000%".
		expect(formatExtractionConfidence(1.4)).toBe('100%');
		expect(formatExtractionConfidence(-0.2)).toBe('0%');
	});

	it('falls back to the placeholder for a missing or non-numeric value', () => {
		expect(formatExtractionConfidence(null)).toBe('—');
		expect(formatExtractionConfidence(undefined)).toBe('—');
		expect(formatExtractionConfidence(Number.NaN)).toBe('—');
		expect(formatExtractionConfidence(Number.POSITIVE_INFINITY)).toBe('—');
		expect(formatExtractionConfidence(null, 'n/a')).toBe('n/a');
	});
});

describe('isMachineRead', () => {
	it('is true only when the run carries an extraction block', () => {
		expect(
			isMachineRead({
				extraction: { method: 'ai_extraction', provider: 'mock', confidence: 0.6, line_count: 3 }
			})
		).toBe(true);
		expect(isMachineRead({ extraction: null })).toBe(false);
	});
});

describe('sourceStatementFilename', () => {
	it('composes from the run metadata, not the storage key', () => {
		expect(
			sourceStatementFilename({
				source_format: 'pdf',
				statement_date: '2026-01-31',
				vendor_name: 'Acme Supplies Ltd'
			})
		).toBe('statement-acme-supplies-ltd-2026-01-31.pdf');
	});

	it('uses a .csv extension for every non-PDF intake', () => {
		expect(
			sourceStatementFilename({
				source_format: 'csv',
				statement_date: '2026-02-28',
				vendor_name: 'Globex'
			})
		).toBe('statement-globex-2026-02-28.csv');
	});

	it('survives a missing or punctuation-only vendor name', () => {
		expect(
			sourceStatementFilename({ source_format: 'csv', statement_date: '2026-03-31', vendor_name: null })
		).toBe('statement-vendor-2026-03-31.csv');
		expect(
			sourceStatementFilename({ source_format: 'csv', statement_date: '2026-03-31', vendor_name: '&&&' })
		).toBe('statement-vendor-2026-03-31.csv');
	});
});

describe('RECON_SOURCE_FORMAT_LABELS', () => {
	it('covers every source format the backend can stamp', () => {
		// `SOURCE_MANUAL` / `SOURCE_CSV` / `SOURCE_PDF` in
		// backend/app/models/vendor_statement_recon.py. An unlabelled format would
		// render a blank pill.
		expect(Object.keys(RECON_SOURCE_FORMAT_LABELS).sort()).toEqual(['csv', 'manual', 'pdf']);
		for (const label of Object.values(RECON_SOURCE_FORMAT_LABELS)) {
			expect(label.length).toBeGreaterThan(0);
		}
	});
});
