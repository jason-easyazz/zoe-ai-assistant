#!/usr/bin/env python3
"""Speaker-gate rebuild, step 1 — embed the replay corpus with the candidate embedder.

Runs ON THE PI (``zoe-pi``), never on the Jetson (no model load there: RAM-starved).
Record: ``docs/research/speaker-gate-rebuild-2026-10-04.md`` (§1.2 pick, §5 plan);
results: ``docs/research/speaker-gate-step1-results-2026-10-05.md``.

What it does
------------
1. Resolves the candidate embedder (default: 3D-Speaker CAM++ en-voxceleb, ONNX,
   Apache-2.0, torch-free) and pins it: the model file's SHA-256, size, licence and
   source URL go to a sidecar JSON next to the file. The model is downloaded ONLY
   here, only under the shadow dir, and a hash mismatch against a pinned digest
   refuses to run. Non-permissive licences (anything outside ``PERMISSIVE``) are
   refused unless ``--allow-nonpermissive`` — the record's rule is permissive-only
   on the live path.
2. Embeds every WAV under ``$SPEAKER_SHADOW_CORPUS`` (resample to the model rate;
   clips shorter than ``SPEAKER_SHADOW_MIN_S`` are skipped AND counted) and writes
   ``embeddings.npz`` + ``manifest.json`` to the shadow dir. The manifest keys clips
   by a hash of the RELATIVE PATH, never the name; it holds durations, levels and
   timings, never transcripts.
3. Measures per-clip compute latency (feature extraction + inference, after a
   warm-up), wall time and peak RSS (``getrusage``; the Pi has no ``/usr/bin/time``).
4. Guards the live daemon: polls its ``/health`` every ``SPEAKER_SHADOW_HEALTH_EVERY_S``
   and ABORTS (exit 3, partial results saved) if it fails repeatedly or its uptime
   goes backwards (= it restarted). ``--stop-at HH:MM`` ends the run gracefully at a
   wall-clock deadline (the nightly voice gate uses the panel from 04:18).

BIOMETRICS: the embeddings are voiceprints. They stay under the shadow dir (mode
700/600), are never committed, copied off the Pi, or printed. Output here is
aggregates only.

Environment (scripts only — no live flag reads any of these)
------------------------------------------------------------
  SPEAKER_SHADOW_DIR            shadow dir (default ~/.zoe-voice/speaker-shadow)
  SPEAKER_SHADOW_CORPUS         corpus root (default $SPEAKER_SHADOW_DIR/corpus)
  SPEAKER_SHADOW_MODEL          registry key (default campplus_en_voxceleb)
  SPEAKER_SHADOW_THREADS        ORT/BLAS threads, hard-capped at 2 (default 2)
  SPEAKER_SHADOW_MIN_S          skip clips shorter than this (default 0.5)
  SPEAKER_SHADOW_MAX_S          crop clips longer than this (default 30)
  SPEAKER_SHADOW_WARMUP         inferences excluded from latency stats (default 3)
  SPEAKER_SHADOW_HEALTH_URL     daemon health URL (default http://127.0.0.1:7777/health)
  SPEAKER_SHADOW_HEALTH_EVERY_S health poll period (default 60; 0 disables)

Exit codes: 0 ok, 2 bad usage/model refusal, 3 daemon-health abort, 4 deadline reached.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import resource
import sys
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Thread caps must be in the environment BEFORE numpy/onnxruntime import. The cap is
# a hard 2 (owner rule for Pi jobs) whatever SPEAKER_SHADOW_THREADS says.
MAX_THREADS = 2


def _threads() -> int:
    try:
        n = int(os.environ.get("SPEAKER_SHADOW_THREADS", str(MAX_THREADS)))
    except ValueError:
        n = MAX_THREADS
    return max(1, min(MAX_THREADS, n))


for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_var] = str(_threads())

import numpy as np  # noqa: E402

# --------------------------------------------------------------------------- registry

PERMISSIVE = frozenset({"Apache-2.0", "MIT", "BSD-3-Clause", "BSD-2-Clause"})

#: Candidate embedders. ``sha256`` is the pinned digest of the ONNX file; ``None``
#: means "first download is trust-on-first-use": the observed digest is written to the
#: sidecar and must then be pasted here (a later run refuses a different file).
MODELS: Dict[str, Dict[str, Any]] = {
    "campplus_en_voxceleb": {
        "file": "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
        "url": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
        ),
        # Trust-on-first-use digest, observed 2026-10-04 on the Pi after a download whose
        # size (29,596,978 B) matched the GitHub release-asset size; the release API
        # publishes no digest to cross-check against.
        "sha256": "357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
        "licence": "Apache-2.0",
        "source": "https://github.com/modelscope/3D-Speaker (CAM++, VoxCeleb-trained)",
        "dim": 512,  # measured from the ONNX output on the Pi 2026-10-04 (the record said 192)
        "rate": 16000,
        "n_mels": 80,
        "role": "primary candidate (record §1.2 / §1.5)",
    },
    # The INCUMBENT, measured on the same clips as a baseline and as a second opinion on the
    # corpus structure. Weights ship inside the installed `resemblyzer` package (read-only import
    # from the daemon's venv; nothing is installed or downloaded). Needs torch, so Pi-only.
    "resemblyzer_baseline": {
        "kind": "resemblyzer",
        "file": "pretrained.pt",
        "url": None,
        "sha256": "39373b86598fa3da9fcddee6142382efe09777e8d37dc9c0561f41f0070f134e",  # TOFU 2026-10-04 (package pretrained.pt, 17,090,379 B)
        "licence": "Apache-2.0",
        "source": "https://github.com/resemble-ai/Resemblyzer (GE2E, what zoe_voice_daemon.py runs today)",
        "dim": 256,
        "rate": 16000,
        "n_mels": None,
        "role": "incumbent baseline",
    },
}


class ModelRefused(RuntimeError):
    """The model cannot be used (licence, pin mismatch, missing file)."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _chmod_private(path: Path, is_dir: bool) -> None:
    os.chmod(path, 0o700 if is_dir else 0o600)


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_private(path, True)
    return path


def resolve_model(
    key: str,
    models_dir: Path,
    *,
    allow_nonpermissive: bool = False,
    download: bool = True,
    registry: Optional[Dict[str, Dict[str, Any]]] = None,
    opener: Callable[[str], Any] = urllib.request.urlopen,
) -> Tuple[Path, Dict[str, Any]]:
    """Return ``(onnx_path, sidecar)``; download under *models_dir* if absent.

    Order of refusals: unknown key, non-permissive licence, pinned-digest mismatch.
    The sidecar (``<file>.json``) is rewritten every call with the OBSERVED digest.
    """
    reg = registry if registry is not None else MODELS
    if key not in reg:
        raise ModelRefused(f"unknown model {key!r}; known: {sorted(reg)}")
    spec = reg[key]
    if spec["licence"] not in PERMISSIVE and not allow_nonpermissive:
        raise ModelRefused(
            f"{key}: licence {spec['licence']!r} is not permissive; the live path is "
            "permissive-only (pass --allow-nonpermissive for a lab-only run)"
        )
    ensure_private_dir(models_dir)
    if spec.get("kind") == "resemblyzer":
        path = _resemblyzer_weights()
        return path, _write_sidecar(key, spec, path, models_dir / f"{key}.json")
    path = models_dir / spec["file"]
    if not path.exists():
        if not download:
            raise ModelRefused(f"{path} is missing and downloading is disabled")
        tmp = path.with_suffix(path.suffix + ".part")
        old_umask = os.umask(0o077)
        try:
            with opener(spec["url"]) as resp, open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            os.umask(old_umask)
        os.replace(tmp, path)
    _chmod_private(path, False)
    return path, _write_sidecar(key, spec, path, path.with_suffix(path.suffix + ".json"))


def _resemblyzer_weights() -> Path:
    import importlib.util as _u

    found = _u.find_spec("resemblyzer")
    if found is None or not found.submodule_search_locations:
        raise ModelRefused("resemblyzer is not importable in this interpreter")
    path = Path(list(found.submodule_search_locations)[0]) / "pretrained.pt"
    if not path.is_file():
        raise ModelRefused("resemblyzer weights (pretrained.pt) not found in the installed package")
    return path


def _write_sidecar(key: str, spec: Dict[str, Any], path: Path, side: Path) -> Dict[str, Any]:
    """Hash the model file, refuse a pinned-digest mismatch, and (re)write the sidecar JSON."""
    digest = sha256_file(path)
    pinned = spec.get("sha256")
    if pinned and digest != pinned:
        raise ModelRefused(
            f"{path.name}: sha256 {digest[:12]}… != pinned {pinned[:12]}… — refusing to run"
        )
    sidecar = {
        "model_key": key,
        "file": spec["file"],
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "pinned_in_registry": bool(pinned),
        "licence": spec["licence"],
        "licence_permissive": spec["licence"] in PERMISSIVE,
        "source": spec["source"],
        "url": spec.get("url"),
        "dim": spec["dim"],
        "rate": spec["rate"],
        "n_mels": spec["n_mels"],
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    side.write_text(json.dumps(sidecar, indent=2) + "\n")
    _chmod_private(side, False)
    return sidecar


# --------------------------------------------------------------------------- audio

def read_wav_mono(path: Path) -> Tuple[np.ndarray, int]:
    """Read a WAV as float32 mono in [-1, 1]. soundfile if present, else stdlib wave."""
    try:
        import soundfile as sf  # type: ignore

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return data.mean(axis=1).astype(np.float32, copy=False), int(sr)
    except ImportError:
        pass
    with wave.open(str(path), "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    return x.reshape(-1, ch).mean(axis=1).astype(np.float32, copy=False), int(sr)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    from math import gcd

    from scipy.signal import resample_poly  # Pi venv has scipy; slim CI never gets here

    g = gcd(sr_in, sr_out)
    return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32, copy=False)


def clip_stats(x: np.ndarray) -> Tuple[float, float]:
    """(rms dBFS, peak) of a float signal — coarse level descriptors, no content."""
    if x.size == 0:
        return -120.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return (20.0 * math.log10(rms) if rms > 1e-9 else -120.0), float(np.max(np.abs(x)))


# --------------------------------------------------------------------------- features

def _mel(f: np.ndarray) -> np.ndarray:
    return 1127.0 * np.log(1.0 + f / 700.0)


def mel_banks(n_mels: int = 80, n_fft: int = 512, rate: int = 16000, low: float = 20.0) -> np.ndarray:
    """Kaldi/torchaudio ``get_mel_banks`` (high_freq=0 → Nyquist), shape (n_mels, n_fft//2+1)."""
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


_FB_CACHE: Dict[Tuple[int, int, int], np.ndarray] = {}
_EPS = float(np.finfo(np.float32).eps)


def kaldi_fbank(wave16: np.ndarray, *, n_mels: int = 80, rate: int = 16000, cmn: bool = True) -> np.ndarray:
    """torchaudio.compliance.kaldi.fbank defaults, numpy only: 25 ms / 10 ms frames,
    snip_edges, DC removal, 0.97 pre-emphasis, Povey window, 512-pt power spectrum,
    log mel floor eps, dither 0 — then per-utterance mean normalisation (3D-Speaker).
    Returns (T, n_mels) float32; shape (0, n_mels) when the clip is shorter than a frame.
    """
    win, hop, n_fft = 400, 160, 512
    x = np.asarray(wave16, dtype=np.float32)
    if x.size < win:
        return np.zeros((0, n_mels), dtype=np.float32)
    n_frames = 1 + (x.size - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
    fr = x[idx].astype(np.float32)
    fr = fr - fr.mean(axis=1, keepdims=True)
    prev = np.concatenate([fr[:, :1], fr[:, :-1]], axis=1)  # replicate-pad first sample
    fr = fr - 0.97 * prev
    window = np.power(0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(win) / (win - 1)), 0.85).astype(np.float32)
    fr = fr * window
    spec = np.abs(np.fft.rfft(fr, n=n_fft, axis=1)) ** 2
    key = (n_mels, n_fft, rate)
    if key not in _FB_CACHE:
        _FB_CACHE[key] = mel_banks(n_mels, n_fft, rate)
    mel = spec.astype(np.float32) @ _FB_CACHE[key].T
    feat = np.log(np.maximum(mel, _EPS)).astype(np.float32)
    if cmn:
        feat = feat - feat.mean(axis=0, keepdims=True)
    return feat


# --------------------------------------------------------------------------- embedder

class OnnxEmbedder:
    """ONNX speaker embedder: wave16k -> L2-normalised vector, with split timings."""

    def __init__(self, model_path: Path, spec: Dict[str, Any], threads: int):
        import onnxruntime as ort  # lazy: unit tests never import it

        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(MAX_THREADS, threads))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.out = self.sess.get_outputs()[0].name
        self.spec = spec
        self.metadata = dict(self.sess.get_modelmeta().custom_metadata_map or {})
        self.ort_version = ort.__version__

    def embed(self, wave16: np.ndarray) -> Tuple[np.ndarray, float, float]:
        """Returns (unit vector, fbank_ms, infer_ms)."""
        t0 = time.perf_counter()
        feat = kaldi_fbank(wave16, n_mels=self.spec["n_mels"], rate=self.spec["rate"])
        t1 = time.perf_counter()
        out = self.sess.run([self.out], {self.inp: feat[None, :, :]})[0]
        t2 = time.perf_counter()
        v = np.asarray(out, dtype=np.float32).reshape(-1)
        n = float(np.linalg.norm(v))
        return (v / n if n > 0 else v), (t1 - t0) * 1000.0, (t2 - t1) * 1000.0


class ResemblyzerEmbedder:
    """The incumbent GE2E encoder, run the way the daemon runs it (preprocess_wav + embed_utterance).

    ``fbank_ms`` is the preprocess (volume-normalise + VAD trim) time, ``infer_ms`` the encoder.
    A clip that trims to nothing raises ValueError and is counted as an ``error`` row.
    """

    def __init__(self, model_path: Path, spec: Dict[str, Any], threads: int):
        import torch  # lazy; Pi venv only

        torch.set_num_threads(max(1, min(MAX_THREADS, threads)))
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        from resemblyzer import VoiceEncoder, preprocess_wav

        self._pre = preprocess_wav
        self.enc = VoiceEncoder(device="cpu", verbose=False, weights_fpath=Path(model_path))
        self.spec = spec
        self.metadata = {"framework": "resemblyzer-ge2e"}
        self.ort_version = f"torch {torch.__version__}"

    def embed(self, wave16: np.ndarray) -> Tuple[np.ndarray, float, float]:
        t0 = time.perf_counter()
        wav = self._pre(wave16, source_sr=int(self.spec["rate"]))
        t1 = time.perf_counter()
        if wav.size < 1600:
            raise ValueError("no speech after preprocess")
        v = np.asarray(self.enc.embed_utterance(wav), dtype=np.float32).reshape(-1)
        t2 = time.perf_counter()
        n = float(np.linalg.norm(v))
        return (v / n if n > 0 else v), (t1 - t0) * 1000.0, (t2 - t1) * 1000.0


def make_embedder(model_path: Path, spec: Dict[str, Any], threads: int) -> Any:
    if spec.get("kind") == "resemblyzer":
        return ResemblyzerEmbedder(model_path, spec, threads)
    return OnnxEmbedder(model_path, spec, threads)


# --------------------------------------------------------------------------- daemon guard

class DaemonGuard:
    """Polls the live daemon's /health; trips on repeated failure or an uptime reset."""

    def __init__(self, url: str, every_s: float, *, fetch: Optional[Callable[[str], Dict[str, Any]]] = None,
                 clock: Callable[[], float] = time.monotonic, max_failures: int = 3):
        self.url, self.every_s, self.clock = url, every_s, clock
        self.fetch = fetch or self._http_fetch
        self.max_failures = max_failures
        self.last_check = -1e18
        self.last_uptime: Optional[float] = None
        self.failures = 0
        self.checks = 0
        self.restarts_seen = 0
        self.tripped: Optional[str] = None

    @staticmethod
    def _http_fetch(url: str) -> Dict[str, Any]:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def poll(self, force: bool = False) -> Optional[str]:
        """Return a reason string if the run must abort, else None."""
        if self.every_s <= 0 and not force:
            return None
        now = self.clock()
        if not force and now - self.last_check < self.every_s:
            return None
        self.last_check = now
        self.checks += 1
        try:
            doc = self.fetch(self.url)
            ok = str(doc.get("status", "")).lower() == "ok"
            up = doc.get("uptime_s")
        except Exception:
            ok, up = False, None
        if not ok:
            self.failures += 1
            if self.failures >= self.max_failures:
                self.tripped = f"daemon health failed {self.failures} polls in a row"
            return self.tripped
        self.failures = 0
        if isinstance(up, (int, float)):
            if self.last_uptime is not None and up + 1.0 < self.last_uptime:
                self.restarts_seen += 1
                self.tripped = "daemon uptime went backwards (restart)"
                return self.tripped
            self.last_uptime = float(up)
        return None


# --------------------------------------------------------------------------- corpus run

def clip_id(rel_path: str) -> str:
    """Stable, name-free handle for a clip: sha256 of its relative path (first 16 hex)."""
    return hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]


def clip_group(rel_path: str) -> str:
    top = rel_path.split("/", 1)[0]
    if top.startswith("quarantine-tv"):
        return "tv"
    if top.startswith("quarantine-nonspeech"):
        return "nonspeech"
    if top.startswith("quarantine-"):
        return "other_quarantine"
    return "top"


def list_corpus(root: Path) -> List[Path]:
    """Every WAV under *root* (top level + the quarantine dirs that were shipped), sorted."""
    return sorted(p for p in root.rglob("*.wav") if p.is_file())


def _deadline_epoch(stop_at: Optional[str], now: Optional[float] = None) -> Optional[float]:
    if not stop_at:
        return None
    hh, mm = stop_at.split(":")
    now_dt = _dt.datetime.fromtimestamp(now if now is not None else time.time())
    target = now_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if target <= now_dt:
        target += _dt.timedelta(days=1)
    return target.timestamp()


def run_embed(
    corpus: Path,
    out_dir: Path,
    embedder: Any,
    *,
    sidecar: Dict[str, Any],
    min_s: float = 0.5,
    max_s: float = 30.0,
    warmup: int = 3,
    guard: Optional[DaemonGuard] = None,
    deadline: Optional[float] = None,
    limit: Optional[int] = None,
    now: Callable[[], float] = time.time,
) -> Tuple[int, Dict[str, Any]]:
    """Embed the corpus; write embeddings.npz + manifest.json. Returns (exit_code, manifest).

    *embedder* needs ``.embed(wave16k) -> (unit_vec, fbank_ms, infer_ms)``, ``.spec`` and
    ``.metadata``/``.ort_version`` (the latter two optional). Tests pass a fake.
    """
    rate = int(embedder.spec["rate"])
    ensure_private_dir(out_dir)
    old_umask = os.umask(0o077)
    try:
        files = list_corpus(corpus)
        if limit:
            files = files[:limit]
        t_wall0 = time.perf_counter()
        rows: List[Dict[str, Any]] = []
        vecs: List[np.ndarray] = []
        kept_rows: List[int] = []
        exit_code, abort_reason = 0, None
        n_embedded = 0
        for i, p in enumerate(files):
            if guard is not None:
                reason = guard.poll()
                if reason:
                    exit_code, abort_reason = 3, reason
                    break
            if deadline is not None and now() >= deadline:
                exit_code, abort_reason = 4, "deadline reached"
                break
            rel = p.relative_to(corpus).as_posix()
            row: Dict[str, Any] = {"id": clip_id(rel), "group": clip_group(rel)}
            try:
                st = p.stat()
                row["mtime"] = int(st.st_mtime)
                t_r0 = time.perf_counter()
                x, sr_in = read_wav_mono(p)
                row["sr_in"], row["duration_s"] = sr_in, round(x.size / sr_in, 3)
                row["rms_dbfs"], row["peak"] = (round(v, 3) for v in clip_stats(x))
                if row["duration_s"] < min_s:
                    row["status"] = "short"
                    rows.append(row)
                    continue
                x = resample(x, sr_in, rate)
                x = x[: int(max_s * rate)]
                row["resample_ms"] = round((time.perf_counter() - t_r0) * 1000.0, 3)
                vec, f_ms, i_ms = embedder.embed(x)
                if not np.all(np.isfinite(vec)):
                    raise FloatingPointError("non-finite embedding")
                n_embedded += 1
                row.update(status="ok", fbank_ms=round(f_ms, 3), infer_ms=round(i_ms, 3),
                           compute_ms=round(f_ms + i_ms, 3), warmup=n_embedded <= warmup)
                kept_rows.append(len(rows))
                vecs.append(vec)
            except Exception as exc:  # one bad clip must not sink the run; count it by class
                row["status"] = "error"
                row["error"] = type(exc).__name__
            rows.append(row)
        wall_s = time.perf_counter() - t_wall0
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KiB
        dim = int(embedder.spec["dim"])
        emb = np.vstack(vecs).astype(np.float32) if vecs else np.zeros((0, dim), dtype=np.float32)
        counts: Dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        manifest = {
            "schema": 1,
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "model": {k: sidecar.get(k) for k in ("model_key", "file", "sha256", "licence", "dim", "rate", "n_mels")},
            "runtime": getattr(embedder, "ort_version", None),
            "onnx_metadata": getattr(embedder, "metadata", {}),
            "onnxruntime": getattr(embedder, "ort_version", None),
            "numpy": np.__version__,
            "threads": _threads(),
            "min_s": min_s,
            "max_s": max_s,
            "warmup": warmup,
            "n_files_seen": len(files),
            "n_listed": len(rows),
            "counts": counts,
            "complete": exit_code == 0 and len(rows) == len(files),
            "abort_reason": abort_reason,
            "wall_s": round(wall_s, 2),
            "peak_rss_mb": round(peak_rss_mb, 1),
            "daemon_health_checks": getattr(guard, "checks", 0),
            "daemon_restarts_seen": getattr(guard, "restarts_seen", 0),
            "clips": rows,
            "emb_row_of_clip": {rows[j]["id"]: k for k, j in enumerate(kept_rows)},
        }
        np.savez(out_dir / "embeddings.npz", emb=emb)
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
        for name in ("embeddings.npz", "manifest.json"):
            _chmod_private(out_dir / name, False)
        return exit_code, manifest
    finally:
        os.umask(old_umask)


# --------------------------------------------------------------------------- CLI

def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shadow-dir", default=os.environ.get("SPEAKER_SHADOW_DIR", str(Path.home() / ".zoe-voice" / "speaker-shadow")))
    ap.add_argument("--corpus", default=os.environ.get("SPEAKER_SHADOW_CORPUS"))
    ap.add_argument("--model", default=os.environ.get("SPEAKER_SHADOW_MODEL", "campplus_en_voxceleb"))
    ap.add_argument("--allow-nonpermissive", action="store_true", help="lab-only; never for the live path")
    ap.add_argument("--download-only", action="store_true", help="fetch + pin the model, then exit")
    ap.add_argument("--limit", type=int, default=None, help="embed only the first N clips (smoke test)")
    ap.add_argument("--stop-at", default=None, metavar="HH:MM", help="end gracefully at this local wall-clock time")
    ap.add_argument("--out-dir", default=None, help="where embeddings.npz/manifest.json go (default: shadow dir)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    shadow = ensure_private_dir(Path(args.shadow_dir).expanduser())
    corpus = Path(args.corpus).expanduser() if args.corpus else shadow / "corpus"
    try:
        model_path, sidecar = resolve_model(
            args.model, shadow / "models", allow_nonpermissive=args.allow_nonpermissive
        )
    except ModelRefused as exc:
        print(f"model refused: {exc}", file=sys.stderr)
        return 2
    print(f"model {sidecar['model_key']} licence={sidecar['licence']} sha256={sidecar['sha256'][:12]}… "
          f"pinned={sidecar['pinned_in_registry']} size={sidecar['size_bytes']}B")
    if args.download_only:
        return 0
    if not corpus.is_dir():
        print("corpus dir missing", file=sys.stderr)
        return 2
    spec = MODELS[args.model]
    embedder = make_embedder(model_path, spec, _threads())
    guard = DaemonGuard(
        os.environ.get("SPEAKER_SHADOW_HEALTH_URL", "http://127.0.0.1:7777/health"),
        float(os.environ.get("SPEAKER_SHADOW_HEALTH_EVERY_S", "60")),
    )
    first = guard.poll(force=True) if guard.every_s > 0 else None
    if first:
        print(f"refusing to start: {first}", file=sys.stderr)
        return 3
    code, man = run_embed(
        corpus,
        Path(args.out_dir).expanduser() if args.out_dir else shadow,
        embedder,
        sidecar=sidecar,
        min_s=float(os.environ.get("SPEAKER_SHADOW_MIN_S", "0.5")),
        max_s=float(os.environ.get("SPEAKER_SHADOW_MAX_S", "30")),
        warmup=int(os.environ.get("SPEAKER_SHADOW_WARMUP", "3")),
        guard=guard,
        deadline=_deadline_epoch(args.stop_at),
        limit=args.limit,
    )
    print(json.dumps({k: man[k] for k in ("counts", "complete", "abort_reason", "wall_s", "peak_rss_mb",
                                           "threads", "daemon_health_checks", "daemon_restarts_seen")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
