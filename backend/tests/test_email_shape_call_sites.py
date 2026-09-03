r"""The three surfaces that decide who receives a tenant's data by email.

`tests/test_email_shape.py` pins the shared rule (`app/utils/emails.py`) and
scans for a fourth copy of the regex. What it cannot show is that the three
places which used to hold their own copy now *behave* the same way — a call
site that imports `looks_like_email` and then never calls it, or calls it after
the value has already been stored, passes every test in that file.

So this one drives the addresses through the HTTP surfaces:

* `POST /api/signup/start` — the address becomes a new tenant's admin login;
* `POST /api/partner/children/provision` — the address becomes a
  partner-provisioned child tenant's admin login;
* `POST/PATCH /api/analytics/scheduled-reports` — every address on the list
  receives a recurring CSV of the tenant's AP spend.

The headline case is the trailing newline. All three copies of the rule ended in
`$`, which in Python matches at end-of-string **or just before a trailing
newline**, so `"user@example.com\n"` satisfied every one of them. That value then
reached an SMTP header, where a newline is the header-injection primitive — the
attacker-controlled continuation being an extra `Bcc:` on a mail carrying a
tenant's AP register. The shared pattern anchors with `\Z`; these tests are
where that shows up as behaviour rather than as a regex property
([decisions.md](../../docs/decisions.md) §50).

The ADMIT half matters as much as the reject half, and for the same reason it
does in the unit file: the rule is deliberately permissive, so a later
tightening has to come here and edit a failing test — making "who can still be
mailed" a decision somebody took rather than a side effect.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.models.organization import Organization
from app.models.signup import EmailVerification
from app.models.user import User, UserRole

pytestmark = pytest.mark.asyncio

_SCHEDULES = "/api/analytics/scheduled-reports"

#: Addresses no surface may accept. The newline pair is the reason this file
#: exists; the rest are the ordinary garbage the shape check is for, asserted
#: at each surface so a call site can't be quietly loosened on its own.
REJECTED = [
    pytest.param("user@example.com\n", id="trailing-newline"),
    pytest.param("user@example.com\nbcc: attacker@evil.test", id="header-injection"),
    pytest.param("not-an-email", id="no-at-sign"),
    pytest.param("user@localhost", id="bare-hostname"),
    pytest.param("user@example.", id="trailing-dot"),
    pytest.param("two@at@example.com", id="two-at-signs"),
    pytest.param("has space@example.com", id="whitespace"),
    pytest.param("", id="empty"),
]


#: The subset that is refused outright *everywhere*, i.e. excluding the values
#: whose only defect is trailing whitespace — the recipient validator strips
#: those before shape-checking, so they normalise rather than 422 (asserted in
#: full by `test_scheduled_report_never_stores_a_malformed_recipient`). Derived
#: rather than restated so a new case joins both lists at once.
REJECTED_OUTRIGHT = [
    case
    for case in REJECTED
    if not (case.values[0].strip() and case.values[0].strip() != case.values[0])
]


def _unique_slug(prefix: str = "eshape") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _signup_body(slug: str, email: str) -> dict:
    return {
        "company_name": "Shape Co",
        "slug": slug,
        "admin_name": "Shape Tester",
        "admin_email": email,
        "captcha_token": "ignored-in-dev",
    }


def _schedule_body(**overrides) -> dict:
    body = {
        "name": "Weekly AP Register",
        "report_type": "invoice_register",
        "cadence": "weekly",
        "recipients": ["cfo@acme.test"],
        "period_days": 30,
    }
    body.update(overrides)
    return body


async def _no_signup_row(realdb, slug: str) -> bool:
    async with realdb.control_sessionmaker()() as s:
        row = (
            await s.execute(select(EmailVerification).where(EmailVerification.slug == slug))
        ).scalar_one_or_none()
    return row is None


async def _cleanup_org(realdb, slug: str) -> None:
    """Best-effort teardown for a slug a test may (but should not) have created."""
    async with realdb.control_sessionmaker()() as s:
        org = (
            await s.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if org is None:
            await s.execute(delete(EmailVerification).where(EmailVerification.slug == slug))
            await s.commit()
            return
        user_ids = (
            (await s.execute(select(User.id).where(User.organization_id == org.id))).scalars().all()
        )
        if user_ids:
            await s.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
            await s.execute(delete(User).where(User.id.in_(user_ids)))
        await s.execute(delete(EmailVerification).where(EmailVerification.slug == slug))
        await s.execute(delete(Organization).where(Organization.id == org.id))
        await s.commit()


# ---------------------------------------------------------------------------
# POST /api/signup/start — the address becomes a tenant admin's login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address", REJECTED)
async def test_signup_start_refuses_a_malformed_admin_email(realdb, address):
    slug = _unique_slug()
    try:
        async with realdb.client(key="a", role=None) as c:
            resp = await c.post("/api/signup/start", json=_signup_body(slug, address))
        assert resp.status_code == 422, resp.text
        # …and refused BEFORE any state exists. A verification row is a live
        # invitation carrying the address, so "rejected but recorded" would
        # still have put the newline into the mail the runner sends.
        assert await _no_signup_row(realdb, slug)
    finally:
        await _cleanup_org(realdb, slug)


async def test_signup_start_accepts_an_ordinary_address(realdb):
    """The control. Without it, a validator that rejected everything would pass
    every case above."""
    slug = _unique_slug()
    try:
        async with realdb.client(key="a", role=None) as c:
            resp = await c.post(
                "/api/signup/start", json=_signup_body(slug, f"admin+tag@{slug}.example.com")
            )
        assert resp.status_code in (200, 201), resp.text
        assert not await _no_signup_row(realdb, slug)
    finally:
        await _cleanup_org(realdb, slug)


# ---------------------------------------------------------------------------
# POST /api/partner/children/provision — a child tenant's admin login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address", REJECTED)
async def test_partner_provision_refuses_a_malformed_admin_email(realdb, address):
    """The email check runs BEFORE the slug validation and before any tenant
    resources exist — this asserts the 422 and that no org was created, because
    a half-provisioned child would leave an orphan database behind."""
    slug = _unique_slug("pshape")
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.post(
                "/api/partner/children/provision",
                json={"name": "Shape Child", "slug": slug, "admin_email": address},
            )
        assert resp.status_code == 422, resp.text
        async with realdb.control_sessionmaker()() as s:
            created = (
                await s.execute(select(Organization).where(Organization.slug == slug))
            ).scalar_one_or_none()
        assert created is None
    finally:
        await _cleanup_org(realdb, slug)


# ---------------------------------------------------------------------------
# Scheduled-report recipients — a recurring CSV of the tenant's AP spend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("address", REJECTED)
async def test_scheduled_report_never_stores_a_malformed_recipient(realdb, address):
    """Refused, or normalised — never stored as given.

    The recipient validator `.strip()`s before it shape-checks, so a *trailing*
    newline is trimmed off and the address behind it stored clean, while every
    other malformed value is a 422. Both outcomes are safe and the distinction
    is deliberate (a pasted list picks up trailing whitespace; a login does
    not, which is why signup and partner provisioning refuse outright). What
    must never happen is the third outcome — a stored recipient still carrying
    the newline, which is what `$` instead of `\Z` used to allow straight into
    an SMTP header.
    """
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(_SCHEDULES, json=_schedule_body(recipients=[address]))
    if resp.status_code == 201:
        stored = resp.json()["recipients"]
        assert stored == [address.strip()], resp.text
        assert all("\n" not in r and "\r" not in r for r in stored)
    else:
        assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("address", REJECTED_OUTRIGHT)
async def test_scheduled_report_refuses_a_malformed_recipient_among_valid_ones(realdb, address):
    """One bad address in a list of good ones refuses the whole list.

    A validator that filtered instead of refusing would silently drop a
    recipient the operator believes is subscribed — and the operator would have
    no way to tell, since the response echoes the cleaned list.
    """
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(_SCHEDULES, json=_schedule_body(recipients=["cfo@acme.test", address]))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("address", REJECTED_OUTRIGHT)
async def test_patch_cannot_smuggle_a_malformed_recipient_past_create(realdb, address):
    """PATCH is the second door onto the same list. A create-only check would
    leave it wide open — and an existing schedule is the more attractive target,
    because it is already enabled and already trusted."""
    async with realdb.client(key="a", role="admin") as c:
        created = await c.post(_SCHEDULES, json=_schedule_body())
        assert created.status_code == 201, created.text
        schedule_id = created.json()["id"]

        resp = await c.patch(
            f"{_SCHEDULES}/{schedule_id}", json={"recipients": ["cfo@acme.test", address]}
        )
        assert resp.status_code == 422, resp.text

        # The stored list is untouched — a rejected PATCH must not partially apply.
        after = (await c.get(f"{_SCHEDULES}/{schedule_id}")).json()
        assert after["recipients"] == ["cfo@acme.test"]


@pytest.mark.parametrize(
    "address",
    [
        "cfo@acme.test",
        "first.last@sub.domain.example.co.uk",
        "user+weekly-report@example.io",
        "UPPER@Example.COM",
    ],
)
async def test_scheduled_report_admits_the_addresses_the_rule_deliberately_allows(realdb, address):
    """The permissiveness is a decision, not an accident — tightening it should
    turn this red and force the decision to be retaken. Guards against a
    "harden the email check" change quietly cutting off recipients who are
    receiving reports today."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(_SCHEDULES, json=_schedule_body(recipients=[address]))
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipients"] == [address]


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def test_every_surface_that_shape_checks_an_address_uses_the_shared_rule():
    """The complement to `test_email_shape.py`'s regex scan.

    That one fails if the *pattern literal* is copied. This one fails if a
    surface grows its own ad-hoc check that doesn't use the pattern at all —
    an `"@" in value`, a `str.count("@")`, an `email.split("@")` guard — which
    is the cheaper way to reintroduce exactly the hole `\\Z` closed, since none
    of those spellings notice a trailing newline either.
    """
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    suspicious = ('"@" in', "'@' in", 'count("@")', "count('@')")
    #: Uses of `@` that are not shape checks, each with the reason it stays:
    exempt = {
        # Extracts the HOST from an address for a domain-keyed enrichment
        # provider. Not a decision about validity.
        "api/enrichment.py",
        # Decides whether a SAML NameID *is* an email or an opaque subject id,
        # falling through to the attribute statement when it isn't. Routing a
        # legitimately internal-domain NameID (`user@intranet`, which the shape
        # rule refuses for having no dot) down the fallback path would break
        # sign-in for a tenant whose IdP sends no email attribute. The address
        # this picks is then control-character-checked by
        # `identity_provisioning.extract_and_check_email`, which is where the
        # real exposure was.
        "api/auth_saml.py",
    }
    offenders: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        if str(path.relative_to(app_dir)) in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in suspicious:
            if needle in text:
                offenders.append(f"{path.relative_to(app_dir)} ({needle})")

    assert not offenders, (
        f"ad-hoc email shape check in {offenders}. Use "
        "`app.utils.emails.looks_like_email` — a hand-rolled `@` test admits a "
        "trailing newline, which is the SMTP header-injection primitive the "
        "shared pattern's `\\Z` anchor exists to refuse."
    )
