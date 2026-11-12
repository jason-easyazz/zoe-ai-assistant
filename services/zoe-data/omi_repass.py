"""The pendant NIGHT RE-PASS: the best transcript the box can make of a day's pendant audio, made while Zoe sleeps.

Flag ``ZOE_OMI_NIGHT_REPASS`` = off | shadow | on (default OFF). Spec and owner acceptance: ``docs/knowledge/omi-night-repass.md``.

Moonshine (the live STT rock, in-process, 20 s chunks, between voice turns) writes the FIRST transcript within minutes of a sync. This module is
the SECOND, offline pass the night window runs before the 12B loads, with the 4B brain and Kokoro stopped: faster-whisper large-v3 (distil-large-v3
when the day's audio would overrun the budget) with VAD, word timestamps and a household name hint, plus the panel ResNet34 speaker units matched against the
wearer's pendant-channel print and the voices the owner has taught Zoe. The model work happens in ``scripts/night/jobs/omi_repass_worker.py`` (its
own venv, its own capped process); everything that DECIDES - what to run, how to label, what to store, what to keep - is here, pure where it can be.

  off     nothing changes; the first pass deletes a WAV exactly as before.
  shadow  a first-pass WAV is HELD until the night pass has seen it; the pass transcribes and logs COUNTS (word agreement vs Moonshine, speaker
          counts, durations - never a word) and replaces nothing.
  on      as shadow, and the improved transcript + speaker segments REPLACE the Moonshine ones, with provenance (``repass_model``, ``repass_at``).

Invariants (each has a negative control in ``tests/test_omi_repass.py``):
  * AUDIO IS NEVER DELETED BEFORE THE PASS HAS PROCESSED IT: with the flag on or shadow, ``_transcribe_one`` holds the WAV (``repass_state =
    'pending'``) and ``release_tagged_audio`` skips it. The hold is bounded by the existing retention maximum (``audio_expires_at``): past it the
    WAV goes whether or not the pass ran, exactly as any kept WAV does.
  * FORGOTTEN STAYS FORGOTTEN: every new line is redacted against the forget ledger (``_redact_forgotten``, as the first pass does) and the write
    goes through ``user_erase_gate.writing`` - a member erased since the job started has nothing written and the recording is tombstoned.
  * NO SILENT SELF-LABELLING: a line is "me" outright only against a CONFIRMED pendant wearer print (windows the owner labelled "me", or a heuristic proposal the owner confirmed); the owner's
    consented PANEL profile can only make it "you?" (leaning). A turn is labelled only with a clear margin over the runner-up (>= 0.10; >= 0.20 under 1.2 s), leaned at >= 0.05, else "?" (see
    ``omi_repass_who``). A model label never overwrites a human one: tags, "me/other" labels and not-a-person marks on the first-pass segments are carried onto the overlapping new segments.
  * A suggestion is never a tag: a recognised family member / guest voice becomes ``suggested_person_id`` (the owner confirms), as in omi_voices.
  * Unfinished work carries over: a recording the budget could not reach stays ``pending`` for the next night (it is not a failure).
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np

import user_erase_gate as erase_gate
from typed_env import env_float, env_int, env_str

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
import speaker_models                              # noqa: E402 - the panel's own speaker model: ONE speaker space for the panel, named guests, the first pass and this pass
import omi_repass_who as who                       # noqa: E402
import omi_diarize                                 # noqa: E402 - the first pass's pendant-channel bars (one shoulder for the first pass and the relabel)

MODEL_ID = speaker_models.RESNET34_MODEL_ID        # the speaker-space tag on every embedding / voiceprint this pass writes: the SAME model as the panel (WeSpeaker R34 int8, c1)
MAX_PASS_ATTEMPTS = 3
#: the HOLD (owner, 2026-11-12): a recording still owed its re-pass keeps its WAV past the 7-day retention - leftovers carry to the next window - but never past this many days
#: from upload, and never while the audio disk is short (``omi_recordings.purge_expired_audio``, which logs every forced delete loudly)
HOLD_CAP_DAYS = 30
HOLD_MIN_FREE_MB = 2048
MIN_WEARER_WINDOWS = 10                           # a wearer print needs this many confirmed 1.5 s windows before any line may read "me"
#: THE BACKEND SEAM. Each ASR engine the worker can run (``omi_repass_worker.ASR``) is DECLARED here with its budget facts, so the window's RAM / time
#: arithmetic is per backend and a new engine is one registry entry in the worker plus one row here (the bake-off owns installing models):
#:   rtf          seconds of work per second of speech on the device it runs on - the plan's default until a night measures its own (state.json)
#:   gpu_rtf      the same on the GPU, or None = never measured here (the planner never assumes a GPU figure)
#:   peak_rss_mb  the engine's declared peak resident size (+ the speaker model, ``DIAR_RSS_MB``): sets the capped scope's MemoryMax and the RAM check
#:   fallback     the cheaper backend the planner drops to when the day's audio would overrun the budget (None = none)
#:   device       "gpu" (needs the GPU recipe: drop caches + compact IMMEDIATELY before the load, one model alone) | "cpu"
#: ROUND-2 BAKE-OFF (2026-11-09, Sunday audio, Jetson Orin): qwen3-asr is the default (GPU RTF 0.42 per speech second, ~4.0 GB GPU, ~8.7 GB process RSS, ~14 s load); parakeet-tdt the
#: fallback for a day that would overrun it (GPU RTF 0.08, ~5.7 GB RSS). faster-whisper stays registered as a CPU last resort only: GPU large-v3 reached RTF 0.74 and needs a
#: hand-built CTranslate2. Gemma 12B's own audio was rejected.
BACKENDS: dict = {
    "qwen3-asr": {"rtf": 0.42, "gpu_rtf": 0.42, "peak_rss_mb": 8700, "gpu_peak_mb": 4000, "fallback": "parakeet-tdt", "device": "gpu"},
    "parakeet-tdt": {"rtf": 0.08, "gpu_rtf": 0.08, "peak_rss_mb": 5700, "gpu_peak_mb": 2500, "fallback": None, "device": "gpu"},
    #: the third ROVER voter (production) and the evaluation contenders. RTF per speech second = minutes per speech hour / 60 as measured in the round-2 bake-off: distil ~26, large-v3 ~44,
    #: Granite ~28 min per hour. Peak RSS for the two Whisper rows is DECLARED (not yet measured on the CUDA CTranslate2 build): the daytime rehearsal reports the real one.
    "whisper-gpu:distil-large-v3": {"rtf": 0.44, "gpu_rtf": 0.44, "peak_rss_mb": 4500, "fallback": None, "device": "gpu"},
    "whisper-gpu:large-v3": {"rtf": 0.74, "gpu_rtf": 0.74, "peak_rss_mb": 5500, "fallback": None, "device": "gpu"},
    "granite-speech": {"rtf": 0.47, "gpu_rtf": 0.47, "peak_rss_mb": 9700, "gpu_peak_mb": 4500, "fallback": None, "device": "gpu"},
    "faster-whisper:large-v3": {"rtf": 1.0, "gpu_rtf": None, "peak_rss_mb": 2100, "fallback": "faster-whisper:distil-large-v3", "device": "cpu"},
    "faster-whisper:distil-large-v3": {"rtf": 0.45, "gpu_rtf": None, "peak_rss_mb": 2400, "fallback": None, "device": "cpu"},
}
DEFAULT_ASR = "qwen3-asr"
FLOOR_MB = 1200                                    # the window's MemAvailable floor: a pass only starts if the box can hold the engine + the speaker model and still clear it
DIAR_BACKENDS = ("resnet34",)                        # the speaker-window engines the worker has (``omi_repass_worker.DIAR``); MODEL_ID is tied to this one
DIAR_RSS_MB = 400                                  # the R34 int8 session + its fbank; no torch
DEFAULT_RTF = {k: v["rtf"] for k, v in BACKENDS.items()}
PLAN_MARGIN = 0.85                                # plan to use at most this much of the budget (the estimate is a mean over files of very different density)
LOAD_S = 40.0                                     # one worker's model load + start, as the plan prices it
SPEECH_FRAC = 0.35                                # share of a pendant recording that is speech, until a night measures its own (state.json speech_frac; 2026-11-12: 0.354)
HINT_MAX_NAMES = 30
PROPOSAL_KEEP_DAYS = 14
PROPOSAL_MAX = 20
WEARER_LINK = 0.6                                  # the greedy cosine linkage both wearer proposals cluster with (the night heuristic and the stored-sample one)
#: The wearer print FROM STORED SAMPLES (``propose_wearer_from_stored``): a candidate voice must be heard in at least this many DISTINCT recordings, at most this many candidates are
#: offered side by side, and one recording gives a candidate at most this many windows (so one long conversation cannot define a voice). Measured on the live box 2026-11-12 (counts only):
#: 2,156 stored segment embeddings over 254 recordings; at link 0.6 the top voice spans 87 recordings (194 segments), the runner-up 65.
STORED_MIN_RECORDINGS = 10
STORED_MAX_VOICES = 3
STORED_PER_RECORDING = 2
#: ``relabel_stored_from_print``: a stored segment reads "me" only at this cosine to the CONFIRMED pendant print or above - the SAME provisional shoulder the first pass uses against
#: that print (``omi_diarize.PENDANT_YOU_MIN``, measurement in its comment): a false "me" is the worse error, so the band below it stays unlabelled rather than guessed.
#: This is the DEFAULT only: the relabel resolves the live bar through ``omi_diarize.thresholds("pendant")`` at call time.
RELABEL_YOU_MIN = omi_diarize.PENDANT_YOU_MIN
_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,38}$")
_ST_OK = "('done', 'empty_transcript')"
#: THE ONE "still owed its re-pass" predicate. ``pending_recordings`` lists these rows and the retention purge / the hourly release HOLD their WAV, from this
#: same fragment so the two can never drift (an empty first pass keeps the migration default repass_state '' - it is owed all the same).
OWED_SQL = f"(status IN {_ST_OK} AND audio_path != '' AND repass_state IN ('pending', '') AND repass_attempts < {MAX_PASS_ATTEMPTS})"


# ── flags ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def mode() -> str:
    m = env_str("ZOE_OMI_NIGHT_REPASS", "off").strip().lower()
    return m if m in MODES else "off"


def asr_backend() -> str:
    """``ZOE_OMI_NIGHT_ASR`` - the ASR backend id (a key of ``BACKENDS``). An unknown id is returned as is: ``run_night`` refuses it loudly rather than guessing."""
    return env_str("ZOE_OMI_NIGHT_ASR", DEFAULT_ASR).strip() or DEFAULT_ASR


def diar_backend() -> str:
    return env_str("ZOE_OMI_NIGHT_DIAR", "resnet34").strip() or "resnet34"


def scope_mem_max(asr: str, diar: bool = True) -> str:
    """The capped scope's MemoryMax: ``ZOE_OMI_REPASS_MEM_MAX`` if set, else the declared peaks + 20 % headroom."""
    forced = env_str("ZOE_OMI_REPASS_MEM_MAX", "")
    if forced:
        return forced
    return f"{int((BACKENDS[asr]['peak_rss_mb'] + (DIAR_RSS_MB if diar else 0)) * 1.2)}M"


def need_mb(asr: str, diar: bool = True) -> int:
    """MemAvailable the pass needs before it starts: the engine's declared peak + the speaker model (only the first pass loads it) + the floor."""
    return int(BACKENDS[asr]["peak_rss_mb"] + (DIAR_RSS_MB if diar else 0) + FLOOR_MB)


#: engines that are EVALUATION-only, whatever the config says: adding Granite as a fourth voter measured WORSE (5.0-5.2 % WER against 4.5 %)
NEVER_VOTE = ("granite-speech",)


def _ids(name: str, default: str) -> list[str]:
    return [x.strip() for x in env_str(name, default).split(",") if x.strip() and x.strip() in BACKENDS]


def _voters(name: str, default: str) -> list[str]:
    return [x for x in _ids(name, default) if x not in NEVER_VOTE]


def second_backend() -> str:
    """The second production voter (``ZOE_OMI_NIGHT_ASR_SECOND``, default parakeet-tdt; empty = none)."""
    return (_voters("ZOE_OMI_NIGHT_ASR_SECOND", "parakeet-tdt") or [""])[0]


def third_choices() -> list[str]:
    """The third production voter, in preference order (``ZOE_OMI_NIGHT_ASR_THIRD``): ROVER needs at least three voters to outvote the pivot. Whisper large-v3 (GPU) when the night
    has room - the measured best third voter - else distil-large-v3 (about 40 % cheaper, within noise of it on the hand reference). The first that fits the budget is used."""
    return _voters("ZOE_OMI_NIGHT_ASR_THIRD", "whisper-gpu:large-v3,whisper-gpu:distil-large-v3")


def eval_backends() -> list[str]:
    """The EVALUATION contenders (``ZOE_OMI_NIGHT_ASR_EVAL``): a rotating sample of the day's speech, never in the stored text. Whatever voted that night is left out; Granite is
    evaluation-only because measured adding it as a fourth voter HURTS the vote (5.0-5.2 % WER against 4.5 % for Qwen3 + Parakeet + Whisper large-v3)."""
    return _ids("ZOE_OMI_NIGHT_ASR_EVAL", "whisper-gpu:large-v3,whisper-gpu:distil-large-v3,granite-speech")


def ct2_dir() -> str:
    return env_str("ZOE_OMI_REPASS_CT2_DIR", "") or os.path.expanduser("~/.zoe/ct2build")


def mem_available_mb() -> float:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 1e9            # unreadable: do not block on a guess


def models_dir() -> str:
    return env_str("ZOE_OMI_REPASS_MODELS", "") or os.path.expanduser("~/.zoe/models/omi-repass")


def gpu_prep() -> None:
    """THE GPU RECIPE (round-2 bake-off, after earlier NvMap error-12 failures): with the 4B, Kokoro, the router and zoe-data stopped, drop the page cache and compact physical
    memory IMMEDIATELY before the model loads, and load ONE model alone (the worker loads nothing else on the GPU). Best effort: no passwordless sudo = the window's own compaction stands."""
    try:
        subprocess.run(["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory"], timeout=60, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def holds_audio() -> bool:
    """The first pass keeps a WAV for the night pass while the flag is shadow or on."""
    return mode() != "off"


def defers_review() -> bool:
    """Only ``on`` replaces text, so only ``on`` makes the day review wait for it (shadow leaves the text alone: nothing to wait for)."""
    return mode() == "on"


#: SQL fragment: a recording the day review must not read yet - the night pass will replace its text tonight. Bounded: a row whose WAV is gone
#: (retention, release) is never waiting.
PENDING_SQL = "(repass_state = 'pending' AND audio_path != '')"


def home_dir() -> Path:
    return Path(env_str("ZOE_OMI_REPASS_DIR", "") or os.path.expanduser("~/.zoe/omi-repass"))


def venv_python() -> str:
    return os.path.join(env_str("ZOE_OMI_REPASS_VENV", "") or os.path.expanduser("~/.zoe/venvs/omi-repass"), "bin", "python")


def worker_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "night" / "jobs" / "omi_repass_worker.py"


# ── names ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def clean_names(names: Iterable[Any]) -> list[str]:
    """First words of display names that are plain letters; de-duplicated case-insensitively, order kept, capped."""
    seen, out = set(), []
    for raw in names:
        n = str(raw or "").strip()
        if not _NAME_OK.match(n):
            continue
        n = n.split()[0].strip("-'")
        if len(n) < 2 or n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
    return out[:HINT_MAX_NAMES]


def name_hint(names: Iterable[Any]) -> str:
    """The ASR prompt that steers spelling: ``People who may be spoken to: A, B, C.`` (empty when there are no names)."""
    n = clean_names(names)
    return f"People who may be mentioned: {', '.join(n)}." if n else ""


def _variants(name: str) -> set[str]:
    low = name.lower()
    out: set[str] = set()
    for a, b in (("ie", "y"), ("y", "ie"), ("ey", "y"), ("y", "ey"), ("ee", "y"), ("y", "ee")):
        if low.endswith(a) and len(low) > len(a) + 1:
            out.add(low[: -len(a)] + b)
    out.discard(low)
    return out


def normalise_names(text: str, names: Iterable[Any]) -> str:
    """Whisper writes the common spelling ("Billy") of a household name spelt another way ("Billie"): map a variant spelling to the hinted one.
    Whole words only, case kept in shape; a word that is itself a hinted name is never touched."""
    ns = clean_names(names)
    have = {n.lower() for n in ns}
    table: dict[str, str] = {}
    for n in ns:
        for v in _variants(n):
            if v not in have:
                table[v] = n
    if not table:
        return text
    return re.sub(r"[A-Za-z']+", lambda m: table.get(m.group(0).lower(), m.group(0)), text)


# ── planning ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def plan_model(audio_s: float, budget_s: float, rtf: Optional[dict] = None, preferred: str = DEFAULT_ASR, fallback: Optional[str] = None) -> dict:
    """Which ASR backend (``BACKENDS`` id; the fallback defaults to the preferred one's declared fallback) for ``audio_s`` seconds of audio in ``budget_s`` seconds. The preferred model when its estimate fits the margin; else the
    fallback; either way ``fits`` says whether the WHOLE day is expected to finish (when it is not, the worker takes recordings oldest first and
    the rest carries to the next night). ``rtf`` = seconds of work per second of audio (last night's measured figures win over the bake-off's)."""
    fallback = fallback or (BACKENDS.get(preferred) or {}).get("fallback")
    r = {**DEFAULT_RTF, **{k: float(v) for k, v in (rtf or {}).items() if v}}
    usable = max(0.0, budget_s) * PLAN_MARGIN
    est = {m: audio_s * r.get(m, 1.0) for m in (preferred, fallback) if m}
    if est[preferred] <= usable or not fallback:
        return {"model": preferred, "est_s": round(est[preferred]), "fits": est[preferred] <= usable, "fallback": False}
    return {"model": fallback, "est_s": round(est[fallback]), "fits": est[fallback] <= usable, "fallback": True}


# ── words, windows, lines (pure) ─────────────────────────────────────────────────────────────────────────────────────────

def _unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


Refs = who.Refs
label_window = who.label_window
#: omi_voices.learn's "this unsure line is far enough from the wearer to learn as a bystander" bar: an ``unsure`` line is held only at <= LEARN_YOU_MIN - LOW_MARGIN (0.10).
#: Against the confirmed pendant print a turn in [0.35, 0.6) may only LEAN "you?" and is stored ``unsure`` with its wearer score, so the old 0.5 (held at <= 0.40) would keep
#: 0.35-0.40 wearer-tail lines as a bystander's voice. 0.4499 holds an unsure line only below the pendant ``other`` bar (<= 0.3499, under omi_diarize.PENDANT_OTHER_MAX 0.35):
#: bystander turns still learn (the measured bulk against a pendant print is 0.2-0.3; a matched person is ``other`` and always learnable). PROVISIONAL with those bars.
LEARN_YOU_MIN = 0.4499


def build_lines(words: list, windows: list, refs: Refs, *, gap_s: float = 2.0, smooth: int = 3, zoe_spans: Iterable = (), anchors: Iterable = (),
                zoe_by_log: bool = False) -> list[dict]:
    """Words (``[word, t0, t1, p]``) + windows (``[t0, t1, emb, rms]``) -> speaker lines: each word takes the label of the windows covering its
    midpoint (a vote, strong verdicts weigh double), labels are smoothed over +-``smooth`` words, and runs of one label join into a line unless a
    silence of ``gap_s`` separates them. A one-or-two-word run of an uncertain label between two lines is absorbed (label flicker)."""
    if not words:
        return []
    ws = [(float(w[1]), float(w[2])) for w in words]
    spans = [(int(a), int(b)) for a, b in zoe_spans]
    anc = [(int(a["start_ms"]), int(a["end_ms"]), str(a["key"])) for a in anchors]       # panel utterances of a KNOWN speaker (omi_panel_anchor): above the voice match
    if zoe_by_log and refs.zoe is not None:        # the panel log covers this recording: Zoe is what she SAID, never what a voice print resembles
        refs = copy.copy(refs)
        refs.zoe = None

    def zoe_hit(t0: float, t1: float) -> bool:       # the panel log's Zoe replies are exact: a unit mostly inside one IS Zoe
        d = max(1.0, (t1 - t0) * 1000.0)
        return any(max(0.0, min(t1 * 1000.0, b) - max(t0 * 1000.0, a)) / d >= 0.5 for a, b in spans)

    def anchored(t0: float, t1: float) -> Optional[str]:
        d = max(1.0, (t1 - t0) * 1000.0)
        best = max(((max(0.0, min(t1 * 1000.0, b) - max(t0 * 1000.0, a)) / d, k) for a, b, k in anc), default=(0.0, None))
        return best[1] if best[0] >= 0.5 else None

    def verdict(w) -> dict:
        t0, t1 = float(w[0]), float(w[1])
        key = anchored(t0, t1)
        if key and not zoe_hit(t0, t1):               # the panel's own record of who spoke beats the voice match; Zoe's logged speech beats the anchor
            return {"key": key, "conf": "sure", "ranked": [], "margin": None}
        return label_window(w[2], refs, dur_s=t1 - t0, rms=(float(w[3]) if len(w) > 3 else None), zoe_hit=zoe_hit(t0, t1))

    verdicts = [(float(w[0]), float(w[1]), verdict(w)) for w in windows]
    raw: list[str] = []
    for a, b in ws:
        mid = (a + b) / 2.0
        votes: dict[str, int] = {}
        for t0, t1, v in verdicts:
            if t0 - 0.1 <= mid <= t1 + 0.1:
                votes[v["key"]] = votes.get(v["key"], 0) + {"sure": 2.0, "lean": 1.5}.get(v["conf"], 1.0)
        raw.append(max(votes.items(), key=lambda kv: kv[1])[0] if votes else "unverified" if not refs.has_prints() else "?")
    sm: list[str] = []
    for i in range(len(raw)):
        win = raw[max(0, i - smooth): i + smooth + 1]
        top = sorted(((win.count(k), k) for k in set(win)), reverse=True)
        sm.append(top[0][1] if len(top) == 1 or top[0][0] > top[1][0] else raw[i])
    lines: list[dict] = []
    for w, (a, b), lab in zip(words, ws, sm):
        if lines and lines[-1]["key"] == lab and a - lines[-1]["end"] < gap_s:
            ln = lines[-1]
            ln["text"] += str(w[0])
            ln["end"] = b
            ln["ps"].append(float(w[3]))
        else:
            lines.append({"key": lab, "start": a, "end": b, "text": str(w[0]), "ps": [float(w[3])]})
    merged: list[dict] = []
    for ln in lines:
        if merged and ln["start"] - merged[-1]["end"] < gap_s and len(ln["text"].split()) <= 2 and ln["key"] in ("?", "unverified") \
                and ln["key"] != merged[-1]["key"]:
            merged[-1]["text"] += ln["text"]
            merged[-1]["end"] = ln["end"]
            merged[-1]["ps"] += ln["ps"]
        elif merged and merged[-1]["key"] == ln["key"] and ln["start"] - merged[-1]["end"] < gap_s:
            merged[-1]["text"] += ln["text"]
            merged[-1]["end"] = ln["end"]
            merged[-1]["ps"] += ln["ps"]
        else:
            merged.append(ln)
    out = []
    for ln in merged:
        text = re.sub(r"\s+", " ", ln["text"]).strip()
        if not text:
            continue
        if ln["key"] == "zoe" and re.search(r"\bzoe\b", text, re.I):
            ln["key"] = "?"                           # a line that NAMES Zoe is addressed to her: never her own
        cover = [w for w in windows if float(w[0]) < ln["end"] and float(w[1]) > ln["start"]]
        emb = _unit(np.mean([_unit(w[2]) for w in cover], axis=0)) if cover else None
        out.append({"cands": who.candidates(emb, refs) if (emb is not None and ln["key"].endswith("?")) else [], "key": ln["key"], "start_ms": int(round(ln["start"] * 1000)), "end_ms": max(int(round(ln["end"] * 1000)), int(round(ln["start"] * 1000)) + 1),
                    "text": text, "windows": len(cover), "embedding": emb, "low_conf": sum(p < 0.4 for p in ln["ps"]) / max(1, len(ln["ps"])) > 0.3})
    return out


def segment_fields(line: dict, refs: Refs) -> dict:
    """A line as the ``omi_segments`` insert wants it (``omi_voices.store_segments``). 'p:ID' -> ``other`` + a SUGGESTED person (a suggestion is never a tag); 'X?' / '?' -> ``unsure`` (+ the
    leaning person as a suggestion) and a ``who_said`` item for the owner; ``zoe`` -> ``other`` + ``not_person='zoe'`` (the pendant heard Zoe's own voice, not a person)."""
    key = line["key"]
    kind, pid, leaning = who.key_parts(key)
    e = line.get("embedding")
    score = None
    if e is not None and refs.you is not None:
        score = round(float(refs.you @ who.remove_mean(e, refs.mu)), 4)
    speaker = {"you": "you", "zoe": "other", "person": "other", "unknown": "unsure", "unverified": "unverified"}[kind]
    if leaning:
        speaker = "unsure"
    pscore = None
    if pid and e is not None:
        mine = [p for p in refs.people if p["person"] == pid]
        pscore = round(float(mine[0]["v"] @ who.remove_mean(e, refs.mu)), 4) if mine else None
    return {"start_ms": line["start_ms"], "end_ms": line["end_ms"], "speaker": speaker, "score": score, "windows": line["windows"], "embedding": e,
            "text": line["text"], "not_person": "zoe" if kind == "zoe" else None, "person": pid, "person_score": pscore,
            "mark": "?" if (key.endswith("?")) else "", "lean": bool(leaning), "cands": line.get("cands") or []}


def word_agreement(a: str, b: str, cap: int = 2500) -> float:
    """How much of two transcripts is the same words, in order (0..1): the shadow log's headline number. Counts only; no text leaves this."""
    x, y = re.findall(r"[a-z0-9']+", a.lower())[:cap], re.findall(r"[a-z0-9']+", b.lower())[:cap]
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return round(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(), 4)


def carry_labels(new: list[dict], old: list[dict], min_share: float = 0.5) -> int:
    """Copy the owner's DECISIONS from the first-pass segments onto the new segment they cover (>= ``min_share`` of it, counting every old line that carries the SAME decision together, so a
    merged re-pass line covering three consecutive 10-second turns the owner labelled alike keeps the label). Decisions: owner_label, tagged_person_id (+ source / time), not_person.
    The human decision is AUTHORITATIVE: a carried person / "me" decision clears the model's own not-person verdict on the new line, and a carried not-person mark replaces the person
    suggestion. Returns how many new lines got one."""
    n = 0
    for s in new:
        dur = max(1, s["end_ms"] - s["start_ms"])
        groups: dict = {}
        for o in old:
            if not (o.get("owner_label") or o.get("tagged_person_id") or o.get("not_person")):
                continue
            ov = max(0, min(s["end_ms"], o["end_ms"]) - max(s["start_ms"], o["start_ms"]))
            if ov <= 0:
                continue
            g = groups.setdefault((o.get("owner_label"), o.get("tagged_person_id"), o.get("not_person")), {"total": 0, "best": o, "best_ov": 0})
            g["total"] += ov
            if ov > g["best_ov"]:
                g["best"], g["best_ov"] = o, ov
        if not groups:
            continue
        g = max(groups.values(), key=lambda x: x["total"])
        if g["total"] / dur >= min_share:
            best = g["best"]
            s["carry"] = {k: best.get(k) for k in ("owner_label", "owner_labelled_at", "tagged_person_id", "tag_source", "tagged_at", "not_person")}
            if best.get("not_person"):
                s["not_person"] = best["not_person"]
                s.pop("person", None)
            else:
                s["not_person"] = None                    # the owner named / labelled this voice: the model's "Zoe / TV" guess does not hide it
            n += 1
    return n


def _greedy_voices(embs: list, order: Iterable[int], link: float = WEARER_LINK) -> list[dict]:
    """Greedy cosine clustering in ``order``: each vector joins the nearest running centre at ``link`` or above, else starts a voice. -> [{"c": unit centre, "m": [indices]}]. Pure."""
    clusters: list[dict] = []
    for i in order:
        e = _unit(embs[i])
        best = max(clusters, key=lambda c: float(c["c"] @ e), default=None)
        if best is not None and float(best["c"] @ e) >= link:
            best["m"].append(i)
            best["c"] = _unit(best["c"] * (len(best["m"]) - 1) + e)
        else:
            clusters.append({"c": e, "m": [i]})
    return clusters


def _nearest(cl: dict, embs: list, max_n: int, *, group: Optional[list] = None, per_group: int = 0) -> list[int]:
    """A voice's members closest to its centre, best first, at most ``max_n`` - and, with ``group``/``per_group``, at most ``per_group`` from any one group (recording). Pure."""
    out: list[int] = []
    used: dict = {}
    for i in sorted(cl["m"], key=lambda i: -float(cl["c"] @ _unit(embs[i]))):
        if per_group and group is not None:
            if used.get(group[i], 0) >= per_group:
                continue
            used[group[i]] = used.get(group[i], 0) + 1
        out.append(i)
        if len(out) >= max_n:
            break
    return out


def propose_wearer(windows: list, *, loud_pct: float = 70.0, link: float = WEARER_LINK, min_cluster: int = 6, max_n: int = PROPOSAL_MAX) -> list[int]:
    """The loudest-nearest-voice heuristic: among the speech windows at or above the ``loud_pct`` RMS percentile (the wearer's mouth is the nearest
    thing to the pendant), cluster greedily by cosine and return the indices of the largest cluster's windows closest to its centre. A PROPOSAL only:
    nothing here makes anyone "me" - the owner confirms (``confirm_wearer``). [] when no cluster is big enough."""
    if len(windows) < min_cluster:
        return []
    rms = np.array([float(w[3]) if len(w) > 3 else 0.0 for w in windows])
    cut = float(np.percentile(rms, loud_pct))
    embs = [w[2] for w in windows]
    clusters = _greedy_voices(embs, [i for i in range(len(windows)) if rms[i] >= cut and rms[i] > 0], link)
    if not clusters:
        return []
    top = max(clusters, key=lambda c: len(c["m"]))
    if len(top["m"]) < min_cluster:
        return []
    return sorted(_nearest(top, embs, max_n))


# ── the database ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _blob(v: Any) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _vec(b: Any) -> np.ndarray:
    return np.frombuffer(bytes(b), dtype=np.float32).astype(np.float64)


async def _all(db, sql: str, params: Iterable = ()) -> list:
    cur = await db.execute(sql, tuple(params))
    return list(await cur.fetchall())


# ── what is worth the extra voters first (owner policy 2026-11-09: Qwen3 on ALL audio, the other voters on the IMPORTANT recordings first) ──

_NUMBERY = re.compile(r"\b(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
                      r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|dollars?|bucks|percent)\b", re.I)
_TIMEY = re.compile(r"\b(\d{1,2}[:.]\d{2}|o'?clock|a\.?m\.?|p\.?m\.?|noon|midnight|today|tonight|tomorrow|yesterday|morning|afternoon|evening|weekend|next week|this week|"
                    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)
_COMMITY = re.compile(r"\b(remind(er)?|remember|don'?t forget|need to|have to|has to|gotta|going to|promise[sd]?|appointment|booked|booking|book|deadline|due|schedule[sd]?|meeting|"
                      r"pick up|pay|owe[sd]?|cancel|order|call|bring|buy)\b", re.I)


def importance(text: str, names: Iterable[Any] = (), *, candidate: bool = False) -> int:
    """How much a recording is worth a second and third opinion, from its FIRST-PASS text: names (household members, named guests) +3, numbers +2, times / dates +2, commitment words +2,
    feeding a pending day-review candidate +4. Pure; the sum of the categories present."""
    t = text or ""
    score = 4 if candidate else 0
    if any(re.search(rf"\b{re.escape(n)}\b", t, re.I) for n in clean_names(names)):
        score += 3
    for rx in (_NUMBERY, _TIMEY, _COMMITY):
        if rx.search(t):
            score += 2
    return score


async def candidate_recording_ids(db, user_id: str) -> set:
    """Recording ids that a PENDING day-review candidate rests on (they feed the owner's next review: transcribe them best first). Fail-soft: pre-0054 / no table -> empty."""
    out: set = set()
    try:
        for (raw,) in await _all(db, "SELECT recording_ids FROM omi_day_candidates WHERE user_id = ? AND status = 'pending'", (user_id,)):
            try:
                out.update(str(x) for x in json.loads(raw or "[]"))
            except (ValueError, TypeError):
                pass
    except Exception:  # noqa: BLE001
        pass
    return out


def rank_important(rows: list, names_by_user: dict, cand_by_user: dict) -> list:
    """The rows most important first, then the one whose audio expires FIRST (so nothing ages out while a newer one is read), then the ones the first pass found
    words in before the ones it found none in, then oldest first. Sets ``row['importance']``."""
    for r in rows:
        r["importance"] = importance(r.get("transcript") or "", names_by_user.get(r["user_id"], []), candidate=r["id"] in cand_by_user.get(r["user_id"], set()))
    return sorted(rows, key=lambda r: (-r["importance"], str(r.get("audio_expires_at") or "~"), 1 if r.get("status") == "empty_transcript" else 0, r.get("started_at") or ""))


def plan_window(costs: list, usable_s: float, *, fixed_s: float = 0.0) -> tuple:
    """THE WINDOW (owner, 2026-11-12: "give it a window and it can only use that amount of time and the left overs go to the next night"). ``costs`` = [(id, seconds the
    WHOLE pass costs that recording: the pivot and every expected voter)] in processing order. -> (ids to process, ids that could never fit even an empty window).
    Takes recordings in order while the next one's whole cost still fits; the first that does not stops the plan (it and everything after carry, in order). A recording
    that alone would overrun the empty window is passed over (counted ``oversize``) so it cannot block every night behind it; a longer window takes it. Pure."""
    room = max(0.0, usable_s) - fixed_s
    take: list = []
    oversize: list = []
    stopped = False
    for rid, c in costs:
        if c > max(0.0, usable_s) - fixed_s:
            oversize.append(rid)                # counted wherever it sits, so the report is whole
            continue
        if stopped or c > room:
            stopped = True                      # the order holds: nothing after the first that does not fit is taken
            continue
        room -= c
        take.append(rid)
    return take, oversize


async def pending_recordings(db, *, now: Optional[datetime] = None, limit: Optional[int] = None) -> list[dict]:
    """EVERY recording the night pass owes a look (the window ranks the whole held backlog itself; the hold cap bounds it to 30 days of uploads; ``limit`` is for
    callers that want a sample): first-pass done (or empty) rows whose WAV is still on disk, inside the hold, not yet passed. A recording whose file vanished is not listed (nothing to process; ``audio_path`` is cleared by the retention loop)."""
    rows = await _all(db, "SELECT id, user_id, audio_path, duration_s, started_at, status, transcript, repass_attempts, device_id, audio_expires_at FROM omi_recordings "
                          f"WHERE {OWED_SQL} "
                          "AND created_at >= ? ORDER BY CASE WHEN status = 'empty_transcript' THEN 1 ELSE 0 END, started_at, created_at" + (" LIMIT ?" if limit else ""),
                      (_iso((now or _now()) - timedelta(days=HOLD_CAP_DAYS)),) + ((limit,) if limit else ()))
    out = []
    for r in rows:
        d = {k: r[k] for k in ("id", "user_id", "audio_path", "duration_s", "started_at", "status", "transcript", "repass_attempts", "device_id", "audio_expires_at")}
        if d["audio_path"] and os.path.exists(d["audio_path"]):
            out.append(d)
    return out


async def household_names(db, user_id: str) -> list[str]:
    """Every member's first name plus this owner's named guests - minus anything on the forget ledger (a forgotten name is never offered to the ASR)."""
    names: list[str] = []
    try:
        names += [r[0] for r in await _all(db, "SELECT name FROM users WHERE COALESCE(role, 'member') != 'guest'")]
    except Exception:  # noqa: BLE001
        pass
    try:
        names += [r[0] for r in await _all(
            db, "SELECT DISTINCT p.name FROM people p JOIN learned_voiceprints v ON v.people_id = p.id WHERE v.owner_user_id = ? AND p.user_id = ? "
                "AND (p.deleted = 0 OR p.deleted IS NULL)", (user_id, user_id))]
    except Exception:  # noqa: BLE001
        pass
    out = []
    for n in clean_names(names):
        try:
            import named_guests
            if await named_guests._forgotten(user_id, n):
                continue
        except Exception as exc:  # noqa: BLE001 - FAIL CLOSED: a name that cannot be proven un-forgotten is not offered to the ASR (or written to the manifest)
            logger.warning("OMI_REPASS household name withheld: forget ledger unreadable (%s)", type(exc).__name__)
            continue
        out.append(n)
    return out


async def wearer_print(db, user_id: str) -> Optional[np.ndarray]:
    """The wearer's pendant-channel print: the leave-one-out mean of the CONFIRMED windows, or None below ``MIN_WEARER_WINDOWS``."""
    import omi_voices
    try:
        rows = await _all(db, "SELECT embedding FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'confirmed'", (user_id, MODEL_ID))
    except Exception as exc:  # noqa: BLE001
        if omi_voices._missing(exc):
            return None
        raise
    if len(rows) < MIN_WEARER_WINDOWS:
        return None
    vec, _ = omi_voices.loo_mean(np.stack([_unit(_vec(r[0])) for r in rows]))
    return vec


async def load_refs(db, user_id: str, zoe: Optional[Any], mu: Optional[Any] = None, loud_cut: Optional[float] = None, persist: bool = False) -> Refs:
    """Everything a turn is matched against, in the right channel. ``mu`` = the pendant session (or stored per-device) mean. The wearer prior is the device owner's print, mean-removed: the
    CONFIRMED pendant print if there is one, else the owner's consented PANEL profile (mean-removed with the registry's panel mean), which can only make a turn "you?" (leaning)."""
    import omi_voices
    people, panel_mu = [], None
    try:
        people, panel_mu = await who.load_people(db, user_id, MODEL_ID, mu, persist=persist)
    except Exception as exc:  # noqa: BLE001
        if not omi_voices._missing(exc):
            raise
    you, panel_only = await wearer_print(db, user_id), False
    if you is not None:
        you = who.remove_mean(you, mu)
    else:
        try:
            prof = await omi_voices.wearer_profile(db, user_id, MODEL_ID)
        except Exception as exc:  # noqa: BLE001
            if not omi_voices._missing(exc):
                raise
            prof = None
        if prof is not None:
            you, panel_only = who.remove_mean(prof, panel_mu), True
    return Refs(you=you, zoe=zoe, people=people, mu=mu, you_panel_only=panel_only, loud_cut=loud_cut)


async def panel_evidence_for(uid: str, rid: str) -> tuple:
    """(Zoe spans, person anchors, covered) from what the panel recorded for this recording - ``omi_voices.panel_spans``. ``covered`` True means the panel log was
    aligned to the pendant's clock this night, so Zoe is decided by what she SAID and her voice print no longer can. ([], [], False) on any failure."""
    import omi_voices
    from db_compat import get_compat_db
    try:
        async with get_compat_db() as db:
            return await omi_voices.panel_spans(db, uid, rid)
    except Exception:  # noqa: BLE001
        return [], [], False


async def _first_pass_segments(db, rid: str) -> list[dict]:
    cols = ("id", "start_ms", "end_ms", "owner_label", "owner_labelled_at", "tagged_person_id", "tag_source", "tagged_at", "not_person", "voice_cluster_id")
    rows = await _all(db, f"SELECT {', '.join(cols)} FROM omi_segments WHERE recording_id = ? ORDER BY seq", (rid,))
    return [{c: r[c] for c in cols} for r in rows]


async def _add_wearer_samples(db, user_id: str, rid: str, windows: list, spans: list, *, source: str, status: str, proposal: Optional[str] = None) -> int:
    n = 0
    for w in windows:
        a, b = int(float(w[0]) * 1000), int(float(w[1]) * 1000)
        if spans and not any(min(b, e) - max(a, s) >= 0.5 * (b - a) for s, e in spans):
            continue
        cur = await db.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, proposal_id, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (recording_id, start_ms, end_ms, model_id) DO NOTHING",
                               (str(uuid.uuid4()), user_id, rid, MODEL_ID, a, b, _blob(_unit(w[2])), source, status, proposal, _iso(_now())))
        n += int(getattr(cur, "rowcount", 1) or 0)
    return n


async def _release_or_freeze(db, uid: str, tagged_old: list[str]) -> list[tuple]:
    """The first-pass lines are about to be replaced. Where the re-pass WILL re-teach (``omi_voices.may_learn``: flag on, the member's switch on, an adult) the old contributions are
    released (a print's own stopped switch still protects it); where learning is stopped (the print's own switch, the member's, or the global flag) NOTHING re-teaches, so the
    contributions are kept and returned [(voiceprint_id, old_segment_id)] to be relinked to the new line ids."""
    import omi_voices
    no_learning = not await omi_voices.may_learn(db, uid)
    frozen = await _frozen_contributions(db, uid, tagged_old, all_prints=no_learning)
    if not no_learning:
        await omi_voices._release_contributions(db, uid, tagged_old, keep_empty=True, skip_frozen=True)
    return frozen


async def _frozen_contributions(db, user_id: str, seg_ids: list[str], *, all_prints: bool = False) -> list[tuple]:
    """[(voiceprint_id, old_segment_id)] of the contributions a STOPPED (learning_enabled = 0) print holds for these first-pass segments; ``all_prints`` (the member or the global
    learning switch is off) counts every print as stopped."""
    if not seg_ids:
        return []
    import omi_voices
    try:
        rows = await _all(db, "SELECT c.voiceprint_id, c.segment_id FROM learned_voiceprint_segments c JOIN learned_voiceprints v ON v.id = c.voiceprint_id "
                              f"WHERE v.owner_user_id = ?{'' if all_prints else ' AND v.learning_enabled = 0'} AND c.segment_id IN ({','.join('?' * len(seg_ids))})", (user_id, *seg_ids))
    except Exception as exc:  # noqa: BLE001 - a box without the voiceprint tables has none
        if not omi_voices._missing(exc):
            raise
        return []
    return [(r[0], r[1]) for r in rows]


async def _relink_frozen(db, frozen: list[tuple], old: list[dict], new_ids: list[str], segs: list[dict]) -> None:
    """The first-pass segments are gone, so a stopped print's preserved contributions are RE-KEYED to the replacement line that carries the same person tag over the same span
    (a later "that is me" / "not a person" correction of that line can then release them). No matching line: the contribution is dropped and the print rebuilt (nothing
    could ever release it). Never creates a contribution."""
    if not frozen:
        return
    import omi_voices
    by_old = {o["id"]: o for o in old}
    touched: set = set()
    fused_sum: dict = {}
    for vp, osid in frozen:
        o = by_old.get(osid)
        best, best_ov = None, 0
        for nid, sg in zip(new_ids, segs):
            if o is None or (sg.get("carry") or {}).get("tagged_person_id") != o.get("tagged_person_id"):
                continue
            ov = min(sg["end_ms"], o["end_ms"]) - max(sg["start_ms"], o["start_ms"])
            if ov > best_ov:
                best, best_ov = nid, ov
        held = await _all(db, "SELECT embedding FROM learned_voiceprint_segments WHERE voiceprint_id = ? AND segment_id = ?", (vp, best)) if best is not None else []
        if best is not None and not held:
            await db.execute("UPDATE learned_voiceprint_segments SET segment_id = ? WHERE voiceprint_id = ? AND segment_id = ?", (best, vp, osid))
        elif best is not None:
            # two first-pass lines of the same person merged into ONE replacement line: a contribution is keyed (print, segment), so the two embeddings are FUSED (unit mean) into the
            # one row instead of the later being discarded - a stopped print cannot be re-taught, so dropping a sample would lose it for good
            mine = await _all(db, "SELECT embedding FROM learned_voiceprint_segments WHERE voiceprint_id = ? AND segment_id = ?", (vp, osid))
            if mine:
                total = fused_sum.get((vp, best))
                if total is None:
                    total = _unit(_vec(held[0][0]))
                total = total + _unit(_vec(mine[0][0]))      # the running UNNORMALISED sum: every merged sample weighs the same however many collide
                fused_sum[(vp, best)] = total
                fused = _unit(total)
                await db.execute("UPDATE learned_voiceprint_segments SET embedding = ? WHERE voiceprint_id = ? AND segment_id = ?", (_blob(fused), vp, best))
            await db.execute("DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ? AND segment_id = ?", (vp, osid))
            touched.add(vp)
        else:
            await db.execute("DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ? AND segment_id = ?", (vp, osid))
            touched.add(vp)
    for vp in sorted(touched):
        await omi_voices.rebuild(db, vp, keep_empty=True)


async def _learn_carried_tags(db, user_id: str, seg_ids: list[str], segs: list[dict], rid: str) -> int:
    """The owner's earlier tags (first-pass segments) teach the R34 person prints: each carried ``tagged_person_id`` line's embedding joins that
    person's pendant print in THIS model (the same rules as ``omi_voices.tag``: learning allowed, never a child)."""
    import omi_voices
    from person_age import person_may_be_child
    n = 0
    if not await omi_voices.may_learn(db, user_id):
        return 0
    for sid, s in zip(seg_ids, segs):
        pid = (s.get("carry") or {}).get("tagged_person_id")
        if not pid or s.get("embedding") is None:
            continue
        rows = await _all(db, "SELECT relationship, birthday FROM people WHERE id = ? AND user_id = ?", (pid, user_id))
        if not rows or person_may_be_child({"relationship": rows[0][0], "birthday": rows[0][1]}):
            continue
        vp = await omi_voices._voiceprint(db, user_id, pid, MODEL_ID, create=True)
        if not vp or not vp["learning_enabled"]:
            continue
        await db.execute("INSERT INTO learned_voiceprint_segments (voiceprint_id, segment_id, recording_id, embedding, added_at) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT (voiceprint_id, segment_id) DO NOTHING", (vp["id"], sid, rid, _blob(s["embedding"]), _iso(_now())))
        await omi_voices.rebuild(db, vp["id"])
        n += 1
    return n


def _shadow_log(rec: dict) -> None:
    """One counts-only line per recording: no word, no name, no label text."""
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "shadow.jsonl"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    os.chmod(p, 0o600)


async def _redact_row(uid: str, row: dict) -> dict:
    """A hypothesis row about to be stored: every word-bearing field (each engine's text, the fused text, the contested slots' picks / vote keys / who keys) goes through the forget ledger first.
    FAIL CLOSED: a ledger that cannot be read raises (the caller stores nothing for that member this night) - unlike the transcript path, original words must never be written unredacted."""
    import forget_match as fm
    import memory_forgotten as mf
    if row.get("kind") == "who_said":
        return row

    async def red(t: str) -> str:
        if not t:
            return t
        spans = await mf.spans(uid, t, strict=True)
        return fm.redact(t, spans)[0] if spans else t

    async def go(o: Any) -> Any:
        if isinstance(o, str):
            return await red(o)
        if isinstance(o, dict):
            out: dict = {}
            for k, v in o.items():
                nk = await red(k) if isinstance(k, str) else k
                out[nk] = (out[nk] + v) if (nk in out and isinstance(v, (int, float)) and isinstance(out[nk], (int, float))) else await go(v)
            return out
        if isinstance(o, list):
            return [await go(v) for v in o]
        return o
    return {**row, **{k: await go(row[k]) for k in ("hyps", "rover", "slots") if k in row}}


async def member_tags(user_id: str) -> bool:
    """The member's own "tag voices on my recordings" switch (``omi_member_settings.speaker_tag_on``). FAILS CLOSED: unreadable = do not tag."""
    try:
        import omi_member_settings
        return bool(await omi_member_settings.speaker_tag_on(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("OMI_REPASS_TAG_PREF_UNREADABLE reason=%s", type(exc).__name__)
        return False


async def finish_one(r: dict, res: dict, *, refs: Refs, names: list, model_label: str, seen: dict, m: str, now: Optional[datetime] = None) -> dict:
    """Apply one worker result to one recording. ``m`` is the mode the job started in. Returns a counts-only summary dict (``status``: replaced |
    shadow | erased | gone | failed). Never raises for a data problem."""
    import omi_recordings as omi
    import omi_voices
    from db_compat import get_compat_db
    rid, uid = r["id"], r["user_id"]
    who_text: dict = {}            # seg_id -> the turn text (kept out of ``who_items``: that row is stored without words)
    tag = await member_tags(uid)   # "tag voices on my recordings" off (or unreadable): text only - no speaker label, no embedding, no voiceprint, no wearer sample
    if tag:
        spans, anchors, by_log = await panel_evidence_for(uid, rid)
    else:
        refs, spans, anchors, by_log = Refs(), [], [], False
        res = {**res, "windows": []}
    lines = build_lines(res["words"], res["windows"], refs, zoe_spans=spans, anchors=anchors, zoe_by_log=by_log)   # the panel's record first (Zoe's sayings, known speakers), then the voices
    segs = []
    for ln in lines:
        f = segment_fields(ln, refs)
        f["text"] = normalise_names(f["text"], names)
        segs.append(f)
    old_text = r.get("transcript") or ""
    counts = {"words": len(res["words"]), "lines": len(segs), "speakers": len({ln["key"] for ln in lines}), "dur_s": res.get("dur_s"),
              "speech_s": res.get("speech_s"), "model": model_label, "me": sum(ln["key"] == "you" for ln in lines),
              "zoe": sum(ln["key"] == "zoe" for ln in lines), "named": sum(ln["key"].startswith("p:") for ln in lines)}
    stamp = _iso(now or _now())
    text = ""
    import manner_block
    adult = tag and await manner_block.eligible(uid)       # D14, fail closed: a child's (or an unverifiable, or an opted-out member's) pendant gets TEXT only - no voiceprint, wearer sample or held embedding is retained
    async with erase_gate.writing(uid, seen.get(uid, 0)) as live:
        async with get_compat_db() as db:
            if live:
                # REDACTION HAPPENS HERE, INSIDE THE GATE: an entity forget (``erasing(bump=False)``) does not move the generation, so it is only
                # ordered against this write by the lock itself - redacting before we held it could store text the ledger forgot in between.
                try:
                    for sgm in segs:
                        sgm["text"] = await omi._redact_forgotten(uid, sgm["text"], strict=True)
                except Exception as exc:  # noqa: BLE001 - forget ledger unreadable: nothing is stored; the recording stays pending for the next night
                    return {"status": "failed", "why": "forget_ledger:" + type(exc).__name__, **counts}
                text = " ".join(sgm["text"] for sgm in segs if sgm["text"].strip()).strip()
                counts["agree"] = word_agreement(old_text, text)
            if not live:                              # the member was erased since this was read: nothing of it survives (the first pass's rule)
                rows = await omi._fetch(db, "SELECT id, audio_path, status FROM omi_recordings WHERE id = ?", (rid,))
                if rows and rows[0]["status"] != "deleted":
                    await omi._tombstone(db, rows[0], omi._now())
                    await db.commit()
                return {"status": "erased", **counts}
            cur = await omi._fetch(db, "SELECT status, repass_state, audio_path FROM omi_recordings WHERE id = ?", (rid,))
            if not cur or cur[0]["status"] not in ("done", "empty_transcript") or cur[0]["repass_state"] not in ("pending", "") or not cur[0]["audio_path"]:
                return {"status": "gone", **counts}      # deleted / already passed / audio released while we worked: write nothing
            if m == "shadow" or not text:
                # a pass that heard nothing leaves the first-pass text alone (an empty answer must never erase a Moonshine transcript)
                await db.execute("UPDATE omi_recordings SET repass_state = 'shadow', repass_model = ?, repass_at = ?, repass_attempts = repass_attempts + 1 "
                                 "WHERE id = ? AND repass_state IN ('pending', '')", (model_label, stamp, rid))
                await db.commit()
                _shadow_log({"at": stamp, "rid_sha": hashlib.sha256(rid.encode()).hexdigest()[:12], **{k: v for k, v in counts.items() if k != "model"},
                             "model": model_label, "old_words": len(re.findall(r"\S+", old_text)), "mode": m, "empty": not text})
                counts["status"] = "shadow"
            else:
                old = await _first_pass_segments(db, rid)
                carried = carry_labels(segs, old)
                former = sorted({o["voice_cluster_id"] for o in old if o.get("voice_cluster_id")})
                pending_wearer_labels = [o for o in old if o.get("owner_label") == "you"]
                frozen: list = []
                try:
                    # ONE explicit transaction: asyncpg auto-commits each statement outside one (AsyncpgCompat.commit() is a no-op), so without it a failure
                    # between the segment delete and the recording update would leave the owner's original lines gone and nothing in their place
                    async with db.transaction():
                        omi_voices.mark_overlaps(segs, await omi_voices.zoe_spoken_spans(db, uid, rid, segs=segs, night=True))
                        # the first-pass lines are about to be replaced: what their tags taught the people's voiceprints is released first (the segment ids die with them, and a later
                        # correction of the NEW line only knows the new id); the carried tags re-teach below under the new ids
                        try:
                            tagged_old = [o["id"] for o in old if o.get("tagged_person_id")]
                            if tagged_old:
                                frozen = await _release_or_freeze(db, uid, tagged_old)
                        except Exception as exc:  # noqa: BLE001 - a box without the voiceprint tables has nothing to release
                            if not omi_voices._missing(exc):
                                raise
                        ids = await omi_voices.store_segments(db, uid, rid, segs, model_id=MODEL_ID, channel="pendant" if refs.you is not None else None)
                        for sid, s in zip(ids, segs):
                            sets, vals = [], []
                            c = s.get("carry")
                            if c or s.get("not_person"):
                                sets.append("not_person = ?")
                                vals.append(s.get("not_person"))
                            if c:
                                for k in ("owner_label", "owner_labelled_at", "tagged_person_id", "tag_source", "tagged_at"):
                                    sets.append(f"{k} = ?")
                                    vals.append(c.get(k))
                            elif s.get("person") and not s.get("not_person"):
                                sets += ["suggested_person_id = ?", "suggested_score = ?"]
                                vals += [s["person"], s.get("person_score")]
                            if sets:
                                await db.execute(f"UPDATE omi_segments SET {', '.join(sets)} WHERE id = ? AND user_id = ?", (*vals, sid, uid))
                        await _relink_frozen(db, frozen, old, ids, segs)
                        untagged = [(sid, s) for sid, s in zip(ids, segs) if not s.get("carry") and not s.get("not_person")]
                        if untagged and adult:      # bystander voices: clusters + suggestions in the R34 space, exactly the first pass's machinery
                            await omi_voices.learn(db, uid, [i for i, _ in untagged], [s for _, s in untagged], model_id=MODEL_ID, you_min=LEARN_YOU_MIN)
                        if adult:
                            await _learn_carried_tags(db, uid, ids, segs, rid)
                        ttl = _iso((now or _now()) + timedelta(days=omi_voices.EMBED_TTL_DAYS))
                        who_items = []
                        for sid, sg in zip(ids, segs):             # the "who said this?" turns: the owner's tap adds THIS embedding to the person's pendant print (omi_voices.tag)
                            if adult and sg.get("mark") and not sg.get("carry") and not sg.get("not_person") and sg.get("embedding") is not None:
                                await db.execute("INSERT INTO omi_segment_embeddings (segment_id, user_id, model_id, embedding, expires_at) VALUES (?, ?, ?, ?, ?) "
                                                 "ON CONFLICT (segment_id) DO NOTHING", (sid, uid, MODEL_ID, _blob(sg["embedding"]), ttl))
                                who_text[sid] = sg["text"]
                                who_items.append({"seg_id": sid, "t0": round(sg["start_ms"] / 1000.0, 3), "t1": round(sg["end_ms"] / 1000.0, 3),
                                                  "mark": "lean" if sg.get("lean") else "unknown",
                                                  "cands": sg["cands"]})
                        counts["who_items"] = who_items
                        if pending_wearer_labels and adult:
                            spans = [(o["start_ms"], o["end_ms"]) for o in pending_wearer_labels]
                            counts["wearer_windows"] = await _add_wearer_samples(db, uid, rid, res["windows"], spans, source="owner_label", status="confirmed")
                        for cid in former:
                            await omi_voices.rebuild_cluster(db, cid)
                        state = "tagged" if (refs.you is not None and not refs.you_panel_only) else "unverified"
                        await db.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, speaker_state = ?, repass_state = 'done', repass_model = ?, repass_at = ?, "
                                         "repass_attempts = repass_attempts + 1 WHERE id = ? AND repass_state IN ('pending', '')", (text, state, model_label, stamp, rid))
                except Exception as exc:  # noqa: BLE001 - the transcript is still better than none; but never a half-written segment set
                    logger.warning("OMI_REPASS_SEGMENTS_NOT_STORED id=%s reason=%s", rid, type(exc).__name__)
                    return {"status": "failed", "why": "segments:" + type(exc).__name__, **counts}
                await db.commit()
                counts.update(status="replaced", carried=carried)
            if m == "on" and adult and (refs.you is None or refs.you_panel_only) and res["windows"]:           # a proposal for the owner to confirm; never used until then. Shadow stores nothing
                try:
                    pick = propose_wearer(res["windows"])
                    if pick:
                        await _add_wearer_samples(db, uid, rid, [res["windows"][i] for i in pick], [], source="heuristic", status="proposed", proposal=stamp[:10])
                        await db.commit()
                        counts["proposed"] = len(pick)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OMI_REPASS_PROPOSAL_FAILED reason=%s", type(exc).__name__)
    if counts.get("status") in ("replaced", "shadow"):
        # the morning transcript check (ZOE_TRANSCRIPT_CHECK, default off = nothing): the ROVER fusion's contested chunks (``eu.is_contested``: a slot with no
        # strict majority, or low_conf - the SAME rows ``eu.check_queue`` reads back from the hyps store) and the "who said this?" turns become ranked check items BEFORE
        # the release below, so an open item keeps its snippet's WAV (the hyps file is written only after every recording is finished, i.e. after that release).
        # Inside the ORIGINAL generation's erase gate (a member erased since the job started stores nothing); ``enqueue`` filters the forget ledger fail-closed. Never fails the pass.
        import transcript_check
        if transcript_check.enabled():
            try:
                async with erase_gate.writing(uid, seen.get(uid, 0)) as live_q:
                    if live_q:
                        who_rows = []
                        if counts.get("who_items"):
                            async with get_compat_db() as db_q:
                                people = {p["id"]: p["name"] for p in await omi_voices.own_people(db_q, uid)}
                            who_rows = [transcript_check.who_row_from_item(wi, who_text.get(wi.get("seg_id"), ""), people) for wi in counts["who_items"]]
                        cands = [c for row in res.get("check_rows") or [] for c in transcript_check.candidates_from_row(row)]
                        cands += [c for row in who_rows for c in transcript_check.candidates_from_who(row)]
                        if cands:
                            counts["check"] = (await transcript_check.enqueue(uid, r, cands, names=names)).get("stored", 0)
            except Exception as exc:  # noqa: BLE001 - the check queue never fails the night pass
                logger.warning("OMI_TRANSCRIPT_CHECK_NOT_QUEUED reason=%s", type(exc).__name__)
    await omi.release_tagged_audio(uid)            # the pass has seen it: the WAV goes unless the owner keeps it or a line still needs a name
    return counts


async def confirm_who(db, user_id: str, segment_id: str, person_id: str) -> dict:
    """The owner's tap on a ``who_said`` item ("Who said this?"): ``person_id`` is a person id or ``"you"``. The turn's stored embedding joins that person's PENDANT print through the existing
    machinery (``omi_voices.tag`` - the member's own person, learning rules, never a child), or - for "you" - becomes a CONFIRMED wearer sample and the line is labelled "me". Idempotent."""
    import omi_voices
    if person_id == "you":
        rows = await _all(db, "SELECT s.recording_id, s.start_ms, s.end_ms, e.embedding FROM omi_segments s JOIN omi_segment_embeddings e ON e.segment_id = s.id "
                              "WHERE s.id = ? AND s.user_id = ?", (segment_id, user_id))
        ok = await omi_voices.set_owner_label(db, user_id, segment_id, "you")
        if rows:
            rid, a, b, emb = rows[0]
            await db.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, proposal_id, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, 'owner_label', 'confirmed', NULL, ?) ON CONFLICT (recording_id, start_ms, end_ms, model_id) DO NOTHING",
                             (str(uuid.uuid4()), user_id, rid, MODEL_ID, int(a), int(b), bytes(emb), _iso(_now())))
            await db.commit()
        return {"person": "you", "labelled": bool(ok)}
    return {"person": person_id, **await omi_voices.tag(db, user_id, person_id=person_id, segment_id=segment_id, source="owner")}


async def requeue_failed(user_id: Optional[str] = None) -> int:
    """After a fix: put recordings the pass gave up on (and whose WAV is still held) back in the queue. Operator/CLI use."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        sql = "UPDATE omi_recordings SET repass_state = 'pending', repass_attempts = 0 WHERE repass_state = 'failed' AND audio_path != ''"
        cur = await db.execute(sql + (" AND user_id = ?" if user_id else ""), (user_id,) if user_id else ())
        await db.commit()
        return int(getattr(cur, "rowcount", 0) or 0)


async def note_failure(rid: str, *, carry: bool) -> None:
    """A recording the worker could not process. ``carry`` (the deadline): untouched, it waits for tomorrow. Otherwise one more attempt is counted and,
    at ``MAX_PASS_ATTEMPTS``, the pass gives up (``failed``): the Moonshine text stands and the WAV is STILL HELD until its retention expiry (a failure may be a bug that a
    fix clears - ``requeue_failed`` puts them back - and the audio is the only copy of what was said), never released early."""
    if carry:
        return
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        await db.execute("UPDATE omi_recordings SET repass_attempts = repass_attempts + 1 WHERE id = ? AND repass_state IN ('pending', '')", (rid,))
        await db.execute("UPDATE omi_recordings SET repass_state = 'failed' WHERE id = ? AND repass_state IN ('pending', '') AND repass_attempts >= ?",
                         (rid, MAX_PASS_ATTEMPTS))
        await db.commit()


# ── the owner's side of the wearer print ────────────────────────────────────────────────────────────────────────────────

async def wearer_status(db, user_id: str) -> dict:
    rows = await _all(db, "SELECT status, COUNT(*) FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? GROUP BY status", (user_id, MODEL_ID))
    c = {r[0]: int(r[1]) for r in rows}
    props = await _all(db, "SELECT proposal_id, COUNT(*), COUNT(DISTINCT recording_id) FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' "
                           "GROUP BY proposal_id ORDER BY proposal_id", (user_id, MODEL_ID))
    return {"confirmed": c.get("confirmed", 0), "needed": MIN_WEARER_WINDOWS, "proposals": [{"id": p[0], "windows": int(p[1]), "recordings": int(p[2])} for p in props]}


async def proposal_lines(db, user_id: str, proposal_id: str, limit: int = 6) -> list[dict]:
    """What was SAID in a proposal's windows (the stored lines overlapping them), so the owner can judge "was that me?" without any audio being kept.
    Owner-only output (the CLI prints it to the owner's terminal; it is never logged or committed)."""
    rows = await _all(db, "SELECT recording_id, start_ms, end_ms FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' "
                          "AND proposal_id = ? ORDER BY recording_id, start_ms", (user_id, MODEL_ID, proposal_id))
    out = []
    for rid, a, b in rows:
        segs = await _all(db, "SELECT text FROM omi_segments WHERE recording_id = ? AND user_id = ? AND start_ms < ? AND end_ms > ? ORDER BY seq LIMIT 1", (rid, user_id, b, a))
        if segs and segs[0][0]:
            out.append({"recording_id": rid, "at_ms": a, "text": segs[0][0]})
        if len(out) >= limit:
            break
    return out


@contextlib.asynccontextmanager
async def _wearer_txn(db, user_id: str):
    """ONE transaction holding a per-member advisory lock (``list_service.list_write_txn``'s pattern), so two wearer-print writers for one member - in this process or another (the
    owner's CLI beside zoe-data) - serialise on their "is a batch already open / a print already confirmed?" checks and write all-or-nothing. A DB shim without ``transaction`` runs
    unwrapped."""
    txn = getattr(db, "transaction", None)
    if not callable(txn):
        yield
        return
    async with txn():
        await db.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"omi_wearer|{user_id}",))
        yield


async def confirm_wearer(db, user_id: str, proposal_id: str) -> dict:
    """THE owner's word that a proposal's windows are their own voice: they join the wearer print (and only then does a line read "me"). A ``stored-...`` candidate is one of up to three
    voices offered side by side (``propose_wearer_from_stored``): confirming one withdraws its siblings from the same batch - they are someone else (nothing is learned of them).
    Refused (``not_eligible``) for a member who is not positively an adult NOW (D14): a proposal made while they were may never become a print. -> {status, confirmed}."""
    import manner_block
    if not await manner_block.eligible(user_id):                 # the cheap exit; repeated under the lock right before the write
        return {"status": "not_eligible", "confirmed": 0}
    async with _wearer_txn(db, user_id):
        if not await manner_block.eligible(user_id):             # eligibility can change while this waited for the member's lock
            return {"status": "not_eligible", "confirmed": 0}
        cur = await db.execute("UPDATE omi_wearer_samples SET status = 'confirmed' WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id = ?",
                               (user_id, MODEL_ID, proposal_id))
        n = int(getattr(cur, "rowcount", 0) or 0)
        if n and proposal_id.startswith("stored-"):
            await db.execute("DELETE FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id LIKE ? AND proposal_id != ?",
                             (user_id, MODEL_ID, proposal_id.rsplit("-", 1)[0] + "-%", proposal_id))
    await db.commit()
    return {"status": "confirmed" if n else "not_found", "confirmed": n}


async def reject_wearer(db, user_id: str, proposal_id: str) -> int:
    cur = await db.execute("DELETE FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id = ?",
                           (user_id, MODEL_ID, proposal_id))
    await db.commit()
    return int(getattr(cur, "rowcount", 0) or 0)


async def drop_stale_proposals(db, *, now: Optional[datetime] = None) -> int:
    cut = _iso((now or _now()) - timedelta(days=PROPOSAL_KEEP_DAYS))
    cur = await db.execute("DELETE FROM omi_wearer_samples WHERE status = 'proposed' AND created_at < ?", (cut,))
    return int(getattr(cur, "rowcount", 0) or 0)


_STORED_SQL = ("SELECT s.id, s.recording_id, s.start_ms, s.end_ms, s.voice_cluster_id, e.embedding FROM omi_segment_embeddings e JOIN omi_segments s ON s.id = e.segment_id "
               "WHERE e.user_id = ? AND s.user_id = ? AND e.model_id = ? AND e.expires_at > ? AND s.owner_label IS NULL AND s.tagged_person_id IS NULL AND s.not_person IS NULL")


async def propose_wearer_from_stored(db, user_id: str, *, now: Optional[datetime] = None) -> dict:
    """The wearer print from what the pendant has ALREADY heard: the member's held segment embeddings (``omi_segment_embeddings``, the first pass's pendant-channel units) clustered
    with the night heuristic's own greedy linkage, voices ranked by the DISTINCT recordings they are heard in, and up to ``STORED_MAX_VOICES`` voices heard in at least
    ``STORED_MIN_RECORDINGS`` recordings written as side-by-side PROPOSALS (``stored-YYYY-MM-DD-a|b|c``; at most ``PROPOSAL_MAX`` windows nearest the voice's centre, at most
    ``STORED_PER_RECORDING`` from one recording). The top voice is probably the wearer but may be a partner, the TV or Zoe's own speaker, so nothing is "me" until the owner confirms ONE
    (``confirm_wearer``). Lines the owner already decided (a label, a tag, not-a-person) are not evidence. No-op (``status`` says why) with a confirmed print, open proposals, a member
    not positively an adult (D14, the first pass's own ``manner_block.eligible``), no voice big enough, or an erase since the read. Those checks are REPEATED inside the erase gate and the
    member's wearer transaction (``_wearer_txn``) together with the writes, and each selected line's decision is re-read there (a line decided since the read is dropped), so a second
    run that overlapped this one, or a decision taken meanwhile, cannot add a batch or a decided line. A candidate is kept only if what was actually WRITTEN (an existing sample can
    conflict) still spans ``STORED_MIN_RECORDINGS`` recordings and ``MIN_WEARER_WINDOWS`` windows; the response reports written counts only."""
    import manner_block
    g = erase_gate.generation(user_id)
    if not await manner_block.eligible(user_id):
        return {"status": "not_eligible"}
    await drop_stale_proposals(db, now=now)
    if await wearer_print(db, user_id) is not None:
        return {"status": "has_print"}
    if await _all(db, "SELECT 1 FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' LIMIT 1", (user_id, MODEL_ID)):
        return {"status": "open_proposals"}
    stamp = now or _now()
    rows = await _all(db, _STORED_SQL + " ORDER BY s.created_at, s.recording_id, s.seq", (user_id, user_id, MODEL_ID, _iso(stamp)))
    embs = [_vec(r[5]) for r in rows]
    recs = [r[1] for r in rows]
    voices = _greedy_voices(embs, range(len(rows)))
    for v in voices:
        v["recs"] = len({recs[i] for i in v["m"]})
    big = sorted((v for v in voices if v["recs"] >= STORED_MIN_RECORDINGS), key=lambda v: (-v["recs"], -len(v["m"])))[:STORED_MAX_VOICES]
    out = {"segments": len(rows), "voices": len(voices)}
    if not big:
        return {"status": "no_voice", **out}
    picks = [(f"stored-{stamp.strftime('%Y-%m-%d')}-{'abc'[k]}", _nearest(v, embs, PROPOSAL_MAX, group=recs, per_group=STORED_PER_RECORDING)) for k, v in enumerate(big)]
    props, dropped = [], 0
    async with erase_gate.writing(user_id, g) as live:
        if not live:                               # the member was erased while this read: nothing of their voice is written
            return {"status": "erased", **out}
        async with _wearer_txn(db, user_id):
            # the invariants again, UNDER the member's lock and in the same transaction as the writes: another run (or a confirmation) may have landed since the read
            if not await manner_block.eligible(user_id):
                return {"status": "not_eligible", **out}
            await drop_stale_proposals(db, now=now)
            if await wearer_print(db, user_id) is not None:
                return {"status": "has_print", **out}
            if await _all(db, "SELECT 1 FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' LIMIT 1", (user_id, MODEL_ID)):
                return {"status": "open_proposals", **out}
            sel = [rows[i][0] for _, idx in picks for i in idx]
            undecided = {r[0] for r in await _all(db, "SELECT s.id FROM omi_segments s JOIN omi_segment_embeddings e ON e.segment_id = s.id WHERE s.user_id = ? "
                                                      f"AND s.id IN ({','.join('?' * len(sel))}) AND s.owner_label IS NULL AND s.tagged_person_id IS NULL "
                                                      "AND s.not_person IS NULL", (user_id, *sel))} if sel else set()
            for pid, idx in picks:
                for i in idx:
                    if rows[i][0] not in undecided:    # the owner decided this line (or it went) since the read: it is not evidence
                        dropped += 1
                        continue
                    await db.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, proposal_id, created_at) "
                                     "VALUES (?, ?, ?, ?, ?, ?, ?, 'heuristic', 'proposed', ?, ?) ON CONFLICT (recording_id, start_ms, end_ms, model_id) DO NOTHING",
                                     (str(uuid.uuid4()), user_id, rows[i][1], MODEL_ID, int(rows[i][2]), int(rows[i][3]), _blob(_unit(embs[i])), pid, _iso(stamp)))
                got = await _all(db, "SELECT COUNT(*), COUNT(DISTINCT recording_id) FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' "
                                     "AND proposal_id = ?", (user_id, MODEL_ID, pid))
                n, nrec = (int(got[0][0]), int(got[0][1])) if got else (0, 0)
                if nrec < STORED_MIN_RECORDINGS or n < MIN_WEARER_WINDOWS:     # conflicts / decisions left too little: not a candidate the owner could confirm
                    await db.execute("DELETE FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id = ?", (user_id, MODEL_ID, pid))
                    continue
                props.append({"id": pid, "windows": n, "recordings": nrec})
        await db.commit()
    out["decided_since_read"] = dropped
    logger.info("OMI_WEARER_PROPOSED_FROM_STORED voices=%d proposals=%d dropped=%d", len(voices), len(props), dropped)
    if not props:
        return {"status": "no_voice", **out}
    return {"status": "proposed", "proposals": props, **out}


async def relabel_stored_from_print(db, user_id: str, *, min_cos: Optional[float] = None) -> dict:
    """Once the pendant print is CONFIRMED, the member's EXISTING lines with a held embedding at ``min_cos`` or above read "me" (``speaker='you'``, ``profile_channel='pendant'``). A line
    the owner decided (a label, a tag, not-a-person) is never touched, nothing is ever labelled ``other`` from this, and below the bar a line stays as it is. As with "that was me", the
    wearer's own voice is not held as a bystander's: the relabelled line's embedding, unknown-voice link and person suggestion go (the voice is rebuilt). Idempotent. Counts only."""
    import manner_block
    import omi_voices
    if min_cos is None:                # the CONFIGURED pendant bar, read at call time: the same one both diarisation passes use (an override must not let the relabel say "me" below it)
        min_cos = omi_diarize.thresholds("pendant")[0]
    g = erase_gate.generation(user_id)
    if not await manner_block.eligible(user_id):
        return {"status": "not_eligible"}
    you = await wearer_print(db, user_id)
    if you is None:
        return {"status": "no_print"}
    you = _unit(you)
    rows = await _all(db, _STORED_SQL + " AND s.speaker IN ('other', 'unsure')", (user_id, user_id, MODEL_ID, _iso(_now())))
    hits = [(r[0], r[4], float(_unit(_vec(r[5])) @ you)) for r in rows]
    hits = [h for h in hits if h[2] >= min_cos]               # the UNROUNDED score against the bar (0.59996 is below 0.6); only the stored copy is rounded
    n, touched = 0, set()
    async with erase_gate.writing(user_id, g) as live:
        if not live:
            return {"status": "erased", "checked": len(rows)}
        async with _wearer_txn(db, user_id):
            # the early checks again, under the member's lock and right before the write: eligibility may have changed, or the print been withdrawn, while this scored
            if not await manner_block.eligible(user_id):
                return {"status": "not_eligible", "checked": len(rows)}
            if await wearer_print(db, user_id) is None:
                return {"status": "no_print", "checked": len(rows)}
            for sid, cid, score in hits:
                cur = await db.execute("UPDATE omi_segments SET speaker = 'you', score = ?, profile_channel = 'pendant', voice_cluster_id = NULL, suggested_person_id = NULL, "
                                       "suggested_score = NULL WHERE id = ? AND user_id = ? AND owner_label IS NULL AND tagged_person_id IS NULL AND not_person IS NULL",
                                       (round(score, 4), sid, user_id))
                if int(getattr(cur, "rowcount", 0) or 0):
                    await db.execute("DELETE FROM omi_segment_embeddings WHERE segment_id = ? AND user_id = ?", (sid, user_id))
                    n += 1
                    if cid:
                        touched.add(cid)
            for cid in sorted(touched):
                await omi_voices.rebuild_cluster(db, cid)
        await db.commit()
    logger.info("OMI_WEARER_RELABELLED checked=%d relabelled=%d", len(rows), n)
    return {"status": "ok", "checked": len(rows), "relabelled": n, "unchanged": len(rows) - n}


# ── the night job ───────────────────────────────────────────────────────────────────────────────────────────────────────

def state_path() -> Path:
    return home_dir() / "state.json"


def read_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def write_state(st: dict) -> None:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def spawn_worker(manifest: Path, out: Path, timeout_s: float, *, mem_max: str, threads: int) -> int:
    """The default runner: the worker in its OWN capped scope (``MemoryMax`` + no swap, nice 10), killed at ``timeout_s``. rc 3 = a model is missing."""
    cmd = [venv_python(), str(worker_path()), "--manifest", str(manifest), "--out", str(out)]
    gpu_prep()          # a no-op on a CPU backend is harmless (it only frees page cache)
    try:
        ct2 = json.loads(Path(manifest).read_text()).get("ct2_dir") or ""
    except (OSError, ValueError):
        ct2 = ""
    scope = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={mem_max}", "-p", "MemorySwapMax=0", "nice", "-n", "10"]
    env = {**os.environ, "OMP_NUM_THREADS": str(threads), "MALLOC_ARENA_MAX": "2", "HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTORCH_CUDA_ALLOC_CONF", None)         # the default torch allocator: it is what loaded the bake-off winners
    if ct2:                                          # the hand-built CUDA CTranslate2 (whisper-gpu:*): its python package and libs must be on the path BEFORE the process starts
        env["PYTHONPATH"] = f"{ct2}/pylib" + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["LD_LIBRARY_PATH"] = f"{ct2}/install/lib" + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    try:
        return subprocess.run(scope + cmd, env=env, timeout=timeout_s, check=False, stdout=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 124
    except FileNotFoundError:
        return 3


def read_results(path: Path) -> tuple[dict, dict, Optional[list]]:
    """(items by id, the final ``done`` record, the Zoe reference if the worker made one)."""
    items, done, zoe = {}, {}, None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if "zoe_ref" in o:
                zoe = o["zoe_ref"]
            elif o.get("done"):
                done = o
            elif "id" in o:
                items[o["id"]] = o
    except OSError:
        pass
    return items, done, zoe


def zoe_clips() -> list[str]:
    d = Path(env_str("ZOE_OMI_REPASS_ZOE_CLIPS", "") or os.path.expanduser("~/.zoe/kokoro_cache"))
    try:
        return sorted(str(p) for p in d.glob("*.wav"))[:60]
    except OSError:
        return []


def pick_rehearsal(rows: list, recording_id: str = "") -> list:
    """ONE recording for the daytime rehearsal: the one named, else the newest 20-180 s one (long enough to be real, short enough to be a short run), else the newest."""
    if recording_id:
        return [r for r in rows if r["id"] == recording_id][:1]
    ok = [r for r in rows if 20.0 <= float(r["duration_s"] or 0) <= 180.0]
    pool = ok or rows
    return sorted(pool, key=lambda r: str(r["started_at"]))[-1:]


async def preview_one(r: dict, res: dict, *, refs: Refs, names: list) -> dict:
    """The REHEARSAL's read-only twin of ``finish_one``: the same words -> lines -> speaker labels -> name normalisation -> forget-ledger redaction, and the counts the shadow log
    would hold - but NOTHING is written and no audio is released. Counts only (never a word)."""
    import omi_recordings as omi
    lines = build_lines(res["words"], res["windows"], refs)
    texts = []
    for ln in lines:
        texts.append(await omi._redact_forgotten(r["user_id"], normalise_names(segment_fields(ln, refs)["text"], names), strict=True))
    text = " ".join(t for t in texts if t.strip()).strip()
    return {"words": len(res["words"]), "lines": len(lines), "speakers": len({ln["key"] for ln in lines}), "dur_s": res.get("dur_s"), "speech_s": res.get("speech_s"),
            "asr_s": res.get("asr_s"), "emb_s": res.get("emb_s"), "agree": word_agreement(r.get("transcript") or "", text), "chars": len(text),
            "me": sum(ln["key"] == "you" for ln in lines), "zoe": sum(ln["key"] == "zoe" for ln in lines), "named": sum(ln["key"].startswith("p:") for ln in lines)}


async def run_night(*, budget_s: float, runner: Optional[Callable[..., int]] = None, now: Optional[datetime] = None, dry_run: bool = False,
                    clock: Callable[[], float] = time.time, rehearse: bool = False, recording_id: str = "",
                    mem_available: Callable[[], float] = mem_available_mb) -> dict:
    """The night job. Plans the engines against ``budget_s``, runs them as SEPARATE worker processes (each GPU model loads alone after a compaction), fuses their chunk texts with ROVER, applies
    each finished recording, and updates the contender ledger. Returns a counts-only summary (the CLI prints it). ``runner(manifest, out, timeout_s, mem_max=, threads=) -> rc`` is injectable.

    PRODUCTION is a fixed WINDOW (owner, 2026-11-12): the pass uses ``budget_s`` and never more. Recordings are taken in order (important first, then the audio that expires first) and
    EACH one gets the whole pass - the Qwen3-ASR pivot, Parakeet and the best third voter, fused by ROVER - priced before anything starts (``plan_window``), with the voters' time
    reserved before the pivot runs. The first recording whose whole pass does not fit stops the plan; it and everything after it stay ``pending`` with their WAV and carry to the
    next window. A recording is only marked passed (``on`` or ``shadow``) when every expected voter ran on it. EVALUATION: only when production finished with time to spare - Whisper large-v3 and Granite on a rotating SAMPLE of the day's speech
    (``ZOE_OMI_REPASS_EVAL_MIN`` minutes), their hypotheses stored and judged in the ledger, never in the stored text.

    ``rehearse``: the daytime rehearsal: the REAL path (every engine, the fusion, a small evaluation sample) on ONE recording, whatever the flag says, writing NOTHING (no segments, state,
    ledger, hypotheses, audio release). It must pass before ZOE_OMI_NIGHT_REPASS goes to shadow."""
    import omi_repass_eval as eu
    from db_compat import get_compat_db
    m = "shadow" if rehearse else mode()
    res: dict = {"status": "ok", "mode": m, "rehearse": rehearse}
    if m == "off":
        return {"status": "flag_off", "mode": m}
    started = clock()
    seen = erase_gate.snapshot()
    async with get_compat_db() as db:
        rows = await pending_recordings(db, now=now)
        if rehearse:
            rows = pick_rehearsal(rows, recording_id)
        if not dry_run and not rehearse:
            try:
                await drop_stale_proposals(db, now=now)
                await db.commit()
            except Exception:  # noqa: BLE001 - pre-0075
                pass
    if not rows:
        return {**res, "status": "idle", "pending": 0}
    audio_s = sum(float(r["duration_s"] or 0) for r in rows)
    st = read_state()
    pref = asr_backend()
    if pref not in BACKENDS or diar_backend() not in DIAR_BACKENDS:
        return {**res, "status": "bad_backend", "asr": pref, "diar": diar_backend(), "known": sorted(BACKENDS)}
    plan = plan_model(audio_s, budget_s, st.get("rtf"), pref)
    primary = pref if pref == eu.PIVOT else plan["model"]       # the pivot is never swapped for the cheaper engine
    rt = {**DEFAULT_RTF, **(st.get("rtf") or {})}
    multi = primary == eu.PIVOT
    second = second_backend() if multi else ""
    thirds = [c for c in third_choices() if c not in (primary, second)] if second else []
    third = thirds[0] if thirds else ""                          # the BEST configured third voter: the window buys fewer recordings, never a weaker vote
    expected = [x for x in (second, third) if x]                 # every recording this window passes gets ALL of these, or it is not marked passed
    evals = [e for e in eval_backends() if e not in (primary, *expected)] if multi and second else []
    voters = [primary] + expected
    res.update(pending=len(rows), audio_s=round(audio_s), model=primary, est_s=plan["est_s"], fits=plan["fits"], fallback_model=plan["fallback"], engines=voters, eval_engines=evals,
               window_s=round(budget_s))
    items, names_by_user, cand_by_user = [], {}, {}
    async with get_compat_db() as db:
        for r in rows:
            if r["user_id"] not in names_by_user:
                names_by_user[r["user_id"]] = await household_names(db, r["user_id"])
                cand_by_user[r["user_id"]] = await candidate_recording_ids(db, r["user_id"])
    rows = rank_important(rows, names_by_user, cand_by_user)          # important first, then the audio that expires first
    # ── THE WINDOW: each recording's WHOLE pass (pivot + every expected voter) is priced before anything starts; the ones that fit run, the rest carry, in order ──
    deadline = started + max(0.0, budget_s - 45.0)                # the workers' hard edge: 45 s of the window are kept back for the fusion, the DB writes and the release of audio
    f_speech = float(st.get("speech_frac") or SPEECH_FRAC)
    c_vote = sum(rt.get(e, 1.0) for e in expected)               # per SPEECH second
    r_piv = rt.get(primary, 1.0)                                 # per AUDIO second (a night's measured worker rtf; the default is per speech second, so it over-prices: safe)
    costs = [(r["id"], float(r["duration_s"] or 0) * (r_piv + f_speech * c_vote)) for r in rows]
    head, oversize = plan_window(costs, (deadline - clock()) * PLAN_MARGIN, fixed_s=LOAD_S * (1 + len(expected)))
    head_set = set(head)
    reserve_s = sum(float(r["duration_s"] or 0) * f_speech * c_vote for r in rows if r["id"] in head_set) + LOAD_S * len(expected)
    res.update(planned=len(head), oversize=len(oversize))
    if dry_run:
        return {**res, "status": "dry_run"}
    if not head:
        return {**res, "status": "ok", "done": 0, "carried": len(rows), "failed": 0, "used_s": round(clock() - started), "missing_voters": None,
                "carried_min": round(audio_s / 60.0, 2), "why": "window_too_small"}
    threads = env_int("ZOE_OMI_REPASS_THREADS", 4)
    zoe_ref = st.get("zoe_ref")
    tag_by_user = {u: await member_tags(u) for u in names_by_user}     # a member who turned voice tagging off: the worker computes no speaker windows for their audio
    for r in rows:
        if r["id"] in head_set:
            items.append({"id": r["id"], "path": r["audio_path"], "hint": name_hint(names_by_user[r["user_id"]]), "diar": tag_by_user[r["user_id"]]})
    work = home_dir() / "work"
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    try:
        eu.sweep_work(home_dir())          # a run SIGKILLed / rebooted mid-flight leaves its worker files: recovered here, and by retention + erasure
    except Exception:  # noqa: BLE001 - housekeeping never stops the night
        pass
    pivot_dl = min(deadline, max(clock() + 30.0, deadline - reserve_s))      # the voters' time is RESERVED: a pivot slower than planned stops here, it never eats the voters' share
    skipped: dict = {}

    async def one_pass(engine: str, its: list, *, diar: str, dl: float, clips: bool = False) -> dict:
        """One worker process: ONE model alone (the GPU recipe runs in the default runner just before it starts). Returns {rc, got, done, zref} or {skipped: why}."""
        if runner is None:
            have, need = mem_available(), need_mb(engine, diar != "none")
            if have < need:
                skipped[engine] = f"low_mem {round(have)}<{need}"
                return {"skipped": skipped[engine], "got": {}, "done": {}, "rc": None}
        stamp = uuid.uuid4().hex[:8]
        man, out = work / f"{stamp}.manifest.json", work / f"{stamp}.out.jsonl"
        man.write_text(json.dumps({"asr": engine, "diar": diar, "threads": threads, "gpu": env_str("ZOE_OMI_REPASS_GPU", "1") != "0", "models_dir": models_dir(), "r34_dir": env_str("ZOE_OMI_TAG_MODEL_DIR", "").strip() or os.path.expanduser("~/.cache/zoe-speaker-resnet34"),
                                   "chunk_s": env_float("ZOE_OMI_REPASS_CHUNK_S", 14.0), "deadline_epoch": min(dl, deadline - 20.0), "ct2_dir": ct2_dir() if engine.startswith("whisper-gpu") else "",
                                   "rtf": rt.get(engine, 1.0), "items": its, "zoe_clips": [] if (zoe_ref or not clips) else zoe_clips()}))
        os.chmod(man, 0o600)
        try:
            rc = (runner or spawn_worker)(man, out, max(1.0, min(dl + 20.0, deadline) - clock()), mem_max=scope_mem_max(engine, diar != "none"), threads=threads)
            got, done, zref = read_results(out)
        finally:
            for p in (man, out):
                try:
                    p.unlink()
                except OSError:
                    pass
        if rc == 3 and not got:
            skipped[engine] = "model_unavailable"
            return {"rc": rc, "got": got, "done": done, "zref": zref, "skipped": "model_unavailable"}      # the pivot caller returns before any recording's retry is consumed
        return {"rc": rc, "got": got, "done": done, "zref": zref}

    # ── pass 1: the pivot, on all the audio, with the speaker windows ──
    p1 = await one_pass(primary, items, diar=diar_backend(), dl=pivot_dl, clips=True)          # the pivot on the window's recordings, inside its share
    if p1.get("skipped"):
        return {**res, "status": "low_mem" if p1["skipped"].startswith("low_mem") else "model_missing", "why": p1["skipped"], "have_mb": round(mem_available()), "need_mb": need_mb(primary)}
    got1, done1, rc = p1["got"], p1["done"], p1["rc"]
    if p1.get("zref") and not zoe_ref:
        st["zoe_ref"] = zoe_ref = p1["zref"]
    res["aborted"] = done1.get("aborted") or None
    ok1 = [it for it in items if (got1.get(it["id"]) or {}).get("ok")]
    by_id = {r["id"]: r for r in rows}
    for it in ok1:          # belt and braces (an older worker ignores the per-item switch): an opted-out member's windows never reach a channel mean or a segment
        if not it["diar"]:
            got1[it["id"]]["windows"] = []

    # ── the pendant channel mean per member: this session's turns (>= 10 min of speech), else the stored per-device mean; and the wearer's loud cut ──
    mu_by: dict = {}
    loud_by: dict = {}
    user_rids: dict = {}
    for it in ok1:        # one channel mean per (member, DEVICE): two pendants are two channels and never share an offset
        user_rids.setdefault((by_id[it["id"]]["user_id"], by_id[it["id"]].get("device_id") or ""), []).append(it["id"])
    for key_, rids_ in user_rids.items():
        uid_, dev_ = key_
        wins_ = [w for rid_ in rids_ for w in (got1[rid_].get("windows") or [])]
        speech_ = sum(float(got1[rid_].get("speech_s") or 0.0) for rid_ in rids_)
        loud_by[key_] = float(np.percentile([float(w[3]) for w in wins_ if len(w) > 3], 75)) if any(len(w) > 3 for w in wins_) else None
        async with get_compat_db() as db:
            if wins_ and speech_ >= who.SESSION_MIN_S:
                mu_by[key_] = who.session_mean([w[2] for w in wins_])
                if m == "on":             # shadow (and a rehearsal) keeps the mean in memory only: shadow stores nothing biometric
                    try:
                        from manner_block import eligible as _elig
                        async with erase_gate.writing(uid_, seen.get(uid_, 0)) as live_:        # a member erased while the worker ran keeps no channel mean (it is derived from their voice)
                            if live_ and await _elig(uid_):
                                await who.store_mean(db, uid_, MODEL_ID, who.CHANNEL_PENDANT, dev_, mu_by[key_], len(wins_), speech_, now)
                                await db.commit()
                    except Exception as exc:  # noqa: BLE001 - pre-0075
                        logger.warning("OMI_REPASS_MEAN_NOT_STORED reason=%s", type(exc).__name__)
            else:
                mu_by[key_] = await who.load_mean(db, uid_, MODEL_ID, who.CHANNEL_PENDANT, dev_)
    refs_cache: dict = {}

    async def refs_for(uid_: str, dev_: str = "") -> Refs:
        key_ = (uid_, dev_ or "")
        if key_ not in refs_cache:
            async with get_compat_db() as db:
                refs_cache[key_] = await load_refs(db, uid_, zoe_ref, mu_by.get(key_), loud_by.get(key_), persist=m == "on")
        return refs_cache[key_]
    chunked = [it for it in ok1 if isinstance(got1[it["id"]].get("chunks"), list)]

    async def voter_pass(engine: str, ids: list) -> dict:
        want = set(ids)
        its = [{"id": it["id"], "path": it["path"], "chunks": [[c[0], c[1]] for c in got1[it["id"]]["chunks"]]} for it in chunked if it["id"] in want]
        if not its or deadline - clock() < 60:
            vskip[engine] = "no_time"                      # never started (under a minute left): its recordings carry; reported as ``short_voters``, not as a failure
            return {"got": {}, "done": {}, "rc": None}
        return await one_pass(engine, its, diar="none", dl=deadline)

    # ── passes 2 and 3: EVERY expected voter on the recordings the pivot finished, in order, as far as the time left buys the WHOLE vote ──
    rank_of = {r["id"]: i for i, r in enumerate(rows)}
    room, ids3 = (deadline - clock()) * 0.9 - LOAD_S * len(expected), []
    for it in sorted(chunked, key=lambda x: rank_of[x["id"]]):          # a recording the pivot heard no speech in still gets every voter's (empty) reading: no exemption
        sp = sum(max(0.0, float(c[1]) - float(c[0])) for c in got1[it["id"]]["chunks"])       # priced on the SPEECH a voter is handed, not the file's length
        if sp * c_vote > room:
            break                                                                             # it, and everything after it, carries
        room -= sp * c_vote
        ids3.append(it["id"])
    vgot: dict = {}
    vskip: dict = {}                       # production-voter outcomes, kept apart from ``skipped`` (which the evaluation contenders also write)
    for eng in expected:
        if not ids3:
            break
        pv = await voter_pass(eng, ids3)
        vgot[eng] = {k: v for k, v in pv["got"].items() if v.get("ok") and len(v.get("chunks") or []) == len(got1[k]["chunks"])}
    if expected:
        full = {rid for rid in ids3 if all(rid in vgot.get(e, {}) for e in expected)}
    else:
        full = {it["id"] for it in chunked}
    if not expected:
        full |= {it["id"] for it in ok1 if not isinstance(got1[it["id"]].get("chunks"), list)}   # a single-engine pass without chunk texts IS the whole pass; with voters expected it carries
    complete = bool(full)
    voters = [primary] + [e for e in expected if vgot.get(e)]
    evals = [e for e in evals if e not in voters] if complete else []             # the evaluation sample only when this window passed something whole
    res["engines"], res["eval_engines"] = voters, evals
    miss = {e: skipped.get(e) or "no_result" for e in expected if ids3 and e not in vskip and any(rid not in vgot.get(e, {}) for rid in ids3)}   # a voter that FAILED on a recording it was given
    res["short_voters"] = vskip or None
    res["missing_voters"] = miss or None

    # ── fusion: ROVER per chunk over the voters that finished that recording ──
    fusion = env_str("ZOE_OMI_REPASS_FUSION", "rover")
    chunk_rows: dict = {}                   # rid -> [row dicts]
    fused_res: dict = {}
    for it in ok1:
        rid, o = it["id"], got1[it["id"]]
        if rid not in full:
            continue                       # not passed whole this window: it carries, untouched, with its WAV
        names = names_by_user.get(by_id[rid]["user_id"], [])
        if not isinstance(o.get("chunks"), list):
            fused_res[rid] = o
            continue
        rws, words = [], []
        for i, (t0, t1, text) in enumerate(o["chunks"]):
            hy = {primary: text}
            for eng, g in vgot.items():
                if rid in g:
                    hy[eng] = g[rid]["chunks"][i][2]
            fz = eu.fuse(hy, primary, fusion, names)
            rws.append({"u": by_id[rid]["user_id"], "rid": rid, "i": i, "t0": t0, "t1": t1, "hyps": hy, "rover": fz["text"], "slots": fz["slots"], "low_conf": fz["low_conf"], "voters": fz["voters"],
                        "_fz": fz})
            toks = fz["text"].split()
            for k, w in enumerate(toks):
                words.append([(" " if words else "") + w, round(t0 + (t1 - t0) * k / len(toks), 3), round(t0 + (t1 - t0) * (k + 1) / len(toks), 3), 0.6])
        chunk_rows[rid] = rws
        fused_res[rid] = {**o, "words": words}

    # ── evaluation: a rotating sample of the day's speech, only with production done and time to spare ──
    sample_s = 0.0
    if evals and complete and chunk_rows:
        flat = [(it["id"], r["i"], r["t0"], r["t1"]) for it in chunked for r in chunk_rows.get(it["id"], [])]
        budget_speech = 60.0 if rehearse else env_float("ZOE_OMI_REPASS_EVAL_MIN", 25.0) * 60.0
        pick = eu.pick_sample(flat, budget_speech, (now or _now()).date().toordinal())
        for eng in evals:
            need_s = sum(c[3] - c[2] for c in pick) * rt.get(eng, 1.0) + 40.0
            if not pick or deadline - clock() < need_s + 30:
                skipped[eng] = "no_time"
                continue
            grouped: dict = {}
            for rid, i, t0, t1 in pick:
                grouped.setdefault(rid, []).append((i, t0, t1))
            its = [{"id": rid, "path": next(x["path"] for x in items if x["id"] == rid), "chunks": [[t0, t1] for _i, t0, t1 in g]} for rid, g in grouped.items()]
            pe = await one_pass(eng, its, diar="none", dl=deadline)
            for rid, g in grouped.items():
                oo = (pe["got"].get(rid) or {})
                if oo.get("ok") and len(oo.get("chunks") or []) == len(g):
                    for (i, _t0, _t1), ch in zip(g, oo["chunks"]):
                        chunk_rows[rid][i]["hyps"][eng] = ch[2]
        sample_s = sum(c[3] - c[2] for c in pick)

    # ── what each model contributed (counts only) ──
    cl = []
    for rws in chunk_rows.values():
        for r in rws:
            fz = r["_fz"]
            withs = {}
            for mm in (x for x in r["hyps"] if x in evals):
                withs[mm] = eu.fuse({**{k: v for k, v in r["hyps"].items() if k not in evals}, mm: r["hyps"][mm]}, primary, fusion, [])
            cl.append({"hyps": {k: v for k, v in r["hyps"].items() if v is not None}, "fused": r["rover"], "speech_s": r["t1"] - r["t0"], "sources": fz["sources"], "with": withs})
    contrib = eu.contributions(cl, voters)
    res.update(low_conf_chunks=sum(1 for rws in chunk_rows.values() for r in rws if r["low_conf"]), contested_slots=sum(len(r["slots"]) for rws in chunk_rows.values() for r in rws),
               sample_speech_s=round(sample_s), chunks=sum(len(v) for v in chunk_rows.values()), skipped=skipped or None,
               contributions={k: {"chunks": v["chunks"], "sole": v["sole"], "invented": v["invented"], "words": v["words"], "changed": v.get("changed", 0),
                                  "agree": round(v["agree_sum"] / v["agree_n"], 3) if v["agree_n"] else None} for k, v in contrib.items()})
    model_label = (f"{'+'.join(voters)}/{fusion}" if len(voters) > 1 else primary) + f"+{diar_backend()}"
    if rehearse:
        o = fused_res.get(rows[0]["id"])
        if o is None or not (got1.get(rows[0]["id"]) or {}).get("ok"):
            return {**res, "status": "rehearsal_failed", "why": ((got1.get(rows[0]["id"]) or {}).get("why", "no result")), "worker_rc": rc}
        refs = await refs_for(rows[0]["user_id"], rows[0].get("device_id") or "")
        pv = await preview_one(rows[0], o, refs=refs, names=names_by_user.get(rows[0]["user_id"], []))
        return {**res, "status": "rehearsal_ok", **pv, "worker_rc": rc, "device": done1.get("device"), "worker_rtf": done1.get("rtf"), "peak_rss_mb": done1.get("peak_rss_mb"),
                "gpu_peak_mb": done1.get("gpu_peak_mb"), "load_s": done1.get("load_s"), "wall_s": round(clock() - started)}
    ok_ids = {it["id"] for it in ok1}
    tally = {"replaced": 0, "shadow": 0, "erased": 0, "gone": 0, "failed": 0, "carried": 0, "agree_sum": 0.0, "me": 0, "named": 0, "zoe": 0, "proposed": 0}
    who_rows: list = []
    drop_rids: set = set()                  # recordings whose hypotheses are NOT kept: erased mid-job, deleted meanwhile, or never finished
    for rid, r in by_id.items():
        o = fused_res.get(rid)
        raw = got1.get(rid)
        if o is None:
            carry = rid not in head_set or rid in ok_ids or (raw is None and rc in (0, 124)) or (raw is not None and raw.get("why") in ("deadline", "backend_failed"))
            await note_failure(rid, carry=carry)
            tally["carried" if carry else "failed"] += 1
            continue
        refs = await refs_for(r["user_id"], r.get("device_id") or "")
        contested = [{k: v for k, v in x.items() if k != "_fz"} for x in chunk_rows.get(rid, []) if eu.is_contested(x)]
        if contested:
            o["check_rows"] = contested
        c = await finish_one(r, o, refs=refs, names=names_by_user.get(r["user_id"], []), model_label=model_label, seen=seen, m=m, now=now)
        s = c["status"]
        tally[s] = tally.get(s, 0) + 1
        for wi in c.get("who_items") or []:
            who_rows.append({"kind": "who_said", "u": r["user_id"], "rid": rid, **wi})
        if s in ("replaced", "shadow"):
            tally["agree_sum"] += float(c.get("agree") or 0.0)
            for k in ("me", "named", "zoe", "proposed"):
                tally[k] += int(c.get(k) or 0)
        else:
            drop_rids.add(rid)
    done_n = tally["replaced"] + tally["shadow"]
    cap = {"speech_min": 0.0, "fused3_min": 0.0, "fused2_min": 0.0, "qwen_only_min": 0.0, "carried_min": 0.0}
    for rid, r in by_id.items():                  # the night's capacity, in minutes of speech - the evidence for a separate pre-window slot
        sp = float((got1.get(rid) or {}).get("speech_s") or r["duration_s"] or 0) / 60.0
        if fused_res.get(rid) is None:
            cap["carried_min"] += float(r["duration_s"] or 0) / 60.0
            continue
        cap["speech_min"] += sp
        nv = 1 + sum(1 for e in vgot if rid in vgot[e])
        cap["fused3_min" if nv >= 3 else "fused2_min" if nv == 2 else "qwen_only_min"] += sp
    cap = {k: round(v, 2) for k, v in cap.items()}
    meas = [(float(got1[i_]["speech_s"] or 0), float(got1[i_].get("dur_s") or by_id[i_]["duration_s"] or 0)) for i_ in ok_ids if got1[i_].get("speech_s") is not None]
    if sum(d for _s, d in meas) >= 600:                           # the next window's speech share, measured (>= 10 min of audio)
        st["speech_frac"] = round(sum(sp for sp, _d in meas) / sum(d for _s, d in meas), 3)
    night = (now or _now()).date().isoformat()
    try:                                                         # the evaluation record: counts in the ledger, the hypotheses (words, private, 7 days) in the hyps store
        home = home_dir()
        stored = [{k: v for k, v in r.items() if k != "_fz"} for rid, rws in chunk_rows.items() if rid not in drop_rids for r in rws]
        stored += [w for w in who_rows if w["rid"] not in drop_rids]                  # the "who said this?" items ride in the same private store (ids and scores only, no words)
        if cl or who_rows:
            if cl:
                eu.append_ledger(home, night, contrib, evals)
            by_user: dict[str, list] = {}
            for row in stored:
                by_user.setdefault(row["u"], []).append(row)
            for u, urows in by_user.items():            # per member, INSIDE the erase gate the hypothesis sweep shares: an erase that completed since the job was read keeps nothing
                async with erase_gate.writing(u, seen.get(u, 0)) as live:
                    if live:
                        g0 = erase_gate.generation(u)
                        try:
                            rids_ = sorted({x["rid"] for x in urows})
                            async with get_compat_db() as db_:        # a recording deleted since the job was read (delete_recording does not move the generation) keeps no hypotheses
                                alive = {str(r_[0]) for r_ in await _all(db_, f"SELECT id FROM omi_recordings WHERE user_id = ? AND status != 'deleted' AND id IN ({','.join('?' * len(rids_))})", (u, *rids_))}
                            keep = [await _redact_row(u, row) for row in urows if row["rid"] in alive]
                        except Exception as exc:  # noqa: BLE001 - fail closed: nothing of this member's words is stored this night
                            logger.warning("OMI_REPASS_HYPS_NOT_STORED reason=%s", type(exc).__name__)
                            continue
                        eu.store_hyps(home, night, keep)
                        if erase_gate.generation(u) != g0:
                            eu.forget_user(home, u)                 # an erase landed during the write: swept again
            eu.prune_hyps(home, (now or _now()).date())
        if (now or _now()).weekday() == 0:                       # Monday: the weekly verdict line for the morning report
            res["contenders"] = eu.contender_line(eu.read_ledger(home))
    except OSError as exc:
        logger.warning("OMI_REPASS_LEDGER_NOT_WRITTEN reason=%s", type(exc).__name__)
    if done1.get("rtf"):
        st.setdefault("rtf", {})[primary] = done1["rtf"]
    st["last"] = {"at": _iso(now or _now()), "model": primary, "engines": voters, "files": done_n, "device": done1.get("device"), "capacity": cap}
    write_state(st)
    res.update(done=done_n, replaced=tally["replaced"], shadow=tally["shadow"], carried=tally["carried"], failed=tally["failed"], erased=tally["erased"],
               agree=round(tally["agree_sum"] / done_n, 3) if done_n else None, me_lines=tally["me"], named_lines=tally["named"], zoe_lines=tally["zoe"],
               proposed=tally["proposed"], capacity=cap, **cap, used_s=round(clock() - started), worker_rc=rc, device=done1.get("device"), worker_rtf=done1.get("rtf"), peak_rss_mb=done1.get("peak_rss_mb"),
               wall_s=round(clock() - started))
    return res
