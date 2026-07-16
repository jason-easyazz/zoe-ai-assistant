"""Pin the owner-scoping predicates of scripts/maintenance/purge_orphaned_test_data.py.

The purge tool soft-deletes rows in the LIVE household database, so its owner
patterns are safety-critical in both directions:

  * too narrow -> orphaned test junk survives and shows on the family panel
    (the operator's recurring "dentist spam");
  * too wide   -> it soft-deletes a real household member's calendar/lists.

These tests exercise the predicate logic only -- no database, no network -- so
they are slim-dep-green and run in the fast GitHub lane.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "maintenance" / "purge_orphaned_test_data.py"
)


def _load():
    """Import the purge script by path (scripts/ is not an importable package).

    Safe at import time: the module performs no I/O at import (argv parsing and
    the database connection are both behind `if __name__ == "__main__"`), and it
    imports asyncpg lazily inside main(), so this test needs no PostgreSQL
    driver. That decoupling is deliberate and pinned by
    test_module_imports_without_a_db_driver below: it keeps these
    safety-critical assertions running unconditionally rather than
    importorskip-ing themselves into a silent pass.
    """
    spec = importlib.util.spec_from_file_location("purge_orphaned_test_data", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


purge = _load()


# Owner ids minted by the retired ad-hoc smoke scripts, as
# f"<prefix>_{int(time.time())}". 1752624000 is a realistic 10-digit stamp.
JUNK_OWNERS = [
    "test_calendar_1752624000",
    "test_shopping_1752624000",
    "test_memory_1752624000",
    "test_isolation_a_1752624000",
    "test_isolation_b_1752624000",
    "final_test_1752624000",
    "final_test_2_1752624000",
]

# Real / plausible household + system owners. NONE of these may ever match.
REAL_OWNERS = [
    "jason",
    "Jason",
    "jason@easyazz.com",
    "zoe-touch-pi",
    "panel_abc123",
    "admin",
    "user_1752624000",          # digit-suffixed, but not an enumerated prefix
    "test_memory",              # prefix alone, no timestamp
    "test_memory_notes",        # prefix + non-digit suffix (a LIKE would match!)
    "test_memory_2024_notes",   # digits present but not a pure suffix
    "xtest_memory_1752624000",  # unanchored head
    "test_memory_1752624000x",  # unanchored tail
    "test_memory_12345",        # too-short suffix to be a unix timestamp
    "test_isolation_c_1752624000",  # only a/b were ever written
    "final_test_3_1752624000",      # only final_test / final_test_2 were written
]


@pytest.mark.parametrize("owner", JUNK_OWNERS)
def test_regex_matches_every_retired_script_owner(owner):
    assert re.match(purge.TEST_OWNER_RE, owner), (
        f"{owner!r} is junk written by a retired smoke script but the purge "
        f"predicate no longer matches it -- it would survive the sweep."
    )


@pytest.mark.parametrize("owner", REAL_OWNERS)
def test_regex_never_matches_a_real_owner(owner):
    assert not re.match(purge.TEST_OWNER_RE, owner), (
        f"{owner!r} MUST NOT match the purge predicate -- this pattern would "
        f"soft-delete real household data."
    )


def test_regex_is_fully_anchored():
    """Anchoring is the core of the safety argument; assert it structurally."""
    assert purge.TEST_OWNER_RE.startswith("^")
    assert purge.TEST_OWNER_RE.endswith("$")


def test_owner_pred_binds_the_requested_column():
    pred = purge.owner_pred("l.user_id")
    assert "l.user_id = 'guest'" in pred
    assert "l.user_id LIKE 'test-sec-b-%'" in pred
    assert "l.user_id ~ " in pred
    # the bare column must not leak through unqualified (the old .replace() trap)
    assert not re.search(r"(?<![.\w])user_id", pred)


def test_owner_pred_defaults_to_user_id():
    assert purge.owner_pred() == purge.owner_pred("user_id") == purge.EVENT_PRED


def test_legacy_scopes_still_covered():
    """The original guest / security-test scopes must not regress."""
    pred = purge.owner_pred()
    assert "'guest'" in pred
    assert "test-sec-b-%" in pred


def test_module_imports_without_a_db_driver():
    """The predicate constants must be importable with no PostgreSQL driver.

    If asyncpg (a C-extension) ever regains a module-level import, this file
    could only survive CI by importorskip-ing -- i.e. silently skipping the
    checks that stop the purge tool eating real household data. Pin the
    decoupling instead: hide asyncpg and re-import from scratch.
    """
    import builtins

    real_import = builtins.__import__

    def no_asyncpg(name, *a, **kw):
        if name == "asyncpg" or name.startswith("asyncpg."):
            raise ModuleNotFoundError("No module named 'asyncpg'")
        return real_import(name, *a, **kw)

    saved = sys.modules.pop("asyncpg", None)
    builtins.__import__ = no_asyncpg
    try:
        mod = _load()
        assert mod.TEST_OWNER_RE == purge.TEST_OWNER_RE
        assert mod.owner_pred("user_id") == purge.owner_pred("user_id")
    finally:
        builtins.__import__ = real_import
        if saved is not None:
            sys.modules["asyncpg"] = saved


def test_pred_is_a_single_or_group():
    """Wrapped in parens: it is interpolated next to `AND deleted = 0`, where a
    bare OR-chain would bind wrong and widen the sweep to the whole table."""
    pred = purge.owner_pred()
    assert pred.startswith("(") and pred.endswith(")")
