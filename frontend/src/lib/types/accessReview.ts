// Types for the periodic SOX access-review surface (`/admin/access-review`).
// Mirrors `backend/app/schemas/access_review.py`.

export interface AccessReviewUser {
	user_id: string;
	full_name: string;
	email: string;
	roles: string[];
	last_privileged_action_at: string | null;
	dormant: boolean;
	days_since: number | null;
}

export interface AccessReviewResponse {
	dormant_after_days: number;
	generated_at: string;
	total: number;
	dormant_count: number;
	users: AccessReviewUser[];
}

export interface AccessReviewAcknowledgeResponse {
	acknowledged: boolean;
	last_completed_at: string;
	reviewer_id: string;
}
