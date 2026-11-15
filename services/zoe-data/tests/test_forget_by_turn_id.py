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

    def ctx(self):
        chat = self

        class _Db:
            async def execute(self, sql, params=()):
                if sql.lstrip().upper().startswith("INSERT INTO CHAT_MESSAGES"):
                    chat.rows.setdefault(params[0], [params[3], params[4]])
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
        return [(m, self.rows[m][1]) for m in mids if m in self.rows]

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
    for k in ("source_turn", "source_kind"):                      # an OLD row: written before the ids
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
        for k in ("source_turn", "source_kind"):
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
