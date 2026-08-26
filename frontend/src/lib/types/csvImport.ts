// Shared shape of the Day-0 CSV importers — `POST /api/vendors/import-csv`
// and `POST /api/invoices/import-csv`. Both are skip-and-report: a bad row
// never aborts the batch, it's just counted and explained. Mirrors the
// backend's `services/csv_import.ImportResult.to_dict()`. See
// backend/docs/csv-import.md.

export interface ImportRowError {
	/** The row's literal line number in the uploaded file — header is line 1, so the first data row is 2. */
	row: number;
	message: string;
}

export interface ImportResult {
	imported: number;
	skipped: number;
	errors: ImportRowError[];
}
