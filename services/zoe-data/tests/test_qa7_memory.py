"""QA round 7 memory fixes (2026-11-09): a same-session forget holds, spoken corrections stick (a typo'd cue, "on friday", a lazily typed
name, a possessive subject), a speaker's own facts are not a contact, grandchildren and nieces are not pets, and "dont forget to add X" is
not a memory. Synthetic names only (Asha, Kai, Dev, Priya, Max)."""
import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fact_subject  # noqa: E402
import forget_session  # noqa: E402
import forget_topic  # noqa: E402
import intent_router  # noqa: E402
import kin_pet_guard  # noqa: E402
import memory_extractor  # noqa: E402
import person_extractor as pe  # noqa: E402
import value_correction as vc  # noqa: E402
import zoe_flue_client as zfc  # noqa: E402
from test_qa4_memory_fixes import svc  # noqa: E402,F401  (the in-memory MemoryService fixture)

pytestmark = pytest.mark.ci_safe

UID = "demo_qa4_00000001"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_epochs(tmp_path, monkeypatch):
    monkeypatch.setattr(forget_session, "_PATH", tmp_path / "forget_session_epochs.json")
    monkeypatch.setattr(forget_session, "_LOADED", False)
    forget_session._EPOCH.clear()
    yield
    forget_session._EPOCH.clear()


# ── 1. same-session forget ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,topic", [
    ("forget the thing about asha and mushrooms", "asha and mushrooms"),
    ("forget the bit about my brother", "my brother"),
    ("forget the thing about zara", "zara"),
    ("ok forget that thing about the shops", "the shops"),
    ("please forget all the stuff I said about my mum's heart", "my mum's heart"),
])
def test_the_words_people_use_for_a_topic_forget(text, topic):
    assert forget_topic.parse(text) == topic
    intent = intent_router.detect_intent(text, log_miss=False)
    assert intent.name == "memory_forget_entity" and intent.slots == {"topic": topic}


@pytest.mark.parametrize("text", [
    "forget about my day", "forget it", "forget the bit about", "forget the thing", "don't forget the thing about the bins",
    "dont forget the bit about mum's heart",
])
def test_negative_controls_a_dismissal_or_a_teach_is_never_a_forget(text):
    assert forget_topic.parse(text) is None


MUSH = "Asha dislikes mushrooms"
MUSH2 = "asha hates mushrooms, she makes a face like shes been betrayed"
AGE = "Asha is 5"


def test_a_topic_forget_removes_every_copy_and_rotates_the_brain_session(svc):
    for t in (MUSH, MUSH2, AGE):
        run(svc.ingest(t, user_id=UID, source="brain_tool"))
    reply = run(intent_router.execute_intent(
        intent_router.detect_intent("forget the thing about asha and mushrooms", log_miss=False), UID))
    assert reply.startswith("Done")
    live = sorted(r.text for r in run(svc.list_by_status(user_id=UID, status="approved")))
    assert live == [AGE]                                  # both copies of the mushroom fact went, the age stayed
    assert forget_session.sidecar_session(UID, "bar-a11") != "bar-a11"      # the brain's own history of the chat is not reused


def test_negative_control_without_a_forget_the_session_is_unchanged():
    assert forget_session.sidecar_session(UID, "bar-a11") == "bar-a11"
    forget_session.note_forget("demo_other_00000002")
    assert forget_session.sidecar_session(UID, "bar-a11") == "bar-a11"       # another member's forget never moves this one's


def test_the_rotation_survives_a_restart_and_is_per_member(monkeypatch):
    forget_session.note_forget(UID)
    rotated = forget_session.sidecar_session(UID, "bar-a11")
    assert rotated != "bar-a11"
    # a zoe-data restart: the in-memory state is gone, the file is not - the old sidecar history is never addressed again
    forget_session._EPOCH.clear()
    monkeypatch.setattr(forget_session, "_LOADED", False)
    assert forget_session.sidecar_session(UID, "bar-a11") == rotated
    assert forget_session.sidecar_session("demo_other_00000002", "bar-a11") == "bar-a11"


def test_many_members_never_evict_one_anothers_rotation():
    for i in range(400):
        forget_session.note_forget(f"demo_m_{i:08d}")
    assert forget_session.sidecar_session("demo_m_00000000", "s") != "s"      # the oldest of 400 still rotated (no cap)


def test_two_forgets_rotate_twice():
    forget_session.note_forget(UID)
    first = forget_session.sidecar_session(UID, "s")
    forget_session.note_forget(UID)
    assert forget_session.sidecar_session(UID, "s") not in ("s", first)


def test_the_sidecar_is_addressed_with_the_rotated_session(monkeypatch):
    monkeypatch.setitem(zfc._SESSION_UID, "bar-a11", UID)
    before = zfc._endpoint("bar-a11", stream=True)
    assert before.endswith("/bar-a11") or "bar-a11" in before and "~f" not in before
    forget_session.note_forget(UID)
    after = zfc._endpoint("bar-a11", stream=True)
    assert "~f" in after and after != before
    assert "~f" not in zfc._endpoint("bar-unknown-session", stream=True)       # a session no member is known for is untouched


def test_every_forget_intent_rotates_the_session(svc):
    run(intent_router.execute_intent(intent_router.Intent("memory_forget_last", {}), UID))
    assert forget_session.sidecar_session(UID, "s") != "s"
    forget_session._EPOCH.clear()
    run(intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "Dana"}), UID))
    assert forget_session.sidecar_session(UID, "s") != "s"


# ── 2. corrections ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_typod_cue_and_an_on_day_apply():
    c = vc.statement("actualy my exam is on friday not thursday")
    assert c is not None and (c.new, c.old) == ("friday", "thursday")
    assert vc.rewrite("User has an economics exam on Thursday", c) == "User has an economics exam on Friday"   # not "on on Friday"
    assert vc.rewrite("User has a dentist visit on Thursday", c) is None


def test_a_lazily_typed_name_and_two_numbers_is_an_age_correction():
    c = vc.statement("also kais 21 not 22")
    assert c is not None and c.subject == "kais" and (c.new, c.old) == ("21", "22")      # kept as typed: the row decides
    assert vc.rewrite("Kai: 22", c) is None                              # the exact typed name first: nobody called "Kais" -> no rewrite yet
    assert vc.rewrite("Kai: 22", c, lazy_s=True) == "Kai: 21"            # ... then Kai's row (the compact "Name: value" shape)
    assert vc.rewrite("User's boyfriend Kai is 22", c, lazy_s=True) == "User's boyfriend Kai is 21"
    assert vc.rewrite("Zara: 22", c, lazy_s=True) is None
    assert vc.rewrite("Kai has a train at 10:22", c, lazy_s=True) is None             # a time is not an age
    assert vc.rewrite("Kai: 10:22", c, lazy_s=True) is None
    assert vc.rewrite("Kai: 22 years old", c, lazy_s=True) == "Kai: 21 years old"
    c2 = vc.statement("also kai's 21 not 22")
    assert c2 is not None and c2.subject == "kai"
    # a name that really ends in s is not shortened: it rewrites its own row, and no invented "Chri" row is touched
    c3 = vc.statement("also chris 21 not 22")
    assert vc.rewrite("Chris: 22", c3) == "Chris: 21"
    assert vc.rewrite("Chri: 22", c3, lazy_s=True) == "Chri: 21"          # only when nobody is called Chris (apply() decides)


@pytest.mark.parametrize("text", ["page 4 not 5", "price 20 not 25", "friday 5 not 6", "tea not coffee", "thats 21 not 22 degrees"])
def test_negative_controls_a_bare_pair_with_no_name_is_no_correction(text):
    assert vc.statement(text) is None


def test_a_possessive_subject_keeps_its_owner():
    c = vc.statement("actually ashas swimming is on wednesday not thursday")
    assert c is not None and vc.owner_noun(c.subject) == ("Ashas", "swimming")      # as typed; the row decides Asha or Ashas
    assert vc.rewrite("Asha's swimming is on Thursday", c) is None                    # exact word first ("Ashas" has no row) ...
    assert vc.rewrite("Asha's swimming is on Thursday", c, lazy_s=True) == "Asha's swimming is on Wednesday"   # ... then the lazy possessive
    assert vc.rewrite("Ashanti's swimming is on Thursday", c, lazy_s=True) is None    # a longer name is not Asha
    c2 = vc.statement("actually chris swimming is on wednesday not thursday")
    assert vc.rewrite("Chris swimming is on Thursday", c2) == "Chris swimming is on Wednesday"
    assert vc.owner_noun(vc.statement("actually asha's swimming is on wednesday not thursday").subject) == ("Asha", "swimming")
    assert vc.rewrite("User's swimming is on Thursday", c, lazy_s=True) is None         # someone else's swimming is not Asha's
    assert vc.rewrite("Asha's dance is on Thursday", c, lazy_s=True) is None
    # the owner must HAVE the noun, not merely appear in the row
    assert vc.rewrite("Dev's swimming with Asha is on Thursday", c, lazy_s=True) is None
    assert vc.rewrite("Dev takes Asha to swimming on Thursday", c, lazy_s=True) is None
    assert vc.rewrite("Asha swimming is on Thursday", c, lazy_s=True) == "Asha swimming is on Wednesday"


class _Row:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": UID}


class _Svc:
    def __init__(self, *texts):
        self.rows = [_Row(f"r{i}", t) for i, t in enumerate(texts)]
        self.edits = []

    async def list_by_status(self, **_k):
        return [r for r in self.rows]

    async def review(self, rid, decision=None, edits=None, **_k):
        self.edits.append((rid, edits))
        return object()


def _apply(svc, text, monkeypatch):
    import correction_apply

    async def opted_in(_u):
        return False

    import user_prefs

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    return run(vc.apply(text, UID, None, svc=svc))


def test_apply_prefers_the_exact_name_and_only_then_the_lazy_one(monkeypatch):
    # Carlo AND Carlos are known people: "carlos 21 not 22" is Carlos, never Carlo
    svc = _Svc("Carlo is 22", "Carlos is 22")
    assert _apply(svc, "also carlos 21 not 22", monkeypatch) == 1
    assert svc.edits == [("r1", "Carlos is 21")]
    # only Kai exists: "kais 21 not 22" is Kai's (the apostrophe was not typed)
    svc = _Svc("Kai is 22", "Zara is 22")
    assert _apply(svc, "also kais 21 not 22", monkeypatch) == 1
    assert svc.edits == [("r0", "Kai is 21")]
    # a Carlos row exists but already says 21: "carlos 21 not 22" must not fall back to Carlo's 22
    svc = _Svc("Carlo is 22", "Carlos is 21")
    assert _apply(svc, "also carlos 21 not 22", monkeypatch) == 0 and svc.edits == []
    # Carlos exists (an age row) and Carlo has the swimming row: "carlos swimming" must not rewrite Carlo's
    svc = _Svc("Carlos is 22", "Carlo's swimming is on Thursday")
    assert _apply(svc, "actually carlos swimming is on wednesday not thursday", monkeypatch) == 0 and svc.edits == []
    # negative control: with no Carlos row at all the lazy fallback still reaches Carlo
    svc = _Svc("Carlo's swimming is on Thursday")
    assert _apply(svc, "actually carlos swimming is on wednesday not thursday", monkeypatch) == 1
    # an EXPLICIT possessive never shortens: "Carlos's swimming" is Carlos's, even when only Carlo has a swimming row
    svc = _Svc("Carlo's swimming is on Thursday")
    assert _apply(svc, "actually Carlos's swimming is on wednesday not thursday", monkeypatch) == 0 and svc.edits == []
    # ... and a Carlos row about another attribute blocks the lazy form too
    svc = _Svc("Carlos's birthday is in May", "Carlo's swimming is on Thursday")
    assert _apply(svc, "actually carlos swimming is on wednesday not thursday", monkeypatch) == 0 and svc.edits == []
    # only the bare lazy form shortens: an apostrophized or a plain "is" statement naming Carlos is never Carlo's
    svc = _Svc("Carlo is 22")
    assert _apply(svc, "also Carlos's 21 not 22", monkeypatch) == 0 and svc.edits == []
    svc = _Svc("Carlo is 22")
    assert _apply(svc, "Carlos is 21 not 22", monkeypatch) == 0 and svc.edits == []
    # nobody matches either way: nothing changes
    svc = _Svc("Zara is 22")
    assert _apply(svc, "also kais 21 not 22", monkeypatch) == 0 and svc.edits == []


def test_the_digest_row_that_lost_its_possessor_gets_it_back():
    msg = "actually ashas swimming is on wednesday not thursday"
    known = frozenset({"asha", "dev"})                       # the member's people
    assert fact_subject.attribute("User's swimming is on Wednesday", msg, known) == ("rewrite", "Asha's swimming is on Wednesday")
    assert fact_subject.attribute("User's swimming is on Wednesday", "actually asha's swimming is on wednesday")[1] \
        == "Asha's swimming is on Wednesday"                  # an apostrophe needs no list of names
    assert fact_subject.lazy_owner_candidate("User's swimming is on Wednesday", msg)
    # a name that ends in s is that name, never a shortened invention; an unknown lazy owner leaves the fact alone
    assert fact_subject.attribute("User's swimming is on Wednesday", "chris swimming is on wednesday", frozenset({"chris"})) \
        == ("rewrite", "Chris's swimming is on Wednesday")
    assert fact_subject.attribute("User's swimming is on Wednesday", "chris swimming is on wednesday", known)[0] == "keep"
    assert fact_subject.attribute("User's swimming is on Wednesday", msg)[0] == "keep"
    # two people have the activity in the turn: the clause that carries the fact's own words decides
    two = "Asha's swimming is on Wednesday. Dev's swimming is on Thursday"
    assert fact_subject.attribute("User's swimming is on Wednesday", two)[1] == "Asha's swimming is on Wednesday"
    assert fact_subject.attribute("User's swimming is on Thursday", two)[1] == "Dev's swimming is on Thursday"
    assert fact_subject.attribute("User's swimming is on Friday", two)[0] == "keep"           # no clause backs it: never guessed
    assert fact_subject.attribute("User's swimming is on Wednesday", "Asha's swimming is on Wednesday. Dev's swimming is on Wednesday")[0] == "keep"
    # Carlo AND Carlos are known: "carlos swimming" could be either, so it is never guessed
    assert fact_subject.attribute("User's swimming is on Wednesday", "carlos swimming is on wednesday", frozenset({"carlo", "carlos"}))[0] == "keep"
    assert fact_subject.attribute("User's swimming is on Wednesday", "carlos swimming is on wednesday", frozenset({"carlos"})) \
        == ("rewrite", "Carlos's swimming is on Wednesday")
    # the same rule for the digest's subject: an EXPLICIT possessive is that name, never shortened to a known Carlo; the lazy form shortens only with no Carlos known
    assert fact_subject.attribute("User's swimming is on Wednesday", "actually Carlos's swimming is on wednesday", frozenset({"carlo"})) \
        == ("rewrite", "Carlos's swimming is on Wednesday")
    assert fact_subject.attribute("User's swimming is on Wednesday", "carlos swimming is on wednesday", frozenset({"carlo"})) \
        == ("rewrite", "Carlo's swimming is on Wednesday")
    # negative controls: the user's own, or a relation word, or a turn that names no owner
    assert fact_subject.attribute("User's swimming is on Wednesday", "my swimming is on wednesday")[0] == "keep"
    assert fact_subject.attribute("User's swimming is on Wednesday", "swimming is on wednesday")[0] == "keep"


def _stub_person_writer(monkeypatch, calls, people=None):
    people = people or {}
    async def no(*_a, **_k):
        return False

    async def ensure(_db=None):
        return object(), False

    async def resolve(name, *_a, **_k):
        return people.get(name.lower())

    async def ingest(text, user_id, person_name, entity_id, **_k):
        calls.append((person_name, text, entity_id))
        return "mem-1"

    monkeypatch.setattr(pe, "_opted_out", no)
    monkeypatch.setattr(pe, "_names_forgotten", no)
    monkeypatch.setattr(pe, "_ensure_db", ensure)
    monkeypatch.setattr(pe, "_resolve_person", resolve)
    monkeypatch.setattr(pe, "_ingest_to_mempalace", ingest)

    async def own(text, user_id, **_k):
        calls.append(("<own>", text, None))
        return "mem-own"

    monkeypatch.setattr(pe, "_ingest_own_fact", own)


def test_the_stray_s_of_a_correction_is_not_a_person(monkeypatch):
    calls = []
    _stub_person_writer(monkeypatch, calls)
    import own_name

    async def nobody(_u):
        return frozenset()

    monkeypatch.setattr(own_name, "names", nobody)
    kai = types.SimpleNamespace(person_id="p-kai", ambiguous=False)
    _stub_person_writer(monkeypatch, calls, {"kai": kai})
    for fn in ("_write_activity", "_post_write_hooks"):
        async def noop(*_a, **_k):
            return None

        monkeypatch.setattr(pe, fn, noop)
    assert run(pe.apply_person_fact("Kais", "fact", "21", user_id=UID, source="conversation",
                                    source_excerpt="also kais 21 not 22"))
    assert calls == [("Kai", "Kai: 21", "p-kai")]               # the row is Kai's, not a person called Kais
    calls.clear()
    run(pe.apply_person_fact("Kais", "fact", "21", user_id=UID, source="conversation", source_excerpt="kais is my mate"))
    assert calls[0][0] == "Kais"                                  # negative control: no correction turn, the name stands
    calls.clear()
    run(pe.apply_person_fact("Chris", "fact", "21", user_id=UID, source="conversation", source_excerpt="also chris 21 not 22"))
    assert calls[0][0] == "Chris"                                 # negative control: nobody called Chri exists, a real name is never shortened


def _no_activity_writes(monkeypatch):
    for fn in ("_write_activity", "_post_write_hooks"):
        async def noop(*_a, **_k):
            return None

        monkeypatch.setattr(pe, fn, noop)


def _nobody_is_the_speaker(monkeypatch):
    import own_name

    async def nobody(_u):
        return frozenset()

    monkeypatch.setattr(own_name, "names", nobody)


def test_an_explicit_name_is_never_shortened_to_a_known_person(monkeypatch):
    """The person writer shares value_correction.may_shorten: only the lazy bare form shortens, and never when the name has its own record."""
    calls = []
    _nobody_is_the_speaker(monkeypatch)
    _no_activity_writes(monkeypatch)
    carlo = types.SimpleNamespace(person_id="p-carlo", ambiguous=False)
    carlos = types.SimpleNamespace(person_id="p-carlos", ambiguous=False)
    _stub_person_writer(monkeypatch, calls, {"carlo": carlo})
    for turn in ("Carlos is 21 not 22", "actually Carlos's age is 21 not 22"):
        calls.clear()
        run(pe.apply_person_fact("Carlos", "fact", "21", user_id=UID, source="conversation", source_excerpt=turn))
        assert calls and all(c[0] == "Carlos" and c[2] != "p-carlo" for c in calls), turn           # never filed on Carlo
    calls.clear()
    run(pe.apply_person_fact("Carlos", "fact", "21", user_id=UID, source="conversation", source_excerpt="also carlos 21 not 22"))
    assert calls == [("Carlo", "Carlo: 21", "p-carlo")]                                             # control: the lazy bare form, nobody called Carlos, IS Carlo's
    _stub_person_writer(monkeypatch, calls, {"carlo": carlo, "carlos": carlos})
    calls.clear()
    run(pe.apply_person_fact("Carlos", "fact", "21", user_id=UID, source="conversation", source_excerpt="also carlos 21 not 22"))
    assert calls and all(c[2] == "p-carlos" for c in calls)                                         # a Carlos record exists: the lazy form is Carlos too


def test_an_ambiguous_name_is_held_never_shortened(monkeypatch):
    """Two people called Carlos (person_id None, ambiguous) plus a Carlo: the lazy 'carlos 21 not 22' reaches the ambiguity guard, not Carlo."""
    calls, held = [], []
    _nobody_is_the_speaker(monkeypatch)
    _no_activity_writes(monkeypatch)
    carlo = types.SimpleNamespace(person_id="p-carlo", ambiguous=False)
    two = types.SimpleNamespace(person_id=None, ambiguous=True, tier="exact", matches=[object(), object()])
    _stub_person_writer(monkeypatch, calls, {"carlo": carlo, "carlos": two})

    async def hold(*a, **k):
        held.append(a[1] if len(a) > 1 else "")
    monkeypatch.setattr(pe, "_hold_fact_belief", hold)
    run(pe.apply_person_fact("Carlos", "fact", "21", user_id=UID, source="conversation", source_excerpt="also carlos 21 not 22"))
    assert not any(c[2] == "p-carlo" for c in calls) and held                                     # held as ambiguous, nothing filed on Carlo
    assert not vc.may_shorten(vc.statement("also carlos 21 not 22"), False, True)                  # the shared rule
    assert vc.may_shorten(vc.statement("also carlos 21 not 22"), False, False)                     # control: positively no match -> still shortens


_EXPLICIT_FORMS = ["actually Carlos's swimming is on Wednesday not Thursday", "Carlos is 21 not 22", "actually Carlos's age is 21 not 22"]


@pytest.mark.parametrize("turn", _EXPLICIT_FORMS)
def test_table_an_explicit_carlos_never_writes_on_carlo_anywhere(monkeypatch, turn):
    """ONE table over the explicit forms, through every writer: the shared rule, the correction apply (rows) and the person writer."""
    corr = vc.statement(turn)
    assert corr is not None
    assert vc.may_shorten(corr, False) is False                                                     # the shared rule itself
    svc = _Svc("Carlo is 22", "Carlo's swimming is on Thursday")
    assert _apply(svc, turn, monkeypatch) == 0 and svc.edits == []                                  # the row writer
    calls = []
    _nobody_is_the_speaker(monkeypatch)
    _no_activity_writes(monkeypatch)
    _stub_person_writer(monkeypatch, calls, {"carlo": types.SimpleNamespace(person_id="p-carlo", ambiguous=False)})
    run(pe.apply_person_fact("Carlos", "fact", corr.new, user_id=UID, source="conversation", source_excerpt=turn))
    assert calls and all(c[2] != "p-carlo" for c in calls)                                         # the person writer (it still files the fact, on Carlos)


def test_the_shared_rule_for_the_lazy_forms():
    lazy = vc.statement("also carlos 21 not 22")
    assert lazy.lazy and vc.may_shorten(lazy, False) and not vc.may_shorten(lazy, True)             # lazy, and only with no exact record
    owner_lazy = vc.statement("actually carlos swimming is on wednesday not thursday")
    assert vc.may_shorten(owner_lazy, False) and not vc.may_shorten(owner_lazy, True)
    assert vc.may_shorten(vc.statement("also chris 21 not 22"), False)                               # a name that could carry a stray s may shorten (the caller still needs a KNOWN shorter one)
    assert not vc.may_shorten(vc.statement("also dev 21 not 22"), False)                            # ... one that cannot, never


# ── 3. the speaker's own facts ───────────────────────────────────────────────────────────────────────────────────────

def test_an_own_fact_is_ingested_with_no_person_linkage(monkeypatch):
    import memory_service

    seen = {}

    class Svc:
        async def ingest(self, text, **kw):
            seen.update(kw, text=text)
            return types.SimpleNamespace(id="m1", metadata={})

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())
    assert run(pe._ingest_own_fact("User is a night shift nurse", UID, source="conversation")) == "m1"
    assert seen["text"] == "User is a night shift nurse" and seen["memory_type"] == "fact"
    assert "entity_type" not in seen and "entity_id" not in seen                    # not a person_pending slug for "Priya"
    assert "person" not in seen["tags"] and not any(t.startswith("slug:") for t in seen["tags"])


def test_the_speakers_own_facts_are_the_users_not_a_contact(monkeypatch):
    calls = []
    _stub_person_writer(monkeypatch, calls)
    import own_name

    async def mine(_u):
        return frozenset({"priya"})

    monkeypatch.setattr(own_name, "names", mine)
    intro = "hi zoe, im priya. night shift nurse, running on fumes"
    assert run(pe.apply_person_fact("Priya", "fact", "36", user_id=UID, source="conversation", source_excerpt=intro))
    assert run(pe.apply_person_fact("Priya", "fact", "Priya is a night shift nurse", user_id=UID, source="conversation",
                                    source_excerpt=intro))
    assert [c[1] for c in calls] == ["User (Priya): 36", "User is a night shift nurse"]
    assert all(c[2] is None for c in calls)                       # no person node
    calls.clear()
    # negative controls: the name list alone is not identity - a colleague with the speaker's first name, or no introduction in the text
    run(pe.apply_person_fact("Priya", "fact", "Priya is a teacher", user_id=UID, source="conversation",
                             source_excerpt="my colleague Priya is a teacher"))
    run(pe.apply_person_fact("Priya", "fact", "36", user_id=UID, source="conversation"))
    assert [c[1] for c in calls] == ["Priya is a teacher", "Priya: 36"] and all(c[0] == "Priya" for c in calls)
    calls.clear()
    assert run(pe.apply_person_fact("Asha", "fact", "5", user_id=UID, source="conversation"))
    assert calls == [("Asha", "Asha: 5", None)]                   # negative control: another person is still "Name: value"


# ── 4. child vs pet ──────────────────────────────────────────────────────────────────────────────────────────────────

GREG = "wife lorraine is 60 and has type 2 diabetes, takes metformin. grandson max is 4 and our dog is rusty, a kelpie"


@pytest.mark.parametrize("fact,fixed", [
    ("User's pet is named Max", "User's grandson is named Max"),
    ("User's pet Max is at kindy on Tuesday", "User's grandson Max is at kindy on Tuesday"),
])
def test_a_grandson_is_never_filed_as_a_pet(fact, fixed):
    assert kin_pet_guard.repair(fact, "pet", GREG) == (fixed, "relationship")


@pytest.mark.parametrize("turn,fact,fixed", [
    ("my niece ava is 7 and my cat called mo", "User's pet Ava is 7", "User's niece Ava is 7"),
    ("my nephew sam is 9, and our dog called rex", "User's pet is named Sam", "User's nephew is named Sam"),
    ("my granddaughter eve is 2 and a cat called mo", "User's pet Eve is 2", "User's granddaughter Eve is 2"),
])
def test_niece_nephew_granddaughter_count_as_children(turn, fact, fixed):
    assert kin_pet_guard.repair(fact, "pet", turn) == (fixed, "relationship")


def test_negative_controls_the_real_pet_stays_a_pet_and_plain_kids_keep_the_old_wording():
    assert kin_pet_guard.repair("User's pet is named Rusty", "pet", GREG) is None
    assert kin_pet_guard.repair("User's pet Ava is 8", "pet", "kids Ava (8) and Ben (4), and a fat cat called Mo") \
        == ("User's child Ava is 8", "relationship")


def test_the_voice_child_gate_knows_nieces_and_grandchildren():
    import person_age

    for rel in ("niece", "nephew", "grandchildren", "grandkid"):
        assert person_age.person_may_be_child({"relationship": rel})
    assert not person_age.person_may_be_child({"relationship": "uncle"})


# ── 5. "dont forget to add X" is not a memory ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "dont forget to add renew my rego", "dont forget to add screws", "don't forget to put milk on the list",
    "dont forget to remind me about the dentist", "dont forget to schedule the plumber",
])
def test_a_list_or_calendar_ask_makes_no_memory_row(text):
    assert memory_extractor._mine_templates(text, text, set()) == []


@pytest.mark.parametrize("text,row", [
    ("dont forget that mum is allergic to nuts", "Important note: mum is allergic to nuts"),
    ("don't forget my mum's birthday is in may", "Important note: my mum's birthday is in may"),
])
def test_negative_controls_a_real_note_is_still_kept(text, row):
    assert [c.text for c in memory_extractor._mine_templates(text, text, set())] == [row]


def test_exact_name_never_consumes_another_names_final_s():
    from value_correction import Correction, rewrite

    corr = Correction("Carlo", "21", "22", True)
    assert rewrite("Carlo: 22", corr) == "Carlo: 21"
    assert rewrite("Carlos: 22", corr) is None


def test_compact_age_row_ignores_a_number_that_starts_another_attribute():
    from value_correction import Correction, rewrite

    corr = Correction("Kai", "21", "22", True)
    assert rewrite("Kai: 22 Willow Street", corr) is None
    assert rewrite("Kai: 22", corr) == "Kai: 21"
    assert rewrite("Kai: 22 years old", corr) == "Kai: 21 years old"
    assert rewrite("Kai: 22 years old likes trains", corr) == "Kai: 21 years old likes trains"
    assert rewrite("Kai: 22, likes trains", corr) == "Kai: 21, likes trains"


def test_an_apostrophized_owner_is_matched_exactly_never_expanded_to_a_longer_name():
    c = vc.statement("actually Carlo's swimming is on Wednesday not Thursday")
    assert c is not None and vc.owner_noun(c.subject) == ("Carlo", "swimming")
    assert vc.rewrite("Carlos swimming is on Thursday", c) is None          # Carlos is another person
    assert vc.rewrite("Carlo's swimming is on Thursday", c) == "Carlo's swimming is on Wednesday"
