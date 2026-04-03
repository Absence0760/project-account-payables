import type { Invoice, InvoiceStatus } from '$lib/types/invoice';

// Sample PDF for demo — replace with real S3/MinIO URLs when backend is wired up
const SAMPLE_PDF = '/sample-invoice.pdf';

const MOCK_INVOICES: Invoice[] = [
	{ id: '1', vendor: 'Acme Corp', invoice_number: 'INV-2024-001', amount: 12500.00, currency: 'USD', due_date: '2026-04-15', status: 'new', po_number: 'PO-1001', description: 'Office supplies Q1', created_at: '2026-03-28', file_url: SAMPLE_PDF },
	{ id: '2', vendor: 'TechFlow Inc', invoice_number: 'INV-2024-002', amount: 45000.00, currency: 'USD', due_date: '2026-04-20', status: 'pending', po_number: 'PO-1002', description: 'Cloud hosting March', created_at: '2026-03-29', file_url: SAMPLE_PDF },
	{ id: '3', vendor: 'Global Logistics', invoice_number: 'INV-2024-003', amount: 8750.50, currency: 'USD', due_date: '2026-04-10', status: 'ready_for_review', po_number: 'PO-1003', description: 'Freight shipment #4421', created_at: '2026-03-30', file_url: SAMPLE_PDF },
	{ id: '4', vendor: 'DataSync Ltd', invoice_number: 'INV-2024-004', amount: 3200.00, currency: 'USD', due_date: '2026-04-18', status: 'failed', po_number: 'PO-1004', description: 'API integration services', created_at: '2026-03-31', file_url: null },
	{ id: '5', vendor: 'CleanSpace Co', invoice_number: 'INV-2024-005', amount: 1850.00, currency: 'USD', due_date: '2026-04-25', status: 'sent_to_erp', po_number: 'PO-1005', description: 'Janitorial services April', created_at: '2026-04-01', file_url: SAMPLE_PDF },
	{ id: '6', vendor: 'Acme Corp', invoice_number: 'INV-2024-006', amount: 7300.00, currency: 'USD', due_date: '2026-04-22', status: 'new', po_number: 'PO-1006', description: 'Printer maintenance', created_at: '2026-04-01', file_url: SAMPLE_PDF },
	{ id: '7', vendor: 'BrightSign Media', invoice_number: 'INV-2024-007', amount: 22000.00, currency: 'USD', due_date: '2026-04-12', status: 'pending', po_number: 'PO-1007', description: 'Digital signage license', created_at: '2026-04-02', file_url: null },
	{ id: '8', vendor: 'SecureNet', invoice_number: 'INV-2024-008', amount: 15400.00, currency: 'USD', due_date: '2026-04-30', status: 'ready_for_review', po_number: 'PO-1008', description: 'Cybersecurity audit Q1', created_at: '2026-04-02', file_url: SAMPLE_PDF },
	{ id: '9', vendor: 'FreshFoods Catering', invoice_number: 'INV-2024-009', amount: 960.00, currency: 'USD', due_date: '2026-04-08', status: 'failed', po_number: 'PO-1009', description: 'Team lunch event March', created_at: '2026-04-03', file_url: SAMPLE_PDF },
	{ id: '10', vendor: 'TechFlow Inc', invoice_number: 'INV-2024-010', amount: 45000.00, currency: 'USD', due_date: '2026-05-01', status: 'new', po_number: 'PO-1010', description: 'Cloud hosting April', created_at: '2026-04-03', file_url: SAMPLE_PDF },
	{ id: '11', vendor: 'Pacific Paper', invoice_number: 'INV-2024-011', amount: 2100.00, currency: 'USD', due_date: '2026-04-14', status: 'sent_to_erp', po_number: 'PO-1011', description: 'Paper and toner supply', created_at: '2026-04-03', file_url: null },
	{ id: '12', vendor: 'QuickShip Express', invoice_number: 'INV-2024-012', amount: 4800.00, currency: 'USD', due_date: '2026-04-16', status: 'pending', po_number: 'PO-1012', description: 'Express delivery contracts', created_at: '2026-04-03', file_url: SAMPLE_PDF },
];

function createInvoiceStore() {
	let invoices = $state<Invoice[]>([...MOCK_INVOICES]);

	function update(id: string, changes: Partial<Invoice>) {
		invoices = invoices.map((inv) => (inv.id === id ? { ...inv, ...changes } : inv));
	}

	return {
		get all() { return invoices; },
		update,
	};
}

export const invoiceStore = createInvoiceStore();
