r"""An IdP-asserted email becomes a login AND a mail destination.

Both SSO protocols funnel their verified `(provider, subject, email)` tuple
through `services/identity_provisioning.extract_and_check_email`, which
lower-cased, stripped and domain-allowlisted it — and checked nothing about its
*shape*. `.strip()` removes a TRAILING newline, so the obvious case was
incidentally safe; an INTERIOR one survived it untouched and was stored as
`User.email`, from where every notification the app sends that person addresses
it. A newline in a value that reaches an SMTP header is the header-injection
primitive: the continuation line is attacker-chosen, and the natural choice is a
`Bcc:` on mail carrying that tenant's AP data.

Found by the drift guard in `test_email_shape_call_sites.py`, which asks which
modules decide "is this an email" without the shared rule. The two it named were
`api/enrichment.py` (extracting a host — not a validity decision) and
`api/auth_saml.py` (deciding whether a NameID is an email at all), and following
the second one down led here.

**Why `is_header_safe` and not `looks_like_email`.** The full shape rule requires
a dotted domain, and a corporate IdP can legitimately assert `user@intranet`.
Imposing it here would lock a whole tenant out of its workspace over a cosmetic
rule — a worse outcome than the one being prevented. A control character has no
such defence: no IdP has a reason to emit one, so refusing is free. The refusal
is also a refusal, not a rewrite: silently stripping a character out of an
identity the IdP asserted would provision a *different* user than the one who
signed in.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.services.identity_provisioning import (
    EmailDomainNotAllowed,
    UnsafeEmailAddress,
    extract_and_check_email,
)
from app.utils.emails import is_header_safe

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.mark.parametrize(
    "value",
    [
        "user@example.com",
        "user@intranet",  # no dot — legitimate internal domain, must stay allowed
        "user+tag@example.co.uk",
        "ÜSER@exämple.de",
        "",
    ],
)
def test_is_header_safe_admits_anything_without_a_control_character(value):
    assert is_header_safe(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "user@example.com\n",
        "user@example.com\r",
        "user@example.com\r\nbcc: attacker@evil.test",
        "user\n@example.com",
        "user@exa\tmple.com",
        "user@example.com\x00",
        "user@example.com\x7f",
    ],
)
def test_is_header_safe_refuses_every_control_character(value):
    assert is_header_safe(value) is False


def test_interior_newline_is_refused_not_stripped():
    """The case `.strip()` could never have caught, and the reason this guard
    exists at all."""
    with pytest.raises(UnsafeEmailAddress) as caught:
        extract_and_check_email("user@example.com\nbcc: attacker@evil.test", [])
    # The exception carries the value for the caller to decide about; the OIDC
    # caller deliberately does NOT put it in the audit row.
    assert "\n" in caught.value.email


def test_trailing_whitespace_is_normalised_and_accepted():
    """Unchanged behaviour, pinned: an IdP padding its assertion must not
    become a failed sign-in. The stored value carries no newline."""
    assert extract_and_check_email("  User@Example.com \n", []) == "user@example.com"


def test_an_internal_domain_still_provisions():
    """The deliberate looseness. If someone later swaps this guard for
    `looks_like_email`, this test is the one that has to be argued with — and
    the argument is a tenant on an internal-only mail domain losing SSO."""
    assert extract_and_check_email("user@intranet", []) == "user@intranet"


def test_the_domain_allowlist_still_applies_after_the_safety_check():
    assert extract_and_check_email("user@allowed.test", ["allowed.test"]) == "user@allowed.test"
    with pytest.raises(EmailDomainNotAllowed):
        extract_and_check_email("user@other.test", ["allowed.test"])


def test_the_safety_check_runs_before_the_allowlist():
    """Ordering matters: with an allowlist configured, an unsafe address whose
    domain happens to be allowed must still be refused as unsafe rather than
    waved through, and one that is both unsafe and off-list must report the
    sharper of the two."""
    with pytest.raises(UnsafeEmailAddress):
        extract_and_check_email("user@allowed.test\nbcc: x@evil.test", ["allowed.test"])


@pytest.mark.parametrize("module", ["api/auth_sso.py", "api/auth_saml.py"])
def test_both_sso_callbacks_handle_the_refusal(module):
    """An unhandled `UnsafeEmailAddress` would be a 500 on a public callback —
    which is a worse failure than the one being fixed, and the easiest thing to
    forget when adding a raise to shared code. AST-scanned rather than
    grepped so a mention in a docstring or an import line doesn't satisfy it.
    """
    tree = ast.parse((APP_DIR / module).read_text(encoding="utf-8"), filename=module)
    handled = {
        handler.type.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if isinstance(handler.type, ast.Name)
    }
    assert "UnsafeEmailAddress" in handled, (
        f"{module} calls extract_and_check_email but never catches "
        "UnsafeEmailAddress — an IdP-supplied control character would surface "
        "as a 500 on a public SSO callback instead of a refused sign-in."
    )
