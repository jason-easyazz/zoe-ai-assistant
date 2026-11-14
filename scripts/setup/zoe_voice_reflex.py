"""The panel's "I heard you" reflex: the pure decision logic (stdlib only, so it imports on the Pi with nothing from zoe-data).

At the moment the daemon commits the close of a recording it may play ONE soft, non-lexical take ("Mm.", "Mm-hm.") in Zoe's own
Kokoro voice, from a pre-rendered bank. This module decides whether it may, which take, what the turn request tells the server,
and how long the reply's first PCM must wait so the two never overlap. It plays nothing and touches no device; the daemon owns
the player (UNREGISTERED: never ``_register_tts_process``, or the barge monitor's grace expires before the reply's onset).

Truthfulness (design note W2 sections 0, 3; ``docs/knowledge/voice-reflex.md``):

* never before the commit: the close was decided, so "I heard sound that stopped" is true;
* non-lexical only: the bank holds "mm" takes, nothing that claims understanding or agreement;
* one voice: the bank's manifest voice must equal the household's current voice, else silence (never the wrong voice);
* silent when in doubt: every rule below that fails is ONE named skip reason from the fixed ``SKIPS`` enum.

Logs and the handshake carry integers and fixed enums only, never text or audio.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import random
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

# ── constants (S1, design note section 7); each overridable by env, clamped ─────────────────────────────────────────────────
REFLEX_ENABLED = False          # master switch (env REFLEX_ENABLED), default OFF
REFLEX_MIN_SPEECH_MS = 480      # clears a wake-word-only turn and "stop"/"yes"-length turns
REFLEX_MAX_MS = 600             # the longest take; bounds the worst delay of a fast canned reply
REFLEX_SEAM_MS = 120            # silence kept between the reflex's end and the reply's first PCM
REFLEX_MIN_GAP_S = 30           # at most one reflex per this many seconds (keeps it off tic territory)
REFLEX_IDLE_GREET_H = 4.0       # first turn after this many idle hours: a greeting is likely, stay silent
REFLEX_NIGHT_START = 22         # silent from 22:00 ...
REFLEX_NIGHT_END = 7            # ... to 07:00 (env REFLEX_NIGHT="22-07")
REFLEX_GAIN_DB = -3.0           # a take's active-speech RMS relative to the reference reply speech
REFLEX_NOISE_FLOOR_DBFS = -40.0  # pre-roll RMS above this = music / TV in the room; PROVISIONAL until S0 data sets it
REFLEX_CHAIN_ENABLED = False    # S4 (later): follow-up / conversation turns under the chain rule
REFLEX_CHAIN_EVERY = 3          # chain rule: at most one reflex per this many turns of a chain
REFLEX_DIR = "~/.zoe-voice/reflex"

#: fixed bank format: the format of the server's reply chunks (kokoro_sidecar ``_SAMPLE_RATE``), so there is no resampling seam
BANK_RATE = 24000
BANK_CHANNELS = 1
BANK_WIDTH = 2
BANK_MIN_TAKES = 3              # "avoid the last 2" needs a third take to choose
_MAX_TAKE_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024

# ── fixed enums ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
KINDS = ("heard", "none")
RULES = ("deep", "quiet", "clean", "hesitation", "no_speech", "max_duration")
TURN_KINDS = ("wake", "followup", "conversation", "barge")
SKIPS = ("short", "capped", "muted", "busy", "noisy", "night", "idle_greeting", "budget", "no_bank", "voice_mismatch",
         "not_wake", "disabled", "prev_question")
LOAD_RESULTS = ("ok", "missing", "bad_manifest", "voice_mismatch", "sha_mismatch", "bad_format", "too_long", "too_few")
NO_SKIP = "-"

TAKE_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
BANK_SHA_RE = re.compile(r"^[0-9a-f]{8}$")
VOICE_RE = re.compile(r"^[a-z0-9_]{2,64}$")     # the sidecar's _VOICE_NAME_RE shape

# ── the daemon's trim rule (zoe_voice_daemon._trim_chunk_silence), as ONE pure function ──────────────────────────────────────
#: pinned against the daemon by tests/unit/test_voice_reflex_bank_render.py (constants and output on synthetic PCM)
TRIM_KEEP_TAIL_MS = 130         # daemon _TTS_KEEP_TAIL_MS (env ZOE_TTS_KEEP_TAIL_MS)
TRIM_LEAD_GUARD_MS = 20         # daemon _TTS_LEAD_GUARD_MS (env ZOE_TTS_LEAD_GUARD_MS)
TRIM_PEAK_FRACTION = 0.02       # 2% of the chunk's peak ...
TRIM_FLOOR = 96                 # ... with this floor


def _abs16(v: int) -> int:
    # numpy's abs on int16 wraps -32768 to -32768; the daemon's rule inherits that, so this does too (byte-identical output)
    return -32768 if v == -32768 else abs(v)


def trim_silence(pcm: bytes, rate: int, ch: int, width: int, *, keep_tail_ms: int = TRIM_KEEP_TAIL_MS,
                 lead_guard_ms: int = TRIM_LEAD_GUARD_MS) -> bytes:
    """Trim leading/trailing near-silence from 16-bit PCM, keeping a ``lead_guard_ms`` lead and a ``keep_tail_ms`` tail: the
    daemon's rule, byte for byte. Non-16-bit, empty, all-silent or malformed input passes through untouched."""
    if width != 2 or not pcm:
        return pcm
    try:
        a = array.array("h")
        a.frombytes(pcm)
        ch = max(1, int(ch))
        if len(a) % ch:
            return pcm
        n = len(a) // ch
        env = [max(_abs16(a[i * ch + c]) for c in range(ch)) for i in range(n)] if ch > 1 else [_abs16(v) for v in a]
        if not env:
            return pcm
        peak = max(env)
        if peak == 0:
            return pcm
        thr = max(int(peak * TRIM_PEAK_FRACTION), TRIM_FLOOR)
        first = next((i for i, v in enumerate(env) if v > thr), None)
        if first is None:
            return pcm
        last = next(i for i in range(n - 1, -1, -1) if env[i] > thr)
        start = max(0, first - int(rate * lead_guard_ms / 1000))
        end = min(n, last + 1 + int(rate * keep_tail_ms / 1000))
        return a[start * ch:end * ch].tobytes()
    except (ValueError, TypeError, OverflowError):
        return pcm


def active_rms_db(pcm: bytes) -> float:
    """Active-speech RMS of mono S16 PCM in dBFS: samples above the trim threshold (2% of peak, floor 96). -120.0 for silence."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not a:
        return -120.0
    peak = max(abs(int(v)) for v in a)
    thr = max(int(peak * TRIM_PEAK_FRACTION), TRIM_FLOOR)
    act = [int(v) for v in a if abs(int(v)) > thr]
    if not act:
        return -120.0
    rms = math.sqrt(sum(v * v for v in act) / len(act))
    return 20.0 * math.log10(max(rms, 1e-9) / 32768.0)


# ── settings ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_num(env: Mapping[str, str], name: str, default: float, lo: float, hi: float) -> float:
    """A finite number clamped to [lo, hi]; missing, malformed or non-finite keeps the default."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    if not math.isfinite(v):
        return default
    return min(hi, max(lo, v))


def _env_night(env: Mapping[str, str]) -> "tuple[int, int]":
    m = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", env.get("REFLEX_NIGHT") or "")
    if not m:
        return REFLEX_NIGHT_START, REFLEX_NIGHT_END
    s, e = int(m.group(1)), int(m.group(2))
    if s > 23 or e > 23:
        return REFLEX_NIGHT_START, REFLEX_NIGHT_END
    return s, e


@dataclass(frozen=True)
class Settings:
    enabled: bool = REFLEX_ENABLED
    min_speech_ms: int = REFLEX_MIN_SPEECH_MS
    max_ms: int = REFLEX_MAX_MS
    seam_ms: int = REFLEX_SEAM_MS
    min_gap_s: float = REFLEX_MIN_GAP_S
    idle_greet_h: float = REFLEX_IDLE_GREET_H
    night_start: int = REFLEX_NIGHT_START
    night_end: int = REFLEX_NIGHT_END
    gain_db: float = REFLEX_GAIN_DB
    noise_floor_dbfs: float = REFLEX_NOISE_FLOOR_DBFS
    chain_enabled: bool = REFLEX_CHAIN_ENABLED
    chain_every: int = REFLEX_CHAIN_EVERY
    dir: str = REFLEX_DIR


def settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """The reflex settings from ``env`` (default ``os.environ``), each clamped to a sane range."""
    env = os.environ if env is None else env
    ns, ne = _env_night(env)
    return Settings(
        enabled=_env_bool(env, "REFLEX_ENABLED", REFLEX_ENABLED),
        min_speech_ms=int(_env_num(env, "REFLEX_MIN_SPEECH_MS", REFLEX_MIN_SPEECH_MS, 240, 5000)),
        max_ms=int(_env_num(env, "REFLEX_MAX_MS", REFLEX_MAX_MS, 100, 1000)),
        seam_ms=int(_env_num(env, "REFLEX_SEAM_MS", REFLEX_SEAM_MS, 40, 500)),
        min_gap_s=_env_num(env, "REFLEX_MIN_GAP_S", REFLEX_MIN_GAP_S, 10, 3600),
        idle_greet_h=_env_num(env, "REFLEX_IDLE_GREET_H", REFLEX_IDLE_GREET_H, 0.5, 72),
        night_start=ns, night_end=ne,
        gain_db=_env_num(env, "REFLEX_GAIN_DB", REFLEX_GAIN_DB, -24, 0),
        noise_floor_dbfs=_env_num(env, "REFLEX_NOISE_FLOOR_DBFS", REFLEX_NOISE_FLOOR_DBFS, -90, 0),
        chain_enabled=_env_bool(env, "REFLEX_CHAIN_ENABLED", REFLEX_CHAIN_ENABLED),
        chain_every=int(_env_num(env, "REFLEX_CHAIN_EVERY", REFLEX_CHAIN_EVERY, 2, 20)),
        dir=(env.get("REFLEX_DIR") or "").strip() or REFLEX_DIR,
    )


# ── the decision ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Ctx:
    """What the daemon knows at the committed close: numbers and enums only."""
    rule: str                           # RULES: the endpoint rule (tail_rule, or max_duration when the cap stopped it)
    speech_ms: int                      # speech chunks x 80 ms
    muted_ms: int = 0                   # the Jabra's playback mute inside this recording
    turn_kind: str = "wake"             # TURN_KINDS
    capped: bool = False                # _last_record_capped: the hard cap closed mid-speech
    other_player: bool = False          # _active_playback() is not None (an announcement)
    noise_dbfs: float = -120.0          # pre-roll RMS
    local_hour: int = 12
    since_last_s: Optional[float] = None    # seconds since the last reflex (None = never)
    idle_h: float = 0.0                 # hours since the previous turn
    prev_question: bool = False         # the previous reply ended in a question (_prev_reply_asked)
    chain_turns_since: Optional[int] = None  # turns of this chain since the last reflex (None = none this chain)
    bank_voice: Optional[str] = None    # the loaded bank's manifest voice (None = no bank)
    current_voice: Optional[str] = None  # the household's current voice (cached from the done frame; None = unknown)
    takes: tuple = ()                   # the loaded bank's take ids


@dataclass(frozen=True)
class Decision:
    kind: str                   # "heard" | "none"
    take: Optional[str] = None
    skip: str = NO_SKIP         # SKIPS, or "-" when heard


def _none(skip: str) -> Decision:
    assert skip in SKIPS
    return Decision("none", None, skip)


def is_night(hour: int, s: Settings) -> bool:
    h = int(hour) % 24
    if s.night_start == s.night_end:
        return False
    if s.night_start > s.night_end:
        return h >= s.night_start or h < s.night_end
    return s.night_start <= h < s.night_end


def gate(ctx: Ctx, s: Optional[Settings] = None) -> Optional[str]:
    """The first silence rule ``ctx`` trips (a ``SKIPS`` member), or None when the reflex may play."""
    s = s or settings()
    if not s.enabled:
        return "disabled"
    window = ctx.turn_kind in ("followup", "conversation")
    if ctx.turn_kind != "wake" and not (window and s.chain_enabled):
        return "not_wake"               # S1: wake turns only; a barge turn never gets one
    if ctx.rule not in RULES or ctx.rule == "max_duration" or ctx.capped:
        return "capped"                 # an unknown rule is a close we cannot vouch for
    if ctx.rule == "no_speech" or int(ctx.speech_ms) < s.min_speech_ms:
        return "short"
    if int(ctx.muted_ms) > 0:
        return "muted"
    if ctx.other_player:
        return "busy"
    if not (ctx.noise_dbfs <= s.noise_floor_dbfs):     # NaN counts as noisy
        return "noisy"
    if is_night(ctx.local_hour, s):
        return "night"
    if ctx.idle_h >= s.idle_greet_h:
        return "idle_greeting"
    if window and ctx.prev_question:
        return "prev_question"
    if ctx.since_last_s is not None and not (ctx.since_last_s >= s.min_gap_s):
        return "budget"
    if window and ctx.chain_turns_since is not None and ctx.chain_turns_since < s.chain_every:
        return "budget"                 # chain rule: one per REFLEX_CHAIN_EVERY turns, never two consecutive turns
    if not ctx.bank_voice or len(ctx.takes) < BANK_MIN_TAKES:
        return "no_bank"
    if not ctx.current_voice or ctx.current_voice != ctx.bank_voice:
        return "voice_mismatch"         # unknown counts: never the wrong voice
    return None


def pick_take(takes, state: Optional[Mapping] = None, rng: Optional[random.Random] = None) -> Optional[str]:
    """One take that is neither of the last two played (fewer avoided only when the bank is too small; never the very last
    one unless it is the only take). Deterministic for a seeded ``rng``."""
    ids = sorted({t for t in takes if isinstance(t, str) and TAKE_ID_RE.fullmatch(t)})
    if not ids:
        return None
    recent = [r for r in (state or {}).get("recent") or [] if isinstance(r, str)][-2:]
    rng = rng or random.Random()
    for avoid in range(len(recent), -1, -1):
        banned = set(recent[len(recent) - avoid:]) if avoid else set()
        options = [t for t in ids if t not in banned]
        if options:
            return rng.choice(options)
    return ids[0]


def decide(ctx: Ctx, state: Optional[Mapping] = None, *, s: Optional[Settings] = None,
           seed: Optional[int] = None) -> Decision:
    """The reflex decision at the close. Pure: ``state`` is read, never written (``record`` writes it)."""
    s = s or settings()
    skip = gate(ctx, s)
    if skip:
        return _none(skip)
    take = pick_take(ctx.takes, state, random.Random(seed) if seed is not None else None)
    if take is None:
        return _none("no_bank")
    return Decision("heard", take, NO_SKIP)


def record(state: Optional[Mapping], decision: Decision, now_s: float) -> dict:
    """The new persisted state after ``decision``: a tiny dict {"recent": [<=2 take ids], "last_at": <seconds>}."""
    out = {"recent": [r for r in (state or {}).get("recent") or [] if isinstance(r, str)][-2:],
           "last_at": (state or {}).get("last_at")}
    if decision.kind == "heard" and decision.take:
        out["recent"] = (out["recent"] + [decision.take])[-2:]
        out["last_at"] = float(now_s)
    return out


def since_last(state: Optional[Mapping], now_s: float) -> Optional[float]:
    last = (state or {}).get("last_at")
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return None
    return max(0.0, float(now_s) - float(last))


# ── the handshake (the block on the /turn_stream payload) ──────────────────────────────────────────────────────────────────
def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(f):
        return lo
    return int(min(hi, max(lo, f)))


def handshake_block(decision: Decision, at_ms, dur_ms, bank_sha: Optional[str], voice: Optional[str]) -> dict:
    """The ``reflex`` block for the turn request. Never text, never audio: enums, a take id, a bank sha8, a voice id, two ints.
    A heard decision whose ids are not well formed is sent as ``none`` + ``no_bank`` (it cannot be vouched for)."""
    if decision.kind == "heard":
        ok = (isinstance(decision.take, str) and TAKE_ID_RE.fullmatch(decision.take)
              and isinstance(bank_sha, str) and BANK_SHA_RE.fullmatch(bank_sha)
              and isinstance(voice, str) and VOICE_RE.fullmatch(voice))
        if ok:
            return {"kind": "heard", "take": decision.take, "bank": bank_sha, "voice": voice,
                    "at_ms": _clamp_int(at_ms, 0, 10000), "dur_ms": _clamp_int(dur_ms, 0, 2000)}
        return {"kind": "none", "skip": "no_bank"}
    skip = decision.skip if decision.skip in SKIPS else "disabled"
    return {"kind": "none", "skip": skip}


# ── the ordering rule (H3): the reply's first PCM never overlaps the reflex ──────────────────────────────────────────────────
def ordering_wait_ms(reflex_started_at: Optional[float], reflex_dur_ms, now: float, s: Optional[Settings] = None) -> int:
    """How long the stream loop waits before its first server PCM write so the reply starts at least ``REFLEX_SEAM_MS`` after
    the reflex ends. 0 when no reflex played or it ended long ago. Bounded: the clip counts for at most ``REFLEX_MAX_MS``, so the
    wait is never more than ``REFLEX_MAX_MS + REFLEX_SEAM_MS``. Both players share the same onset latency, so it cancels."""
    s = s or settings()
    if reflex_started_at is None:
        return 0
    dur = _clamp_int(reflex_dur_ms, 0, s.max_ms)
    ready = float(reflex_started_at) + (dur + s.seam_ms) / 1000.0
    wait = (ready - float(now)) * 1000.0
    if not math.isfinite(wait) or wait <= 0:
        return 0
    return int(min(math.ceil(wait), s.max_ms + s.seam_ms))


# ── the bank ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def bank_sha8(voice: str, style_len, takes: "list[tuple[str, str]]") -> str:
    """The bank id: sha256 over the voice, the pinned style row and every (take id, file sha256), sorted. A render date or a
    tool version does not change it; any changed byte of audio does."""
    canon = json.dumps({"voice": voice, "style_len": style_len, "takes": sorted([list(t) for t in takes])},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Take:
    id: str
    dur_ms: int
    rms_db: float
    pcm: bytes = field(repr=False)


@dataclass(frozen=True)
class Bank:
    voice: str
    sha8: str
    style_len: Optional[int]
    takes: "dict[str, Take]"

    @property
    def take_ids(self) -> tuple:
        return tuple(sorted(self.takes))


@dataclass(frozen=True)
class LoadResult:
    bank: Optional[Bank]
    reason: str                 # LOAD_RESULTS


def _read_take(path: Path, max_ms: int) -> "tuple[Optional[bytes], str, int]":
    try:
        if not path.is_file() or path.stat().st_size > _MAX_TAKE_BYTES:
            return None, "bad_format", 0
        with wave.open(str(path), "rb") as wf:
            if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (BANK_CHANNELS, BANK_WIDTH, BANK_RATE):
                return None, "bad_format", 0
            n = wf.getnframes()
            pcm = wf.readframes(n)
    except (OSError, EOFError, wave.Error):
        return None, "bad_format", 0
    if n <= 0 or len(pcm) != n * BANK_WIDTH:
        return None, "bad_format", 0
    dur_ms = int(round(n * 1000 / BANK_RATE))
    if dur_ms > max_ms:
        return None, "too_long", dur_ms
    return pcm, "ok", dur_ms


def load_bank_dir(bank_dir, voice: str, s: Optional[Settings] = None) -> LoadResult:
    """Load and verify ONE bank directory ``<root>/<voice>/<sha8>/``. Any mismatch is no bank (fail silent, never the wrong
    voice): the manifest voice and the directory names must equal ``voice``; the directory name must equal the sha8 recomputed
    from the files; every file must hash to its manifest sha256, be 24 kHz mono S16, and be at most ``REFLEX_MAX_MS`` long."""
    s = s or settings()
    d = Path(bank_dir)
    mf = d / "manifest.json"
    try:
        if not mf.is_file() or mf.stat().st_size > _MAX_MANIFEST_BYTES:
            return LoadResult(None, "missing")
        man = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LoadResult(None, "bad_manifest")
    if not isinstance(man, dict) or not isinstance(man.get("takes"), list):
        return LoadResult(None, "bad_manifest")
    if not (isinstance(voice, str) and VOICE_RE.fullmatch(voice)) or man.get("voice") != voice or d.parent.name != voice:
        return LoadResult(None, "voice_mismatch")
    style_len = man.get("style_len")
    if style_len is not None and (isinstance(style_len, bool) or not isinstance(style_len, int)):
        return LoadResult(None, "bad_manifest")
    takes: dict = {}
    pairs = []
    for t in man["takes"]:
        if not isinstance(t, dict):
            return LoadResult(None, "bad_manifest")
        tid, sha = t.get("id"), t.get("sha256")
        if not (isinstance(tid, str) and TAKE_ID_RE.fullmatch(tid)) or not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            return LoadResult(None, "bad_manifest")
        path = d / f"{tid}.wav"
        try:
            raw = path.read_bytes() if path.is_file() and path.stat().st_size <= _MAX_TAKE_BYTES else None
        except OSError:
            raw = None
        if raw is None:
            return LoadResult(None, "missing")
        if hashlib.sha256(raw).hexdigest() != sha:
            return LoadResult(None, "sha_mismatch")
        pcm, why, dur_ms = _read_take(path, s.max_ms)
        if pcm is None:
            return LoadResult(None, why)
        rms = t.get("rms_db")
        takes[tid] = Take(tid, dur_ms, float(rms) if isinstance(rms, (int, float)) and not isinstance(rms, bool) else active_rms_db(pcm), pcm)
        pairs.append((tid, sha))
    if len(takes) < BANK_MIN_TAKES or len(takes) != len(man["takes"]):
        return LoadResult(None, "too_few")
    sha8 = bank_sha8(voice, style_len, pairs)
    if d.name != sha8 or man.get("bank") not in (None, sha8):
        return LoadResult(None, "sha_mismatch")
    return LoadResult(Bank(voice, sha8, style_len, takes), "ok")


def load_bank(voice: str, root: Optional[str] = None, s: Optional[Settings] = None) -> LoadResult:
    """The bank for ``voice`` under ``root`` (default ``REFLEX_DIR``): the valid ``<sha8>`` directory with the latest
    manifest ``render_date`` (ties: the greatest sha8). No valid directory = no bank, with the first failure's reason."""
    s = s or settings()
    if not (isinstance(voice, str) and VOICE_RE.fullmatch(voice)):
        return LoadResult(None, "voice_mismatch")
    vdir = Path(os.path.expanduser(root or s.dir)) / voice
    try:
        cands = sorted(p for p in vdir.iterdir() if p.is_dir() and BANK_SHA_RE.fullmatch(p.name))
    except OSError:
        return LoadResult(None, "missing")
    best, best_key, first_fail = None, None, None
    for c in cands:
        res = load_bank_dir(c, voice, s)
        if res.bank is None:
            first_fail = first_fail or res.reason
            continue
        try:
            date = str(json.loads((c / "manifest.json").read_text(encoding="utf-8")).get("render_date") or "")
        except (OSError, ValueError):
            date = ""
        key = (date, c.name)
        if best_key is None or key > best_key:
            best, best_key = res, key
    return best or LoadResult(None, first_fail or "missing")
