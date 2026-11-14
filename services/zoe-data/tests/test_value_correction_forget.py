"""Forgotten means forever for the in-process correction memory (``value_correction._REMEMBERED``): it holds the corrected
subject's NAME and both values for 6 h, and until this change no forget path cleared it. Each of the five forget paths now does
(the member erase, "forget <name>", a topic forget, "forget that", the physical row erase), and none of them can be broken by it.

Each path is exercised through its REAL entry point where that runs offline, and pinned by AST with a break-the-fix control.
Invented names only (the repo is public). ``ci_safe``.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import re
from pathlib import Path

import pytest

import value_correction as vc

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
A, B = "demo_vcf_member_a", "demo_vcf_member_b"


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


def _subjects(uid):
    return sorted(c.subject for _t, c in vc._REMEMBERED.get(uid, ()))


def _stale(uid, fact):
    return vc.superseded_by_correction(uid, fact)


# ── the functions ───────────────────────────────────────────────────────────────────────────

def test_the_seed_is_live_before_any_forget():
    """Control: without a forget the remembered correction still marks the old value stale (else every test below is vacuous)."""
    assert _stale(A, "User's daughter Juno is 7 years old") and _stale(B, "User's daughter Juno is 5 years old")


def test_forget_user_drops_every_entry_of_that_member_only():
    assert vc.forget_user(A) == 3
    assert _subjects(A) == [] and _subjects(B) == ["Juno"]
    assert not _stale(A, "User's daughter Juno is 7 years old")


def test_forget_subject_drops_that_name_only():
    assert vc.forget_subject(A, "Juno") == 1
    assert _subjects(A) == ["Wren", "the book club"] and _subjects(B) == ["Juno"]


def test_forget_matching_takes_a_pattern_or_a_callable_and_drops_what_it_cannot_judge():
    assert vc.forget_matching(A, re.compile(r"\bwren\b", re.I)) == 1
    assert vc.forget_matching(A, lambda text: "book" in text) == 1

    def boom(_text):
        raise ValueError("unreadable")
    assert vc.forget_matching(A, boom) == 1                     # over-forgetting is the safe error
    assert _subjects(A) == [] and _subjects(B) == ["Juno"]


def test_forget_in_texts_matches_whole_subject_words():
    assert vc.forget_in_texts(A, ["juniper is lovely"]) == 0       # not a whole-word subject
    assert vc.forget_in_texts(A, ["User's daughter Juno is 8 years old"]) == 1
    assert vc.forget_in_texts(A, ["we moved book club again"]) == 1
    assert _subjects(A) == ["Wren"] and _subjects(B) == ["Juno"]


def test_the_functions_never_raise():
    real = vc._REMEMBERED
    vc._REMEMBERED = None            # a broken store: every clear answers 0 instead of raising into the forget
    try:
        assert vc.forget_user(A) == 0 and vc.forget_matching(A, lambda _t: True) == 0
        assert vc.forget_subject(A, "Juno") == 0 and vc.forget_in_texts(A, ["Juno"]) == 0
    finally:
        vc._REMEMBERED = real


class _Hostile:
    """Neither ``str()`` nor ``strip()`` works on it."""

    def __str__(self):
        raise ValueError("unreadable")

    def strip(self):
        raise ValueError("unreadable")

    def __iter__(self):
        raise ValueError("unreadable")


@pytest.mark.parametrize("bad", [None, 42, _Hostile()])
def test_no_function_raises_on_any_input(bad):
    """The contract is never-raising for EVERY argument (not only via the callers' wrappers)."""
    for call in (lambda: vc.forget_user(bad), lambda: vc.forget_subject(bad, "Juno"), lambda: vc.forget_subject(A, bad),
                 lambda: vc.forget_matching(bad, lambda _t: True), lambda: vc.forget_matching(A, bad),
                 lambda: vc.forget_in_texts(bad, ["Juno"]), lambda: vc.forget_in_texts(A, bad), lambda: vc.forget_in_texts(A, [bad])):
        assert isinstance(call(), int)
    assert _subjects(B) == ["Juno"]                       # whatever was over-forgotten, another member's entry is never touched


def test_an_unreadable_name_or_text_over_forgets_the_member():
    """Over-forgetting is the safe error: a name or text that cannot be read drops every entry of that member, and only of them."""
    assert vc.forget_subject(A, _Hostile()) == 3
    assert _subjects(A) == [] and _subjects(B) == ["Juno"]
    vc._remember(A, vc.Correction("Wren", "4", "3"))
    assert vc.forget_in_texts(A, _Hostile()) == 1 and _subjects(A) == []
    vc._remember(A, vc.Correction("Wren", "4", "3"))
    assert vc.forget_in_texts(A, ["ok", _Hostile()]) == 1 and _subjects(A) == []
    vc._remember(A, vc.Correction("Wren", "4", "3"))
    assert vc.forget_matching(A, None) == 1 and _subjects(A) == []


def test_a_none_name_or_empty_text_forgets_nothing():
    assert vc.forget_subject(A, None) == 0 and vc.forget_subject(A, "  ") == 0 and vc.forget_in_texts(A, None) == 0
    assert vc.forget_user(None) == 0 and vc.forget_user(42) == 0
    assert _subjects(A) == ["Juno", "Wren", "the book club"]


# ── each forget path, through its real entry point ──────────────────────────────────────────

def test_entity_forget_through_the_cascade_clears_that_name(monkeypatch):
    import memory_forget_cascade as cascade

    async def mutes():
        return 0
    res = cascade.CascadeResult()
    # only the in-process steps: no database pass, no file store of the box is touched
    run(cascade._clear(A, cascade._name_re("Juno"), mutes, res, only=frozenset({"restraint_mutes"})))
    assert _subjects(A) == ["Wren", "the book club"] and _subjects(B) == ["Juno"]
    assert not res.failed


def test_the_cascade_retry_with_the_ledger_pattern_clears_it_too():
    import memory_forget_cascade as cascade

    class _Ledger:                       # the LedgerPattern shape: only .search
        def search(self, text):
            return re.search(r"\bwren\b", text, re.I)

    async def mutes():
        return 0
    run(cascade._clear(A, _Ledger(), mutes, cascade.CascadeResult(), only=frozenset({"restraint_mutes"})))
    assert _subjects(A) == ["Juno", "the book club"]


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


def test_topic_forget_through_the_real_path_clears_the_topic(forget_env):
    import forget_topic

    res = run(forget_topic.forget(_Svc(("r1", "The book club meets at the library")), A, "the book club"))
    assert not res.store_down and res.forgotten == 1
    assert _subjects(A) == ["Juno", "Wren"] and _subjects(B) == ["Juno"]


def test_topic_forget_clears_a_subject_its_rows_name(forget_env):
    import forget_topic

    run(forget_topic.forget(_Svc(("r1", "User's daughter Juno started swimming lessons")), A, "swimming lessons"))
    assert _subjects(A) == ["Wren", "the book club"]


def test_a_failing_clear_never_breaks_the_topic_forget(forget_env, monkeypatch):
    import forget_topic

    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(vc, "forget_matching", boom)
    res = run(forget_topic.forget(_Svc(("r1", "The book club meets at the library")), A, "the book club"))
    assert not res.store_down and res.forgotten == 1


def _forget_last(monkeypatch, text):
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
    monkeypatch.setattr(turn_forget, "previous_turn", lambda uid, **k: turn_forget.Turn(text, 1.0, "h", 1))
    monkeypatch.setattr(turn_forget, "retract", lambda *a, **k: None)
    monkeypatch.setattr(turn_forget, "mark_forgotten", lambda *a, **k: None)
    monkeypatch.setattr(exact_words, "erase_text", noop)
    monkeypatch.setattr(exact_words, "forget_transcript", noop)
    return run(ir._execute_forget_last(A))


def test_forget_that_through_the_real_path_clears_that_turns_correction(monkeypatch):
    assert _forget_last(monkeypatch, "juno is 8 not 7").startswith("Done")
    assert _subjects(A) == ["Wren", "the book club"] and _subjects(B) == ["Juno"]


def test_a_failing_clear_never_breaks_forget_that(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(vc, "forget_in_texts", boom)
    assert _forget_last(monkeypatch, "juno is 8 not 7").startswith("Done")


def _erasing_svc(monkeypatch, tmp_path, texts):
    import memory_service

    svc = memory_service.MemoryService(data_dir=str(tmp_path / "palace"))
    monkeypatch.setattr(svc, "_owned_rows_sync", lambda uid, ids: (list(ids), list(texts)))
    monkeypatch.setattr(svc, "_append_audit_sync", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_delete_ids", lambda ids: None)
    monkeypatch.setattr(svc, "_delete_audit_for_rows_sync", lambda ids: 0)

    async def no_physical(_needles):
        return {}
    monkeypatch.setattr(svc, "_physical_erase", no_physical)
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    return svc


def test_the_physical_row_erase_clears_the_subjects_of_the_erased_rows(monkeypatch, tmp_path):
    svc = _erasing_svc(monkeypatch, tmp_path, ["User's son Wren is 4 years old"])
    assert run(svc.erase_rows(A, ["zoe_x_r1"], actor=A))["rows_removed"] == 1
    assert _subjects(A) == ["Juno", "the book club"] and _subjects(B) == ["Juno"]


def test_a_failing_clear_never_breaks_the_row_erase(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(vc, "forget_in_texts", boom)
    svc = _erasing_svc(monkeypatch, tmp_path, ["User's son Wren is 4 years old"])
    assert run(svc.erase_rows(A, ["zoe_x_r1"], actor=A))["rows_removed"] == 1


# ── the call sites, by AST, each with its break-the-fix control ─────────────────────────────

CALL_SITES = (
    ("memory_service.py", "delete_user", "forget_user"),
    ("memory_forget_cascade.py", "_clear", "forget_matching"),
    ("forget_topic.py", "forget", "forget_matching"),
    ("forget_topic.py", "forget", "forget_in_texts"),
    ("intent_router.py", "_execute_forget_last", "forget_in_texts"),
    ("memory_service.py", "erase_rows", "forget_in_texts"),
)


def _calls(source: str, within: str, fn: str) -> bool:
    for scope in ast.walk(ast.parse(source)):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.name == within:
            for n in ast.walk(scope):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == fn
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "value_correction"):
                    return True
    return False


@pytest.mark.parametrize("fname, within, fn", CALL_SITES)
def test_every_forget_path_clears_the_correction_memory(fname, within, fn):
    src = (SVC / fname).read_text(encoding="utf-8")
    assert _calls(src, within, fn), f"{fname}:{within} no longer calls value_correction.{fn}"
    broken = src.replace(f"value_correction.{fn}(", f"value_correction.NOT_{fn}(")
    assert broken != src and not _calls(broken, within, fn)                    # break-the-fix control
    assert not _calls(broken + f"\n# value_correction.{fn}(x)\n", within, fn)   # a comment is not a call
