"""The minor-affect safety cells (owner decision 2026-11-03: affect memory stays ON for children, tested toward safety;
docs/governance/emotional-safety-note.md section 4.1). Deterministic; each carries a break-the-fix control (``test_control_*``).

  (a) false positives  - ``extract_affect`` (its label is RETAINED on a row) over ``fixtures/minor_affect_utterances.json``,
                         measured by ``scripts/perf/minor_affect_fp.py``: no false positive, every positive detected.
  (b) guest linkability - no affect row for ANY guest shape in any gate mode; a speaker-rejected voice turn's feeling is never an
                         approved row on the member (per-turn and nightly); no guest read returns a child's affect row.
  (c) correct + forget - one call forgets (with the twin) or corrects a minor's affect row; nobody else can.
  (d) the kid-safe drift rubric is pinned in ``test_persona_drift.py``.

Synthetic ids and invented text only, fake Chroma, no database, no network (``ci_safe``).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import memory_authority as ma
import memory_service
import memory_space
from memory_gate import extract_affect
from test_memory_authority_gates import svc as _gates_svc
from test_memories_voice import ME, OTHER, approved, env, row_id  # noqa: F401 - ``env`` is a fixture

pytestmark = pytest.mark.ci_safe

FIXTURE = Path(__file__).parent / "fixtures" / "minor_affect_utterances.json"
MINOR = "demo_bar_0000000c"
GUEST_SHAPES = ["guest", "GUEST", " guest ", "anonymous", "voice-guest", "voice-daemon", "", "guest-3f9a1c",
                "Guest-AB12", " guest-77 "]
MODES = [None, "household", "members", "optin", "off"]


def run(coro):
    return asyncio.run(coro)


def _persona(monkeypatch, minors=(MINOR, OTHER)):
    """Every id in ``minors`` is a child (``kid``, minor); everyone else an adult member."""
    import persona_layer

    async def load(uid, db=None):
        if uid in minors:
            return persona_layer.MemberMode(mode="kid", minor=True)
        return persona_layer.MemberMode(mode="companion", minor=False)

    monkeypatch.setattr(persona_layer, "load_member_mode", load)


@pytest.fixture
def svc(monkeypatch):
    s = _gates_svc.__wrapped__(monkeypatch)          # test_memory_authority_gates' fake-Chroma service, env cleared
    _persona(monkeypatch)
    return s


def emo(svc, user, text="User was scared of the thunder last night.", **kw):
    return run(svc.ingest(text, user_id=user, source="digest", memory_type="emotional_moment", status="approved", **kw))


def felt_fact(svc, user, text="User has a swimming carnival on Friday.", **kw):
    return run(svc.ingest(text, user_id=user, source="turn_digest", status="approved", metadata={"affect": "nervous"}, **kw))


def _gate(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv("ZOE_AFFECT_CONSENT_GATE", raising=False)
    else:
        monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", mode)


# ── (a) false positives of the retained detector ─────────────────────────────────────────────────────

def _rows(split=None):
    data = _harness().load(FIXTURE)          # the harness's own loader: the test and the measurement read one schema
    splits = (split,) if split else ("dev", "held_out")
    return [r for s in splits for r in data[s]]


@pytest.mark.parametrize("text,label", [
    ("i'm afraid of the dark", "scared"), ("i'm afraid to go in there", "scared"), ("i'm afraid that it will hurt", "scared"),
    ("i'm afraid my dad will die", "scared"), ("i'm afraid i'll fail the test", "scared"),
    ("i'm afraid we won't make it", "scared"),
    ("i'm happy to be home", "happy"), ("i'm happy to see grandma", "happy"),
    ("i've been feeling down the last few days", "sad"), ("i was down the whole week", "sad"), ("i'm down about it", "sad"),
])
def test_a_the_idiom_guards_keep_the_real_feeling(text, label):
    assert extract_affect(text)[0] == label


def test_control_a_without_the_idiom_guards_the_fixture_has_false_positives(monkeypatch):
    """Break the fix: the pre-2026-11-05 forms (no idiom guards) re-introduce the measured false positives."""
    import re

    import memory_gate as mg

    old = dict(mg._AFFECT_LABELS)
    old["scared"] = r"scared|afraid|terrified|frightened"
    old["sad"] = r"sad|down(?!\s+(?:for|to|with|here|there|at|in|on|by)\b)|low(?!\s+on\b)|miserable|heartbroken|gutted"
    old["happy"] = r"happy|delighted|over\s+the\s+moon"
    res = tuple((lab, re.compile(mg._AFFECT_ANCHOR + mg._AFFECT_SOFTENERS + r"(?:" + forms + r")\b", re.IGNORECASE))
                for lab, forms in old.items()) + mg._AFFECT_RES[-1:]
    monkeypatch.setattr(mg, "_AFFECT_RES", res)
    fps = [r["text"] for r in _rows() if r["label"] != "positive" and mg.extract_affect(r["text"])[0]]
    assert len(fps) >= 8, fps


def _harness():
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "scripts" / "perf" / "minor_affect_fp.py"
    spec = importlib.util.spec_from_file_location("minor_affect_fp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_the_harness_passes_the_bar_and_reports_counts_only():
    h = _harness()
    rep = h.measure(h.load(FIXTURE))
    assert rep["status"] == "pass", rep["reasons"]
    t = rep["minor_retained_all_splits"]
    assert t["false_positives"] == 0 and t["n_negative"] >= 100 and t["recall"] == 1.0
    blob = json.dumps(rep)
    assert not any(r["text"] in blob for r in _rows()), "the report carries no utterance text"


@pytest.mark.parametrize("fire,why", [(lambda _t: True, "false-positive"), (lambda _t: False, "recall")])
def test_control_a_the_harness_bar_fails_an_always_and_a_never_firing_detector(monkeypatch, fire, why):
    h = _harness()
    monkeypatch.setattr(h, "detectors", lambda: {"retained": fire, "cue": fire})
    rep = h.measure(h.load(FIXTURE))
    assert rep["status"] == "fail" and any(why in r for r in rep["reasons"]), rep["reasons"]


# ── (b) no retained affect row a guest can be linked to ─────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["household", "members", "optin"])
@pytest.mark.parametrize("guest", GUEST_SHAPES)
def test_b_the_affect_gate_refuses_every_guest_shape(svc, monkeypatch, mode, guest):
    _gate(monkeypatch, mode)
    assert run(svc._affect_allowed(guest)) is False


def test_control_b_a_sentinel_only_gate_lets_a_guest_principal_through(svc, monkeypatch):
    """Break the fix: the gate's old predicate (sentinels only) allowed ``guest-<id>`` under the default household mode.
    A child and an adult member are still kept by default."""
    from user_filters import GUEST_USERS

    assert run(svc._affect_allowed(MINOR)) is True and run(svc._affect_allowed(ME)) is True

    monkeypatch.setattr(memory_service, "_is_unidentified", lambda u: (u or "").strip().lower() in GUEST_USERS)
    assert run(svc._affect_allowed("guest-3f9a1c")) is True


def _refused(write):
    """None, or the store's own refusal of an empty id (``user_id is required``): both mean nothing was filed."""
    try:
        return write()
    except memory_service.MemoryServiceError:
        return None


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("guest", GUEST_SHAPES)
def test_b_no_affect_row_is_kept_for_any_guest_shape(svc, monkeypatch, mode, guest):
    _gate(monkeypatch, mode)
    assert _refused(lambda: emo(svc, guest)) is None
    assert _refused(lambda: felt_fact(svc, guest)) is None
    assert not svc._col.rows, "nothing at all is filed for a guest: no row to link a feeling to"


def test_control_b_without_the_identity_walls_a_guest_principal_keeps_a_feeling(svc, monkeypatch):
    """Break the fix: with ``is_unidentified`` removed from ingest AND the gate, a ``guest-<id>`` turn files a feeling."""
    monkeypatch.setattr(memory_service, "_is_unidentified", lambda _u: False)
    ref = emo(svc, "guest-3f9a1c")
    assert ref is not None and ref.metadata["memory_type"] == "emotional_moment"


VOICE_FEELING = "i'm nervous about the swimming carnival on friday"


def _voice_felt(svc, who, verified):
    return run(svc.ingest("User is nervous about the swimming carnival on Friday.", user_id=who, source="voice_turn_digest",
                          status="approved", metadata={"affect": "nervous"}, speaker_verified=verified,
                          anchor_text=VOICE_FEELING, source_excerpt=VOICE_FEELING))


@pytest.mark.parametrize("who", [MINOR, ME])
def test_b_an_unverified_voices_feeling_is_never_an_approved_row_on_the_member(svc, who):
    """PER-TURN voice lane: a guest at a child's panel is attributed to the child with ``speaker_verified=False``; the feeling
    may be a candidate to confirm, never an approved, servable row about the child."""
    ref = _voice_felt(svc, who, False)
    assert ref is None or ref.metadata.get("status") != "approved", ref.metadata
    served = run(svc.load_for_prompt(who, limit=50))
    assert not any(ma.carries_affect(r.metadata) for r in served)


def test_control_b_a_verified_voices_feeling_is_kept(svc):
    ref = _voice_felt(svc, MINOR, True)
    assert ref is not None and ref.metadata.get("status") == "approved" and ref.metadata.get("candidate_affect") == "nervous"


REJECTED = '{"speaker_verified": false}'
DAY = [("i was so scared of the thunder last night", "m1", "2026-10-08T01:00:00+00:00", None),
       ("i'm really sad my best friend moved away", "m2", "2026-10-08T02:00:00+00:00", REJECTED),
       ("we made pancakes for breakfast and i flipped them", "m3", "2026-10-08T03:00:00+00:00", "{}")]


def _night_emotional_pass_reads(monkeypatch, rows=DAY):
    """Run the NIGHTLY emotional pass over ``rows`` (chat_messages, metadata last); return what the (faked) model was shown."""
    import memory_digest as md

    seen: list[str] = []

    async def fake_call(text, *_a, **_k):
        seen.append(str(text))
        return []

    monkeypatch.setattr(md, "_emotional_call", fake_call)
    transcript = run(md._transcript_from_rows(MINOR, rows))
    run(md._emotional_memory_pass(MINOR, transcript, svc=None))
    return transcript, " ".join(seen)


def test_b_the_nightly_emotional_pass_never_reads_a_turn_the_speaker_gate_rejected(monkeypatch):
    """The NIGHTLY lane files ``emotional_moment`` rows as ``digest`` with no per-turn verdict: a turn the speaker gate rejected
    (a guest or sibling at the child's panel) must not be read, or that stranger's feeling becomes the child's."""
    transcript, shown = _night_emotional_pass_reads(monkeypatch)
    assert transcript.unverified == frozenset({"m2"})
    assert "thunder" in shown and "pancakes" in shown, "the member's own turns are still read"
    assert "best friend moved away" not in shown
    assert _night_emotional_pass_reads(monkeypatch, rows=[DAY[1]])[1] == "", "a day of only rejected turns reads nothing"


def test_control_b_without_the_filter_the_nightly_pass_reads_the_strangers_feeling(monkeypatch):
    import memory_digest as md

    monkeypatch.setattr(md, "without_rejected_speakers", lambda text: text)
    _t, shown = _night_emotional_pass_reads(monkeypatch)
    assert "best friend moved away" in shown


@pytest.mark.parametrize("guest", GUEST_SHAPES)
def test_b_a_guest_read_never_returns_a_childs_affect_row(svc, guest):
    assert emo(svc, MINOR) is not None and felt_fact(svc, MINOR) is not None
    assert run(svc.load_for_prompt(guest, limit=50)) == []


def test_control_b_the_child_reads_their_own_affect_rows(svc):
    emo(svc, MINOR)
    felt_fact(svc, MINOR)
    got = run(svc.load_for_prompt(MINOR, limit=50))
    assert any(r.metadata.get("memory_type") == "emotional_moment" for r in got)
    assert any(r.metadata.get("candidate_affect") == "nervous" for r in got)


# ── (c) one-step correction and forget of an affect row about a minor ────────────────────────────────────

EXCERPT = "i was really scared of the thunder last night"


def _child(env, monkeypatch):
    """``OTHER`` (role ``child`` in the voice env) is also a minor in ``member_modes``; their affect rows are kept by default."""
    env.db.conn.execute("INSERT INTO member_modes VALUES (?, ?, ?)", (OTHER, "kid", 1))
    env.db.conn.commit()
    _persona(monkeypatch, minors=(OTHER,))
    monkeypatch.delenv("ZOE_AFFECT_CONSENT_GATE", raising=False)
    a = run(env.svc.ingest("User was scared of the thunder last night.", user_id=OTHER, source="digest",
                           memory_type="emotional_moment", status="approved", source_excerpt=EXCERPT,
                           captured_at="2026-10-06T02:00:00Z"))
    b = run(env.svc.ingest("User heard thunder last night.", user_id=OTHER, source="turn_digest", status="approved",
                           metadata={"affect": "scared"}, source_excerpt=EXCERPT, captured_at="2026-10-06T02:00:00Z"))
    assert a is not None and b is not None and b.metadata.get("candidate_affect") == "scared"
    return a, b


def _affect_left(env, user=OTHER):
    rows = run(env.svc.list_by_status(user_id=user, status="approved", limit=500))
    return [r.text for r in rows if r.metadata.get("memory_type") == "emotional_moment" or ma.carries_affect(r.metadata)]


def test_c_one_call_forgets_a_minors_affect_row_and_its_twin(env, monkeypatch):
    a, _b = _child(env, monkeypatch)
    assert run(memory_space.forget_row(OTHER, a.id)) == "forgotten"
    assert _affect_left(env) == [], "the feeling must not survive in the utterance's twin"
    packet = run(env.svc.load_for_prompt(OTHER, limit=50))
    assert "thunder" not in str([r.text for r in packet])


def test_c_a_forgotten_feeling_is_not_re_mined(env, monkeypatch):
    a, _b = _child(env, monkeypatch)
    run(memory_space.forget_row(OTHER, a.id))
    again = run(env.svc.ingest("User was scared of the thunder last night.", user_id=OTHER, source="digest",
                               memory_type="emotional_moment", status="approved", source_excerpt=EXCERPT,
                               captured_at="2026-10-06T02:00:00Z"))
    assert again is None or again.metadata.get("status") != "approved"
    assert _affect_left(env) == []


def test_c_one_call_corrects_a_minors_affect_row(env, monkeypatch):
    a, _b = _child(env, monkeypatch)
    assert run(memory_space.correct_row(OTHER, a.id, "I wasn't scared of the thunder, I liked watching it.")) == "corrected"
    old = run(env.svc.get(a.id))
    assert old.metadata["status"] == "superseded" and old.metadata.get("superseded_by_id")
    left = approved(env, OTHER)
    assert any("liked watching it" in t for t in left)
    assert not any("scared of the thunder last night" in t for t in left), "the old feeling is not served beside the fix"
    rows = run(env.svc.list_by_status(user_id=OTHER, status="approved", limit=500))
    assert not any(r.metadata.get("candidate_affect") == "scared" for r in rows), "nor in the twin that carried it"


def test_c_nobody_else_can_forget_or_correct_a_minors_affect_row(env, monkeypatch):
    a, _b = _child(env, monkeypatch)
    assert run(memory_space.forget_row(ME, a.id)) == "gone"
    assert run(memory_space.correct_row(ME, a.id, "something else")) == "gone"
    assert run(memory_space.forget_row("guest-3f9a1c", a.id)) == "gone"
    assert len(_affect_left(env)) == 2, "the child's rows are untouched"


def test_c_a_guardian_corrects_one_affect_row_of_a_minor_in_one_call(env, monkeypatch):
    """A ``child`` role's own screen and voice forget/correct are refused by design ("ask a grown-up"): the admin edit is the
    grown-up's one step to correct."""
    _a, b = _child(env, monkeypatch)
    fixed = run(env.svc.review(b.id, decision="edit", edits="User watched the storm last night.", actor=ME, origin="admin"))
    assert fixed is not None and fixed.metadata.get("status") == "approved"
    assert run(env.svc.get(b.id)).metadata["status"] == "superseded"


def _review_as(user, rid):
    """``POST /api/memories/{id}/review`` (the real router) as ``user``, action reject."""
    from test_memory_space import make_client

    return make_client(lambda _r: dict(user)).post(f"/api/memories/{rid}/review", json={"action": "reject"})


ADMIN = {"user_id": ME, "role": "admin", "username": "casey"}


def test_c_a_guardians_one_call_forgets_the_row_and_its_twin(env, monkeypatch):
    """The admin review route's reject of another member's approved row is ``memory_space.guardian_forget``: one call, the twin
    carrying the feeling goes too, and every reject is the admin's (operator class), never the child's own word."""
    a, b = _child(env, monkeypatch)
    r = _review_as(ADMIN, a.id)
    assert r.status_code == 200, r.text
    assert _affect_left(env) == []
    assert run(env.svc.get(b.id)).metadata["status"] == "rejected"
    assert any(x.get("mem_id") == b.id and x.get("action") == "reject" for x in env.audit), "audited reject of the twin"


def test_c_only_an_admin_takes_the_guardian_path(env, monkeypatch):
    a, _b = _child(env, monkeypatch)
    assert _review_as({"user_id": "demo_bar_00000009", "role": "user"}, a.id).status_code == 403
    assert len(_affect_left(env)) == 2


def test_control_c_the_old_single_row_reject_leaves_the_feeling_in_the_twin(env, monkeypatch):
    """Break the fix: the plain per-row admin reject (the route before the guardian path) clears one row only."""
    a, _b = _child(env, monkeypatch)
    run(env.svc.review(a.id, decision="reject", actor=ME, origin="admin", note="guardian"))
    assert _affect_left(env) == ["User heard thunder last night."]


def test_control_c_without_the_twin_sweep_the_feeling_survives_a_forget(env, monkeypatch):
    """Break the fix: with ``twins_of`` returning nothing, forgetting the emotional moment leaves the twin carrying the feeling."""
    import provenance_answers as pa

    a, _b = _child(env, monkeypatch)

    async def no_twins(*_a, **_k):
        return []

    monkeypatch.setattr(pa, "twins_of", no_twins)
    assert run(memory_space.forget_row(OTHER, a.id)) == "forgotten"
    assert _affect_left(env) == ["User heard thunder last night."]


def test_c_a_half_finished_guardian_forget_resumes_on_retry(env, monkeypatch):
    """The first request rejects the row, then the twin's reject fails (503). The retry must NOT be an ordinary single-row reject
    that reports success with the twin still approved: it finishes every remaining step."""
    a, b = _child(env, monkeypatch)
    real = env.svc.review
    seen = {"failed": False}

    async def flaky(mem_id, **kw):
        if mem_id == b.id and not seen["failed"]:
            seen["failed"] = True
            raise RuntimeError("store blip")
        return await real(mem_id, **kw)

    monkeypatch.setattr(env.svc, "review", flaky)
    assert _review_as(ADMIN, a.id).status_code == 503
    assert run(env.svc.get(a.id)).metadata["status"] == "rejected"
    assert run(env.svc.get(b.id)).metadata["status"] == "approved"          # the twin is still carrying the feeling
    r = _review_as(ADMIN, a.id)
    assert r.status_code == 200, r.text
    assert _affect_left(env) == []
    assert run(env.svc.get(b.id)).metadata["status"] == "rejected"


def test_c_a_row_rejected_by_something_else_is_not_resumed(env, monkeypatch):
    import memory_space as ms

    assert ms.guardian_forget_open({"status": "approved"}) and ms.guardian_forget_open({})
    assert ms.guardian_forget_open({"status": "rejected", "review_note": "guardian_forget"})
    assert not ms.guardian_forget_open({"status": "rejected", "review_note": "not mine"})
    assert not ms.guardian_forget_open({"status": "superseded"})


@pytest.mark.parametrize("text", ["i'm afraid i'm being followed", "i'm afraid my dad is angry with me"])
def test_a_present_tense_fear_stays_a_feeling(text):
    assert extract_affect(text)[0] == "scared"
