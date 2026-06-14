import type { Expense } from '$lib/types/expense';
import { listExpenses, type ExpenseListParams } from '$lib/api/expenses';

function createExpenseStore() {
	let expenses = $state<Expense[]>([]);
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let lastParams = $state<ExpenseListParams>({});

	async function fetch(params: ExpenseListParams = {}) { // noqa: raw-fetch-in-component — store method name; routes through listExpenses → api client
		loading = true;
		try {
			const res = await listExpenses({ ...params, page: 1, page_size: 20 });
			expenses = res.items;
			total = res.total;
			page = res.page;
			lastParams = params;
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		loading = true;
		try {
			const res = await listExpenses({ ...lastParams, page: page + 1, page_size: 20 });
			expenses = [...expenses, ...res.items];
			total = res.total;
			page = res.page;
		} finally {
			loading = false;
		}
	}

	// Replace one row in place (after an edit / receipt upload / GL recode) so
	// the list reflects the change without a full refetch.
	function upsert(updated: Expense) {
		const idx = expenses.findIndex((e) => e.id === updated.id);
		if (idx === -1) {
			expenses = [updated, ...expenses];
			total += 1;
		} else {
			expenses = expenses.map((e) => (e.id === updated.id ? updated : e));
		}
	}

	function remove(id: string) {
		if (expenses.some((e) => e.id === id)) {
			expenses = expenses.filter((e) => e.id !== id);
			total = Math.max(0, total - 1);
		}
	}

	return {
		get all() {
			return expenses;
		},
		get loading() {
			return loading;
		},
		get total() {
			return total;
		},
		get page() {
			return page;
		},
		get hasMore() {
			return expenses.length < total;
		},
		fetch,
		loadMore,
		upsert,
		remove
	};
}

export const expenseStore = createExpenseStore();
