"""ZOE_MOONSHINE_KEYTERMS — runtime decoder biasing on moonshine-voice >= 0.1.2.

B1.10 (docs/knowledge/moonshine-0-1-5-upgrade.md). Pins the contract of
`routers.voice_tts._ensure_moonshine` around the optional `set_keyterms` step:

* default EMPTY = off — the transcriber is built exactly as before and
  `set_keyterms` is never called (the 0.0.62 behaviour, byte-for-byte);
* a configured list is parsed (split on comma, stripped, empties dropped,
  de-duplicated in order) and handed to `Transcriber.set_keyterms` once, right
  after the singleton loads;
* the step is FEATURE-DETECTED, not version-gated: a `Transcriber` without
  `set_keyterms` (0.0.62) still loads and the state says `supported: False`;
* biasing is best-effort: a refused list logs a warning and the rock keeps its
  transcriber (STT must never fail because of a biasing knob).

No model is loaded: `moonshine_voice` is replaced in `sys.modules` by a fake
whose `Transcriber` records what it is asked to do. ci_safe: the import chain
of `routers.voice_tts` is already proven slim-green by `test_voice_smoke_ci.py`.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

import routers.voice_tts as vt

pytestmark = pytest.mark.ci_safe


class _Arch:
    MEDIUM_STREAMING = "MEDIUM_STREAMING"
    SMALL_STREAMING = "SMALL_STREAMING"


def _install_fake_moonshine(monkeypatch, *, with_keyterms: bool, refuse: Exception | None = None):
    """Fake `moonshine_voice` + `moonshine_voice.transcriber`. Returns the record
    dict the fake Transcriber writes into."""
    record: dict = {"ctor": None, "set_keyterms": []}

    class Transcriber:
        def __init__(self, model_path, model_arch=None, **kw):
            record["ctor"] = (model_path, model_arch)

        def transcribe_without_streaming(self, audio, sr=16000, flags=0):
            return types.SimpleNamespace(lines=[])

    if with_keyterms:
        def set_keyterms(self, keyterms):
            if refuse is not None:
                raise refuse
            record["set_keyterms"].append(list(keyterms))
        Transcriber.set_keyterms = set_keyterms  # 0.1.2+ shape

    mv = types.ModuleType("moonshine_voice")
    mv.ModelArch = _Arch
    mv.get_model_for_language = lambda lang, arch: (f"/fake/{lang}/{arch}", arch)
    tr_mod = types.ModuleType("moonshine_voice.transcriber")
    tr_mod.Transcriber = Transcriber
    mv.transcriber = tr_mod
    monkeypatch.setitem(sys.modules, "moonshine_voice", mv)
    monkeypatch.setitem(sys.modules, "moonshine_voice.transcriber", tr_mod)
    return record


@pytest.fixture(autouse=True)
def _fresh_singleton(monkeypatch):
    """Each test starts with no cached transcriber and a clean keyterms state."""
    monkeypatch.setattr(vt, "_moonshine_model", None)
    monkeypatch.setattr(vt, "_moonshine_load_error", None)
    monkeypatch.setattr(
        vt, "_moonshine_keyterms_state",
        {"configured": 0, "applied": 0, "supported": None, "error": None},
    )
    monkeypatch.delenv("ZOE_MOONSHINE_KEYTERMS", raising=False)
    monkeypatch.delenv("ZOE_MOONSHINE_ARCH", raising=False)


# ── parsing ──────────────────────────────────────────────────────────────────

def test_default_is_empty_and_off(monkeypatch):
    assert vt.moonshine_keyterms() == ()
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "   ")
    assert vt.moonshine_keyterms() == ()


def test_parse_strips_dedupes_and_drops_empties(monkeypatch):
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", " Jason, Zoe ,,Kitchen, Jason ,Lounge,")
    assert vt.moonshine_keyterms() == ("Jason", "Zoe", "Kitchen", "Lounge")


def test_no_term_can_carry_a_comma(monkeypatch):
    """The library's delimiter is the comma and it RAISES on a term containing one;
    splitting on the comma makes that impossible by construction."""
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "a,,b,,,c d")
    terms = vt.moonshine_keyterms()
    assert terms == ("a", "b", "c d")
    assert all("," not in t for t in terms)


# ── loader contract ──────────────────────────────────────────────────────────

def test_flag_off_never_touches_set_keyterms(monkeypatch):
    rec = _install_fake_moonshine(monkeypatch, with_keyterms=True)
    tr = vt._ensure_moonshine()
    assert tr is vt._moonshine_model
    assert rec["ctor"] == ("/fake/en/MEDIUM_STREAMING", "MEDIUM_STREAMING")
    assert rec["set_keyterms"] == []
    assert vt.moonshine_keyterms_state() == {
        "configured": 0, "applied": 0, "supported": True, "error": None,
    }
    assert vt.moonshine_error() is None


def test_flag_on_applies_the_parsed_list_once(monkeypatch):
    rec = _install_fake_moonshine(monkeypatch, with_keyterms=True)
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "Jason, Zoe,Kitchen,Jason")
    tr = vt._ensure_moonshine()
    assert rec["set_keyterms"] == [["Jason", "Zoe", "Kitchen"]]
    # The singleton is cached: a second call must not re-apply.
    assert vt._ensure_moonshine() is tr
    assert rec["set_keyterms"] == [["Jason", "Zoe", "Kitchen"]]
    assert vt.moonshine_keyterms_state() == {
        "configured": 3, "applied": 3, "supported": True, "error": None,
    }


def test_old_moonshine_without_set_keyterms_still_loads(monkeypatch, caplog):
    """0.0.62 shape: no `set_keyterms`. The rock loads, biasing is reported OFF."""
    rec = _install_fake_moonshine(monkeypatch, with_keyterms=False)
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "Jason,Zoe")
    with caplog.at_level("WARNING"):
        tr = vt._ensure_moonshine()
    assert tr is not None and vt.moonshine_ready()
    assert vt.moonshine_error() is None
    assert rec["ctor"] is not None
    state = vt.moonshine_keyterms_state()
    assert state["configured"] == 2 and state["applied"] == 0
    assert state["supported"] is False and state["error"] is None
    assert any("no Transcriber.set_keyterms" in r.getMessage() for r in caplog.records)


def test_refused_list_is_best_effort_and_keeps_the_transcriber(monkeypatch, caplog):
    class MoonshineError(RuntimeError):
        pass

    _install_fake_moonshine(monkeypatch, with_keyterms=True, refuse=MoonshineError("bad model"))
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "Jason")
    with caplog.at_level("WARNING"):
        tr = vt._ensure_moonshine()
    assert tr is not None and vt.moonshine_ready()
    assert vt.moonshine_error() is None  # NOT a load failure
    state = vt.moonshine_keyterms_state()
    assert state == {"configured": 1, "applied": 0, "supported": True, "error": "MoonshineError"}
    assert any("refused the list" in r.getMessage() for r in caplog.records)


def test_transcriber_construction_failure_still_surfaces(monkeypatch):
    """Negative control: the best-effort wrapper must NOT swallow a real load
    failure — the rock going missing has to stay a raised, recorded error."""
    _install_fake_moonshine(monkeypatch, with_keyterms=True)

    class Boom(Exception):
        pass

    def _ctor(*a, **k):
        raise Boom("no model files")

    sys.modules["moonshine_voice.transcriber"].Transcriber = _ctor
    monkeypatch.setenv("ZOE_MOONSHINE_KEYTERMS", "Jason")
    with pytest.raises(Boom):
        vt._ensure_moonshine()
    assert vt.moonshine_error() == "Boom"
    assert not vt.moonshine_ready()


def test_arch_fallback_is_unchanged(monkeypatch):
    """Rock guard: an unknown ZOE_MOONSHINE_ARCH still falls back to MEDIUM_STREAMING."""
    rec = _install_fake_moonshine(monkeypatch, with_keyterms=True)
    monkeypatch.setenv("ZOE_MOONSHINE_ARCH", "LARGE_STREAMING_DOES_NOT_EXIST")
    vt._ensure_moonshine()
    assert rec["ctor"] == ("/fake/en/MEDIUM_STREAMING", "MEDIUM_STREAMING")


# ── readiness visibility ─────────────────────────────────────────────────────

def test_readyz_stt_reports_keyterms_state(monkeypatch):
    import main

    monkeypatch.setattr(vt, "moonshine_ready", lambda: True)
    monkeypatch.setattr(vt, "moonshine_error", lambda: None)
    monkeypatch.setattr(
        vt, "_moonshine_keyterms_state",
        {"configured": 2, "applied": 2, "supported": True, "error": None},
    )
    report = asyncio.run(main._check_stt_ready())
    assert report["ok"] is True
    assert report["keyterms"] == {"configured": 2, "applied": 2, "supported": True, "error": None}
