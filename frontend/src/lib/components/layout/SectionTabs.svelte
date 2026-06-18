<script lang="ts">
	import { page } from '$app/state';
	import { auth } from '$lib/stores/auth.svelte';
	import { groupForPath, sectionTabActive, visibleChildren } from '$lib/nav';

	// The grouped section (Procurement / Billing / Insights / Settings) that owns
	// the current route, if any. Top-level links (Invoices, Payments, …) return
	// null → no sub-tab bar. Children the current role can't see are dropped.
	let group = $derived(groupForPath(page.url.pathname));
	let tabs = $derived(group ? visibleChildren(group, auth.hasAnyRole.bind(auth)) : []);
</script>

<!-- Only show the bar when the section actually offers a choice. A lone
     accessible tab (e.g. a CFO's Settings = just Audit Trail) would just
     duplicate the page title, so we suppress it. -->
{#if group && tabs.length > 1}
	<nav class="section-tabs" aria-label={`${group.label} sections`}>
		<div class="section-tabs-inner">
			{#each tabs as tab (tab.href)}
				{@const active = sectionTabActive(tab, tabs, page.url)}
				<a
					href={tab.href}
					class="section-tab"
					class:active
					aria-current={active ? 'page' : undefined}
				>
					{tab.label}
				</a>
			{/each}
		</div>
	</nav>
{/if}

<style>
	/* Secondary nav strip above the page content. Full-width border, inner
	   content aligned to the same 1800px / 20px gutter as `.workspace` so the
	   tabs line up with the page title below. */
	.section-tabs {
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}

	.section-tabs-inner {
		max-width: 1800px;
		margin: 0 auto;
		padding: 0 20px;
		display: flex;
		gap: 4px;
	}

	.section-tab {
		padding: 12px 16px;
		color: var(--text-muted);
		font-size: 0.9rem;
		font-weight: 500;
		text-decoration: none;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
		white-space: nowrap;
	}

	.section-tab:hover {
		color: var(--text);
	}

	.section-tab.active {
		color: var(--accent);
		border-bottom-color: var(--accent);
	}
</style>
