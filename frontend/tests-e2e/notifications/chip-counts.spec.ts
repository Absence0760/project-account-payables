/**
 * Regression guard: the `/notifications` chips and the pagination footer count
 * two DIFFERENT sets, and must come from two different fields.
 *
 * The store used to write the list envelope's `total` into one field — but that
 * number counts whatever the request was FILTERED to, so with `unread_only=true`
 * it IS the unread count. The All chip then echoed the Unread chip (both "3" on
 * a 5-notification inbox) and one of the two was simply mislabelled. The same
 * single field made `markAllRead` leave the footer claiming "Showing all 3
 * notifications" for a filtered set nothing was left in.
 *
 * The fix keeps `inboxTotal` (whole inbox — the All chip) apart from
 * `filteredTotal` (the active filter's set — the footer + Load more).
 */
import { expect, signInAndWait, test } from '../fixtures/helpers';
import { clearNotifications, currentUserId, orgId, seedNotifications } from './seed';

type Page = import('@playwright/test').Page;

/** A chip's `.count` badge. Chips render as `<button>` with the count inside. */
function chipCount(page: Page, label: 'All' | 'Unread') {
	return page.getByRole('button', { name: new RegExp(`^${label}\\b`) }).locator('.count');
}

test.describe('notification center — chip counts vs footer', () => {
	test('the All chip keeps counting the whole inbox while the Unread filter is on', async ({
		page
	}) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		const org = orgId();
		clearNotifications(me);
		// 5 in the inbox, only 3 of them unread — so "whole set" and "unread"
		// are different numbers and a single field can't stand in for both.
		seedNotifications(me, org, 2, { read: true, title: 'E2E read notification' });
		seedNotifications(me, org, 3, { title: 'E2E unread notification' });

		await page.goto('/notifications');

		// Unfiltered: All counts everything, Unread counts the unread subset.
		await expect(chipCount(page, 'All')).toHaveText('5');
		await expect(chipCount(page, 'Unread')).toHaveText('3');
		await expect(page.locator('.load-more-end')).toHaveText('Showing all 5 notifications');

		await page.getByRole('button', { name: /^Unread\b/ }).click();

		// The regression: All must STILL be 5. Before the fix it flipped to 3 —
		// the unread-filtered response's `total` — so both chips read the same.
		await expect(chipCount(page, 'Unread')).toHaveText('3');
		await expect(chipCount(page, 'All')).toHaveText('5');
		// ...while the footer describes the FILTERED set, which really is 3.
		await expect(page.locator('.load-more-end')).toHaveText('Showing all 3 notifications');
		await expect(page.locator('tbody tr.clickable')).toHaveCount(3);

		clearNotifications(me);
	});

	test('mark-all-read under the Unread filter leaves both chips and the footer truthful', async ({
		page
	}) => {
		await signInAndWait(page);
		const me = await currentUserId(page);
		const org = orgId();
		clearNotifications(me);
		seedNotifications(me, org, 2, { read: true, title: 'E2E read notification' });
		seedNotifications(me, org, 3, { title: 'E2E unread notification' });

		await page.goto('/notifications');
		await expect(chipCount(page, 'All')).toHaveText('5');

		await page.getByRole('button', { name: /^Unread\b/ }).click();
		await expect(page.locator('.load-more-end')).toHaveText('Showing all 3 notifications');

		await page.getByRole('button', { name: 'Mark all read' }).click();

		// Nothing matches the Unread filter any more, so the table says so...
		await expect(page.getByTestId('table-empty')).toHaveText('No unread notifications.');
		// ...and the footer must NOT still claim "Showing all 3" for a set no
		// row is in. `filteredTotal` is 0, so no footer is rendered at all.
		await expect(page.locator('.load-more-end')).toHaveCount(0);

		// Marking read doesn't delete anything: the inbox is still 5, and the
		// unread tally is now genuinely 0.
		await expect(chipCount(page, 'Unread')).toHaveText('0');
		await expect(chipCount(page, 'All')).toHaveText('5');

		// Back on All, every one of the 5 rows is still there and readable.
		await page.getByRole('button', { name: /^All\b/ }).click();
		await expect(page.locator('tbody tr.clickable')).toHaveCount(5);
		await expect(page.locator('.load-more-end')).toHaveText('Showing all 5 notifications');

		clearNotifications(me);
	});
});
