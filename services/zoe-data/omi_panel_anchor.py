"""PANEL ANCHORS for the pendant's "who said it": what the wall panel KNOWS about a stretch of time, used as evidence stronger than a voice match.

Two anchors (owner, 2026-11-09 - a guest's line TO Zoe ("Well, Zoe, I've got some issues") was labelled Zoe because the seam returned nothing and the
Zoe voice print decided):

1. **Zoe's own speech.** A pendant turn is Zoe ONLY if its text matches something Zoe actually said, time-aligned within the clock jitter. A turn that
   NAMES Zoe ("Zoe, ...") is never Zoe. Zoe's text + times come from the Flue session store (``assistant_text_*`` events).
2. **Panel utterances from known speakers.** Each panel voice turn has STT text (the Flue ``user_message``), a per-clip speaker verdict (the panel
   ``voice.log`` speaker lines) and a saved clip. A time- and text-aligned pendant turn inherits that speaker as a strong anchor (above the voice
   match) unless the verdict was unknown. The per-CLIP verdict is trusted, not the session's: a guest's session can hold the owner's clip.

The panel clock and the pendant clock differ (~88 s on one Sunday, jitter -8..+12 s): ``estimate_offset`` takes per-clip shifts and returns a robust
per-night offset; ``correlate_shift`` finds one shift by log-mel pattern correlation of a panel clip against the pendant audio (the lab method,
``~/.zoe/lab/omi-people-tagging``). ``AlignRule`` holds every tunable (jitter, text-match floor, minimum words) so a measured rule plugs in without
touching the callers; env ``ZOE_OMI_ANCHOR_*`` overrides.

Pure functions first (tests need no store); the readers at the bottom are best effort and every failure returns "no evidence" - never a guess.
Household speech never leaves this module's return values: nothing here logs text.
"""
from __future__ import annotations

import bisect
import datetime as dt
import difflib
import json
import os
import re
import sqlite3
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

FLUE_DB = os.path.expanduser("~/assistant/labs/flue-zoe-brain-2x/data/zoe-brain.db")
_WORD = re.compile(r"[a-z0-9']+")
_ZOE = re.compile(r"\bzoe\b", re.I)


def _env_f(name: str, default: float) -> float:
    from typed_env import env_float
    return env_float(name, default)


@dataclass(frozen=True)
class AlignRule:
    """The alignment rule. Defaults are the coordinator's first numbers (jitter ~+-15 s after the offset); replace them with the measured rule here."""
    jitter_s: float = 30.0            # a pendant turn may sit this far outside the panel event's own span, after the per-night offset (Sunday: 75-95 s, drifting)
    tts_lag_s: float = 10.0           # Zoe's words are logged when GENERATED; the speaker plays them up to this much later (and for ~words/2.4 s)
    min_match: float = 0.6            # a PERSON anchor: share of the pendant turn's words found in the panel utterance (panel STT is noisy: similarity, not equality)
    min_words: int = 3                # a person anchor needs at least this many words
    zoe_min_words: int = 6            # a turn is ZOE only with at least this many words ...
    zoe_min_cover: float = 0.85       # ... and at least this share of them in one Zoe reply (measured on Sunday: Zoe turns 0.94-1.00, any other turn of >4 words < 0.6)
    max_offset_spread_s: float = 12.0   # per-clip shifts that disagree by more than this give no night offset
    min_offset_clips: int = 2

    @classmethod
    def current(cls) -> "AlignRule":
        d = cls()
        return cls(jitter_s=_env_f("ZOE_OMI_ANCHOR_JITTER_S", d.jitter_s), tts_lag_s=_env_f("ZOE_OMI_ANCHOR_TTS_LAG_S", d.tts_lag_s),
                   min_match=_env_f("ZOE_OMI_ANCHOR_MIN_MATCH", d.min_match), min_words=int(_env_f("ZOE_OMI_ANCHOR_MIN_WORDS", d.min_words)),
                   zoe_min_words=int(_env_f("ZOE_OMI_ANCHOR_ZOE_MIN_WORDS", d.zoe_min_words)), zoe_min_cover=_env_f("ZOE_OMI_ANCHOR_ZOE_MIN_COVER", d.zoe_min_cover),
                   max_offset_spread_s=_env_f("ZOE_OMI_ANCHOR_MAX_SPREAD_S", d.max_offset_spread_s),
                   min_offset_clips=int(_env_f("ZOE_OMI_ANCHOR_MIN_CLIPS", d.min_offset_clips)))


@dataclass
class Said:
    """One thing the panel side heard or said. Times are epoch seconds on the PANEL side clock; ``kind`` is ``zoe`` or ``person``.
    For a person: ``speaker`` = ``member`` | ``guest`` | ``unknown``, ``person`` = the resolved key ('you' | 'p:<people id>') or None."""
    kind: str
    t0: float
    t1: float
    text: str
    speaker: str = "unknown"
    person: Optional[str] = None
    clip: Optional[str] = None


@dataclass
class Evidence:
    """What the panel knows about one recording: its sayings, the night's offset (panel_time = pendant_time + offset_s) and how many clips fixed it."""
    said: list = field(default_factory=list)
    offset_s: float = 0.0
    offset_clips: int = 0
    covered: bool = False             # the panel log covers this recording's span: Zoe is then decided by the log, not by her voice print


# ── text ─────────────────────────────────────────────────────────────────────────────────────────

_NUMS = (("twenty twenty one", "2021"), ("twenty twenty", "2020"), ("twenty", "20"), ("thirty", "30"), ("forty", "40"), ("fifty", "50"), ("sixty", "60"),
         ("seventy", "70"), ("eighty", "80"), ("ninety", "90"), ("hundred", "100"), ("eighteen", "18"), ("nineteen", "19"), ("seventeen", "17"), ("sixteen", "16"),
         ("fifteen", "15"), ("fourteen", "14"), ("thirteen", "13"), ("twelve", "12"), ("eleven", "11"), ("ten", "10"), ("nine", "9"), ("eight", "8"),
         ("seven", "7"), ("six", "6"), ("five", "5"), ("four", "4"), ("three", "3"), ("two", "2"), ("one", "1"), ("zero", "0"))
_SENT = re.compile(r"(?<=[.!?])\s+")


def words_of(text: str) -> list:
    """Lowercase, punctuation stripped, spoken numbers as digits ("twenty one" and "21" are one word)."""
    t = " " + " ".join(_WORD.findall((text or "").lower().replace("-", " "))) + " "
    for k, v in _NUMS:
        t = re.sub(rf"(?<=\s){k}(?=\s)", v, t)
    t = re.sub(r"(?<=\s)([2-9]0) ([1-9])(?=\s)", lambda m: str(int(m.group(1)) + int(m.group(2))), t)    # "20 1" -> 21
    return t.split()


def names_zoe(text: str) -> bool:
    """A turn that says her name is addressing her: never Zoe herself."""
    return bool(_ZOE.search(text or ""))


def cover(turn: str, said: str) -> tuple:
    """(share of the TURN's words that appear in ``said``, how many): the measured similarity (set membership, tolerant of the panel STT's noise)."""
    a = words_of(turn)
    b = set(words_of(said))
    n = sum(w in b for w in a)
    return (n / len(a) if a else 0.0), n


def sentences(text: str) -> list:
    """Split at sentence boundaries: a mixed turn (Zoe's greeting + a guest's "Well, Zoe, I've got some issues") is tested sentence by sentence."""
    return [x for x in _SENT.split((text or "").strip()) if x]


# ── offset ───────────────────────────────────────────────────────────────────────────────────────

def estimate_offset(shifts: Iterable, rule: Optional[AlignRule] = None) -> Optional[tuple]:
    """(offset_s, clips used) from per-clip shifts (panel_time - pendant_time, seconds), or None. A median with outliers (> max_spread from it) dropped;
    fewer than ``min_offset_clips`` survivors, or survivors that still spread wider than ``max_offset_spread_s``, give None (no anchors rather than wrong ones)."""
    rule = rule or AlignRule.current()
    xs = sorted(float(s) for s in shifts if s is not None)
    if len(xs) < rule.min_offset_clips:
        return None
    med = statistics.median(xs)
    keep = [x for x in xs if abs(x - med) <= rule.max_offset_spread_s]
    if len(keep) < rule.min_offset_clips or (max(keep) - min(keep)) > rule.max_offset_spread_s:
        return None
    return round(statistics.median(keep), 3), len(keep)


def _logmel(x, sr: int, n: int = 32, lo: float = 100.0, hi: float = 4000.0, nfft: int = 512):
    import numpy as np
    mel = lambda f: 1127 * np.log(1 + f / 700)
    imel = lambda m: 700 * (np.exp(m / 1127) - 1)
    pts = imel(np.linspace(mel(lo), mel(hi), n + 2))
    fq = np.fft.rfftfreq(nfft, 1 / sr)
    fb = np.zeros((n, len(fq)))
    for i in range(n):
        a, b, c = pts[i], pts[i + 1], pts[i + 2]
        fb[i] = np.maximum(0, np.minimum((fq - a) / (b - a), (c - fq) / (c - b)))
    hop, win = sr // 100, int(sr * 0.025)
    k = 1 + (len(x) - win) // hop
    if k < 1:
        return None
    idx = np.arange(win)[None, :] + hop * np.arange(k)[:, None]
    sp = np.abs(np.fft.rfft(x[idx] * np.hanning(win), nfft, axis=1)) ** 2
    return np.log(sp @ fb.T + 1e-9)               # (frames @100/s, bands)


def correlate_shift(pendant, clip, sr: int, *, predicted_s: float, search_s: float = 60.0, min_z: float = 15.0) -> Optional[tuple]:
    """Find where ``clip`` (a panel clip, float mono at ``sr``) sits in ``pendant`` (the recording's audio) near ``predicted_s`` (seconds into the recording)
    by log-mel normalised cross-correlation. -> (start_s, z) when the peak stands ``min_z`` robust deviations above the search window, else None."""
    import numpy as np
    P, C = _logmel(np.asarray(pendant, dtype=float), sr), _logmel(np.asarray(clip, dtype=float), sr)
    if P is None or C is None or len(C) < 100:
        return None
    P, C = P - P.mean(0), C - C.mean(0)
    a0 = int(round(predicted_s * 100))
    lo, hi = max(0, a0 - int(search_s * 100)), min(len(P) - len(C), a0 + int(search_s * 100))
    if hi <= lo:
        return None
    seg, T = P[lo:hi + len(C)], len(C)
    nfft = 1 << int(np.ceil(np.log2(len(seg) + T)))
    num_total = 0.0
    cs_total = np.zeros(len(seg) - T + 1)
    for b in range(seg.shape[1]):
        p, c = seg[:, b], C[:, b]
        num = np.fft.irfft(np.fft.rfft(p, nfft) * np.conj(np.fft.rfft(c, nfft)), nfft)[:len(seg) - T + 1]
        cp = np.concatenate([[0], np.cumsum(p)])
        cp2 = np.concatenate([[0], np.cumsum(p * p)])
        s = cp[T:] - cp[:-T]
        s2 = cp2[T:] - cp2[:-T]
        var = np.maximum(s2 - s * s / T, 1e-9)
        cs_total += num / (np.sqrt(var) * (np.linalg.norm(c) + 1e-9))
    r = cs_total / seg.shape[1]
    k = int(np.argmax(r))
    mad = float(np.median(np.abs(r - np.median(r)))) * 1.4826 + 1e-9
    z = float((r[k] - np.median(r)) / mad)
    return ((lo + k) / 100.0, z) if z >= min_z else None


# ── the two anchors ──────────────────────────────────────────────────────────────────────────────

def _seg_window(seg: dict, rec_start: float, offset_s: float) -> tuple:
    return rec_start + offset_s + int(seg["start_ms"]) / 1000.0, rec_start + offset_s + int(seg["end_ms"]) / 1000.0


def _near(w0: float, w1: float, s0: float, s1: float, jitter: float) -> bool:
    return w1 >= s0 - jitter and w0 <= s1 + jitter


def _span_of(s: Said, rule: AlignRule) -> tuple:
    """The window a saying may have been HEARD in: Zoe's from her first generated word to the end of her speech (words/2.4 s + the playback lag);
    a person's is the clip's own span."""
    if s.kind != "zoe":
        return s.t0, s.t1
    return s.t0, max(s.t1, s.t0 + len(words_of(s.text)) / 2.4) + rule.tts_lag_s


def zoe_spans(segs: list, ev: Evidence, rec_start: float, rule: Optional[AlignRule] = None) -> list:
    """(start_ms, end_ms) of the stretches that ARE Zoe. A segment is split into sentences (its time shared by word count) and each sentence is tested on
    its own: >= ``zoe_min_words`` words, >= ``zoe_min_cover`` of them in ONE Zoe reply heard inside the jitter, and never one that names her. Anything that
    fails is not Zoe - the print never overrules this."""
    rule = rule or AlignRule.current()
    zs = [s for s in ev.said if s.kind == "zoe"]
    out = []
    for g in segs:
        if not zs:
            break
        sents = sentences(g.get("text") or "")
        counts = [max(1, len(words_of(x))) for x in sents]
        total, a0, dur, done = sum(counts) or 1, int(g["start_ms"]), int(g["end_ms"]) - int(g["start_ms"]), 0
        for x, c in zip(sents, counts):
            s0, s1 = a0 + int(dur * done / total), a0 + int(dur * (done + c) / total)
            done += c
            if names_zoe(x) or len(words_of(x)) < rule.zoe_min_words:
                continue
            w0, w1 = _seg_window({"start_ms": s0, "end_ms": s1}, rec_start, ev.offset_s)
            for s in zs:
                a, b = _span_of(s, rule)
                if _near(w0, w1, a, b, rule.jitter_s) and cover(x, s.text)[0] >= rule.zoe_min_cover:
                    out.append((s0, s1))
                    break
    return out


def person_anchors(segs: list, ev: Evidence, rec_start: float, rule: Optional[AlignRule] = None) -> list:
    """Anchors from panel utterances: [{start_ms, end_ms, key, speaker}] for each segment aligned in time AND text to a panel turn whose per-clip verdict
    named someone (``person`` resolved; ``unknown`` never anchors). A segment that names Zoe is still a person's line, so it can anchor; the best
    text match wins when several panel turns are in range."""
    rule = rule or AlignRule.current()
    ps = [s for s in ev.said if s.kind == "person" and s.speaker != "unknown" and s.person]
    out = []
    for g in segs:
        text = g.get("text") or ""
        if not ps or len(words_of(text)) < rule.min_words:
            continue
        w0, w1 = _seg_window(g, rec_start, ev.offset_s)
        best = None
        for s in ps:
            if not _near(w0, w1, s.t0, s.t1, rule.jitter_s):
                continue
            share, m = cover(text, s.text)
            if share >= rule.min_match and len(words_of(text)) >= rule.min_words and (best is None or share > best[0]):
                best = (share, s)
        if best:
            out.append({"start_ms": int(g["start_ms"]), "end_ms": int(g["end_ms"]), "key": best[1].person, "speaker": best[1].speaker})
    return out


# ── readers (best effort: every failure is "no evidence") ─────────────────────────────────────────

def _epoch(ts: str) -> Optional[float]:
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def flue_said(t0: float, t1: float, *, db_path: Optional[str] = None) -> Optional[list]:
    """Zoe's replies (``assistant_text_delta`` events joined per block) and the panel turns' STT text (``user_message``) between two epoch times, from the
    Flue session store, read-only. None when the store is unreadable; [] when nothing was said. Person sayings come back with ``speaker='unknown'``
    until ``join_verdicts`` attaches the panel's per-clip verdict."""
    from typed_env import env_str
    path = db_path or env_str("ZOE_OMI_ANCHOR_FLUE_DB", "") or FLUE_DB
    if not os.path.exists(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    blocks: dict = {}
    out: list = []
    try:
        lo, hi = t0 - 120, t1 + 120
        for (path_, seq, data) in con.execute("SELECT path, seq, data FROM flue_conversation_stream_batches ORDER BY rowid DESC LIMIT 400000"):
            try:
                evs = json.loads(data)
                if isinstance(evs, dict) and "$flueChunkCount" in evs:
                    # a large batch is stored as a sentinel + rows in the chunk table (the same representation self_review.flue_replies rebuilds)
                    parts = con.execute("SELECT data FROM flue_conversation_stream_batch_chunks WHERE path = ? AND seq = ? ORDER BY chunk_index",
                                        (path_, seq)).fetchall()
                    evs = json.loads("".join(p[0] for p in parts))
            except (ValueError, sqlite3.Error):
                continue
            for e in evs if isinstance(evs, list) else [evs]:
                if not isinstance(e, dict):
                    continue
                t = _epoch(e.get("timestamp"))
                if t is None or not (lo <= t <= hi):
                    continue
                kind = e.get("type")
                if kind == "assistant_text_delta":
                    b = blocks.setdefault((e.get("messageId"), e.get("blockId")), {"t0": t, "t1": t, "parts": []})
                    b["t0"], b["t1"] = min(b["t0"], t), max(b["t1"], t)
                    b["parts"].append((int(e.get("sequence") or 0), str(e.get("delta") or "")))
                elif kind == "user_message":
                    c = e.get("content")
                    txt = c if isinstance(c, str) else " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict)) if isinstance(c, list) else ""
                    if txt.strip():
                        out.append(Said("person", t, t, txt.strip()))
    except sqlite3.Error:
        return None
    finally:
        con.close()
    for b in blocks.values():
        txt = "".join(p for _, p in sorted(b["parts"])).strip()
        if txt:
            out.append(Said("zoe", b["t0"], b["t1"], txt))
    return sorted(out, key=lambda s: s.t0)


def join_verdicts(person_said: list, turns: list, resolve: Callable[[Any], tuple], *, window_s: float = 45.0) -> list:
    """Attach each panel turn's PER-CLIP verdict to the STT text of that turn. ``turns`` are ``panel_voice_log.Turn`` rows (``recorded_ts`` panel time,
    ``duration_s``, ``claim_user``/``claim_score``); ``resolve(turn)`` -> (speaker kind 'member'|'guest'|'unknown', person key or None). The turn nearest the
    text in time wins; the sayings get the PANEL's clock (the turn's span) so they align with the pendant through the panel offset. Unmatched texts are
    dropped - an unlocated utterance is no anchor."""
    stamps = [(t.recorded_ts.timestamp(), t) for t in turns if getattr(t, "recorded_ts", None) is not None]
    stamps.sort(key=lambda x: x[0])
    keys = [s[0] for s in stamps]
    out = []
    for s in person_said:
        i = bisect.bisect_left(keys, s.t0 - window_s)
        best = None
        while i < len(stamps) and stamps[i][0] <= s.t0 + window_s:
            gap = abs(stamps[i][0] - s.t0)
            if best is None or gap < best[0]:
                best = (gap, stamps[i][1])
            i += 1
        if not best:
            continue
        t = best[1]
        kind, key = resolve(t)
        a = t.recorded_ts.timestamp()
        out.append(Said("person", a, a + float(t.duration_s or 0.0), s.text, speaker=kind if key else "unknown", person=key if kind != "unknown" else None,
                        clip=getattr(t, "clip", None)))
    return out
