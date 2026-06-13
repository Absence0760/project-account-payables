import { api } from '$lib/api';
import {
	ALL_ENTITIES,
	getStoredEntitySelection,
	setStoredEntitySelection,
} from '$lib/entity';

/**
 * Multi-entity (subsidiary) selector store — backs the sidebar entity
 * switcher. Loads the tenant's entities (`GET /api/entities`) and tracks the
 * current selection (a UUID, or {@link ALL_ENTITIES} for the consolidated
 * view). The selection persists via `$lib/entity` (tenant-scoped localStorage);
 * `api.ts` reads it on every request to set `X-Entity-ID`.
 *
 * Switching entity calls {@link select}, which persists the choice and reloads
 * the page so every store/page re-fetches scoped data — pages fetch in their
 * own `$effect`/`onMount`, not in SvelteKit `load`, so a hard reload is the
 * simplest correct way to re-scope the whole app at once.
 *
 * The switcher only renders when the tenant has more than one entity, so a
 * single-entity tenant sees exactly the pre-multi-entity UI.
 */

export interface Entity {
	id: string;
	name: string;
	slug: string;
	currency: string | null;
	is_default: boolean;
	is_active: boolean;
}

class EntityStore {
	entities = $state<Entity[]>([]);
	/** The current selection: an entity id or {@link ALL_ENTITIES}. */
	selectedId = $state<string>(getStoredEntitySelection());
	loading = $state(false);
	#loaded = false;
	#inflight: Promise<void> | null = null;

	/** True once the tenant has more than one entity — gates the switcher UI. */
	get multiEntity(): boolean {
		return this.entities.length > 1;
	}

	/** The selected Entity object, or `null` for the consolidated view. */
	get selected(): Entity | null {
		if (this.selectedId === ALL_ENTITIES) return null;
		return this.entities.find((e) => e.id === this.selectedId) ?? null;
	}

	/** Label for the current selection (switcher button text). */
	get selectedLabel(): string {
		return this.selected?.name ?? 'All entities';
	}

	async ensureLoaded(): Promise<void> {
		if (this.#loaded) return;
		if (this.#inflight) return this.#inflight;
		this.loading = true;
		this.#inflight = (async () => {
			try {
				const rows = await api.get<Entity[]>('/api/entities');
				this.entities = rows;
				// Drop a stale selection (e.g. an entity removed since last visit)
				// back to the consolidated view so requests don't 400.
				if (
					this.selectedId !== ALL_ENTITIES &&
					!rows.some((e) => e.id === this.selectedId)
				) {
					this.#applySelection(ALL_ENTITIES);
				}
				this.#loaded = true;
			} catch {
				// Non-fatal: without the list the switcher just doesn't render.
			} finally {
				this.loading = false;
				this.#inflight = null;
			}
		})();
		return this.#inflight;
	}

	/** Persist a selection without reloading (internal — used for stale reset). */
	#applySelection(id: string): void {
		this.selectedId = id;
		setStoredEntitySelection(id);
	}

	/**
	 * Switch the active entity. Persists the choice and reloads so every page
	 * re-fetches under the new scope. No-op when the selection is unchanged.
	 */
	select(id: string): void {
		if (id === this.selectedId) return;
		this.#applySelection(id);
		if (typeof window !== 'undefined') {
			window.location.reload();
		}
	}

	reset(): void {
		this.entities = [];
		this.selectedId = getStoredEntitySelection();
		this.#loaded = false;
		this.#inflight = null;
	}
}

export const entityStore = new EntityStore();
export { ALL_ENTITIES };
