"""The panel daemon's reflex WIRING: REFLEX_MODE off | shadow | on (default off), with the audio device faked.

Pinned here: off changes nothing; shadow decides on the real inputs with the real bank and only LOGS it (``REFLEX_DECISION``);
on plays ONE take through its own UNREGISTERED player; the reply's first PCM write never moves; a reply that arrives while the
reflex plays CUTS it, and one that arrives before it can start DROPS it (``reply_first``); never over a person still making
sound (the post-close mic window); the bank, the voice, the player and the decision may each fail without touching the turn.

Synthetic audio and invented ids only. Negative controls are inline: each rule's test also shows the unguarded case plays.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import logging
import re
import sys
import threading
import time
import types
import wave
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


SH = _by_path("shadow_helpers_for_reflex_wiring", Path(__file__).with_name("test_voice_daemon_shadow_reflex.py"))
FS, REFLEX, RN34 = SH.FS, SH.REFLEX, SH.RN34
TAKE_MS = 400


@pytest.fixture(scope="module")
def daemon():
    return FS._load("zoe_voice_daemon_reflex_wiring_test")


def _wav_bytes(ms: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        n = 24000 * ms // 1000
        w.writeframes((6000 * np.sin(np.arange(n) * 0.05)).astype(np.int16).tobytes())
    return buf.getvalue()


def make_bank(root: Path, voice: str = "af_sky", n: int = 4):
    pairs, takes = [], []
    files = {}
    for i in range(1, n + 1):
        tid = f"mm_{i:02d}"
        raw = _wav_bytes(TAKE_MS + i)               # a distinct file per take
        sha = hashlib.sha256(raw).hexdigest()
        files[tid] = raw
        pairs.append((tid, sha))
        takes.append({"id": tid, "sha256": sha, "dur_ms": TAKE_MS + i})
    man = {"voice": voice, "style_len": None, "takes": takes, "render_date": "2026-11-15"}
    sha8 = REFLEX.manifest_sha8(man)
    man["bank"] = sha8
    d = root / voice / sha8
    d.mkdir(parents=True)
    for tid, raw in files.items():
        (d / f"{tid}.wav").write_bytes(raw)
    (d / "manifest.json").write_text(json.dumps(man))
    return REFLEX.load_bank(voice, str(root), REFLEX.Settings(enabled=True)).bank


@pytest.fixture(autouse=True)
def _fresh(daemon, monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_reflex", REFLEX)
    monkeypatch.setattr(daemon, "_rn34", RN34)
    for k in ("REFLEX_ENABLED", "REFLEX_VOICE", "REFLEX_SCORE_DEADLINE_MS", "ZOE_SPEAKER_RESNET34_TIER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(daemon, "REFLEX_MODE", "off")
    monkeypatch.setattr(daemon, "_REFLEX_ON_ALLOWED", False)   # production: on is BLOCKED (no at-close safety gate)
    monkeypatch.setattr(daemon, "_REFLEX_BANK", None)
    monkeypatch.setattr(daemon, "_REFLEX_STATE", {})
    monkeypatch.setattr(daemon, "_REFLEX_CUR", None)
    monkeypatch.setattr(daemon, "_REFLEX_DONE_VOICE", None)
    daemon._turn_close.clear()
    daemon._TALK.begin()
    daemon._TALK.armed = False
    yield
    daemon._turn_close.clear()


class _Player:
    """The reflex's fake aplay. ``hold`` = it keeps "playing" until terminated (so a reply can arrive mid-take)."""

    def __init__(self, hold=False):
        self.hold, self.buf, self.pid = hold, [], 7001
        self.terminated = threading.Event()
        self.stdin = types.SimpleNamespace(write=self.buf.append, close=lambda: None, flush=lambda: None)

    def poll(self):
        return None if self.hold and not self.terminated.is_set() else 0

    def wait(self, timeout=None):
        if self.hold and not self.terminated.wait(timeout):
            import subprocess
            raise subprocess.TimeoutExpired("aplay", timeout)
        return 0

    def terminate(self):
        self.terminated.set()

    kill = terminate


class _Resp(FS._FakeResp):
    def __init__(self, items, clock, before):
        super().__init__(items, clock)
        self._before = before

    def iter_lines(self, decode_unicode=False):
        if self._before:
            self._before()                      # the real-time hook: let the reflex thread run before the clock moves
        yield from super().iter_lines(decode_unicode)


def _talk(daemon, *, chunks=0, first=99.9, last=100.3):
    t = daemon._TALK
    t.armed, t.opened, t.stopped, t.chunks, t.first_cap, t.last_end = True, True, False, chunks, first, last


def _turn(daemon, monkeypatch, caplog, *, mode, bank=None, hold=False, talk=True, claim=0.40, claim_s=0.09,
          before="join", wire=None, hour=12, wake=True, on_allowed=None, safety_out=None, stamps=None):
    """One streamed wake turn. Returns (ok, FIRST_SOUND line, reply writes, reflex players, reply-start clock, registered).
    ``mode="on"`` exercises the GATED player: it sets ``_REFLEX_ON_ALLOWED`` on this test module's daemon copy (production is
    False, pinned below) unless ``on_allowed`` says otherwise."""
    monkeypatch.setattr(daemon, "REFLEX_MODE", mode)
    monkeypatch.setattr(daemon, "_REFLEX_ON_ALLOWED", (mode == "on") if on_allowed is None else bool(on_allowed))
    monkeypatch.setattr(daemon, "_REFLEX_BANK", bank)
    monkeypatch.setenv("REFLEX_VOICE", "af_sky")
    clock = FS._Clock(100.0)
    writes, posts = FS._stream_turn_setup(daemon, monkeypatch, clock, wire=wire)
    players, reply_first_at = [], []

    def start(rate, ch, width):
        if threading.current_thread().name == "reflex-turn":
            p = _Player(hold=hold)
            players.append(p)
            return p
        reply_first_at.append(clock.t)
        proc = FS._FakeProc(writes)
        if stamps is not None:                  # REAL time of the reply's first PCM write (not the scripted clock)
            sink = proc.stdin.write

            def _write(b):
                stamps.setdefault("first_write", time.perf_counter())
                return sink(b)
            proc.stdin = types.SimpleNamespace(write=_write, flush=lambda: None, close=lambda: None)
        return proc

    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(start_pcm_stream=start))
    if stamps is not None:                      # REAL time the reply path entered its first chunk
        real_feed = daemon._feed_pcm_chunk

        def _feed(aplay, wav_bytes):
            if aplay is None:
                stamps.setdefault("feed", time.perf_counter())
            return real_feed(aplay, wav_bytes)
        monkeypatch.setattr(daemon, "_feed_pcm_chunk", _feed)
    registered = []
    real_register = daemon._register_tts_process
    monkeypatch.setattr(daemon, "_register_tts_process", lambda p: (registered.append(p.pid), real_register(p)))
    SH._close(daemon, hour=hour)
    if talk:
        _talk(daemon)

    def _hook():
        ctl = daemon._REFLEX_CUR
        if ctl is None:
            return
        if not talk:
            clock.t += 0.4              # real time passes with no mic window: the decision deadline is reached
        if before == "join":
            ctl._thread.join(timeout=3)
        elif before == "playing":
            for _ in range(300):
                if ctl.started_at is not None or not ctl._thread.is_alive():
                    break
                threading.Event().wait(0.01)

    items = FS._wire() if wire is None else wire
    monkeypatch.setattr(daemon.requests, "post", lambda url, json=None, **kw: _Resp(items, clock, _hook), raising=False)

    def _score(_wav):
        clock.t += claim_s
        return SH._claim(daemon, claim)

    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", _score)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        ok = daemon._do_single_turn_stream(FS._FakePA(), b"RIFFwav", wake_turn=wake)
    (line,) = FS._msgs(caplog, "FIRST_SOUND")
    dec = FS._msgs(caplog, "REFLEX_DECISION")
    if safety_out is not None:
        safety_out.extend(FS._msgs(caplog, "REFLEX_SAFETY"))
    caplog.clear()
    return ok, line, writes, players, reply_first_at, registered, dec


def _f(line: str, key: str) -> str:
    return re.search(rf"\b{key}=(\S+)", line).group(1)


@pytest.fixture
def bank(tmp_path):
    return make_bank(tmp_path / "bank")


# ── off: nothing runs ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_off_is_the_default_and_changes_nothing(daemon, monkeypatch, caplog, bank):
    assert daemon.REFLEX_MODE == "off"
    ok, line, writes, players, first, _, dec = _turn(daemon, monkeypatch, caplog, mode="off", bank=bank)
    assert ok and players == [] and dec == [] and len(writes) == 3
    assert (_f(line, "reflex"), _f(line, "reflex_at_ms"), _f(line, "reflex_cut"), _f(line, "gap_ms")) == ("none", "-1", "0", "-1")
    assert daemon._REFLEX_CUR is None and FS.FIRST_SOUND_RE.match(line), line


# ── on is BLOCKED: no at-close crisis / medical / refusal gate exists ───────────────────────────────────────────────────

def test_on_is_blocked_in_the_source():
    """The contract pin: the reflex must not be able to play before a crisis / medical / refusal reply, and nothing at the
    close can know. ``_REFLEX_ON_ALLOWED`` is False in the shipped source; changing it is a reviewed code change."""
    src = DAEMON.read_text(encoding="utf-8")
    assert src.count("\n_REFLEX_ON_ALLOWED = False\n") == 1 and "_REFLEX_ON_ALLOWED = True" not in src
    assert src.count("self._play(d)") == 1                         # ONE call site of the player, and it is gated
    guard = next(x for x in src.splitlines() if x.strip().startswith("if d.kind == \"heard\" and self.mode == \"on\""))
    assert "and _REFLEX_ON_ALLOWED" in guard.split("#")[0], guard     # in the CODE of the condition, not its comment


def test_reflex_mode_on_never_plays_and_logs_shadow(daemon, monkeypatch, caplog, bank):
    """REFLEX_MODE=on with the production constant: the turn decides and logs as shadow, and NO player is ever started."""
    ok, line, writes, players, first, registered, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank,
                                                              on_allowed=False)
    assert ok and players == [] and len(writes) == 3
    assert len(dec) == 1 and "mode=shadow" in dec[0] and "kind=heard" in dec[0], dec   # would have played: counted, silent
    assert _f(line, "reflex") == "none" and _f(line, "reflex_at_ms") == "-1"
    # positive control: the very same turn with the gate constant True plays one take (so the block is what stopped it)
    _, _, _, players2, _, _, dec2 = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank, on_allowed=True)
    assert len(players2) == 1 and "mode=on" in dec2[0]


def test_the_blocked_on_mode_says_so_at_start(daemon, monkeypatch, caplog):
    monkeypatch.setattr(daemon, "REFLEX_MODE", "on")
    monkeypatch.setattr(daemon, "PANEL_PLATFORM", "pi", raising=False)
    monkeypatch.setattr(REFLEX, "load_bank", lambda *a, **k: REFLEX.LoadResult(None, "missing"))
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._reflex_init()
    assert any("REFLEX_MODE=on is BLOCKED" in r.getMessage() for r in caplog.records)
    assert daemon._reflex_effective_mode("on") == "shadow" and daemon._reflex_effective_mode("shadow") == "shadow"
    assert daemon._reflex_effective_mode("off") == "off" and daemon._reflex_effective_mode("loud") == "off"


# ── the shadow's SAFETY COUNT (contract: shadow must COUNT crisis / medical / refusal turns it would have played on) ──────

def _wire_with_safety(value):
    items = list(FS._wire())
    done = json.loads(items[-1][1])
    if value is not None:
        done["safety"] = value
    items[-1] = (items[-1][0], json.dumps(done).encode())
    return items


@pytest.mark.parametrize("value,cls", [("distress", "distress"), ("medical", "medical"), ("refusal", "refusal"),
                                       ("child_floor", "child_floor"), ("none", "none"),
                                       (None, "unknown"), ("Help me please", "unknown"), (7, "unknown")])
def test_shadow_counts_a_would_play_turn_by_the_servers_safety_class(daemon, monkeypatch, caplog, bank, value, cls):
    """A would-play shadow turn logs ONE REFLEX_SAFETY line carrying the done frame's class (closed enum; anything else,
    including free text, is ``unknown``). Today's server sends no ``safety`` key, so live lines say ``unknown``."""
    out = []
    _, line, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="shadow", bank=bank,
                                           wire=_wire_with_safety(value), safety_out=out)
    assert players == [] and "kind=heard" in dec[0]
    assert out == [f"REFLEX_SAFETY mode=shadow would_play=1 class={cls}"], out
    assert "Help" not in " ".join(out)


def test_the_safety_count_also_runs_when_on_is_blocked_and_never_plays(daemon, monkeypatch, caplog, bank):
    out = []
    _, _, _, players, _, _, _ = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank, on_allowed=False,
                                      wire=_wire_with_safety("distress"), safety_out=out)
    assert players == [] and out == ["REFLEX_SAFETY mode=shadow would_play=1 class=distress"]


def test_a_suppressed_turn_is_counted_with_would_play_0_and_off_logs_nothing(daemon, monkeypatch, caplog, bank):
    out = []
    _turn(daemon, monkeypatch, caplog, mode="shadow", bank=bank, hour=23, wire=_wire_with_safety("medical"), safety_out=out)
    assert out == ["REFLEX_SAFETY mode=shadow would_play=0 class=medical"]
    out2 = []
    _turn(daemon, monkeypatch, caplog, mode="off", bank=bank, wire=_wire_with_safety("medical"), safety_out=out2)
    assert out2 == []                                              # off: nothing reflex-related runs


def test_an_unknown_mode_value_is_off(daemon):
    src = DAEMON.read_text(encoding="utf-8")
    assert 'if REFLEX_MODE not in ("off", "shadow", "on"):' in src and 'REFLEX_MODE = "off"' in src
    assert "REFLEX_ENABLED" not in re.sub(r"#.*|\"\"\"[\s\S]*?\"\"\"", "", src[src.index("def _reflex_ctx"):])   # one switch only


# ── shadow: decides and logs, plays nothing ──────────────────────────────────────────────────────────────────────────────────

def test_shadow_logs_the_real_decision_and_plays_nothing(daemon, monkeypatch, caplog, bank):
    ok, line, writes, players, first, registered, dec = _turn(daemon, monkeypatch, caplog, mode="shadow", bank=bank)
    assert ok and players == [] and len(writes) == 3                       # not one extra player, not one extra write
    (d,) = dec
    assert re.fullmatch(r"REFLEX_DECISION mode=shadow kind=heard skip=- take=mm_0[1-4] decided_ms=190 talk_obs_ms=\d+ "
                        r"noise_db=-?\d+ spk=1 media=0 win=wake bank=[0-9a-f]{8}", d), d
    assert (_f(line, "reflex"), _f(line, "reflex_at_ms")) == ("none", "-1") and _f(line, "skip") == "-"   # "-" = would have played
    assert FS.FIRST_SOUND_RE.match(line)


@pytest.mark.parametrize("kw,skip", [
    (dict(talk=False), "talking"),                 # nobody listened after the close: unknown, silent
    (dict(claim=0.30), "low_confidence"),          # a member's claim scored under the far01 bar (not hidden as guest)
    (dict(hour=23), "night"),
    (dict(claim_s=0.40), "late"),                  # the decision ready 500 ms after the close (deadline 250)
])
def test_shadow_names_each_guard(daemon, monkeypatch, caplog, bank, kw, skip):
    _, line, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="shadow", bank=bank, **kw)
    assert f"skip={skip} " in dec[0] and _f(line, "skip") == skip and players == []


def test_without_a_bank_a_clean_turn_says_no_bank(daemon, monkeypatch, caplog):
    _, _, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=None)
    assert "skip=no_bank" in dec[0] and players == []


def test_a_wrong_or_unknown_voice_is_silence(daemon, monkeypatch, caplog, bank):
    monkeypatch.setattr(daemon, "_REFLEX_DONE_VOICE", "bf_other")
    _, _, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert "skip=voice_mismatch" in dec[0] and players == []
    monkeypatch.setattr(daemon, "_REFLEX_DONE_VOICE", None)
    monkeypatch.setattr(daemon, "_reflex_current_voice", lambda: None)       # unknown voice: never the wrong one
    _, _, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert "skip=voice_mismatch" in dec[0] and players == []


def test_a_done_frame_voice_is_validated(daemon):
    daemon._reflex_note_voice("../etc")
    assert daemon._REFLEX_DONE_VOICE is None
    daemon._reflex_note_voice(7)
    assert daemon._REFLEX_DONE_VOICE is None
    daemon._reflex_note_voice("af_sky")
    assert daemon._REFLEX_DONE_VOICE == "af_sky"


# ── on: one take, its own unregistered player, the reply never moves ─────────────────────────────────────────────────────────

def test_on_plays_one_take_in_an_unregistered_player(daemon, monkeypatch, caplog, bank):
    ok, line, writes, players, first, registered, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert ok and len(players) == 1 and len(writes) == 3
    (p,) = players
    assert len(p.buf) == 1 and p.buf[0] in {t.pcm for t in bank.takes.values()}      # exactly one whole bank take
    assert 4242 in registered and p.pid not in registered                              # only the REPLY's player is registered
    assert _f(line, "reflex") == "heard" and int(_f(line, "reflex_at_ms")) == 190
    assert int(_f(line, "reflex_dur_ms")) in {TAKE_MS + i for i in range(1, 5)} and _f(line, "reflex_cut") == "0"
    assert _f(line, "skip") == "-" and re.fullmatch(r"\d+", _f(line, "gap_ms"))
    assert FS.FIRST_SOUND_RE.match(line.replace("reflex=heard", "reflex=none").replace(
        f"reflex_at_ms={_f(line, 'reflex_at_ms')}", "reflex_at_ms=-1").replace(
        f"reflex_dur_ms={_f(line, 'reflex_dur_ms')}", "reflex_dur_ms=-1").replace(
        f"gap_ms={_f(line, 'gap_ms')}", "gap_ms=-1")), line                              # the shape is unchanged


def test_the_replys_first_write_does_not_move(daemon, monkeypatch, caplog, bank):
    """THE number: the reply's first PCM write (scripted clock) and every reply byte are identical with the reflex off, shadow
    and on. NEGATIVE CONTROL below: a wait for the reflex (the old H3 rule) would move it."""
    out = {}
    for mode in ("off", "shadow", "on"):
        with monkeypatch.context() as m:
            _, line, writes, _, first, _, _ = _turn(daemon, m, caplog, mode=mode, bank=bank)
            out[mode] = (first, b"".join(writes), _f(line, "server_first_ms"), _f(line, "post_ms"))
    assert out["off"] == out["shadow"] == out["on"]
    ctl_wait = REFLEX.ordering_wait_ms(100.0, TAKE_MS, 100.0 + 0.19)
    assert ctl_wait > 0       # control: had the daemon applied the H3 wait the reply would have been held this long


def _first_write_delay_s(daemon, monkeypatch, caplog, bank, mode, *, plant=None):
    """REAL seconds from the reply path entering its first chunk to the reply's first PCM write. ``plant`` wraps the
    controller's note_reply (the negative controls)."""
    stamps = {}
    with monkeypatch.context() as m:
        if plant is not None:
            real = daemon._ReflexTurn.note_reply
            m.setattr(daemon._ReflexTurn, "note_reply", lambda self: plant(lambda: real(self)))
        _turn(daemon, m, caplog, mode=mode, bank=bank, stamps=stamps)
    return stamps["first_write"] - stamps["feed"]


def test_the_reply_path_never_waits_on_the_reflex_up_to_its_first_pcm_write(daemon, monkeypatch, caplog, bank):
    """Measured to the reply's FIRST PCM WRITE in real time, for off, shadow and on: under 50 ms each (it is pure
    in-memory work here). NEGATIVE CONTROLS, run in the same test: a 300 ms sleep planted in note_reply, and note_reply
    taking a lock another thread holds for 300 ms (what the old lock-taking note_reply could do), are both CAUGHT."""
    for mode in ("off", "shadow", "on"):
        assert _first_write_delay_s(daemon, monkeypatch, caplog, bank, mode) < 0.05, mode
    slept = _first_write_delay_s(daemon, monkeypatch, caplog, bank, "shadow",
                                 plant=lambda real: (time.sleep(0.3), real()))
    assert slept >= 0.25, slept                                   # control 1: a planted sleep is visible
    held = threading.Lock()

    def locked(real):
        with held:
            real()

    held.acquire()
    threading.Timer(0.3, held.release).start()
    blocked = _first_write_delay_s(daemon, monkeypatch, caplog, bank, "shadow", plant=locked)
    assert blocked >= 0.25, blocked                               # control 2: a held lock in note_reply is visible


def test_note_reply_only_sets_an_event_no_lock_no_subprocess(daemon, monkeypatch, bank):
    """The reply path's ONLY reflex action is Event.set: it never touches the player (poll / terminate / wait) or a lock."""
    fs, ctl = _controller(daemon, monkeypatch, bank)

    class _Boom:
        def __getattr__(self, name):
            pytest.fail(f"the reply path touched the reflex player: {name}")

    ctl._proc = _Boom()
    ctl.note_reply()
    assert ctl._reply.is_set() and not ctl.cut
    import inspect
    body = inspect.getsource(daemon._ReflexTurn.note_reply)
    for banned in ("_lock", "poll(", "terminate(", "kill(", "wait(", "_proc"):
        assert banned not in body.split('"""')[-1], banned


def test_off_does_no_reflex_work_on_the_first_audio_path(daemon, monkeypatch, caplog, bank):
    """REFLEX_MODE off: the first-audio path never calls into the reflex (not even the no-op hook)."""
    monkeypatch.setattr(daemon, "_reflex_note_reply", lambda: pytest.fail("reflex hook ran with REFLEX_MODE off"))
    ok, _, writes, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="off", bank=bank)
    assert ok and len(writes) == 3 and players == [] and dec == []
    src = DAEMON.read_text(encoding="utf-8")
    calls = [ln for ln in src.splitlines() if ln.strip().startswith("_reflex_note_reply()")]
    assert len(calls) == 2
    for i, ln in enumerate(src.splitlines()):
        if ln.strip().startswith("_reflex_note_reply()"):
            assert 'if REFLEX_MODE != "off":' in src.splitlines()[i - 1], i


def test_a_reply_during_the_take_cuts_the_reflex_and_is_never_delayed(daemon, monkeypatch, caplog, bank):
    ok, line, writes, players, first, _, _ = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank, hold=True,
                                                   before="playing")
    (p,) = players
    assert ok and p.terminated.is_set() and len(writes) == 3 and len(p.buf) == 1
    assert _f(line, "reflex") == "heard" and _f(line, "reflex_cut") == "1" and _f(line, "gap_ms") == "0"
    base = _turn(daemon, monkeypatch, caplog, mode="off", bank=bank)
    assert first == base[4] and b"".join(writes) == b"".join(base[2])         # the reply started exactly when it would have


def test_budget_one_reflex_per_gap(daemon, monkeypatch, caplog, bank):
    _, _, _, p1, _, _, _ = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert len(p1) == 1
    _, line, _, p2, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)      # moments later
    assert p2 == [] and "skip=budget" in dec[0]


def test_a_speculative_turn_never_gets_a_reflex(daemon, monkeypatch):
    fs = daemon._FirstSound(speculative=True)
    monkeypatch.setattr(daemon, "REFLEX_MODE", "on")
    daemon._reflex_begin(fs)
    assert fs.reflex is None and daemon._REFLEX_CUR is None
    daemon._turn_close.update(t=5.0, rule="deep", ms=640, seq=9)
    plain = daemon._FirstSound(speculative=False)
    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(start_pcm_stream=lambda *a: pytest.fail("player")))
    monkeypatch.setattr(daemon._ReflexTurn, "start", lambda self: None)      # control: a plain turn DOES get a controller
    daemon._reflex_begin(plain)
    assert plain.reflex is not None
    daemon._reflex_end(plain)
    assert daemon._REFLEX_CUR is None


# ── the late-skip, on the controller itself ──────────────────────────────────────────────────────────────────────────────────

def _controller(daemon, monkeypatch, bank, *, mode="on"):
    daemon._turn_close.update(t=99.9, rule="deep", ms=640, seq=3, committed=True, busy=False, speech_ms=1200, idle_s=60.0,
                              prev_q=False, hour=12, media=False, media_age_ms=10, frames=[SH.QUIET] * 40, skip=20)
    monkeypatch.setattr(daemon, "_fs_now", lambda: 100.1)
    monkeypatch.setattr(daemon, "REFLEX_MODE", mode)
    monkeypatch.setattr(daemon, "_REFLEX_ON_ALLOWED", mode == "on")      # the gated player's mechanics (production: False)
    monkeypatch.setenv("REFLEX_VOICE", "af_sky")
    fs = daemon._FirstSound(speculative=False, win="wake")
    fs.speaker(SH._claim(daemon, 0.4), 100.05)
    _talk(daemon)
    return fs, daemon._ReflexTurn(fs, mode, bank)


def test_a_reply_before_the_decision_drops_the_reflex(daemon, monkeypatch, bank):
    fs, ctl = _controller(daemon, monkeypatch, bank)
    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(start_pcm_stream=lambda *a: pytest.fail("player")))
    ctl.note_reply()
    ctl._run()
    assert ctl.skip == "reply_first" and ctl.kind == "none" and ctl.started_at is None
    fs2, ctl2 = _controller(daemon, monkeypatch, bank)                 # NEGATIVE CONTROL: no reply, the same turn plays
    started = []
    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(
        start_pcm_stream=lambda *a: (started.append(1), _Player())[1]))
    ctl2._run()
    assert ctl2.kind == "heard" and started == [1]


def test_a_reply_while_the_player_spawns_kills_it_unwritten(daemon, monkeypatch, bank):
    fs, ctl = _controller(daemon, monkeypatch, bank)
    p = _Player()

    def spawn(*a):
        ctl.note_reply()                                              # the reply's first audio lands during the spawn
        return p

    monkeypatch.setattr(daemon, "_PLATFORM", types.SimpleNamespace(start_pcm_stream=spawn))
    ctl._run()
    assert ctl.skip == "reply_first" and ctl.kind == "none" and p.buf == [] and p.terminated.is_set()


def test_the_turn_ending_first_drops_the_decision(daemon, monkeypatch, bank):
    fs, ctl = _controller(daemon, monkeypatch, bank)
    ctl.end()
    ctl._run()
    assert ctl.kind == "none" and ctl.skip == "late"


# ── never over a person still making sound ───────────────────────────────────────────────────────────────────────────────────

def test_peek_states(daemon):
    daemon._turn_close.update(t=100.0)
    t = daemon._TALK
    t.armed, t.opened, t.stopped, t.chunks, t.first_cap, t.last_end = True, True, False, 0, None, None
    assert t.peek(100.5) == (None, 0)                                   # the mic has not produced a chunk yet
    t.first_cap, t.last_end = 100.0, 100.16
    assert t.peek(100.2) == (False, 160)                                # >= 160 ms covered, fresh, no speech
    t.last_end = 100.12
    assert t.peek(100.2)[0] is None                                     # under 160 ms covered: not enough to say
    t.last_end = 100.16
    assert t.peek(100.9)[0] is None                                     # its newest chunk is stale (> 300 ms old)
    t.chunks = 1
    assert t.peek(100.2)[0] is True                                     # one speech chunk is enough: talking
    t.chunks, t.opened = 0, False
    assert t.peek(100.2) == (None, 0)                                   # no monitor mic (barge-in off, mic not open)
    t.opened, t.stopped = True, True
    assert t.peek(100.2) == (None, 0)                                   # a first sound already ended the window
    t.stopped, t.armed = False, False
    assert t.peek(100.2) == (None, 0)


def test_talking_after_the_close_is_silence_and_so_is_not_knowing(daemon, monkeypatch, caplog, bank):
    # talking now: a mic saw speech after the close
    with monkeypatch.context() as m:
        m.setattr(daemon, "_TALK", daemon._TALK)
        _, line, _, players, _, _, dec = _turn_talk(daemon, m, caplog, bank, chunks=2)
        assert "skip=talking" in dec[0] and players == []
    with monkeypatch.context() as m:                                    # NEGATIVE CONTROL: the same turn, no speech heard
        _, line, _, players, _, _, dec = _turn_talk(daemon, m, caplog, bank, chunks=0)
        assert "skip=-" in dec[0] and len(players) == 1


def _turn_talk(daemon, monkeypatch, caplog, bank, *, chunks):
    real = daemon._TALK.peek

    def peek(now):
        _talk(daemon, chunks=chunks)
        return real(now)

    monkeypatch.setattr(daemon._TALK, "peek", peek)
    return _turn(daemon, monkeypatch, caplog, mode="on", bank=bank, talk=False)


# ── failures never touch the turn ────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("what", ["no_module", "decide_raises", "player_raises", "stdin_raises", "ctx_raises"])
def test_a_broken_reflex_never_changes_the_turn(daemon, monkeypatch, caplog, bank, what):
    base = _turn(daemon, monkeypatch, caplog, mode="off", bank=bank)
    with monkeypatch.context() as m:
        if what == "no_module":
            m.setattr(daemon, "_reflex", None)
        elif what == "decide_raises":
            m.setattr(REFLEX, "decide", lambda *a, **k: 1 / 0)
        elif what == "ctx_raises":
            m.setattr(daemon, "_reflex_ctx", lambda *a, **k: 1 / 0)
        elif what == "player_raises":
            m.setattr(_Player, "__init__", lambda self, hold=False: 1 / 0)
        else:
            m.setattr(_Player, "poll", lambda self: 0)
            orig = _Player.__init__

            def init(self, hold=False):
                orig(self, hold)
                self.stdin = types.SimpleNamespace(write=lambda b: 1 / 0, close=lambda: None, flush=lambda: None)

            m.setattr(_Player, "__init__", init)
        ok, line, writes, players, first, _, _ = _turn(daemon, m, caplog, mode="on", bank=bank)
    assert ok is True and b"".join(writes) == b"".join(base[2]) and first == base[4]
    assert _f(line, "reflex") == "none"
    if what in ("player_raises", "stdin_raises"):
        assert _f(line, "skip") == "player_failed"


def test_the_bank_loader_fails_silent(daemon, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(daemon, "REFLEX_MODE", "shadow")
    monkeypatch.setenv("REFLEX_VOICE", "af_sky")
    monkeypatch.setenv("REFLEX_DIR", str(tmp_path / "none"))
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._reflex_init()
    assert daemon._REFLEX_BANK is None and any("REFLEX_BANK result=missing" in r.getMessage() for r in caplog.records)
    make_bank(tmp_path / "good")
    monkeypatch.setenv("REFLEX_DIR", str(tmp_path / "good"))
    daemon._reflex_init()
    assert daemon._REFLEX_BANK is not None and len(daemon._REFLEX_BANK.takes) == 4
    monkeypatch.setattr(daemon, "REFLEX_MODE", "off")                  # NEGATIVE CONTROL: off never touches the disk
    monkeypatch.setattr(daemon, "_REFLEX_BANK", None)
    monkeypatch.setattr(REFLEX, "load_bank", lambda *a, **k: pytest.fail("loaded in off"))
    daemon._reflex_init()
    assert daemon._REFLEX_BANK is None


def test_no_voice_means_no_bank_not_a_guess(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "REFLEX_MODE", "on")
    monkeypatch.delenv("REFLEX_VOICE", raising=False)
    daemon._reflex_init()
    assert daemon._REFLEX_BANK is None


# ── the noise input ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_floor_is_the_quietest_chunk_not_the_wake_word(daemon):
    loud = (np.sin(np.arange(1280) * 0.3) * 12000).astype(np.int16).tobytes()    # a wake word at conversational level
    pause = np.full(1280, 20, dtype=np.int16).tobytes()                          # the room before it
    frames = [pause, pause, loud, loud, loud, loud]
    floor = daemon._rms_floor_dbfs(frames, 6)
    assert floor < -60.0                                       # the pause decides: far under the -40 dBFS bar
    whole = 20 * np.log10(np.sqrt(np.mean(np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float64) ** 2)) / 32768.0)
    assert whole > -40.0                                       # NEGATIVE CONTROL: the old whole-window RMS says "noisy"
    steady = [loud] * 6
    assert daemon._rms_floor_dbfs(steady, 6) > -40.0            # steady music / TV keeps even the quietest chunk high
    assert daemon._rms_floor_dbfs([], 3) is None and daemon._rms_floor_dbfs(frames, 0) is None


def test_the_reflex_player_is_never_registered_as_playback():
    src = DAEMON.read_text(encoding="utf-8")
    body = src[src.index("class _ReflexTurn"):src.index("def _reflex_begin")]
    assert "_register_tts_process" not in body and "_active_playback" not in body


def test_a_listed_member_never_gets_the_reflex_and_the_id_is_never_logged(daemon, monkeypatch, caplog, bank):
    """The panel has no age or role for a member; REFLEX_SILENT_MEMBERS is the owner's explicit list (the children)."""
    monkeypatch.setenv("REFLEX_SILENT_MEMBERS", " other_one, member_a ")
    _, line, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert "skip=guest" in dec[0] and players == [] and "member_a" not in dec[0] + line
    monkeypatch.setenv("REFLEX_SILENT_MEMBERS", "someone_else")                      # NEGATIVE CONTROL: not listed, it plays
    _, line, _, players, _, _, dec = _turn(daemon, monkeypatch, caplog, mode="on", bank=bank)
    assert "skip=-" in dec[0] and len(players) == 1
    monkeypatch.delenv("REFLEX_SILENT_MEMBERS")
    assert daemon._reflex_silent_members() == frozenset()
