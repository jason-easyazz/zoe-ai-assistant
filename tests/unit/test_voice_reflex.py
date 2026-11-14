"""The panel reflex's pure decision logic (scripts/setup/zoe_voice_reflex.py): every silence rule is one named skip, take
rotation never repeats, the budget, the handshake carries no text, the H3 ordering wait, the bank loader fails silent.

Negative controls are automated: ``test_each_guard_is_load_bearing`` deletes each skip's ``return`` from a copy of the module
and shows the matching case stops producing that skip.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import random
import re
import struct
import sys
import types
import wave
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_MOD = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_reflex.py"


def _load(name: str = "zoe_voice_reflex_under_test", source: str | None = None):
    if source is None:
        spec = importlib.util.spec_from_file_location(name, _MOD)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    mod = types.ModuleType(name)
    mod.__file__ = str(_MOD)
    sys.modules[name] = mod
    try:
        exec(compile(source, str(_MOD), "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


R = _load()
ON = R.Settings(enabled=True)
TAKES = ("mm_01", "mm_02", "mm_03", "mm_04")
#: the otherwise-clean context: every explicit input states its safe value
CLEAN = dict(rule="deep", speech_ms=1200, turn_kind="wake", local_hour=12, bank_voice="af_sky", current_voice="af_sky",
             takes=TAKES, committed=True, guest=False, speaker_conf=0.9, media_active=False)


def ok_ctx(**kw):
    base = dict(CLEAN)
    base.update(kw)
    return R.Ctx(**base)


# ── the gate: one case per skip reason ───────────────────────────────────────────────────────────────────────────────────────
SKIP_CASES = {
    "disabled": (dict(), R.Settings(enabled=False)),
    "error": (dict(speech_ms=None), ON),
    "not_committed": (dict(committed=None), ON),
    "media": (dict(media_active=None), ON),
    "guest": (dict(guest=True), ON),
    "low_confidence": (dict(speaker_conf=0.5), ON),
    "not_wake": (dict(turn_kind="followup"), ON),
    "capped": (dict(rule="max_duration"), ON),
    "short": (dict(speech_ms=400), ON),
    "muted": (dict(muted_ms=80), ON),
    "busy": (dict(other_player=True), ON),
    "noisy": (dict(noise_dbfs=-20.0), ON),
    "night": (dict(local_hour=23), ON),
    "idle_greeting": (dict(idle_h=5.0), ON),
    "prev_question": (dict(turn_kind="conversation", prev_question=True), replace(ON, chain_enabled=True)),
    "budget": (dict(since_last_s=12.0), ON),
    "no_bank": (dict(takes=("mm_01", "mm_02")), ON),
    "voice_mismatch": (dict(current_voice="bf_emma"), ON),
}


def test_every_skip_reason_has_a_case():
    assert set(SKIP_CASES) == set(R.SKIPS)


def test_the_base_context_is_heard():
    d = R.decide(ok_ctx(), s=ON, seed=1)
    assert d.kind == "heard" and d.take in TAKES and d.skip == "-"


@pytest.mark.parametrize("skip", sorted(SKIP_CASES))
def test_each_skip(skip):
    kw, s = SKIP_CASES[skip]
    d = R.decide(ok_ctx(**kw), s=s, seed=1)
    assert (d.kind, d.take, d.skip) == ("none", None, skip)


#: one otherwise-clean case per guard SITE (a ``# G:<site>`` marker in the module), so guards that share a reason are proven
#: one by one. ``except`` is the decide() handler: its mutant re-raises and is killed by test_decide_never_raises below.
SITE_CASES = {
    "disabled": (dict(), R.Settings(enabled=False)),
    "input": (dict(idle_h=float("nan")), ON),             # unvalidated, NaN slips past ``idle_h >= 4``
    "not_committed": (dict(committed=None), ON),
    "not_wake": (dict(turn_kind="followup"), ON),
    "capped": (dict(rule="max_duration"), ON),
    "short": (dict(speech_ms=400), ON),
    "muted": (dict(muted_ms=80), ON),
    "busy": (dict(other_player=True), ON),
    "media": (dict(media_active=None), ON),
    "noisy": (dict(noise_dbfs=-20.0), ON),
    "night": (dict(local_hour=23), ON),
    "idle_greeting": (dict(idle_h=5.0), ON),
    "guest": (dict(guest=True), ON),
    "conf_missing": (dict(speaker_conf=None), ON),
    "conf_low": (dict(speaker_conf=0.5), ON),
    "prev_question": (dict(turn_kind="conversation", prev_question=True), replace(ON, chain_enabled=True)),
    "budget_gap": (dict(since_last_s=12.0), ON),
    "budget_chain": (dict(turn_kind="followup", chain_turns_since=1), replace(ON, chain_enabled=True)),
    "no_bank": (dict(takes=("mm_01", "mm_02")), ON),
    "no_take": (dict(takes=("BAD 1", "BAD 2", "BAD 3")), ON),
    "voice_mismatch": (dict(current_voice="bf_emma"), ON),
}
_SITE_RE = re.compile(r"# G:([a-z_]+)")


def _sites() -> "dict[str, int]":
    out = {}
    for i, line in enumerate(_MOD.read_text(encoding="utf-8").splitlines()):
        m = _SITE_RE.search(line)
        if m:
            assert m.group(1) not in out, f"duplicate guard marker {m.group(1)}"
            out[m.group(1)] = i
    return out


def _mutant(site: str):
    lines = _MOD.read_text(encoding="utf-8").splitlines()
    i = _sites()[site]
    if site == "except":
        i += 1                                             # the handler's return is on the next line
        new = re.sub(r"return .*$", "raise", lines[i])
    else:
        new = re.sub(r"return [^#]*", "pass  ", lines[i])
    assert new != lines[i]
    lines[i] = new
    return _load(f"zoe_voice_reflex_mut_{site}", "\n".join(lines) + "\n")


def test_every_guard_site_has_a_case():
    assert set(_sites()) == set(SITE_CASES) | {"except"}
    assert len(_sites()) == 22


@pytest.mark.parametrize("site", sorted(SITE_CASES))
def test_each_guard_site_is_load_bearing(site):
    """Each guard SITE on its own: its case is silent as written, and with only that site's ``return`` removed the same case
    PLAYS (``heard``). A removed guard is proven to let a sound through."""
    kw, s = SITE_CASES[site]
    assert R.decide(ok_ctx(**kw), s=s, seed=1).kind == "none"
    M = _mutant(site)
    ms = M.Settings(**{f: getattr(s, f) for f in s.__dataclass_fields__})
    base = dict(CLEAN)
    base.update(kw)
    d = M.decide(M.Ctx(**base), s=ms, seed=1)
    assert (d.kind, d.skip) == ("heard", "-")
    if base["takes"] == TAKES:
        assert d.take in TAKES


def test_decide_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(R, "is_night", boom)
    assert R.decide(ok_ctx(), s=ON) == R.Decision("none", None, "error")
    M = _mutant("except")                                  # the control: without the handler the same fault escapes
    monkeypatch.setattr(M, "is_night", boom)
    with pytest.raises(RuntimeError):
        M.decide(M.Ctx(**CLEAN), s=M.Settings(enabled=True))


_GARBAGE = (None, "5", True, float("nan"), float("inf"), float("-inf"), [1], -1)


@pytest.mark.parametrize("field,value", [(f, v) for f in ("speech_ms", "muted_ms", "idle_h") for v in _GARBAGE]
                         + [("noise_dbfs", v) for v in _GARBAGE if v != -1]
                         + [(f, v) for f in ("since_last_s", "chain_turns_since") for v in _GARBAGE if v is not None]
                         + [("local_hour", v) for v in _GARBAGE + (7.5, 24, -1)])
def test_garbage_numeric_inputs_are_silence(field, value):
    s = replace(ON, chain_enabled=True)
    kw = {field: value, "turn_kind": "followup"} if field == "chain_turns_since" else {field: value}
    assert R.decide(ok_ctx(**kw), s=s) == R.Decision("none", None, "error")


def test_the_optional_numbers_accept_none():
    assert R.decide(ok_ctx(since_last_s=None, chain_turns_since=None), s=ON).kind == "heard"


def test_the_explicit_inputs_fail_silent():
    for v in (None, False, 1, "yes"):
        assert R.decide(ok_ctx(committed=v), s=ON).skip == "not_committed"     # exactly True, nothing else
    for v in (None, True, 0, "no"):
        assert R.decide(ok_ctx(media_active=v), s=ON).skip == "media"
    for v in (None, True, 0):
        assert R.decide(ok_ctx(guest=v), s=ON).skip == "guest"                  # unknown speaker = guest
    allow = replace(ON, for_guests=True)
    assert R.decide(ok_ctx(guest=True), s=allow).kind == "heard"
    assert R.decide(ok_ctx(guest=None, speaker_conf=0.8), s=allow).kind == "heard"
    assert R.decide(ok_ctx(guest=None, speaker_conf=None), s=allow).skip == "low_confidence"  # still needs a score
    # speaker confidence: missing, low, NaN, inf or a non-number is silence, always
    for v in (None, 0.69, float("nan"), float("inf"), "0.9", True, [0.9]):
        assert R.decide(ok_ctx(speaker_conf=v), s=ON).skip == "low_confidence"
    assert R.decide(ok_ctx(speaker_conf=0.70), s=ON).kind == "heard"
    assert R.decide(ok_ctx(speaker_conf=1), s=ON).kind == "heard"
    assert R.decide(ok_ctx(speaker_conf=0.75), s=replace(ON, min_speaker_conf=0.8)).skip == "low_confidence"
    # the safe defaults: a bare Ctx (the daemon stated nothing) is silent
    bare = R.Ctx(rule="deep", speech_ms=1200, bank_voice="af_sky", current_voice="af_sky", takes=TAKES)
    assert R.decide(bare, s=ON).skip == "not_committed"


def test_more_silence_rules():
    for rule in ("no_speech",):
        assert R.decide(ok_ctx(rule=rule), s=ON).skip == "short"
    assert R.decide(ok_ctx(rule="banana"), s=ON).skip == "capped"        # unknown rule: cannot vouch for the close
    assert R.decide(ok_ctx(capped=True), s=ON).skip == "capped"
    assert R.decide(ok_ctx(turn_kind="barge"), s=replace(ON, chain_enabled=True)).skip == "not_wake"
    assert R.decide(ok_ctx(noise_dbfs=float("nan")), s=ON).skip == "error"
    assert R.decide(ok_ctx(current_voice=None), s=ON).skip == "voice_mismatch"   # unknown voice: never the wrong one
    assert R.decide(ok_ctx(takes=("mm_01", "mm_02")), s=ON).skip == "no_bank"     # rotation needs a third take
    assert R.decide(ok_ctx(bank_voice=None), s=ON).skip == "no_bank"
    for h in (22, 0, 6):
        assert R.decide(ok_ctx(local_hour=h), s=ON).skip == "night"
    for h in (7, 9, 21):
        assert R.decide(ok_ctx(local_hour=h), s=ON).kind == "heard"
    assert R.decide(ok_ctx(speech_ms=480), s=ON).kind == "heard"              # the boundary itself plays


# ── rotation ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_rotation_never_repeats_and_avoids_the_last_two():
    rng = random.Random(7)
    state, seq = {}, []
    for i in range(1000):
        t = R.pick_take(TAKES, state, rng)
        assert t not in state.get("recent", [])
        seq.append(t)
        state = R.record(state, R.Decision("heard", t), now_s=float(i))
    assert all(a != b for a, b in zip(seq, seq[1:]))
    assert all(seq[i] not in (seq[i - 1], seq[i - 2]) for i in range(2, len(seq)))
    assert set(seq) == set(TAKES)          # the whole bank gets used


def test_rotation_negative_control_without_state_repeats():
    rng = random.Random(7)
    seq = [R.pick_take(TAKES, None, rng) for _ in range(200)]
    assert any(a == b for a, b in zip(seq, seq[1:]))


def test_rotation_is_deterministic_for_a_seed():
    a = [R.decide(ok_ctx(), {"recent": ["mm_01"]}, s=ON, seed=k).take for k in range(20)]
    b = [R.decide(ok_ctx(), {"recent": ["mm_01"]}, s=ON, seed=k).take for k in range(20)]
    assert a == b and "mm_01" not in a


def test_state_is_tiny_and_tolerates_junk():
    st = R.record({"recent": ["mm_01", 5, "mm_02", "mm_03"], "x": "y"}, R.Decision("heard", "mm_04"), now_s=10.0)
    assert st == {"recent": ["mm_03", "mm_04"], "last_at": 10.0}
    assert R.record(st, R.Decision("none", None, "night"), now_s=99.0) == st
    assert json.loads(json.dumps(st)) == st
    assert R.pick_take(["mm_01", "mm_02"], {"recent": ["mm_01", "mm_02"]}, random.Random(1)) == "mm_01"   # small bank: avoid last only
    assert R.pick_take(["only"], {"recent": ["only"]}) == "only"
    assert R.pick_take(["BAD ID", ""], {}) is None


# ── the budget ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_budget_one_per_min_gap():
    st = R.record({}, R.Decision("heard", "mm_01"), now_s=100.0)
    assert R.since_last(st, 129.9) == pytest.approx(29.9)
    assert R.decide(ok_ctx(since_last_s=R.since_last(st, 129.9)), st, s=ON).skip == "budget"
    assert R.decide(ok_ctx(since_last_s=R.since_last(st, 130.0)), st, s=ON).kind == "heard"
    assert R.since_last({}, 5.0) is None and R.since_last({"last_at": True}, 5.0) is None
    assert R.decide(ok_ctx(since_last_s=float("nan")), s=ON).skip == "error"


def test_chain_rule_is_flagged_off_and_bounded_when_on():
    assert R.settings({}).chain_enabled is False
    chain = replace(ON, chain_enabled=True)
    assert R.decide(ok_ctx(turn_kind="followup"), s=ON).skip == "not_wake"
    assert R.decide(ok_ctx(turn_kind="followup"), s=chain).kind == "heard"
    assert R.decide(ok_ctx(turn_kind="followup", chain_turns_since=1), s=chain).skip == "budget"   # never two in a row
    assert R.decide(ok_ctx(turn_kind="followup", chain_turns_since=2), s=chain).skip == "budget"
    assert R.decide(ok_ctx(turn_kind="followup", chain_turns_since=3), s=chain).kind == "heard"


# ── the handshake ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_handshake_heard_shape_carries_no_text():
    b = R.handshake_block(R.Decision("heard", "mm_03"), 41.7, 455, "0a1b2c3d", "af_sky")
    assert b == {"kind": "heard", "take": "mm_03", "bank": "0a1b2c3d", "voice": "af_sky", "at_ms": 41, "dur_ms": 455}
    for v in b.values():
        assert isinstance(v, int) or (isinstance(v, str) and re.fullmatch(r"[a-z0-9_]{1,64}", v))


def test_handshake_none_and_hostile_inputs():
    assert R.handshake_block(R.Decision("none", None, "night"), 0, 0, None, None) == {"kind": "none", "skip": "night"}
    assert R.handshake_block(R.Decision("none", None, "made up"), 0, 0, None, None) == {"kind": "none", "skip": "disabled"}
    for take, sha, voice in (("Mm, I heard you", "0a1b2c3d", "af_sky"), ("mm_01", "xyz", "af_sky"),
                             ("mm_01", "0a1b2c3d", "../etc"), ("mm_01", None, "af_sky")):
        assert R.handshake_block(R.Decision("heard", take), 1, 1, sha, voice) == {"kind": "none", "skip": "no_bank"}
    b = R.handshake_block(R.Decision("heard", "mm_01"), 1e12, float("inf"), "0a1b2c3d", "af_sky")
    assert b["at_ms"] == 10000 and b["dur_ms"] == 0


# ── the ordering wait (H3) ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_ordering_wait_arithmetic():
    s = ON
    assert R.ordering_wait_ms(None, 500, 10.0, s) == 0
    assert R.ordering_wait_ms(10.0, 500, 10.1, s) == 520          # 500 - 100 + 120 seam
    assert R.ordering_wait_ms(10.0, 500, 10.62, s) == 0           # ended + seam already passed
    assert R.ordering_wait_ms(10.0, 5000, 10.0, s) == 600 + 120   # a too-long clip counts as REFLEX_MAX_MS
    assert R.ordering_wait_ms(10.0, "x", 10.0, s) == 120
    assert R.ordering_wait_ms(10.0, 500, 9.0, s) <= 720


@pytest.mark.parametrize("server_first_s", [0.05, 0.2, 0.45, 0.62, 1.5])
def test_a_fast_canned_reply_never_overlaps_the_reflex(server_first_s):
    started, dur = 100.0, 480
    arrive = started + server_first_s
    first_write = arrive + R.ordering_wait_ms(started, dur, arrive, ON) / 1000.0
    reflex_end = started + dur / 1000.0
    assert first_write - reflex_end >= ON.seam_ms / 1000.0 - 1e-9
    assert first_write - arrive <= (ON.max_ms + ON.seam_ms) / 1000.0
    if server_first_s >= 0.6:                                     # a brain turn: no added wait
        assert first_write == arrive


# ── env clamping ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_defaults_are_the_s1_constants():
    s = R.settings({})
    assert (s.enabled, s.min_speech_ms, s.max_ms, s.seam_ms, s.min_gap_s, s.idle_greet_h, s.night_start, s.night_end,
            s.gain_db) == (False, 480, 600, 120, 30, 4, 22, 7, -3)
    assert (s.for_guests, s.min_speaker_conf) == (False, 0.70)
    assert not hasattr(s, "require_speaker")                # removed: a missing score is always silence


def test_env_overrides_are_clamped():
    s = R.settings({"REFLEX_ENABLED": "yes", "REFLEX_MIN_GAP_S": "1", "REFLEX_MAX_MS": "99999", "REFLEX_SEAM_MS": "-5",
                    "REFLEX_MIN_SPEECH_MS": "nan", "REFLEX_GAIN_DB": "+12", "REFLEX_IDLE_GREET_H": "inf",
                    "REFLEX_NIGHT": "21-06", "REFLEX_CHAIN_EVERY": "1"})
    assert s.enabled is True and s.min_gap_s == 10 and s.max_ms == 1000 and s.seam_ms == 40
    assert s.min_speech_ms == 480 and s.gain_db == 0 and s.idle_greet_h == 4 and (s.night_start, s.night_end) == (21, 6)
    assert s.chain_every == 2
    t = R.settings({"REFLEX_FOR_GUESTS": "1", "REFLEX_MIN_SPEAKER_CONF": "7"})
    assert t.for_guests and t.min_speaker_conf == 1.0
    assert R.settings({"REFLEX_MIN_SPEAKER_CONF": "-1"}).min_speaker_conf == 0.0
    assert R.settings({"REFLEX_ENABLED": "maybe"}).enabled is False
    assert R.settings({"REFLEX_NIGHT": "25-07"}).night_start == 22
    assert R.settings({"REFLEX_NIGHT": "7-7"}).night_start == 7 and not R.is_night(23, R.settings({"REFLEX_NIGHT": "7-7"}))


def test_module_is_stdlib_only():
    tree = ast.parse(_MOD.read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods - {"__future__"} <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


# ── the bank loader ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _wav(ms: int, rate: int = 24000, freq: float = 180.0, amp: int = 6000) -> bytes:
    import io

    n = int(rate * ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)))
    return buf.getvalue()


def make_bank(root: Path, voice: str = "af_sky", n: int = 3, ms: int = 400, rate: int = 24000, dirname: str | None = None,
              manifest_voice: str | None = None):
    files = {f"mm_{i + 1:02d}": _wav(ms + 10 * i, rate=rate, freq=150.0 + 10 * i) for i in range(n)}
    pairs = [(k, hashlib.sha256(v).hexdigest()) for k, v in files.items()]
    sha8 = R.bank_sha8(voice, 60, pairs)
    d = root / voice / (dirname or sha8)
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / f"{k}.wav").write_bytes(v)
    man = {"voice": manifest_voice or voice, "bank": sha8, "style_len": 60, "render_date": "2026-11-14",
           "takes": [{"id": k, "sha256": s, "dur_ms": 0, "rms_db": -20.0} for k, s in pairs]}
    (d / "manifest.json").write_text(json.dumps(man))
    return d, sha8


def test_loader_accepts_a_good_bank(tmp_path):
    d, sha8 = make_bank(tmp_path)
    res = R.load_bank("af_sky", str(tmp_path), ON)
    assert res.reason == "ok" and res.bank.sha8 == sha8 and res.bank.take_ids == ("mm_01", "mm_02", "mm_03")
    assert res.bank.takes["mm_01"].dur_ms == 400 and len(res.bank.takes["mm_01"].pcm) == 400 * 24 * 2
    ctx = ok_ctx(bank_voice=res.bank.voice, takes=res.bank.take_ids)
    assert R.decide(ctx, s=ON).kind == "heard"


def test_loader_refuses_a_tampered_file(tmp_path):
    d, _ = make_bank(tmp_path)
    raw = bytearray((d / "mm_02.wav").read_bytes())
    raw[-1] ^= 0x01
    (d / "mm_02.wav").write_bytes(bytes(raw))
    assert R.load_bank("af_sky", str(tmp_path), ON) == R.LoadResult(None, "sha_mismatch")


def test_loader_refuses_a_wrong_sample_rate(tmp_path):
    make_bank(tmp_path, rate=16000)
    assert R.load_bank("af_sky", str(tmp_path), ON).reason == "bad_format"


def test_loader_refuses_a_voice_mismatch(tmp_path):
    make_bank(tmp_path)
    assert R.load_bank("bf_emma", str(tmp_path), ON).bank is None
    tmp2 = tmp_path / "b"
    make_bank(tmp2, manifest_voice="bf_emma")
    assert R.load_bank("af_sky", str(tmp2), ON).reason == "voice_mismatch"


def test_loader_refuses_other_defects(tmp_path):
    make_bank(tmp_path / "long", ms=700)
    assert R.load_bank("af_sky", str(tmp_path / "long"), ON).reason == "too_long"
    make_bank(tmp_path / "few", n=2)
    assert R.load_bank("af_sky", str(tmp_path / "few"), ON).reason == "too_few"
    make_bank(tmp_path / "name", dirname="deadbeef")
    assert R.load_bank("af_sky", str(tmp_path / "name"), ON).reason == "sha_mismatch"
    d, _ = make_bank(tmp_path / "junk")
    (d / "manifest.json").write_text("{not json")
    assert R.load_bank("af_sky", str(tmp_path / "junk"), ON).reason == "bad_manifest"
    assert R.load_bank("af_sky", str(tmp_path / "nothing"), ON).reason == "missing"
    assert R.load_bank("../x", str(tmp_path), ON).reason == "voice_mismatch"
    assert set(R.LOAD_RESULTS) >= {"ok", "missing", "bad_manifest", "voice_mismatch", "sha_mismatch", "bad_format",
                                   "too_long", "too_few"}


def test_loader_negative_control_untampered_copy_loads(tmp_path):
    d, _ = make_bank(tmp_path)
    raw = (d / "mm_02.wav").read_bytes()
    (d / "mm_02.wav").write_bytes(raw)          # rewritten unchanged: still loads (the tamper test is not vacuous)
    assert R.load_bank("af_sky", str(tmp_path), ON).reason == "ok"
