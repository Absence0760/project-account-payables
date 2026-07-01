"""Region → default card provider routing.

`REGION_DEFAULTS.get(region, "nium")` falls back to Nium for any unmapped
region. EU/EEA member states omitted from the map silently routed to Nium — a
EUR/SEPA vendor could then fail at the terminal or eat FX fees. Pin that every
EU-27 (+ EEA/EFTA SEPA) state routes to Lithic and that a genuinely unknown
region still falls back.
"""

from __future__ import annotations

import pytest

from app.services.card_adapters.dispatcher import REGION_DEFAULTS, get_default_provider

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
    assert get_default_provider(region) == "lithic"


@pytest.mark.parametrize("region", ["ZA", "AU", "SG", "IN", "BR", "JP"])
def test_rest_of_world_routes_to_nium(region):
    assert get_default_provider(region) == "nium"


def test_unknown_region_falls_back_to_nium():
    # A region we've never mapped is not silently EU — it lands on Nium.
    assert get_default_provider("XX") == "nium"
