"""The pendant NIGHT RE-PASS: the best transcript the box can make of a day's pendant audio, made while Zoe sleeps.

Flag ``ZOE_OMI_NIGHT_REPASS`` = off | shadow | on (default OFF). Spec and owner acceptance: ``docs/knowledge/omi-night-repass.md``.

Moonshine (the live STT rock, in-process, 20 s chunks, between voice turns) writes the FIRST transcript within minutes of a sync. This module is
the SECOND, offline pass the night window runs before the 12B loads, with the 4B brain and Kokoro stopped: faster-whisper large-v3 (distil-large-v3
when the day's audio would overrun the budget) with VAD, word timestamps and a household name hint, plus ECAPA speaker windows matched against the
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
  * NO SILENT SELF-LABELLING: a line is "me" only against a CONFIRMED wearer print (windows the owner labelled "me", or a heuristic proposal the owner
    confirmed). Without one every line reads ``unverified``. A model label never overwrites a human one: tags, "me/other" labels and not-a-person
    marks on the first-pass segments are carried onto the overlapping new segments.
  * A suggestion is never a tag: a recognised family member / guest voice becomes ``suggested_person_id`` (the owner confirms), as in omi_voices.
  * Unfinished work carries over: a recording the budget could not reach stays ``pending`` for the next night (it is not a failure).
"""
from __future__ import annotations

import asyncio
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
MODEL_ID = "ecapa-voxceleb-1.5s"                  # the speaker-space tag on every embedding / voiceprint this pass writes (never mixed with resnet34's)
MAX_PASS_ATTEMPTS = 3
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
DIAR_BACKENDS = ("ecapa",)                        # the speaker-window engines the worker has (``omi_repass_worker.DIAR``); MODEL_ID is tied to this one
DIAR_RSS_MB = 700
DEFAULT_RTF = {k: v["rtf"] for k, v in BACKENDS.items()}
PLAN_MARGIN = 0.85                                # plan to use at most this much of the budget (the estimate is a mean over files of very different density)
HINT_MAX_NAMES = 30
PROPOSAL_KEEP_DAYS = 14
PROPOSAL_MAX = 20
_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,38}$")
_ST_OK = "('done', 'empty_transcript')"


# ── flags ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def mode() -> str:
    m = env_str("ZOE_OMI_NIGHT_REPASS", "off").strip().lower()
    return m if m in MODES else "off"


def asr_backend() -> str:
    """``ZOE_OMI_NIGHT_ASR`` - the ASR backend id (a key of ``BACKENDS``). An unknown id is returned as is: ``run_night`` refuses it loudly rather than guessing."""
    return env_str("ZOE_OMI_NIGHT_ASR", DEFAULT_ASR).strip() or DEFAULT_ASR


def diar_backend() -> str:
    return env_str("ZOE_OMI_NIGHT_DIAR", "ecapa").strip() or "ecapa"


def scope_mem_max(asr: str, diar: bool = True) -> str:
    """The capped scope's MemoryMax: ``ZOE_OMI_REPASS_MEM_MAX`` if set, else the declared peaks + 20 % headroom."""
    forced = env_str("ZOE_OMI_REPASS_MEM_MAX", "")
    if forced:
        return forced
    return f"{int((BACKENDS[asr]['peak_rss_mb'] + (DIAR_RSS_MB if diar else 0)) * 1.2)}M"


def need_mb(asr: str, diar: bool = True) -> int:
    """MemAvailable the pass needs before it starts: the engine's declared peak + the speaker model (only the first pass loads it) + the floor."""
    return int(BACKENDS[asr]["peak_rss_mb"] + (DIAR_RSS_MB if diar else 0) + FLOOR_MB)


def _ids(name: str, default: str) -> list[str]:
    return [x.strip() for x in env_str(name, default).split(",") if x.strip() and x.strip() in BACKENDS]


def second_backend() -> str:
    """The second production voter (``ZOE_OMI_NIGHT_ASR_SECOND``, default parakeet-tdt; empty = none)."""
    return (_ids("ZOE_OMI_NIGHT_ASR_SECOND", "parakeet-tdt") or [""])[0]


def third_choices() -> list[str]:
    """The third production voter, in preference order (``ZOE_OMI_NIGHT_ASR_THIRD``): ROVER needs at least three voters to outvote the pivot. Whisper large-v3 (GPU) when the night
    has room - the measured best third voter - else distil-large-v3 (about 40 % cheaper, within noise of it on the hand reference). The first that fits the budget is used."""
    return _ids("ZOE_OMI_NIGHT_ASR_THIRD", "whisper-gpu:large-v3,whisper-gpu:distil-large-v3")


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


def you_min() -> float:
    return env_float("ZOE_OMI_REPASS_YOU_MIN", 0.45)


def name_min() -> float:
    return env_float("ZOE_OMI_REPASS_NAME_MIN", 0.45)


def name_margin() -> float:
    return env_float("ZOE_OMI_REPASS_NAME_MARGIN", 0.08)


def zoe_min() -> float:
    return env_float("ZOE_OMI_REPASS_ZOE_MIN", 0.55)


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


class Refs:
    """The voices a window is matched against: the wearer (confirmed print only), Zoe's own TTS, and the owner's taught people (ECAPA space)."""

    def __init__(self, you: Optional[Any] = None, zoe: Optional[Any] = None, people: Optional[list] = None):
        self.you = None if you is None else _unit(you)
        self.zoe = None if zoe is None else _unit(zoe)
        self.people = [{"person": p["person"], "v": _unit(p["v"]), "rej": int(p.get("rej", 0))} for p in (people or [])]


def label_window(e: Any, refs: Refs) -> dict:
    """One window's verdict: ``zoe`` | ``you`` | ``person`` (+ id) | ``other`` | ``unsure`` | ``unverified``, with the cosines that decided it.
    ``unverified`` is what a window reads when there is no confirmed wearer print - Zoe's voice excepted - so nothing is ever "me" by default."""
    e = _unit(e)
    s_you = float(refs.you @ e) if refs.you is not None else None
    s_zoe = float(refs.zoe @ e) if refs.zoe is not None else None
    scored = sorted(((float(p["v"] @ e), p) for p in refs.people), key=lambda t: -t[0])
    out = {"label": "unverified", "you": s_you, "zoe": s_zoe, "person": None, "person_score": None}
    if s_zoe is not None and s_zoe >= zoe_min() and (s_you is None or s_zoe > s_you):
        out["label"] = "zoe"
        return out
    if scored:
        best_s, best = scored[0]
        runner = max([scored[1][0]] if len(scored) > 1 else [-1.0])
        if s_you is not None:
            runner = max(runner, s_you)
        margin = name_margin() + 0.02 * min(best["rej"], 5)
        if best_s >= name_min() and best_s - runner >= margin:
            out.update(label="person", person=best["person"], person_score=best_s)
            return out
    if s_you is None:
        return out
    if s_you >= you_min():
        out["label"] = "you"
    elif s_you >= you_min() - 0.12:
        out["label"] = "unsure"
    else:
        out["label"] = "other"
    return out


def _key(v: dict) -> str:
    return f"p:{v['person']}" if v["label"] == "person" else v["label"]


def build_lines(words: list, windows: list, refs: Refs, *, gap_s: float = 2.0, smooth: int = 3) -> list[dict]:
    """Words (``[word, t0, t1, p]``) + windows (``[t0, t1, emb, rms]``) -> speaker lines: each word takes the label of the windows covering its
    midpoint (a vote, strong verdicts weigh double), labels are smoothed over +-``smooth`` words, and runs of one label join into a line unless a
    silence of ``gap_s`` separates them. A one-or-two-word run of an uncertain label between two lines is absorbed (label flicker)."""
    if not words:
        return []
    ws = [(float(w[1]), float(w[2])) for w in words]
    verdicts = [(float(w[0]), float(w[1]), label_window(w[2], refs)) for w in windows]
    raw: list[str] = []
    for a, b in ws:
        mid = (a + b) / 2.0
        votes: dict[str, int] = {}
        for t0, t1, v in verdicts:
            if t0 - 0.1 <= mid <= t1 + 0.1:
                votes[_key(v)] = votes.get(_key(v), 0) + (2 if v["label"] in ("zoe", "you", "person") else 1)
        raw.append(max(votes.items(), key=lambda kv: kv[1])[0] if votes else "unverified" if refs.you is None else "unsure")
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
        if merged and ln["start"] - merged[-1]["end"] < gap_s and len(ln["text"].split()) <= 2 and ln["key"] in ("unsure", "unverified") \
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
        cover = [w for w in windows if float(w[0]) < ln["end"] and float(w[1]) > ln["start"]]
        emb = _unit(np.mean([_unit(w[2]) for w in cover], axis=0)) if cover else None
        out.append({"key": ln["key"], "start_ms": int(round(ln["start"] * 1000)), "end_ms": max(int(round(ln["end"] * 1000)), int(round(ln["start"] * 1000)) + 1),
                    "text": text, "windows": len(cover), "embedding": emb, "low_conf": sum(p < 0.4 for p in ln["ps"]) / max(1, len(ln["ps"])) > 0.3})
    return out


def segment_fields(line: dict, refs: Refs) -> dict:
    """A line as the ``omi_segments`` insert wants it (``omi_voices.store_segments``). ``person`` -> speaker ``other`` + a SUGGESTED person (set by the caller);
    ``zoe`` -> ``other`` + ``not_person='zoe'`` (the pendant heard Zoe's own voice, not a person)."""
    key = line["key"]
    e = line.get("embedding")
    score = None
    if e is not None and refs.you is not None:
        score = round(float(refs.you @ e), 4)
    speaker = {"you": "you", "zoe": "other", "unsure": "unsure", "unverified": "unverified"}.get(key, "other")
    pscore = None
    if key.startswith("p:") and e is not None:
        mine = [p for p in refs.people if p["person"] == key[2:]]
        pscore = round(float(mine[0]["v"] @ e), 4) if mine else None
    return {"start_ms": line["start_ms"], "end_ms": line["end_ms"], "speaker": speaker, "score": score, "windows": line["windows"], "embedding": e,
            "text": line["text"], "not_person": "zoe" if key == "zoe" else None, "person": key[2:] if key.startswith("p:") else None, "person_score": pscore}


def word_agreement(a: str, b: str, cap: int = 2500) -> float:
    """How much of two transcripts is the same words, in order (0..1): the shadow log's headline number. Counts only; no text leaves this."""
    x, y = re.findall(r"[a-z0-9']+", a.lower())[:cap], re.findall(r"[a-z0-9']+", b.lower())[:cap]
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return round(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(), 4)


def carry_labels(new: list[dict], old: list[dict], min_share: float = 0.5) -> int:
    """Copy the owner's DECISIONS from the first-pass segments onto the new segment that mostly overlaps them (a new line is covered >= ``min_share``
    by one old line that has a decision). Decisions: owner_label, tagged_person_id (+ source / time), not_person. Returns how many new lines got one."""
    n = 0
    for s in new:
        dur = max(1, s["end_ms"] - s["start_ms"])
        best, best_ov = None, 0
        for o in old:
            if not (o.get("owner_label") or o.get("tagged_person_id") or o.get("not_person")):
                continue
            ov = max(0, min(s["end_ms"], o["end_ms"]) - max(s["start_ms"], o["start_ms"]))
            if ov > best_ov:
                best, best_ov = o, ov
        if best is not None and best_ov / dur >= min_share:
            s["carry"] = {k: best.get(k) for k in ("owner_label", "owner_labelled_at", "tagged_person_id", "tag_source", "tagged_at", "not_person")}
            if best.get("not_person") and not s.get("not_person"):
                s["not_person"] = best["not_person"]
            n += 1
    return n


def propose_wearer(windows: list, *, loud_pct: float = 70.0, link: float = 0.6, min_cluster: int = 6, max_n: int = PROPOSAL_MAX) -> list[int]:
    """The loudest-nearest-voice heuristic: among the speech windows at or above the ``loud_pct`` RMS percentile (the wearer's mouth is the nearest
    thing to the pendant), cluster greedily by cosine and return the indices of the largest cluster's windows closest to its centre. A PROPOSAL only:
    nothing here makes anyone "me" - the owner confirms (``confirm_wearer``). [] when no cluster is big enough."""
    if len(windows) < min_cluster:
        return []
    rms = np.array([float(w[3]) if len(w) > 3 else 0.0 for w in windows])
    cut = float(np.percentile(rms, loud_pct))
    idx = [i for i in range(len(windows)) if rms[i] >= cut and rms[i] > 0]
    clusters: list[dict] = []
    for i in idx:
        e = _unit(windows[i][2])
        best = max(clusters, key=lambda c: float(c["c"] @ e), default=None)
        if best is not None and float(best["c"] @ e) >= link:
            best["m"].append(i)
            best["c"] = _unit(best["c"] * (len(best["m"]) - 1) + e)
        else:
            clusters.append({"c": e, "m": [i]})
    if not clusters:
        return []
    top = max(clusters, key=lambda c: len(c["m"]))
    if len(top["m"]) < min_cluster:
        return []
    ranked = sorted(top["m"], key=lambda i: -float(top["c"] @ _unit(windows[i][2])))
    return sorted(ranked[:max_n])


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


async def pending_recordings(db, *, now: Optional[datetime] = None, limit: int = 500) -> list[dict]:
    """Recordings the night pass owes a look, oldest first (recordings the first pass found NO words in go last: a pendant that records ~12 h a day
    cannot all be re-read in one night, so the ones most likely to hold speech are done first and the rest carry): first-pass done (or empty) rows whose WAV is still on disk, inside retention, not yet
    passed. A recording whose file vanished is not listed (nothing to process; ``audio_path`` is cleared by the retention loop)."""
    stamp = _iso(now or _now())
    rows = await _all(db, "SELECT id, user_id, audio_path, duration_s, started_at, status, transcript, repass_attempts FROM omi_recordings "
                          f"WHERE status IN {_ST_OK} AND audio_path != '' AND repass_state IN ('pending', '') AND repass_attempts < ? "
                          "AND (audio_expires_at IS NULL OR audio_expires_at > ?) ORDER BY CASE WHEN status = 'empty_transcript' THEN 1 ELSE 0 END, started_at, created_at LIMIT ?",
                      (MAX_PASS_ATTEMPTS, stamp, limit))
    out = []
    for r in rows:
        d = {k: r[k] for k in ("id", "user_id", "audio_path", "duration_s", "started_at", "status", "transcript", "repass_attempts")}
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
        except Exception:  # noqa: BLE001 - a ledger outage withholds nothing it cannot judge: the name is a spelling hint, not a memory
            pass
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


async def load_refs(db, user_id: str, zoe: Optional[Any]) -> Refs:
    import omi_voices
    people = []
    try:
        people = await omi_voices._voiceprints_for_suggest(db, user_id, MODEL_ID)
        people = [{"person": p["person"], "v": p["v"], "rej": p["rej"]} for p in people]
    except Exception as exc:  # noqa: BLE001
        if not omi_voices._missing(exc):
            raise
    return Refs(you=await wearer_print(db, user_id), zoe=zoe, people=people)


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


async def _learn_carried_tags(db, user_id: str, seg_ids: list[str], segs: list[dict], rid: str) -> int:
    """The owner's earlier tags (first-pass segments) teach the ECAPA person prints: each carried ``tagged_person_id`` line's embedding joins that
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


async def finish_one(r: dict, res: dict, *, refs: Refs, names: list, model_label: str, seen: dict, m: str, now: Optional[datetime] = None) -> dict:
    """Apply one worker result to one recording. ``m`` is the mode the job started in. Returns a counts-only summary dict (``status``: replaced |
    shadow | erased | gone | failed). Never raises for a data problem."""
    import omi_recordings as omi
    import omi_voices
    from db_compat import get_compat_db
    rid, uid = r["id"], r["user_id"]
    lines = build_lines(res["words"], res["windows"], refs)
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
    async with erase_gate.writing(uid, seen.get(uid, 0)) as live:
        async with get_compat_db() as db:
            if live:
                # REDACTION HAPPENS HERE, INSIDE THE GATE: an entity forget (``erasing(bump=False)``) does not move the generation, so it is only
                # ordered against this write by the lock itself - redacting before we held it could store text the ledger forgot in between.
                for sgm in segs:
                    sgm["text"] = await omi._redact_forgotten(uid, sgm["text"])
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
                try:
                    omi_voices.mark_overlaps(segs, await omi_voices.zoe_spoken_spans(db, uid, rid))
                    ids = await omi_voices.store_segments(db, uid, rid, segs, model_id=MODEL_ID, channel="pendant" if refs.you is not None else None)
                    for sid, s in zip(ids, segs):
                        sets, vals = [], []
                        if s.get("not_person"):
                            sets.append("not_person = ?")
                            vals.append(s["not_person"])
                        c = s.get("carry")
                        if c:
                            for k in ("owner_label", "owner_labelled_at", "tagged_person_id", "tag_source", "tagged_at"):
                                sets.append(f"{k} = ?")
                                vals.append(c.get(k))
                        elif s.get("person") and not s.get("not_person"):
                            sets += ["suggested_person_id = ?", "suggested_score = ?"]
                            vals += [s["person"], s.get("person_score")]
                        if sets:
                            await db.execute(f"UPDATE omi_segments SET {', '.join(sets)} WHERE id = ? AND user_id = ?", (*vals, sid, uid))
                    untagged = [(sid, s) for sid, s in zip(ids, segs) if not s.get("carry") and not s.get("not_person")]
                    if untagged:      # bystander voices: clusters + suggestions in the ECAPA space, exactly the first pass's machinery
                        await omi_voices.learn(db, uid, [i for i, _ in untagged], [s for _, s in untagged], model_id=MODEL_ID, you_min=you_min())
                    await _learn_carried_tags(db, uid, ids, segs, rid)
                    if pending_wearer_labels:
                        spans = [(o["start_ms"], o["end_ms"]) for o in pending_wearer_labels]
                        counts["wearer_windows"] = await _add_wearer_samples(db, uid, rid, res["windows"], spans, source="owner_label", status="confirmed")
                    for cid in former:
                        await omi_voices.rebuild_cluster(db, cid)
                except Exception as exc:  # noqa: BLE001 - the transcript is still better than none; but never a half-written segment set
                    logger.warning("OMI_REPASS_SEGMENTS_NOT_STORED id=%s reason=%s", rid, type(exc).__name__)
                    await db.rollback()
                    return {"status": "failed", "why": "segments:" + type(exc).__name__, **counts}
                state = "tagged" if refs.you is not None else "unverified"
                await db.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, speaker_state = ?, repass_state = 'done', repass_model = ?, repass_at = ?, "
                                 "repass_attempts = repass_attempts + 1 WHERE id = ? AND repass_state IN ('pending', '')", (text, state, model_label, stamp, rid))
                await db.commit()
                counts.update(status="replaced", carried=carried)
            if refs.you is None and res["windows"]:           # a proposal for the owner to confirm; never used until then
                try:
                    pick = propose_wearer(res["windows"])
                    if pick:
                        await _add_wearer_samples(db, uid, rid, [res["windows"][i] for i in pick], [], source="heuristic", status="proposed", proposal=stamp[:10])
                        await db.commit()
                        counts["proposed"] = len(pick)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OMI_REPASS_PROPOSAL_FAILED reason=%s", type(exc).__name__)
    await omi.release_tagged_audio(uid)            # the pass has seen it: the WAV goes unless the owner keeps it or a line still needs a name
    return counts


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


async def confirm_wearer(db, user_id: str, proposal_id: str) -> int:
    """THE owner's word that a proposal's windows are their own voice: they join the wearer print (and only then does a line read "me")."""
    cur = await db.execute("UPDATE omi_wearer_samples SET status = 'confirmed' WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id = ?",
                           (user_id, MODEL_ID, proposal_id))
    await db.commit()
    return int(getattr(cur, "rowcount", 0) or 0)


async def reject_wearer(db, user_id: str, proposal_id: str) -> int:
    cur = await db.execute("DELETE FROM omi_wearer_samples WHERE user_id = ? AND model_id = ? AND status = 'proposed' AND proposal_id = ?",
                           (user_id, MODEL_ID, proposal_id))
    await db.commit()
    return int(getattr(cur, "rowcount", 0) or 0)


async def drop_stale_proposals(db, *, now: Optional[datetime] = None) -> int:
    cut = _iso((now or _now()) - timedelta(days=PROPOSAL_KEEP_DAYS))
    cur = await db.execute("DELETE FROM omi_wearer_samples WHERE status = 'proposed' AND created_at < ?", (cut,))
    return int(getattr(cur, "rowcount", 0) or 0)


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
        texts.append(await omi._redact_forgotten(r["user_id"], normalise_names(segment_fields(ln, refs)["text"], names)))
    text = " ".join(t for t in texts if t.strip()).strip()
    return {"words": len(res["words"]), "lines": len(lines), "speakers": len({ln["key"] for ln in lines}), "dur_s": res.get("dur_s"), "speech_s": res.get("speech_s"),
            "asr_s": res.get("asr_s"), "emb_s": res.get("emb_s"), "agree": word_agreement(r.get("transcript") or "", text), "chars": len(text),
            "me": sum(ln["key"] == "you" for ln in lines), "zoe": sum(ln["key"] == "zoe" for ln in lines), "named": sum(ln["key"].startswith("p:") for ln in lines)}


async def run_night(*, budget_s: float, runner: Optional[Callable[..., int]] = None, now: Optional[datetime] = None, dry_run: bool = False,
                    clock: Callable[[], float] = time.time, rehearse: bool = False, recording_id: str = "",
                    mem_available: Callable[[], float] = mem_available_mb) -> dict:
    """The night job. Plans the engines against ``budget_s``, runs them as SEPARATE worker processes (each GPU model loads alone after a compaction), fuses their chunk texts with ROVER, applies
    each finished recording, and updates the contender ledger. Returns a counts-only summary (the CLI prints it). ``runner(manifest, out, timeout_s, mem_max=, threads=) -> rc`` is injectable.

    PRODUCTION every night: Qwen3-ASR (pivot, all audio) -> Parakeet (all audio) -> a third voter (distil-large-v3, where time allows); ROVER over the three. A night that is over budget
    keeps that order and carries the rest. EVALUATION: only when production finished with time to spare - Whisper large-v3 and Granite on a rotating SAMPLE of the day's speech
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
            except Exception:  # noqa: BLE001 - pre-0072
                pass
    if not rows:
        return {**res, "status": "idle", "pending": 0}
    audio_s = sum(float(r["duration_s"] or 0) for r in rows)
    st = read_state()
    pref = asr_backend()
    if pref not in BACKENDS or diar_backend() not in DIAR_BACKENDS:
        return {**res, "status": "bad_backend", "asr": pref, "diar": diar_backend(), "known": sorted(BACKENDS)}
    plan = plan_model(audio_s, budget_s, st.get("rtf"), pref)
    primary = plan["model"]
    rt = {**DEFAULT_RTF, **(st.get("rtf") or {})}
    multi = primary == eu.PIVOT
    second = second_backend() if multi else ""
    third = ""
    if second and audio_s * (rt[primary] + rt[second]) > budget_s * PLAN_MARGIN:      # production first: the pivot alone this night, the rest carries
        second = ""
    if second:                                                                       # the best third voter that the night has room for (large-v3 when it fits, else distil)
        third = next((c for c in third_choices() if audio_s * (rt[primary] + rt[second] + rt.get(c, 1.0)) <= budget_s * PLAN_MARGIN), "")
    evals = [e for e in eval_backends() if e not in (primary, second, third)] if multi and second else []
    voters = [primary] + [x for x in (second, third) if x]
    res.update(pending=len(rows), audio_s=round(audio_s), model=primary, est_s=plan["est_s"], fits=plan["fits"], fallback_model=plan["fallback"], engines=voters, eval_engines=evals,
               est_voters_s=round(audio_s * sum(rt[v] for v in voters)))
    if dry_run:
        return {**res, "status": "dry_run"}
    threads = env_int("ZOE_OMI_REPASS_THREADS", 4)
    zoe_ref = st.get("zoe_ref")
    items, names_by_user = [], {}
    async with get_compat_db() as db:
        for r in rows:
            if r["user_id"] not in names_by_user:
                names_by_user[r["user_id"]] = await household_names(db, r["user_id"])
            items.append({"id": r["id"], "path": r["audio_path"], "hint": name_hint(names_by_user[r["user_id"]])})
    work = home_dir() / "work"
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    deadline = started + max(30.0, budget_s - 45.0)               # 45 s kept back for the DB writes and the release of audio
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
        man.write_text(json.dumps({"asr": engine, "diar": diar, "threads": threads, "gpu": env_str("ZOE_OMI_REPASS_GPU", "1") != "0", "models_dir": models_dir(),
                                   "chunk_s": env_float("ZOE_OMI_REPASS_CHUNK_S", 14.0), "deadline_epoch": dl, "ct2_dir": ct2_dir() if engine.startswith("whisper-gpu") else "",
                                   "rtf": rt.get(engine, 1.0), "items": its, "zoe_clips": [] if (zoe_ref or not clips) else zoe_clips()}))
        os.chmod(man, 0o600)
        try:
            rc = (runner or spawn_worker)(man, out, max(60.0, dl - clock() + 30.0), mem_max=scope_mem_max(engine, diar != "none"), threads=threads)
            got, done, zref = read_results(out)
        finally:
            for p in (man, out):
                try:
                    p.unlink()
                except OSError:
                    pass
        if rc == 3 and not got:
            skipped[engine] = "model_unavailable"
        return {"rc": rc, "got": got, "done": done, "zref": zref}

    # ── pass 1: the pivot, on all the audio, with the speaker windows ──
    p1 = await one_pass(primary, items, diar=diar_backend(), dl=deadline - (_reserve_s(audio_s, rt, second, load_s=40.0) if second else 0.0), clips=True)
    if p1.get("skipped"):
        return {**res, "status": "low_mem" if p1["skipped"].startswith("low_mem") else "model_missing", "why": p1["skipped"], "have_mb": round(mem_available()), "need_mb": need_mb(primary)}
    got1, done1, rc = p1["got"], p1["done"], p1["rc"]
    if p1.get("zref") and not zoe_ref:
        st["zoe_ref"] = zoe_ref = p1["zref"]
    res["aborted"] = done1.get("aborted") or None
    ok1 = [it for it in items if (got1.get(it["id"]) or {}).get("ok")]
    chunked = [it for it in ok1 if isinstance(got1[it["id"]].get("chunks"), list)]
    by_id = {r["id"]: r for r in rows}

    async def voter_pass(engine: str) -> dict:
        its = [{"id": it["id"], "path": it["path"], "chunks": [[c[0], c[1]] for c in got1[it["id"]]["chunks"]]} for it in chunked]
        if not its or deadline - clock() < 60:
            return {"got": {}, "done": {}, "rc": None}
        return await one_pass(engine, its, diar="none", dl=deadline)

    # ── passes 2 and 3: the other production voters (pivot's chunks), in value-per-minute order ──
    vgot: dict = {}
    for eng in [x for x in (second, third) if x]:
        pv = await voter_pass(eng)
        vgot[eng] = {k: v for k, v in pv["got"].items() if v.get("ok") and len(v.get("chunks") or []) == len(got1[k]["chunks"])}
    complete = bool(chunked) and all(all(it["id"] in vgot.get(e, {}) for it in chunked) for e in vgot) and len(vgot) == len([x for x in (second, third) if x])

    # ── fusion: ROVER per chunk over the voters that finished that recording ──
    fusion = env_str("ZOE_OMI_REPASS_FUSION", "rover")
    chunk_rows: dict = {}                   # rid -> [row dicts]
    fused_res: dict = {}
    for it in ok1:
        rid, o = it["id"], got1[it["id"]]
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
        async with get_compat_db() as db:
            refs = await load_refs(db, rows[0]["user_id"], zoe_ref)
        pv = await preview_one(rows[0], o, refs=refs, names=names_by_user.get(rows[0]["user_id"], []))
        return {**res, "status": "rehearsal_ok", **pv, "worker_rc": rc, "device": done1.get("device"), "worker_rtf": done1.get("rtf"), "peak_rss_mb": done1.get("peak_rss_mb"),
                "gpu_peak_mb": done1.get("gpu_peak_mb"), "load_s": done1.get("load_s"), "wall_s": round(clock() - started)}
    tally = {"replaced": 0, "shadow": 0, "erased": 0, "gone": 0, "failed": 0, "carried": 0, "agree_sum": 0.0, "me": 0, "named": 0, "zoe": 0, "proposed": 0}
    drop_rids: set = set()                  # recordings whose hypotheses are NOT kept: erased mid-job, deleted meanwhile, or never finished
    for rid, r in by_id.items():
        o = fused_res.get(rid)
        raw = got1.get(rid)
        if o is None:
            carry = (raw is None and rc in (0, 124)) or (raw is not None and raw.get("why") in ("deadline", "backend_failed"))
            await note_failure(rid, carry=carry)
            tally["carried" if carry else "failed"] += 1
            continue
        async with get_compat_db() as db:
            refs = await load_refs(db, r["user_id"], zoe_ref)
        c = await finish_one(r, o, refs=refs, names=names_by_user.get(r["user_id"], []), model_label=model_label, seen=seen, m=m, now=now)
        s = c["status"]
        tally[s] = tally.get(s, 0) + 1
        if s in ("replaced", "shadow"):
            tally["agree_sum"] += float(c.get("agree") or 0.0)
            for k in ("me", "named", "zoe", "proposed"):
                tally[k] += int(c.get(k) or 0)
        else:
            drop_rids.add(rid)
    done_n = tally["replaced"] + tally["shadow"]
    night = (now or _now()).date().isoformat()
    try:                                                         # the evaluation record: counts in the ledger, the hypotheses (words, private, 7 days) in the hyps store
        home = home_dir()
        stored = [{k: v for k, v in r.items() if k != "_fz"} for rid, rws in chunk_rows.items() if rid not in drop_rids for r in rws]
        if cl:
            eu.append_ledger(home, night, contrib, evals)
            eu.store_hyps(home, night, stored)
            eu.prune_hyps(home, (now or _now()).date())
        if (now or _now()).weekday() == 0:                       # Monday: the weekly verdict line for the morning report
            res["contenders"] = eu.contender_line(eu.read_ledger(home))
    except OSError as exc:
        logger.warning("OMI_REPASS_LEDGER_NOT_WRITTEN reason=%s", type(exc).__name__)
    if done1.get("rtf"):
        st.setdefault("rtf", {})[primary] = done1["rtf"]
    st["last"] = {"at": _iso(now or _now()), "model": primary, "engines": voters, "files": done_n, "device": done1.get("device")}
    write_state(st)
    res.update(done=done_n, replaced=tally["replaced"], shadow=tally["shadow"], carried=tally["carried"], failed=tally["failed"], erased=tally["erased"],
               agree=round(tally["agree_sum"] / done_n, 3) if done_n else None, me_lines=tally["me"], named_lines=tally["named"], zoe_lines=tally["zoe"],
               proposed=tally["proposed"], worker_rc=rc, device=done1.get("device"), worker_rtf=done1.get("rtf"), peak_rss_mb=done1.get("peak_rss_mb"),
               wall_s=round(clock() - started))
    return res


def _reserve_s(audio_s: float, rt: dict, second: str, load_s: float = 40.0) -> float:
    """Seconds the pivot pass leaves for the second production voter (Parakeet is ~20 % of Qwen3's cost): its estimate plus a model load. Production first: the pivot never eats the voter's time."""
    return (audio_s * rt.get(second, 0.1) + load_s) if second else 0.0
