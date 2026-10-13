"""WeSpeaker ResNet34 speaker embedder for the panel daemon (static INT8 QDQ, cohort-centred) - FLAG-DARK.

The record is ``docs/research/speaker-resnet34-optimisation-2026-10.md`` (it is the spec of this file); the operator steps
are ``docs/knowledge/speaker-resnet34-switch.md``. Nothing here runs unless ``ZOE_SPEAKER_MODEL`` is ``dual`` or
``resnet34`` in the daemon's environment (default ``resemblyzer``: this module is not even imported).

What lives here, all of it testable without audio or a model (``tests/unit/test_speaker_resnet34.py``):

* the model identity and the pins - ``MODEL_ID`` names the model, the quantisation AND the centring-vector version, and
  profiles of different ids NEVER compare (``match_profiles`` filters on it; ResNet34 and Resemblyzer are both 256-d, so
  a shape check cannot);
* the numpy Kaldi fbank front end, the ONNX session (``ORT_ENABLE_ALL``, 2 threads, ``allow_spinning=0``), cohort-mean
  centring with the shipped public-cohort vector, and the START-UP SELF-TEST that refuses to run on drift;
* length-group thresholds (2 s or less / longer), mirrored from ``services/zoe-data/speaker_models.py`` and drift-pinned;
* the enrolment recipe: mean of the L2-normalised clip embeddings with leave-one-out outlier rejection.

Biometrics: audio never leaves the panel for this model - the daemon embeds on the Pi and uploads the 256-float PROFILE.
The artifacts file holds only PUBLIC data (a mean of LibriSpeech embeddings, the embedding of a synthetic tone signal).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import threading
import time
import wave
from typing import Any, Callable, Optional, Sequence

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# -- identity -------------------------------------------------------------------------------------------------------
FAMILY = "wespeaker_resnet34"
QUANT = "i8qdq-u8s8"
CENTRE_VERSION = "c1"
MODEL_ID = FAMILY + "+" + QUANT + "+" + CENTRE_VERSION
LEGACY_MODEL_ID = "resemblyzer"
EMBED_DIM = 256
SAMPLE_RATE = 16000

# -- pins -----------------------------------------------------------------------------------------------------------
MODEL_FILENAME = "wespeaker_resnet34_i8qdq_u8s8.onnx"
#: static INT8 QDQ (U8 activations, S8 per-channel weights, MinMax over 100 public dev-clean clips), built by
#: ``scripts/perf/speaker_resnet34_artifacts.py build`` from the upstream FP32 file pinned just below.
MODEL_SHA256 = "d1f65aa0e09ba00793df05ecdb21df7c5fc1336e219e029d399b2f5aa71eae32"
FP32_SHA256 = "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94"
FP32_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
            "wespeaker_en_voxceleb_resnet34.onnx")
ARTIFACTS_FILENAME = "zoe_speaker_resnet34_artifacts.json"
#: sha256 of the centring vector as 256 little-endian float32 (the bytes ``mean`` produces), pinned so an edited JSON refuses
CENTRE_SHA256 = "9e78bcc589667c0ad9e3c2b3a3ff45dccf4eac876caa1782cc16c38926029754"
DEFAULT_MODEL_DIR = os.path.expanduser("~/.zoe-voice/models")

# -- runtime (record section 3.3: EXTENDED or higher is MANDATORY for this file) ---------------------------------------
ORT_LEVEL = "ORT_ENABLE_ALL"
ORT_LEVELS_ALLOWED = ("ORT_ENABLE_EXTENDED", "ORT_ENABLE_ALL")
ORT_THREADS = 2
SELFTEST_MIN_COSINE = 0.9999
SELFTEST_MAX_FACTOR = 3.0

# -- thresholds (MIRROR of services/zoe-data/speaker_models.py; drift-pinned) -----------------------------------------
SHORT_MAX_S = 2.0
THRESHOLDS = dict(far1=dict(short=0.233, long=0.265), far01=dict(short=0.309, long=0.351))
DEFAULT_TIER = "far1"
ADAPT_MIN_SCORE = 0.342
ADAPT_ANCHOR_FLOOR = 0.45
ENROLL_CLIPS = 5
ENROLL_TAU = 0.30
ENROLL_MIN_KEEP = 3


class Unavailable(RuntimeError):
    """The embedder cannot be used (file missing, pin mismatch, ORT absent, self-test failed). The daemon scores nothing then."""


class SelfTestFailed(Unavailable):
    """The start-up self-test saw drift: the embedder is REFUSED rather than trusted."""


# -- vectors --------------------------------------------------------------------------------------------------------

def l2(x: Any) -> np.ndarray:
    """Unit-length float32 copy of ``x`` (rows if 2-D). A zero vector stays zero rather than becoming NaN."""
    a = np.asarray(x, dtype=np.float64)
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return (a / np.maximum(n, 1e-12)).astype(np.float32)


def centre_embedding(raw: Any, mu: Any) -> np.ndarray:
    """Cohort-mean centring (record 7a): ``l2(l2(raw) - mu)``. ``mu`` is the mean of the L2-normalised cohort embeddings."""
    return l2(l2(raw).astype(np.float64) - np.asarray(mu, dtype=np.float64))


def cosine(a: Any, b: Any) -> float:
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if a.shape != b.shape:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0 or nb <= 0 or not math.isfinite(na * nb):
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# -- thresholds -----------------------------------------------------------------------------------------------------

def length_group(seconds: Any) -> str:
    """``short`` (2 s or less) | ``long``. Unknown / non-finite length is ``long`` (the stricter bar)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "long"
    if not math.isfinite(s) or s <= 0:
        return "long"
    return "short" if s <= SHORT_MAX_S else "long"


def tier_from_env() -> str:
    raw = os.environ.get("ZOE_SPEAKER_RESNET34_TIER", DEFAULT_TIER).strip().lower()
    return raw if raw in THRESHOLDS else DEFAULT_TIER


def threshold_for(group: str, tier: Optional[str] = None) -> float:
    """The accept bar for a length group (env overrides ``ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT/_LONG`` as on the server)."""
    g = "short" if group == "short" else "long"
    table = THRESHOLDS[tier if tier in THRESHOLDS else tier_from_env()]
    try:
        over = float(os.environ.get("ZOE_SPEAKER_RESNET34_THRESHOLD_" + g.upper(), ""))
    except ValueError:
        over = float("nan")
    return over if math.isfinite(over) else table[g]


def decision(score: Optional[float], seconds: Any, tier: Optional[str] = None) -> dict:
    """What the gate would say about ``score`` for a clip of ``seconds``: group, threshold, accept. Informational on the
    panel (the SERVER decides every claim)."""
    group = length_group(seconds)
    thr = threshold_for(group, tier)
    ok = score is not None and math.isfinite(float(score)) and float(score) >= thr
    return dict(group=group, threshold=round(thr, 4), accept=bool(ok))


# -- enrolment (record 6a/6c) ---------------------------------------------------------------------------------------

class ProfileBuild:
    """Result of ``build_profile``: the unit profile vector (None when too few clips survive), who was kept and dropped."""

    def __init__(self, vector: Optional[np.ndarray], kept: list, dropped: list):
        self.vector, self.kept, self.dropped = vector, kept, dropped

    @property
    def ok(self) -> bool:
        return self.vector is not None


def _loo(E: np.ndarray, idx: list) -> np.ndarray:
    out = []
    for i in idx:
        rest = [j for j in idx if j != i]
        out.append(float(E[i] @ l2(E[rest].mean(axis=0))))
    return np.array(out)


def build_profile(embeddings: Sequence[Any], tau: float = ENROLL_TAU, min_keep: int = ENROLL_MIN_KEEP,
                  reject_floor: int = 2) -> ProfileBuild:
    """One profile vector from the enrolment clips' (already centred) embeddings.

    Mean of the L2-normalised clips, re-normalised; before that, drop - one at a time, lowest first - any clip whose
    leave-one-out cosine to the mean of the others is under ``tau`` (a stranger's clip in the middle of the enrolment),
    never going below ``reject_floor`` clips. Fewer than ``min_keep`` survivors means NO profile (``vector`` is None): the
    caller re-asks or stores nothing. Pure and deterministic.
    """
    E = l2(np.asarray(list(embeddings), dtype=np.float64).reshape(len(embeddings), -1)) if len(embeddings) else None
    if E is None:
        return ProfileBuild(None, [], [])
    keep = list(range(len(E)))
    dropped: list = []
    while len(keep) > max(2, reject_floor):
        c = _loo(E, keep)
        k = int(np.argmin(c))
        if c[k] >= tau:
            break
        dropped.append(keep.pop(k))
    if len(keep) < max(1, min_keep):
        return ProfileBuild(None, keep, sorted(dropped))
    return ProfileBuild(l2(E[keep].mean(axis=0)), keep, sorted(dropped))


# -- matching -------------------------------------------------------------------------------------------------------

def profile_model_id(profile: dict) -> str:
    raw = profile.get("model_id") if isinstance(profile, dict) else None
    s = raw.strip() if isinstance(raw, str) else ""
    return s or LEGACY_MODEL_ID


def match_profiles(embedding: Any, profiles: Sequence[dict], model_id: str = MODEL_ID) -> tuple:
    """Best cosine match of ``embedding`` (in ``model_id``'s space) over the synced profiles OF THAT MODEL ONLY.

    Returns ``(user_id | None, score | None, compared)``. A profile whose ``model_id`` differs is skipped without being
    scored - the cross-model guard - and so is a malformed / wrong-dimension row.
    """
    import base64

    emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
    best_user, best_score, compared = None, -1.0, 0
    for p in profiles:
        uid = p.get("user_id") if isinstance(p, dict) else None
        if not uid or profile_model_id(p) != model_id:
            continue
        try:
            ref = np.frombuffer(base64.b64decode(p["embedding_base64"]), dtype=np.float32)
        except Exception:
            continue
        if ref.shape != emb.shape:
            continue
        s = cosine(emb, ref)
        compared += 1
        if s > best_score:
            best_user, best_score = uid, s
    if best_user is None:
        return None, None, compared
    return best_user, best_score, compared


# -- front end (a copy of scripts/voice/speaker_shadow_embed.py kaldi_fbank; drift-pinned by the tests) -----------------

def _mel(f: np.ndarray) -> np.ndarray:
    return 1127.0 * np.log(1.0 + f / 700.0)


def mel_banks(n_mels: int = 80, n_fft: int = 512, rate: int = 16000, low: float = 20.0) -> np.ndarray:
    """Kaldi / torchaudio ``get_mel_banks`` (high_freq = Nyquist), shape (n_mels, n_fft // 2 + 1)."""
    n_bins = n_fft // 2
    high = rate / 2.0
    mel_lo, mel_hi = float(_mel(np.float64(low))), float(_mel(np.float64(high)))
    delta = (mel_hi - mel_lo) / (n_mels + 1)
    b = np.arange(n_mels, dtype=np.float64)[:, None]
    left = mel_lo + b * delta
    center = mel_lo + (b + 1.0) * delta
    right = mel_lo + (b + 2.0) * delta
    mel_f = _mel(np.arange(n_bins, dtype=np.float64) * (rate / n_fft))[None, :]
    up = (mel_f - left) / (center - left)
    down = (right - mel_f) / (right - center)
    fb = np.maximum(0.0, np.minimum(up, down))
    return np.pad(fb, ((0, 0), (0, 1))).astype(np.float32)


_FB_CACHE: dict = {}
_EPS = float(np.finfo(np.float32).eps)


def kaldi_fbank(wave16: np.ndarray, n_mels: int = 80, rate: int = 16000) -> np.ndarray:
    """80-bin Kaldi fbank + per-utterance mean normalisation, numpy only. (T, 80) float32; (0, 80) under one frame."""
    win, hop, n_fft = 400, 160, 512
    x = np.asarray(wave16, dtype=np.float32)
    if x.size < win:
        return np.zeros((0, n_mels), dtype=np.float32)
    n_frames = 1 + (x.size - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
    fr = x[idx].astype(np.float32)
    fr = fr - fr.mean(axis=1, keepdims=True)
    prev = np.concatenate([fr[:, :1], fr[:, :-1]], axis=1)
    fr = fr - 0.97 * prev
    window = np.power(0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(win) / (win - 1)), 0.85).astype(np.float32)
    fr = fr * window
    spec = np.abs(np.fft.rfft(fr, n=n_fft, axis=1)) ** 2
    key = (n_mels, n_fft, rate)
    if key not in _FB_CACHE:
        _FB_CACHE[key] = mel_banks(n_mels, n_fft, rate)
    mel = spec.astype(np.float32) @ _FB_CACHE[key].T
    feat = np.log(np.maximum(mel, _EPS)).astype(np.float32)
    return feat - feat.mean(axis=0, keepdims=True)


def wav_to_float(wav_bytes: bytes) -> tuple:
    """(mono float32 in [-1, 1] at 16 kHz, seconds) from WAV bytes. Anything but 16-bit PCM raises ``ValueError``."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        ch, width, rate, n = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        raw = wf.readframes(n)
    if width != 2 or ch < 1 or rate <= 0:
        raise ValueError("unsupported WAV (need 16-bit PCM)")
    x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1).astype(np.float32)
    if rate != SAMPLE_RATE:
        try:
            from math import gcd
            from scipy.signal import resample_poly   # the panel venv has scipy (resemblyzer needs it)

            g = gcd(rate, SAMPLE_RATE)
            x = resample_poly(x, SAMPLE_RATE // g, rate // g).astype(np.float32)
        except ImportError:
            t_new = np.arange(int(len(x) * SAMPLE_RATE / rate)) * (rate / SAMPLE_RATE)
            x = np.interp(t_new, np.arange(len(x)), x).astype(np.float32)
    return x, float(len(x)) / SAMPLE_RATE


def synthetic_clip(seconds: float = 3.0) -> np.ndarray:
    """A deterministic PUBLIC test signal for the self-test: a voiced-ish harmonic stack with vibrato and a slow formant
    sweep plus a chirp 'noise' floor. Pure arithmetic - no RNG - so every numpy build yields the same samples to float32
    rounding. It is not a voice and carries no biometric."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    f0 = 118.0 * (1.0 + 0.03 * np.sin(2 * np.pi * 5.0 * t))
    phase = 2 * np.pi * np.cumsum(f0) / SAMPLE_RATE
    sweep = 900.0 + 500.0 * np.sin(2 * np.pi * 0.7 * t)
    x = np.zeros(n, dtype=np.float64)
    for k in range(1, 25):
        amp = np.exp(-0.5 * ((k * 118.0 - sweep) / 450.0) ** 2) / k
        x += amp * np.sin(k * phase)
    x += 0.004 * np.sin(2 * np.pi * (300.0 * t + 1500.0 * t * t))
    x *= 0.5 * (1.0 - np.cos(2 * np.pi * np.minimum(t / 0.05, 1.0) * 0.5))
    return (0.3 * x / max(1e-9, float(np.max(np.abs(x))))).astype(np.float32)


# -- artifacts (public data only) -----------------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def centre_digest(vector: Any) -> str:
    """The pinned digest of a centring vector: sha256 over its 256 little-endian float32 bytes."""
    return hashlib.sha256(np.asarray(vector, dtype="<f4").tobytes()).hexdigest()


def find_artifacts_path(model_dir: Optional[str] = None) -> Optional[str]:
    for d in (model_dir, HERE, DEFAULT_MODEL_DIR):
        if d:
            p = os.path.join(d, ARTIFACTS_FILENAME)
            if os.path.isfile(p):
                return p
    return None


def load_artifacts(path: Optional[str] = None, *, check_pin: bool = True) -> dict:
    """The artifacts JSON (centring vector + self-test reference), verified: right model id, vector of the right size and,
    unless ``check_pin`` is off (tests), its digest equal to ``CENTRE_SHA256``."""
    p = path or find_artifacts_path()
    if not p:
        raise Unavailable("artifacts file " + ARTIFACTS_FILENAME + " not found beside the daemon")
    try:
        with open(p, encoding="utf-8") as fh:
            art = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Unavailable("artifacts unreadable: " + type(exc).__name__) from exc
    if art.get("model_id") != MODEL_ID:
        raise Unavailable("artifacts are for model " + str(art.get("model_id")) + ", this build is " + MODEL_ID)
    vec = (art.get("centre") or {}).get("vector")
    if not isinstance(vec, list) or len(vec) != EMBED_DIM or not all(isinstance(v, (int, float)) for v in vec):
        raise Unavailable("centring vector missing or the wrong size")
    if check_pin and centre_digest(vec) != CENTRE_SHA256:
        raise Unavailable("centring vector does not match its pinned digest")
    ref = (art.get("selftest") or {}).get("reference")
    if not isinstance(ref, list) or len(ref) != EMBED_DIM:
        raise Unavailable("self-test reference missing or the wrong size")
    return art


# -- ORT session ----------------------------------------------------------------------------------------------------

def check_level(level: str) -> str:
    """Refuse a graph-optimisation level that leaves the INT8 QDQ graph unfused: at BASIC / DISABLE_ALL this file is 2.6x
    SLOWER than itself and its embeddings change (record 3.3). The opposite rule from CAM++, so it is pinned per model."""
    if level not in ORT_LEVELS_ALLOWED:
        raise Unavailable("graph optimisation level " + str(level) + " is not allowed for " + MODEL_ID
                          + " (need one of " + ", ".join(ORT_LEVELS_ALLOWED) + ")")
    return level


def make_session(model_path: str, ort_module: Any = None, level: str = ORT_LEVEL, threads: int = ORT_THREADS) -> Any:
    """The InferenceSession: ORT_ENABLE_ALL, ``threads`` intra-op threads, 1 inter-op, ``allow_spinning=0`` (idle workers
    stop burning CPU and it is about 13 % faster), CPU provider only."""
    check_level(level)
    ort = ort_module
    if ort is None:
        try:
            import onnxruntime as ort   # type: ignore
        except ImportError as exc:
            raise Unavailable("onnxruntime is not installed") from exc
    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, min(int(threads), 4))
    so.inter_op_num_threads = 1
    so.graph_optimization_level = getattr(ort.GraphOptimizationLevel, level)
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])


# -- the embedder ---------------------------------------------------------------------------------------------------

class Resnet34Embedder:
    """wave16k -> raw 256-d embedding (``embed_raw``) or the CENTRED unit embedding the profiles live in (``embed``).

    One session, loaded once; ``run`` is serialised by a lock (one inference at a time, flat memory). ``last_ms`` is
    ``[fbank_ms, infer_ms]`` of the latest call.
    """

    def __init__(self, session: Any, artifacts: dict, *, clock: Callable[[], float] = time.perf_counter):
        self.session = session
        self.mu = np.asarray(artifacts["centre"]["vector"], dtype=np.float32)
        self.artifacts = artifacts
        self._in = session.get_inputs()[0].name
        self._out = session.get_outputs()[0].name
        self._lock = threading.Lock()
        self._clock = clock
        self.last_ms = [0.0, 0.0]

    @classmethod
    def load(cls, model_dir: Optional[str] = None, *, artifacts_path: Optional[str] = None, ort_module: Any = None,
             level: str = ORT_LEVEL, threads: int = ORT_THREADS, selftest_factor: float = SELFTEST_MAX_FACTOR,
             verify: bool = True) -> "Resnet34Embedder":
        """The production loader: pin the file, build the session, run the self-test. Any failure raises ``Unavailable`` -
        the daemon then scores nothing with this model (it never falls back to an unverified one)."""
        d = model_dir or os.environ.get("SPEAKER_RESNET34_MODEL_DIR") or DEFAULT_MODEL_DIR
        path = os.path.join(d, MODEL_FILENAME)
        if not os.path.isfile(path):
            raise Unavailable(MODEL_FILENAME + " not found in " + d + " (run scripts/setup/fetch_speaker_models.sh verify)")
        digest = sha256_file(path)
        if digest != MODEL_SHA256:
            raise Unavailable("model file sha256 " + digest[:12] + " != pinned " + MODEL_SHA256[:12])
        art = load_artifacts(artifacts_path or find_artifacts_path(d))
        emb = cls(make_session(path, ort_module, level, threads), art)
        if verify:
            selftest(emb, max_factor=selftest_factor)
        return emb

    def embed_raw(self, wave16: np.ndarray) -> Optional[np.ndarray]:
        """Raw (uncentred) L2-normalised embedding, or None for a clip under one frame / ten feature frames."""
        t0 = self._clock()
        feat = kaldi_fbank(wave16)
        t1 = self._clock()
        if feat.shape[0] < 10:
            return None
        with self._lock:
            out = self.session.run([self._out], dict([(self._in, feat[None])]))[0]
        self.last_ms = [(t1 - t0) * 1e3, (self._clock() - t1) * 1e3]
        v = np.asarray(out, dtype=np.float32).reshape(-1)
        if v.size != EMBED_DIM or not np.all(np.isfinite(v)):
            return None
        return l2(v)

    def embed(self, wave16: np.ndarray) -> Optional[np.ndarray]:
        raw = self.embed_raw(wave16)
        return None if raw is None else centre_embedding(raw, self.mu)

    def embed_wav(self, wav_bytes: bytes) -> tuple:
        """(centred embedding | None, clip seconds) from WAV bytes. The whole clip is embedded - trimming silence costs
        +2.3 pp EER on the owner corpus (record 3c/3d) and a duration cap costs more (3b)."""
        x, seconds = wav_to_float(wav_bytes)
        return self.embed(x), seconds


def selftest(embedder: Any, *, reference: Optional[Sequence[float]] = None, max_factor: float = SELFTEST_MAX_FACTOR,
             min_cosine: float = SELFTEST_MIN_COSINE, expected_ms: Optional[float] = None,
             clock: Callable[[], float] = time.perf_counter) -> dict:
    """Embed the bundled PUBLIC synthetic signal and refuse on drift. Raises ``SelfTestFailed``; returns a small report.

    (a) cosine of the RAW embedding to the stored reference >= ``min_cosine`` - this is what an ORT upgrade, a wrong graph
        level (unfused graph: cosine 0.92-0.97) or a swapped file changes;
    (b) one warm call within ``max_factor`` x the expected latency - a gross slowdown (it is NOT sensitive enough to see
        the unfused graph on its own; the cosine is).
    """
    st = (getattr(embedder, "artifacts", None) or {}).get("selftest") or {}
    ref = np.asarray(reference if reference is not None else st.get("reference", []), dtype=np.float64)
    if ref.size != EMBED_DIM:
        raise SelfTestFailed("self-test reference is missing")
    exp_ms = float(expected_ms if expected_ms is not None else st.get("expected_ms", 110.0))
    clip = synthetic_clip(float(st.get("seconds", 3.0)))
    embedder.embed_raw(clip)                       # warm-up (first call pays one-off allocator work)
    t0 = clock()
    got = embedder.embed_raw(clip)
    ms = (clock() - t0) * 1e3
    if got is None:
        raise SelfTestFailed("self-test clip produced no embedding")
    cos = cosine(got, ref)
    report = dict(cosine=round(cos, 6), ms=round(ms, 1), expected_ms=exp_ms, factor=max_factor)
    if not math.isfinite(cos) or cos < min_cosine:
        raise SelfTestFailed("embedding drift: cosine " + format(cos, ".5f") + " to the reference (need >= "
                             + format(min_cosine, ".4f") + ")")
    if ms > max_factor * exp_ms:
        raise SelfTestFailed("too slow: " + format(ms, ".0f") + " ms for the self-test clip (limit "
                             + format(max_factor * exp_ms, ".0f") + " ms) - wrong graph level or a starved CPU")
    return report
