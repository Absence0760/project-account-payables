// Types for the 1099 reporting surface. Mirrors the JSON returned by
// `GET /api/tax/1099-report?year=` (backend `Report1099.to_dict()` /
// `VendorReportRow.to_dict()`). Money fields arrive as string-Decimals.

export interface Vendor1099Row {
	vendor_id: string;
	vendor_name: string;
	/** Masked/full tax id as the backend chooses to expose it; may be null. */
	tax_id: string | null;
	/** W-9 box-3 classification (e.g. "Individual/sole proprietor"); may be null. */
	tax_classification: string | null;
	is_1099_eligible: boolean;
	/** ISO date (YYYY-MM-DD) the W-9 was received, or null. */
	w9_received_date: string | null;
	w9_on_file: boolean;
	/** Year-to-date completed payments — string-Decimal. */
	ytd_paid: string;
	over_threshold: boolean;
	payment_count: number;
}

export interface Report1099 {
	year: number;
	/** IRS reporting threshold — string-Decimal (e.g. "600"). */
	threshold_usd: string;
	vendor_count_total: number;
	vendor_count_eligible_over_threshold: number;
	vendor_count_over_threshold_without_w9: number;
	/** Sum of YTD paid across eligible-over-threshold vendors — string-Decimal. */
	total_reportable_usd: string;
	/** ISO date the report was generated. */
	generated_at: string;
	rows: Vendor1099Row[];
}
