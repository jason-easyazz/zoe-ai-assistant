"""Every forget resolves to SAVED-TURN ids and erases by id (PR #2574, review rounds 3-7).

Two look-alike turns that differ only by a contact detail and a trailing word store the SAME redacted words wherever the write
boundary scrubs (the exact-words index, a fact row's excerpt), and one redacted excerpt sits INSIDE the other. So no forget may
compare redacted text: each derived row carries the saved turn (``chat_messages.id``) it came from, stamped at write time, and
every forget path resolves to those ids first. A third turn about ANOTHER topic shares the first turn's redacted address.

The world is planted through the REAL writers on both the old and the new code (``routers.chat._save_chat_message`` over a fake
``chat_messages``; the post-turn capture inside ``turn_forget.capture_scope``), and every seam the fixture patches exists in both,
so on the old code the sweep fails BY ASSERTION, not at setup. A rotated forget-ledger secret changes nothing: no forget reads the
keyed tag. Synthetic placeholder words and invented addresses only.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time

import pytest

import exact_words as xw
import forget_topic
import intent_router
import memory_provenance as mp
import memory_service
import night_mind
import night_store
import provenance_answers as pa
import turn_forget
from forgotten_support import ledger_env  # noqa: F401 - fixture (the secret the audit tags are keyed with)
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0000id01"
SAID = {"a": "My plumber works from 1 Example Street", "b": "My plumber works from 2 Example Street on weekdays",
        "c": "My dentist works from 1 Example Street"}          # c: another topic, the SAME redacted address as a
FACTS = {k: v.replace("My ", "User's ", 1) for k, v in SAID.items()}
COPIES = {"a": "User has a plumber at 1 Example Street", "b": "User has a plumber at 2 Example Street",
          "c": "User has a dentist at 1 Example Street"}
KEPT = {"a": "My plumber works from", "b": "My plumber works from", "c": "My dentist works from"}   # what the scrub leaves


def run(coro):
    return asyncio.run(coro)


class _Chat:
    """``chat_messages`` behind the real save path and exact_words' transcript seams (every seam exists in both versions)."""

    def __init__(self):
        self.rows: dict = {}                                     # id -> [content, metadata]
        self.created: dict = {}                                  # id -> epoch it was saved (the real column's default)
        self.clock = None                                        # a test may pin "now" to plant rows hours apart

    def ctx(self):
        chat = self

        class _Db:
            async def execute(self, sql, params=()):
                if sql.lstrip().upper().startswith("INSERT INTO CHAT_MESSAGES"):
                    chat.rows.setdefault(params[0], [params[3], params[4]])
                    chat.created.setdefault(params[0], chat.clock if chat.clock is not None else time.time())
                return self

            rowcount = 0

            async def execute_fetchall(self, *a, **k):
                return []

            async def fetchall(self):                            # any other table a forget path reads: empty
                return []

            async def fetchone(self):
                return None

            async def commit(self):
                return None

        @contextlib.asynccontextmanager
        async def _ctx():
            yield _Db()
        return _ctx()

    async def by_id(self, user_id, mids):
        # the real seam also returns every committed COPY of each turn: same session (this fake has one), role and words, saved
        # within exact_words.COPY_WINDOW_S of it - the same words hours apart are another turn
        want = [m for m in mids if m in self.rows]

        def copy_of(m, c):
            return any(c == self.rows[w][0] and abs(self.created.get(m, 0) - self.created.get(w, 0)) <= xw.COPY_WINDOW_S
                       for w in want)
        return [(m, meta) for m, (c, meta) in self.rows.items() if m in mids or copy_of(m, c)]

    async def candidates(self, user_id, needle):
        return [(m, c, meta) for m, (c, meta) in self.rows.items() if needle in c.lower() and not self.off(m)]

    async def setmeta(self, rid, meta):
        self.rows[rid][1] = meta

    def off(self, mid):
        return mp.OFF_RECORD_JSON_MARK in str(self.rows[mid][1] or "")

    def mid(self, key):
        return next(m for m, (c, _meta) in self.rows.items() if c == SAID[key])


@pytest.fixture
def world(monkeypatch, ledger_env):
    import db_pool

    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", mp.ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.setattr(memory_service, "_RECENT_AUTO_TTL_S", 0.0)   # the paraphrase dedup is not under test: every row lands
    svc = MemoryService(data_dir="/nonexistent/zoe-test-forget-by-turn-id")
    col = _Col()
    svc._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False
    svc._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(pa, "_svc_default", lambda: svc)
    chat = _Chat()
    chat.patch = monkeypatch
    chat.db_pool = db_pool
    monkeypatch.setattr(xw, "_transcript_rows_by_id", chat.by_id, raising=False)
    monkeypatch.setattr(xw, "_transcript_candidates", chat.candidates)
    monkeypatch.setattr(xw, "_set_transcript_metadata", chat.setmeta)
    monkeypatch.setattr(xw, "SqlBackend", type(xw.get_backend()))          # the lab index stands in for the SQL one
    turn_forget.reset()
    mp.reset()
    yield svc, chat
    turn_forget.reset()
    mp.reset()


def _plant(svc, chat, target: str) -> dict:
    """The three turns saved and captured the way the live chat lane does it, the TARGET last ("forget that" binds to it)."""
    from routers.chat import _save_chat_message

    rows: dict = {}
    for key in sorted(SAID, key=lambda k: k == target):
        said = SAID[key]
        # the fake chat_messages answers ONLY the save: every other store a forget reaches (the value registry, the notes) sees
        # exactly the database every other forget test sees, so a fake answering empty can never read as a store failure
        with chat.patch.context() as scoped:
            scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
            assert run(_save_chat_message("s-1", "user", said, user_id=UID)) is True

        async def capture():
            with turn_forget.capture_scope(UID, said):                          # the post-turn capture of THAT turn
                assert await xw.index_turn(UID, said) is True
                fact = await svc.ingest(FACTS[key], user_id=UID, source="turn_digest", status="approved", source_excerpt=said)
                copy = await svc.ingest(COPIES[key], user_id=UID, source="chat_regex", status="approved",
                                        source_excerpt=said[:22])   # a writer's cut, before the address
                return fact, copy
        rows[key] = run(capture())
        assert rows[key][0] is not None and rows[key][1] is not None, "both derived rows landed"
        run(night_store.get_backend().put_observation(night_store.obs_row(
            id=f"o-{key}", user_id=UID, turn_id=chat.mid(key), quote=KEPT[key], day="2026-10-08", said_at=time.time())))
    return rows


def _xw_rows():
    """{turn key: index id} of what the exact-words index still holds, keyed by which turn's words it stores."""
    out = {}
    for (_u, tid), r in xw.get_backend().rows.items():
        out.setdefault("c" if "dentist" in r["text"] else ("b" if "weekdays" in r["text"] else "a"), tid)
    return out


def _approved(svc):
    return {r.text for r in run(svc.list_by_status(user_id=UID, status="approved"))}


def _night():
    return sorted(o["id"][2:] for o in run(night_store.get_backend().observations(UID)))


def _left(svc, chat) -> dict:
    """Per turn: does anything of it remain in each store?"""
    xr, ap, nt = _xw_rows(), _approved(svc), _night()
    return {k: {"words": k in xr, "fact": FACTS[k] in ap, "copy": COPIES[k] in ap,
                "transcript": not chat.off(chat.mid(k)), "night": k in nt} for k in SAID}


def _diagnosis(path, reply, before, after, caplog) -> str:
    """The per-store before/after table, the path's reply and every eraser warning it logged: a failure names its cause."""
    rows = [f"{k} {s:<10} {int(before[k][s])} -> {int(after[k][s])}" for k in SAID for s in ("words", "fact", "copy", "transcript", "night")]
    logs = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO and any(
        m in r.getMessage() for m in ("failed", "unavailable", "unbound", "skipped", "forget_"))]
    return f"\npath={path} reply={reply!r}\n" + "\n".join(rows) + "\nlogs:\n" + "\n".join(logs)


# ── the seven forget paths: each forgets the TARGET turn (the topic forget: the target's TOPIC) ────────────────────────

def _forget_last(svc, chat, target, rows):
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))


def _provenance_turn(svc, chat, target, rows):
    tid = _xw_rows()[target]
    stored = xw.get_backend().rows[(UID, tid)]["text"]
    assert "[REDACTED]" in stored                                              # the quote IS the redacted stored form
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=tid)
    return run(pa._forget_turn(UID, exp, svc=svc))


def _provenance_fact(svc, chat, target, rows):
    ref = run(svc.get(rows[target][0].id))
    quote = str(ref.metadata.get("source_excerpt") or "")
    assert "[REDACTED]" in quote
    return run(pa.forget_it(UID, mp.Explained(seq=1, row_id=ref.id, ts=time.time(), text=ref.text, quote=quote), svc=svc))


def _topic(svc, chat, target, rows):
    res = run(forget_topic.forget(svc, UID, "my plumber"))
    return "Done" if not (res.store_down or res.failed or res.too_many) else "not done"


# The three ERASERS, each handed what the forget hands it: on the new code the saved-turn id; on the old code (no id API) what the
# old forget passed - the turn's words as said, and the redacted stored quote.
def _night_mind(svc, chat, target, rows):
    quote = xw.get_backend().rows[(UID, _xw_rows()[target])]["text"]
    run(night_mind.erase_words(UID, chat.mid(target), SAID[target], quote))
    return "Done"


def _exact_words(svc, chat, target, rows):
    if hasattr(xw, "erase_turns"):
        run(xw.erase_turns(UID, [chat.mid(target)]))
    else:
        run(xw.erase_text(UID, SAID[target]))
    return "Done"


def _transcript(svc, chat, target, rows):
    if hasattr(xw, "flag_transcript_turns"):
        run(xw.flag_transcript_turns(UID, [chat.mid(target)]))
    else:
        run(xw.forget_transcript(UID, xw.get_backend().rows[(UID, _xw_rows()[target])]["text"]))
    return "Done"


PATHS = {"forget_last": _forget_last, "provenance_turn": _provenance_turn, "provenance_fact": _provenance_fact,
         "topic_index_hits": _topic, "night_mind": _night_mind, "exact_words": _exact_words, "transcript_flag": _transcript}
#: the stores each path owns (an ERASER owns only its own store)
OWNS = {"forget_last": {"words", "fact", "copy", "transcript", "night"},
        "provenance_turn": {"words", "fact", "copy", "transcript", "night"},
        "provenance_fact": {"words", "fact", "copy", "transcript", "night"},
        "topic_index_hits": {"words", "fact", "copy", "transcript"},
        "night_mind": {"night"}, "exact_words": {"words"}, "transcript_flag": {"transcript"}}


@pytest.mark.parametrize("rotated", [False, True])
@pytest.mark.parametrize("target", ["a", "b"])
@pytest.mark.parametrize("path", sorted(PATHS))
def test_every_forget_path_takes_exactly_its_turn(world, monkeypatch, caplog, path, target, rotated):
    """Forgetting one of two look-alike turns takes exactly that turn's data in every store the path owns, and nothing of the
    other turn or of the third. The TOPIC forget takes every turn about its topic by design (both plumber turns), never the
    dentist turn that shares the redacted address. Also after the ledger secret ROTATES between the write and the forget."""
    import memory_forgotten

    svc, chat = world
    rows = _plant(svc, chat, target)
    if rotated:
        monkeypatch.setenv(memory_forgotten.SALT_ENV, "another-unit-test-ledger-secret-9876543210")
    before = _left(svc, chat)
    caplog.set_level(logging.INFO)
    reply = PATHS[path](svc, chat, target, rows)
    left = _left(svc, chat)
    why = _diagnosis(path, reply, before, left, caplog)
    assert reply.startswith("Done"), why
    gone = {"a", "b"} if path == "topic_index_hits" else {target}
    for key in SAID:
        for store in sorted(OWNS[path]):
            assert left[key][store] is (key not in gone), (key, store, why)


def test_every_derived_row_carries_its_saved_turn(world):
    svc, chat = world
    rows = _plant(svc, chat, "b")
    for key, (fact, copy) in rows.items():
        for ref in (fact, copy):
            assert run(svc.get(ref.id)).metadata.get("source_turn") == chat.mid(key)
        assert xw.mid_of(_xw_rows()[key]) == chat.mid(key)


def test_a_legacy_redacted_entry_is_forgotten_by_its_words_not_refused(world):
    """An entry written BEFORE the ids (legacy): refusing it would leave it readable forever, so its redacted words match it - and a
    legacy look-alike that redacts alike goes too (over-forgetting is accepted for legacy rows only). This replaced the round-6
    rule "no id = the forget is refused", which now holds only for NEW writes, and those always carry an id (index_turn refuses)."""
    svc, chat = world
    stored = "My plumber works from [REDACTED]"
    run(xw.get_backend().add(UID, "xw-old", time.time(), stored, " plumber work ", "chat"))
    run(xw.get_backend().add(UID, "xw-old2", time.time(), stored + " on weekdays", " plumber work weekday ", "chat"))
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id="xw-old")
    assert run(pa._forget_turn(UID, exp, svc=svc)).startswith("Done")
    assert not {"xw-old", "xw-old2"} & {k[1] for k in xw.get_backend().rows}


# ── THE PARTITION: a row WITH an id is erased by id only; only a LEGACY (id-less) row ever meets a text matcher ────────────

LEGACY_SAID = "My locksmith works from the corner shop on Example Lane"


def _plant_partition(svc):
    """The SAME words in every store twice: once legacy (no id, written before the ids) and once bound to ANOTHER saved turn."""
    now = time.time()
    xw_be, nt_be = xw.get_backend(), night_store.get_backend()
    run(xw_be.add(UID, "xw-legacy", now, LEGACY_SAID, xw.tokens_column(LEGACY_SAID), "chat"))
    run(xw_be.add(UID, "xw-bound@m-other", now, LEGACY_SAID, xw.tokens_column(LEGACY_SAID), "chat"))
    run(nt_be.put_observation(night_store.obs_row(id="o-legacy", user_id=UID, turn_id="", quote=LEGACY_SAID, day="2026-10-08",
                                                  said_at=now)))
    run(nt_be.put_observation(night_store.obs_row(id="o-bound", user_id=UID, turn_id="m-other", quote=LEGACY_SAID,
                                                  day="2026-10-08", said_at=now)))
    old = run(svc.ingest("User's locksmith is at the corner shop", user_id=UID, source="turn_digest", status="approved",
                         source_excerpt=LEGACY_SAID))
    for k in ("source_turn", "source_kind", "source_excerpt_said"):   # no provenance of any kind                      # an OLD row: written before the ids
        svc._collection().rows[old.id][1].pop(k, None)
    bound = run(svc.ingest("User's locksmith is in the corner shop", user_id=UID, source="turn_digest", status="approved",
                           source_excerpt=LEGACY_SAID, metadata={"source_turn": "m-other"}))
    return old, bound


def _forget_legacy_target(svc):
    """A forget whose target is LEGACY (a quoted entry with no saved-turn id): the only way into the text matchers."""
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=LEGACY_SAID, quote=LEGACY_SAID, turn_id="xw-legacy")
    return run(pa._forget_turn(UID, exp, svc=svc))


def _state(svc):
    ap = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    return {k[1] for k in xw.get_backend().rows}, set(_night()), ap


@pytest.mark.parametrize("broken", [False, True])
def test_a_legacy_target_takes_the_legacy_rows_and_never_a_bound_row(world, monkeypatch, broken):
    """A legacy target's words take every LEGACY row holding them (exact words, night observation, fact row) and NO row bound to
    another saved turn, however identical its words. Control (``broken``): with the partitions removed (every row legacy) the very
    same forget takes the bound rows too - so the survivors are the partition's doing, not the matcher's."""
    svc, chat = world
    old, bound = _plant_partition(svc)
    if broken:
        monkeypatch.setattr(xw, "partition", lambda rows: ([], list(rows or ())))
        monkeypatch.setattr(night_mind, "partition_observations", lambda obs: ([], list(obs or ())))
        monkeypatch.setattr(turn_forget, "partition", lambda rows: ([], list(rows or ())))
    assert _forget_legacy_target(svc).startswith("Done")
    words, night, approved = _state(svc)
    assert "xw-legacy" not in words and "legacy" not in night and old.id not in approved     # legacy rows: forgotten
    survived = {"xw-bound@m-other" in words, "bound" in night, bound.id in approved}
    assert survived == ({False} if broken else {True})


@pytest.mark.parametrize("broken", [False, True])
def test_a_bound_row_is_never_matched_by_text_when_the_forget_has_an_id(world, monkeypatch, broken):
    """A forget WITH a saved-turn id never matches a BOUND row by its words: rows of another turn holding the target's very words
    survive. The decision is per row, so a LEGACY entry with those words (no saved turn of its own: it may be this turn's from
    before ids were stamped) is taken; that errs toward forgetting and reaches legacy rows only. Control (``broken``): the old text fallback (night_mind before this
    change: the words over every observation) takes the other turn's observation."""
    svc, chat = world
    _old, bound = _plant_partition(svc)
    if broken:
        monkeypatch.setattr(night_mind, "partition_observations", lambda obs: ([], list(obs or ())))
        run(night_mind.erase_words(UID, "", LEGACY_SAID))            # what a text fallback beside the id did
    run(turn_forget.erase_turn_words(UID, ("m-target",), (LEGACY_SAID,)))
    words, night, approved = _state(svc)
    assert ("bound" in night) is (not broken)
    assert "xw-bound@m-other" in words and "xw-legacy" not in words and bound.id in approved


def test_row_is_from_turn_consults_no_words_for_a_bound_or_non_turn_row():
    said = "My locksmith works from the corner shop"
    assert turn_forget.row_is_from_turn({"source_excerpt": said}, said)                               # legacy: by its words
    assert not turn_forget.row_is_from_turn({"source_excerpt": said, "source_turn": "m-1"}, said, mids=("m-2",))
    assert turn_forget.row_is_from_turn({"source_excerpt": said, "source_turn": "m-1"}, said, mids=("m-1",))
    assert not turn_forget.row_is_from_turn({"source_excerpt": said, "source_kind": "omi_recording"}, said)


def test_every_new_fact_row_names_its_source_or_is_counted_unbound(world, monkeypatch, caplog):
    """(a) for the fact store: a row from a saved turn carries its id; a row with no turn evidence, or from a non-turn lane, an
    explicit ``source_kind``; a row WITH turn evidence and neither is counted (shadow, default) and REFUSED under enforce - never
    silently stored id-less. The log line carries a code and counts only."""
    svc, chat = world
    caplog.set_level(logging.INFO)
    plain = run(svc.ingest("User likes tea", user_id=UID, source="chat_regex", status="approved"))
    assert run(svc.get(plain.id)).metadata.get("source_kind") == "no_turn"
    # a lane that never writes from a saved chat turn names its kind even with an excerpt (a pendant recording's quote)
    assert memory_service._write_provenance(UID, "omi_review", {}, "the locksmith is on Example Lane", None) == {
        "source_kind": "omi_recording"}
    assert memory_service._write_provenance(UID, "turn_digest", {"source_kind": "made-up"}, "words said", None) == {
        memory_service._UNBOUND_KEY: True}                                                   # the kinds are a closed set
    before = memory_service.UNBOUND["no_source_turn"]
    shadow = run(svc.ingest("User's cousin plays the cello", user_id=UID, source="turn_digest", status="approved",
                            source_excerpt="my cousin plays the cello every weekend"))
    assert shadow is not None and memory_service.UNBOUND["no_source_turn"] == before + 1
    assert not {"source_turn", "source_kind"} & set(run(svc.get(shadow.id)).metadata)       # legacy-shaped: a forget finds it by words
    monkeypatch.setenv(memory_service.TURN_ID_FLAG, "enforce")
    refused = run(svc.ingest("User's cousin plays the viola", user_id=UID, source="turn_digest", status="approved",
                             source_excerpt="my cousin plays the viola every weekend"))
    assert refused is None and memory_service.UNBOUND["no_source_turn"] == before + 2
    lines = [r.getMessage() for r in caplog.records if "MEMORY_WRITE_UNBOUND" in r.getMessage()]
    assert lines and all("code=no_source_turn" in m and "cousin" not in m and "viola" not in m for m in lines)
    named = run(svc.ingest("User's cousin plays the harp", user_id=UID, source="turn_digest", status="approved",
                           source_excerpt="my cousin plays the harp", metadata={"source_turn": "m-harp"}))
    assert named is not None and run(svc.get(named.id)).metadata.get("source_turn") == "m-harp"       # control: the id writes it


def test_a_failing_words_eraser_fails_forget_that_and_the_retry_finishes(world, monkeypatch, caplog):
    """An eraser of STORED words failing fails the forget (not confirmed, the turn kept for the retry); an in-process cache clear
    failing does not (test_value_correction_forget.py::test_a_failing_clear_never_breaks_forget_that)."""
    svc, chat = world
    _plant(svc, chat, "b")
    real = xw.flag_transcript_turns

    async def down(*_a, **_k):
        raise RuntimeError("store down")
    monkeypatch.setattr(xw, "flag_transcript_turns", down)
    caplog.set_level(logging.INFO)
    first = _forget_last(svc, chat, "b", None)
    assert not first.startswith("Done"), first
    assert "b" in _xw_rows(), "the words entry stays until the transcript is flagged (nothing left unfindable)"
    monkeypatch.setattr(xw, "flag_transcript_turns", real)
    before = _left(svc, chat)
    second = _forget_last(svc, chat, "b", None)
    assert second.startswith("Done"), _diagnosis("forget_last retry", second, before, _left(svc, chat), caplog)
    assert chat.off(chat.mid("b")) and "b" not in _xw_rows() and not chat.off(chat.mid("a"))


# ── PER ROW, never per target (review P1, PR #2574): a turn backfilled AFTER the upgrade has an id, its older fact does not ─────

BACKFILLED = "My piano teacher lives next to the old bakery"


def _plant_backfilled(svc, chat):
    """A pre-upgrade fact (no source_turn) whose transcript turn is indexed afterwards WITH its saved-turn id (what
    ``backfill_recent`` does: ``index_turn(..., message_id=<chat_messages.id>)``), beside a bound row of ANOTHER turn with the same
    words and a legacy row whose words do not match."""
    from routers.chat import _save_chat_message

    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", BACKFILLED, user_id=UID)) is True
    mid = next(m for m, (c, _meta) in chat.rows.items() if c == BACKFILLED)

    def legacy(text, excerpt):
        ref = run(svc.ingest(text, user_id=UID, source="turn_digest", status="approved", source_excerpt=excerpt))
        for k in ("source_turn", "source_kind", "source_excerpt_said"):   # no provenance of any kind
            svc._collection().rows[ref.id][1].pop(k, None)              # written before the ids
        return ref
    old = legacy("User's piano teacher lives next to the old bakery", BACKFILLED)
    unrelated = legacy("User's dentist lives next to the old bakery", "My dentist lives next to the old bakery")
    mine = run(svc.ingest("User has a piano teacher near the old bakery", user_id=UID, source="turn_digest", status="approved",
                          source_excerpt=BACKFILLED, metadata={"source_turn": mid}))
    other = run(svc.ingest("User's piano teacher lives by the old bakery", user_id=UID, source="turn_digest", status="approved",
                           source_excerpt=BACKFILLED, metadata={"source_turn": "m-another-turn"}))
    assert run(xw.index_turn(UID, BACKFILLED, message_id=mid)) is True           # the backfill: the entry now carries the id
    tid = next(k[1] for k in xw.get_backend().rows)
    assert xw.mid_of(tid) == mid
    return {"old": old, "unrelated": unrelated, "mine": mine, "other": other, "tid": tid}


def _forget_backfilled(path, svc, planted):
    if path == "forget_it_after_why":
        stored = xw.get_backend().rows[(UID, planted["tid"])]["text"]
        exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=planted["tid"])
        return run(pa._forget_turn(UID, exp, svc=svc))
    if path == "forget_last":
        return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))
    import memory_space

    return "Done" if run(memory_space.forget_row(UID, planted["mine"].id, svc=svc)) == "forgotten" else "not done"


@pytest.mark.parametrize("path", ["forget_it_after_why", "forget_last", "memory_space"])
def test_a_legacy_fact_of_a_backfilled_turn_is_forgotten_with_it(world, path):
    """The target turn HAS an id (its entry was backfilled), yet the fact written before the upgrade has none: bound versus legacy
    is decided per ROW, so that legacy fact is matched by its words and goes - the answer is Done because it is gone. The id rule
    still holds for every bound row (another turn's row with the very same words stays), and a legacy row whose words do not match
    stays. Fails on e27797bfb for forget-it and the memory-space erase (they decided legacy-ness per target)."""
    svc, chat = world
    planted = _plant_backfilled(svc, chat)
    reply = _forget_backfilled(path, svc, planted)
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert reply.startswith("Done"), reply
    assert planted["old"].id not in approved and planted["mine"].id not in approved          # the turn's rows, legacy and bound
    assert planted["other"].id in approved                                                    # another turn's bound row: by id only
    assert planted["unrelated"].id in approved                                                # a legacy row that does not match


# ── THREE ROW KINDS x EVERY FORGET PATH (turn_forget.classify): @<id> by id only; @src:<kind> only itself; legacy by words ──────

KW = "My kayak paddles live in the blue shed behind the garage"
OTHER_MID = "m-another-turn-kw"


def _plant_kind(svc, chat, kind):
    """The target turn's transcript (saved through the real path) and ONE target per store of the given kind, beside bystanders
    holding the very same words: an entry and a fact bound to ANOTHER saved turn (always survive), and - unless the target is
    itself legacy - a legacy entry, fact and observation (they go only by the per-row words fallback, never for a non-turn
    target)."""
    from routers.chat import _save_chat_message

    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", KW, user_id=UID)) is True
    mid = next(m for m, (c, _meta) in chat.rows.items() if c == KW)
    now, be, nt = time.time(), xw.get_backend(), night_store.get_backend()

    def entry(tid):
        run(be.add(UID, tid, now, KW, xw.tokens_column(KW), "chat"))
        return tid

    def fact(text, prov):
        ref = run(svc.ingest(text, user_id=UID, source="turn_digest", status="approved", source_excerpt=KW))
        meta = svc._collection().rows[ref.id][1]
        for k in ("source_turn", "source_kind", "source_excerpt_said"):   # no provenance of any kind
            meta.pop(k, None)
        meta.update(prov)
        return ref.id

    def obs(oid, turn_id):
        run(nt.put_observation(night_store.obs_row(id=oid, user_id=UID, turn_id=turn_id, quote=KW, day="2026-10-08", said_at=now)))
        return oid[2:]

    prov = {"turn": {"source_turn": mid}, "kind": {"source_kind": "omi_recording"}, "legacy": {}}[kind]
    w = {"mid": mid,
         "entry": entry({"turn": f"xw-target@{mid}", "kind": "xw-target@src:lab", "legacy": "xw-target"}[kind]),
         "fact": fact("User keeps kayak paddles in the blue shed behind the garage", prov),
         "other_entry": entry(f"xw-other@{OTHER_MID}"),
         "other_fact": fact("User stores kayak paddles in a blue shed behind the garage", {"source_turn": OTHER_MID})}
    if kind != "kind":
        w["obs"] = obs("o-target", mid if kind == "turn" else "")
    if kind != "legacy":
        w["old_entry"] = entry("xw-old")
        w["old_fact"] = fact("User's kayak paddles live in the blue shed behind the garage", {})
        w["old_obs"] = obs("o-old", "")
    return w


def _run_path(path, svc, w):
    if path == "forget_it_after_why":                                  # the quoted exact-words entry is the target
        stored = xw.get_backend().rows[(UID, w["entry"])]["text"]
        return run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored,
                                                     turn_id=w["entry"]), svc=svc))
    if path == "forget_it_fact":                                       # the explained FACT row is the target
        ref = run(svc.get(w["fact"]))
        return run(pa.forget_it(UID, mp.Explained(seq=1, row_id=ref.id, ts=time.time(), text=ref.text,
                                                  quote=str(ref.metadata.get("source_excerpt") or ""), turn_id=""), svc=svc))
    if path == "memory_space":
        import memory_space
        return "Done" if run(memory_space.forget_row(UID, w["fact"], svc=svc)) == "forgotten" else "not done"
    if path == "forget_last":                                          # the target is the saved TURN itself
        return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))
    if path == "night_mind":                                           # the dispatcher with the turn's id, as forget-last calls it
        run(turn_forget.erase_turn_words(UID, (w["mid"],), (KW,)))
        return "Done"
    raise AssertionError(path)


#: (path, kind) -> what must be GONE (True) or STAY (False). Unlisted keys are not asserted: the legacy bystander FACT is the same
#: fact as the target (same excerpt, near-identical words), so on the fact paths it is a TWIN and goes by the twin rule
#: (test_a_twin_of_a_non_turn_fact_goes_with_it_and_a_look_alike_stays pins that), or a store the path does not own. ``transcript`` = the target turn's saved transcript flagged off the record.
_MATRIX = {
    ("forget_it_after_why", "turn"): dict(entry=True, transcript=True, fact=True, obs=True, old_entry=True, old_obs=True),
    ("forget_it_after_why", "kind"): dict(entry=True, transcript=False, fact=False, old_entry=False, old_fact=False, old_obs=False),
    ("forget_it_after_why", "legacy"): dict(entry=True, transcript=True, fact=True, obs=True),
    ("forget_it_fact", "turn"): dict(fact=True, transcript=True, entry=True, obs=True, old_entry=True),
    ("forget_it_fact", "kind"): dict(fact=True, transcript=False, entry=False, old_entry=False, old_obs=False),
    ("forget_it_fact", "legacy"): dict(fact=True, transcript=True, entry=True, obs=True),
    ("memory_space", "turn"): dict(fact=True, transcript=True, entry=True, obs=True, old_entry=True),
    ("memory_space", "kind"): dict(fact=True, transcript=False, entry=False, old_entry=False, old_obs=False),
    ("memory_space", "legacy"): dict(fact=True, transcript=True, entry=True, obs=True),
    ("forget_last", "turn"): dict(fact=True, transcript=True, entry=True, obs=True, old_fact=True),
    ("forget_last", "kind"): dict(fact=False, transcript=True, entry=False, old_fact=True),
    ("forget_last", "legacy"): dict(fact=True, transcript=True, entry=True, obs=True),
    ("night_mind", "turn"): dict(obs=True, old_obs=True),
    ("night_mind", "legacy"): dict(obs=True),
}


def _gone(svc, chat, w):
    rows = {k[1] for k in xw.get_backend().rows}
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    night = set(_night())
    out = {"transcript": chat.off(w["mid"])}
    for k in ("entry", "other_entry", "old_entry"):
        if k in w:
            out[k] = w[k] not in rows
    for k in ("fact", "other_fact", "old_fact"):
        if k in w:
            out[k] = w[k] not in approved
    for k in ("obs", "old_obs"):
        if k in w:
            out[k] = w[k] not in night
    return out


@pytest.mark.parametrize("path,kind", sorted(_MATRIX))
def test_each_row_kind_through_each_forget_path(world, path, kind):
    """Per row, three kinds: bound to a saved turn = by id only; bound to a NON-TURN source = only that row (no saved transcript is
    flagged, no legacy row with the same words is taken: it was not the target); legacy = by its words. Rows of ANOTHER saved turn
    with the very same words survive every cell."""
    svc, chat = world
    w = _plant_kind(svc, chat, kind)
    reply = _run_path(path, svc, w)
    gone = _gone(svc, chat, w)
    assert reply.startswith("Done"), (reply, gone)
    assert gone.get("other_entry") is False and gone.get("other_fact") is False, gone          # another turn: by id only
    for key, want in _MATRIX[(path, kind)].items():
        if key in gone:
            assert gone[key] is want, (key, want, gone)


def test_a_non_turn_kind_beats_a_coincidental_words_match_at_write_time(world):
    """A pendant quote that happens to equal a just-saved chat turn is still the recording's: the explicit / lane kind is stamped,
    never that turn's id (``_write_provenance`` resolves the kind before ``turn_ref``)."""
    svc, chat = world
    from routers.chat import _save_chat_message

    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", KW, user_id=UID)) is True
    assert memory_service._write_provenance(UID, "omi_review", {}, KW, None) == {"source_kind": "omi_recording"}
    assert memory_service._write_provenance(UID, "turn_digest", {"source_kind": "omi_recording"}, KW, None) == {
        "source_kind": "omi_recording"}
    assert "source_turn" in memory_service._write_provenance(UID, "turn_digest", {}, KW, None)            # control: a chat lane


@pytest.mark.parametrize("kind,flagged", [("turn", True), ("kind", False), ("legacy", True)])
def test_the_topic_forget_treats_each_index_hit_by_its_kind(world, kind, flagged):
    """forget_topic's index hits, per row: an entry of a saved turn flags that turn by id; a legacy entry by its words; an entry
    bound to a NON-TURN source (``@src:<kind>``) is deleted and NO saved transcript is flagged for it (on e27797bfb its empty
    ``mid_of`` sent it to the words matcher, which flagged the look-alike transcript). The turn notes are cleared so only the
    index hit can reach the transcript."""
    svc, chat = world
    w = _plant_kind(svc, chat, kind)
    be = xw.get_backend()
    for tid in [k[1] for k in be.rows if k[1] != w["entry"]]:
        run(be.delete(UID, [tid]))                                     # the target entry alone (a topic forget takes every hit)
    turn_forget.reset()
    res = run(forget_topic.forget(svc, UID, "my kayak paddles"))
    assert not (res.store_down or res.failed), res
    assert w["entry"] not in {k[1] for k in be.rows}
    assert chat.off(w["mid"]) is flagged


@pytest.mark.parametrize("path", ["forget_it_fact", "memory_space"])
def test_a_twin_of_a_non_turn_fact_goes_with_it_and_a_look_alike_stays(world, path):
    """The twin rule (the same fact written twice for the same member - e.g. a capture's copy) still holds for a target bound to a
    NON-TURN source: its true twin (same excerpt, near-identical words, no saved turn of its own) goes, or the forgotten fact comes
    straight back. Not twins: a row of ANOTHER saved turn with the very same words (ids separate), and a legacy row that only
    shares some words with another excerpt. The non-turn target still flags no saved transcript."""
    svc, chat = world
    w = _plant_kind(svc, chat, "kind")
    look = run(svc.ingest("User's kayak is red and lives at the club", user_id=UID, source="turn_digest", status="approved",
                          source_excerpt="My kayak is red and it lives at the club"))
    for k in ("source_turn", "source_kind", "source_excerpt_said"):   # no provenance of any kind
        svc._collection().rows[look.id][1].pop(k, None)
    reply = _run_path(path, svc, w)
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert reply.startswith("Done"), reply
    assert w["fact"] not in approved and w["old_fact"] not in approved            # the target and its true twin
    assert w["other_fact"] in approved and look.id in approved                       # ids separate; a look-alike is no twin
    assert chat.off(w["mid"]) is False                                               # still no transcript for a non-turn target


# ── a saved-turn id is recorded only once its row COMMITTED (review P1: the voice arrival insert failing transiently) ────────────

RECOVER = "My climbing shoes are drying on the back porch"


def test_a_failed_insert_never_names_the_turn_and_the_recovery_copy_does(world):
    """The arrival insert fails (its id names no transcript row); the recovery save (count_turn=False) commits another id. The
    turn's bookkeeping holds EXACTLY the committed id, the fact and the exact-words entry written in that turn carry it, and
    forgetting the turn removes them and flags the committed transcript row. On bc5884042 the failed id was recorded first and
    ``turn_ref`` took it, so the derived rows named a row that does not exist."""
    from routers.chat import _save_chat_message

    svc, chat = world
    calls = {"n": 0}

    def failing_ctx():
        calls["n"] += 1
        if calls["n"] == 1:
            @contextlib.asynccontextmanager
            async def _down():
                raise RuntimeError("transient insert failure")
                yield  # pragma: no cover
            return _down()
        return chat.ctx()

    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", failing_ctx)
        assert run(_save_chat_message("s-1", "user", RECOVER, user_id=UID, message_id="m-arrival-failed")) is False
        assert run(_save_chat_message("s-1", "user", RECOVER, user_id=UID, count_turn=False, message_id="m-recovered")) is True
    assert "m-arrival-failed" not in chat.rows and "m-recovered" in chat.rows
    turn = turn_forget.previous_turn(UID)
    assert turn is not None and turn.mids == ("m-recovered",)

    async def capture():
        with turn_forget.capture_scope(UID, RECOVER):
            assert await xw.index_turn(UID, RECOVER) is True
            return await svc.ingest("User's climbing shoes are drying on the back porch", user_id=UID, source="turn_digest",
                                    status="approved", source_excerpt=RECOVER)
    fact = run(capture())
    assert fact is not None and fact.id in {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert run(svc.get(fact.id)).metadata.get("source_turn") == "m-recovered"
    assert [xw.mid_of(k[1]) for k in xw.get_backend().rows] == ["m-recovered"]
    reply = run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))
    assert reply.startswith("Done"), reply
    assert fact.id not in {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert not xw.get_backend().rows and chat.off("m-recovered")


# ── a LEGACY target's resolved saved turns come back to the caller: everything bound to them goes (review P1) ─────────────────

UPGRADED = "My bike pump hangs on the hook inside the laundry"


def test_a_legacy_entry_takes_the_upgraded_facts_bound_to_its_turn(world):
    """A pre-upgrade exact-words entry (no id) is explained after the upgraded digest wrote a fact from the same transcript turn
    WITH that turn's id. "forget it" resolves the legacy words to that saved turn FIRST and the bound fact goes too; a fact bound
    to a different turn with similar words stays. On bc5884042 the resolved ids never left ``_erase_words``, so the bound fact
    stayed approved behind a "Done"."""
    from routers.chat import _save_chat_message

    svc, chat = world
    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", UPGRADED, user_id=UID)) is True
    mid = next(m for m, (c, _meta) in chat.rows.items() if c == UPGRADED)
    run(xw.get_backend().add(UID, "xw-pre-upgrade", time.time(), UPGRADED, xw.tokens_column(UPGRADED), "chat"))
    bound = run(svc.ingest("User's bike pump hangs inside the laundry", user_id=UID, source="digest", status="approved",
                           source_excerpt=UPGRADED, metadata={"source_turn": mid}))
    other = run(svc.ingest("User's bike pump is kept in the garage", user_id=UID, source="digest", status="approved",
                           source_excerpt="my bike pump is kept in the garage", metadata={"source_turn": "m-another-turn-bp"}))
    assert {bound.id, other.id} <= {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=UPGRADED, quote=UPGRADED, turn_id="xw-pre-upgrade")
    assert run(pa._forget_turn(UID, exp, svc=svc)).startswith("Done")
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert bound.id not in approved                     # bound to the resolved turn: gone
    assert other.id in approved                         # another turn: by id only
    assert chat.off(mid) and "xw-pre-upgrade" not in {k[1] for k in xw.get_backend().rows}


# ── one spoken turn committed TWICE (the arrival save, and the reply's save when the arrival outcome came too late) ─────────────

TWICE = "My fishing rods lean against the wall of the carport"


def _plant_twice(svc, chat):
    """Two committed transcript rows of ONE turn (``Turn.mids`` holds both); the turn's capture stamps the first id; the nightly
    catch-up indexing the SECOND row creates an entry with the second id; the digest binds a fact to the second id."""
    from routers.chat import _save_chat_message

    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", TWICE, user_id=UID, message_id="m-first")) is True
        assert run(_save_chat_message("s-1", "user", TWICE, user_id=UID, count_turn=False, message_id="m-second")) is True
    assert turn_forget.previous_turn(UID).mids == ("m-first", "m-second")

    async def capture():
        with turn_forget.capture_scope(UID, TWICE):
            assert await xw.index_turn(UID, TWICE) is True
            return await svc.ingest("User's fishing rods lean against the carport wall", user_id=UID, source="turn_digest",
                                    status="approved", source_excerpt=TWICE)
    fact = run(capture())
    assert run(svc.get(fact.id)).metadata.get("source_turn") == "m-first"
    assert run(xw.index_turn(UID, TWICE, said_at=time.time() - 3600, message_id="m-second")) is True   # the catch-up's copy
    digest = run(svc.ingest("User keeps the fishing rods in the carport", user_id=UID, source="digest", status="approved",
                            source_excerpt=TWICE, metadata={"source_turn": "m-second"}))
    assert sorted(xw.mid_of(k[1]) for k in xw.get_backend().rows) == ["m-first", "m-second"]
    return {"fact": fact.id, "digest": digest.id}


def _forget_twice(path, svc, planted):
    entry = next(k[1] for k in xw.get_backend().rows if xw.mid_of(k[1]) == "m-first")
    if path == "forget_it_after_why":
        stored = xw.get_backend().rows[(UID, entry)]["text"]
        return run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=entry),
                                   svc=svc))
    if path == "forget_it_fact":
        ref = run(svc.get(planted["fact"]))
        return run(pa.forget_it(UID, mp.Explained(seq=1, row_id=ref.id, ts=time.time(), text=ref.text,
                                                  quote=str(ref.metadata.get("source_excerpt") or ""), turn_id=""), svc=svc))
    if path == "memory_space":
        import memory_space
        return "Done" if run(memory_space.forget_row(UID, planted["fact"], svc=svc)) == "forgotten" else "not done"
    if path == "forget_topic":
        res = run(forget_topic.forget(svc, UID, "my fishing rods"))
        return "Done" if not (res.store_down or res.failed or res.too_many) else "not done"
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))


@pytest.mark.parametrize("path", ["forget_it_after_why", "forget_it_fact", "memory_space", "forget_topic", "forget_last"])
def test_a_turn_committed_twice_is_forgotten_whole_from_any_one_id(world, path):
    """A forget that starts from ONE item of the turn (an entry or a fact carrying the FIRST id) takes the whole committed set:
    both transcript rows are flagged, no exact-words entry is left for either id, the fact bound to the SECOND id goes, and a
    simulated nightly catch-up (``index_turn`` for every unflagged transcript row, as ``backfill_recent`` does) re-creates
    nothing. The durable link is the transcript itself (same session + words: ``exact_words.turn_copies``), not the in-process
    ``Turn.mids``. On f2a5d630f the entry / fact / memory-space paths flagged only the first row and left the second id's entry
    and fact (forget-last and the topic forget were already whole: guards)."""
    svc, chat = world
    planted = _plant_twice(svc, chat)
    reply = _forget_twice(path, svc, planted)
    assert reply.startswith("Done"), reply
    assert chat.off("m-first") and chat.off("m-second")
    assert not xw.get_backend().rows
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert planted["fact"] not in approved and planted["digest"] not in approved
    for mid in ("m-first", "m-second"):                                   # the nightly catch-up: unflagged rows only
        if not chat.off(mid):
            run(xw.index_turn(UID, TWICE, message_id=mid))
    assert not xw.get_backend().rows


# ── any failed reject fails the forget (after everything reachable ran), and the retry finishes it (cross-review, PR #2574) ─────

@pytest.mark.parametrize("path", ["forget_it_after_why", "forget_it_fact", "memory_space"])
def test_a_refused_reject_of_a_bound_row_is_not_done_and_the_retry_finishes(world, monkeypatch, path):
    """The store refuses the reject of a row bound to the target's turn (it returns None once): the forget must NOT say Done while
    that fact is still approved, everything else reachable still goes, and the same forget again completes it."""
    svc, chat = world
    w = _plant_kind(svc, chat, "turn")
    stray = run(svc.ingest("User keeps the kayak paddles behind the garage door", user_id=UID, source="digest",
                           status="approved", source_excerpt="something else entirely about kayaks",
                           metadata={"source_turn": w["mid"]}))          # bound to the same turn, not a twin by words
    real, refused = svc.review, {"left": 1}

    async def review(row_id, *a, **k):
        if row_id == stray.id and refused["left"]:
            refused["left"] -= 1
            return None
        return await real(row_id, *a, **k)
    monkeypatch.setattr(svc, "review", review)
    stored = xw.get_backend().rows[(UID, w["entry"])]["text"]                   # the explained quote, kept for the retry

    def go():
        if path == "forget_it_after_why":
            return run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored,
                                                         turn_id=w["entry"]), svc=svc))
        if path == "memory_space":
            import memory_space
            try:
                return "Done" if run(memory_space.forget_row(UID, w["fact"], svc=svc)) == "forgotten" else "not done"
            except RuntimeError:
                return "not done"
        return _run_path(path, svc, w)

    first = go()
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert not first.startswith("Done") and stray.id in approved, first
    assert w["fact"] not in approved and chat.off(w["mid"])                     # the rest that was reachable still went
    second = go()                                                               # the same forget again: it finishes the job
    assert second.startswith("Done"), second
    assert stray.id not in {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}



def test_the_same_words_hours_apart_are_two_turns_not_copies(world):
    """The owner says the same sentence twice in one session, hours apart: two turns. Forgetting the first (from its entry) leaves
    the second's transcript row, entry and fact. Copies are only rows saved within ``COPY_WINDOW_S`` of each other (the voice lane's
    arrival / recovery race is seconds); on ac3fa1d3d the second turn's fact went too."""
    from routers.chat import _save_chat_message

    svc, chat = world
    t0 = time.time() - 6 * 3600
    stamped = {}
    for mid, at in (("m-morning", t0), ("m-afternoon", t0 + 6 * 3600 - 60)):
        chat.clock = at
        with chat.patch.context() as scoped:
            scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
            assert run(_save_chat_message("s-1", "user", TWICE, user_id=UID, message_id=mid)) is True
        assert run(xw.index_turn(UID, TWICE, said_at=at, message_id=mid)) is True
        stamped[mid] = run(svc.ingest(f"User's fishing rods lean on the carport wall ({mid})", user_id=UID, source="digest",
                                      status="approved", source_excerpt=TWICE, metadata={"source_turn": mid})).id
    chat.clock = None
    entry = next(k[1] for k in xw.get_backend().rows if xw.mid_of(k[1]) == "m-morning")
    stored = xw.get_backend().rows[(UID, entry)]["text"]
    assert run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=entry),
                               svc=svc)).startswith("Done")
    approved = {r.id for r in run(svc.list_by_status(user_id=UID, status="approved"))}
    assert chat.off("m-morning") and stamped["m-morning"] not in approved
    assert not chat.off("m-afternoon") and stamped["m-afternoon"] in approved
    assert [xw.mid_of(k[1]) for k in xw.get_backend().rows] == ["m-afternoon"]


# ── a retry can always find its targets: the ids a legacy target resolved are KEPT with it (bot P1, PR #2574) ────────────────

@pytest.mark.parametrize("fault", ["fact_read", "bound_reject"])
def test_a_legacy_forget_that_fails_part_way_is_finished_by_the_retry(world, monkeypatch, fault):
    """A legacy entry, an upgraded fact bound to its turn. The forget fails part-way - the fact-store read (once), or the reject of
    the bound fact (once, after the transcript was flagged). The answer is not Done; the retry, from the target the forget kept
    (``mp`` state, no manual help), removes the bound fact. On b7aa267a the transcript was flagged and the entry deleted before
    the read, and a retry could not resolve the turn again (the transcript read skips off-record rows)."""
    from routers.chat import _save_chat_message

    svc, chat = world
    with chat.patch.context() as scoped:
        scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
        assert run(_save_chat_message("s-1", "user", UPGRADED, user_id=UID)) is True
    mid = next(m for m, (c, _meta) in chat.rows.items() if c == UPGRADED)
    run(xw.get_backend().add(UID, "xw-pre-upgrade", time.time(), UPGRADED, xw.tokens_column(UPGRADED), "chat"))
    bound = run(svc.ingest("User's bike pump hangs inside the laundry", user_id=UID, source="digest", status="approved",
                           source_excerpt=UPGRADED, metadata={"source_turn": mid}))
    real_list, real_review, left = svc.list_by_status, svc.review, {"n": 1}

    async def list_by_status(*a, **k):
        if fault == "fact_read" and left["n"]:
            left["n"] -= 1
            raise RuntimeError("store hiccup")
        return await real_list(*a, **k)

    async def review(row_id, *a, **k):
        if fault == "bound_reject" and row_id == bound.id and left["n"]:
            left["n"] -= 1
            return None
        return await real_review(row_id, *a, **k)
    monkeypatch.setattr(svc, "list_by_status", list_by_status)
    monkeypatch.setattr(svc, "review", review)
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text=UPGRADED, quote=UPGRADED, turn_id="xw-pre-upgrade")
    first = run(pa._forget_turn(UID, exp, svc=svc))
    assert not first.startswith("Done"), first
    assert bound.id in {r.id for r in run(real_list(user_id=UID, status="approved"))}
    kept = mp._state(UID).explained                                   # what the forget kept for the retry
    assert kept is not None and kept.turn_id == "xw-pre-upgrade"
    second = run(pa._forget_turn(UID, kept, svc=svc))
    assert second.startswith("Done"), second
    assert bound.id not in {r.id for r in run(real_list(user_id=UID, status="approved"))}
    assert chat.off(mid) and "xw-pre-upgrade" not in {k[1] for k in xw.get_backend().rows}


# ── a SURVIVING night-mind thread keeps nothing derived from an erased turn (cross-review, PR #2574) ─────────────────────────────

POTTERY = "My cousin Odalys starts a pottery class next Tuesday evening"
HOSE = "The garden hose still leaks near the back tap"


def _plant_thread(chat, title_from):
    """One thread, two observations from two saved turns (POTTERY, HOSE); its title and anchors carry ``title_from``'s words."""
    from routers.chat import _save_chat_message

    mids = {}
    for said in (POTTERY, HOSE):
        with chat.patch.context() as scoped:
            scoped.setattr(chat.db_pool, "get_db_ctx", chat.ctx)
            assert run(_save_chat_message("s-1", "user", said, user_id=UID)) is True
        mids[said] = next(m for m, (c, _meta) in chat.rows.items() if c == said)
    be, now = night_store.get_backend(), time.time()
    title = " ".join(title_from.split()[:8])
    run(be.put_thread(night_store.thread_row(id="t-mixed", user_id=UID, title=title, anchors="@odalys pottery class garden hose leaks",
                                             topic="", first_day="2026-10-07", last_day="2026-10-08", mentions_n=2)))
    for oid, said, day, at in (("o-pottery", POTTERY, "2026-10-07", now - 90000), ("o-hose", HOSE, "2026-10-08", now - 3600)):
        run(be.put_observation(night_store.obs_row(id=oid, user_id=UID, thread_id="t-mixed", turn_id=mids[said], quote=said,
                                                   who="Odalys" if said == POTTERY else "", day=day, said_at=at)))
    return mids


def _thread():
    return next(t for t in run(night_store.get_backend().threads(UID)) if t["id"] == "t-mixed")


@pytest.mark.parametrize("path", ["dispatcher", "forget_it_after_why"])
def test_a_surviving_thread_loses_the_title_and_anchors_of_an_erased_turn(world, path):
    """The thread's title came from the POTTERY turn; that turn is forgotten by id. The HOSE observation (another turn) survives,
    so the thread survives - and its title, anchors and topic no longer carry the erased words. On e19282eac an id-scoped erase
    skipped the title and left it (a thread was only touched once orphaned)."""
    svc, chat = world
    mids = _plant_thread(chat, POTTERY)
    if path == "dispatcher":
        run(turn_forget.erase_turn_words(UID, (mids[POTTERY],), ()))
    else:
        assert run(xw.index_turn(UID, POTTERY, message_id=mids[POTTERY])) is True
        entry = next(k[1] for k in xw.get_backend().rows if xw.mid_of(k[1]) == mids[POTTERY])
        stored = xw.get_backend().rows[(UID, entry)]["text"]
        assert run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=entry),
                                   svc=svc)).startswith("Done")
    assert _night() == ["hose"]                                              # the other turn's observation survives
    t = _thread()
    blob = (t["title"] + " " + t["anchors"]).lower()
    assert not {"pottery", "odalys", "cousin", "tuesday"} & set(re.findall(r"[a-z]+", blob)), t
    assert t["title"] == " ".join(HOSE.split()[:8]) and t["mentions_n"] == 1 and t["first_day"] == "2026-10-08"


def test_a_surviving_thread_whose_title_came_from_the_survivor_keeps_it(world):
    svc, chat = world
    mids = _plant_thread(chat, HOSE)
    run(turn_forget.erase_turn_words(UID, (mids[POTTERY],), ()))
    t = _thread()
    assert t["title"] == " ".join(HOSE.split()[:8])                          # supported by what survives: kept as it was
    assert "pottery" not in t["anchors"] and "@odalys" not in t["anchors"]
