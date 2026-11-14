"""The reflex bank renderer (scripts/setup/render_reflex_bank.py) on a synthetic sine "voice", with no sidecar: the DSP (trim,
fade, RMS match within 1 dB, peak cap), the manifest round-trips through the panel's loader, the same inputs give the same
bank sha, and the shared trim rule equals the daemon's own on synthetic PCM.
"""
from __future__ import annotations

import importlib.util
import io
import json
import math
import random
import re
import struct
import sys
import types
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup"
_DAEMON = _SETUP / "zoe_voice_daemon.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RB = _load("render_reflex_bank_under_test", _SETUP / "render_reflex_bank.py")
RX = RB.RX
RATE = 24000


def tone(ms, amp=8000, freq=200.0, lead_ms=400, tail_ms=400, rate=RATE) -> bytes:
    n = int(rate * ms / 1000)
    body = [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    pcm = [0] * int(rate * lead_ms / 1000) + body + [0] * int(rate * tail_ms / 1000)
    return b"".join(struct.pack("<h", v) for v in pcm)


def wav(pcm: bytes, rate=RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class SineVoice:
    """A deterministic fake sidecar: Kokoro-like 0.4 s silence on both ends, length from the text and speed, level from the
    style row. Records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, voice, speed, style_len):
        self.calls.append((text, voice, speed, style_len))
        if len(text) > 12:                                    # a reference sentence
            return wav(tone(1500, amp=9000, freq=180.0))
        ms = int((180 + 40 * len(text)) / speed)
        amp = 3000 + 20 * (style_len or 60)
        return wav(tone(ms, amp=amp, freq=150.0 + 10 * len(text)))


# ── DSP ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_trim_keeps_a_20ms_lead_and_a_130ms_tail():
    pcm = tone(300, lead_ms=450, tail_ms=450)
    out = RX.trim_silence(pcm, RATE, 1, 2)
    samples = len(out) // 2
    lead, tail = int(RATE * 0.020), int(RATE * 0.130)
    assert abs(samples - (int(RATE * 0.3) + lead + tail)) <= 2
    assert RX.trim_silence(b"", RATE, 1, 2) == b"" and RX.trim_silence(b"\x00" * 100, RATE, 1, 2) == b"\x00" * 100
    assert RX.trim_silence(b"\x01\x02\x03", RATE, 1, 2) == b"\x01\x02\x03"     # malformed: untouched


def test_fade_ramps_both_ends():
    pcm = tone(100, amp=10000, freq=50.0, lead_ms=0, tail_ms=0)
    pcm = struct.pack("<h", 10000) * (len(pcm) // 2)                     # a flat block shows the ramp exactly
    out = RB._samples(RB.fade(pcm))
    n = int(RATE * 0.010)
    assert out[0] == 0 and out[-1] == 0
    assert out[n // 2] == pytest.approx(5000, abs=2) and out[n] == 10000 and out[len(out) // 2] == 10000
    assert all(out[i] <= out[i + 1] for i in range(n))


def test_rms_match_within_1db():
    pcm = RX.trim_silence(tone(400, amp=3000), RATE, 1, 2)
    for target in (-30.0, -24.0, -20.0):
        out, capped = RB.match_level(pcm, target)
        assert not capped and abs(RX.active_rms_db(out) - target) <= 1.0


def test_peak_cap_wins_over_the_target():
    pcm = RX.trim_silence(tone(400, amp=3000), RATE, 1, 2)
    out, capped = RB.match_level(pcm, -2.0)                         # a sine at -2 dB RMS would peak at about +1 dBFS
    assert capped and RB.peak_dbfs(out) <= -1.0 + 0.01
    # negative control: without the cap the same target clips past -1 dBFS
    out2, _ = RB.match_level(pcm, -2.0, peak_cap_dbfs=6.0)
    assert RB.peak_dbfs(out2) > -1.0


def test_a_non_24k_render_is_refused():
    with pytest.raises(ValueError):
        RB.decode_wav(wav(tone(100), rate=16000))


# ── the render ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _render(tmp_path, sub="a", style_pin=True, **kw):
    voice = SineVoice()
    final, man = RB.render(synth=voice, voice="af_sky", render_date="2026-11-14", out_root=tmp_path / sub, style_pin=style_pin,
                           versions={"kokoro": "0.0-test", "misaki": None}, sidecar_sha="abc123", **kw)
    return voice, final, man


def test_the_manifest_round_trips_through_the_panel_loader(tmp_path):
    _, final, man = _render(tmp_path)
    assert len(man["takes"]) == 27 and man["rejected"] == [] and final.name == man["bank"]
    res = RX.load_bank("af_sky", str(tmp_path / "a"), RX.Settings(enabled=True))
    assert res.reason == "ok" and res.bank.sha8 == man["bank"] and len(res.bank.takes) == 27
    target = man["reference_rms_db"] + man["gain_db"]
    for t in man["takes"]:
        assert abs(t["rms_db"] - target) <= 1.0 and t["peak_db"] <= -1.0 + 0.01
        assert res.bank.takes[t["id"]].dur_ms == t["dur_ms"]
    for key in ("voice", "style_len", "speeds", "kokoro_version", "misaki_version", "sidecar_sha", "render_date"):
        assert key in man
    ctx = RX.Ctx(rule="deep", speech_ms=900, bank_voice=res.bank.voice, current_voice="af_sky", takes=res.bank.take_ids,
                 committed=True, guest=False, speaker_conf=0.9, media_active=False)
    assert RX.decide(ctx, s=RX.Settings(enabled=True), seed=4).kind == "heard"


def test_the_same_inputs_give_the_same_bank(tmp_path):
    _, f1, m1 = _render(tmp_path, "a")
    _, f2, m2 = _render(tmp_path, "b")
    assert m1["bank"] == m2["bank"]
    assert {p.name: p.read_bytes() for p in f1.iterdir()} == {p.name: p.read_bytes() for p in f2.iterdir()}
    _, f3, m3 = _render(tmp_path, "c", gain_db=-6.0)          # negative control: a different level is a different bank
    assert m3["bank"] != m1["bank"]


def test_keep_renders_only_the_chosen_takes_with_identical_audio(tmp_path):
    _, f1, m1 = _render(tmp_path, "a")
    _, f2, m2 = _render(tmp_path, "b", keep=["mm_02", "mm_05", "mm_09"])
    assert [t["id"] for t in m2["takes"]] == ["mm_02", "mm_05", "mm_09"]
    for t in m2["takes"]:
        assert (f2 / f"{t['id']}.wav").read_bytes() == (f1 / f"{t['id']}.wav").read_bytes()
    with pytest.raises(ValueError):
        _render(tmp_path, "c", keep=["mm_99"])


def test_style_len_is_sent_only_with_style_pin(tmp_path):
    v, _, man = _render(tmp_path, "a", style_pin=True)
    assert {c[3] for c in v.calls} == {20, 60, 120} and man["style_pin"] is True and man["style_len"] == 120
    v, final, man = _render(tmp_path, "b", style_pin=False)
    assert {c[3] for c in v.calls} == {None} and man["style_pin"] is False and man["style_len"] is None
    assert len(man["takes"]) == 9 and "WITHOUT style_len" in man["note"]
    assert RX.load_bank("af_sky", str(tmp_path / "b"), RX.Settings(enabled=True)).reason == "ok"


def test_a_take_longer_than_the_max_is_rejected_not_shipped(tmp_path):
    _, _, man = _render(tmp_path, "a", texts=("Mm.", "Mm-hmmmmmmm."), speeds=(0.5, 1.0))
    assert {r["reason"] for r in man["rejected"]} == {"too_long"}
    assert all(t["dur_ms"] <= RX.REFLEX_MAX_MS for t in man["takes"])
    assert RX.load_bank("af_sky", str(tmp_path / "a"), RX.Settings(enabled=True)).reason == "ok"


def test_main_dry_run_touches_nothing(tmp_path, capsys):
    def boom(*a, **k):
        raise AssertionError("dry run must not synthesise or call /health")
    assert RB.main(["--render-date", "2026-11-14", "--out", str(tmp_path), "--dry-run"], synth=boom, health=boom) == 0
    assert "mm_27" in capsys.readouterr().out and not any(tmp_path.iterdir())


def test_main_renders_with_injected_sidecar(tmp_path):
    v = SineVoice()
    rc = RB.main(["--render-date", "2026-11-14", "--out", str(tmp_path), "--speeds", "1.0"], synth=v,
                 health=lambda: {"voice": "af_sky", "status": "ok"})
    assert rc == 0
    (bank,) = list((tmp_path / "af_sky").iterdir())
    man = json.loads((bank / "manifest.json").read_text())
    assert man["style_pin"] is False and len(man["takes"]) == 3
    with pytest.raises(ValueError):
        RB.main(["--render-date", "14/11/2026", "--out", str(tmp_path)], synth=v, health=lambda: {"voice": "af_sky"})


def test_the_renderer_is_stdlib_only():
    import ast

    tree = ast.parse((_SETUP / "render_reflex_bank.py").read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods - {"__future__", "zoe_voice_reflex"} <= set(sys.stdlib_module_names)


# ── the trim rule IS the daemon's ────────────────────────────────────────────────────────────────────────────────────────────
def test_the_trim_constants_are_pinned_to_the_daemon_source():
    src = _DAEMON.read_text(encoding="utf-8")
    assert re.search(r'_TTS_KEEP_TAIL_MS = _int_env\("ZOE_TTS_KEEP_TAIL_MS", 130\)', src)
    assert re.search(r'_TTS_LEAD_GUARD_MS = _int_env\("ZOE_TTS_LEAD_GUARD_MS", 20\)', src)
    assert "thr = max(int(peak * 0.02), 96)" in src
    assert (RX.TRIM_KEEP_TAIL_MS, RX.TRIM_LEAD_GUARD_MS, RX.TRIM_PEAK_FRACTION, RX.TRIM_FLOOR) == (130, 20, 0.02, 96)


@pytest.fixture(scope="module")
def daemon():
    pytest.importorskip("numpy")
    pytest.importorskip("requests")
    fake = types.ModuleType("pyaudio")
    fake.paInt16 = 8
    fake.PyAudio = object
    saved = sys.modules.get("pyaudio")
    sys.modules["pyaudio"] = fake
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_trim_under_test", _DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("pyaudio", None)
        else:
            sys.modules["pyaudio"] = saved
    return mod


def _cases():
    rng = random.Random(11)
    cases = [(b"", 1), (b"\x00\x00" * 500, 1), (b"\x01\x02\x03", 1), (struct.pack("<h", -32768) * 400, 1),
             (tone(200, amp=50), 1), (tone(300, lead_ms=10, tail_ms=0), 1), (tone(300, lead_ms=0, tail_ms=5), 1)]
    for _ in range(40):
        ms = rng.randint(5, 400)
        pcm = tone(ms, amp=rng.choice([90, 97, 500, 8000, 32767]), freq=rng.uniform(60, 900),
                   lead_ms=rng.randint(0, 500), tail_ms=rng.randint(0, 500))
        if rng.random() < 0.3:
            vals = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
            vals[rng.randrange(len(vals))] = -32768
            pcm = struct.pack(f"<{len(vals)}h", *vals)
        cases.append((pcm, 1))
    st = tone(200)
    cases.append((st + st[: len(st) // 2 * 2], 2))
    cases.append((st[:-2], 2))                                     # odd frame count for stereo
    return cases


def test_trim_equals_the_daemons_on_synthetic_pcm(daemon):
    for pcm, ch in _cases():
        assert RX.trim_silence(pcm, RATE, ch, 2) == daemon._trim_chunk_silence(pcm, RATE, ch, 2)


def test_trim_equality_negative_control(daemon):
    """A copy with a different tail is caught by the equality test above (it is not vacuous)."""
    pcm = tone(300)
    assert RX.trim_silence(pcm, RATE, 1, 2, keep_tail_ms=100) != daemon._trim_chunk_silence(pcm, RATE, 1, 2)
