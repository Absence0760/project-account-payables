# Contributing

Thanks for considering a contribution. This file describes how to work in this repo.

## Before you start

- Open an issue (or comment on an existing one) describing what you want to change. For non-trivial changes, get rough agreement on the approach before opening a PR — it's easier to redirect a sentence than a 500-line diff.
- Look at recent commits in the area you're touching for style cues.
- Per-workspace dev setup lives next to the code: `backend/CLAUDE.md`, `frontend/CLAUDE.md`, `mobile/CLAUDE.md`. First-time bootstrap is in `docs/getting-started.md`.

## Branching

Work on a feature branch off `main`:

```
git checkout -b feat/<short-slug>      # for features
git checkout -b fix/<short-slug>       # for bug fixes
git checkout -b chore/<short-slug>     # for tooling / housekeeping
git checkout -b docs/<short-slug>      # for docs only
```

Keep branches short-lived. If you're working on something that'll take more than a couple of days, rebase onto `main` regularly to avoid drift.

## Commits

Use conventional-commit-style messages:

```
feat(scope): add the thing
fix(scope): stop the crash on Y
chore(scope): bump dependency Z
docs(scope): clarify the setup steps for X
```

Scope is the area you're touching (e.g. `frontend`, `backend`, `infra`, `auth`, `audit`, etc.). Keep the subject line under 70 characters; put rationale in the body if the change isn't self-evident.

## Tests + docs are part of the change

Per the rule in `CLAUDE.md`: every PR that touches code also touches tests and docs in the same diff. If a change is genuinely untestable (config, pure styling, a one-line constant), say so in the PR description — don't skip silently.

## Running the checks locally

Each workspace has its own toolchain — run the ones that apply to your change:

```
# Backend (from backend/)
ruff check .                  # lint
ruff format --check .         # format
pytest                        # unit / integration tests

# Frontend (from frontend/)
pnpm i
pnpm check                    # svelte-check + tsc
pnpm test:e2e                 # Playwright (backend must be up on :8000)

# Mobile (from mobile/)
flutter analyze
flutter test

# Repo-wide
pre-commit run --all-files    # gitleaks + the other hygiene hooks
```

Or, if Claude Code is available: `/check` runs the relevant gates against the working diff and reports.

## Opening a PR

- Title: same conventional-commit format as commits.
- Description: fill in the `pull_request_template.md`. The "Money / data safety checklist" is there for a reason — even ticking the boxes is a useful prompt to think through each item.
- Mark as **Draft** while CI is still running; flip to ready when checks are green.
- Don't squash on merge unless you're cleaning up a noisy WIP series — preserving meaningful commits in `main` makes `git blame` more useful.

## Reviewing a PR

- Pull the branch locally, run the test suite, exercise the change manually if it's user-visible.
- The `/code-reviewer` agent (in `.claude/agents/`) can produce a first-pass review. Use it as a starting point, not a substitute for human eyes.

## Security findings

If you discover a vulnerability, **do not** open a public issue or PR. Email the maintainer directly — `SECURITY.md` has the contact and the response SLA.
