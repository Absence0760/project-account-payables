<script lang="ts">
	/**
	 * The "there is genuinely nothing here yet, and here is the one thing to
	 * do about it" block — a first-run / zero-data affordance, not a filtered
	 * "no matches" line.
	 *
	 * Callers MUST keep the four empty states distinct (frontend/CLAUDE.md
	 * § Data tables): render this ONLY for the genuinely-empty-and-unfiltered
	 * case. `loading`, `errored`, and "a filter matched nothing" keep their own
	 * copy (a plain message / retry block) — this component is not for them.
	 *
	 * i18n-agnostic like `FieldWarning` / `SecretReveal`: every string is
	 * passed in already-localized. The optional action renders as a `<button>`
	 * (pass `onaction`) or an `<a>` (pass `actionHref`); omit both for a
	 * message-only state.
	 */
	let {
		icon,
		heading,
		description,
		actionLabel,
		onaction,
		actionHref,
		testId
	}: {
		/** A single emoji, shown large and decorative. */
		icon?: string;
		heading: string;
		description?: string;
		actionLabel?: string;
		onaction?: () => void;
		actionHref?: string;
		/** `data-testid` for an e2e selector; the class is Svelte-scoped. */
		testId?: string;
	} = $props();

	const showAction = $derived(!!actionLabel && (!!onaction || !!actionHref));
</script>

<div class="empty-state" data-testid={testId}>
	{#if icon}
		<span class="empty-state-icon" aria-hidden="true">{icon}</span>
	{/if}
	<p class="empty-state-heading">{heading}</p>
	{#if description}
		<p class="empty-state-description">{description}</p>
	{/if}
	{#if showAction}
		{#if actionHref}
			<a class="btn-primary empty-state-action" href={actionHref}>{actionLabel}</a>
		{:else}
			<button type="button" class="btn-primary empty-state-action" onclick={onaction}>
				{actionLabel}
			</button>
		{/if}
	{/if}
</div>

<style>
	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		text-align: center;
		gap: 8px;
		padding: 48px 20px;
	}
	.empty-state-icon {
		font-size: 2rem;
		line-height: 1;
		margin-bottom: 4px;
	}
	.empty-state-heading {
		margin: 0;
		font-size: 0.95rem;
		font-weight: 600;
		color: var(--text);
	}
	.empty-state-description {
		margin: 0;
		max-width: 30rem;
		font-size: 0.85rem;
		line-height: 1.5;
		color: var(--text-muted);
	}
	.empty-state-action {
		margin-top: 8px;
		text-decoration: none;
	}
</style>
