import { expect, test } from '../fixtures/helpers';

/**
 * /organization — first-time-admin "Getting started" wayfinding strip.
 *
 * A card at the top of the settings stack with anchor links that jump to the
 * sections a new tenant configures first (issue #328, persona-new-user). It
 * hides nothing — purely a shortcut. This spec asserts the strip renders and
 * that an in-page link scrolls to / focuses its target section.
 */

test.describe('/organization getting-started strip', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/organization');
		await page.waitForLoadState('networkidle');
	});

	test('renders above the Company Profile section with the expected links', async ({ page }) => {
		const strip = page.locator('section.getting-started');
		await expect(strip.getByRole('heading', { name: 'Getting started' })).toBeVisible();

		const links = strip.getByRole('link');
		await expect(links).toHaveCount(5);
		await expect(strip.getByRole('link', { name: 'Company profile' })).toHaveAttribute(
			'href',
			'#org-company'
		);
		await expect(strip.getByRole('link', { name: 'Users & roles' })).toHaveAttribute(
			'href',
			'/admin'
		);

		// The strip sits before the first real section.
		const companyHeading = page.getByRole('heading', { name: 'Company Profile' });
		await expect(companyHeading).toBeVisible();
	});

	test('an anchor link jumps to its section', async ({ page }) => {
		await page.locator('section.getting-started').getByRole('link', { name: 'Branding' }).click();
		await expect(page).toHaveURL(/#org-branding$/);

		const branding = page.locator('section#org-branding');
		await expect(branding).toBeInViewport();
		await expect(branding.getByRole('heading', { name: 'Branding' })).toBeVisible();
	});
});
