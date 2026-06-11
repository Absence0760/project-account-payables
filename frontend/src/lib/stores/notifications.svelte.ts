import { api } from '$lib/api';
import { hasToken } from '$lib/api';
import type {
	Notification,
	NotificationListResponse,
	NotificationPrefs,
} from '$lib/types/notification';

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

	async function load(opts: { append?: boolean; nextPage?: number; unreadOnly?: boolean } = {}) {
		const nextPage = opts.nextPage ?? 1;
		loading = true;
		try {
			const params = new URLSearchParams();
			params.set('page', String(nextPage));
			params.set('page_size', String(PAGE_SIZE));
			if (opts.unreadOnly) params.set('unread_only', 'true');
			const res = await api.get<NotificationListResponse>(`/api/notifications?${params}`);
			items = opts.append ? [...items, ...res.items] : res.items;
			total = res.total;
			unread = res.unread;
			page = nextPage;
		} finally {
			loading = false;
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
		try {
			const res = await api.get<{ unread: number }>('/api/notifications/unread-count');
			unread = res.unread;
		} catch {
			/* badge is non-critical — ignore transient failures */
		}
	}

	async function markRead(id: string) {
		await api.post(`/api/notifications/${id}/read`, {});
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
