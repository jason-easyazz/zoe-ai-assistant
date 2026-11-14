#!/usr/bin/env python3
"""Render the panel reflex bank: soft non-lexical "Mm." takes in Zoe's own Kokoro voice (docs/knowledge/voice-reflex.md).

Run ON THE ORIN by the owner, against the local Kokoro sidecar (``/synthesize``). Never run by an agent against the live sidecar.

    python3 scripts/setup/render_reflex_bank.py --voice af_sky --render-date 2026-11-20 --dry-run
    python3 scripts/setup/render_reflex_bank.py --voice af_sky --render-date 2026-11-20 --out ~/reflex-candidates
    python3 scripts/setup/render_reflex_bank.py --voice af_sky --render-date 2026-11-20 --keep mm_02,mm_05,mm_07 --out ~/reflex-bank

Kokoro is deterministic, so variety comes from a grid: text x style_len x speed (design note W2 section 4). The owner listens to
the candidates, keeps 6-8, and re-renders only those with ``--keep`` (the ids are grid positions, stable across runs).

Each take: synthesised -> trimmed with the DAEMON'S OWN rule (``zoe_voice_reflex.trim_silence``, pinned equal to
``zoe_voice_daemon._trim_chunk_silence``) -> 10 ms fade at both ends -> RMS-matched to the reference reply speech (20 neutral
sentences in the same voice) plus ``REFLEX_GAIN_DB`` (-3 dB), peak capped at -1 dBFS. A take longer than ``REFLEX_MAX_MS`` after
the trim is rejected (listed in the manifest, not shipped). Output: ``<out>/<voice>/<bank_sha8>/{mm_NN}.wav`` + ``manifest.json``.

``style_len`` (the W1 pinned Kokoro style row) is sent only when the sidecar's ``/health`` reports ``style_pin``; otherwise the
grid collapses to the sidecar's own style choice and the manifest says ``"style_pin": false``. Stdlib only.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import io
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zoe_voice_reflex as RX  # noqa: E402

DEFAULT_TEXTS = ("Mm.", "Mm-hm.", "Mm...")
DEFAULT_STYLE_LENS = (20, 60, 120)
DEFAULT_SPEEDS = (0.94, 1.0, 1.06)
REPLY_STYLE_LEN = 120           # W1 ZOE_KOKORO_STYLE_PIN_LEN default: the row pinned replies use (the reference level)
FADE_MS = 10
PEAK_CAP_DBFS = -1.0
SIDECAR_URL = "http://127.0.0.1:10201"
#: neutral, invented reference reply sentences: the level a reply is spoken at in this voice
REFERENCE_SENTENCES = (
    "The kettle should be ready in about two minutes.",
    "Tomorrow looks clear in the morning and a little windy after lunch.",
    "I have added that to the shopping list.",
    "Your timer for the oven is set for twenty minutes.",
    "There are three things on the list for this week.",
    "The lights in the kitchen are on now.",
    "That song is called something like a summer evening.",
    "It is a quarter past four in the afternoon.",
    "The library closes at five on Saturdays.",
    "I can remind you about that tomorrow at nine.",
    "The forecast says a light shower is possible tonight.",
    "Okay, the music is paused.",
    "There is nothing else on the calendar for today.",
    "The bus usually takes about twenty five minutes.",
    "I have moved the reminder to Thursday morning.",
    "That recipe needs two cups of flour and one egg.",
    "The front door sensor shows it is closed.",
    "You asked me to check the washing at three.",
    "The next item on the list is the garden hose.",
    "It should be warmer by the weekend.",
)

Synth = Callable[[str, str, float, Optional[int]], bytes]


# ── audio helpers (mono S16 at 24 kHz) ───────────────────────────────────────────────────────────────────────────────────────
def decode_wav(wav_bytes: bytes) -> bytes:
    """The PCM of a 24 kHz mono S16 WAV; anything else is a ValueError (the bank never resamples)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (RX.BANK_CHANNELS, RX.BANK_WIDTH, RX.BANK_RATE):
            raise ValueError("sidecar audio is not 24 kHz mono S16")
        return wf.readframes(wf.getnframes())


def encode_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(RX.BANK_CHANNELS)
        wf.setsampwidth(RX.BANK_WIDTH)
        wf.setframerate(RX.BANK_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def _samples(pcm: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return a


def fade(pcm: bytes, ms: int = FADE_MS, rate: int = RX.BANK_RATE) -> bytes:
    """A linear fade-in and fade-out of ``ms`` at both ends (shorter clips: half the clip each)."""
    a = _samples(pcm)
    n = min(int(rate * ms / 1000), len(a) // 2)
    for i in range(n):
        g = i / n
        a[i] = int(round(a[i] * g))
        a[len(a) - 1 - i] = int(round(a[len(a) - 1 - i] * g))
    return a.tobytes()


def peak_dbfs(pcm: bytes) -> float:
    a = _samples(pcm)
    peak = max((abs(int(v)) for v in a), default=0)
    return 20.0 * math.log10(peak / 32768.0) if peak else -120.0


def scale(pcm: bytes, gain: float) -> bytes:
    a = _samples(pcm)
    return array.array("h", (max(-32768, min(32767, int(round(v * gain)))) for v in a)).tobytes()


def match_level(pcm: bytes, target_db: float, peak_cap_dbfs: float = PEAK_CAP_DBFS) -> "tuple[bytes, bool]":
    """Scale so the active-speech RMS is ``target_db``, unless that would put the peak above ``peak_cap_dbfs``: then the cap
    wins (returned flag True) and the take is quieter than the target."""
    cur = RX.active_rms_db(pcm)
    pk = peak_dbfs(pcm)
    if cur <= -120.0 or pk <= -120.0:
        return pcm, False
    gain_db = target_db - cur
    capped = pk + gain_db > peak_cap_dbfs
    if capped:
        gain_db = peak_cap_dbfs - pk
    return scale(pcm, 10 ** (gain_db / 20.0)), capped


def process_take(wav_bytes: bytes, target_db: float) -> "tuple[bytes, bool]":
    """Trim (the daemon's rule) -> fade -> level match. Returns (pcm, peak_capped)."""
    pcm = RX.trim_silence(decode_wav(wav_bytes), RX.BANK_RATE, RX.BANK_CHANNELS, RX.BANK_WIDTH)
    pcm = fade(pcm)
    return match_level(pcm, target_db)


def reference_db(synth: Synth, voice: str, style_len: Optional[int], sentences: Sequence[str] = REFERENCE_SENTENCES) -> float:
    """The median active-speech RMS of the reference reply sentences, each trimmed with the daemon's rule."""
    levels = []
    for s in sentences:
        pcm = RX.trim_silence(decode_wav(synth(s, voice, 1.0, style_len)), RX.BANK_RATE, RX.BANK_CHANNELS, RX.BANK_WIDTH)
        db = RX.active_rms_db(pcm)
        if db > -120.0:
            levels.append(db)
    if not levels:
        raise ValueError("no reference speech was rendered")
    return round(statistics.median(levels), 2)


# ── the grid ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def grid(texts: Sequence[str], style_lens: Sequence[Optional[int]], speeds: Sequence[float]) -> "list[dict]":
    """Every (text, style_len, speed), with a stable id by grid position (mm_01, mm_02, ...)."""
    out = []
    for t in texts:
        for sl in style_lens:
            for sp in speeds:
                out.append({"id": f"mm_{len(out) + 1:02d}", "text": t, "style_len": sl, "speed": float(sp)})
    return out


def _clamp_style(v: int) -> int:
    return max(1, min(510, int(v)))


# ── the render ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def render(*, synth: Synth, voice: str, render_date: str, out_root, style_pin: bool, texts: Sequence[str] = DEFAULT_TEXTS,
           style_lens: Sequence[int] = DEFAULT_STYLE_LENS, speeds: Sequence[float] = DEFAULT_SPEEDS,
           keep: Optional[Sequence[str]] = None, gain_db: float = RX.REFLEX_GAIN_DB, max_ms: int = RX.REFLEX_MAX_MS,
           versions: Optional[dict] = None, sidecar_sha: Optional[str] = None) -> "tuple[Path, dict]":
    """Render the bank; returns (bank directory, manifest). Pure apart from ``synth`` and the files it writes."""
    if not RX.VOICE_RE.fullmatch(voice or ""):
        raise ValueError("voice must be a bare Kokoro voice id")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", render_date or ""):
        raise ValueError("render date must be YYYY-MM-DD")
    sls: "list[Optional[int]]" = sorted({_clamp_style(v) for v in style_lens}) if style_pin else [None]
    plan = grid(texts, sls, speeds)
    if keep:
        wanted = set(keep)
        unknown = wanted - {g["id"] for g in plan}
        if unknown:
            raise ValueError(f"--keep names ids outside the grid: {sorted(unknown)}")
        plan = [g for g in plan if g["id"] in wanted]
    reply_style = REPLY_STYLE_LEN if style_pin else None
    ref_db = reference_db(synth, voice, reply_style)
    target = ref_db + gain_db

    takes, files, rejected = [], {}, []
    for g in plan:
        pcm, capped = process_take(synth(g["text"], voice, g["speed"], g["style_len"]), target)
        dur_ms = int(round(len(pcm) / RX.BANK_WIDTH * 1000 / RX.BANK_RATE))
        if dur_ms > max_ms or dur_ms <= 0:
            rejected.append({"id": g["id"], "reason": "too_long" if dur_ms > max_ms else "empty", "dur_ms": dur_ms})
            continue
        wav = encode_wav(pcm)
        files[g["id"]] = wav
        takes.append({**g, "sha256": hashlib.sha256(wav).hexdigest(), "dur_ms": dur_ms,
                      "rms_db": round(RX.active_rms_db(pcm), 2), "peak_db": round(peak_dbfs(pcm), 2), "peak_capped": capped})
    sha8 = RX.bank_sha8(voice, reply_style, [(t["id"], t["sha256"]) for t in takes])
    manifest = {
        "schema": 1, "voice": voice, "bank": sha8, "style_pin": bool(style_pin), "style_len": reply_style,
        "speeds": sorted({float(s) for s in speeds}), "kokoro_version": (versions or {}).get("kokoro"),
        "misaki_version": (versions or {}).get("misaki"), "sidecar_sha": sidecar_sha, "render_date": render_date,
        "gain_db": gain_db, "reference_rms_db": ref_db, "peak_cap_dbfs": PEAK_CAP_DBFS, "fade_ms": FADE_MS,
        "trim": {"keep_tail_ms": RX.TRIM_KEEP_TAIL_MS, "lead_guard_ms": RX.TRIM_LEAD_GUARD_MS},
        "takes": takes, "rejected": rejected,
    }
    if not style_pin:
        manifest["note"] = "the sidecar does not report style_pin: rendered WITHOUT style_len (the sidecar's own style row)"

    vdir = Path(os.path.expanduser(str(out_root))) / voice
    vdir.mkdir(parents=True, exist_ok=True)
    final = vdir / sha8
    tmp = Path(tempfile.mkdtemp(prefix=".render-", dir=vdir))
    try:
        for tid, wav in files.items():
            (tmp / f"{tid}.wav").write_bytes(wav)
        (tmp / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return final, manifest


# ── the sidecar (stdlib HTTP) ────────────────────────────────────────────────────────────────────────────────────────────────
def http_health(url: str) -> dict:
    with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=10) as r:  # noqa: S310 - a local URL the owner passes
        return json.loads(r.read().decode("utf-8"))


def http_synth(url: str, send_style: bool) -> Synth:
    def synth(text: str, voice: str, speed: float, style_len: Optional[int]) -> bytes:
        body = {"text": text, "voice": voice, "speed": speed}
        if send_style and style_len is not None:
            body["style_len"] = style_len
        req = urllib.request.Request(f"{url.rstrip('/')}/synthesize", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return r.read()
    return synth


def discover_versions() -> dict:
    out = {}
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        return out
    for pkg in ("kokoro", "misaki"):
        try:
            out[pkg] = metadata.version(pkg)
        except Exception:  # noqa: BLE001 - not installed in this interpreter: unknown
            out[pkg] = None
    return out


def file_sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def main(argv: Optional[Sequence[str]] = None, *, synth: Optional[Synth] = None,
         health: Optional[Callable[[], dict]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render the panel reflex bank (run on the Orin by the owner).")
    ap.add_argument("--voice", help="Kokoro voice id (default: the sidecar's /health voice)")
    ap.add_argument("--render-date", required=True, help="YYYY-MM-DD, recorded in the manifest")
    ap.add_argument("--out", default="~/reflex-bank", help="output root; the bank lands in <out>/<voice>/<sha8>/")
    ap.add_argument("--sidecar-url", default=SIDECAR_URL)
    ap.add_argument("--sidecar-path", default=str(Path(__file__).resolve().parent / "kokoro_sidecar.py"),
                    help="the sidecar source whose sha the manifest records")
    ap.add_argument("--texts", default="|".join(DEFAULT_TEXTS), help="'|'-separated (e.g. '[Mm](/m/).' if Mm phonemises oddly)")
    ap.add_argument("--style-lens", default=",".join(str(v) for v in DEFAULT_STYLE_LENS))
    ap.add_argument("--speeds", default=",".join(str(v) for v in DEFAULT_SPEEDS))
    ap.add_argument("--keep", default="", help="comma-separated take ids to keep (after listening to the candidates)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network, no files")
    a = ap.parse_args(argv)

    texts = [t for t in a.texts.split("|") if t.strip()]
    style_lens = [int(v) for v in a.style_lens.split(",") if v.strip()]
    speeds = [float(v) for v in a.speeds.split(",") if v.strip()]
    keep = [k.strip() for k in a.keep.split(",") if k.strip()]
    if a.dry_run:
        print(f"voice={a.voice or '<from sidecar /health>'} render_date={a.render_date} out={a.out}/<voice>/<sha8>/")
        print("style_len is sent only if the sidecar /health reports style_pin (not checked in a dry run)")
        for label, sls in (("with style_pin", sorted({_clamp_style(v) for v in style_lens})), ("without", [None])):
            plan = grid(texts, sls, speeds)
            print(f"grid {label}: {len(plan)} takes")
            for g in plan:
                if not keep or g["id"] in keep:
                    print(f"  {g['id']} text={g['text']!r} style_len={g['style_len']} speed={g['speed']}")
        print(f"+ {len(REFERENCE_SENTENCES)} reference sentences for the level (target = reference {RX.REFLEX_GAIN_DB:+.0f} dB)")
        return 0

    h = (health or (lambda: http_health(a.sidecar_url)))()
    style_pin = bool(h.get("style_pin"))
    voice = a.voice or str(h.get("voice") or "")
    if not style_pin:
        print("NOTE: the sidecar does not report style_pin; rendering WITHOUT style_len (manifest says so)", file=sys.stderr)
    final, man = render(synth=synth or http_synth(a.sidecar_url, style_pin), voice=voice, render_date=a.render_date,
                        out_root=a.out, style_pin=style_pin, texts=texts, style_lens=style_lens, speeds=speeds, keep=keep,
                        versions=discover_versions(), sidecar_sha=file_sha(Path(a.sidecar_path)))
    print(f"bank {man['bank']} -> {final}: {len(man['takes'])} takes, {len(man['rejected'])} rejected, "
          f"reference {man['reference_rms_db']} dBFS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
