"""The panel daemon's at-close speaker fields and SHADOW reflex decision on FIRST_SOUND (log-only).

``spk=<1|0|-1> spk_ms=<n|-1>``: the speaker model's own accept at the FAR 0.1 % bar for this turn's claim, and when it was
ready (ms from the close). ``skip=``: what ``zoe_voice_reflex.decide()`` would have said on the real inputs, with the master
switch forced on inside that one call. Nothing plays: no player is created and no bank is loaded for it, pinned below.

Synthetic audio and invented ids only. Negative controls: the far1 bar would accept what far01 refuses; a shadow call
without the forced switch reports ``disabled`` on every turn; a removed input changes the skip.
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup"
DAEMON = SETUP / "zoe_voice_daemon.py"


def _by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FS = _by_path("first_sound_helpers_for_shadow", Path(__file__).with_name("test_voice_daemon_first_sound_log.py"))
REFLEX = _by_path("zoe_voice_reflex_for_shadow", SETUP / "zoe_voice_reflex.py")
RN34 = _by_path("zoe_speaker_resnet34_for_shadow", SETUP / "zoe_speaker_resnet34.py")


@pytest.fixture(scope="module")
def daemon():
    return FS._load("zoe_voice_daemon_shadow_reflex_test")


@pytest.fixture(autouse=True)
def _fresh(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_reflex", REFLEX)
    monkeypatch.setattr(daemon, "_rn34", RN34)
    monkeypatch.setattr(daemon, "REFLEX_MODE", "shadow")   # the post-hoc shadow runs only in shadow / on (off: nothing)
    for k in ("ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT", "ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "REFLEX_ENABLED",
              "ZOE_SPEAKER_RESNET34_TIER", "REFLEX_SILENT_MEMBERS"):
        monkeypatch.delenv(k, raising=False)
    daemon._turn_close.clear()
    daemon._TALK.begin()
    daemon._TALK.armed = False
    yield
    daemon._turn_close.clear()


def _claim(daemon, score, seconds=3.0, model=None, guest=None):
    c = daemon._Claim("member_a", score, RN34.MODEL_ID if model is None else model, seconds)
    c.guest = guest
    return c


# ── the speaker verdict ──────────────────────────────────────────────────────

def test_spk_verdict_is_the_far01_accept(daemon):
    v = daemon._spk_verdict
    assert v(None) is None and v(daemon._CLAIM_UNSET) is None and v("member_a") is None
    assert v(daemon.SCORED_NO_MATCH) is False                     # the gate ran, nobody matched
    assert v(_claim(daemon, 0.352)) is True                       # long clip: 0.351
    assert v(_claim(daemon, 0.350)) is False
    assert v(_claim(daemon, 0.310, seconds=2.0)) is True          # short clip: 0.309
    assert v(_claim(daemon, 0.300, seconds=2.0)) is False
    assert v(_claim(daemon, 0.36, model="resemblyzer")) is None   # a retired model's score means nothing here
    assert v(_claim(daemon, float("nan"))) is None
    assert v(_claim(daemon, 0.40, guest=("guest_b", 0.45))) is False   # a named guest is closer
    assert v(_claim(daemon, 0.40, guest=("guest_b", 0.30))) is True


def test_far1_would_accept_what_far01_refuses(daemon):
    """NEGATIVE CONTROL: the panel's informational accept (the wake tier, FAR 1 %) says yes to 0.30 on a long clip."""
    assert RN34.decision(0.30, 3.0)["accept"] is True
    assert daemon._spk_verdict(_claim(daemon, 0.30)) is False


def test_an_env_override_can_only_raise_the_bar(daemon, monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.20")
    assert RN34.threshold_for("long", "far01") == pytest.approx(0.20)   # the override would erase far01 ...
    assert daemon._spk_verdict(_claim(daemon, 0.30)) is False             # ... but the table bar still holds
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.50")
    assert daemon._spk_verdict(_claim(daemon, 0.45)) is False
    monkeypatch.setattr(daemon, "_rn34", None)
    assert daemon._spk_verdict(_claim(daemon, 0.9)) is None


# ── the stream turn: spk, spk_ms and skip on FIRST_SOUND ─────────────────────

class _Ep:
    tail_rule, close_ms, _quiet, _hesitating, _speech_chunks = "deep", 640, 8, False, 15


QUIET = np.full(1280, 3, dtype=np.int16).tobytes()   # room-quiet, not digital silence (so not "muted")


def _close(daemon, *, media=False, busy=False, stop="silence", t=99.9, hour=12):
    daemon._turn_close.update(t=t - 60.0)                 # a previous turn a minute ago: not an idle greeting
    daemon._MEDIA = types.SimpleNamespace(snapshot=lambda now: (media, 30))
    daemon._stamp_close(t if stop == "silence" else None, _Ep(), stop, [QUIET] * 40, 20)
    daemon._turn_close.update(t=t, busy=busy, hour=hour)
    daemon._MEDIA = None


def _turn(daemon, monkeypatch, caplog, *, claim_fn, claim_s=0.09, carried=None, **close_kw):
    clock = FS._Clock(100.0)
    writes, posts = FS._stream_turn_setup(daemon, monkeypatch, clock)
    _close(daemon, **close_kw)

    def _score(_wav):
        clock.t += claim_s
        return claim_fn()

    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", _score)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        kw = {} if carried is None else {"voice_claim": carried}      # a claim carried in: this turn does not score
        ok = daemon._do_single_turn_stream(FS._FakePA(), b"RIFFwav", wake_turn=True, **kw)
    (line,) = FS._msgs(caplog, "FIRST_SOUND")
    caplog.clear()
    return ok, line, writes, posts


def _f(line: str, key: str) -> str:
    return re.search(rf"\b{key}=(\S+)", line).group(1)


def test_a_member_turn_with_every_guard_clear_would_have_played(daemon, monkeypatch, caplog):
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert FS.FIRST_SOUND_RE.match(line), line
    assert (_f(line, "spk"), _f(line, "spk_ms"), _f(line, "media")) == ("1", "190", "0")   # close 99.9, ready 100.09
    assert _f(line, "skip") == "no_bank"            # every guard passed; only the absent bank stops it
    assert _f(line, "reflex") == "none" and _f(line, "reflex_at_ms") == "-1"   # and nothing played


@pytest.mark.parametrize("kw,claim,skip", [
    (dict(media=None), 0.40, "media"),               # the watcher could not say: silent
    (dict(media=True), 0.40, "media"),
    (dict(busy=True), 0.40, "busy"),
    (dict(stop="max_duration"), 0.40, "not_committed"),
    (dict(hour=23), 0.40, "night"),
    (dict(), 0.30, "low_confidence"),                # a member's claim scored under far01: low confidence, not a guest
    (dict(claim_s=0.40), 0.40, "late"),              # verdict ready 500 ms after the close: over 250
])
def test_each_real_input_moves_the_shadow_skip(daemon, monkeypatch, caplog, kw, claim, skip):
    claim_s = kw.pop("claim_s", 0.09)
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, claim), claim_s=claim_s, **kw)
    assert _f(line, "skip") == skip, line


def test_spk_guest_separates_who_from_whether_accepted(daemon):
    g = daemon._spk_guest
    assert g(None) is None and g(daemon._CLAIM_UNSET) is None and g("member_a") is None     # not scored: unknown
    assert g(daemon.SCORED_NO_MATCH) is True                       # nobody in the household matched: a guest
    assert g(_claim(daemon, 0.40)) is False and g(_claim(daemon, 0.30)) is False   # a member's claim, accepted or not
    assert g(_claim(daemon, 0.40, guest=("guest_b", 0.45))) is True                # a named guest is closer
    assert g(_claim(daemon, 0.36, model="resemblyzer")) is None and g(_claim(daemon, float("nan"))) is None


def test_an_under_bar_member_is_counted_low_confidence_and_a_closer_guest_is_a_guest(daemon, monkeypatch, caplog):
    """The shadow must COUNT a low-confidence member as ``low_confidence`` (not hide it under ``guest``)."""
    for score, seconds, guest, skip in ((0.30, 3.0, None, "low_confidence"), (0.300, 2.0, None, "low_confidence"),
                                        (0.40, 3.0, ("guest_b", 0.45), "guest"), (0.40, 3.0, None, "no_bank")):
        _, line, _, _ = _turn(daemon, monkeypatch, caplog,
                              claim_fn=lambda: _claim(daemon, score, seconds=seconds, guest=guest))
        assert _f(line, "skip") == skip, (score, guest, line)


def test_a_listed_silent_member_is_silent_and_counted_as_guest(daemon, monkeypatch, caplog):
    monkeypatch.setenv("REFLEX_SILENT_MEMBERS", "member_x, member_a")
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert _f(line, "skip") == "guest", line
    assert "member_a" not in line                                  # the id is never logged
    monkeypatch.setenv("REFLEX_SILENT_MEMBERS", "member_x")       # positive control: another member is not silenced
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert _f(line, "skip") == "no_bank", line


def test_reflex_mode_off_runs_no_reflex_work_and_logs_a_dash(daemon, monkeypatch, caplog):
    """The default (REFLEX_MODE absent or off): no decide(), no pre-roll RMS, ``skip=-``; the reply is byte-identical."""
    calls = []
    monkeypatch.setattr(daemon, "REFLEX_MODE", "off")
    monkeypatch.setattr(REFLEX, "decide", lambda *a, **k: calls.append("decide") or pytest.fail("decide ran in off"))
    monkeypatch.setattr(daemon, "_rms_dbfs", lambda *a, **k: calls.append("rms") or pytest.fail("rms ran in off"))
    ok, line, writes, posts = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert _f(line, "skip") == "-" and calls == [] and ok is True and len(writes) == 3


def test_reflex_mode_is_read_once_and_defaults_off():
    src = DAEMON.read_text(encoding="utf-8")
    assert src.count('REFLEX_MODE = (os.environ.get("REFLEX_MODE") or "off").strip().lower()') == 1
    assert re.search(r'if REFLEX_MODE not in \("off", "shadow", "on"\):\n\s+REFLEX_MODE = "off"', src)


def test_shadow_mode_claim_is_unknown_and_silent(daemon, monkeypatch, caplog):
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: None)
    assert (_f(line, "spk"), _f(line, "spk_ms")) == ("-1", "-1")
    assert _f(line, "skip") == "guest"               # unknown speaker = guest = silent
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: daemon.SCORED_NO_MATCH)
    assert _f(line, "spk") == "0" and _f(line, "skip") == "guest"


def test_without_the_reflex_module_the_line_says_dash(daemon, monkeypatch, caplog):
    monkeypatch.setattr(daemon, "_reflex", None)
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert _f(line, "skip") == "-" and _f(line, "spk") == "1"


def test_the_forced_switch_is_load_bearing_and_local(daemon):
    """NEGATIVE CONTROL: decide() with the real settings says ``disabled`` (REFLEX_ENABLED is off), so a shadow that did not
    force the switch would log nothing useful. And the forcing never leaks: the settings stay off afterwards."""
    _close(daemon)
    fs = daemon._FirstSound(speculative=False, win="wake")
    assert REFLEX.decide(REFLEX.Ctx(rule="deep", speech_ms=1200), s=REFLEX.settings()).skip == "disabled"
    assert daemon._shadow_reflex_skip(fs) not in ("disabled", "-")
    assert REFLEX.settings().enabled is False


def test_shadow_decide_never_plays_and_never_loads_a_bank(daemon, monkeypatch, caplog):
    for name in ("load_bank", "load_bank_dir", "pick_take", "record", "handshake_block"):
        monkeypatch.setattr(REFLEX, name, lambda *a, **k: pytest.fail("the shadow reached the playback side"))
    ok, line, writes, posts = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    assert _f(line, "skip") == "no_bank"
    src = DAEMON.read_text(encoding="utf-8")
    for forbidden in ("_reflex.load_bank", "_reflex.record(", "_reflex.handshake_block", "_reflex.pick_take",
                      "_ReflexPlayer", "REFLEX_DIR"):
        assert forbidden not in src, forbidden
    assert ok is True and len(writes) == 3          # the reply's three chunks only: no extra player, no extra write


def _effects(daemon, monkeypatch, caplog, mode):
    if mode == "no_module":
        monkeypatch.setattr(daemon, "_reflex", None)
    elif mode == "raising":
        monkeypatch.setattr(REFLEX, "decide", lambda *a, **k: 1 / 0)
        monkeypatch.setattr(daemon, "_spk_verdict", lambda c: 1 / 0)
    registered = []
    real_register = daemon._register_tts_process
    monkeypatch.setattr(daemon, "_register_tts_process", lambda p: (registered.append(p.pid), real_register(p)))
    ok, _, writes, posts = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))
    return ok, [len(w) for w in writes], b"".join(writes), posts, registered, dict(daemon._last_turn_flags)


def test_playback_posts_and_flags_identical_with_the_shadow_on_off_or_broken(daemon, monkeypatch, caplog):
    out = {}
    for mode in ("on", "no_module", "raising"):
        with monkeypatch.context() as m:
            out[mode] = _effects(daemon, m, caplog, mode)
    assert out["on"] == out["no_module"] == out["raising"]
    assert out["on"][0] is True and len(out["on"][1]) == 3 and len(out["on"][3]) == 1


def test_no_text_or_id_reaches_the_new_fields(daemon, monkeypatch, caplog):
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40, guest=("guest_b", 0.2)))
    assert "member_a" not in line and "guest_b" not in line and "0.4" not in line
    assert re.fullmatch(r"-?\d+", _f(line, "spk")) and re.fullmatch(r"-?\d+", _f(line, "spk_ms"))


def test_a_carried_claim_logs_an_unknown_verdict_and_never_counts_as_accepted(daemon, monkeypatch, caplog):
    """A claim carried in (scored on an earlier step) was not scored this turn: spk=-1 and spk_ms=-1, whatever its value, and the
    reflex decision treats the unknown verdict as silence. A claim scored this turn still logs its verdict and time."""
    def never():
        raise AssertionError("a carried claim must not be re-scored")

    for value in (0.40, 0.05):                                    # a claim that WOULD clear the bar, and one under it
        _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=never, carried=_claim(daemon, value))
        assert (_f(line, "spk"), _f(line, "spk_ms")) == ("-1", "-1"), line
        assert _f(line, "skip") != "-" and _f(line, "skip") != "no_bank"      # never "every guard passed"
    _, line, _, _ = _turn(daemon, monkeypatch, caplog, claim_fn=lambda: _claim(daemon, 0.40))   # CONTROL: scored this turn
    assert (_f(line, "spk"), _f(line, "spk_ms"), _f(line, "skip")) == ("1", "190", "no_bank")
