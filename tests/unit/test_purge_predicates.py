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


# --------------------------------------------------------------------------- #
# Chat arm — HARD delete, so the owner set is exact and closed.
# --------------------------------------------------------------------------- #
CHAT_JUNK_OWNERS = ["test-route-probe", "test-sec-b-4f9c0c", "test-sec-b-000000"]

CHAT_KEEP_OWNERS = REAL_OWNERS + [
    "guest",                      # shared kiosk: real household turns live here
    "test-route-probe-2",         # unanchored tail
    "xtest-route-probe",          # unanchored head
    "test-sec-b-4F9C0C",          # token_hex is lowercase
    "test-sec-b-4f9c0",           # 5 hex, not token_hex(3)
    "test-sec-b-4f9c0c1",         # 7 hex
    "test-sec-b-zzzzzz",          # not hex
    "demo_isoA_1a2b",             # samantha_live demo users tear themselves down
    "test_memory_1752624000",     # calendar/list junk, not a chat probe
]


@pytest.mark.parametrize("owner", CHAT_JUNK_OWNERS)
def test_chat_regex_matches_probe_owners(owner):
    assert re.match(purge.CHAT_OWNER_RE, owner)


@pytest.mark.parametrize("owner", CHAT_KEEP_OWNERS)
def test_chat_regex_never_matches_anything_else(owner):
    assert not re.match(purge.CHAT_OWNER_RE, owner), (
        f"{owner!r} MUST NOT match the chat purge -- it hard-deletes sessions."
    )


def test_chat_pred_is_anchored_grouped_and_guards_foreign_turns():
    pred = purge.chat_session_pred("cs")
    assert purge.CHAT_OWNER_RE.startswith("^") and purge.CHAT_OWNER_RE.endswith("$")
    assert pred.startswith("(") and pred.endswith(")")
    assert f"cs.user_id ~ '{purge.CHAT_OWNER_RE}'" in pred
    # The second rail: a session with any turn naming a different owner is kept.
    assert "NOT EXISTS" in pred and "m.session_id = cs.id" in pred
    assert "<> cs.user_id" in pred


# --------------------------------------------------------------------------- #
# Chat arm: verified backup BEFORE the hard delete (fake conn, no database)
# --------------------------------------------------------------------------- #
class _FakeConn:
    def __init__(self, delete_count=None):
        self.sessions = [{"id": "s1", "user_id": "test-route-probe"},
                         {"id": "s2", "user_id": "test-sec-b-4f9c0c"}]
        self.messages = [{"id": "m1", "session_id": "s1", "content": "octopus fact"},
                         {"id": "m2", "session_id": "s1", "content": "again"},
                         {"id": "m3", "session_id": "s2", "content": "sec probe"}]
        self.runs = [{"id": "r1", "session_id": "s1"}]
        self.deletes = []
        self.delete_count = delete_count

    async def fetch(self, sql, *args):
        if "FROM chat_sessions" in sql:
            return self.sessions
        if "FROM chat_messages" in sql:
            return self.messages
        if "FROM chat_ag_ui_runs" in sql:
            return self.runs
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        assert sql.startswith("DELETE FROM chat_sessions")
        assert purge.chat_session_pred("cs") in sql  # still scoped by the predicate
        self.deletes.append(list(args[0]))
        n = len(args[0]) if self.delete_count is None else self.delete_count
        return f"DELETE {n}"


def test_chat_purge_writes_verified_backup_then_deletes_exactly_it(tmp_path):
    import asyncio
    import json

    conn = _FakeConn()
    path, deleted = asyncio.run(purge.purge_chat(conn, str(tmp_path / "purge"), "2026-09-28"))
    back = json.loads(open(path).read())
    assert path.endswith("2026-09-28-chat.json")
    assert [len(back[t]) for t in ("chat_sessions", "chat_messages", "chat_ag_ui_runs")] == [2, 3, 1]
    assert conn.deletes == [["s1", "s2"]] and deleted == 2


def test_chat_purge_refuses_delete_when_backup_fails(tmp_path):
    import asyncio

    blocker = tmp_path / "purge"
    blocker.write_text("a FILE where the backup dir should be")  # makedirs fails
    conn = _FakeConn()
    with pytest.raises(OSError):
        asyncio.run(purge.purge_chat(conn, str(blocker), "2026-09-28"))
    assert conn.deletes == []  # nothing deleted without a backup


def test_chat_purge_raises_when_delete_count_differs_from_backup(tmp_path):
    import asyncio

    with pytest.raises(RuntimeError, match="backed up 2"):
        asyncio.run(purge.purge_chat(_FakeConn(delete_count=1), str(tmp_path), "x"))


def test_backup_prunes_files_older_than_14_days(tmp_path):
    import os
    import time

    old = tmp_path / "2026-09-01-chat.json"
    old.write_text("{}")
    os.utime(old, (time.time() - 15 * 86400,) * 2)
    keep = tmp_path / "2026-09-20-chat.json"
    keep.write_text("{}")
    purge.write_verified_backup({"chat_sessions": []}, str(tmp_path), "2026-09-28")
    assert not old.exists() and keep.exists()
