"""tests/unit guard: a test module must not leave a STUB under a real repo module's name in ``sys.modules``.

Why this exists (2026-10-07): ``test_intent_router.py`` installed stub ``memory_digest`` / ``openclaw_ws`` /
``database`` ... modules at import time and relied on a ``teardown_module`` hook to pop them. Import time is
collection time, so the stubs were live for every other test module collected or run in the same process, and the
hook never ran at all when that module's tests were deselected (``-k``, ``-m``, ``--deselect``). Result: every later
``import memory_digest`` returned the empty stub and ``tests/unit/test_zmb_*`` failed 22-31 tests with
``module 'memory_digest' has no attribute '_load_todays_messages'`` — in an order- and selection-dependent way, so it
looked like flakiness.

The rule this file enforces: stubs belong in a fixture (``monkeypatch.setitem(sys.modules, ...)`` or a
snapshot/restore fixture), never at module scope. After every test module (and before the first), any entry in
``sys.modules`` whose name is one of the repo's own ``services/zoe-data`` modules but which is a stub — a
``MagicMock``/``SimpleNamespace`` or a module object with no ``__file__`` — fails the run with a message that names
the module and the way to fix it. Stubs are also evicted at collection time (``pytest_collectstart``) so a later
module's module-scope ``from x import y`` cannot hit one. Pinned by ``test_sys_modules_stub_guard.py``.
"""
from __future__ import annotations

import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

_ZOE_DATA = Path(__file__).resolve().parents[2] / "services" / "zoe-data"


def _repo_module_names() -> frozenset[str]:
    names = {p.stem for p in _ZOE_DATA.glob("*.py") if not p.name.startswith(("test_", "__"))}
    names |= {p.name for p in _ZOE_DATA.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return frozenset(names)


_REPO_MODULES = _repo_module_names()
# Stubs found while COLLECTING (they were installed at import time by an earlier module). They are evicted
# immediately - so the next module's collection-time imports get the real module - and reported by the fixture below.
_PENDING: dict[str, str] = {}


def stubbed_repo_modules(modules=None) -> dict[str, str]:
    """``{name: why}`` for every sys.modules entry under a repo module's name that is a stub, not the real module."""
    modules = sys.modules if modules is None else modules
    found: dict[str, str] = {}
    for name in _REPO_MODULES:
        mod = modules.get(name)
        if mod is None:
            continue
        if isinstance(mod, (mock.NonCallableMock, types.SimpleNamespace)):
            found[name] = f"a {type(mod).__name__} object"
        elif not getattr(mod, "__file__", None) and not getattr(mod, "__path__", None):
            found[name] = "a module object with no __file__"
    return found


def _evict_stubs() -> dict[str, str]:
    """Remove every stub from sys.modules (the next import loads the real module again) and return what was removed."""
    stubs = stubbed_repo_modules()
    for name in stubs:
        sys.modules.pop(name, None)
    return stubs


def pytest_collectstart(collector) -> None:
    """Collection-time guard: evict stubs left by an already-imported module BEFORE the next module is imported.

    Import time is collection time, so a module-scoped fixture alone runs too late - a later module doing
    ``from memory_digest import _load_todays_messages`` at module scope would already have died with a collection
    error on the stub. The leak is recorded in ``_PENDING`` and surfaced as a failure by the fixture.
    """
    if isinstance(collector, pytest.Module):
        _PENDING.update(_evict_stubs())


def _fail_on_stubs(when: str, module_name: str) -> None:
    # Evict (not just report): a stub left in sys.modules would make every later module fail the same way, which is
    # the cascade this guard exists to stop.
    found = {**_PENDING, **_evict_stubs()}
    _PENDING.clear()
    if not found:
        return
    lines = "\n".join(f"  - sys.modules[{n!r}] is {why}" for n, why in sorted(found.items()))
    pytest.fail(
        f"sys.modules stub leak ({when} {module_name}; the stubs were evicted so later modules see the real ones):\n{lines}\n"
        "A test module left a stub under a real repo module's name, so every later `import` of it gets the empty "
        "stub (e.g. \"module 'memory_digest' has no attribute '_load_todays_messages'\"). Install stubs in a "
        "fixture - `monkeypatch.setitem(sys.modules, name, stub)` or a module-scoped fixture that restores the "
        "original entries in a finally - never at import/module scope; `teardown_module` does not run when the "
        "module's tests are deselected.",
        pytrace=False,
    )


@pytest.fixture(autouse=True, scope="module")
def _no_sys_modules_stub_leaks(request):
    name = request.module.__name__
    _fail_on_stubs("present before", name)
    yield
    _fail_on_stubs("left behind by", name)
