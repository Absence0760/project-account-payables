import { getActiveFormatLocale } from '$lib/i18n/formatLocale';

/**
 * Friendly relative-time formatting (e.g. "Just now", "5m ago", "2d ago").
 * Shared by the notifications page + the sidebar notification popover so the
 * two can't drift. Coarse buckets only — exact timestamps go in `title`.
 */
export function timeAgo(iso: string): string {
	const diff = Date.now() - new Date(iso).getTime();
	const mins = Math.floor(diff / 60000);
	if (mins < 1) return 'Just now';
	if (mins < 60) return `${mins}m ago`;
	const hours = Math.floor(mins / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	return days === 1 ? '1d ago' : `${days}d ago`;
}

/**
 * Parse a bare calendar date (`YYYY-MM-DD` or `YYYY-MM`) into a `Date` at
 * *local* midnight. `new Date('2026-06-20')` parses the string as UTC, which
 * in a negative-offset timezone (e.g. America/New_York) rolls the displayed
 * calendar day back to the 19th — so a date-only key must be split and fed to
 * the local-time `Date(y, mIndex, d)` constructor instead. Returns `null` when
 * the components aren't a real date. A value carrying a time component (an ISO
 * timestamp) is left to the normal `Date` parser, since its instant is exact.
 */
function parseLocalDate(value: string): Date | null {
	const dateOnly = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(value);
	const d = dateOnly
		? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, dateOnly[3] ? Number(dateOnly[3]) : 1)
		: new Date(value);
	return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Locale-aware short calendar date (e.g. "Jun 20, 2026" / "20 juin 2026").
 *
 * The locale follows the active in-app i18n locale (the picker), read from
 * `formatLocale.ts::getActiveFormatLocale()` — the same holder `money.ts`
 * already drives `Intl.NumberFormat` off, so dates and money switch together.
 * Until a locale is actively selected the holder is `undefined`, so the
 * browser/runtime locale is used (the pre-i18n behaviour — nothing regresses).
 *
 * Returns the `placeholder` (default `—`) for a null/empty/unparseable value.
 * Read it inside a `$derived` / template so a locale switch re-renders.
 */
export function formatDate(value: string | null | undefined, placeholder = '—'): string {
	if (!value) return placeholder;
	const d = parseLocalDate(value);
	if (!d) return placeholder;
	return d.toLocaleDateString(getActiveFormatLocale(), {
		month: 'short',
		day: 'numeric',
		year: 'numeric'
	});
}

/**
 * Locale-aware short label for a reporting-period key — `YYYY-MM` (month) or
 * `YYYY-MM-DD` (day/week). Month keys render month + 2-digit year; day/week
 * keys add the day-of-month. Drives its locale off the active i18n locale,
 * the same way {@link formatDate} does. Returns the raw key unchanged if it
 * isn't a parseable date.
 */
export function formatPeriod(p: string): string {
	// month keys are YYYY-MM; day/week keys are YYYY-MM-DD.
	const isMonthKey = p.length === 7;
	const d = parseLocalDate(p);
	if (!d) return p;
	return d.toLocaleDateString(getActiveFormatLocale(), {
		month: 'short',
		day: isMonthKey ? undefined : 'numeric',
		year: '2-digit'
	});
}
