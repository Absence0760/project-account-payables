"""The stripe_treasury adapter must honor a configurable API base.

Empty FEOH_STRIPE_API_BASE → live Stripe (prod default). Set it → the adapter
targets that host (the local stripe-mock container). A per-config `api_base`
overrides both. This is the seam that lets the payment path run offline against
stripe-mock (see backend/docs/payments.md § Local testing).
"""

from __future__ import annotations

from app.config import settings
from app.services.payment_adapters.stripe_treasury import API_BASE, StripeTreasuryAdapter

LOCAL = "http://localhost:12111/v1"


def test_defaults_to_live_stripe(monkeypatch):
    monkeypatch.setattr(settings, "stripe_api_base", "")
    adapter = StripeTreasuryAdapter({"api_key": "sk_test_x"})
    assert adapter.api_base == API_BASE == "https://api.stripe.com/v1"


def test_settings_repoints_to_stripe_mock(monkeypatch):
    monkeypatch.setattr(settings, "stripe_api_base", LOCAL)
    adapter = StripeTreasuryAdapter({"api_key": "sk_test_x"})
    assert adapter.api_base == LOCAL


def test_per_config_api_base_wins(monkeypatch):
    monkeypatch.setattr(settings, "stripe_api_base", LOCAL)
    adapter = StripeTreasuryAdapter({"api_key": "sk_test_x", "api_base": "http://other/v1"})
    assert adapter.api_base == "http://other/v1"
