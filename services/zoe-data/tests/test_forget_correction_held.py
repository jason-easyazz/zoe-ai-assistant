"""FORGET_CORRECTION_HELD: the instrument for #2487 (gap audit 2026-11-14.6). At a forget's completion it counts the corrections
``value_correction._REMEMBERED`` still holds about what was forgotten. Counts only, read-only, never raises.

Before #2487 (this branch's base) the count is > 0 on every path below: that is the BEFORE number, proven here. After #2487 it is
0. The instrument changes nothing: each path's result and the in-process memory are identical with the instrument stubbed out.
Invented names only (the repo is public). ``ci_safe``.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import re
from pathlib import Path

import pytest

import value_correction as vc

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
A, B = "demo_fch_member_a", "demo_fch_member_b"
LINE_RE = re.compile(r"^FORGET_CORRECTION_HELD path=(topic|name|last|member|other) held=(-1|\d{1,4}) live=(-1|\d{1,4})$")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def seeded():
    vc.reset_state()
    vc._remember(A, vc.Correction("Juno", "8", "7"))
    vc._remember(A, vc.Correction("the book club", "the 19th", "the 12th"))
    vc._remember(A, vc.Correction("Wren", "4", "3"))
    vc._remember(B, vc.Correction("Juno", "6", "5"))
    yield
    vc.reset_state()


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("FORGET_CORRECTION_HELD")]


def _snapshot():
    """The memory without its timestamps (a re-seed gets new ones)."""
    return {u: [c for _t, c in v] for u, v in copy.deepcopy(vc._REMEMBERED).items()}


# ── the counter ────────────────────────────────────────────────────────────────────────────

def test_counts_by_pattern_text_or_everything_and_never_mutates():
    before = copy.deepcopy(vc._REMEMBERED)
    assert vc.held_after_forget(A, pattern=re.compile(r"\bjuno\b", re.I)) == (1, 3)
    assert vc.held_after_forget(A, pattern=lambda t: "book" in t) == (1, 3)
    assert vc.held_after_forget(A, texts=["wren is 4 not 3"]) == (1, 3)
    assert vc.held_after_forget(A, texts=["juniper is lovely"]) == (0, 3)        # not a whole-word subject
    assert vc.held_after_forget(A) == (3, 3) and vc.held_after_forget(B) == (1, 1)
    assert vc.held_after_forget("nobody") == (0, 0)
    assert vc._REMEMBERED == before                                              # read-only


def test_an_expired_entry_is_not_held(monkeypatch):
    import time
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + vc.REMEMBER_S + 1)
    assert vc.held_after_forget(A) == (0, 0)


def test_never_raises_and_a_broken_store_is_minus_one():
    real = vc._REMEMBERED
    vc._REMEMBERED = None
    try:
        assert vc.held_after_forget(A) == (-1, -1)
        vc.log_held_after_forget("name", A)
    finally:
        vc._REMEMBERED = real

    def boom(_t):
        raise ValueError("unreadable")
    assert vc.held_after_forget(A, pattern=boom) == (-1, -1)


def test_the_line_is_codes_and_counts_only(caplog):
    caplog.set_level(logging.INFO, logger="value_correction")
    vc._remember(A, vc.Correction("Zelphinax", "9", "8"))
    vc.log_held_after_forget("name", A, pattern=re.compile("Zelphinax"))
    vc.log_held_after_forget("Zelphinax private words", A)                         # an unknown path is `other`, never echoed
    vc.log_held_after_forget("member", A)
    lines = _lines(caplog)
    assert len(lines) == 3 and all(LINE_RE.match(x) for x in lines)
    assert lines[0] == "FORGET_CORRECTION_HELD path=name held=1 live=4"
    assert lines[1].startswith("FORGET_CORRECTION_HELD path=other ")
    assert not any("Zelphinax" in x or A in x for x in lines)


def test_the_strict_format_rejects_text():
    """Negative control for LINE_RE: a line with a name or an id in it does not pass."""
    assert not LINE_RE.match("FORGET_CORRECTION_HELD path=name held=1 live=4 subject=Juno")
    assert not LINE_RE.match("FORGET_CORRECTION_HELD path=Juno held=1 live=4")
    assert not LINE_RE.match(f"FORGET_CORRECTION_HELD path=name held=1 live=4 user={A}")


# ── each forget path, through its real entry point: the BEFORE number, and behaviour-neutral ──────

@pytest.fixture
def forget_env(monkeypatch):
    import exact_words
    import memory_tombstones
    import pending_suggestions
    import turn_forget

    async def flag(_u, _t):
        return 1

    class _NoOffers:
        async def fetch(self, *_a):
            return []

        async def execute(self, *_a):
            return None

    @contextlib.asynccontextmanager
    async def offers():
        yield _NoOffers()

    monkeypatch.setattr(pending_suggestions, "get_db_ctx", offers)
    monkeypatch.setattr(exact_words, "forget_transcript", flag)
    turn_forget.reset()
    memory_tombstones.clear_all()
    exact_words.set_backend(exact_words.MemoryBackend())
    yield
    exact_words.set_backend(None)
    turn_forget.reset()
    memory_tombstones.clear_all()


class _Row:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": A}


class _Svc:
    def __init__(self, *rows):
        self.rows = [_Row(i, t) for i, t in rows]

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def search(self, *_a, **_k):
        return list(self.rows)

    async def review(self, *_a, **_k):
        return object()


def _both_ways(monkeypatch, caplog, call):
    """(result, memory, lines) with the instrument, then (result, memory) with it stubbed out: they must be equal."""
    caplog.set_level(logging.INFO, logger="value_correction")
    out = call()
    mem = _snapshot()
    lines = _lines(caplog)
    vc.reset_state()
    vc._remember(A, vc.Correction("Juno", "8", "7"))
    vc._remember(A, vc.Correction("the book club", "the 19th", "the 12th"))
    vc._remember(A, vc.Correction("Wren", "4", "3"))
    vc._remember(B, vc.Correction("Juno", "6", "5"))
    monkeypatch.setattr(vc, "log_held_after_forget", lambda *a, **k: None)
    out2 = call()
    assert repr(out) == repr(out2) and _snapshot() == mem
    return out, lines


def test_topic_forget_logs_held_and_is_unchanged(forget_env, monkeypatch, caplog):
    import forget_topic

    res, lines = _both_ways(monkeypatch, caplog,
                            lambda: run(forget_topic.forget(_Svc(("r1", "The book club meets at the library")), A, "the book club")))
    assert res.forgotten == 1 and lines == ["FORGET_CORRECTION_HELD path=topic held=1 live=3"]   # before #2487: still held


def test_name_forget_through_the_cascade_logs_held_and_is_unchanged(monkeypatch, caplog):
    import memory_forget_cascade as cascade

    async def only_mutes(user_id, rx, mutes, res, **_k):      # the in-process step only: no database, no file store
        await cascade._step("restraint_mutes", mutes, res)

    async def no_retry(*_a, **_k):
        return None
    monkeypatch.setattr(cascade, "_clear", only_mutes)
    monkeypatch.setattr(cascade, "retry_pending", no_retry)
    import restraint

    async def zero(*_a, **_k):
        return 0
    monkeypatch.setattr(restraint, "erase_entity", zero)
    res, lines = _both_ways(monkeypatch, caplog, lambda: run(cascade.cascade_forget(A, "Juno")))
    assert not res.failed and lines == ["FORGET_CORRECTION_HELD path=name held=1 live=3"]


def test_forget_that_logs_held_and_is_unchanged(monkeypatch, caplog):
    import exact_words
    import intent_router as ir
    import memory_service
    import turn_forget

    class Svc:
        async def review(self, *a, **k):
            return object()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())

    async def from_turn(svc, uid, t, ts):
        return []

    async def noop(*a, **k):
        return 0
    monkeypatch.setattr(turn_forget, "rows_from_turn", from_turn)
    monkeypatch.setattr(turn_forget, "previous_turn", lambda uid, **k: turn_forget.Turn("wren is 4 not 3", 1.0, "h", 1))
    monkeypatch.setattr(turn_forget, "retract", lambda *a, **k: None)
    monkeypatch.setattr(turn_forget, "mark_forgotten", lambda *a, **k: None)
    monkeypatch.setattr(exact_words, "erase_text", noop)
    monkeypatch.setattr(exact_words, "forget_transcript", noop)
    reply, lines = _both_ways(monkeypatch, caplog, lambda: run(ir._execute_forget_last(A)))
    assert reply.startswith("Done") and lines == ["FORGET_CORRECTION_HELD path=last held=1 live=3"]


def test_the_member_erase_calls_the_instrument_after_its_caches():
    """delete_user needs every store; pinned by source order instead: the instrument runs after the in-process caches go."""
    src = (SVC / "memory_service.py").read_text()
    body = src[src.index("forget_redact.clear_turn_caches(user_id)"):]
    assert 'value_correction.log_held_after_forget("member", user_id)' in body[:2000]
