"""Region → preferred card provider routing.

`region_preference` (`REGION_DEFAULTS.get(region, "nium")`) falls back to Nium
for any unmapped region. EU/EEA member states omitted from the map silently
routed to Nium — a EUR/SEPA vendor could then fail at the terminal or eat FX
fees. Pin that every EU-27 (+ EEA/EFTA SEPA) state prefers Lithic and that a
genuinely unknown region still falls back.

The map is a *preference*, not the resolution: `get_default_provider` applies it
only once a credential for the preferred issuer exists, so a fresh clone gets
`mock` (guard rail 7). That gate is pinned in
`tests/test_card_provider_local_first.py`; here we hold the routing intent
itself, which the gate must not quietly rewrite.
"""

from __future__ import annotations

import pytest

from app.services.card_adapters.dispatcher import (
    REGION_DEFAULTS,
    get_default_provider,
    region_preference,
)

# The 27 EU member states (ISO-3166 alpha-2; Greece also has the EU code EL).
_EU_27 = [
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
]

# EEA / EFTA non-EU but SEPA-reachable.
_EEA_EFTA = ["NO", "IS", "LI", "CH"]


@pytest.mark.parametrize("region", _EU_27 + _EEA_EFTA + ["EL", "US", "UK", "GB"])
def test_eu_and_eea_regions_route_to_lithic(region):
    assert REGION_DEFAULTS.get(region) == "lithic", f"{region} must default to Lithic"
    assert region_preference(region) == "lithic"


@pytest.mark.parametrize("region", ["ZA", "AU", "SG", "IN", "BR", "JP"])
def test_rest_of_world_routes_to_nium(region):
    assert region_preference(region) == "nium"


def test_unknown_region_falls_back_to_nium():
    # A region we've never mapped is not silently EU — it lands on Nium.
    assert region_preference("XX") == "nium"


def test_the_preference_is_what_a_credentialed_deployment_resolves_to(monkeypatch):
    """The credential gate must not reorder the map — an operator holding a
    Lithic key gets Lithic in the EU/US and nothing invented elsewhere."""
    from app.config import settings

    monkeypatch.setattr(settings, "lithic_api_key", "live_key")
    monkeypatch.setattr(settings, "nium_client_id", "cid")
    monkeypatch.setattr(settings, "nium_client_secret", "sec")
    for region in ("US", "GB", "DE", "ZA", "AU", "XX"):
        assert get_default_provider(region) == region_preference(region)
