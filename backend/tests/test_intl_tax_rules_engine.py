"""Country-specific tax rules engine + tax-rate adapter tests.

The rules engine is the data-driven core the VAT / GST / withholding layers
read. These tests pin its contract: every configured country is well-formed,
lookups are case-insensitive, unknown countries fail loud (never silently
zero-rate), and the mock rate adapter resolves deterministic rates from the
engine.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.services.international_tax.country_rules import (
    COUNTRY_RULES,
    TaxRegime,
    UnknownCountry,
    get_country_rule,
    is_eu_country,
    supported_countries,
)
from app.services.tax_rate_adapters import get_tax_rate_adapter


def _run(coro):
    return asyncio.run(coro)


def test_every_rule_is_well_formed():
    for code, rule in COUNTRY_RULES.items():
        assert rule.country_code == code, "key must match the row's country_code"
        assert len(code) == 2, "ISO 3166-1 alpha-2"
        assert rule.regime in {
            TaxRegime.VAT,
            TaxRegime.GST,
            TaxRegime.SALES_TAX,
            TaxRegime.NONE,
        }
        # Rates are Decimal, never float — money is exact.
        assert isinstance(rule.standard_rate, Decimal)
        for r in rule.rate_categories.values():
            assert isinstance(r, Decimal)
        for w in rule.withholding:
            assert isinstance(w.rate, Decimal)
        # At most one default withholding bracket.
        defaults = [w for w in rule.withholding if w.default]
        assert len(defaults) <= 1


def test_get_country_rule_is_case_insensitive():
    assert get_country_rule("gb").country_code == "GB"
    assert get_country_rule("  De ").country_code == "DE"


def test_unknown_country_raises_not_zero():
    with pytest.raises(UnknownCountry):
        get_country_rule("ZZ")


def test_eu_membership_flags():
    # EU members support intra-EU reverse charge.
    assert is_eu_country("DE") is True
    assert is_eu_country("FR") is True
    # GB is post-Brexit: VAT, RC-capable domestically, but NOT EU.
    assert is_eu_country("GB") is False
    assert is_eu_country("AU") is False
    assert is_eu_country("ZZ") is False


def test_supported_countries_sorted_and_complete():
    countries = supported_countries()
    assert countries == sorted(countries)
    for required in ("AU", "IN", "CA", "GB", "DE"):
        assert required in countries


# ---------- mock tax-rate adapter ------------------------------------------


def test_mock_adapter_resolves_standard_rates():
    adapter = get_tax_rate_adapter({"rate_provider": "mock"})
    assert _run(adapter.get_rate("GB")).rate == Decimal("20")
    assert _run(adapter.get_rate("DE")).rate == Decimal("19")
    assert _run(adapter.get_rate("AU")).rate == Decimal("10")
    assert _run(adapter.get_rate("IN")).rate == Decimal("18")
    assert _run(adapter.get_rate("CA")).rate == Decimal("5")  # federal GST


def test_mock_adapter_echoes_regime():
    assert _run(get_tax_rate_adapter(None).get_rate("GB")).regime == TaxRegime.VAT
    assert _run(get_tax_rate_adapter(None).get_rate("AU")).regime == TaxRegime.GST


def test_mock_adapter_rate_category():
    adapter = get_tax_rate_adapter(None)
    reduced = _run(adapter.get_rate("GB", rate_category="reduced"))
    assert reduced.rate == Decimal("5")
    assert reduced.rate_category == "reduced"


def test_mock_adapter_unknown_category_raises():
    adapter = get_tax_rate_adapter(None)
    with pytest.raises(ValueError):
        _run(adapter.get_rate("GB", rate_category="nonexistent"))


def test_mock_adapter_per_tenant_override():
    adapter = get_tax_rate_adapter({"mock_rates": {"GB": "17.5"}})
    assert _run(adapter.get_rate("GB")).rate == Decimal("17.5")


def test_mock_adapter_unknown_country_raises():
    adapter = get_tax_rate_adapter(None)
    with pytest.raises(UnknownCountry):
        _run(adapter.get_rate("ZZ"))


def test_dispatcher_falls_back_to_mock_for_unknown_provider():
    adapter = get_tax_rate_adapter({"rate_provider": "does_not_exist"})
    assert adapter.provider_name == "mock"


def test_cloud_skeletons_registered_but_unimplemented():
    avalara = get_tax_rate_adapter({"rate_provider": "avalara", "account_id": "x", "api_key": "y"})
    assert avalara.provider_name == "avalara"
    with pytest.raises(NotImplementedError):
        _run(avalara.get_rate("GB"))
    taxjar = get_tax_rate_adapter({"rate_provider": "taxjar", "api_key": "y"})
    with pytest.raises(NotImplementedError):
        _run(taxjar.get_rate("GB"))


def test_cloud_skeletons_require_credentials():
    avalara = get_tax_rate_adapter({"rate_provider": "avalara"})
    with pytest.raises(RuntimeError):
        _run(avalara.get_rate("GB"))
