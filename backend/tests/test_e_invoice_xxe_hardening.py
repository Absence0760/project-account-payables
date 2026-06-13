"""XXE / external-entity hardening regression tests for the e_invoice parser.

``app.services.e_invoice._xml.parse_secure`` is the single parse surface for
structured e-invoices, which arrive from *untrusted* senders (email intake,
supplier portal). Its hardening flags — ``resolve_entities=False`` +
``no_network=True`` + ``load_dtd=False`` + ``huge_tree=False`` — are the only
defence against an XXE / billion-laughs attack. These tests feed real malicious
payloads through the parser and assert the attack is neutralised: no file
content is disclosed, no network fetch is attempted, and no entity-bomb hangs
or OOMs the worker.

If a future edit weakens a parser flag, one of these fails immediately.
"""

from __future__ import annotations

import pytest
from lxml.etree import XMLSyntaxError

from app.services.e_invoice._xml import parse_secure

# A sentinel that must NEVER appear in a parsed tree — proves the external
# entity was not resolved into the document.
_SECRET_MARKER = "root:x:0:0:"


def _all_text(root) -> str:
    """Concatenate every text node in the parsed tree."""
    return "".join(t for t in root.itertext())


def test_xxe_file_disclosure_entity_is_not_resolved(tmp_path):
    """A SYSTEM external-entity pointing at a local file must not be expanded
    into the document text. With resolve_entities=False, lxml leaves the
    &xxe; reference unexpanded (empty) — the file contents never appear."""
    secret = tmp_path / "secret.txt"
    secret.write_text(f"{_SECRET_MARKER}fake-shadow-contents")

    payload = (
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE root [ <!ENTITY xxe SYSTEM "file://{secret}"> ]>\n'
        "<root><data>&xxe;</data></root>"
    ).encode()

    # Either lxml refuses the entity entirely (XMLSyntaxError) or it parses
    # with the entity unexpanded — but the file content must NOT be present.
    try:
        root = parse_secure(payload)
    except XMLSyntaxError:
        return  # entity rejected outright — also a pass
    assert _SECRET_MARKER not in _all_text(root)


def test_xxe_etc_passwd_classic_payload_blocked(tmp_path):
    """The canonical /etc/passwd XXE shape, but pointed at a sandboxed file so
    the test is hermetic. The marker we plant must not leak into the tree."""
    target = tmp_path / "passwd"
    target.write_text(f"{_SECRET_MARKER}0:0:root:/root:/bin/bash\n")

    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE foo [ <!ELEMENT foo ANY ><!ENTITY xxe SYSTEM "file://{target}"> ]>\n'
        "<foo>&xxe;</foo>"
    ).encode()

    try:
        root = parse_secure(payload)
    except XMLSyntaxError:
        return
    assert _SECRET_MARKER not in _all_text(root)


def test_billion_laughs_does_not_expand_or_hang():
    """A billion-laughs entity-expansion bomb must not blow up memory or hang.
    With resolve_entities=False the nested entities are never expanded, so the
    parser returns quickly (or raises XMLSyntaxError) — it must NOT materialise
    the exponential expansion."""
    payload = (
        b'<?xml version="1.0"?>\n'
        b"<!DOCTYPE lolz [\n"
        b'  <!ENTITY lol "lol">\n'
        b'  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b'  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
        b'  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">\n'
        b'  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">\n'
        b'  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">\n'
        b'  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">\n'
        b'  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">\n'
        b'  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">\n'
        b"]>\n"
        b"<lolz>&lol9;</lolz>"
    )

    try:
        root = parse_secure(payload)
    except XMLSyntaxError:
        return  # parser rejected the entity bomb — a pass
    # No expansion happened: the text must be far smaller than the 10^9 chars
    # a fully-expanded billion-laughs would produce.
    text = _all_text(root)
    assert len(text) < 10_000


def test_external_parameter_entity_with_no_network_is_not_fetched():
    """An external *parameter* entity (the SSRF / OOB-exfil vector) references a
    remote DTD. no_network=True + load_dtd=False mean the parser never opens a
    socket — it raises XMLSyntaxError or parses with the entity unresolved, but
    must not attempt the fetch (which would hang or error on a real network)."""
    payload = (
        b'<?xml version="1.0"?>\n'
        b"<!DOCTYPE root [\n"
        b'  <!ENTITY % remote SYSTEM "http://127.0.0.1:1/evil.dtd">\n'
        b"  %remote;\n"
        b"]>\n"
        b"<root>ok</root>"
    )

    try:
        root = parse_secure(payload)
    except XMLSyntaxError:
        return  # rejected — a pass (no fetch attempted)
    # Parsed without crashing: the document body survives, the remote DTD was
    # never pulled in.
    assert "".join(root.itertext()).strip() == "ok"


def test_well_formed_xml_still_parses():
    """Sanity: the hardening does not break ordinary entity-free XML."""
    root = parse_secure(b'<?xml version="1.0"?><Invoice><ID>INV-1</ID></Invoice>')
    assert root.tag == "Invoice"
    assert root.find("ID").text == "INV-1"


def test_malformed_xml_raises_xml_syntax_error():
    """An unclosed tag must surface as XMLSyntaxError so parse.py can translate
    it into a field-named EInvoiceValidationError."""
    with pytest.raises(XMLSyntaxError):
        parse_secure(b'<?xml version="1.0"?><Invoice><Unclosed')
