"""A parked transcript flag (``exact_words.park_transcript`` -> ``pending-erases.json`` "messages" -> ``turn_forget.finish_parked``)
on REAL PostgreSQL: the by-id read ``exact_words._transcript_rows`` and its owner rule, the park, a restart, and the finish.

Opt-in, against a THROWAWAY database only (its name must contain "test" or "scratch"; anything else is refused):

    ZOE_TEST_PG_URL=postgresql://... python3 -B -m pytest -p no:cacheprovider -q services/zoe-data/tests/test_exact_words_transcript_postgres.py

Run it FROM THE REPO ROOT with the system python3 (the root pytest.ini: pythonpath, asyncio mode). ``ZOE_REQUIRE_PG_TESTS=1`` makes
a missing URL a failure instead of a skip (the convention of ``test_list_rename_postgres.py``).

Not ``ci_safe`` (the slim lane has no database); without the variable every test skips. Each test runs in its OWN new schema holding
only ``users`` / ``chat_sessions`` / ``chat_messages``, created from migration 0001's own CREATE TABLE text and dropped with CASCADE
at teardown, so the database's other contents and its migration state are never read or changed. Real code throughout: the
statements of ``exact_words`` over ``db_pool.AsyncpgCompat`` connections (``db_pool.get_db_ctx`` hands out fresh ones in the test
schema), the real park file, the real worker and replay against the fake kernel. Synthetic member ids and words only.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

URL = os.environ.get("ZOE_TEST_PG_URL")
REQUIRED = bool(os.environ.get("ZOE_REQUIRE_PG_TESTS"))
if REQUIRED:
    assert URL, "ZOE_REQUIRE_PG_TESTS is set but no scratch PostgreSQL URL (ZOE_TEST_PG_URL) was supplied"
    import asyncpg
else:
    asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(not URL, reason="no scratch PostgreSQL (ZOE_TEST_PG_URL)")

import db_pool  # noqa: E402
import exact_words  # noqa: E402
import os_shadow  # noqa: E402
import turn_forget  # noqa: E402
from test_os_shadow import add, kernel  # noqa: E402,F401  (kernel is a fixture: the fake kernel and its state directory)

_INITIAL = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial_schema.py"
SAID = "the synthetic garden grows lavender"
NEEDLE = "synthetic"


def _create_sql(table: str) -> str:
    """Migration 0001's own ``CREATE TABLE IF NOT EXISTS <table> (...)`` statement, by matching parentheses."""
    text = _INITIAL.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\(", text)
    assert m, f"no CREATE TABLE for {table}"
    depth, i = 1, m.end()
    while depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    return text[m.start():i]


def _throwaway_or_refuse(url: str) -> None:
    name = (urlparse(url).path or "").lstrip("/").lower()
    if "test" not in name and "scratch" not in name:
        pytest.fail(f"refusing to run: database name {name!r} contains neither 'test' nor 'scratch' (throwaway databases only)")


class World:
    def __init__(self, schema: str):
        self.schema = schema
        self.raws: list = []
        self.a, self.b = ("m-" + uuid.uuid4().hex[:8] for _ in range(2))

    async def raw(self):
        c = await asyncpg.connect(URL, server_settings={"search_path": self.schema})
        self.raws.append(c)
        return c

    @contextlib.asynccontextmanager
    async def ctx(self):
        """``db_pool.get_db_ctx`` for ``exact_words`` (read at call time): a FRESH connection in the test schema."""
        yield db_pool.AsyncpgCompat(await self.raw())

    async def meta(self, message_id: str) -> dict:
        c = await self.raw()                                      # a SEPARATE connection: only committed rows are visible
        raw = await c.fetchval("SELECT metadata FROM chat_messages WHERE id = $1", message_id)
        return json.loads(raw) if raw else {}


@pytest.fixture
async def world(monkeypatch, kernel):  # noqa: F811
    """A and B, each with a session and turns; a guest-owned kiosk session in which A spoke (the turn's metadata names A)."""
    _throwaway_or_refuse(URL)
    w = World("xw_" + uuid.uuid4().hex[:12])
    admin = await asyncpg.connect(URL)
    await admin.execute(f'CREATE SCHEMA "{w.schema}"')
    c = await w.raw()
    for table in ("users", "chat_sessions", "chat_messages"):
        await c.execute(_create_sql(table))
    for uid in (w.a, w.b, "guest"):
        await c.execute("INSERT INTO users (id, name) VALUES ($1, 'synthetic')", uid)
        await c.execute("INSERT INTO chat_sessions (id, user_id) VALUES ($1, $2)", f"s-{uid}", uid)
    rows = (
        ("a-said", f"s-{w.a}", "user", f"I said {SAID} today", None),
        ("a-other", f"s-{w.a}", "user", "an unrelated synthetic remark about the weather", None),
        ("a-reply", f"s-{w.a}", "assistant", f"you said {SAID}", None),
        ("a-kiosk", "s-guest", "user", SAID, json.dumps({"user_id": w.a, "panel": "kitchen"})),
        ("b-said", f"s-{w.b}", "user", SAID, None),
    )
    for mid, sid, role, content, metadata in rows:
        await c.execute("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ($1, $2, $3, $4, $5)",
                        mid, sid, role, content, metadata)
    monkeypatch.setattr(db_pool, "get_db_ctx", w.ctx)
    exact_words.set_backend(exact_words.SqlBackend())             # the production backend: forget_transcript acts only with it
    turn_forget.reset()
    try:
        yield w
    finally:
        exact_words.set_backend(None)
        turn_forget.reset()
        for raw in w.raws:
            await raw.close()
        await admin.execute(f'DROP SCHEMA "{w.schema}" CASCADE')
        await admin.close()


def _parked(kernel_) -> dict:
    return json.loads((kernel_.state / "pending-erases.json").read_text())


async def _until(cond, timeout=6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


async def test_the_by_id_read_returns_only_the_owners_own_user_turns(world):
    """``_transcript_rows`` on the real schema: the owner's user turns among the ids asked for - their own session's and the turn they
    spoke in a guest-owned session (the per-turn owner wins) - never an assistant turn, never another member's turn, even when that
    id is asked for; and an empty id list is no query at all."""
    w = world
    everything = ["a-said", "a-other", "a-reply", "a-kiosk", "b-said", "no-such-id"]
    got = await exact_words._transcript_rows(w.a, everything)
    assert sorted(r[0] for r in got) == ["a-kiosk", "a-other", "a-said"]
    assert {r[0]: r[1] for r in got}["a-kiosk"] == SAID and all(len(r) == 3 for r in got)
    assert sorted(r[0] for r in await exact_words._transcript_rows(w.b, everything)) == ["b-said"]
    assert await exact_words._transcript_rows(w.b, ["a-said", "a-kiosk"]) == []          # another member's ids: nothing
    assert await exact_words._transcript_rows(w.a, []) == []
    assert await exact_words._transcript_rows(w.a, ["a-said"]) == [("a-said", f"I said {SAID} today", None)]


async def test_a_refused_transcript_flag_is_parked_by_id_and_finished_by_the_replay_after_a_restart(world, kernel):  # noqa: F811
    """The whole path on real rows. The kernel refuses: ``forget_transcript`` raises and flags nothing; ``park_transcript`` parks the
    ids of A's turns holding the words (never the words, never B's turn that says the same). Restart (module state cleared, a new
    worker, the park read back from disk). The kernel recovers and any job runs the replay: A's turns are flagged off the record -
    their other metadata kept - the candidates read no longer returns them, B's identical turn and A's unrelated turn are untouched,
    and the park entry is gone."""
    w = world
    kernel.forget_mode = "refuse_all"
    with pytest.raises(os_shadow.ShadowForgetUnavailable) as err:
        await exact_words.forget_transcript(w.a, SAID)
    await exact_words.park_transcript(w.a, SAID, err.value)
    assert {m: sorted(e["messages"]) for m, e in _parked(kernel).items()} == {w.a: ["a-kiosk", "a-said"]}
    assert "lavender" not in (kernel.state / "pending-erases.json").read_text()          # ids only
    assert await w.meta("a-said") == {} and await w.meta("a-kiosk") == {"user_id": w.a, "panel": "kitchen"}      # nothing flagged yet

    turn_forget.reset()                                           # a restart: no in-process memo, no noted turns ...
    os_shadow._reset_for_tests()                                  # ... module state cleared, the old worker stopped ...
    assert os_shadow.revive() and os_shadow._erase_members is None     # ... a new worker that must read the park back from disk
    kernel.forget_mode = "ok"
    add()                                                         # any job at all: not a forget, not a request to finish
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert not (kernel.state / "pending-forgets.json").exists()

    assert (await w.meta("a-said")) == {"off_record": True}
    assert (await w.meta("a-kiosk")) == {"user_id": w.a, "panel": "kitchen", "off_record": True}
    assert await w.meta("a-other") == {} and await w.meta("b-said") == {} and await w.meta("a-reply") == {}
    assert await exact_words._transcript_candidates(w.a, NEEDLE) == [("a-other", "an unrelated synthetic remark about the weather", None)]
    assert [r[0] for r in await exact_words._transcript_candidates(w.b, NEEDLE)] == ["b-said"]
    assert await exact_words.finish_parked(w.a, [], ["a-said", "a-kiosk"]) == 0           # replayed twice: nothing left to flag


async def test_a_parked_id_that_belongs_to_another_member_is_left_alone(world, kernel):  # noqa: F811
    """The negative case: A's park entry names B's turn (and an id that does not exist). The finish flags A's own turn only; B's is
    not touched, and the entry is dropped."""
    w = world
    assert await turn_forget.finish_parked(w.a, {"messages": ["b-said", "no-such-id"]}) == {}      # nothing left (and nothing of B's touched)
    assert await w.meta("b-said") == {}
    os_shadow.park_erase(w.a, messages=["b-said", "a-said", "no-such-id"])
    add()                                                         # the kernel is healthy: the replay finishes at once
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert await w.meta("a-said") == {"off_record": True} and await w.meta("b-said") == {}
    assert [r[0] for r in await exact_words._transcript_candidates(w.b, NEEDLE)] == ["b-said"]
