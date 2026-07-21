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
	/**
	 * Year-to-date **reportable** completed payments — string-Decimal. Card-rail
	 * payments are excluded: the card settlement entity files those on a 1099-K,
	 * so counting them here would over-report the vendor. This is the figure that
	 * lands in the 1099 box amount.
	 */
	ytd_paid: string;
	over_threshold: boolean;
	/** Count of the reportable (non-card) payments behind `ytd_paid`. */
	payment_count: number;
	/**
	 * Card-rail total deliberately EXCLUDED from `ytd_paid` — string-Decimal.
	 * Surfaced so an operator can reconcile against the processor's 1099-K.
	 */
	card_paid: string;
	card_payment_count: number;
}

export interface Report1099 {
	year: number;
	/** IRS reporting threshold — string-Decimal (e.g. "600"). */
	threshold_usd: string;
	/**
	 * The currency the totals + per-vendor `ytd_paid` are denominated in — the
	 * org's reporting (home) currency. `Payment.amount` is already home-currency
	 * so this is an honest label, not an FX conversion. Authoritative for the
	 * display currency — prefer it over the org-default store.
	 */
	currency: string;
	vendor_count_total: number;
	vendor_count_eligible_over_threshold: number;
	vendor_count_over_threshold_without_w9: number;
	/** Sum of YTD paid across eligible-over-threshold vendors — string-Decimal. */
	total_reportable: string;
	/** @deprecated Back-compat alias of `total_reportable` (same value). */
	total_reportable_usd: string;
	/**
	 * Card-rail spend for the year across EVERY vendor row (not just the
	 * eligible-over-threshold ones `total_reportable` covers) — the money the
	 * 1099 leaves out because the card processor reports it on a 1099-K.
	 * string-Decimal.
	 */
	total_card_excluded: string;
	/** ISO date the report was generated. */
	generated_at: string;
	rows: Vendor1099Row[];
}
