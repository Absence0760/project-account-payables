r"""One definition of "does this look like an email address" for the backend.

Three modules used to carry their own copy of the same regex — ``api/signup.py``
(who gets a tenant), ``api/partner.py`` (who administers a partner-provisioned
child tenant) and ``schemas/scheduled_report.py`` (who receives a recurring CSV
of the tenant's AP spend). All three decide **who receives a tenant's data**, so
three copies of one rule is exactly the shape that drifts: tighten one and the
other two keep admitting what it now refuses, silently.

The check is deliberately **permissive**. It exists to reject obvious garbage —
no ``@``, whitespace, a bare hostname, a trailing dot — not to decide
deliverability. The real validation is a round trip: signup and partner
provisioning both mail the address a link that must be clicked, and a scheduled
report that can't be delivered surfaces as a delivery failure on the schedule.
Pulling in ``email-validator`` to be stricter would buy syntax rigour the RFC
itself makes nearly unusable (quoted local parts, address literals) without
answering the question anyone actually cares about.

The domain side uses non-dot character classes delimited by literal dots rather
than a nested quantifier, so the engine can't backtrack catastrophically on an
adversarial input (the polynomial-ReDoS pattern). Keep that shape if the rule
is ever tightened.

One deliberate tightening came with the hoist: the three copies all ended in
``$``, which in Python matches at end-of-string **or just before a trailing
newline** — so ``"user@example.com\n"`` passed all three shape checks and was
stored as a login, a partner child's admin address, or a report recipient. The
shared pattern ends in ``\Z`` instead, which matches only the true end. A
trailing newline in an address is never legitimate input, and a newline in a
value that reaches an SMTP header is the header-injection primitive, so the
narrower anchor is the safe end of the trade.

``tests/test_email_shape.py`` pins the behaviour in both directions — including
the cases this pattern deliberately ADMITS — so a later tightening is a
conscious edit to a failing test rather than a silent change in who can be
mailed.
"""

from __future__ import annotations

import re

__all__ = ["EMAIL_SHAPE_PATTERN", "is_header_safe", "looks_like_email"]

#: Permissive shape check. Local part: any run of non-space, non-``@``. Domain:
#: one or more dot-delimited labels of non-space, non-``@``, non-dot characters
#: — so ``a@b`` (no dot) and ``a@b.`` (trailing dot) are both refused. Anchored
#: with ``\Z``, not ``$``: see the module docstring.
EMAIL_SHAPE_PATTERN = re.compile(r"^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+\Z")


#: Characters that can never legitimately appear in an email address and must
#: never reach a mail header: C0 controls (CR and LF above all) plus DEL. CR/LF
#: are the header-injection primitive — a value carrying one can continue the
#: header it lands in, e.g. with an attacker-chosen ``Bcc:``.
_HEADER_UNSAFE_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def is_header_safe(value: str) -> bool:
    r"""True when ``value`` carries nothing that could break out of a mail header.

    Weaker than :func:`looks_like_email` and deliberately so. It exists for the
    one caller that must NOT impose the full shape rule: an SSO-supplied address
    (`services/identity_provisioning.extract_and_check_email`). A corporate IdP
    can legitimately assert an internal-only address like ``user@intranet`` with
    no dot in the domain, and refusing that would lock a whole tenant out of its
    own workspace over a cosmetic rule. A control character is a different
    matter — no IdP has a legitimate reason to emit one, and the value becomes
    both a login and a mail destination.
    """
    return _HEADER_UNSAFE_PATTERN.search(value) is None


def looks_like_email(value: str) -> bool:
    """True when ``value`` has the shape of an email address.

    Shape only — never deliverability, and never a claim the mailbox exists.
    Callers raise their own error (a 422 in the API layer, a ``ValueError`` in
    a pydantic validator) so the message can stay appropriate to the surface;
    the recipient-list validator in particular must not echo the offending
    value, which is third-party PII on an HTTP error body.
    """
    return bool(EMAIL_SHAPE_PATTERN.match(value))
