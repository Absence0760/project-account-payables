"""Adverse-media support in the sanctions adapters + the Dow Jones /
Refinitiv provider skeletons.

Pins:
  * The mock adapter returns an `adverse_media` category +
    `review_required` for the negative-news fixture name, overridable
    via `config["mock_adverse_media"]`.
  * Existing sanctions `match` / high-risk-country behaviour is intact
    and now carries `categories`.
  * `ScreeningResult` still constructs with the historical
    positional/kwarg signature (back-compat — `vendor_screening.py` and
    `compliance.py` depend on the existing fields).
  * The Dow Jones / Refinitiv skeletons fail closed (RuntimeError)
    without an api_key, and parse their documented response contracts
    into the right verdict + categories when given a payload.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.sanctions_adapters import get_sanctions_adapter
from app.services.sanctions_adapters.base import ScreeningResult
from app.services.sanctions_adapters.complyadvantage import ComplyAdvantageAdapter
from app.services.sanctions_adapters.dowjones import DowJonesAdapter
from app.services.sanctions_adapters.mock_adapter import MockSanctionsAdapter
from app.services.sanctions_adapters.refinitiv import RefinitivAdapter

# ---------------------------------------------------------------------------
# ScreeningResult — backward-compatible construction.
# ---------------------------------------------------------------------------


def test_screening_result_back_compat_construction():
    """The historical 5-field constructor must keep working unchanged —
    no positional shift, no new required field."""
    r = ScreeningResult(
        provider="mock",
        result="match",
        matched_list="OFAC",
        risk_score=Decimal("90.00"),
        raw_response={"hit": "x"},
    )
    assert r.provider == "mock"
    assert r.result == "match"
    assert r.matched_list == "OFAC"
    assert r.risk_score == Decimal("90.00")
    assert r.raw_response == {"hit": "x"}
    # New fields default safely.
    assert r.categories == ()
    assert r.adverse_media is False


def test_screening_result_positional_back_compat():
    """Existing positional usage (provider, result) stays valid."""
    r = ScreeningResult("mock", "clear")
    assert r.provider == "mock"
    assert r.result == "clear"
    assert r.categories == ()


def test_screening_result_adverse_media_flag_derived():
    r = ScreeningResult(
        provider="mock",
        result="review_required",
        matched_list="ADVERSE_MEDIA",
        categories=("adverse_media",),
    )
    assert r.adverse_media is True


# ---------------------------------------------------------------------------
# Mock adapter — adverse media + existing behaviour intact.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_adverse_media_fixture_returns_review_with_category():
    adapter = MockSanctionsAdapter()
    result = await adapter.screen_vendor(vendor_name="Adverse Media Test Co", vendor_country="US")
    assert result.result == "review_required"
    assert result.matched_list == "ADVERSE_MEDIA"
    assert result.categories == ("adverse_media",)
    assert result.adverse_media is True
    assert result.risk_score == Decimal("50.00")


@pytest.mark.asyncio
async def test_mock_adverse_media_override_via_config():
    adapter = MockSanctionsAdapter({"mock_adverse_media": ["Shady Holdings"]})
    result = await adapter.screen_vendor(vendor_name="Shady Holdings", vendor_country="US")
    assert result.result == "review_required"
    assert result.adverse_media is True


@pytest.mark.asyncio
async def test_mock_sanctions_match_still_works_and_is_categorised():
    adapter = MockSanctionsAdapter()
    result = await adapter.screen_vendor(vendor_name="Sanctioned Test Entity", vendor_country="US")
    assert result.result == "match"
    assert result.matched_list == "MOCK_TEST_SDN"
    assert result.categories == ("sanctions",)
    assert result.adverse_media is False


@pytest.mark.asyncio
async def test_mock_clear_vendor_has_no_categories():
    adapter = MockSanctionsAdapter()
    result = await adapter.screen_vendor(vendor_name="Wholly Innocent Ltd", vendor_country="DE")
    assert result.result == "clear"
    assert result.categories == ()
    assert result.adverse_media is False


# ---------------------------------------------------------------------------
# Dow Jones / Refinitiv skeletons — fail closed without a key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dowjones_raises_without_api_key():
    adapter = DowJonesAdapter()
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.screen_vendor(vendor_name="Acme", vendor_country="US")


@pytest.mark.asyncio
async def test_refinitiv_raises_without_api_key():
    adapter = RefinitivAdapter()
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.screen_vendor(vendor_name="Acme", vendor_country="US")


def test_dispatcher_routes_to_new_providers_when_configured():
    dj = get_sanctions_adapter({"provider": "dowjones", "api_key": "k"})
    assert dj.provider_name == "dowjones"
    rf = get_sanctions_adapter({"provider": "refinitiv", "api_key": "k"})
    assert rf.provider_name == "refinitiv"


# ---------------------------------------------------------------------------
# Dow Jones / Refinitiv — response-contract parsing (no network; we feed
# the documented payload shape directly into the private parser).
# ---------------------------------------------------------------------------


def test_dowjones_parses_sanctions_match():
    adapter = DowJonesAdapter({"api_key": "k"})
    payload = {"data": {"attributes": {"matches": [{"match-type": "sanctions"}]}}}
    r = adapter._parse(payload)
    assert r.result == "match"
    assert "sanctions" in r.categories
    assert r.matched_list == "DOWJONES_SANCTIONS"


def test_dowjones_parses_adverse_media_as_review():
    adapter = DowJonesAdapter({"api_key": "k"})
    payload = {"data": {"attributes": {"matches": [{"match-type": "adverse-media"}]}}}
    r = adapter._parse(payload)
    assert r.result == "review_required"
    assert r.categories == ("adverse_media",)
    assert r.adverse_media is True


def test_dowjones_parses_no_matches_as_clear():
    adapter = DowJonesAdapter({"api_key": "k"})
    r = adapter._parse({"data": {"attributes": {"matches": []}}})
    assert r.result == "clear"
    assert r.risk_score == Decimal("0.00")


def test_refinitiv_parses_sanctions_match():
    adapter = RefinitivAdapter({"api_key": "k"})
    payload = {"results": [{"categories": [{"name": "SANCTIONS"}]}]}
    r = adapter._parse(payload)
    assert r.result == "match"
    assert "sanctions" in r.categories
    assert r.matched_list == "WORLDCHECK_SANCTIONS"


def test_refinitiv_parses_adverse_media_as_review():
    adapter = RefinitivAdapter({"api_key": "k"})
    payload = {"results": [{"categories": [{"name": "ADVERSE-MEDIA"}]}]}
    r = adapter._parse(payload)
    assert r.result == "review_required"
    assert r.categories == ("adverse_media",)
    assert r.adverse_media is True


def test_refinitiv_parses_no_results_as_clear():
    adapter = RefinitivAdapter({"api_key": "k"})
    r = adapter._parse({"results": []})
    assert r.result == "clear"


# ---------------------------------------------------------------------------
# ComplyAdvantage — the third skeleton, brought to taxonomy parity with its
# siblings. It computed a `types` set to pick the verdict and then threw it
# away, so a CA tenant got no categories at all.
# ---------------------------------------------------------------------------


def _ca_payload(*types_per_hit: list[str]) -> dict:
    hits = [{"doc": {"types": t}} for t in types_per_hit]
    return {"content": {"data": {"hits": hits, "total_hits": len(hits)}}}


def test_complyadvantage_parses_sanction_match_with_categories():
    adapter = ComplyAdvantageAdapter({"api_key": "k"})
    r = adapter._parse(_ca_payload(["sanction", "pep"]))
    assert r.result == "match"
    assert r.matched_list == "OFAC/EU/UN/UK_SANCTION"
    assert r.categories == ("pep", "sanctions")
    assert r.adverse_media is False


def test_complyadvantage_parses_adverse_media_as_review():
    adapter = ComplyAdvantageAdapter({"api_key": "k"})
    r = adapter._parse(_ca_payload(["adverse-media"]))
    assert r.result == "review_required"
    assert r.categories == ("adverse_media",)
    assert r.adverse_media is True


def test_complyadvantage_carries_an_unmapped_type_through():
    """An unmapped CA type is still evidence — carried through with hyphens
    normalised, never silently dropped."""
    adapter = ComplyAdvantageAdapter({"api_key": "k"})
    r = adapter._parse(_ca_payload(["fitness-probity"]))
    assert r.result == "review_required"
    assert r.categories == ("fitness_probity",)


def test_complyadvantage_parses_no_hits_as_clear_with_no_categories():
    adapter = ComplyAdvantageAdapter({"api_key": "k"})
    r = adapter._parse({"content": {"data": {"hits": [], "total_hits": 0}}})
    assert r.result == "clear"
    assert r.categories == ()


def test_complyadvantage_requests_adverse_media():
    """Negative-news screening is part of what this module promises; a control
    that never asks the provider for the signal is a false assurance."""
    from app.services.sanctions_adapters.complyadvantage import _SEARCH_TYPES

    assert "adverse-media" in _SEARCH_TYPES


@pytest.mark.asyncio
async def test_complyadvantage_raises_without_api_key():
    adapter = ComplyAdvantageAdapter()
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.screen_vendor(vendor_name="Acme", vendor_country="US")
