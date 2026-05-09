function createSidebarStore() {
	let collapsed = $state(false);

	return {
		get collapsed() { return collapsed; },
		toggle() { collapsed = !collapsed; },
	};
}

export const sidebar = createSidebarStore();
