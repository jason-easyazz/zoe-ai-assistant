"""Every forget resolves to SAVED-TURN ids and erases by id (PR #2574, review rounds 3-6).

Two look-alike turns that differ only by a contact detail and a trailing word store the SAME redacted words wherever the write
boundary scrubs (the exact-words index, a fact row's excerpt), and one redacted excerpt sits INSIDE the other. So no forget may
compare redacted text: each derived row carries the saved turn (``chat_messages.id``) it came from, stamped at write time, and
every forget path resolves to those ids first. For each path, forgetting one turn takes exactly that turn's exact-words entry,
fact row, capture copy, transcript flag and night-mind observation, and nothing of the other turn.

A rotated forget-ledger secret changes nothing: no forget reads the keyed tag. Synthetic placeholder words and invented addresses
only. Negative control: on d6e876e0d the sweep fails (the fact path left the
exact-words entry behind; the containment case took the other turn's fact; the stored rows carried no saved-turn id).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import exact_words as xw
import intent_router
import memory_provenance as mp
import memory_service
import night_store
import provenance_answers as pa
import turn_forget
from forgotten_support import ledger_env  # noqa: F401 - fixture (the secret the audit tags are keyed with)
from memory_service import MemoryService
from test_memory_authority import _Col

pytestmark = pytest.mark.ci_safe

UID = "demo_bar_0000id01"
TURNS = {"m1": "My plumber works from 1 Example Street", "m2": "My plumber works from 2 Example Street on weekdays"}
FACTS = {"m1": "User's plumber works from 1 Example Street", "m2": "User's plumber works from 2 Example Street on weekdays"}
COPIES = {"m1": "User has a plumber at 1 Example Street", "m2": "User has a plumber at 2 Example Street"}
KEPT = "My plumber works from"                     # the words the scrub leaves: identical in both turns' night observations


def run(coro):
    return asyncio.run(coro)


class _Transcript:
    """``chat_messages`` behind exact_words' transcript seams: by id, and (old callers) by a needle."""

    def __init__(self):
        self.rows = {mid: [text, None] for mid, text in TURNS.items()}

    async def by_id(self, user_id, mids):
        return [(m, self.rows[m][1]) for m in mids if m in self.rows]

    async def candidates(self, user_id, needle):
        return [(m, c, meta) for m, (c, meta) in self.rows.items() if needle in c.lower()]

    async def setmeta(self, rid, meta):
        self.rows[rid][1] = meta


@pytest.fixture
def world(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", mp.ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
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
    t = _Transcript()
    monkeypatch.setattr(xw, "_transcript_rows_by_id", t.by_id)
    monkeypatch.setattr(xw, "_transcript_candidates", t.candidates)
    monkeypatch.setattr(xw, "_set_transcript_metadata", t.setmeta)
    monkeypatch.setattr(xw, "SqlBackend", type(xw.get_backend()))          # the lab index stands in for the SQL one
    turn_forget.reset()
    mp.reset()
    yield svc, t
    turn_forget.reset()
    mp.reset()


def _plant(svc, target: str) -> dict:
    """Both turns saved and captured the way the live lanes do it, the TARGET last (so "forget that" binds to it)."""
    ids: dict = {}
    for mid in sorted(TURNS, key=lambda m: m == target):
        said = TURNS[mid]
        turn_forget.note_user_turn(UID, said, now=time.time(), mid=mid)          # routers.chat._save_chat_message

        async def capture():
            with turn_forget.capture_scope(UID, said):                          # the post-turn capture of THAT turn
                assert await xw.index_turn(UID, said) is True
                fact = await svc.ingest(FACTS[mid], user_id=UID, source="turn_digest", status="approved", source_excerpt=said)
                copy = await svc.ingest(COPIES[mid], user_id=UID, source="chat_regex", status="approved",
                                        source_excerpt=said[:30])
                return fact, copy
        ids[mid] = run(capture())
        run(night_store.get_backend().put_observation(night_store.obs_row(
            id=f"o-{mid}", user_id=UID, turn_id=mid, quote=KEPT, day="2026-10-08", said_at=time.time())))
    return ids


def _xw_row(mid):
    return next((k[1], r["text"]) for k, r in xw.get_backend().rows.items() if xw.mid_of(k[1]) == mid)


def _approved(svc):
    return {r.text for r in run(svc.list_by_status(user_id=UID, status="approved"))}


def _forget_last(svc, target, ids):
    return run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))


def _forget_quoted_turn(svc, target, ids):
    tid, stored = _xw_row(target)
    assert "[REDACTED]" in stored                                              # the quote IS the redacted stored form
    return run(pa._forget_turn(UID, mp.Explained(seq=1, row_id="", ts=time.time(), text=stored, quote=stored, turn_id=tid), svc=svc))


def _forget_explained_fact(svc, target, ids):
    ref = run(svc.get(ids[target][0].id))
    quote = str(ref.metadata.get("source_excerpt") or "")
    assert "[REDACTED]" in quote
    return run(pa.forget_it(UID, mp.Explained(seq=1, row_id=ref.id, ts=time.time(), text=ref.text, quote=quote), svc=svc))


PATHS = {"forget_last": _forget_last, "provenance_turn": _forget_quoted_turn, "provenance_fact": _forget_explained_fact}


@pytest.mark.parametrize("rotated", [False, True])
@pytest.mark.parametrize("target", sorted(TURNS))
@pytest.mark.parametrize("path", sorted(PATHS))
def test_every_forget_path_takes_exactly_one_look_alike_turn(world, ledger_env, monkeypatch, path, target, rotated):
    """Also after the ledger secret ROTATES between the write and the forget: the saved-turn id is the key, the keyed tag only an
    audit identity, so a stale tag can never strand a row."""
    import memory_forgotten

    svc, transcript = world
    other = next(m for m in TURNS if m != target)
    ids = _plant(svc, target)
    assert all("~" in k[1] for k in xw.get_backend().rows)                    # redacted entries carry their (audit) tag
    if rotated:
        monkeypatch.setenv(memory_forgotten.SALT_ENV, "another-unit-test-ledger-secret-9876543210")
    for mid in TURNS:                                                          # every derived row carries its saved turn
        assert run(svc.get(ids[mid][0].id)).metadata.get("source_turn") == mid
        assert run(svc.get(ids[mid][1].id)).metadata.get("source_turn") == mid
    assert PATHS[path](svc, target, ids).startswith("Done")
    assert {xw.mid_of(k[1]) for k in xw.get_backend().rows} == {other}          # exact words
    assert _approved(svc) == {FACTS[other], COPIES[other]}                      # fact row + capture copy
    assert mp.OFF_RECORD_JSON_MARK in (transcript.rows[target][1] or "")        # transcript flag
    assert transcript.rows[other][1] is None
    assert [o["turn_id"] for o in run(night_store.get_backend().observations(UID))] == [other]   # night mind


def test_a_redacted_quote_with_no_saved_turn_id_is_refused_not_guessed(world):
    """An entry written before the ids: its redacted words name no turn, so the forget is not confirmed and nothing is touched."""
    svc, transcript = world
    run(xw.get_backend().add(UID, "xw-old", time.time(), "My plumber works from [REDACTED]", " plumber work ", "chat"))
    exp = mp.Explained(seq=1, row_id="", ts=time.time(), text="My plumber works from [REDACTED]",
                       quote="My plumber works from [REDACTED]", turn_id="")
    assert not run(pa._forget_turn(UID, exp, svc=svc)).startswith("Done")
    assert all(meta is None for _c, meta in transcript.rows.values())
