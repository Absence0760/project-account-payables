/**
 * Save a `Blob` fetched through the authenticated `api` client (a bare `<a
 * href>` can't carry the Bearer/tenant headers, so every CSV/export download
 * goes through `api.downloadBlob(Post)` first and then this).
 */
export function triggerDownload(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
	URL.revokeObjectURL(url);
}
