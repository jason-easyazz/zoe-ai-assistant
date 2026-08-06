#!/usr/bin/env python3
"""Audit + quarantine the replay-gate voice corpus (``~/.zoe-voice-samples``).

The corpus is the permanent replay-gate evidence base (root ``AGENTS.md``): every
voice change is replayed against it, and the probe replays a *slice* of it
(``replay_samples.py --last N``). So corpus hygiene is gate stability — an
off-format or non-speech capture inside the replayed slice moves the gate's
numbers without any code changing. #1642 is the worked example: a single
non-speech capture landing at the front of the sort order turned a green test
red for weeks.

This tool classifies every TOP-LEVEL WAV into three buckets and MOVES the
failures into dated quarantine subdirectories with a JSON manifest:

  keep                 readable RIFF whose audio the real Silero VAD believes
                       contains speech — INCLUDING off-contract-but-usable
                       captures (see below), which are reported as DRIFT
  quarantine-format    UNUSABLE audio only: unparseable RIFF, zero audio frames,
                       or a rate the pipeline cannot honestly resample (<= 0)
  quarantine-nonspeech CLEAR non-speech: the real VAD's PEAK speech probability
                       over the whole recording is below --speech-threshold

Why "off-format" is NOT "unusable" (Codex P2, #1643)
----------------------------------------------------
An earlier version quarantined anything that missed the 16 kHz / mono / s16
capture contract. That was measurably wrong, and it cost the gate 95 real-voice
samples on its first executed run. The replay path is
``replay_samples.py`` → ``routers.voice_tts._transcribe_audio_impl`` →
``_run_moonshine`` → ``_prepare_audio_for_moonshine`` (``voice_tts.py:2071``),
and that helper RESAMPLES off-rate audio to 16 kHz (and downmixes multi-channel)
before transcription — a 24 kHz mono s16 capture transcribes fine. The only
input it refuses is one whose native rate is unknown/invalid (``sr <= 0``),
which it explicitly declines to pretend is 16 kHz.

So the quarantine class is now exactly the set the STT path itself cannot use.
Missing the capture contract is a real signal and is still surfaced — as a
``drift`` attribute on a KEPT file, the same way a 0.20–0.50 peak is surfaced as
BORDERLINE and kept. Two different layers: corpus hygiene must not silently
shrink the gate's evidence base. The right fix for capture drift is a rate
assertion at the SAVE path, not retroactive eviction of the evidence.

Drifted files are still speech-scored — ``score_speech`` normalises them to
16 kHz mono s16 first, mirroring ``_prepare_audio_for_moonshine``, so the VAD
hears what the replay path hears rather than a mis-rated byte stream.

It **never deletes**: quarantine is a move into a sibling directory, and every
corpus consumer globs the top level only (see "Why subdirectories are safe"),
so a quarantined file leaves the replay set while staying on disk forever.

Why the default threshold is 0.20 and not the runtime 0.50
----------------------------------------------------------
``voice_vad.speech_threshold()`` is 0.5 — that is the LIVE barge-in decision, a
per-hop call made under time pressure. It is deliberately NOT reused here.
#1642 measured the corpus distribution: median peak 0.829, ~89% above 0.5, so
~11% sit below the runtime threshold. Quarantining that whole 11% would evict
quiet, distant or clipped-but-real speech — exactly the hard samples the gate
most needs. A peak below 0.20 means the model never once, in any 32 ms hop of
the entire recording, thought it was hearing speech. That is the conservative
"clear non-speech" line; anything between 0.20 and 0.50 is reported as
BORDERLINE and deliberately left in the corpus.

Why subdirectories are safe (verified, not assumed)
---------------------------------------------------
Every corpus reader globs ``<corpus>/*.wav`` — non-recursive:
  * ``services/zoe-data/tests/replay_samples.py::_select`` (the SSOT the
    voice_regression_probe and scripts/perf/measure_voice.py both drive), and
  * ``services/zoe-data/tests/test_voice_barge_in.py`` real-voice replay.
``tests/unit/test_curate_voice_corpus.py`` proves the ``_select`` behaviour by
executing that function's real source against a fixture tree, so a change to
recursive globbing reddens rather than silently re-admitting quarantined audio.

Usage:
    python3 scripts/maintenance/curate_voice_corpus.py                 # DRY-RUN
    python3 scripts/maintenance/curate_voice_corpus.py --json rep.json # + census
    flock /tmp/zoe-voice-harness.lock \\
        python3 scripts/maintenance/curate_voice_corpus.py --execute   # move

ALWAYS take ``flock /tmp/zoe-voice-harness.lock`` around ``--execute`` so the
replay harness cannot enumerate the corpus mid-move.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# The corpus contract: what the STT/replay path expects of every member.
EXPECTED_RATE = 16000
EXPECTED_CHANNELS = 1
EXPECTED_SAMPWIDTH = 2  # bytes → 16-bit signed PCM

# 20 ms @ 16 kHz mono s16 — the frame size the live voice path feeds the VAD.
FRAME_BYTES = 640
# Silero's hop, mirrored from voice_vad.HOP_SAMPLES. Used to compute how many
# hops a recording OWES, so a partial inference failure is detectable.
HOP_SAMPLES = 512

# Conservative "clear non-speech" line; see the module docstring for why this is
# NOT voice_vad.speech_threshold() (0.5).
DEFAULT_SPEECH_THRESHOLD = 0.20
# Reported-only band: below the live runtime threshold but above the quarantine
# line. These stay in the corpus.
RUNTIME_SPEECH_THRESHOLD = 0.50

CLASS_KEEP = "keep"
CLASS_FORMAT = "quarantine-format"
CLASS_NONSPEECH = "quarantine-nonspeech"

DEFAULT_CORPUS = "/home/zoe/.zoe-voice-samples"

# A file untouched for this long is not one a capture is still writing. Real
# captures are seconds long and written in a single `shutil.copyfile` pass.
MIN_QUIESCENT_S = 60.0


# ── format probe ──────────────────────────────────────────────────────────────

def probe_format(path: str | os.PathLike) -> dict[str, Any]:
    """Read a WAV header and split USABILITY from CONTRACT CONFORMANCE.

    Pure stdlib and offline — the half of the classifier that needs no model.

    Two independent verdicts, and conflating them is the bug this function was
    rewritten to fix (Codex P2, #1643):

    ``ok``        the replay path can actually transcribe this file. False ONLY
                  for unparseable RIFF, zero audio frames, or a rate <= 0 —
                  precisely what ``_prepare_audio_for_moonshine`` refuses. This
                  is the quarantine decision.
    ``conforms``  the file matches the 16 kHz / mono / s16 CAPTURE contract.
                  False sets ``drift`` to a human description and is REPORTED,
                  never quarantined: the file is still usable evidence.
    """
    info: dict[str, Any] = {
        "ok": False, "conforms": False, "reason": None, "drift": None,
        "rate": None, "channels": None, "sampwidth": None, "frames": None,
    }
    try:
        with wave.open(str(path), "rb") as w:
            info["rate"] = w.getframerate()
            info["channels"] = w.getnchannels()
            info["sampwidth"] = w.getsampwidth()
            info["frames"] = w.getnframes()
    except Exception as exc:  # wave.Error, EOFError, OSError …
        info["reason"] = f"unreadable WAV ({exc.__class__.__name__}: {exc})"
        return info

    # ── UNUSABLE: the STT path itself cannot consume these. ──
    if not info["frames"]:
        info["reason"] = "zero audio frames"
        return info
    try:
        rate = int(info["rate"])
    except (TypeError, ValueError):
        rate = 0
    if rate <= 0:
        # _prepare_audio_for_moonshine deliberately will NOT pretend an
        # unknown rate is 16 kHz, so neither do we.
        info["reason"] = f"invalid sample rate ({info['rate']!r}) — cannot be resampled"
        return info

    info["ok"] = True

    # ── USABLE but off the capture contract: reported, kept. ──
    drift = []
    if rate != EXPECTED_RATE:
        drift.append(f"rate={rate} (contract {EXPECTED_RATE}; resampled by the replay path)")
    if info["channels"] != EXPECTED_CHANNELS:
        drift.append(f"channels={info['channels']} (contract {EXPECTED_CHANNELS}; downmixed)")
    if info["sampwidth"] != EXPECTED_SAMPWIDTH:
        drift.append(f"sampwidth={info['sampwidth']}B (contract {EXPECTED_SAMPWIDTH}B)")
    info["drift"] = "; ".join(drift) or None
    info["conforms"] = not drift
    return info


# ── speech scoring (the REAL voice_vad path) ──────────────────────────────────

class VadUnavailable(RuntimeError):
    """``voice_vad.create_vad()`` returned None — model missing or unloadable."""


def load_vad_factory(service_dir: str) -> Callable[[], Any]:
    """Import the LIVE ``voice_vad`` and return its ``create_vad``.

    Deliberately the production module, not a re-implementation: #1642 ruled out
    "preprocessing" as a cause precisely because the test drove the real path.
    A curation tool that scored audio its own way could quarantine files the
    live VAD hears perfectly well.
    """
    if service_dir not in sys.path:
        sys.path.insert(0, service_dir)
    import voice_vad  # noqa: E402  (path set up above)

    return voice_vad.create_vad


class UnscorableAudio(RuntimeError):
    """Readable, but this tool cannot normalise it to 16 kHz mono s16 for the VAD.

    NOT a quarantine reason — the caller treats it as "not scored", which keeps
    the file (this tool never quarantines on absent evidence).
    """


def _to_vad_pcm(raw: bytes, rate: int, channels: int, sampwidth: int) -> bytes:
    """Normalise decoded WAV bytes to 16 kHz MONO s16 — the VAD's input contract.

    Mirrors ``_prepare_audio_for_moonshine`` (downmix, then linear-interpolation
    resample, numpy only) so a drifted capture is scored on the audio the replay
    path would actually transcribe, not on a byte stream the VAD mis-reads as
    16 kHz. A conforming 16 kHz mono s16 file returns its bytes UNCHANGED —
    identity by construction, so this cannot perturb the 2026-08-04 census.

    Sample widths other than 2 bytes raise ``UnscorableAudio`` rather than being
    guessed at: none exist in the corpus, and inventing a decode would be the
    same class of error this rewrite is fixing.
    """
    if sampwidth != EXPECTED_SAMPWIDTH:
        raise UnscorableAudio(f"sampwidth={sampwidth}B — no s16 decode for this width")
    if rate == EXPECTED_RATE and channels == EXPECTED_CHANNELS:
        return raw

    import numpy as np

    a = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32)
    if channels > 1:
        usable = a.size - (a.size % channels)
        a = a[:usable].reshape(-1, channels).mean(axis=1)
    if a.size == 0:
        return b""
    if rate != EXPECTED_RATE:
        n_out = max(1, int(round(a.shape[0] * EXPECTED_RATE / rate)))
        a = np.interp(
            np.linspace(0.0, a.shape[0] - 1, n_out),
            np.arange(a.shape[0], dtype=np.float64),
            a,
        )
    return np.clip(np.rint(a), -32768, 32767).astype(np.int16).tobytes()


def score_speech(path: str | os.PathLike, vad_factory: Callable[[], Any]) -> dict[str, Any]:
    """Stream a WAV through a FRESH VAD (16k mono s16) and summarise the hops.

    One VAD per file (fresh recurrent state), 20 ms frames, state carried across
    the whole recording — the same streaming shape as a live turn. Off-contract
    captures are normalised first (``_to_vad_pcm``); conforming ones are byte-
    identical to what this function always fed the VAD.
    """
    import numpy as np  # only reached when voice_vad imported fine → numpy present

    vad = vad_factory()
    if vad is None:
        raise VadUnavailable("voice_vad.create_vad() returned None")

    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        rate = w.getframerate()
        raw = _to_vad_pcm(raw, rate, w.getnchannels(), w.getsampwidth())
        rate = EXPECTED_RATE

    probs: list[float] = []
    for i in range(0, len(raw), FRAME_BYTES):
        probs.extend(vad.process_hops(raw[i:i + FRAME_BYTES]))

    # A SHORT HOP COUNT IS ABSENT EVIDENCE, NOT MEASURED SILENCE.
    #
    # `process_hops` is documented "never raises": on an ONNX inference failure
    # it `break`s out of its hop loop with a DEBUG log and returns whatever it
    # had already computed. That has two shapes and BOTH are unsafe to score:
    #
    #   none at all — a model that loads but cannot infer returns [] for every
    #   file. peak=0.0 would put every readable WAV below any threshold and one
    #   `--execute` would move the entire replay corpus into non-speech
    #   quarantine, silently (Codex P1, #1643).
    #
    #   PARTIAL — inference succeeds for the leading hops and then starts
    #   failing. The list is non-empty, so a zero-length check passes it, but it
    #   covers only the START of the recording. A clip with quiet leading
    #   silence and speech AFTER the failure point yields a low peak drawn
    #   entirely from the silence, and real evidence gets quarantined on a
    #   truncated measurement. This is the more dangerous of the two, because
    #   nothing about the result looks wrong (Codex P1, #1643 round 4).
    #
    # So the count is verified against what the audio OWES. `process_hops`
    # consumes a hop per HOP_SAMPLES of the normalised stream and carries the
    # remainder internally, so a complete run returns exactly
    # `len(samples) // HOP_SAMPLES` probabilities. Anything short means the loop
    # broke early. Raising routes it to score_error → peak=None → KEEP, the
    # never-quarantine-on-absent-evidence rule the rest of the tool follows.
    expected_hops = (len(raw) // 2) // HOP_SAMPLES
    if not probs or len(probs) < expected_hops:
        raise UnscorableAudio(
            f"the VAD returned {len(probs)} hop(s) for audio owing {expected_hops} "
            "— inference failed part-way (or the clip is shorter than one hop); "
            "refusing to score a truncated measurement as silence"
        )

    samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0

    return {
        "peak": max(probs),
        "mean": sum(probs) / len(probs),
        "hops": len(probs),
        # Fraction of hops the LIVE threshold would call speech — context only.
        "frac_speech_hops": (
            sum(1 for p in probs if p >= RUNTIME_SPEECH_THRESHOLD) / len(probs)
        ),
        "rms": round(rms, 1),
        "duration_s": round(len(raw) / 2 / rate, 2) if rate else 0.0,
    }


# ── classification ────────────────────────────────────────────────────────────

def classify(fmt: dict[str, Any], peak: Optional[float],
             threshold: float = DEFAULT_SPEECH_THRESHOLD) -> tuple[str, str]:
    """(class, reason) for one file. Pure — the offline-testable core.

    UNUSABILITY wins over any speech score: a file the STT path cannot read is
    unusable regardless of what it contains. Contract DRIFT does not — a
    resampleable capture is kept and its drift annotated (Codex P2, #1643).
    ``peak is None`` means "not scored" (VAD skipped, or audio this tool cannot
    normalise) and always keeps — this tool never quarantines on absent evidence.
    """
    if not fmt.get("ok"):
        return CLASS_FORMAT, str(fmt.get("reason") or "format check failed")
    drift = fmt.get("drift")
    suffix = f" [DRIFT: {drift}]" if drift else ""
    if peak is None:
        return CLASS_KEEP, f"readable; speech not scored{suffix}"
    if peak < threshold:
        return CLASS_NONSPEECH, (
            f"peak speech probability {peak:.3f} < {threshold:.2f}{suffix}"
        )
    band = "" if peak >= RUNTIME_SPEECH_THRESHOLD else " (BORDERLINE, kept)"
    return CLASS_KEEP, f"peak speech probability {peak:.3f}{band}{suffix}"


def is_borderline(klass: str, peak: Optional[float]) -> bool:
    """Kept, but under the LIVE runtime threshold — reported, never moved."""
    return klass == CLASS_KEEP and peak is not None and peak < RUNTIME_SPEECH_THRESHOLD


# ── scan / plan / apply ───────────────────────────────────────────────────────

def list_corpus(corpus: str | os.PathLike) -> list[str]:
    """Top-level WAVs only — the exact selection every corpus consumer makes.

    Non-recursive by contract: quarantine subdirectories must stay invisible to
    this tool for the same reason they are invisible to the replay probe.
    """
    return sorted(glob.glob(os.path.join(str(corpus), "*.wav")))


def quarantine_dirname(klass: str, day: str) -> str:
    """``quarantine-nonspeech`` + ``20260804`` → ``quarantine-nonspeech-20260804``.

    Mirrors the existing precedent dir ``quarantine-tv-falsewakes-20260719``.
    """
    return f"{klass}-{day}"


def scan(corpus: str, threshold: float, vad_factory: Optional[Callable[[], Any]],
         limit: int = 0, progress: bool = False) -> list[dict[str, Any]]:
    """Classify every top-level WAV. No filesystem mutation happens here."""
    files = list_corpus(corpus)
    if limit:
        files = files[:limit]
    rows: list[dict[str, Any]] = []
    for n, path in enumerate(files, 1):
        st = os.stat(path)
        fmt = probe_format(path)
        scores: Optional[dict[str, Any]] = None
        score_error = None
        if fmt["ok"] and vad_factory is not None:
            try:
                scores = score_speech(path, vad_factory)
            except VadUnavailable:
                raise
            except Exception as exc:
                score_error = f"{exc.__class__.__name__}: {exc}"
        peak = scores["peak"] if scores else None
        klass, reason = classify(fmt, peak, threshold)
        rows.append({
            "file": os.path.basename(path),
            "class": klass,
            "reason": reason,
            "borderline": is_borderline(klass, peak),
            # Off the capture contract but USABLE — reported, never quarantined.
            "drift": bool(fmt.get("drift")) and klass != CLASS_FORMAT,
            "format": fmt,
            "scores": scores,
            "score_error": score_error,
            "mtime": st.st_mtime,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "size": st.st_size,
        })
        if progress and n % 100 == 0:
            print(f"  … scanned {n}/{len(files)}", file=sys.stderr, flush=True)
    return rows


def plan_moves(rows: list[dict[str, Any]], corpus: str, day: str) -> list[dict[str, Any]]:
    """Attach a destination to every quarantine row; keeps are untouched.

    A destination that already exists is flagged ``conflict`` and skipped — this
    tool never overwrites, because an overwrite is a delete in disguise.
    """
    plan: list[dict[str, Any]] = []
    for row in rows:
        if row["class"] == CLASS_KEEP:
            continue
        dest_dir = os.path.join(corpus, quarantine_dirname(row["class"], day))
        dest = os.path.join(dest_dir, row["file"])
        item = dict(row)
        item["source"] = os.path.join(corpus, row["file"])
        item["dest_dir"] = dest_dir
        item["dest"] = dest
        item["conflict"] = os.path.exists(dest)
        plan.append(item)
    return plan


def _entry_for(item: dict[str, Any], run_at: str, threshold: float,
               model_sha: Optional[str]) -> dict[str, Any]:
    """One manifest row. SHARED by the crash-safe sidecar and the manifest.

    PER-ENTRY PROVENANCE. The manifest's top-level fields describe the LATEST
    run, but ``entries`` is merged across runs — so on a second same-day pass
    with a different ``--speech-threshold`` or ``ZOE_SILERO_VAD_MODEL`` the older
    rows were being re-attributed to parameters they were never classified
    under, which makes the manifest useless for auditing or reversing a
    classification (Codex P2, #1643). Stamped at the moment the verdict is
    recorded, so a merge can never rewrite it.
    """
    return {
        "file": item["file"],
        "quarantined_at": run_at,
        "speech_threshold": threshold,
        "vad_model_sha256": model_sha,
        "reason": item["reason"],
        "class": item["class"],
        "scores": item["scores"],
        "format": item["format"],
        "mtime": item["mtime"],
        "mtime_iso": item["mtime_iso"],
        "size": item["size"],
    }


def _append_pending(dest_dir: str, entry: dict[str, Any]) -> None:
    """Append one JSONL row and FSYNC it, before the file it describes moves.

    ``manifest.jsonl`` is the crash-safe half of the record: append-only, one
    line per quarantined file, flushed and fsync'd so it survives an interrupted
    run. ``manifest.json`` is still the readable artifact, written at the end;
    if that write never happens, this file is what tells the operator why a
    directory full of audio is there. Best-effort by design — a sidecar failure
    must not stop the move, because a moved file with no record beats an
    unmovable corpus.
    """
    try:
        with open(os.path.join(dest_dir, "manifest.jsonl"), "a") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def apply_moves(plan: list[dict[str, Any]], corpus: str, threshold: float,
                model_sha: Optional[str], execute: bool) -> dict[str, Any]:
    """Move quarantined files and write/merge a manifest per quarantine dir.

    Moves only (``shutil.move``). There is no delete path in this module, and
    ``tests/unit/test_curate_voice_corpus.py`` fails if one ever appears.
    """
    result: dict[str, Any] = {
        "moved": 0, "conflicts": 0, "stale": 0, "errors": [], "manifests": [],
    }
    if not execute:
        result["conflicts"] = sum(1 for p in plan if p["conflict"])
        return result

    now = time.time()
    run_at = datetime.now(timezone.utc).isoformat()
    by_dir: dict[str, list[dict[str, Any]]] = {}
    for item in plan:
        if item["conflict"]:
            result["conflicts"] += 1
            result["errors"].append(f"{item['file']}: destination exists, left in place")
            continue
        os.makedirs(item["dest_dir"], exist_ok=True)
        # Re-checked at MOVE time, not trusted from plan time: `shutil.move`
        # clobbers an existing destination, so anything appearing in that window
        # is silently destroyed (cross-review, #1643).
        if os.path.exists(item["dest"]):
            result["conflicts"] += 1
            result["errors"].append(
                f"{item['file']}: destination appeared after planning, left in place"
            )
            continue
        # The SOURCE is re-checked too. The corpus is auto-captured
        # (`ZOE_VOICE_SAVE_AUDIO=1`) and `_maybe_capture_stt` writes with a plain
        # `shutil.copyfile` to the final name without taking the harness lock —
        # so `flock` does NOT serialise capture writes, and a turn finishing
        # mid-scan can be classified while half-written (truncated header →
        # "unreadable", partial audio → "non-speech") and then moved on that
        # stale verdict, with the writer completing through its open descriptor
        # afterwards. A valid real-voice sample would be quarantined on evidence
        # that no longer describes the file (Codex + Greptile P1, #1643).
        #
        # So: only move a file whose (size, mtime) still match what the scan
        # measured. Anything that changed is left in place for the next run,
        # when it will be classified on its final contents. Cheap, and it fails
        # in the safe direction — the cost of a miss is one extra pass, the cost
        # of a wrong move is evidence quarantined on a lie.
        try:
            st = os.stat(item["source"])
        except OSError as exc:
            result["errors"].append(f"{item['file']}: vanished before the move ({exc})")
            continue
        if (st.st_size, st.st_mtime) != (item["size"], item["mtime"]):
            result["stale"] += 1
            result["errors"].append(
                f"{item['file']}: changed on disk after classification "
                "(capture still in flight?), left in place"
            )
            continue
        # ...and a QUIESCENCE window on top, because two snapshots cannot see a
        # writer that made no progress between them: a paused `copyfile` leaves
        # size and mtime identical across both stats, and the move would then
        # relocate a still-open file for the writer to finish inside quarantine
        # (Codex P2, #1643). A capture that has not been touched for a full
        # minute is not one that is mid-write — real captures are seconds long
        # and written in one pass. This is a NARROWING, not a proof: a writer
        # stalled for longer than the window still defeats it. The proof lives
        # at the capture side (temp file + atomic rename in `_maybe_capture_stt`,
        # a voice-path file and therefore a separate replay-gated change); this
        # is what the curator can honestly guarantee against today's writer.
        if now - st.st_mtime < MIN_QUIESCENT_S:
            result["stale"] += 1
            result["errors"].append(
                f"{item['file']}: modified {now - st.st_mtime:.0f}s ago "
                f"(< {MIN_QUIESCENT_S}s quiescence), left in place"
            )
            continue
        # PROVENANCE IS WRITTEN BEFORE THE FILE IS ORPHANED, NOT AFTER THE BATCH.
        # Every move used to happen first and the manifests were written once at
        # the end, so a single interruption, full disk, or raise in the JSON
        # write left an ENTIRE batch of audio sitting in quarantine with no
        # record of why — the reason, scores, threshold and model hash gone, and
        # nothing to reverse the classification from (Codex P2, #1643). A
        # sidecar line is appended and fsync'd for each file at the moment it
        # moves, so the worst case is now one file's record, not 150.
        _append_pending(item["dest_dir"], _entry_for(item, run_at, threshold, model_sha))
        try:
            shutil.move(item["source"], item["dest"])
        except Exception as exc:
            result["errors"].append(f"{item['file']}: move failed ({exc})")
            continue
        result["moved"] += 1
        by_dir.setdefault(item["dest_dir"], []).append(item)

    for dest_dir, items in by_dir.items():
        manifest_path = os.path.join(dest_dir, "manifest.json")
        entries: list[dict[str, Any]] = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path) as fh:
                    prior = json.load(fh).get("entries")
                entries = prior if isinstance(prior, list) else []
            except Exception:
                entries = []
        for item in items:
            entries.append(_entry_for(item, run_at, threshold, model_sha))
        payload = {
            "tool": "scripts/maintenance/curate_voice_corpus.py",
            "corpus": corpus,
            # LATEST-RUN metadata. Kept for at-a-glance reading, but every
            # entry carries its OWN quarantined_at / speech_threshold /
            # vad_model_sha256 — read those when auditing, because these are
            # overwritten on every merge and describe only the last pass.
            "latest_run_at": run_at,
            "latest_speech_threshold": threshold,
            "latest_vad_model_sha256": model_sha,
            "quarantined_at": run_at,
            "speech_threshold": threshold,
            "runtime_speech_threshold": RUNTIME_SPEECH_THRESHOLD,
            "vad_model_sha256": model_sha,
            "note": ("Quarantine is a MOVE, never a delete. Corpus consumers glob "
                     "<corpus>/*.wav (top level only), so these files are out of the "
                     "replay set but preserved on disk. The top-level "
                     "speech_threshold/vad_model_sha256 describe the LATEST run "
                     "only; per-entry copies are authoritative."),
            "entries": entries,
        }
        # tmp + rename: a half-written manifest is not a manifest, and the files
        # it describes have already moved.
        tmp_path = manifest_path + ".tmp"
        with open(tmp_path, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(tmp_path, manifest_path)
        result["manifests"].append(manifest_path)
    return result


# ── reporting ─────────────────────────────────────────────────────────────────

def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def summarise(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    peaks = [r["scores"]["peak"] for r in rows if r["scores"]]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    return {
        "total": len(rows),
        "counts": counts,
        "borderline": sum(1 for r in rows if r["borderline"]),
        # Off the 16k/mono/s16 capture contract but usable — REPORTED, kept.
        "drift": sum(1 for r in rows if r.get("drift")),
        "scored": len(peaks),
        "speech_threshold": threshold,
        "peak_median": round(_percentile(peaks, 0.5), 3),
        "peak_p10": round(_percentile(peaks, 0.10), 3),
        "peak_p90": round(_percentile(peaks, 0.90), 3),
        "frac_above_runtime": round(
            sum(1 for p in peaks if p >= RUNTIME_SPEECH_THRESHOLD) / len(peaks), 4
        ) if peaks else 0.0,
    }


def _sha256(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _print_candidates(plan: list[dict[str, Any]], klass: str, limit: int = 40) -> None:
    items = [p for p in plan if p["class"] == klass]
    if not items:
        return
    print(f"\n{klass} — {len(items)} file(s):")
    for item in items[:limit]:
        peak = item["scores"]["peak"] if item["scores"] else None
        extra = f"  peak={peak:.3f} rms={item['scores']['rms']}" if item["scores"] else ""
        print(f"  {item['file']}  [{item['mtime_iso'][:16]}]  {item['reason']}{extra}")
    if len(items) > limit:
        print(f"  … and {len(items) - limit} more (see --json report)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=os.environ.get("ZOE_VOICE_SAMPLE_DIR") or DEFAULT_CORPUS)
    ap.add_argument("--service-dir", default=None,
                    help="dir holding voice_vad.py (default: this repo's services/zoe-data)")
    ap.add_argument("--speech-threshold", type=float, default=DEFAULT_SPEECH_THRESHOLD,
                    help=f"peak-probability quarantine line (default {DEFAULT_SPEECH_THRESHOLD}; "
                         f"the LIVE runtime threshold is {RUNTIME_SPEECH_THRESHOLD} and is "
                         "deliberately NOT reused — see the module docstring)")
    ap.add_argument("--skip-vad", action="store_true",
                    help="format audit only (no speech scoring, nothing quarantined as non-speech)")
    ap.add_argument("--limit", type=int, default=0, help="scan only the first N files (debug)")
    ap.add_argument("--json", help="write the full census here (a REPORT path: it is\n                    written on a dry run too, since it mutates nothing in the corpus)")
    ap.add_argument("--execute", action="store_true",
                    help="actually MOVE the failures into dated quarantine dirs "
                         "(default is a dry run; take flock /tmp/zoe-voice-harness.lock)")
    args = ap.parse_args()

    # A probability, validated BEFORE anything is scanned or moved. `type=float`
    # happily accepts `20` (a mistyped `0.20`), under which EVERY scored WAV
    # satisfies `peak < 20` and the entire active corpus is quarantined in one
    # `--execute` — the exact opposite of this tool's purpose. `nan` is worse
    # than useless: every comparison against it is False, so nothing would be
    # quarantined and the run would report a clean corpus (Codex P2, #1643).
    if not (0.0 <= args.speech_threshold <= 1.0):  # NaN fails this too
        print(f"--speech-threshold must be a probability in [0, 1], got: "
              f"{args.speech_threshold}", file=sys.stderr)
        return 2

    corpus = os.path.abspath(os.path.expanduser(args.corpus))
    if not os.path.isdir(corpus):
        print(f"corpus not found: {corpus}", file=sys.stderr)
        return 2

    repo = Path(__file__).resolve().parents[2]
    service_dir = args.service_dir or str(repo / "services" / "zoe-data")

    vad_factory = None
    model_sha = None
    if not args.skip_vad:
        try:
            vad_factory = load_vad_factory(service_dir)
        except Exception as exc:
            print(f"cannot import voice_vad from {service_dir}: {exc}", file=sys.stderr)
            return 2
        if vad_factory() is None:
            print("Silero VAD unavailable (model missing / failed to load). "
                  "Re-run with --skip-vad for a format-only audit, or fix the model — "
                  "this tool refuses to classify speech it cannot measure.", file=sys.stderr)
            return 2
        model_sha = _sha256(os.environ.get("ZOE_SILERO_VAD_MODEL", "").strip()
                            or "/home/zoe/models/silero_vad.onnx")

    day = time.strftime("%Y%m%d")
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"curate_voice_corpus [{mode}] corpus={corpus} "
          f"threshold={args.speech_threshold} vad={'off' if args.skip_vad else 'on'}")

    t0 = time.monotonic()
    rows = scan(corpus, args.speech_threshold, vad_factory, args.limit, progress=True)
    plan = plan_moves(rows, corpus, day)
    summary = summarise(rows, args.speech_threshold)

    print(f"\nscanned {summary['total']} top-level WAV(s) in {time.monotonic() - t0:.1f}s")
    for klass in (CLASS_KEEP, CLASS_FORMAT, CLASS_NONSPEECH):
        print(f"  {klass:22s} {summary['counts'].get(klass, 0)}")
    print(f"  {'(of keeps: BORDERLINE)':22s} {summary['borderline']}"
          f"   [{args.speech_threshold} <= peak < {RUNTIME_SPEECH_THRESHOLD}, left in corpus]")
    print(f"  {'(of keeps: DRIFT)':22s} {summary['drift']}"
          f"   [off the {EXPECTED_RATE}Hz/mono/s16 capture contract but resampleable — "
          "REPORTED, left in corpus; fix at the SAVE path]")
    if summary["scored"]:
        print(f"  peak speech prob: median={summary['peak_median']} "
              f"p10={summary['peak_p10']} p90={summary['peak_p90']} "
              f"frac>= {RUNTIME_SPEECH_THRESHOLD}: {summary['frac_above_runtime']}")

    # A SPEECH AUDIT THAT SCORED NOTHING IS A FAILED RUN, NOT A CLEAN CORPUS.
    # score_speech's failures are caught per file and recorded as score_error, so
    # a Silero that loads but cannot infer produces a run where EVERY file is
    # correctly kept as unscored — and the output then looks like a tidy
    # format-only audit that found no candidates and exits 0. That is the same
    # silent-success shape this whole tool exists to prevent, one level up
    # (Codex P2, #1643). Refuse before anything moves.
    if vad_factory is not None and summary["total"] and not summary["scored"]:
        errs = sorted({r["score_error"] for r in rows if r["score_error"]})
        print(f"\nSPEECH SCORING PRODUCED NOTHING: 0 of {summary['total']} file(s) "
              "were scored, so no non-speech verdict in this run is supported by "
              "evidence. Refusing to continue -- this is a broken VAD, not a clean "
              "corpus.", file=sys.stderr)
        for e in errs[:5]:
            print(f"  ! {e}", file=sys.stderr)
        print("Re-run with --skip-vad for an explicit format-only audit.", file=sys.stderr)
        return 2

    _print_candidates(plan, CLASS_FORMAT)
    _print_candidates(plan, CLASS_NONSPEECH)

    applied = apply_moves(plan, corpus, args.speech_threshold, model_sha, args.execute)
    print()
    if args.execute:
        print(f"MOVED {applied['moved']} file(s); manifests: "
              + (", ".join(applied["manifests"]) or "none"))
    else:
        print(f"DRY-RUN — nothing moved. {len(plan)} file(s) would move into "
              f"{corpus}/quarantine-*-{day}/. Re-run with --execute under "
              f"flock /tmp/zoe-voice-harness.lock.")
    if applied["conflicts"]:
        print(f"⚠ {applied['conflicts']} destination(s) already exist and were left in place")
    for err in applied["errors"]:
        print(f"  ! {err}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "mode": mode, "corpus": corpus, "day": day,
                "vad_model_sha256": model_sha,
                "summary": summary, "rows": rows,
                "plan": [{k: v for k, v in p.items() if k != "format"} for p in plan],
                "applied": applied,
            }, fh, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
