"""STT snap (``stt_snap.py``): one misheard word of a command snapped back to the ONE command it can only be.

Every phrasing here is invented for the test or is a public HA/OVOS corpus sentence; none is copied from a household
transcript or log (public repo). Pure: the two regex matchers, no model, no DB, no network.

What is pinned:
  * the owner's live examples snap to the right command with the right safety class;
  * hard negatives - real sentences that merely START like a command ("two people are coming", "to be honest") - never snap;
  * the safety classes: a read / a low-risk write acts, anything destructive / multi-device / sensitive asks first;
  * the corpus gate (``stt_snap_eval``): over every synthetic mishear of the HA/OVOS corpus the snap makes ZERO wrong writes
    of its own, recovers at least ``RECOVERED_FLOOR``, and the instrument itself goes red on a planted greedy snap;
  * the vocabulary file is exactly what the corpus + ``command_shapes`` derive (drift-pinned);
  * the struggle detector records a snapped turn as MISHEAR with fix ``stt_snapped``.
"""
from __future__ import annotations

import json
import logging
import time

import pytest

pytestmark = pytest.mark.ci_safe  # the two regex matchers + committed data; no model, no DB, no network

import stt_snap
import stt_snap_eval as ev
import struggle_detector as sd
import struggle_signals as sig

#: the measured corpus numbers (stt_snap_eval.render). recovered may only rise, wrong only fall; wrong_write is 0, always.
RECOVERED_FLOOR = 922
WRONG_CEILING = 0


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for env in ("ZOE_STT_SNAP", "ZOE_EXPERT_MODE", "ZOE_EXPERT_ENABLED", "ZOE_EXPERT_ACTIVE_DOMAINS"):
        monkeypatch.delenv(env, raising=False)


def snap(text, **kw):
    with ev.quiet():
        return stt_snap.decide(text, **kw)


# ── the owner's examples ─────────────────────────────────────────────────────

@pytest.mark.parametrize("heard,meant,tag,safety", [
    ("two on the light", "turn on the light", "sky:smart_home/turn_on", "act"),
    ("to on the bedroom light", "turn on the bedroom light", "sky:smart_home/turn_on", "act"),
    ("Hey Zoe, two on the light please.", "turn on the light", "sky:smart_home/turn_on", "act"),
    ("two off the kitchen light", "turn off the kitchen light", "sky:smart_home/turn_off", "act"),
    ("set a time for 5 minutes", "set a timer for 5 minutes", "sky:timer/create", "act"),
    # plural device group: more than one device -> a question, never silent
    ("turn of the lights", "turn off the lights", "sky:smart_home/turn_off", "confirm"),
    ("turn of everything", "turn off everything", "ir:smart_home/turn_off", "confirm"),
    # destructive: a question, never silent
    ("council the timer", "cancel the timer", "sky:timer/cancel", "confirm"),
    ("cancer the timer", "cancel the timer", "sky:timer/cancel", "confirm"),
    # a door is sensitive even when the device is a light switch on it
    ("turn of the front door light", "turn off the front door light", "sky:smart_home/turn_off", "confirm"),
])
def test_owner_examples_snap(heard, meant, tag, safety):
    d = snap(heard)
    assert d.snap is not None, d.reason
    assert (d.snap.text, d.snap.intent, d.snap.safety) == (meant, tag, safety)


def test_a_timer_snap_keeps_its_length():
    d = snap("set a time for 5 minutes")
    assert d.snap.claim.seconds == 300


@pytest.mark.parametrize("text", ["turn on the light", "set a timer for 5 minutes", "what's up", "play some musics"])
def test_words_a_matcher_claims_never_snap(text):
    assert snap(text).reason == "claimed"


@pytest.mark.parametrize("text", ev.HARD_NEGATIVES)
def test_hard_negatives_never_snap(text):
    d = snap(text)
    assert d.snap is None, (text, d.snap)


@pytest.mark.parametrize("text", ["should I two on the light", "why did you two off the light", "can I two on the light",
                                  "did you two on the bedroom light"])
def test_a_question_about_a_command_never_snaps(text):
    d = snap(text)
    assert d.snap is None and d.reason == "question", (d.reason, d.snap)


def test_a_swap_after_a_broken_opening_never_snaps():
    """Only ONE word is misheard: a later word is swapped only when the words before it already open a command Zoe handles.
    "as the front door locked" (a misheard "is") must not become "as the forecast door locked" (a weather read)."""
    d = snap("as the front door locked")
    assert d.snap is None, d.snap
    assert snap("turn of the bedroom light").snap.text == "turn off the bedroom light"   # an intact opening still snaps


def test_numbers_are_slots_after_the_first_word(monkeypatch):
    """"two" OPENING the words may be a misheard "turn"; anywhere else a number is a slot and is never swapped."""
    vocab = stt_snap.Vocabulary((frozenset({"turn", "set"}), frozenset({"turn", "to"}), frozenset({"timer"})),
                                frozenset({"0|turn|on", "0|set|turn", "1|turn|on"}), frozenset())
    monkeypatch.setattr(stt_snap, "vocabulary", lambda: vocab)
    anything = lambda text, _ctx=None: C("sky:smart_home/turn_on", text) if "turn" in text else None  # noqa: E731
    assert stt_snap.decide("two on the light", claim_fn=anything).snap.swapped == ("two", "turn")
    d = stt_snap.decide("set two on the light", claim_fn=anything)
    assert d.snap is None and d.reason == "no_candidate"


def test_ambiguous_swaps_abstain():
    """Two DIFFERENT commands within ``MARGIN`` of each other: no guess."""
    def claims(text, _ctx=None):
        return {"turn on the light": stt_snap.Claim("sky:smart_home/turn_on", "a"),
                "turn off the light": stt_snap.Claim("sky:smart_home/turn_off", "b")}.get(text)

    vocab = stt_snap.Vocabulary((frozenset({"turn"}), frozenset({"on", "off"}), frozenset({"the"})),
                                frozenset({"0|turn|on", "0|turn|off", "1|on|the", "1|off|the"}), frozenset())
    orig = stt_snap.vocabulary
    stt_snap.vocabulary = lambda: vocab
    try:
        d = stt_snap.decide("turn of the light", claim_fn=claims)
        assert d.snap is not None and d.snap.text == "turn off the light"      # "of" -> "off" is far cheaper than "on"
        d = stt_snap.decide("turn onf the light", claim_fn=claims)            # equally far from both
        assert d.snap is None and d.reason in ("ambiguous", "no_candidate")
    finally:
        stt_snap.vocabulary = orig


def test_a_likelier_unserved_reading_blocks_a_worse_one():
    """"pours our tv shows": "pause" is the likely word but makes no command Zoe serves - never snap to "play"."""
    d = snap("pours our tv shows")
    assert d.snap is None


# ── sound-alike cost ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("heard,meant,most", [("of", "off", 0.0), ("time", "timer", 0.0), ("council", "cancel", 0.0),
                                              ("sit", "set", 0.0), ("pray", "play", 0.25), ("at", "add", 0.4)])
def test_cost_of_real_sound_alikes(heard, meant, most):
    assert stt_snap.cost(heard, meant) <= most


def test_liaison_reads_a_final_n_into_the_next_vowel():
    assert stt_snap.cost("two", "turn", before_vowel=True) == pytest.approx(stt_snap.LIAISON_COST)
    assert stt_snap.cost("two", "turn") > stt_snap.cost("two", "turn", before_vowel=True)


@pytest.mark.parametrize("heard,meant", [("so", "song"), ("what", "weather"), ("ship", "switch"), ("paws", "play")])
def test_spelled_alike_is_not_sound_alike(heard, meant):
    assert stt_snap.cost(heard, meant) > stt_snap.MAX_COST or not stt_snap.same_onset(heard, meant)


# ── safety classes ───────────────────────────────────────────────────────────

C = stt_snap.Claim


@pytest.mark.parametrize("claim,text,want", [
    (C("sky:clock/show", "k"), "what time is it", "read"),
    (C("sky:weather/current", "k"), "what's the weather", "read"),
    (C("sky:timer/status", "k"), "how long is left on the timer", "read"),
    (C("sky:lists/show", "k"), "show my shopping list", "read"),
    (C("sky:smart_home/delayed", "k"), "turn on the light in 5 minutes", "read"),
    (C("sky:smart_home/turn_on", "k"), "turn on the bedroom light", "act"),
    (C("sky:smart_home/turn_on", "k"), "turn on all the lights", "confirm"),
    (C("sky:smart_home/turn_on", "k"), "turn on the lights", "confirm"),
    (C("sky:smart_home/turn_off", "k"), "turn off the garage light", "confirm"),
    (C("sky:smart_home/turn_off", "k"), "switch off the heater", "confirm"),
    (C("sky:smart_home/set_brightness", "k"), "set the lamp to 50%", "confirm"),
    (C("sky:smart_home/activate_scene", "k"), "activate the movie scene", "confirm"),
    (C("sky:music/play", "k"), "play some music", "act"),
    (C("sky:music/pause", "k"), "pause the music", "act"),
    (C("sky:music/volume_up", "k"), "turn the volume up", "confirm"),
    (C("sky:lists/add_item", "k", item="milk"), "add milk to the shopping list", "act"),
    (C("sky:lists/add_item", "k", item="salt and pepper"), "add salt and pepper to the list", "confirm"),
    (C("sky:lists/remove_item", "k", item="milk"), "remove milk from the list", "confirm"),
    (C("sky:timer/create", "k", seconds=300), "set a timer for 5 minutes", "act"),
    (C("sky:timer/create", "k", seconds=None), "set a timer", "confirm"),
    (C("sky:timer/cancel", "k"), "cancel the timer", "confirm"),
    (C("sky:timer/increase", "k"), "add 5 minutes to the timer", "confirm"),
    (C("sky:calendar/create_event", "k"), "add dentist tomorrow at 3", "confirm"),
    (C("ir:list_add", "k", item="milk"), "add milk", "confirm"),
    (C("ir:time_query", "k"), "what's the time", "read"),
])
def test_safety(claim, text, want):
    assert stt_snap.safety(claim, text) == want


@pytest.mark.parametrize("tag,ok", [("sky:people/show", False), ("sky:people/create", False), ("ir:memory_remember", False),
                                    ("ir:greeting", False), ("ir:engineering_task_create", False), ("sky:voice/set", False),
                                    ("sky:smart_home/turn_on", True), ("ir:weather", True), ("sky:lists/add_item", True)])
def test_snap_never_lands_outside_its_domains(tag, ok):
    assert stt_snap.snap_allowed(C(tag, "k")) is ok


def test_every_corpus_write_glob_is_never_a_read():
    """Anything the phrasing corpus calls a WRITE (mapping.yaml ``writes``) is never classed ``read`` by the snap."""
    import fnmatch

    mapping = ev.corpus.load_mapping()
    tags = ["sky:" + f"{d}/{a}" for d, a in [
        ("timer", "create"), ("timer", "cancel"), ("timer", "increase"), ("timer", "decrease"), ("timer", "set"),
        ("lists", "add_item"), ("lists", "complete_item"), ("lists", "remove_item"), ("lists", "edit_item"),
        ("lists", "create_list"), ("calendar", "create_event"), ("calendar", "delete_event"), ("smart_home", "turn_on"),
        ("smart_home", "turn_off"), ("smart_home", "set_brightness"), ("smart_home", "activate_scene"), ("music", "play"),
        ("music", "pause"), ("music", "stop"), ("music", "volume_up"), ("music", "transfer"), ("voice", "set")]]
    tags += ["ir:timer_create", "ir:list_add", "ir:list_remove", "ir:calendar_create", "ir:reminder_create",
             "ir:smart_home/turn_on"]
    for tag in tags:
        assert any(fnmatch.fnmatchcase(tag, g) for g in mapping["writes"]), tag
        assert stt_snap.safety(C(tag, "k", seconds=300, item="milk"), "turn on the bedroom light") != "read", tag


# ── the corpus gate ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report():
    t0 = time.monotonic()
    rep = ev.evaluate()
    print("\n" + ev.render(rep) + f"\n  ({time.monotonic() - t0:.1f}s)")
    return rep


def test_corpus_snap_makes_no_wrong_write(report):
    assert report.counts["wrong_write"] == 0, report.examples.get("wrong_write")


def test_corpus_snap_never_snaps_a_hard_negative(report):
    assert report.counts["hard_negative_snapped"] == 0, report.examples.get("hard_negative_snapped")


def test_corpus_recovery_ratchet(report):
    assert report.counts["recovered"] >= RECOVERED_FLOOR
    assert report.counts["wrong"] <= WRONG_CEILING, report.examples.get("wrong")


def test_inherited_wrong_writes_all_ask_first(report):
    """A snap that restores words the matchers themselves mis-claim (the phrasing corpus' own wrong writes) never ACTS."""
    assert report.counts["wrong_write_inherited:act"] == 0, report.examples.get("wrong_write_inherited")


def test_instrument_goes_red_on_a_planted_greedy_snap(monkeypatch):
    """Negative control: a snap that turns every unclaimed mishear into a new timer must trip the wrong-write gate."""
    rows = [r for r in ev.corpus.load_rows() if r["label"] in ("HassTurnOn", "HassGetWeather")][:60]

    def greedy(text, **_kw):
        claim = C("sky:timer/create", "planted", seconds=60)
        return stt_snap.Decision(stt_snap.Snap(text, "set a timer", claim, "act", 0.0, 0, ("x", "y")), "snapped")

    monkeypatch.setattr(stt_snap, "decide", greedy)
    rep = ev.evaluate(rows=rows)
    assert rep.counts["wrong_write"] > 0


def test_mishears_are_deterministic():
    rows = ev.corpus.load_rows()[:200]
    assert ev.mishears(rows) == ev.mishears(rows)
    assert ev.mishears(rows, seed=1) != ev.mishears(rows)


# ── the vocabulary ───────────────────────────────────────────────────────────

def test_vocabulary_file_is_what_the_corpus_derives():
    """Regenerate: python3 services/zoe-data/tests/stt_snap_eval.py --write-vocab"""
    assert stt_snap.VOCAB_FILE.read_text(encoding="utf-8") == ev.derived_vocabulary_json()


def test_vocabulary_holds_the_command_verbs_and_no_numbers():
    v = stt_snap.vocabulary()
    assert {"turn", "set", "play", "add", "cancel", "pause"} <= v.positions[0]
    assert not any(w in v.positions[i] for i in range(stt_snap.POSITIONS) for w in ("two", "five", "ten"))
    assert {"cancel", "remove", "increase", "make"} <= set(stt_snap.command_verbs())
    assert "kitchen" not in stt_snap.command_verbs()          # a room is not a verb


def test_a_missing_vocabulary_disables_the_snap(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(stt_snap, "VOCAB_FILE", tmp_path / "missing.json")
    stt_snap.vocabulary.cache_clear()
    try:
        assert snap("two on the light").snap is None
    finally:
        stt_snap.vocabulary.cache_clear()


# ── runtime helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [(None, "shadow"), ("", "shadow"), ("off", "off"), ("on", "on"), ("ON", "on"),
                                      ("bogus", "shadow")])
def test_mode(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv("ZOE_STT_SNAP", raw)
    assert stt_snap.mode() == want


@pytest.mark.parametrize("env,decision,want", [
    ({"ZOE_EXPERT_MODE": "active"}, {"domain": "lists", "score": 0.9}, True),
    ({"ZOE_EXPERT_MODE": "active"}, {"domain": "lists", "score": 0.3}, False),     # under the domain threshold
    ({"ZOE_EXPERT_MODE": "active"}, {"domain": "chat", "score": 0.9}, False),
    ({"ZOE_EXPERT_MODE": "active"}, {"domain": "smart_home", "score": 0.9}, False),  # no expert for it
    ({"ZOE_EXPERT_MODE": "active"}, {"domain": "people", "score": 0.9}, False),      # voice defers people
    ({"ZOE_EXPERT_MODE": "shadow"}, {"domain": "lists", "score": 0.9}, False),
    ({"ZOE_EXPERT_MODE": "active"}, None, False),
])
def test_expert_may_claim(monkeypatch, env, decision, want):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert stt_snap.expert_may_claim(decision) is want


def test_log_line_keeps_a_guests_words_out(caplog):
    s = snap("two on the light").snap
    with caplog.at_level(logging.WARNING, logger="stt_snap"):
        stt_snap.log_line(s, "acted", guest=True)
        stt_snap.log_line(s, "acted", guest=False)
    guest, member = [r.getMessage() for r in caplog.records if "STT_SNAP" in r.getMessage()]
    assert "two on the light" not in guest and "turn on the light" not in guest
    assert f"from={s.from_hash}" in guest and "to=sky:smart_home/turn_on" in guest and "outcome=acted" in guest
    assert "two on the light" in member


def test_confirm_question_names_the_snapped_command():
    assert stt_snap.confirm_question(snap("council the timer").snap) == "Did you mean: cancel the timer?"


# ── the struggle detector ────────────────────────────────────────────────────

@pytest.fixture
def detector(monkeypatch):
    monkeypatch.delenv("ZOE_STRUGGLE_DETECT", raising=False)
    sd.reset()
    be = sd.MemoryBackend()
    sd.set_backend(be)
    yield be
    sd.reset()
    sd.set_backend(None)


async def test_a_snapped_turn_is_a_mishear_with_fix_stt_snapped(detector):
    s = snap("two on the light").snap
    stt_snap.note_struggle(s, "acted", turn_text=s.text)
    events = await sd.process((time.time(), "voice", s.text, "Turned the light on.", "member-a", "voice-panel-hall-1",
                               "hall", False))
    assert [(e.signal, e.fix_type) for e in events] == [("MISHEAR", "stt_snapped")]
    detail = json.loads(detector.data[0]["detail"])
    assert detail["reasons"] == ["stt_snapped", "acted"] and detail["intent"] == "sky:smart_home/turn_on"
    assert not sd._SNAPS                                                     # one note, one turn


async def test_a_snap_note_never_attaches_to_another_turn(detector):
    s = snap("two on the light").snap
    stt_snap.note_struggle(s, "acted", turn_text=s.text)
    events = await sd.process((time.time(), "voice", "what's the weather", "Sunny.", "member-a", "voice-panel-hall-1",
                               "hall", False))
    assert not any(e.fix_type == sig.FIX_STT_SNAPPED for e in events)


def test_struggle_off_keeps_no_snap_note(monkeypatch, detector):
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "off")
    stt_snap.note_struggle(snap("two on the light").snap, "acted", turn_text="turn on the light")
    assert not sd._SNAPS


def test_stt_snapped_is_a_known_fix_type_with_a_hint():
    import struggle_digest

    assert sig.FIX_STT_SNAPPED in sig.FIX_TYPES and sig.FIX_STT_SNAPPED in struggle_digest._FIX_HINT
