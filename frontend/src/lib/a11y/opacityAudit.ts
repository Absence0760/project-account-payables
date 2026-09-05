/**
 * Finds CSS rules that de-emphasise a text-bearing element by fading it with
 * `opacity` instead of naming a muted colour.
 *
 * Why this needs its own scanner rather than a `cssAudit` check: `opacity` is
 * GROUP opacity, so it fades the element's whole SUBTREE. The rule that spends
 * the contrast (`tr.inactive td { opacity: 0.6 }`) therefore names no colour at
 * all — the colours it ruins belong to descendants, sometimes in other files
 * (a `<Badge>`'s calibrated `-on-tint` pair, a `--text-muted` cell). `cssAudit`
 * measures a rule against its OWN declarations, which is exactly why it read
 * that rule as harmless; and axe only sees it when a listed route happens to
 * render an inactive row. Neither guard could see the app's most-repeated
 * de-emphasis idiom.
 *
 * Measured on `--surface`, the fade is an inversion of its own intent: it was
 * survivable on the one colour that wanted dimming and catastrophic on every
 * colour that was already dim.
 *
 *     --text            @0.6 → 5.65:1   @0.5 → 4.34:1
 *     --text-muted      @0.6 → 2.77:1   @0.5 → 2.33:1
 *     a tinted <Badge>  @0.6 → 2.78–2.93:1
 *
 * So this scanner does not try to compute a ratio — it cannot know the
 * descendants' colours, and it does not need to: the answer for a text-bearing
 * element is always "use `.row-muted` / a muted token", never "pick a kinder
 * alpha". It reports the *idiom*, and the exemptions below are the whole of the
 * judgement.
 *
 * Pure — callers hand in already-read sources, exactly like `badgeAudit`, so
 * the repo-wide guard is a file walk plus an assertion.
 */

import { parseRules, type StyleSource } from './cssAudit';

export interface OpacityFadeFinding {
	/** Repo-relative path of the file the rule lives in. */
	path: string;
	/** The selector as written, e.g. `tr.inactive td:not(.actions)`. */
	selector: string;
	/** The fade, as a number in (0, 1). */
	opacity: number;
}

/**
 * Selectors naming an element that is **inactive** in the WCAG 1.4.3 sense —
 * "text or images of text that are part of an inactive user interface
 * component ... have no contrast requirement". A greyed-out submit button is
 * the canonical exempt case, and fading it is the standard way to say so.
 *
 * `busy` / `uploading` / `loading` / `pending` are here for the same reason and
 * are not a loophole: at every current call site the class is applied in the
 * same expression that sets the control's `disabled` attribute (the portal's
 * `.upload-btn.uploading` and `.resubmit-btn.busy` both wrap a
 * `<input disabled={…}>`), so the class marks a genuinely inactive control that
 * a CSS scan cannot otherwise recognise as one. If a future call site fades a
 * control that stays operable, the exemption is wrong for it — rename the class
 * rather than widening this list.
 */
const INACTIVE_SELECTOR =
	/(:disabled\b|\[disabled\b|\bdisabled\b|\[aria-disabled|\bbusy\b|\buploading\b|\bloading\b|\bpending\b)/i;

/**
 * A transient pointer / keyboard state. A `:hover` fade on a filled button is
 * the app's other use of `opacity`, and — measured — it survives: white on
 * `--accent-strong` / `--success-strong` / `--danger-strong` at `0.85`–`0.9`
 * lands between 4.72:1 and 5.10:1 over both `--bg` and `--surface`, so those
 * rules are not what this scanner is looking for.
 *
 * They are still worth keeping out by RULE rather than by allowlist, because a
 * hover fade is a distinct idiom with a distinct answer (darken the fill),
 * and lumping it in here would bury the row-de-emphasis signal under 30
 * entries nobody is meant to act on.
 */
const TRANSIENT_STATE = /:(hover|active|focus|focus-visible|focus-within|target)\b/i;

/**
 * A `@keyframes` step (`0%`, `from`, `to`, `0%, 80%, 100%`) is an animation
 * frame, not a rule that decides how something renders at rest.
 */
const KEYFRAME_STEP = /^(from|to|-?[\d.]+%)(\s*,\s*(from|to|-?[\d.]+%))*$/i;

/**
 * A pseudo-element selector (`::-webkit-calendar-picker-indicator`,
 * `::file-selector-button`). These are UA-drawn chrome, not app text.
 */
const PSEUDO_ELEMENT = /::/;

/** The rule's own `opacity` as a fraction, or `null` when it declares none. */
function fadeAmount(declarations: Array<[string, string]>): number | null {
	let fade: number | null = null;
	for (const [property, value] of declarations) {
		if (property !== 'opacity') continue;
		const raw = value.trim().replace(/\s*!important$/i, '').trim();
		const n = raw.endsWith('%') ? parseFloat(raw) / 100 : parseFloat(raw);
		// Later declaration wins. `0` is a hide, not a de-emphasis, and `1` is
		// a reset — neither is what this looks for.
		if (Number.isFinite(n) && n > 0 && n < 1) fade = n;
		else fade = null;
	}
	return fade;
}

/**
 * Every rule in `sources` that fades a plausibly-text-bearing element.
 *
 * A rule counts when it declares `0 < opacity < 1` and its selector is none of:
 * an inactive component, a transient pointer state, a keyframe step, or a
 * pseudo-element. What survives is a rule fading an element at REST, on the
 * strength of what it *is* rather than what is being done to it — which is the
 * de-emphasis idiom, and the only one whose fix is a colour token.
 *
 * Purely decorative survivors (a chart bar, a 1px divider, an `aria-hidden`
 * glyph) exist and are handled by the guard's allowlist, not here: whether an
 * element carries text is a markup question, and this module only reads CSS.
 */
export function findOpacityFadeRules(sources: StyleSource[]): OpacityFadeFinding[] {
	const findings: OpacityFadeFinding[] = [];
	for (const source of sources) {
		for (const rule of parseRules(source.css)) {
			const opacity = fadeAmount(rule.declarations);
			if (opacity === null) continue;
			if (KEYFRAME_STEP.test(rule.selector)) continue;
			if (PSEUDO_ELEMENT.test(rule.selector)) continue;
			if (TRANSIENT_STATE.test(rule.selector)) continue;
			if (INACTIVE_SELECTOR.test(rule.selector)) continue;
			findings.push({ path: source.path, selector: rule.selector, opacity });
		}
	}
	return findings;
}

/** `path` + `selector`, the stable identity of one finding. */
export function findingKey(finding: OpacityFadeFinding): string {
	return `${finding.path} {${finding.selector}}`;
}
