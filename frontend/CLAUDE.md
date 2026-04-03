# Project Overview

This is a SvelteKit web template deployed to GitHub Pages.

## Stack

- **Framework**: SvelteKit 2 with Svelte 5 (runes/next)
- **Language**: TypeScript
- **Package manager**: pnpm
- **Adapters**: `@sveltejs/adapter-static` (GitHub Pages), `@sveltejs/adapter-vercel` (Vercel)
- **Styling**: normalize.css + custom CSS in `src/app.css`
- **Icons**: unplugin-icons with `@iconify-json/material-symbols`
- **Component explorer**: Storybook 8
- **Markdown**: mdsvex

## Folder Structure

```
src/
  routes/         # SvelteKit file-based routes
    +layout.svelte
    +page.svelte
  app.css         # Global styles
  app.d.ts        # App-level TypeScript declarations
.github/
  workflows/
    deploy.yml    # Builds and deploys to GitHub Pages on push to main
    claude.yml    # Claude Code automation (this workflow)
```

## Development

```bash
pnpm i          # Install dependencies
pnpm dev        # Dev server on :7777
pnpm build      # Production build
pnpm preview    # Preview build on :8888
pnpm check      # Type-check
pnpm storybook  # Storybook on :9999
```

## Conventions

- Use Svelte 5 runes syntax (`$state`, `$derived`, `$effect`, `$props`) — not the legacy options API
- TypeScript throughout; `lang="ts"` on all `<script>` blocks
- Prefer `@sveltejs/adapter-static` for GitHub Pages output (output dir: `build/`)
- `BASE_PATH` env var is set to `/<repo-name>` during CI builds for correct asset paths
- Component stories live alongside components as `*.stories.svelte`

## Deployment

- **GitHub Pages**: push to `main` triggers `.github/workflows/deploy.yml`, which builds and deploys automatically
- The `build/.nojekyll` file is created at build time to bypass Jekyll processing

## Pull Request Guidelines

- Target branch: `main`
- Keep PRs focused; one feature or fix per PR
- Draft PRs are fine for work-in-progress
