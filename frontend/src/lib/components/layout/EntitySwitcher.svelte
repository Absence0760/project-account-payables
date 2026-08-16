<script lang="ts">
	import { entityStore, ALL_ENTITIES } from '$lib/stores/entity.svelte';

	let { collapsed = false }: { collapsed?: boolean } = $props();

	let open = $state(false);
	let triggerBtn = $state<HTMLButtonElement | null>(null);

	// Default entity first, then the rest alphabetically (matches the backend
	// list order); "All entities" is rendered as a fixed first option.
	let entities = $derived(entityStore.entities);
	let selectedId = $derived(entityStore.selectedId);
	let label = $derived(entityStore.selectedLabel);

	function choose(id: string) {
		open = false;
		entityStore.select(id); // persists + reloads when changed
	}

	// Esc closes the entity menu and restores focus to its trigger, matching the
	// backdrop-click dismissal — so a keyboard user isn't trapped in the open menu.
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && open) {
			open = false;
			triggerBtn?.focus();
		}
	}
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#if entityStore.multiEntity}
	<div class="entity-switcher">
		{#if open}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div class="entity-backdrop" onclick={() => (open = false)} onkeydown={() => {}}></div>
			<div class="entity-menu" role="listbox" aria-label="Select entity">
				<button
					class="entity-option"
					class:selected={selectedId === ALL_ENTITIES}
					role="option"
					aria-selected={selectedId === ALL_ENTITIES}
					onclick={() => choose(ALL_ENTITIES)}
				>
					All entities
					<span class="entity-option-sub">Consolidated</span>
				</button>
				{#each entities as e (e.id)}
					<button
						class="entity-option"
						class:selected={selectedId === e.id}
						role="option"
						aria-selected={selectedId === e.id}
						onclick={() => choose(e.id)}
					>
						{e.name}
						{#if e.is_default}<span class="entity-option-sub">Default</span>{/if}
					</button>
				{/each}
			</div>
		{/if}
		<button
			bind:this={triggerBtn}
			class="entity-btn"
			class:collapsed
			title={collapsed ? `Entity: ${label}` : ''}
			aria-haspopup="listbox"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			<span class="entity-icon" aria-hidden="true">
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/><path d="M9 9v.01"/><path d="M9 12v.01"/><path d="M9 15v.01"/></svg>
			</span>
			{#if !collapsed}
				<span class="entity-text">
					<span class="entity-name">{label}</span>
					<span class="entity-hint">Entity</span>
				</span>
				<svg class="entity-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class:flipped={open}><polyline points="6 9 12 15 18 9"/></svg>
			{/if}
		</button>
	</div>
{/if}

<style>
	.entity-switcher {
		position: relative;
		margin: 0 0 10px;
	}

	.entity-btn {
		display: flex;
		align-items: center;
		gap: 10px;
		width: 100%;
		padding: 8px 10px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		cursor: pointer;
		font-family: inherit;
		transition: all 0.12s;
	}

	.entity-btn:hover {
		border-color: var(--accent);
	}

	.entity-btn.collapsed {
		justify-content: center;
		padding: 8px 0;
		border-color: transparent;
	}

	.entity-icon {
		display: grid;
		place-items: center;
		flex-shrink: 0;
		width: 18px;
		height: 18px;
		color: var(--accent);
	}

	.entity-text {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		overflow: hidden;
		flex: 1;
		min-width: 0;
	}

	.entity-name {
		font-size: 0.85rem;
		font-weight: 600;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 100%;
	}

	.entity-hint {
		font-size: 0.65rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-muted);
	}

	.entity-caret {
		flex-shrink: 0;
		color: var(--text-muted);
		transition: transform 0.2s ease;
	}

	.entity-caret.flipped {
		transform: rotate(180deg);
	}

	.entity-backdrop {
		position: fixed;
		inset: 0;
		z-index: 60;
	}

	.entity-menu {
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		margin-top: 6px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 8px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
		padding: 6px;
		z-index: 61;
		display: flex;
		flex-direction: column;
		gap: 2px;
		max-height: 320px;
		overflow-y: auto;
	}

	.entity-option {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		width: 100%;
		padding: 8px 10px;
		border-radius: 5px;
		border: none;
		background: none;
		color: var(--text);
		font-size: 0.85rem;
		font-weight: 500;
		text-align: left;
		cursor: pointer;
		font-family: inherit;
		transition: background 0.12s;
	}

	.entity-option:hover {
		background: rgba(99, 140, 255, 0.08);
	}

	.entity-option.selected {
		background: rgba(99, 140, 255, 0.12);
		color: var(--accent);
	}

	.entity-option-sub {
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted);
		flex-shrink: 0;
	}
</style>
