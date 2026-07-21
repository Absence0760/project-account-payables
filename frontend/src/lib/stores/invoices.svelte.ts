import type { Invoice } from '$lib/types/invoice';
import { api } from '$lib/api';
import { appendUnique } from '$lib/utils/pagination';
import { createRequestSequencer } from '$lib/utils/requestSequence';

interface InvoiceListResponse {
	items: Invoice[];
	total: number;
	page: number;
	page_size: number;
}

function createInvoiceStore() {
	let invoices = $state<Invoice[]>([]);
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let statusCounts = $state<Record<string, number>>({});
	let lastParams = $state<Record<string, string>>({});

	// Sequences `fetch`/`loadMore` calls (they share one counter — whichever
	// was issued last wins) so a slow response for an earlier search/filter
	// can't land after a faster later one and clobber the list with stale
	// results.
	const fetchSequence = createRequestSequencer();

	async function fetch(params?: Record<string, string>) { // noqa: raw-fetch-in-component — store method name; routes through api.get
		const token = fetchSequence.start();
		loading = true;
		try {
			const merged = { ...(params ?? {}) };
			if (!merged.page) merged.page = '1';
			if (!merged.page_size) merged.page_size = '20';
			const query = '?' + new URLSearchParams(merged).toString();
			const res = await api.get<InvoiceListResponse>(`/api/invoices${query}`);
			if (!fetchSequence.isLatest(token)) return; // superseded by a newer fetch/loadMore
			invoices = res.items;
			total = res.total;
			page = res.page;
			lastParams = params ?? {};
		} finally {
			if (fetchSequence.isLatest(token)) loading = false;
		}
	}

	async function loadMore() {
		const token = fetchSequence.start();
		loading = true;
		try {
			const merged = { ...lastParams };
			merged.page = String(page + 1);
			merged.page_size = String(20);
			const query = '?' + new URLSearchParams(merged).toString();
			const res = await api.get<InvoiceListResponse>(`/api/invoices${query}`);
			if (!fetchSequence.isLatest(token)) return; // superseded by a newer fetch/loadMore
			invoices = appendUnique(invoices, res.items);
			total = res.total;
			page = res.page;
		} finally {
			if (fetchSequence.isLatest(token)) loading = false;
		}
	}

	async function fetchCounts() {
		try {
			// Server-side GROUP BY so the chips stay accurate past the first
			// page of results (a client-side tally over page 1 undercounted).
			const res = await api.get<{ counts: Record<string, number>; total: number }>(
				'/api/invoices/counts'
			);
			statusCounts = res.counts;
		} catch {
			// ignore — counts are non-critical
		}
	}

	async function update(id: string, changes: Partial<Invoice>) {
		const updated = await api.patch<Invoice>(`/api/invoices/${id}`, changes);
		invoices = invoices.map((inv) => (inv.id === id ? updated : inv));
	}

	/** Patch the local list cache in place, with no API call. For callers that
	 *  already mutated the invoice via a dedicated endpoint (e.g. the file
	 *  attach/replace/delete routes) and just need the cached row to reflect it. */
	function patchLocal(id: string, changes: Partial<Invoice>) {
		invoices = invoices.map((inv) => (inv.id === id ? { ...inv, ...changes } : inv));
	}

	return {
		get all() { return invoices; },
		get loading() { return loading; },
		get total() { return total; },
		get page() { return page; },
		get hasMore() { return invoices.length < total; },
		get statusCounts() { return statusCounts; },
		fetch,
		loadMore,
		fetchCounts,
		update,
		patchLocal,
	};
}

export const invoiceStore = createInvoiceStore();
