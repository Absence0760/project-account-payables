/**
 * Friendly relative-time formatting (e.g. "Just now", "5m ago", "2d ago").
 * Shared by the notifications page + the sidebar notification popover so the
 * two can't drift. Coarse buckets only — exact timestamps go in `title`.
 */
export function timeAgo(iso: string): string {
	const diff = Date.now() - new Date(iso).getTime();
	const mins = Math.floor(diff / 60000);
	if (mins < 1) return 'Just now';
	if (mins < 60) return `${mins}m ago`;
	const hours = Math.floor(mins / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	return days === 1 ? '1d ago' : `${days}d ago`;
}
