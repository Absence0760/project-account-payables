"""Hardened XML parsing helpers shared across the e_invoice package.

The project has no ``defusedxml`` dependency, so we harden ``lxml`` explicitly:
``resolve_entities=False`` + ``no_network=True`` + ``load_dtd=False`` block
XXE / external-entity / billion-laughs attacks. This mirrors the repo's
existing XXE-hardened ``python3-saml`` posture — structured e-invoices arrive
from untrusted senders (email intake, supplier portal), so every parse runs
through :func:`parse_secure`.

Helpers are namespace-aware but namespace-prefix-agnostic: they match on the
local element name (and optionally namespace) rather than a fixed prefix, so a
document that declares e.g. ``cbc:`` vs ``ns2:`` for the same namespace parses
identically.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree


def secure_parser() -> etree.XMLParser:
    """Return a freshly-configured hardened lxml parser.

    A new parser per call: lxml parsers are not thread-safe for shared reuse,
    and the e_invoice parsers run inside the extraction worker pool.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        dtd_validation=False,
        load_dtd=False,
        huge_tree=False,
    )


def parse_secure(xml_bytes: bytes) -> etree._Element:
    """Parse bytes into a root element with the hardened parser.

    Raises ``lxml.etree.XMLSyntaxError`` on malformed input (the caller in
    :mod:`parse` translates that into an :class:`EInvoiceValidationError`).
    """
    return etree.fromstring(xml_bytes, parser=secure_parser())


def local_name(el: etree._Element) -> str:
    """Local (namespace-stripped) tag name of an element."""
    return etree.QName(el).localname


def namespace_of(el: etree._Element) -> str | None:
    """Namespace URI of an element, or None."""
    return etree.QName(el).namespace


def _child_by_local(parent: etree._Element, name: str) -> etree._Element | None:
    """First direct child whose local name matches ``name`` (ns-agnostic)."""
    for child in parent:
        # Skip comments / processing instructions (no tag string).
        if not isinstance(child.tag, str):
            continue
        if etree.QName(child).localname == name:
            return child
    return None


def find_path(root: etree._Element, *names: str) -> etree._Element | None:
    """Walk a chain of local element names from ``root``; None if any hop misses.

    ``find_path(root, "AccountingSupplierParty", "Party")`` returns the nested
    ``Party`` regardless of namespace prefixes.
    """
    cur: etree._Element | None = root
    for name in names:
        if cur is None:
            return None
        cur = _child_by_local(cur, name)
    return cur


def find_all_local(root: etree._Element, name: str) -> list[etree._Element]:
    """All descendant elements with the given local name (any depth)."""
    return [el for el in root.iter() if isinstance(el.tag, str) and local_name(el) == name]


def find_text(root: etree._Element | None, *names: str) -> str | None:
    """Text of the element reached by ``find_path``; None / empty → None."""
    if root is None:
        return None
    el = find_path(root, *names)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def to_decimal(value: str | None) -> Decimal | None:
    """Parse a string amount into ``Decimal``; never ``float``, never raises."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def to_date(value: str | None) -> date | None:
    """Parse a UBL/CII date string into ``date``; never raises.

    Handles UBL ``YYYY-MM-DD`` (cbc:IssueDate) and CII ``YYYYMMDD`` (the
    ``udt:DateTimeString`` ``format="102"`` basic-date form), plus a full
    ISO datetime (some CII emitters include a time component).
    """
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    # UBL / ISO date first.
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # CII basic date (format 102): YYYYMMDD.
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    # ISO datetime with a time component.
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None
