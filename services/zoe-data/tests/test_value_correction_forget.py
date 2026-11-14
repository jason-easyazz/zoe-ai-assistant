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
    # as ``apply`` remembers them: each with the turn it was said in
    vc._remember(A, vc.Correction("Juno", "8", "7"), "juno is 8 not 7")
    vc._remember(A, vc.Correction("the book club", "the 19th", "the 12th"), "the book club is on the 19th not the 12th")
    vc._remember(A, vc.Correction("Wren", "4", "3"), "wren is 4 not 3")
    vc._remember(B, vc.Correction("Juno", "6", "5"), "juno is 6 not 5")
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


# ── Codex P1 threads: the retracted TURNS, and every subject spelling the matcher accepts ──────────

SAID = "my daughter Wynne is 8, not 7"


def _said_before_any_row(monkeypatch):
    """The correction is remembered from its turn (as ``apply`` does) and that turn is a recent turn - no row exists yet."""
    import turn_forget

    vc._remember(A, vc.Correction("Wynne", "8", "7"), SAID)
    turn_forget.note_user_turn(A, SAID)


def test_apply_remembers_the_turn_a_correction_was_said_in(monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    vc.reset_state()
    run(vc.apply("wynne is 8 not 7", A, None, svc=_Svc(("zoe_r1", "User's daughter Wynne is 7 years old"))))
    (entry,) = [c for _t, c in vc._REMEMBERED[A]]
    assert entry.turn == vc.turn_id_of(A, "wynne is 8 not 7") and entry.turn.startswith("ut-")


def test_a_topic_forget_reaches_a_correction_said_before_any_row(forget_env, monkeypatch):
    """Thread PRRT_kwDOPZpkks63cELD: "forget what I told you about my daughter" retracts the turn; no row exists and the
    correction's own subject / values ("Wynne 8 7") hold no topic word."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    vc._remember(B, vc.Correction("Wynne", "6", "5"), SAID)                 # another member said the same words
    res = run(forget_topic.forget(_Svc(), A, "my daughter"))
    assert not res.store_down and res.turns_retracted == 1
    assert "Wynne" not in _subjects(A) and "Wynne" in _subjects(B)


def test_the_turn_ids_alone_reach_it(forget_env, monkeypatch):
    """Control 1: with the text clear disabled, the retracted turn id still drops it."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    monkeypatch.setattr(vc, "forget_in_texts", lambda *_a, **_k: 0)
    run(forget_topic.forget(_Svc(), A, "my daughter"))
    assert "Wynne" not in _subjects(A)


def test_the_retracted_turn_texts_alone_reach_it(forget_env, monkeypatch):
    """Control 2: with the turn-id clear disabled, the retracted turn's TEXT (passed now, an empty list before) drops it."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    monkeypatch.setattr(vc, "forget_turns", lambda *_a, **_k: 0)
    run(forget_topic.forget(_Svc(), A, "my daughter"))
    assert "Wynne" not in _subjects(A)


def test_with_both_turn_clears_off_the_correction_survives(forget_env, monkeypatch):
    """Control 3: the leak the thread describes is real - with neither turn clear, the correction outlives the forget."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    monkeypatch.setattr(vc, "forget_turns", lambda *_a, **_k: 0)
    real = vc.forget_in_texts
    monkeypatch.setattr(vc, "forget_in_texts", lambda uid, texts: real(uid, [t for t in texts if t != SAID]))
    run(forget_topic.forget(_Svc(), A, "my daughter"))
    assert "Wynne" in _subjects(A)


def test_forget_turns_drops_that_turns_entries_and_any_with_no_turn():
    vc.reset_state()
    vc._remember(A, vc.Correction("Wynne", "8", "7"), "wynne is 8 not 7")
    vc._remember(A, vc.Correction("Tamsin", "4", "3"), "tamsin is 4 not 3")
    vc._remember(A, vc.Correction("Rook", "9", "8"))                         # no recorded turn: cannot be ruled out
    vc._remember(B, vc.Correction("Wynne", "6", "5"), "wynne is 8 not 7")
    assert vc.forget_turns(A, [vc.turn_id_of(A, "Wynne is 8  not 7")]) == 2  # case / whitespace: the same content id
    assert _subjects(A) == ["Tamsin"] and _subjects(B) == ["Wynne"]
    assert vc.forget_turns(A, []) == 0 and _subjects(A) == ["Tamsin"]
    assert vc.forget_turns(A, [None]) == 1 and _subjects(A) == []            # an id that could not be computed: over-forget


def test_forget_that_reaches_the_turns_correction(monkeypatch):
    vc.reset_state()
    vc._remember(A, vc.Correction("Wynne", "8", "7"), SAID)
    vc._remember(B, vc.Correction("Wynne", "6", "5"), SAID)
    monkeypatch.setattr(vc, "forget_in_texts", lambda *_a, **_k: 0)          # the turn id alone
    assert _forget_last(monkeypatch, SAID).startswith("Done")
    assert _subjects(A) == [] and _subjects(B) == ["Wynne"]


@pytest.mark.parametrize("subject, erased", [
    ("Asha's swimming", "Asha swimming is on Wednesday"),        # thread PRRT_kwDOPZpkks63cELH, verbatim shape
    ("Asha's swimming", "ASHA'S SWIMMING is on Wednesday"),
    ("Asha's swimming", "Asha\u2019s   swimming is on Wednesday"),
    ("Asha's swimming", "User's niece Asha has swimming on Wednesday"),     # the owner alone: when in doubt, drop
    ("ashas swimming", "Asha swimming is on Wednesday"),          # a lazily typed possessive
    ("Ana-Lise", "Ana Lise is 8"),                                # hyphen style
    ("Ana Lise", "ana-lise is 8"),
    ("José", "Jose is 8"),                                        # accent fold (the people graph's normaliser)
    ("the book club", "Book  Club moved to the 19th"),
    ("my dad", "User's dad is 76"),
])
def test_forget_in_texts_matches_every_spelling_the_matcher_accepts(subject, erased):
    vc.reset_state()
    vc._remember(A, vc.Correction(subject, "x1", "x2"))
    vc._remember(B, vc.Correction(subject, "x1", "x2"))
    assert vc.forget_in_texts(A, [erased]) == 1
    assert _subjects(A) == [] and _subjects(B) == [subject]           # another member's identical correction survives


def test_the_literal_comparison_was_the_leak():
    """Control: the old literal key match misses the thread's example, so the variant test above is not vacuous."""
    blob = "asha swimming is on wednesday"
    key = vc._subject_key("Asha's swimming")
    assert re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", blob) is None


def test_an_unrelated_text_drops_nothing():
    vc.reset_state()
    vc._remember(A, vc.Correction("Asha's swimming", "wednesday", "thursday"))
    vc._remember(A, vc.Correction("Wynne", "8", "7"))
    assert vc.forget_in_texts(A, ["Rook went to the dentist"]) == 0
    assert _subjects(A) == ["Asha's swimming", "Wynne"]


# ── Codex P1 (class): every turn-keyed clear runs BEFORE any destructive step ─────────────────────

def _one_shot_failure(monkeypatch, step):
    """Make one destructive step of the topic forget fail ONCE (then work), as a store blip would."""
    import conversation_notes
    import forget_topic

    left = {"n": 1}

    def once(real):
        async def flaky(*a, **k):
            if left["n"] > 0:
                left["n"] -= 1
                raise ConnectionError("pool lost")
            return await real(*a, **k)
        return flaky
    if step == "words":
        monkeypatch.setattr(forget_topic, "_erase_words", once(forget_topic._erase_words))
    elif step == "notes":
        monkeypatch.setattr(conversation_notes, "erase_topic", once(conversation_notes.erase_topic))
    elif step == "archive":
        monkeypatch.setattr(_Svc, "review", once(_Svc.review))
    return left


@pytest.mark.parametrize("step", ["words", "notes", "archive"])
def test_a_failed_destructive_step_then_a_healthy_retry_leaves_nothing(forget_env, monkeypatch, step):
    """Thread PRRT_kwDOPZpkks63cwpI, as a class: a one-shot failure in EACH later step in turn. The correction was said before
    any row existed; after the failed call and a healthy retry, nothing of the member's correction remains, and member B's
    identical one is untouched."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    vc._remember(B, vc.Correction("Wynne", "6", "5"), SAID)
    left = _one_shot_failure(monkeypatch, step)
    svc = _Svc(("zoe_r9", "User's daughter has a piano lesson"))
    run(forget_topic.forget(svc, A, "my daughter"))
    assert left["n"] == 0                                           # the injected failure really happened
    assert "Wynne" not in _subjects(A)                              # already gone after the FAILED call: cleared first
    run(forget_topic.forget(svc, A, "my daughter"))                 # the healthy retry
    assert "Wynne" not in _subjects(A) and "Wynne" in _subjects(B)


def test_the_old_ordering_leaks_on_a_notes_failure(forget_env, monkeypatch):
    """Negative control: the order before this fix - the words erased, the notes erase failing, so the in-process clear (after
    it) never ran - and then a healthy retry that finds no turn left to match: the correction outlives the forget."""
    import forget_topic

    _said_before_any_row(monkeypatch)
    real = {"m": vc.forget_matching, "t": vc.forget_turns, "x": vc.forget_in_texts}
    skipped = {"on": True}
    monkeypatch.setattr(vc, "forget_matching", lambda *a: 0 if skipped["on"] else real["m"](*a))
    monkeypatch.setattr(vc, "forget_turns", lambda *a: 0 if skipped["on"] else real["t"](*a))
    monkeypatch.setattr(vc, "forget_in_texts", lambda *a: 0 if skipped["on"] else real["x"](*a))
    _one_shot_failure(monkeypatch, "notes")
    run(forget_topic.forget(_Svc(), A, "my daughter"))              # words erased, notes failed, clear skipped (old order)
    skipped["on"] = False
    run(forget_topic.forget(_Svc(), A, "my daughter"))              # the healthy retry: no retracted turn names it any more
    assert "Wynne" in _subjects(A)


# ── Codex P1 PRRT_kwDOPZpkks63dHC7: the entity path shares the subject normaliser ─────────────────

@pytest.mark.parametrize("subject", ["ashas swimming", "Asha's swimming", "asha's  Swimming", "ashas", "Asha"])
def test_an_entity_forget_reaches_every_subject_form(subject):
    """The thread's example through the REAL entity path (the cascade's ``_clear`` with the ledger's name pattern for "Asha"):
    the apostrophe-free possessive "ashas swimming" names Asha's row, so forgetting Asha drops it; member B keeps theirs."""
    import memory_forget_cascade as cascade

    vc.reset_state()
    vc._remember(A, vc.Correction(subject, "wednesday", "thursday"), f"{subject} is on wednesday not thursday")
    vc._remember(A, vc.Correction("Wren", "4", "3"), "wren is 4 not 3")
    vc._remember(B, vc.Correction(subject, "wednesday", "thursday"), f"{subject} is on wednesday not thursday")

    async def mutes():
        return 0
    run(cascade._clear(A, cascade._name_re("Asha"), mutes, cascade.CascadeResult(), only=frozenset({"restraint_mutes"})))
    assert _subjects(A) == ["Wren"] and _subjects(B) == [subject]


def test_the_literal_subject_was_the_entity_leak():
    """Negative control: the name pattern alone, tested against the literal subject only (the old check), misses the thread's
    example - so the test above measures the shared forms, not luck."""
    import memory_forget_cascade as cascade

    rx = cascade._name_re("Asha")
    assert not rx.search(vc._entry_text(vc.Correction("ashas swimming", "wednesday", "thursday")))
    assert any(rx.search(f) for f in vc.subject_forms("ashas swimming"))


def test_forget_subject_and_the_ledger_pattern_use_the_same_forms():
    class _Ledger:                               # the LedgerPattern shape: .search only, no name
        def search(self, text):
            return re.search(r"\basha\b", text, re.I)
    vc.reset_state()
    vc._remember(A, vc.Correction("ashas swimming", "wednesday", "thursday"), "ashas swimming is on wednesday not thursday")
    vc._remember(A, vc.Correction("kais", "21", "22"), "kais 21 not 22")
    assert vc.forget_matching(A, _Ledger()) == 1
    assert vc.forget_subject(A, "Kai") == 1 and _subjects(A) == []


def test_an_unrelated_name_drops_nothing():
    import memory_forget_cascade as cascade

    vc.reset_state()
    vc._remember(A, vc.Correction("ashas swimming", "wednesday", "thursday"), "ashas swimming is on wednesday not thursday")
    assert vc.forget_matching(A, cascade._name_re("Ash")) == 0 and vc.forget_matching(A, cascade._name_re("Sam")) == 0
    assert _subjects(A) == ["ashas swimming"]


# ── the call sites, by AST, each with its break-the-fix control ─────────────────────────────

CALL_SITES = (
    ("memory_service.py", "delete_user", "forget_user"),
    ("memory_forget_cascade.py", "_clear", "forget_matching"),
    ("forget_topic.py", "forget", "forget_matching"),
    ("forget_topic.py", "forget", "forget_in_texts"),
    ("intent_router.py", "_execute_forget_last", "forget_in_texts"),
    ("intent_router.py", "_execute_forget_last", "forget_turns"),
    ("forget_topic.py", "forget", "forget_turns"),
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
