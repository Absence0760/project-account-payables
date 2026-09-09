"""Resolving a route's `require_permission(...)` gate — by code object, once.

Two suites need the same question answered: `test_rbac.py` (is this route
permission-gated, and on what?) and `test_sod_endpoint_wiring.py` (which routes
are permission-gated, and can a holder / non-holder actually get through?). Both
had their own copy of the resolution, and the copies had drifted apart in the
one way that matters:

    getattr(call, "__qualname__", "").endswith("require_permission.<locals>.checker")

is a **name** match. It is not a bypass — the qualname pins the enclosing factory,
so an unrelated helper called `checker` will not satisfy it — but it breaks on a
rename of the real thing, and a guard that silently stops finding its subject is
the failure mode these tests exist to prevent. `test_rbac.py` moved to comparing
`__code__` against the factory's own; `test_sod_endpoint_wiring.py` (the file the
follow-up cited as the good example) had not.

So the comparison lives here, once. `require_permission` builds a fresh closure
per call and every one of them shares a single code object, which identifies the
factory exactly and survives any rename.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.api.deps import require_permission

#: Every `require_permission(...)` closure shares this code object.
REQUIRE_PERMISSION_CODE = require_permission("user.manage").__code__


def iter_dependants(dependant) -> Iterable:
    """Every `Dependant` reachable from a route's dependency tree, inclusive."""
    yield dependant
    for sub in getattr(dependant, "dependencies", []) or []:
        yield from iter_dependants(sub)


def is_permission_gate(call: object) -> bool:
    """True when `call` is a checker built by `require_permission(...)`."""
    return getattr(call, "__code__", None) is REQUIRE_PERMISSION_CODE


def _captured_permissions(call: Callable) -> frozenset[str] | None:
    """The `needed_set` frozenset the checker closed over, if it has one."""
    for cell in getattr(call, "__closure__", None) or ():
        val = cell.cell_contents
        if isinstance(val, frozenset) and val and all(isinstance(v, str) for v in val):
            return val
    return None


def permission_checkers(route) -> list[Callable]:
    """Every `require_permission` checker reachable from `route`, in tree order."""
    return [
        dep.call
        for dep in iter_dependants(route.dependant)
        if is_permission_gate(getattr(dep, "call", None))
    ]


def permission_gate_sets(route) -> list[frozenset[str]]:
    """The permission set each `require_permission` gate on `route` requires.

    Reading the set out of the closure — rather than asserting merely that some
    auth dependency exists — is what makes the caller's assertion about the
    ACTUAL gate. A checker whose closure holds no string frozenset is skipped
    rather than reported as an empty gate.
    """
    sets = [_captured_permissions(call) for call in permission_checkers(route)]
    return [s for s in sets if s is not None]
