import { describe, expect, it } from 'vitest';
import { formatApiDetail } from './apiError';

/**
 * Regression guard for the "[object Object]" toast.
 *
 * `api.ts` used to do `body.detail || fallback` and hand the result straight to
 * `new Error(...)`. FastAPI's 422 `detail` is a LIST of `{loc, msg, type}`
 * objects, so `String([{…}])` produced literally `"[object Object]"` — every
 * validation failure in the app rendered as that. The expense modal's blank
 * required date is what surfaced it, but the defect was shared by every route.
 */
describe('formatApiDetail', () => {
	const FALLBACK = 'API error 422';

	it('passes a plain string detail through', () => {
		expect(formatApiDetail('Vendor is blocked', FALLBACK)).toBe('Vendor is blocked');
	});

	it('renders a FastAPI validation-error list readably — never "[object Object]"', () => {
		const detail = [
			{
				type: 'date_type',
				loc: ['body', 'expense_date'],
				msg: 'Input should be a valid date',
				input: null
			}
		];
		const out = formatApiDetail(detail, FALLBACK);
		expect(out).toBe('expense_date: Input should be a valid date');
		expect(out).not.toContain('object Object');
	});

	it('joins multiple validation errors', () => {
		const detail = [
			{ loc: ['body', 'expense_date'], msg: 'Input should be a valid date' },
			{ loc: ['body', 'amount'], msg: 'Input should be greater than 0' }
		];
		expect(formatApiDetail(detail, FALLBACK)).toBe(
			'expense_date: Input should be a valid date; amount: Input should be greater than 0'
		);
	});

	it('drops the request-part segment but keeps a nested field path', () => {
		const detail = [{ loc: ['body', 'items', 0, 'invoice_id'], msg: 'field required' }];
		// The numeric index is not a string segment, so it is not part of the path.
		expect(formatApiDetail(detail, FALLBACK)).toBe('items.invoice_id: field required');
	});

	it('renders a message-only validation item without a leading colon', () => {
		expect(formatApiDetail([{ msg: 'Value error, bad tier' }], FALLBACK)).toBe(
			'Value error, bad tier'
		);
	});

	it('renders a lone {msg} / {message} object', () => {
		expect(formatApiDetail({ msg: 'Nope' }, FALLBACK)).toBe('Nope');
		expect(formatApiDetail({ message: 'Submit blocked' }, FALLBACK)).toBe('Submit blocked');
	});

	it('falls back for missing / empty / unrecognised shapes', () => {
		expect(formatApiDetail(undefined, FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail(null, FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail('', FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail('   ', FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail([], FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail([{ type: 'x' }], FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail({ unrelated: 1 }, FALLBACK)).toBe(FALLBACK);
		expect(formatApiDetail(42, FALLBACK)).toBe(FALLBACK);
	});
});
