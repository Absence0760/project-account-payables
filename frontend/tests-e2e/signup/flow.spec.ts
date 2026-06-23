import type { Page } from '@playwright/test';

import { API_BASE, expect, NO_TENANT_BASE, test } from '../fixtures/helpers';
import { SERVICES, skipUnlessReachable } from '../fixtures/services';

/**
 * Self-service signup — the full new-user journey, driven through the UI.
 *
 * Three regimes:
 *  1. Form validation + the "check your email" success state — runs anywhere
 *     (no email delivery needed; /start sends via whatever adapter is configured).
 *  2. The /verify page's error states (no token, invalid/expired token) — also
 *     runs anywhere; we drive it with bogus tokens.
 *  3. The full happy path landing → signup → verification email → /verify →
 *     welcome email (temp password) → first sign-in → forced password change →
 *     dashboard. Gated on Mailpit (the real outbound sink); ALSO needs the
 *     backend on the `smtp` adapter pointed at Mailpit:
 *        pnpm mail:up && AP_EMAIL_PROVIDER=smtp AP_SMTP_PORT=1025 pnpm dev:backend
 *     If the backend is on `console` the verification email never arrives and
 *     the happy-path test fails loudly (a setup error, never a silent pass).
 *
 * Signup + verify render only on the root (no-tenant) origin, so these specs
 * pin baseURL to NO_TENANT_BASE and start unauthenticated.
 */

const MAILPIT = 'http://localhost:8025';

interface MailpitMessage {
	ID: string;
	Subject: string;
	To: Array<{ Address: string }>;
}

async function messagesTo(page: Page, email: string): Promise<MailpitMessage[]> {
	const resp = await page.request.get(`${MAILPIT}/api/v1/search`, {
		params: { query: `to:${email}` }
	});
	if (!resp.ok()) return [];
	return ((await resp.json()) as { messages: MailpitMessage[] }).messages;
}

async function messageText(page: Page, id: string): Promise<string> {
	const resp = await page.request.get(`${MAILPIT}/api/v1/message/${id}`);
	return ((await resp.json()) as { Text: string }).Text;
}

/** Wait for a message with the given subject to land, return its plaintext body. */
async function waitForEmail(page: Page, email: string, subjectPrefix: string): Promise<string> {
	let id: string | null = null;
	await expect
		.poll(
			async () => {
				const msgs = await messagesTo(page, email);
				const hit = msgs.find((m) => m.Subject.startsWith(subjectPrefix));
				id = hit?.ID ?? null;
				return id;
			},
			{ timeout: 15_000 }
		)
		.not.toBeNull();
	return messageText(page, id!);
}

test.use({ storageState: { cookies: [], origins: [] } });

// The consent banner is position:fixed at the bottom of the viewport with
// z-index 10000. On the signup/verify pages (unauthenticated, empty storage
// state) the banner would overlap the submit button — on Ubuntu CI the form
// renders slightly taller than on macOS/Fedora due to font metrics, pushing
// the button centre below the banner's top edge and making Playwright's
// click() fail with a 30 s intercept timeout. Suppress the banner for all
// tests in this file by recording the consent choice before each navigation;
// no test here is verifying the consent banner itself.
test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => {
		try {
			localStorage.setItem('ap_consent_choice', 'accepted');
		} catch {
			// about:blank — ignore
		}
	});
});

// ───────────────────────────────────────────────────────────────────────────
// 1. Signup form — validation + success state (no email delivery needed)
// ───────────────────────────────────────────────────────────────────────────

test.describe('/signup — form', () => {
	test.use({ baseURL: NO_TENANT_BASE });

	async function fillForm(
		page: Page,
		{ company, slug, name, email }: { company: string; slug: string; name: string; email: string }
	) {
		await page.getByLabel('Company name').fill(company);
		await page.getByPlaceholder('acme').fill(slug);
		await page.getByLabel('Your name').fill(name);
		await page.getByLabel('Email').fill(email);
	}

	test('submitting a valid form shows the "check your email" state', async ({ page }) => {
		const slug = `e2eform${Date.now().toString().slice(-9)}`;
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');

		await fillForm(page, {
			company: 'Form E2E Co',
			slug,
			name: 'Form Admin',
			email: `${slug}@example.com`
		});
		// Wait for the debounced slug-check to clear so submit is enabled.
		await expect(page.locator('small.hint.ok')).toBeVisible({ timeout: 5_000 });

		await page.getByRole('button', { name: 'Send verification email' }).click();

		await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible({
			timeout: 10_000
		});
		// The form is gone; next-step guidance is shown.
		await expect(page.getByText(/click the link in that email/i)).toBeVisible();
	});

	test('a taken slug blocks submission with an inline reason', async ({ page }) => {
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');
		await page.getByPlaceholder('acme').fill('acme'); // seeded tenant
		await expect(page.locator('small.hint.bad')).toBeVisible({ timeout: 5_000 });
		await expect(
			page.getByRole('button', { name: 'Send verification email' })
		).toBeDisabled();
	});

	test('the email field enforces a valid address before submit', async ({ page }) => {
		const slug = `e2email${Date.now().toString().slice(-9)}`;
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');
		await fillForm(page, {
			company: 'Bad Email Co',
			slug,
			name: 'Nope',
			email: 'not-an-email'
		});
		await expect(page.locator('small.hint.ok')).toBeVisible({ timeout: 5_000 });
		await page.getByRole('button', { name: 'Send verification email' }).click();

		// type="email" keeps us on the form; the success state never appears.
		await expect(page.getByRole('heading', { name: 'Check your email' })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Send verification email' })).toBeVisible();
	});
});

// ───────────────────────────────────────────────────────────────────────────
// 2. /verify — error states (no email needed)
// ───────────────────────────────────────────────────────────────────────────

test.describe('/verify — error states', () => {
	test.use({ baseURL: NO_TENANT_BASE });

	test('no token in the URL surfaces an error', async ({ page }) => {
		await page.goto('/verify');
		await expect(page.getByRole('heading', { name: 'Something went wrong' })).toBeVisible({
			timeout: 10_000
		});
		await expect(page.getByText(/no verification token/i)).toBeVisible();
		await expect(page.getByRole('link', { name: 'Start over' })).toBeVisible();
	});

	test('an invalid/expired token surfaces the uniform error', async ({ page }) => {
		await page.goto('/verify?token=' + 'x'.repeat(40));
		await expect(page.getByRole('heading', { name: 'Something went wrong' })).toBeVisible({
			timeout: 10_000
		});
		// Backend returns the uniform 410 message (no 404-vs-410 enumeration).
		await expect(page.getByText(/invalid or has expired/i)).toBeVisible();
	});
});

// ───────────────────────────────────────────────────────────────────────────
// 3. Full happy path — UI + real email (Mailpit) end to end
// ───────────────────────────────────────────────────────────────────────────

test.describe('signup happy path (UI + Mailpit)', () => {
	test.use({ baseURL: NO_TENANT_BASE });

	test.beforeEach(async () => {
		await skipUnlessReachable(SERVICES.mailpit);
	});

	test('new user: form → verify email → first sign-in → change password → dashboard', async ({
		page
	}) => {
		const slug = `e2eflow${Date.now().toString().slice(-9)}`;
		const email = `${slug}@example.com`;

		// 1. Fill + submit the signup form through the UI.
		await page.goto('/signup');
		await page.waitForLoadState('networkidle');
		await page.getByLabel('Company name').fill('Happy Path Co');
		await page.getByPlaceholder('acme').fill(slug);
		await page.getByLabel('Your name').fill('Happy Admin');
		await page.getByLabel('Email').fill(email);
		await expect(page.locator('small.hint.ok')).toBeVisible({ timeout: 5_000 });
		await page.getByRole('button', { name: 'Send verification email' }).click();
		await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible({
			timeout: 10_000
		});

		// 2. Pull the verification link out of the email and open it (the user clicks it).
		const verifyBody = await waitForEmail(page, email, 'Verify your Account Payables');
		const verifyLink = verifyBody.match(/\/verify\?token=[A-Za-z0-9_-]+/);
		expect(verifyLink, 'verify link in email').toBeTruthy();

		await page.goto(verifyLink![0]);
		await expect(page.getByRole('heading', { name: 'Your workspace is ready' })).toBeVisible({
			timeout: 20_000
		});

		// 3. Pull the temp password out of the welcome email.
		const welcomeBody = await waitForEmail(page, email, 'Your Account Payables workspace');
		const pwMatch = welcomeBody.match(/Password:\s*(\S+)/);
		expect(pwMatch, 'temp password in welcome email').toBeTruthy();
		const tempPassword = pwMatch![1];

		// 4. Sign in at the new tenant with the temp password.
		await page.goto(`http://${slug}.localhost:7777/login`);
		await page.waitForLoadState('networkidle');
		await page.locator('input[type="email"]').fill(email);
		await page.locator('input[type="password"]').fill(tempPassword);
		await page.locator('form button[type="submit"]').click();

		// 5. must_change_password forces the change-password screen.
		await expect(page).toHaveURL(/\/change-password/, { timeout: 15_000 });

		// 6. Set a real password and land in the workspace.
		const newPassword = 'Sup3rSecret!newpw';
		await page.getByLabel('Current password').fill(tempPassword);
		await page.getByLabel('New password', { exact: true }).fill(newPassword);
		await page.getByLabel('Confirm new password').fill(newPassword);
		await page.locator('form button[type="submit"]').click();

		// Lands on the authenticated dashboard (no longer on change-password/login).
		await expect(page).toHaveURL(/:7777\/?$/, { timeout: 15_000 });
	});
});
