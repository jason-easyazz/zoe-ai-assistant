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
the module and the way to fix it. Pinned by ``test_sys_modules_stub_guard.py``.
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
# Names already reported, so one leak is one failure and not a cascade across every later module.
_REPORTED: set[str] = set()


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


def _fail_on_stubs(when: str, module_name: str) -> None:
    fresh = {n: why for n, why in stubbed_repo_modules().items() if n not in _REPORTED}
    if not fresh:
        return
    _REPORTED.update(fresh)
    lines = "\n".join(f"  - sys.modules[{n!r}] is {why}" for n, why in sorted(fresh.items()))
    pytest.fail(
        f"sys.modules stub leak ({when} {module_name}):\n{lines}\n"
        "A test module left a stub under a real repo module's name, so every later `import` of it gets the empty "
        "stub (e.g. \"module 'memory_digest' has no attribute '_load_todays_messages'\"). Install stubs in a "
        "fixture — `monkeypatch.setitem(sys.modules, name, stub)` or a module-scoped fixture that restores the "
        "original entries in a finally — never at import/module scope; `teardown_module` does not run when the "
        "module's tests are deselected.",
        pytrace=False,
    )


@pytest.fixture(autouse=True, scope="module")
def _no_sys_modules_stub_leaks(request):
    name = request.module.__name__
    _fail_on_stubs("present before", name)
    yield
    _fail_on_stubs("left behind by", name)
