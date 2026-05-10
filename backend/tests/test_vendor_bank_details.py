"""Tests for the bank_details merge logic in `api/vendors.update_vendor`.

`Vendor.bank_details` is JSONB, historically free-form. PATCH must
merge instead of replace so a UI that only edits `counterparty_id`
doesn't clobber sibling keys (`account_last4`, legacy processor
metadata). Empty string and `None` are the clear-this-key signal.
"""

from __future__ import annotations

from app.api.vendors import _merge_bank_details


def test_merge_with_no_existing_returns_incoming():
    assert _merge_bank_details(None, {"counterparty_id": "cp_1"}) == {"counterparty_id": "cp_1"}


def test_merge_preserves_unrelated_existing_keys():
    """Partial UI update of `counterparty_id` must not drop the
    last4 / bank_name fields the row already had."""
    existing = {
        "counterparty_id": "cp_old",
        "account_last4": "1234",
        "bank_name": "First National",
        "legacy_processor_ref": "x-987",
    }
    merged = _merge_bank_details(existing, {"counterparty_id": "cp_new"})
    assert merged == {
        "counterparty_id": "cp_new",
        "account_last4": "1234",
        "bank_name": "First National",
        "legacy_processor_ref": "x-987",
    }


def test_merge_clears_key_on_empty_string():
    """The UI signals "remove this counterparty" by sending an empty
    string — we drop the key rather than persisting "" (which would
    look like a counterparty whose id is the empty string downstream)."""
    existing = {"counterparty_id": "cp_old", "account_last4": "1234"}
    merged = _merge_bank_details(existing, {"counterparty_id": ""})
    assert merged == {"account_last4": "1234"}


def test_merge_clears_key_on_none():
    """API clients that prefer JSON `null` to `""` get the same
    "clear" behaviour."""
    existing = {"counterparty_id": "cp_old", "account_last4": "1234"}
    merged = _merge_bank_details(existing, {"counterparty_id": None})
    assert merged == {"account_last4": "1234"}


def test_merge_collapses_to_none_when_all_keys_cleared():
    """If the merge would land on an empty dict, return None so the
    column stays NULL-ish in JSONB and stops appearing in `from_db`'s
    truthy check."""
    existing = {"counterparty_id": "cp_old"}
    merged = _merge_bank_details(existing, {"counterparty_id": ""})
    assert merged is None


def test_merge_with_incoming_none_returns_existing_unchanged():
    """Defensive: a caller that passes `None` for the entire payload
    should be a no-op, not a wipe."""
    existing = {"counterparty_id": "cp_1", "account_last4": "1234"}
    assert _merge_bank_details(existing, None) == existing


def test_merge_can_add_new_keys_alongside_existing():
    existing = {"counterparty_id": "cp_1"}
    merged = _merge_bank_details(existing, {"account_last4": "9876", "bank_name": "Chase"})
    assert merged == {
        "counterparty_id": "cp_1",
        "account_last4": "9876",
        "bank_name": "Chase",
    }


def test_merge_returns_none_when_both_inputs_empty():
    assert _merge_bank_details(None, None) is None
    assert _merge_bank_details({}, {}) is None
