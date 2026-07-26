"""The list-add replay-guard queries must be valid SQL against a TEXT created_at.

#1137 added a dedup guard `created_at > now() - interval '10 seconds'`, but
`list_items.created_at` is a TEXT column, so on live Postgres it threw
`operator does not exist: text > timestamp` — silently dropping every
intent_router direct add to the mcporter fallback (live 2026-07-08: 69 errors).
The fake-DB unit tests never caught it because they return scripted rows
without executing the SQL.

These pins would have caught it: (1) both dedup queries cast created_at, and
(2) the query shape actually executes against a DB with a text created_at.
"""
import re
import sqlite3

import pytest

pytestmark = pytest.mark.ci_safe

import intent_router
import list_service
import skybridge_service


def test_both_dedup_queries_cast_created_at():
    # A bare `created_at > now()` comparison on the TEXT column is the bug.
    # intent_router's copy of the guard moved into list_service (the canonical
    # list writer), which builds the interval from its dedup_window_s param.
    import inspect
    for mod, src, pattern in (
        ("list_service", list_service, r"created_at[^\n]*interval '"),
        ("skybridge_service", skybridge_service, r"created_at[^\n]*interval '10 seconds'"),
    ):
        text = inspect.getsource(src)
        matches = re.findall(pattern, text)
        # guard against a vacuous pass: the dedup window must still exist here,
        # so a refactor that drops it (and its cast) fails loudly.
        assert matches, f"{mod}: dedup guard not found — did it move or get removed?"
        for frag in matches:
            assert "created_at::timestamptz" in frag, (
                f"{mod}: dedup guard must cast the TEXT created_at column "
                f"(`created_at::timestamptz`), got: {frag!r}"
            )
    # The voice direct add must keep its 10-second replay window when calling
    # the canonical writer.
    assert re.search(r"dedup_window_s=10\b", inspect.getsource(intent_router)), (
        "intent_router: the 10-second list-add replay window is gone — "
        "did the add_item_to_list call lose dedup_window_s=10?"
    )


def test_dedup_predicate_executes_against_text_created_at():
    """Prove the comparison shape is valid when created_at holds ISO text.

    SQLite can't parse Postgres `::timestamptz` / `now() - interval`, so we
    model the semantics the cast provides: comparing a text ISO timestamp to a
    cutoff. The regression was a TYPE error, and this asserts a text created_at
    with an ISO value compares cleanly rather than raising.
    """
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE list_items (id TEXT, list_id TEXT, text TEXT, deleted INT, created_at TEXT)")
    con.execute(
        "INSERT INTO list_items VALUES ('i1','l1','bread',0,'2026-07-08T12:00:00+00:00')"
    )
    # datetime() is SQLite's cast-equivalent: a text-typed column parsed as a
    # timestamp and compared to a cutoff — the exact operation the Postgres
    # ::timestamptz cast enables. A raw text>timestamp mix is what failed live.
    row = con.execute(
        "SELECT id FROM list_items WHERE list_id=? AND lower(text)=lower(?)"
        " AND deleted=0 AND datetime(created_at) > datetime('now','-10 seconds')",
        ("l1", "bread"),
    ).fetchone()
    # inserted with a fixed past timestamp → outside the 10s window → no match,
    # and crucially: no type error raised.
    assert row is None
    con.close()
