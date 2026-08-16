import { describe, it, expect } from 'vitest';
import { createRequestSequencer } from './requestSequence';

describe('createRequestSequencer — fetch vs fetch', () => {
	it('lets the only in-flight request commit', () => {
		const seq = createRequestSequencer();
		const token = seq.start();
		expect(seq.canCommit(token)).toBe(true);
	});

	it('refuses a response superseded by a later request', () => {
		const seq = createRequestSequencer();
		const first = seq.start();
		const second = seq.start();
		expect(seq.canCommit(first)).toBe(false);
		expect(seq.canCommit(second)).toBe(true);
	});
});

describe('createRequestSequencer — local mutation vs in-flight fetch', () => {
	it('refuses a response issued before a local mutation was applied', () => {
		const seq = createRequestSequencer();
		const inFlight = seq.start();
		seq.supersedeInFlight();
		expect(seq.canCommit(inFlight)).toBe(false);
	});

	it('still lets a request issued AFTER the mutation commit', () => {
		const seq = createRequestSequencer();
		seq.start();
		seq.supersedeInFlight();
		const afterEdit = seq.start();
		expect(seq.canCommit(afterEdit)).toBe(true);
	});

	it('is a no-op when nothing is in flight', () => {
		const seq = createRequestSequencer();
		seq.supersedeInFlight();
		const token = seq.start();
		expect(seq.canCommit(token)).toBe(true);
	});

	it('supersedes repeatedly without stranding the next request', () => {
		const seq = createRequestSequencer();
		const stale = seq.start();
		seq.supersedeInFlight();
		seq.supersedeInFlight();
		expect(seq.canCommit(stale)).toBe(false);
		const fresh = seq.start();
		seq.supersedeInFlight();
		expect(seq.canCommit(fresh)).toBe(false);
		expect(seq.canCommit(seq.start())).toBe(true);
	});
});

describe('createRequestSequencer — isCurrentRequest (loading / error bookkeeping)', () => {
	it('stays true after a local mutation supersedes the request', () => {
		// The `finally` that clears a spinner must not read `canCommit` — a
		// local mutation would then leave `loading` stuck on forever, because
		// no newer request exists to clear it.
		const seq = createRequestSequencer();
		const token = seq.start();
		seq.supersedeInFlight();
		expect(seq.canCommit(token)).toBe(false);
		expect(seq.isCurrentRequest(token)).toBe(true);
	});

	it('goes false once a newer request is issued', () => {
		const seq = createRequestSequencer();
		const first = seq.start();
		seq.start();
		expect(seq.isCurrentRequest(first)).toBe(false);
	});
});

/**
 * End-to-end ordering proof over the exact wiring the list loaders use: a
 * request is issued, a local edit lands while it is still in flight, and the
 * request then resolves last. The edit must survive.
 */
describe('a local mutation survives a late-resolving stale fetch', () => {
	/** Mirrors `stores/invoices.svelte.ts` — a list, a sequenced loader, and a
	 *  local-mutation helper that supersedes before it edits. */
	function createList() {
		const seq = createRequestSequencer();
		let rows: Array<{ id: string; status: string }> = [];
		let loading = false;

		return {
			get rows() { return rows; },
			get loading() { return loading; },
			async load(respond: () => Promise<Array<{ id: string; status: string }>>) {
				const token = seq.start();
				loading = true;
				try {
					const items = await respond();
					if (!seq.canCommit(token)) return;
					rows = items;
				} finally {
					if (seq.isCurrentRequest(token)) loading = false;
				}
			},
			patchLocal(id: string, changes: { status: string }) {
				seq.supersedeInFlight();
				rows = rows.map((r) => (r.id === id ? { ...r, ...changes } : r));
			}
		};
	}

	it('discards the pre-edit snapshot instead of reverting the edit', async () => {
		const list = createList();

		// The list is already populated (mount fetch resolved).
		await list.load(async () => [{ id: 'inv-1', status: 'ready_for_review' }]);
		expect(list.rows).toEqual([{ id: 'inv-1', status: 'ready_for_review' }]);

		// A refresh goes out and is still in flight...
		let releaseSlowFetch!: () => void;
		const slowFetch = new Promise<void>((resolve) => (releaseSlowFetch = resolve));
		const inFlight = list.load(async () => {
			await slowFetch;
			// The server snapshot this request read predates the edit below.
			return [{ id: 'inv-1', status: 'ready_for_review' }];
		});

		// ...while the user approves the invoice from the open modal.
		list.patchLocal('inv-1', { status: 'approved' });
		expect(list.rows).toEqual([{ id: 'inv-1', status: 'approved' }]);

		// The stale fetch resolves LAST — it must not win.
		releaseSlowFetch();
		await inFlight;

		expect(list.rows).toEqual([{ id: 'inv-1', status: 'approved' }]);
		// ...and the discarded response still hands back the spinner.
		expect(list.loading).toBe(false);
	});

	it('lets a fetch issued after the edit replace the list', async () => {
		const list = createList();
		await list.load(async () => [{ id: 'inv-1', status: 'ready_for_review' }]);
		list.patchLocal('inv-1', { status: 'approved' });

		// A fresh request reads server state that already includes the edit.
		await list.load(async () => [{ id: 'inv-1', status: 'sending_to_erp' }]);
		expect(list.rows).toEqual([{ id: 'inv-1', status: 'sending_to_erp' }]);
	});

	/**
	 * The dismissal this exists to kill: "the mount fetch must have landed
	 * before there's a row to mutate, so edit/delete can't race it". True for
	 * edit and delete — false for New/Add, which is live while the FIRST GET is
	 * still out. Every store here has such a path (`upsert` prepending an
	 * unseen row, `createUser`, `createFromTemplate`).
	 */
	it('a create that prepends survives the very first load still in flight', async () => {
		const seq = createRequestSequencer();
		let rows: Array<{ id: string }> = [];

		let releaseMount!: () => void;
		const mountGate = new Promise<void>((resolve) => (releaseMount = resolve));

		const token = seq.start();
		const mountLoad = (async () => {
			await mountGate;
			// The server list this request read predates the create below.
			const items = [{ id: 'existing' }];
			if (!seq.canCommit(token)) return;
			rows = items;
		})();

		// The user hits "New" before the list has painted a single row.
		seq.supersedeInFlight();
		rows = [{ id: 'created' }, ...rows];

		releaseMount();
		await mountLoad;

		expect(rows).toEqual([{ id: 'created' }]);
	});
});

/**
 * Two independent lists loaded by two independent requests get one sequencer
 * EACH — `stores/admin.svelte.ts` (users vs roles) and
 * `stores/notifications.svelte.ts` (the list vs the 60s unread-count poll).
 * Sharing one counter would make an unrelated request mark the other's
 * in-flight response un-committable and blank it.
 */
describe('one sequencer per independent list', () => {
	it('a second list’s request does not supersede the first list’s', () => {
		const usersSeq = createRequestSequencer();
		const rolesSeq = createRequestSequencer();

		const usersToken = usersSeq.start();
		rolesSeq.start(); // a roles refresh fires while the users fetch is out

		expect(usersSeq.canCommit(usersToken)).toBe(true);
	});

	it('a local edit that touches both lists supersedes both', () => {
		// `markRead` writes `unread`, which BOTH the list load and the badge
		// poll would otherwise restore to its pre-edit value.
		const listSeq = createRequestSequencer();
		const countSeq = createRequestSequencer();

		const listToken = listSeq.start();
		const countToken = countSeq.start();

		listSeq.supersedeInFlight();
		countSeq.supersedeInFlight();

		expect(listSeq.canCommit(listToken)).toBe(false);
		expect(countSeq.canCommit(countToken)).toBe(false);
	});
});
