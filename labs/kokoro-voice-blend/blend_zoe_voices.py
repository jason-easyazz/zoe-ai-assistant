#!/usr/bin/env python3
"""Kokoro custom voice blending — candidate "Zoe" persona voices (LAB SPIKE).

Kokoro voice identity is a (510, 1, 256) float32 style tensor. New voices are
made by weighted linear blends or slerp (spherical interpolation) of existing
voice tensors — no model needed to *compute* a blend (pure numpy on the
voices bin), so this never loads a second Kokoro next to the live sidecar.

Reproducible: candidate recipes are pinned in CANDIDATES below; running this
script always regenerates byte-identical tensors from /home/zoe/models/voices-v1.0.bin.

Usage (from repo root):
    # 1) (Re)generate the candidate tensors (pure numpy, instant, no lock needed)
    python3 labs/kokoro-voice-blend/blend_zoe_voices.py

    # 2) Also synthesize audition WAVs (loads kokoro-onnx on CPU ~600MB,
    #    unloads on exit). The script acquires /tmp/zoe-voice-harness.lock
    #    ITSELF (bounded wait) — do not wrap it in an outer `flock`:
    python3 labs/kokoro-voice-blend/blend_zoe_voices.py --audio

    # 3) Build an augmented voices bin for deployment (after Jason picks one):
    python3 labs/kokoro-voice-blend/blend_zoe_voices.py \
        --emit-bin /home/zoe/models/voices-v1.0-zoe.bin

Outputs:
    labs/kokoro-voice-blend/voices/<name>.npy   float16 tensors (small, committed)
    /tmp/zoe-voice-blend-samples/<name>.wav     audition WAVs (NOT committed)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

VOICES_BIN = os.environ.get("ZOE_KOKORO_VOICES", "/home/zoe/models/voices-v1.0.bin")
ONNX_MODEL = os.environ.get("ZOE_KOKORO_MODEL", "/home/zoe/models/kokoro-v1.0.onnx")
LAB_DIR = Path(__file__).resolve().parent
TENSOR_DIR = LAB_DIR / "voices"
SAMPLE_DIR = Path("/tmp/zoe-voice-blend-samples")
SAMPLE_RATE = 24000

# Fixed audition paragraph — same text for every candidate so Jason compares
# voices, not content. Mixes statement, question, numbers, and warmth.
TEST_PARAGRAPH = (
    "Hi Jason, it's Zoe. It's a lovely afternoon — twenty four degrees and "
    "clear skies. You have two things on the calendar today: coffee with Sam "
    "at three, and the market run before six. Want me to set a reminder, or "
    "shall we just see how the day goes?"
)


def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Spherical interpolation between two style tensors, row-wise over the
    510 per-length style vectors (each a 256-dim vector)."""
    a64, b64 = a.astype(np.float64), b.astype(np.float64)
    out = np.empty_like(a64)
    for i in range(a64.shape[0]):
        va, vb = a64[i].ravel(), b64[i].ravel()
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na < 1e-8 or nb < 1e-8:
            out[i] = ((1 - t) * va + t * vb).reshape(a64[i].shape)
            continue
        dot = np.clip(np.dot(va / na, vb / nb), -1.0, 1.0)
        omega = np.arccos(dot)
        if omega < 1e-6:
            out[i] = ((1 - t) * va + t * vb).reshape(a64[i].shape)
        else:
            so = np.sin(omega)
            out[i] = (
                (np.sin((1 - t) * omega) / so) * va + (np.sin(t * omega) / so) * vb
            ).reshape(a64[i].shape)
    return out.astype(np.float32)


def linear(voices: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    total = sum(weights.values())
    acc = np.zeros_like(next(iter(voices.values())), dtype=np.float64)
    for name, w in weights.items():
        acc += (w / total) * voices[name].astype(np.float64)
    return acc.astype(np.float32)


# ── Pinned candidate recipes (the deliverable) ────────────────────────────────
# All-female warm/natural blends around af_sky (the current live voice) so the
# persona shifts, not lurches. Two linear, two slerp, one three-way.
def CANDIDATES(v: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {
        # Warm + familiar: half the current voice, half af_bella's warmth.
        "zoe_dawn": linear(v, {"af_sky": 0.5, "af_bella": 0.5}),
        # Richer/rounder: heart-forward with a sky anchor and a nova lift.
        "zoe_ember": linear(v, {"af_heart": 0.4, "af_sky": 0.4, "af_nova": 0.2}),
        # Slerp variant of dawn — preserves per-vector energy, often crisper
        # than the linear mix of the same pair.
        "zoe_dawn_slerp": slerp(v["af_sky"], v["af_bella"], 0.5),
        # Mostly heart with a kore tint (slerp, t toward heart).
        "zoe_kore_heart": slerp(v["af_heart"], v["af_kore"], 0.35),
        # Soft-spoken: sky with a nicole (breathy) shade — gentlest candidate.
        "zoe_velvet": linear(v, {"af_sky": 0.65, "af_nicole": 0.35}),
    }


def load_voices() -> dict[str, np.ndarray]:
    npz = np.load(VOICES_BIN)
    return {name: npz[name] for name in npz.files}


def write_tensors(cands: dict[str, np.ndarray]) -> None:
    TENSOR_DIR.mkdir(parents=True, exist_ok=True)
    for name, tensor in cands.items():
        # float16 keeps committed files small (~261 KB); upcast on use.
        np.save(TENSOR_DIR / f"{name}.npy", tensor.astype(np.float16))
        print(f"wrote {TENSOR_DIR / (name + '.npy')}  shape={tensor.shape}")


HARNESS_LOCK = "/tmp/zoe-voice-harness.lock"
LOCK_TIMEOUT_S = 300.0


def _acquire_harness_lock() -> int:
    """Acquire the shared voice-harness flock (bounded wait, fail loudly).

    Mandatory, not advisory: the script takes the lock itself before loading
    kokoro-onnx, so a bare `python3 blend_zoe_voices.py --audio` can never race
    the replay/perf harnesses. Do NOT wrap the script in an outer
    `flock /tmp/zoe-voice-harness.lock …` — the wrapper's lock lives on a
    different open file description, so the inner acquire would wait behind it
    until the timeout. Bounded retry + a clear error instead of a silent hang.
    Returns the fd, held until process exit.
    """
    import fcntl
    import time

    fd = os.open(HARNESS_LOCK, os.O_CREAT | os.O_RDWR, 0o666)
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise SystemExit(
                    f"Could not acquire {HARNESS_LOCK} within {LOCK_TIMEOUT_S:.0f}s "
                    "— another voice harness holds it; retry later."
                )
            time.sleep(1.0)


def synthesize_samples(cands: dict[str, np.ndarray], voices: dict[str, np.ndarray]) -> None:
    """CPU kokoro-onnx (~600MB), one-shot; acquires the harness flock itself."""
    import wave

    _lock_fd = _acquire_harness_lock()  # noqa: F841 — held until process exit

    from kokoro_onnx import Kokoro  # local CPU pipeline, unloaded at process exit

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    kokoro = Kokoro(ONNX_MODEL, VOICES_BIN)
    to_render = {"baseline_af_sky": voices["af_sky"], **cands}
    for name, tensor in to_render.items():
        samples, sr = kokoro.create(
            TEST_PARAGRAPH, voice=tensor.astype(np.float32), speed=1.0, lang="en-us"
        )
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        out = SAMPLE_DIR / f"{name}.wav"
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm)
        print(f"wrote {out}  ({len(pcm) // 2 / sr:.1f}s)")


def emit_bin(cands: dict[str, np.ndarray], voices: dict[str, np.ndarray], path: str) -> None:
    """Write an augmented voices bin (all stock voices + zoe_* candidates) that
    the live sidecar can point at via ZOE_KOKORO_VOICES — deploy step only."""
    merged = {**voices, **{k: v.astype(np.float32) for k, v in cands.items()}}
    with open(path, "wb") as f:
        np.savez(f, **merged)
    print(f"wrote augmented voices bin: {path}  ({len(merged)} voices)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", action="store_true", help="also synthesize audition WAVs (script acquires the harness flock itself)")
    ap.add_argument("--emit-bin", metavar="PATH", help="write an augmented voices .bin including the candidates")
    args = ap.parse_args()

    voices = load_voices()
    cands = CANDIDATES(voices)
    write_tensors(cands)
    if args.emit_bin:
        emit_bin(cands, voices, args.emit_bin)
    if args.audio:
        synthesize_samples(cands, voices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
