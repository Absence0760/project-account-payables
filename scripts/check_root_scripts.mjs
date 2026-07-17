#!/usr/bin/env node
// Validates the root package.json scripts block (estate format — see root CLAUDE.md
// "Root package.json scripts — estate format"). Plain node, no deps.
//
// Checks:
//   1. Divider keys parse: every "//"-prefixed key matches `//-- <group> --` and
//      carries a non-empty one-line description.
//   2. Grouping: the first scripts entry is a divider (no ungrouped leading entries,
//      which also means no ungrouped entries at all) and no divider heads an empty group.
//   3. Every `cd <dir>` (including subshell `(cd <dir> && ...)`) references an existing
//      directory under the repo root.
//   4. Every `pnpm run <script>` self-reference resolves to a real root script.
//   5. Every `pnpm <script>` run inside a `cd <workspace>` command resolves to a script
//      in that workspace's package.json (reserved pnpm verbs and `pnpm exec` skipped).
//   6. Every `python <file>.py` / `node <file>.mjs|.js` / playwright `--config=<file>`
//      target exists (resolved relative to the command's `cd` dir, if any).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pkgPath = path.join(rootDir, 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
const scripts = pkg.scripts ?? {};
const errors = [];

const isDivider = (name) => name.startsWith('//');
const DIVIDER_RE = /^\/\/-- \S.* --$/;

// --- 1 + 2: divider format and grouping ---------------------------------------------
const names = Object.keys(scripts);
if (names.length === 0) {
	errors.push('scripts block is empty');
} else if (!isDivider(names[0])) {
	errors.push(`first scripts entry "${names[0]}" is not a "//-- <group> --" divider — every script must live under a group`);
}
for (let i = 0; i < names.length; i++) {
	const name = names[i];
	if (!isDivider(name)) continue;
	if (!DIVIDER_RE.test(name)) {
		errors.push(`divider key "${name}" does not match the "//-- <group> --" format`);
	}
	const desc = scripts[name];
	if (typeof desc !== 'string' || desc.trim() === '') {
		errors.push(`divider "${name}" has an empty description — it must carry a one-line description`);
	}
	if (i === names.length - 1 || isDivider(names[i + 1])) {
		errors.push(`divider "${name}" heads an empty group`);
	}
}

// --- helpers -------------------------------------------------------------------------
const childPkgCache = new Map();
function loadChildPkg(dir) {
	if (childPkgCache.has(dir)) return childPkgCache.get(dir);
	const childPath = path.join(rootDir, dir, 'package.json');
	const json = fs.existsSync(childPath) ? JSON.parse(fs.readFileSync(childPath, 'utf8')) : null;
	childPkgCache.set(dir, json);
	return json;
}

const RESERVED_PNPM_VERBS = new Set([
	'install', 'i', 'add', 'remove', 'update', 'exec', 'dlx',
	'run', 'test', 'start', 'build', 'publish', 'pack', 'audit',
	'list', 'ls', 'why', 'outdated', 'config', 'store', 'recursive',
	'rebuild', 'prune', 'link', 'unlink', 'import', 'fetch',
]);

// --- 3–6: per-script target validation -----------------------------------------------
for (const [name, cmd] of Object.entries(scripts)) {
	if (isDivider(name)) continue;

	// 3. `cd <dir>` (also inside `(cd <dir> && ...)` subshells) must reference a real dir.
	const cdDirs = [...cmd.matchAll(/(?:^|&&\s*|\(\s*)cd\s+([^\s'")]+)/g)].map((m) => m[1]);
	for (const dir of cdDirs) {
		if (!fs.existsSync(path.join(rootDir, dir))) {
			errors.push(`${name}: missing directory ${dir}`);
		}
	}
	// The dir the non-subshell tail of the command runs in (this repo's scripts use a
	// single leading `cd <dir> &&`); subshell-only orchestrators keep the root.
	const cwdDir = /^cd\s/.test(cmd) ? cdDirs[0] : null;

	// 4. `pnpm run <script>` self-references must resolve to a root script.
	for (const m of cmd.matchAll(/pnpm\s+run\s+(\S+)/g)) {
		const target = m[1];
		if (cwdDir) {
			const child = loadChildPkg(cwdDir);
			if (child && !(target in (child.scripts ?? {}))) {
				errors.push(`${name}: ${cwdDir}/package.json has no "${target}" script`);
			}
		} else if (!(target in scripts)) {
			errors.push(`${name}: "pnpm run ${target}" does not resolve to a root script`);
		}
	}

	// 5. `cd <workspace> && pnpm <script>` must resolve in the workspace package.json.
	if (cwdDir) {
		for (const m of cmd.matchAll(/(?<!exec\s.*)pnpm\s+(?!run\s|exec\s|-)(\S+)/g)) {
			const target = m[1];
			if (RESERVED_PNPM_VERBS.has(target)) continue;
			const child = loadChildPkg(cwdDir);
			if (!child) {
				errors.push(`${name}: missing ${cwdDir}/package.json`);
			} else if (!(target in (child.scripts ?? {}))) {
				errors.push(`${name}: ${cwdDir}/package.json has no "${target}" script`);
			}
		}
	}

	// 6. Interpreter / config file targets must exist. Resolved relative to the command's
	// cd dir when there is one; orchestrators with per-subshell `(cd <dir> && ...)` legs
	// accept a match against any of the command's cd dirs.
	const candidateDirs = cwdDir
		? [path.join(rootDir, cwdDir)]
		: [rootDir, ...cdDirs.map((d) => path.join(rootDir, d))];
	const fileRefs = [
		...[...cmd.matchAll(/python\s+([^\s'"]+\.py)/g)].map((m) => m[1]),
		...[...cmd.matchAll(/node\s+([^\s'"]+\.(?:mjs|cjs|js))/g)].map((m) => m[1]),
		...[...cmd.matchAll(/--config=([^\s'"]+)/g)].map((m) => m[1]),
	];
	for (const file of fileRefs) {
		if (!candidateDirs.some((dir) => fs.existsSync(path.join(dir, file)))) {
			errors.push(`${name}: missing file ${cwdDir ? `${cwdDir}/` : ''}${file}`);
		}
	}
}

if (errors.length) {
	console.error(`Root scripts validation failed (${errors.length} issue${errors.length === 1 ? '' : 's'}):`);
	for (const e of errors) console.error(`  - ${e}`);
	process.exit(1);
}
const scriptCount = Object.keys(scripts).filter((n) => !isDivider(n)).length;
const groupCount = Object.keys(scripts).filter(isDivider).length;
console.log(`Root scripts validation passed (${scriptCount} scripts in ${groupCount} groups).`);
