import type { Contract } from '$lib/types/contract';
import { listContracts, type ContractListParams } from '$lib/api/contracts';
import { appendUnique } from '$lib/utils/pagination';

function createContractStore() {
	let contracts = $state<Contract[]>([]);
	let loading = $state(false);
	let total = $state(0);
	let page = $state(1);
	let lastParams = $state<ContractListParams>({});

	async function fetch(params: ContractListParams = {}) { // noqa: raw-fetch-in-component — store method name; routes through listContracts → api client
		loading = true;
		try {
			const res = await listContracts({ ...params, page: 1, page_size: 20 });
			contracts = res.items;
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
			const res = await listContracts({ ...lastParams, page: page + 1, page_size: 20 });
			contracts = appendUnique(contracts, res.items);
			total = res.total;
			page = res.page;
		} finally {
			loading = false;
		}
	}

	// Replace one row in place (after a lifecycle action / edit / upload) so the
	// list reflects the change without a full refetch.
	function upsert(updated: Contract) {
		const idx = contracts.findIndex((c) => c.id === updated.id);
		if (idx === -1) {
			contracts = [updated, ...contracts];
			total += 1;
		} else {
			contracts = contracts.map((c) => (c.id === updated.id ? updated : c));
		}
	}

	function remove(id: string) {
		if (contracts.some((c) => c.id === id)) {
			contracts = contracts.filter((c) => c.id !== id);
			total = Math.max(0, total - 1);
		}
	}

	return {
		get all() { return contracts; },
		get loading() { return loading; },
		get total() { return total; },
		get page() { return page; },
		get hasMore() { return contracts.length < total; },
		fetch,
		loadMore,
		upsert,
		remove
	};
}

export const contractStore = createContractStore();
