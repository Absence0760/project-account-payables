/**
 * Finds CSS rules that hand-roll the tinted-badge recipe instead of naming a
 * tone on `components/ui/Badge.svelte`.
 *
 * The recipe is two declarations in one rule — a translucent (or `--*-tint`)
 * background plus a paired text colour — on a badge-shaped selector. It was
 * spelled 205 times across the app in 44 distinct variants of the same five
 * tones, and every one of them passed the contrast guard: this is design-system
 * debt rather than a live defect. But it is the debt the 29 sub-4.5:1 badges
 * accumulated inside before decisions.md §30 fixed them, and a caller that
 * names a *tone* cannot spell it wrong.
 *
 * The conversion is deliberately staged. The shared tokens standardise on alpha
 * `.15`, so converting a `.1` or `.12` rule *visibly* strengthens that badge —
 * landing all of them at once would make any visual complaint unattributable.
 * That makes a ratchet the right guard: it can only be satisfied by converting,
 * never by adding, and it names the file a new hand-roll appeared in.
 *
 * Pure — callers hand in already-read sources, exactly like `cssAudit`, so the
 * repo-wide guard is a file walk plus an assertion.
 */

import { parseRules, type StyleSource } from './cssAudit';

export interface BadgeRuleFinding {
	/** Repo-relative path of the file the rule lives in. */
	path: string;
	/** The selector as written, e.g. `.badge.approved`. */
	selector: string;
	/** The background declaration's value. */
	background: string;
	/** The paired colour declaration's value. */
	color: string;
}

/**
 * Selectors that name a badge-shaped element. Deliberately broad — the point is
 * to catch the recipe wherever it is respelled, and the app has used `badge`,
 * `chip`, `pill` and `tag` for the same 12px uppercase capsule.
 */
const BADGE_SELECTOR = /\b(badge|chip|pill|tag)\b/i;

/**
 * A background that carries a tint: an `rgba()`/`hsla()` with alpha below 1, or
 * one of the palette's own `--*-tint` tokens. A flat `var(--bg)` background is
 * the untinted "no signal" case `Badge`'s `neutral` tone already covers and is
 * NOT counted — it is not the recipe, it is the absence of it.
 */
function isTintedBackground(value: string): boolean {
	if (/var\(\s*--[a-z-]*tint/i.test(value)) return true;
	const alpha = value.match(/\b(?:rgba|hsla)\([^)]*?,\s*(0?\.\d+|0|1(?:\.0+)?)\s*\)/i);
	return alpha ? Number(alpha[1]) < 1 : false;
}

/**
 * Every hand-rolled badge rule in `sources`.
 *
 * A rule counts when its selector is badge-shaped AND it sets both a tinted
 * background and a colour. Requiring the pair is what keeps the layout half of
 * a badge (`.badge { padding; border-radius; font-size }`) and a caller's
 * positioning wrapper out of the count: neither decides a colour, so neither is
 * the thing `Badge.svelte` owns.
 */
export function findHandRolledBadgeRules(sources: StyleSource[]): BadgeRuleFinding[] {
	const findings: BadgeRuleFinding[] = [];
	for (const source of sources) {
		for (const rule of parseRules(source.css)) {
			if (!BADGE_SELECTOR.test(rule.selector)) continue;
			const background = rule.declarations.find(
				([property]) => property === 'background' || property === 'background-color'
			);
			const color = rule.declarations.find(([property]) => property === 'color');
			if (!background || !color) continue;
			if (!isTintedBackground(background[1])) continue;
			findings.push({
				path: source.path,
				selector: rule.selector,
				background: background[1],
				color: color[1]
			});
		}
	}
	return findings;
}

/** Findings grouped by file, for a ratchet that can name what moved. */
export function countByFile(findings: BadgeRuleFinding[]): Record<string, number> {
	const counts: Record<string, number> = {};
	for (const finding of findings) counts[finding.path] = (counts[finding.path] ?? 0) + 1;
	return counts;
}
