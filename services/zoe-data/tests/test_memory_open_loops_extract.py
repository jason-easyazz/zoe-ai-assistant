"""Open-loop extraction (dreaming phase 1.5) + evidence forwarding by the writers.

``_extract_open_loops`` imported ``zoe_agent._llm_chat``, which never existed, so
every nightly run failed at the import and ``open_loops`` stayed empty. Its INSERT
also bound the model's ISO string / string weight to TIMESTAMP / INTEGER columns,
which asyncpg rejects. ``test_fenced_reply_inserts_typed_rows`` is the negative
control for both: red on the old code, and red on the old code with only the
import fixed (the typed-bind assertion).
"""
import json
import sys
import types

import httpx
import pytest

import db_compat
import memory_digest

pytestmark = pytest.mark.ci_safe  # fakes only — no DB, no model, no live service

# asyncpg's parameter types for the INSERT, captured by preparing it against the
# live open_loops schema: (text, text, text, int4, int4). A str in an int slot
# is a DataError there.
_INSERT_PARAM_TYPES = (str, str, str, int, int)


class _Rec(tuple):
    """asyncpg.Record stand-in: index and column-name access."""

    def __getitem__(self, key):
        return tuple.__getitem__(self, 0 if isinstance(key, str) else key)


class _Cur:
    """Both forms the compat layer returns: awaitable and async context."""

    def __init__(self, rows=(), rowcount=0):
        self._rows = [_Rec(r if isinstance(r, tuple) else (r,)) for r in rows]
        self.rowcount = rowcount

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchall(self):
        return self._rows


class _LoopsDb:
    def __init__(self, messages, unresolved=(), expired=0):
        self.messages, self.unresolved, self.expired = messages, unresolved, expired
        self.calls, self.inserts = [], []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        head = " ".join(sql.split())
        if head.startswith("SELECT") and "FROM chat_messages" in head:
            return _Cur(self.messages)  # newest first, like the SQL
        if head.startswith("SELECT loop_text"):
            return _Cur(self.unresolved)
        if head.startswith("SELECT id, loop_text"):
            return _Cur(list(enumerate(self.unresolved, 1)))
        if head.startswith("UPDATE open_loops"):
            return _Cur(rowcount=self.expired)
        assert head.startswith("INSERT INTO open_loops"), head[:80]
        assert tuple(type(p) for p in params) == _INSERT_PARAM_TYPES, params
        self.inserts.append(params)
        return _Cur(rowcount=1)


def _install(monkeypatch, db, reply=None, exc=None):
    """Stub the DB and the brain's HTTP call; returns the captured POSTs."""
    posts = []

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *a, **k):
            posts.append({"timeout": k.get("timeout")})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            posts[-1].update(url=url, payload=json)
            if exc is not None:
                raise exc
            return types.SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"choices": [{"message": {"content": reply}}]})

    monkeypatch.setattr(db_compat, "get_compat_db", _Ctx)
    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    return posts


def _loop(text, weight=3, days=2, **extra):
    return {"loop_text": text, "follow_up_hint": "How did it go?", "emotional_weight": weight,
            "follow_up_in_days": days, **extra}


async def test_fenced_reply_inserts_typed_rows(monkeypatch, caplog):
    db = _LoopsDb(["The plumber still has not sent the quote", "I start the new course next week"])
    reply = "```json\n" + json.dumps([
        _loop("User is waiting on a quote from the plumber", weight="4"),
        # The model has no clock: an absolute date it invents is ignored.
        _loop("User starts a new course next week", days=40, follow_up_after="2023-01-01T09:00:00"),
    ]) + "\n```"
    posts = _install(monkeypatch, db, reply=reply)
    caplog.set_level("INFO", logger="memory_digest.open_loops")

    result = await memory_digest._extract_open_loops("user-1")

    assert (result["extracted"], result["inserted"], result["status"]) == (2, 2, "ok")
    assert db.inserts[0] == ("user-1", "User is waiting on a quote from the plumber", "How did it go?", 4, 2)
    assert db.inserts[1][3:] == (3, memory_digest._OPEN_LOOPS_MAX_FOLLOW_UP_DAYS)
    assert "make_interval(days => ?::int)" in db.calls[-1][0]
    assert posts[0]["url"].endswith("/v1/chat/completions") and posts[0]["timeout"] == 45.0
    prompt = posts[0]["payload"]["messages"][1]["content"]
    assert prompt.index("new course") < prompt.index("plumber"), "prompt reads oldest first"
    assert ("OPEN_LOOPS user=user-1 extracted=2 inserted=2 skipped_dup=0 expired=0 "
            "discarded_meta=0 skipped_turns=0 status=ok") in caplog.text


async def test_duplicates_are_skipped_output_capped_stale_loops_aged_out(monkeypatch):
    db = _LoopsDb(["lots going on"], expired=2,
                  unresolved=["User is waiting to hear back about the job interview"])
    reply = json.dumps([
        _loop("Waiting on the job interview results"),            # paraphrase of a stored loop
        _loop("User wants to repaint the spare bedroom"),
        _loop("User plans to repaint the spare bedroom soon"),    # duplicate within this run
        _loop("User must renew an expired passport"), _loop("User is renovating the bathroom"),
        _loop("User is chasing an unpaid invoice"), _loop("User needs new tyres for the road trip"),
    ])
    _install(monkeypatch, db, reply=reply)

    result = await memory_digest._extract_open_loops("user-1")

    assert (result["extracted"], result["skipped_dup"]) == (memory_digest._OPEN_LOOPS_MAX_PER_RUN, 2)
    assert [p[1] for p in db.inserts] == ["User wants to repaint the spare bedroom",
                                          "User must renew an expired passport",
                                          "User is renovating the bathroom"]
    assert result["expired"] == 2
    update_sql, update_params = next(c for c in db.calls if c[0].lstrip().startswith("UPDATE"))
    assert update_params == ("user-1",) and "resolved IS NOT TRUE" in update_sql
    assert f"INTERVAL '{memory_digest._OPEN_LOOPS_TTL_DAYS} days'" in update_sql


async def test_stale_loops_age_out_even_without_new_turns(monkeypatch):
    db = _LoopsDb([], expired=1)
    _install(monkeypatch, db)

    result = await memory_digest._extract_open_loops("user-1")

    assert (result["status"], result["expired"]) == ("no_messages", 1)


async def test_secrets_in_loop_text_are_redacted(monkeypatch):
    db = _LoopsDb(["need to change the router"])
    _install(monkeypatch, db, reply=json.dumps([_loop("User must call the plumber; wifi password is hunter2")]))

    await memory_digest._extract_open_loops("user-1")

    assert "hunter2" not in db.inserts[0][1] and "[REDACTED]" in db.inserts[0][1]


async def test_meta_turns_and_anchorless_loops_are_discarded(monkeypatch):
    # Owner calibration (2026-09-30), paraphrased: a "let's talk" opener and a
    # correction produced vague loops with no entity; real ones name something.
    db = _LoopsDb(["let's talk", "you got that wrong, fix that",
                   "A relative is in hospital and I'm flying over to visit"],
                  unresolved=["The user expressed a desire to talk continuously",
                              "User is tired because work is busy"])
    posts = _install(monkeypatch, db, reply=json.dumps([
        _loop("The user mentioned a problem with something they said that needs to be fixed", weight=3),
        _loop("A relative is in hospital; the user is travelling to visit", weight=5)]))

    result = await memory_digest._extract_open_loops("user-1")

    prompt = posts[0]["payload"]["messages"][1]["content"]
    assert "let's talk" not in prompt and "fix that" not in prompt and "hospital" in prompt
    assert (result["skipped_turns"], result["discarded_meta"], result["inserted"]) == (2, 2, 1)
    resolved = [p for sql, p in db.calls if "WHERE id = ?" in sql]
    assert resolved == [(1,)]  # only the anchor-less stored loop, resolved with resolved_at
    assert db.inserts[0][3] == 5


@pytest.mark.parametrize("reply, exc, status", [
    (None, httpx.ReadTimeout("slot busy"), "llm_error"),
    ("I could not find any open loops, sorry.", None, "parse_error"),
])
async def test_brain_failures_are_contained(monkeypatch, reply, exc, status):
    db = _LoopsDb(["waiting on the plumber quote"])
    _install(monkeypatch, db, reply=reply, exc=exc)

    result = await memory_digest._extract_open_loops("user-1")

    assert (result["status"], result["inserted"], db.inserts) == (status, 0, [])


@pytest.mark.parametrize("raw, expected", [
    ('```json\n[{"loop_text": "a"}]\n```', [{"loop_text": "a"}]),
    ('Here you go:\n[{"loop_text": "a"}]\nNote: see [1] above.', [{"loop_text": "a"}]),
    ('{"loops": [{"loop_text": "a"}]}', [{"loop_text": "a"}]),
    ("[]", []),
    ("no loops today", None),
    ('{"a": [1], "b": [2]}', None),
])
def test_parse_json_array_tolerates_model_wrapping(raw, expected):
    assert memory_digest._parse_json_array(raw) == expected


# ── the writers hand the WHOLE utterance to the store (it scrubs, then cuts) ──

class _Svc:
    def __init__(self, existing=()):
        self.existing = [types.SimpleNamespace(id=i, text=t, metadata={}) for i, t in existing]
        self.ingested, self.reviewed = [], []

    async def search(self, *a, **k):
        return self.existing

    async def get(self, mem_id):
        return next((r for r in self.existing if r.id == mem_id), None)

    async def ingest(self, text, **kw):
        self.ingested.append(kw)
        return types.SimpleNamespace(id="new", text=text, metadata={})

    async def review(self, mem_id, **kw):
        self.reviewed.append(kw)
        return types.SimpleNamespace(id="edited", text=kw.get("edits"), metadata={})


@pytest.mark.parametrize("existing, fact, message", [
    ((), "User's father's name is Neil.", "my  dad's name\nis Neil by the way"),
    ((("old", "User's birthday is March 15."),), "User's birthday is March 25.",
     "actually my birthday is March 25"),
])
async def test_turn_digest_forwards_the_utterance(monkeypatch, existing, fact, message):
    import memory_service

    svc = _Svc(existing)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    _install(monkeypatch, _LoopsDb([]), reply=json.dumps([{"fact": fact, "type": "fact"}]))

    async def _no_facts(*a, **k):
        return ""

    monkeypatch.setitem(sys.modules, "zoe_agent", types.SimpleNamespace(
        _mempalace_load_user_facts=_no_facts, _invalidate_user_facts_cache=lambda *a, **k: None))

    await memory_digest.run_turn_digest("user-1", message, session_id="s1")

    written = svc.reviewed if existing else svc.ingested
    assert len(written) == 1 and written[0]["source_excerpt"] == " ".join(message.split())


async def test_regex_extractor_forwards_the_whole_utterance(monkeypatch):
    import memory_extractor

    svc = _Svc()
    monkeypatch.setitem(sys.modules, "memory_service",
                        types.SimpleNamespace(get_memory_service=lambda: svc))
    monkeypatch.setattr(memory_extractor, "_prev_user_turns",
                        type(memory_extractor._prev_user_turns)())
    message = "My favourite colour is blue. " + "And more context here. " * 12  # > 220 chars

    await memory_extractor.extract_and_ingest(message, user_id="user-1", session_id="s1",
                                              source="chat_regex")

    assert svc.ingested, "expected a candidate from the favourite-colour template"
    assert all(kw["source_excerpt"] == " ".join(message.split()) for kw in svc.ingested)
