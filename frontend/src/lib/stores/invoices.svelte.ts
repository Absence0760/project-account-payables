import type { Invoice } from '$lib/types/invoice';
import { api } from '$lib/api';

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

	async function fetch(params?: Record<string, string>) {
		loading = true;
		try {
			const merged = { ...(params ?? {}) };
			if (!merged.page) merged.page = '1';
			if (!merged.page_size) merged.page_size = '20';
			const query = '?' + new URLSearchParams(merged).toString();
			const res = await api.get<InvoiceListResponse>(`/api/invoices${query}`);
			invoices = res.items;
			total = res.total;
			page = res.page;
			lastParams = params ?? {};
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		loading = true;
		try {
			const merged = { ...lastParams };
			merged.page = String(page + 1);
			merged.page_size = String(20);
			const query = '?' + new URLSearchParams(merged).toString();
			const res = await api.get<InvoiceListResponse>(`/api/invoices${query}`);
			invoices = [...invoices, ...res.items];
			total = res.total;
			page = res.page;
		} finally {
			loading = false;
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
	};
}

export const invoiceStore = createInvoiceStore();
