"""An org that names no card issuer never reaches a real one.

Guard rail 7 (local-first): every part of the app must run on a dev laptop with
no cloud account, and each external dependency ships with a safe local default.
Every other adapter registry honours that by resolving an *unconfigured*
provider to its fixture adapter. The card family did not:
`card_adapters/dispatcher` resolved an unset `settings.cards.provider` straight
through `REGION_DEFAULTS` to `lithic` (or `nium` outside the EU/US), and
`scripts/seed.py` seeded every demo and e2e tenant with `cards.enabled: true`
and no provider — so a fresh clone's `POST /api/cards/generate` called Lithic's
real host with an empty API key.

Both halves are fixed and both are pinned here:

  * `get_default_provider` (and `get_card_adapter`'s unset path) apply the
    region preference only when a credential for that issuer actually exists —
    on the org's own BYOK config, or on this deployment. Otherwise
    `LOCAL_FIRST_PROVIDER`.
  * `scripts/seed.py` states `"provider": "mock"` on every tenant it creates,
    so the seeded row says what it uses instead of relying on the absence of an
    env var.

What must NOT change is the `decisions.md` §29 / §56 refusal: a provider someone
**named** that we have no adapter for still raises rather than silently
resolving to the fixture adapter. Picking a default where nobody expressed a
preference and substituting one for a preference that was expressed are
different questions, and only the first one is answered with `mock`.

Companion to `tests/test_card_provider_resolution.py` (the refusal) and
`tests/test_card_region_defaults.py` (the region map).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.card_adapters import UnknownCardProviderError, get_card_adapter
from app.services.card_adapters.dispatcher import (
    LOCAL_FIRST_PROVIDER,
    get_default_provider,
    region_preference,
)
from app.services.card_adapters.lithic import LithicAdapter
from app.services.card_adapters.mock_adapter import MockCardAdapter
from app.services.card_adapters.nium import NiumAdapter

_SEED = Path(__file__).resolve().parents[1] / "scripts" / "seed.py"


@pytest.fixture
def no_platform_cards(monkeypatch):
    """A fresh clone: no `FEOH_LITHIC_API_KEY`, no `FEOH_NIUM_CLIENT_*`."""
    from app.config import settings

    for field in ("lithic_api_key", "nium_client_id", "nium_client_secret"):
        monkeypatch.setattr(settings, field, "", raising=False)
    return settings


# ---------------------------------------------------------------------------
# The unset path — the half that changed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("region", ["US", "GB", "DE", "ZA", "AU", "XX"])
def test_unset_provider_with_no_credentials_resolves_the_fixture_adapter(region, no_platform_cards):
    """Pre-fix this returned Lithic (US/EU) or Nium (rest of world) and issued
    cards against a real host with an empty key."""
    assert get_default_provider(region) == LOCAL_FIRST_PROVIDER
    assert isinstance(get_card_adapter({"region": region}), MockCardAdapter)


def test_an_empty_string_provider_is_unset_not_named(no_platform_cards):
    """`_resolve_card_config`'s BYOK branch defaults `provider` to `""`."""
    assert isinstance(get_card_adapter({"provider": "", "region": "US"}), MockCardAdapter)


def test_region_preference_still_records_the_routing_intent():
    """The credential gate changes the RESOLUTION, not the map. A EUR/SEPA
    vendor still prefers Lithic; the rest of the world still prefers Nium."""
    assert region_preference("US") == "lithic"
    assert region_preference("DE") == "lithic"
    assert region_preference("ZA") == "nium"
    assert region_preference("XX") == "nium"


# ---------------------------------------------------------------------------
# A deployment that DID configure an issuer keeps region-based selection
# ---------------------------------------------------------------------------


def test_platform_credentials_restore_the_region_preference(monkeypatch, no_platform_cards):
    monkeypatch.setattr(no_platform_cards, "lithic_api_key", "live_key")
    assert get_default_provider("US") == "lithic"
    assert isinstance(get_card_adapter({"region": "US"}), LithicAdapter)
    # Nium is still uncredentialed, so ZA has not been given a real issuer.
    assert get_default_provider("ZA") == LOCAL_FIRST_PROVIDER


def test_nium_credentials_restore_the_rest_of_world_preference(monkeypatch, no_platform_cards):
    monkeypatch.setattr(no_platform_cards, "nium_client_id", "cid")
    assert get_default_provider("ZA") == "nium"
    assert isinstance(get_card_adapter({"region": "ZA"}), NiumAdapter)
    assert get_default_provider("US") == LOCAL_FIRST_PROVIDER


def test_byok_keys_on_the_config_count_as_credentials(no_platform_cards):
    """A BYOK org supplies its own keys; the platform holds none. The region
    preference is real for that org even though the deployment has no key."""
    adapter = get_card_adapter({"region": "US", "api_key": "byok_key", "sandbox": False})
    assert isinstance(adapter, LithicAdapter)
    adapter = get_card_adapter(
        {"region": "ZA", "client_id": "cid", "client_secret": "sec", "sandbox": False}
    )
    assert isinstance(adapter, NiumAdapter)


def test_blank_byok_keys_do_not_count(no_platform_cards):
    """`_resolve_card_config`'s BYOK branch fills every key slot, most of them
    with `""`. Whitespace is not a credential either."""
    assert isinstance(
        get_card_adapter({"region": "US", "api_key": "", "client_id": "   "}),
        MockCardAdapter,
    )


# ---------------------------------------------------------------------------
# Non-regression: the §29 / §56 refusal is untouched
# ---------------------------------------------------------------------------


def test_a_named_unknown_provider_still_raises(no_platform_cards):
    """The local-first default must not become a fallback for a NAMED provider
    — that is exactly the fixture-adapter substitution §56 removed."""
    with pytest.raises(UnknownCardProviderError) as exc:
        get_card_adapter({"provider": "marqeta", "region": "US"})
    assert exc.value.provider == "marqeta"


def test_a_named_real_provider_still_resolves_without_credentials(no_platform_cards):
    """Naming an issuer is a decision; a missing key is an operator problem to
    be surfaced by the provider call, not quietly rerouted to a fixture PAN."""
    assert isinstance(get_card_adapter({"provider": "lithic", "region": "US"}), LithicAdapter)
    assert isinstance(get_card_adapter({"provider": "nium", "region": "US"}), NiumAdapter)


def test_explicit_mock_is_still_honoured(no_platform_cards):
    assert isinstance(get_card_adapter({"provider": "mock", "region": "US"}), MockCardAdapter)


# ---------------------------------------------------------------------------
# The seed half
# ---------------------------------------------------------------------------


def _card_settings_blocks(source: str) -> list[str]:
    """Every `"cards": { … }` literal in the seed script, brace-matched."""
    blocks: list[str] = []
    for match in re.finditer(r'"cards"\s*:\s*\{', source):
        depth = 0
        for index in range(match.end() - 1, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(source[match.end() - 1 : index + 1])
                    break
    return blocks


def test_every_seeded_tenant_names_the_fixture_card_provider():
    """A seeded tenant must not reach a real issuer even on a machine that has
    a platform card key configured for some other purpose."""
    blocks = _card_settings_blocks(_SEED.read_text())
    assert blocks, "seed.py no longer seeds any `cards` settings block"
    for block in blocks:
        assert '"provider": "mock"' in block, (
            "every seeded tenant must state its card provider (guard rail 7); "
            f"this block does not: {block}"
        )


def test_the_seeded_card_config_resolves_to_the_fixture_adapter(monkeypatch, no_platform_cards):
    """Belt and braces: with a real platform key present, the seeded shape
    still resolves to `mock` because the row names it."""
    monkeypatch.setattr(no_platform_cards, "lithic_api_key", "live_key")
    seeded = {"enabled": True, "program_type": "platform", "region": "US", "provider": "mock"}
    assert isinstance(get_card_adapter(seeded), MockCardAdapter)
