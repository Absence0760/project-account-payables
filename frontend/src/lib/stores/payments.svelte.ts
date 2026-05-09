import type { Payment } from '$lib/types/payment';
import { api } from '$lib/api';

interface PaymentListResponse {
	items: Payment[];
	total: number;
	page: number;
	page_size: number;
}

function createPaymentStore() {
	let payments = $state<Payment[]>([]);
	let loading = $state(false);
	let total = $state(0);

	async function fetch(params?: Record<string, string>) {
		loading = true;
		try {
			const query = params ? '?' + new URLSearchParams(params).toString() : '';
			const res = await api.get<PaymentListResponse>(`/api/payments${query}`);
			payments = res.items;
			total = res.total;
		} finally {
			loading = false;
		}
	}

	return {
		get all() { return payments; },
		get loading() { return loading; },
		get total() { return total; },
		fetch,
	};
}

export const paymentStore = createPaymentStore();
