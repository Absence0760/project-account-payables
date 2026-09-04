r"""The two email-address holes #321 closed, as named cases — plus the guards
that stop a third one arriving by a spelling nobody scanned for.

`tests/test_email_shape.py` pins the shared rule and scans for a copy of the
regex LITERAL; `tests/test_email_shape_call_sites.py` drives addresses through
the three HTTP surfaces that decide who receives a tenant's data. Neither says
what the two live findings actually were, and both can be evaded by a fourth
surface that hand-rolls a *differently spelled* check. This file covers:

**[decisions](../../docs/decisions.md) §50 — the `$`-vs-`\Z` anchor.** All three
hoisted copies ended in `$`, which in Python matches at end-of-string **or just
before a trailing newline**, so `"user@example.com\n"` satisfied every one of
them and was stored as a login, a child tenant's admin address and a
scheduled-report recipient. A newline in a value that reaches an SMTP header is
the header-injection primitive. The test reconstructs the OLD pattern and
asserts it admits exactly what the current one refuses — so the regression is
demonstrated, not merely described, and a "simplification" back to `$` fails on
a test that names the consequence.

**§60 — an IdP-supplied address is refused for a control character, not held to
the shape rule.** `identity_provisioning.extract_and_check_email` `.strip()`s,
which incidentally covered a TRAILING newline; an **interior** one survived and
became `User.email`. The fix is deliberately NOT `looks_like_email`: that rule
requires a dotted domain, and a corporate IdP can legitimately assert
`user@intranet`, so imposing it would trade a header-injection risk for a
guaranteed tenant lockout. `is_header_safe` is therefore weaker on shape and
exactly as wide as the danger — this file pins both halves of that asymmetry,
including the invariant that ties them together: **nothing `looks_like_email`
admits may be header-unsafe.**

Writing the §60 half surfaced a THIRD gap, reported rather than patched (this
change is test-only): `looks_like_email` refuses only the five control
characters `\s` happens to cover, so `NUL`, `BEL`, `ESC` and DEL pass the
SHAPE rule — and the three surfaces that use it do not additionally call
`is_header_safe`. Not the header-injection primitive (CR/LF are refused), but
a control character in a value that becomes a login and a mail destination is
what `is_header_safe` exists to refuse, and one of the two rules refusing it is
not the same as the surface refusing it. `test_no_control_character_survives_both_rules`
pins the invariant in a form that stays true either side of the one-line fix.

Then two drift guards aimed at the gap the existing scans leave. The literal
scan cannot see a REWRITTEN regex, and the `"@" in`-idiom scan cannot see
`pydantic.EmailStr` or a `\w+@\w+` of somebody's own devising — both of which
are how a fourth rule arrives without ever copying the third.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.utils.emails import EMAIL_SHAPE_PATTERN, is_header_safe, looks_like_email

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

#: The pattern as all three call sites spelled it before the hoist — identical
#: to the shared one except for the anchor. Reconstructed here so the §50
#: finding is reproducible rather than a claim in a docstring.
_PRE_HOIST_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$")

#: Every module that decides "is this an email" using the shared shape rule.
#: Named so that dropping the call from one of them is a failing test, which is
#: the failure mode the behavioural HTTP tests in
#: `test_email_shape_call_sites.py` cover for the three surfaces they drive and
#: nothing covers for a fourth.
_SHAPE_RULE_CALL_SITES = (
    "api/signup.py",
    "api/partner.py",
    "schemas/scheduled_report.py",
)

#: The one module that uses the NARROWER rule, and why (§60).
_HEADER_SAFETY_CALL_SITES = ("services/identity_provisioning.py",)


# ---------------------------------------------------------------------------
# §50 — the anchor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("user@example.com\n", id="trailing-lf"),
        pytest.param("user@example.com\r\n", id="trailing-crlf"),
        pytest.param("user@example.com\n\n", id="two-trailing-lf"),
        pytest.param("user@example.com\nBcc: attacker@evil.test", id="header-injection-bcc"),
        pytest.param("user@example.com\r\nBcc: attacker@evil.test", id="header-injection-crlf"),
    ],
)
def test_the_shape_rule_refuses_every_newline_bearing_address(address):
    """The §50 hole, in every form it can arrive in.

    The single trailing `\\n` is the one that actually shipped: `$` admits it,
    so the address passed validation and was stored. The `Bcc:` variants are
    what an attacker does with that: the value lands in a mail header, and the
    newline lets them continue it."""
    assert looks_like_email(address) is False


def test_the_pre_hoist_pattern_admitted_the_trailing_newline_and_the_current_one_does_not():
    """The regression, demonstrated against the old rule side by side.

    Both patterns agree on every ordinary address; they differ on exactly one
    input, which is the entire finding. If someone "simplifies" `\\Z` back to
    `$`, the second assertion here is what fails — with the reason attached.
    """
    hole = "user@example.com\n"
    assert _PRE_HOIST_PATTERN.match(hole) is not None, (
        "the reconstruction no longer reproduces the old behaviour — this test "
        "can no longer show what §50 fixed"
    )
    assert EMAIL_SHAPE_PATTERN.match(hole) is None
    assert _PRE_HOIST_PATTERN.pattern.replace("$", r"\Z") == EMAIL_SHAPE_PATTERN.pattern, (
        "the shared pattern now differs from the pre-hoist one by more than the "
        "anchor; re-derive the reconstruction above so the comparison stays honest"
    )
    for ordinary in ("demo@acme.com", "a@b.c", "first.last@sub.example.co.uk"):
        assert bool(_PRE_HOIST_PATTERN.match(ordinary)) is looks_like_email(ordinary)


# ---------------------------------------------------------------------------
# §60 — header safety is narrower than the shape rule, on purpose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("user\n@example.com", id="interior-lf-local-part"),
        pytest.param("user@exa\nmple.com", id="interior-lf-domain"),
        pytest.param("user@example.com\nBcc: attacker@evil.test", id="interior-lf-then-header"),
        pytest.param("user\r@example.com", id="interior-cr"),
        pytest.param("user@example.com\r\n", id="trailing-crlf"),
        pytest.param("user\x00@example.com", id="nul"),
        pytest.param("user\x7f@example.com", id="del"),
        pytest.param("user\t@example.com", id="tab"),
        pytest.param("user\x0b@example.com", id="vertical-tab"),
    ],
)
def test_an_idp_asserted_address_is_refused_for_any_control_character(address):
    """§60's actual finding: `.strip()` covered the trailing newline, so the
    INTERIOR one survived and was stored as `User.email` — a login and the
    destination of every notification that person receives."""
    assert is_header_safe(address) is False


@pytest.mark.parametrize(
    "address",
    [
        pytest.param("user@intranet", id="internal-domain-no-dot"),
        pytest.param("user@localhost", id="bare-hostname"),
        pytest.param("USER@INTRANET", id="internal-domain-uppercase"),
        pytest.param("first.last@corp.example.com", id="ordinary"),
    ],
)
def test_header_safety_deliberately_admits_what_the_shape_rule_refuses(address):
    """The decision, as behaviour.

    A corporate IdP can legitimately assert a dotless internal domain. Running
    `looks_like_email` on an IdP-supplied address would refuse it and lock the
    whole tenant out of its own workspace — trading a header-injection risk for
    a guaranteed outage. So `is_header_safe` is exactly as wide as the danger
    and no wider. Tightening it means locking someone out; make that call here,
    on a failing test."""
    assert is_header_safe(address) is True


def test_header_safety_is_not_a_shape_check_and_must_not_become_one():
    """Stated explicitly so nobody "hardens" the SSO path by swapping in the
    shape rule: `is_header_safe` admits values that are not addresses at all.
    Its job is to keep a control character out of a mail header, and the domain
    allowlist + the IdP's own assertion are what establish the identity."""
    assert is_header_safe("not-an-email-at-all") is True
    assert looks_like_email("not-an-email-at-all") is False
    assert is_header_safe("") is True


@pytest.mark.parametrize(
    "address",
    [
        "demo@acme.com",
        "a@b.c",
        "first.last@sub.domain.example.com",
        "user+scheduled-report@example.co.uk",
        "UPPER@Example.COM",
        "user_name-123@example-corp.io",
        "ap@例え.jp",
        "nobody@no-such-domain-anywhere.invalid",
    ],
)
def test_nothing_the_shape_rule_admits_is_ever_header_unsafe(address):
    """The invariant that ties the two rules together.

    The three shape-checked surfaces do NOT additionally call `is_header_safe`,
    so `looks_like_email` is the only thing standing between them and a mail
    header. Any future loosening of the shape rule that admitted a control
    character would silently reopen §50's hole on all three at once."""
    assert looks_like_email(address) is True
    assert is_header_safe(address) is True


@pytest.mark.parametrize(
    ("name", "char"),
    [
        ("CR", "\r"),
        ("LF", "\n"),
        ("tab", "\t"),
        ("vertical-tab", "\v"),
        ("form-feed", "\f"),
    ],
)
def test_the_header_injection_primitive_is_refused_by_the_shape_rule(name, char):
    """CR and LF are the characters that let a value continue the mail header
    it lands in, and the other three are the whitespace an SMTP header folds
    on. The shape rule refuses each of them in the local part, in the domain
    and at the end — the last position being the one `$` used to admit (§50).
    """
    for candidate in (
        f"us{char}er@example.com",
        f"user@exam{char}ple.com",
        f"user@example.com{char}",
    ):
        assert looks_like_email(candidate) is False, (
            f"{name} admitted in {candidate!r} — this is the header-injection "
            "primitive the shared pattern exists to refuse"
        )


@pytest.mark.parametrize("code", [*range(0x00, 0x20), 0x7F])
def test_no_control_character_survives_both_rules(code):
    r"""The safety invariant across the whole C0 range plus DEL: a control
    character must be refused by at least one of the two rules, so a surface
    applying either one cannot store it.

    Worth stating as a *pair* because the two rules split the range today, and
    not evenly — `looks_like_email` only refuses the five controls `\s`
    happens to cover (CR, LF, tab, VT, FF), so `NUL`, `BEL`, `ESC` and DEL are
    admitted by the SHAPE rule and refused only by `is_header_safe`. See this
    module's report: the three shape-only surfaces (signup, partner child
    provisioning, scheduled-report recipients) do not call `is_header_safe`,
    which is a real, reported gap in `app/utils/emails.py` — deliberately not
    patched from this test-only change, and deliberately not asserted as
    correct behaviour here either. This invariant is written so it holds both
    before and after that fix: the day `looks_like_email` composes
    `is_header_safe`, nothing here needs editing.
    """
    char = chr(code)
    for candidate in (
        f"us{char}er@example.com",
        f"user@exam{char}ple.com",
        f"user@example.com{char}",
    ):
        assert not (looks_like_email(candidate) and is_header_safe(candidate)), (
            f"U+{code:04X} survives BOTH rules in {candidate!r} — a control "
            "character with no rule refusing it can reach a mail header"
        )


# ---------------------------------------------------------------------------
# Drift guards — the spellings the existing scans cannot see
# ---------------------------------------------------------------------------


def test_every_known_call_site_still_calls_the_shared_rule():
    """A call site that imports the helper and stops CALLING it passes the
    regex-literal scan and the `"@" in` scan alike.

    Asserted at the source level (an AST call-name search), which also covers
    the two surfaces the HTTP tests do not exercise on every code path."""
    for relative in _SHAPE_RULE_CALL_SITES:
        path = APP_DIR / relative
        assert path.is_file(), f"{relative} moved — update _SHAPE_RULE_CALL_SITES"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "looks_like_email" in called, (
            f"{relative} no longer CALLS looks_like_email. It decides who "
            "receives a tenant's data by email; an import with no call is the "
            "same as no check."
        )

    for relative in _HEADER_SAFETY_CALL_SITES:
        path = APP_DIR / relative
        assert path.is_file(), f"{relative} moved — update _HEADER_SAFETY_CALL_SITES"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "is_header_safe" in called, (
            f"{relative} no longer CALLS is_header_safe — an interior newline "
            "in an IdP-asserted address becomes a stored login again (§60)."
        )


def test_no_module_compiles_its_own_email_shaped_regex():
    """The literal scan in `test_email_shape.py` searches for the exact pattern
    text, so a fourth copy spelled differently — `\\w+@\\w+\\.\\w+`,
    `[^@]+@[^@]+\\.[a-z]{2,}` — evades it entirely while being the same rule
    with different bugs (and, spelled with `$`, the same §50 hole).

    This one asks the AST for every `re.compile` whose pattern mentions `@` and
    an anchor, anywhere but the owner."""
    owner = (APP_DIR / "utils" / "emails.py").resolve()
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.resolve() == owner:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"compile", "match", "fullmatch", "search"}:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            pattern = node.args[0].value
            if not isinstance(pattern, str) or "@" not in pattern:
                continue
            # An email-SHAPE check anchors and requires a dotted domain. A
            # token extractor (`invoices+([...])@`, the intake address parser)
            # does neither, and is not deciding whether something is an email.
            if any(anchor in pattern for anchor in ("^", r"\A")) and "\\." in pattern:
                offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno} {pattern!r}")

    assert not offenders, (
        f"hand-rolled email-shape regex at {offenders}. Import "
        "`looks_like_email` from app.utils.emails — a fresh regex is a fourth "
        "copy of a rule that gates who receives a tenant's AP data, and one "
        "spelled with `$` instead of `\\Z` reopens decisions §50."
    )


def test_no_module_introduces_a_second_rule_via_pydantic_emailstr():
    """`EmailStr` is the other way a second, disagreeing rule arrives — as a
    type annotation, with no regex and no `@` idiom for either existing scan to
    catch.

    It is not "stricter and therefore fine": it is a DIFFERENT rule (it refuses
    the dotless internal domains §60 exists to preserve, and pulls in
    `email-validator`, which §50 explicitly rejected). If a surface genuinely
    needs RFC-strict validation, that is a decision to record — not a type
    swap in one schema while the other three keep the shared rule.
    """
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in sorted(APP_DIR.rglob("*.py"))
        if "EmailStr" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"pydantic EmailStr used in {offenders}. Use "
        "`app.utils.emails.looks_like_email` so every surface answers "
        '"is this an email" the same way (decisions §50), or record the '
        "decision to diverge."
    )
