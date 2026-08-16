import { api } from '$lib/api';
import { hasToken } from '$lib/api';
import type {
	Notification,
	NotificationListResponse,
	NotificationPrefs,
} from '$lib/types/notification';
import { appendUnique } from '$lib/utils/pagination';
import { createRequestSequencer } from '$lib/utils/requestSequence';

const PAGE_SIZE = 20;
const POLL_INTERVAL_MS = 60_000;

function createNotificationStore() {
	let items = $state<Notification[]>([]);
	let total = $state(0);
	let unread = $state(0);
	let page = $state(1);
	let loading = $state(false);
	let prefs = $state<NotificationPrefs | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	let hasMore = $derived(items.length < total);

	// Two sequencers, deliberately not one. `listSequence` guards the list
	// `load()` (a filter flip or a load-more resolving out of order), and
	// `countSequence` guards the 60s badge poll. Sharing a single counter would
	// let a poll tick landing mid-load mark that load un-committable and blank
	// the list — the poll and the list are independent reads of the same server
	// state, not successive versions of one request. `markRead`/`markAllRead`
	// supersede BOTH: each writes `unread`, so either could restore the pre-edit
	// count. See `frontend/CLAUDE.md` § Sequencing list fetches.
	const listSequence = createRequestSequencer();
	const countSequence = createRequestSequencer();

	async function load(opts: { append?: boolean; nextPage?: number; unreadOnly?: boolean } = {}) {
		const nextPage = opts.nextPage ?? 1;
		const token = listSequence.start();
		loading = true;
		try {
			const params = new URLSearchParams();
			params.set('page', String(nextPage));
			params.set('page_size', String(PAGE_SIZE));
			if (opts.unreadOnly) params.set('unread_only', 'true');
			const res = await api.get<NotificationListResponse>(`/api/notifications?${params}`);
			// Superseded by a newer load, or by a local mark-read.
			if (!listSequence.canCommit(token)) return;
			items = opts.append ? appendUnique(items, res.items) : res.items;
			total = res.total;
			unread = res.unread;
			page = nextPage;
		} finally {
			if (listSequence.isCurrentRequest(token)) loading = false;
		}
	}

	async function fetchList(opts: { unreadOnly?: boolean } = {}) {
		await load({ unreadOnly: opts.unreadOnly });
	}

	async function loadMore(opts: { unreadOnly?: boolean } = {}) {
		await load({ append: true, nextPage: page + 1, unreadOnly: opts.unreadOnly });
	}

	async function fetchUnreadCount() {
		if (!hasToken()) return;
		const token = countSequence.start();
		try {
			const res = await api.get<{ unread: number }>('/api/notifications/unread-count');
			// A poll issued before a mark-read carries the pre-edit count and
			// would flip the badge back on. Discard it.
			if (!countSequence.canCommit(token)) return;
			unread = res.unread;
		} catch {
			/* badge is non-critical — ignore transient failures */
		}
	}

	/** Retire every in-flight read of server state — both the list load and the
	 *  badge poll carry a pre-edit `unread`, and the list load a pre-edit
	 *  `read_at`. Call immediately before applying a local read-state edit. */
	function supersedeReads() {
		listSequence.supersedeInFlight();
		countSequence.supersedeInFlight();
	}

	async function markRead(id: string) {
		await api.post(`/api/notifications/${id}/read`, {});
		supersedeReads();
		// Reflect locally without a refetch.
		const target = items.find((n) => n.id === id);
		if (target && !target.read_at) {
			target.read_at = new Date().toISOString();
			unread = Math.max(0, unread - 1);
		}
		// Reassign to trigger reactivity on the array.
		items = [...items];
	}

	async function markAllRead() {
		const res = await api.post<{ updated: number }>('/api/notifications/read-all', {});
		supersedeReads();
		const now = new Date().toISOString();
		items = items.map((n) => (n.read_at ? n : { ...n, read_at: now }));
		unread = 0;
		return res.updated;
	}

	async function fetchPrefs() {
		prefs = await api.get<NotificationPrefs>('/api/notifications/preferences');
	}

	async function updatePrefs(changes: Partial<NotificationPrefs>) {
		prefs = await api.patch<NotificationPrefs>('/api/notifications/preferences', changes);
	}

	/** Start the unread-count poll for the sidebar badge. Idempotent. */
	function startPolling() {
		if (pollTimer || typeof setInterval === 'undefined') return;
		fetchUnreadCount();
		pollTimer = setInterval(fetchUnreadCount, POLL_INTERVAL_MS);
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	return {
		get items() {
			return items;
		},
		get total() {
			return total;
		},
		get unread() {
			return unread;
		},
		get loading() {
			return loading;
		},
		get hasMore() {
			return hasMore;
		},
		get prefs() {
			return prefs;
		},
		fetchList,
		loadMore,
		fetchUnreadCount,
		markRead,
		markAllRead,
		fetchPrefs,
		updatePrefs,
		startPolling,
		stopPolling,
	};
}

export const notificationStore = createNotificationStore();
