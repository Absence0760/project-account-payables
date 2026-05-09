# tests-e2e/

Playwright end-to-end tests for project-account-payables. Minimal scaffold — fill in real specs as the UI lands.

## Layout

```
tests-e2e/
├── playwright.config.ts        config; webServer block boots the app's dev server
├── fixtures/
│   ├── auth.ts                 globalSetup — signs each seeded user in once
│   ├── helpers.ts              shared signIn / signOut helpers
│   └── users.ts                pinned UUIDs + emails for seeded users
├── .auth/                      gitignored — storage states written by globalSetup
└── login.spec.ts               example spec — failed-login + form-render
```

## How auth works

`fixtures/auth.ts` runs once per Playwright invocation, signs each user in via the real `/login` form, and writes the resulting Supabase / session cookies to `.auth/<user>.json`. Specs then load that storage state instead of going through the form themselves:

```ts
import { test } from '@playwright/test';
import { ADMIN_A } from './fixtures/users';

test.use({ storageState: ADMIN_A.storageStatePath });

test('admin can list invoices', async ({ page }) => {
	await page.goto('/invoices');
	// ...
});
```

If a spec needs the unauthenticated state (e.g. login.spec.ts), override:

```ts
test.use({ storageState: { cookies: [], origins: [] } });
```

## Running

These commands assume `@playwright/test` is installed at the project root and a `test:e2e` script exists in `package.json`. Wire those up when the project lands on a stack:

```bash
# install once
npm install -D @playwright/test
npx playwright install chromium

# run
npx playwright test --config=tests-e2e/playwright.config.ts
# or, if package.json has a script wired up:
npm run test:e2e
```

## What needs adapting

The scaffold makes assumptions that any AP project would tweak:

1. **`webServer.command` and `webServer.url`** in `playwright.config.ts` — point at the project's actual dev server.
2. **Selectors in `fixtures/helpers.ts`** — `signIn` assumes inputs typed `email`/`password` and a submit button. Match whatever the login form ends up using.
3. **Post-login redirect pattern in `fixtures/auth.ts`** — `waitForURL(/\/(dashboard|invoices|home)$/)` is a guess; tighten once the app's routing exists.
4. **`fixtures/users.ts`** — UUIDs are placeholders. Replace with real seed UUIDs once the seed file lands. Two tenants × three roles is the minimum to assert tenant isolation + role gates.
5. **One-test scaffold (`login.spec.ts`)** — copy this as the template for subsequent specs. The reference `project-running` repo has 40+ specs covering feeds, lists, cross-user mutations, etc. — useful patterns to mine when you write the AP equivalents (invoice list, vendor management, approval routing, payment posting).
