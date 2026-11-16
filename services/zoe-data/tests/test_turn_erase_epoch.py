"""ONE per-turn erase epoch, read before a turn's words exist, checked by every writer of an erasable text store.

The house rule is "forgotten means forever": once an erase reports success, nothing an in-flight turn writes may bring the erased
words back. The intent-miss log and the router shadow log used to read the erase epoch at THEIR OWN start (``detect_intent`` /
``route``). On a voice turn the words they log were produced by STT before either began, so an erase that completed during STT was
already inside their reading and the turn's words were appended after the erase reported success. Now the voice routes hold the
turn's epoch before the clip reaches STT (``local_text_logs.begin_turn``, a ContextVar) and the writers check against it.

End to end through FastAPI's TestClient: the REAL /api/voice/turn and /api/voice/turn_stream -> the REAL voice_command -> the REAL
``semantic_router.route`` (in ``asyncio.to_thread``) and ``intent_router.detect_intent``; only STT, TTS, the brain, push and the
router's model are faked. NEGATIVE CONTROL: ``test_negative_control_writers_reading_their_own_epoch_leak`` puts the writers back
on their own late reading and the erase-during-STT assertion goes red. Synthetic text only.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import contextvars
import json
import re
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

from fastapi.testclient import TestClient

import local_text_logs as ltl
import test_voice_stream_speaker_verdict as base

SVC = Path(__file__).resolve().parents[1]
TEXT = "purple synthetic marbles hum quietly"


class _Head:
    """The router head's model, faked (shadow mode logs one record per routed utterance)."""
    classes_ = np.array(["chat", "weather"])

    def predict_proba(self, _x):
        return np.array([[0.9, 0.1]])


def _logs(monkeypatch, tmp_path):
    import semantic_router as sr

    shadow = tmp_path / "router_head_shadow.jsonl"
    misses = tmp_path / "intent-misses.jsonl"
    monkeypatch.setenv("ZOE_ROUTER_HEAD_LOG", str(shadow))
    monkeypatch.setenv("ZOE_INTENT_MISS_LOG", str(misses))
    monkeypatch.setattr(sr, "_HEAD_LOG_PATH", str(shadow))     # the writer resolved its path at import
    return shadow, misses


def _lines(p: Path) -> list:
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _voice_turn(monkeypatch, tmp_path, route: str, *, erase_during_stt: bool, seen_epochs: list | None = None):
    import routers.voice_tts as vt
    import semantic_router as sr
    import recall_gate_trace

    shadow, misses = _logs(monkeypatch, tmp_path)
    seen: list = []
    app = base._app(monkeypatch, seen, claim_ok=False, filler=False)
    monkeypatch.setenv("ZOE_ROUTER_HEAD", "shadow")
    monkeypatch.setattr(sr, "_ensure_head_loaded", lambda: None)
    monkeypatch.setattr(sr, "_HEAD", _Head())
    monkeypatch.setattr(recall_gate_trace, "erase_entity", lambda rx: 0)
    erased: list = []

    async def stt(_path, **_kw):
        # the seam the STT-log tests use: the forget completes while the clip is being transcribed
        if erase_during_stt:
            erased.append(ltl.erase_entity(re.compile("synthetic-name-nobody-said")))
        await asyncio.sleep(0)
        return TEXT

    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    if seen_epochs is not None:
        real_route = sr.route

        def spy_route(text):
            seen_epochs.append(("route", ltl.writer_epoch()))     # inside asyncio.to_thread: the copied context
            return real_route(text)

        monkeypatch.setattr(sr, "route", spy_route)
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "hall-panel"}
    with TestClient(app) as client:
        r = client.post(route, json=payload)
        assert r.status_code == 200, r.text
    assert erased == ([0] if erase_during_stt else [])           # the erase ran and reported success
    return _lines(shadow), _lines(misses), seen


ROUTES = ["/api/voice/turn", "/api/voice/turn_stream"]


@pytest.mark.parametrize("route", ROUTES)
def test_control_no_erase_both_logs_get_the_turns_lines(monkeypatch, tmp_path, route):
    shadow, misses, _ = _voice_turn(monkeypatch, tmp_path, route, erase_during_stt=False)
    assert shadow and all(set(r) >= {"utt", "head_pred", "actual_routed"} for r in shadow), shadow
    assert misses and all(m["text"] == TEXT for m in misses), misses


@pytest.mark.parametrize("route", ROUTES)
def test_erase_during_stt_nothing_from_the_turn_reaches_either_log(monkeypatch, tmp_path, route):
    shadow, misses, _ = _voice_turn(monkeypatch, tmp_path, route, erase_during_stt=True)
    assert shadow == [], "the router shadow log took a line from a turn whose words predate a completed erase"
    assert misses == [], "the intent-miss log took a line from a turn whose words predate a completed erase"


@pytest.mark.parametrize("route", ROUTES)
def test_the_turns_epoch_survives_every_hop_to_the_writers(monkeypatch, tmp_path, route):
    """Route (request task) -> voice_command (same task on /turn; ``ensure_future`` sub-task on /turn_stream) -> ``route`` in
    ``asyncio.to_thread`` -> the lazily pulled brain stream: each sees the epoch read before STT, not a later one."""
    import intent_router

    epochs: list = []
    real_detect = intent_router.detect_intent

    def spy_detect(text, *a, **k):
        if k.get("log_miss", True):
            epochs.append(("detect", ltl.writer_epoch()))
        return real_detect(text, *a, **k)

    monkeypatch.setattr(intent_router, "detect_intent", spy_detect)
    before = ltl.erase_epoch()
    _voice_turn(monkeypatch, tmp_path, route, erase_during_stt=True, seen_epochs=epochs)
    assert ltl.erase_epoch() == before + 2                        # the erase moved the counter during STT ...
    kinds = {k for k, _ in epochs}
    assert {"route", "detect"} <= kinds, epochs
    assert all(ep == before for _, ep in epochs), epochs           # ... and every writer still holds the pre-STT reading


def test_negative_control_writers_reading_their_own_epoch_leak(monkeypatch, tmp_path):
    """Put the writers back on their own late reading (the old code): the erase during STT is inside it and the lines land."""
    monkeypatch.setattr(ltl, "writer_epoch", lambda own=None: ltl.erase_epoch() if own is None else own)
    shadow, misses, _ = _voice_turn(monkeypatch, tmp_path, "/api/voice/turn", erase_during_stt=True)
    assert shadow and misses, "the negative control did not reproduce the leak: the end-to-end test proves nothing"


# -- the typed path: no per-turn epoch = today's behaviour --------------------------------------------------------------

def _typed_miss(monkeypatch, tmp_path, *, erase_mid_detect: bool = False, erase_before: bool = False, begin: bool = False):
    """One typed turn's ``detect_intent`` (a miss) in its own context, as each request has; returns the intent-miss lines."""
    import intent_router

    _, misses = _logs(monkeypatch, tmp_path)
    real_norm = getattr(intent_router._normalize_chat_intent_text, "_real", intent_router._normalize_chat_intent_text)

    def norm(t):
        if erase_mid_detect:                   # the erase lands after detect_intent read its own epoch
            ltl.erase_entity(re.compile("synthetic-name-nobody-said"))
        return real_norm(t)

    norm._real = real_norm                 # a second call in one test wraps the REAL normaliser, not the first call's erase
    monkeypatch.setattr(intent_router, "_normalize_chat_intent_text", norm)

    def turn():
        if begin:
            ltl.begin_turn()                   # what the chat route does where the words enter
        if erase_before:                       # ... and an erase completes in the awaits before detection
            ltl.erase_entity(re.compile("synthetic-name-nobody-said"))
        return intent_router.detect_intent(TEXT)

    assert contextvars.copy_context().run(turn) is None
    return [m["text"] for m in _lines(misses)]


@pytest.fixture
def typed(monkeypatch):
    import recall_gate_trace

    monkeypatch.setattr(recall_gate_trace, "erase_entity", lambda rx: 0)
    return monkeypatch


def test_typed_turn_without_a_carrier_behaves_as_today(typed, tmp_path):
    assert _typed_miss(typed, tmp_path / "a") == [TEXT]                               # no erase: written
    assert _typed_miss(typed, tmp_path / "b", erase_mid_detect=True) == []           # today's own-start reading still drops
    assert _typed_miss(typed, tmp_path / "c", erase_before=True) == [TEXT]           # no carrier: an earlier erase is not its business


def test_typed_turn_with_an_epoch_held_at_entry_drops_an_erase_before_detection(typed, tmp_path):
    """The chat route holds the epoch where the typed words enter: an erase in the awaits before ``detect_intent`` now counts."""
    assert _typed_miss(typed, tmp_path / "a", erase_before=True, begin=True) == []
    assert _typed_miss(typed, tmp_path / "b", begin=True) == [TEXT]


# -- the carrier itself -------------------------------------------------------------------------------------------------

def _fresh(fn):
    return contextvars.copy_context().run(fn)


def test_begin_turn_keeps_a_held_epoch_and_writer_epoch_falls_back_to_now(typed):
    def turn():
        ep = ltl.begin_turn()
        ltl.erase_entity(re.compile("synthetic-name-nobody-said"))
        return ep, ltl.begin_turn(), ltl.writer_epoch(), ltl.writer_epoch(ltl.erase_epoch())

    ep, again, held, mixed = _fresh(turn)
    assert again == held == ep and mixed == ltl.STALE_EPOCH    # an in-process hand-over keeps it; a later own reading is stale
    assert _fresh(ltl.writer_epoch) == ltl.erase_epoch()      # no carrier: today's reading


def test_a_carried_token_only_ever_refuses_more(typed):
    token = _fresh(lambda: (ltl.begin_turn(), ltl.turn_token())[1])
    assert _fresh(lambda: ltl.begin_turn(token)) == ltl.erase_epoch()             # same epoch, same process: kept
    for forged in (f"{ltl._BOOT}:{ltl.erase_epoch() + 2}", "other-boot:0", 7, ""):
        assert _fresh(lambda: ltl.begin_turn(forged)) == ltl.STALE_EPOCH, forged
    ltl.erase_entity(re.compile("synthetic-name-nobody-said"))
    assert _fresh(lambda: ltl.begin_turn(token)) == ltl.STALE_EPOCH               # an erase since the transcription
    assert ltl.append_line(ltl.INTENT_MISSES, '{"text": "x", "ts": 1}', ltl.STALE_EPOCH) is False


def test_stream_words_that_predate_an_erase_make_the_turn_stale(typed):
    def turn():
        streamed_at = ltl.erase_epoch()                       # the first under-speech chunk
        ltl.erase_entity(re.compile("synthetic-name-nobody-said"))
        ltl.begin_turn()                                      # /turn_stream reads its epoch after the member spoke
        ltl.turn_predates(streamed_at)
        return ltl.writer_epoch()

    assert _fresh(turn) == ltl.STALE_EPOCH
    assert _fresh(lambda: (ltl.begin_turn(), ltl.turn_predates(ltl.erase_epoch()), ltl.writer_epoch())[2]) == ltl.erase_epoch()


# -- structural: every STT route holds the turn's epoch before STT -------------------------------------------------------

STT_ENTRIES = {"_transcribe_audio", "_transcribe_audio_impl", "_take_stt_stream_text", "_transcribe_wav"}
TURN_EPOCH_CALLS = {"_stt_erase_epoch", "begin_turn"}


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _stt_routes():
    out = []
    for path in sorted((SVC / "routers").glob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in tree.body:
            if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)) or fn.name in STT_ENTRIES:
                continue
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            stt = [c.lineno for c in calls if _callee(c) in STT_ENTRIES]
            if stt:
                out.append((path.name, fn.name, min(stt), [c.lineno for c in calls if _callee(c) in TURN_EPOCH_CALLS]))
    return out


def test_every_stt_route_holds_the_turn_epoch_before_stt():
    routes = _stt_routes()
    names = {(f, n) for f, n, _, _ in routes}
    # the known set, so a refactor that hides a route from the walk is noticed too
    assert {("voice_tts.py", "voice_transcribe"), ("voice_tts.py", "voice_turn"), ("voice_tts.py", "voice_turn_stream"),
            ("voice_tts.py", "voice_turn_stream_speculation"), ("voice_tts.py", "voice_ambient"),
            ("voice_livekit.py", "_run_pipeline"), ("voice_livekit.py", "livekit_audio"),
            ("telegram_media.py", "telegram_transcribe")} <= names, sorted(names)
    late = [(f, n) for f, n, first_stt, holds in routes if not holds or min(holds) > first_stt]
    assert late == [], f"STT route(s) that do not hold the turn's erase epoch before STT: {late}"


def test_text_entry_points_hold_the_turn_epoch_before_any_await():
    """Typed words enter at the chat route and /command: the epoch is held before the first await after the body is read."""
    for rel, name in (("routers/chat.py", "chat"), ("routers/voice_tts.py", "voice_command")):
        fn = next(n for n in ast.parse((SVC / rel).read_text()).body if getattr(n, "name", "") == name)
        holds = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and _callee(c) in TURN_EPOCH_CALLS]
        awaits = sorted(a.lineno for a in ast.walk(fn) if isinstance(a, ast.Await))
        assert holds, (rel, name)
        later = [a for a in awaits if a > min(holds)]
        assert len([a for a in awaits if a < min(holds)]) <= (1 if name == "chat" else 0), (rel, name, awaits[:3], holds)
        assert later, (rel, name)
