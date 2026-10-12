#!/usr/bin/env python3
"""Speaker-embedding bake-off harness (Resemblyzer vs ECAPA-TDNN vs WeSpeaker/3D-Speaker/TitaNet).

Record: docs/research/speaker-model-bakeoff-2026-10.md.  Nothing here touches a live
service, the GPU, or the repo's data: every artefact (impostor clips, embeddings, model
weights) lives under a SCRATCH directory outside the checkout, and the script prints
AGGREGATE numbers only (no clip names, no embeddings).

Sub-commands (run in the scratch venv, never the zoe-data venv):

  prep          build the public impostor set: LibriSpeech test-clean crops (clean +
                synthetic far-field variants, three length buckets) and Kokoro TTS voices.
  index-corpus  freeze the owner-corpus listing (top-level *.wav only) + capture sessions.
                Names stay in scratch; the corpus is read, never copied.
  embed         run ONE model over the corpus + impostor set -> emb_<model>.npz
                (embedding, per-clip seconds, peak RSS). One process per model so peak
                RSS is clean.
  score         trials + metrics over every emb_*.npz; prints markdown tables, writes JSON.
  bench         time one model on a directory of PUBLIC wavs (for the Raspberry Pi: no
                household audio is needed to measure latency).

Hard guards (this box runs the live voice brain): CUDA is hidden unconditionally; the
process refuses to start below ``--min-avail-mb`` MemAvailable (default 2048) and waits
(never proceeds) if memory dips below it mid-run; refuses between 02:30 and 04:30 local
(the 12B night window).  Run under ``nice -n 10`` AND inside a SHARED hold of the voice
harness lock so it never overlaps a speed-gated replay probe:

    flock -s -w 3600 /tmp/zoe-voice-harness.lock nice -n 10 \
        python3 scripts/perf/speaker_bakeoff.py embed --model <name> ...
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import random
import resource
import sys
import time
from math import gcd
from pathlib import Path

# The Jetson's GPU is shared with the live brain (NvMap sits outside every cgroup guard).
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

SR = 16000
BUCKETS = {"s": (1.0, 2.0), "m": (2.0, 4.0), "l": (4.0, 8.0)}  # crop lengths in seconds
BUCKET_NAMES = {"s": "<=2s", "m": "2-4s", "l": ">4s"}

MODELS = {
    # name: (kind, file or hub id)
    "resemblyzer": ("resemblyzer", None),
    "ecapa": ("speechbrain", "speechbrain/spkrec-ecapa-voxceleb"),
    # Kaldi-fbank ONNX models run through onnxruntime DIRECTLY with the repo's numpy fbank
    # (scripts/voice/speaker_shadow_embed.py): sherpa-onnx exposes no graph-optimisation
    # switch, and WeSpeaker CAM++ returns wrong embeddings under ORT's default/BASIC graph
    # optimisation on aarch64 (cos 0.03 vs ORT_DISABLE_ALL; see the record, section 4).
    "wespeaker_resnet34": ("ort", "wespeaker_en_voxceleb_resnet34.onnx", "ENABLE_ALL"),
    "wespeaker_campp": ("ort", "wespeaker_en_voxceleb_CAM++.onnx", "DISABLE_ALL"),
    "campp_3dspeaker": ("ort", "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx", "ENABLE_ALL"),
    # NeMo log-mel front end is not Kaldi fbank: use sherpa-onnx's extractor for it.
    "titanet_small": ("sherpa", "nemo_en_titanet_small.onnx"),
}

KOKORO_TEXTS = [
    "Turn off the lights.",
    "What time is it?",
    "Play some music.",
    "Add milk to the shopping list.",
    "Set a timer for ten minutes.",
    "What's the weather going to be like tomorrow afternoon?",
    "Can you remind me to water the garden after dinner tonight?",
    "Please read me my calendar for the rest of the week.",
    "Remind me to call the plumber on Thursday morning about the leak in the laundry, and put it on the shared calendar.",
    "I think we should leave a bit earlier on Saturday because the traffic near the beach is usually terrible.",
]


# ----------------------------------------------------------------------------- guards
def mem_available_mb() -> float:
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def in_blackout(now: _dt.datetime | None = None) -> bool:
    now = now or _dt.datetime.now()
    m = now.hour * 60 + now.minute
    return 150 <= m < 270  # 02:30 - 04:30


def guard(min_mb: float, wait_s: float = 0.0, what: str = "") -> None:
    if in_blackout():
        sys.exit("refusing to run between 02:30 and 04:30 (night window)")
    waited = 0.0
    while mem_available_mb() < min_mb:
        if waited >= wait_s:
            sys.exit(f"MemAvailable {mem_available_mb():.0f} MB < {min_mb:.0f} MB {what}; refusing")
        time.sleep(15)
        waited += 15


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ----------------------------------------------------------------------------- audio
def load_16k(path: str):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != SR:
        g = gcd(sr, SR)
        x = resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def write_wav(path: Path, x) -> None:
    import numpy as np
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x, -1.0, 1.0), SR, subtype="PCM_16")


def trim_edges(x, thresh_db: float = -40.0):
    """Drop leading/trailing 20 ms frames far below the clip's peak frame level."""
    import numpy as np

    hop = int(0.02 * SR)
    n = len(x) // hop
    if n < 3:
        return x
    fr = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    keep = np.where(fr > fr.max() * 10 ** (thresh_db / 20))[0]
    if len(keep) == 0:
        return x
    return x[keep[0] * hop : (keep[-1] + 1) * hop]


def farfield(x, rng):
    """Synthetic far-field/speakerphone channel: reverb tail, band limit, pink-ish noise, low level.

    An APPROXIMATION of the panel channel (matched in level to the corpus median,
    about -26 dBFS rms), not a measurement of it.
    """
    import numpy as np
    from scipy.signal import butter, fftconvolve, sosfilt

    rt60 = rng.uniform(0.25, 0.6)
    drr_db = rng.uniform(-2.0, 8.0)
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    tail[: int(0.004 * SR)] = 0.0
    tail *= math.sqrt(10 ** (-drr_db / 10) / (float((tail**2).sum()) + 1e-12))
    rir = tail
    rir[0] = 1.0
    y = fftconvolve(x, rir)[: len(x)]
    y = sosfilt(butter(2, [120, 6500], btype="band", fs=SR, output="sos"), y)
    snr_db = rng.uniform(8.0, 22.0)
    noise = sosfilt(butter(1, 2000, btype="low", fs=SR, output="sos"), rng.standard_normal(len(y)))
    sig_rms = math.sqrt(float((y**2).mean()) + 1e-12)
    noise *= sig_rms / (10 ** (snr_db / 20)) / (math.sqrt(float((noise**2).mean())) + 1e-12)
    y = y + noise
    target = 10 ** (rng.uniform(-29.0, -23.0) / 20)
    y = y * (target / (math.sqrt(float((y**2).mean())) + 1e-12))
    return np.clip(y, -0.99, 0.99).astype(np.float32)


# ----------------------------------------------------------------------------- prep
def cmd_prep(a) -> None:
    import numpy as np
    import soundfile as sf

    guard(a.min_avail_mb, a.wait_mem_s, "(prep)")
    rng_master = random.Random(a.seed)
    root = Path(a.libri)
    out = Path(a.out)
    manifest: list[dict] = []

    def emit(wav, rel_base: str, meta: dict, variants=("clean", "farfield")):
        crng = np.random.default_rng(_stable_seed(a.seed, rel_base))
        for v in variants:
            y = wav if v == "clean" else farfield(wav, crng)
            rel = f"{meta['source']}/{v}/{rel_base}.wav"
            write_wav(out / rel, y)
            manifest.append({**meta, "variant": v, "path": rel, "dur": round(len(y) / SR, 3)})

    speakers = sorted(p for p in root.iterdir() if p.is_dir())
    for sp in speakers:
        spk = sp.name
        utts = sorted(sp.rglob("*.flac"))
        info = [(u, sf.info(str(u)).duration) for u in utts]
        rng_master.shuffle(info)
        long_ = [u for u, d in info if d >= 6.0]
        if len(long_) < 4:
            continue
        enrol = long_[:4]
        rest = [(u, d) for u, d in info if u not in enrol]
        for i, u in enumerate(enrol):
            x = trim_edges(load_16k(str(u)))[: int(8 * SR)]
            emit(x, f"{spk}_e{i}", {"source": "libri", "speaker": spk, "role": "enrol", "bucket": "l"},
                 variants=("farfield",))
        for b, (lo, hi) in BUCKETS.items():
            pool = [(u, d) for u, d in rest if d >= hi + 0.5]
            rng_master.shuffle(pool)
            for j, (u, _) in enumerate(pool[: a.probes_per_bucket]):
                x = trim_edges(load_16k(str(u)))
                ln = int(rng_master.uniform(lo, hi) * SR)
                if len(x) <= ln:
                    continue
                off = rng_master.randrange(0, len(x) - ln)
                emit(x[off : off + ln], f"{spk}_p{b}{j}", {"source": "libri", "speaker": spk, "role": "probe", "bucket": b})
            rest = [(u, d) for u, d in rest if (u, d) not in pool[: a.probes_per_bucket]]

    if a.kokoro_model and a.kokoro_voices and Path(a.kokoro_model).exists():
        from kokoro_onnx import Kokoro

        guard(a.min_avail_mb, a.wait_mem_s, "(kokoro)")
        k = Kokoro(a.kokoro_model, a.kokoro_voices)
        voices = [v for v in k.get_voices() if v[:2] in ("af", "am", "bf", "bm")]
        for v in voices:
            for ti, text in enumerate(KOKORO_TEXTS):
                lang = "en-gb" if v.startswith("b") else "en-us"
                samples, sr = k.create(text, voice=v, speed=1.0, lang=lang)
                x = np.asarray(samples, dtype=np.float32)
                if sr != SR:
                    from scipy.signal import resample_poly

                    g = gcd(sr, SR)
                    x = resample_poly(x, SR // g, sr // g).astype(np.float32)
                x = trim_edges(x)
                d = len(x) / SR
                b = "s" if d <= 2.0 else ("m" if d <= 4.0 else "l")
                emit(x, f"{v}_t{ti}", {"source": "kokoro", "speaker": v, "role": "probe", "bucket": b})
    else:
        print("kokoro model/voices not given or missing: skipping TTS negatives", file=sys.stderr)

    (out / "manifest.json").write_text(json.dumps(manifest))
    from collections import Counter

    print(json.dumps({
        "clips": len(manifest),
        "by_source_variant_role": Counter(f"{m['source']}/{m['variant']}/{m['role']}" for m in manifest),
        "by_bucket": Counter(m["bucket"] for m in manifest if m["role"] == "probe" and m["variant"] == "farfield"),
        "libri_speakers": len({m["speaker"] for m in manifest if m["source"] == "libri"}),
        "kokoro_voices": len({m["speaker"] for m in manifest if m["source"] == "kokoro"}),
    }, indent=1, default=dict))


def _stable_seed(seed: int, s: str) -> int:
    import zlib

    return (zlib.crc32(s.encode()) ^ (seed * 2654435761)) & 0xFFFFFFFF


# ----------------------------------------------------------------------------- corpus index
def cmd_index_corpus(a) -> None:
    import numpy as np
    import soundfile as sf

    root = Path(os.path.expanduser(a.corpus))
    files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix == ".wav")  # top level only
    rows = []
    for p in files:
        try:
            i = sf.info(str(p))
            rows.append((p.name, i.duration, p.stat().st_mtime))
        except Exception:
            continue
    order = sorted(range(len(rows)), key=lambda i: rows[i][2])
    sess = [0] * len(rows)
    cur = 0
    for k, i in enumerate(order):
        if k and rows[i][2] - rows[order[k - 1]][2] > a.session_gap_s:
            cur += 1
        sess[i] = cur
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "corpus_index.json").write_text(json.dumps({
        "root": str(root), "names": [r[0] for r in rows], "dur": [r[1] for r in rows],
        "session": sess,
    }))
    d = np.array([r[1] for r in rows])
    print(json.dumps({"clips": len(rows), "sessions": cur + 1,
                      "dur_p5_p50_p95": [round(float(np.percentile(d, q)), 2) for q in (5, 50, 95)],
                      "le_2s": int((d <= 2).sum())}))


# ----------------------------------------------------------------------------- models
def model_files_bytes(name: str, models_dir: Path, scratch: Path) -> int:
    kind, ref = MODELS[name][:2]
    if kind in ("sherpa", "ort"):
        return (models_dir / ref).stat().st_size
    if kind == "resemblyzer":
        import resemblyzer

        return (Path(resemblyzer.__file__).parent / "pretrained.pt").stat().st_size
    d = scratch / "ecapa_model"
    return sum(p.stat().st_size for p in d.glob("*") if p.is_file() and p.suffix in (".ckpt", ".pt"))


def load_model(name: str, threads: int, models_dir: Path, scratch: Path):
    """Return (embed_fn(wav16k float32)->1-D np array or None, info dict)."""
    import numpy as np

    kind, ref = MODELS[name][:2]
    if kind == "resemblyzer":
        import torch
        from resemblyzer import VoiceEncoder, preprocess_wav

        torch.set_num_threads(threads)
        enc = VoiceEncoder(device="cpu", verbose=False)  # exactly services/zoe-data/voice_speaker_id.py

        def f(w):
            pw = preprocess_wav(w)  # webrtcvad trim + volume normalise, as the daemon does
            if len(pw) < 1600:
                return None
            return enc.embed_utterance(pw)

        return f, {"runtime": f"torch {torch.__version__} (CPU wheel)"}
    if kind == "speechbrain":
        import torch
        from speechbrain.inference.speaker import EncoderClassifier

        torch.set_num_threads(threads)
        cl = EncoderClassifier.from_hparams(source=ref, savedir=str(scratch / "ecapa_model"), run_opts={"device": "cpu"})

        def f(w):
            with torch.inference_mode():
                return cl.encode_batch(torch.from_numpy(w)[None]).reshape(-1).numpy()

        return f, {"runtime": f"torch {torch.__version__} (CPU wheel) + speechbrain"}
    if kind == "ort":
        import onnxruntime as ort

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "voice"))
        from speaker_shadow_embed import kaldi_fbank  # numpy-only Kaldi fbank + per-utterance CMN

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = getattr(ort.GraphOptimizationLevel, "ORT_" + MODELS[name][2])
        sess = ort.InferenceSession(str(models_dir / ref), so, providers=["CPUExecutionProvider"])
        inp, outp = sess.get_inputs()[0].name, sess.get_outputs()[0].name

        def f(w):
            feat = kaldi_fbank(w)
            if feat.shape[0] < 10:
                return None
            return sess.run([outp], {inp: feat[None]})[0].reshape(-1)

        return f, {"runtime": f"onnxruntime {ort.__version__} (ORT_{MODELS[name][2]}) + numpy Kaldi fbank"}
    import sherpa_onnx

    cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(models_dir / ref), num_threads=threads, debug=False, provider="cpu")
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def f(w):
        s = ex.create_stream()
        s.accept_waveform(SR, w.astype(np.float32))
        s.input_finished()
        if not ex.is_ready(s):
            return None
        return np.asarray(ex.compute(s), dtype=np.float32)

    return f, {"runtime": f"sherpa-onnx {getattr(sherpa_onnx, '__version__', '?')} (ONNX Runtime CPU)"}


def _items(a):
    """Yield (group, key, path) for every clip to embed: owner corpus first, then impostors."""
    scratch = Path(a.scratch)
    items = []
    if a.corpus_index and Path(a.corpus_index).exists():
        idx = json.loads(Path(a.corpus_index).read_text())
        for n in idx["names"]:
            items.append(("corpus", n, str(Path(idx["root"]) / n)))
    man = json.loads((scratch / "impostors" / "manifest.json").read_text())
    for m in man:
        items.append(("imp", m["path"], str(scratch / "impostors" / m["path"])))
    return items


def cmd_embed(a) -> None:
    import numpy as np

    guard(a.min_avail_mb, a.wait_mem_s, f"(before loading {a.model})")
    scratch = Path(a.scratch)
    models_dir = scratch / "models"
    t0 = time.perf_counter()
    f, info = load_model(a.model, a.threads, models_dir, scratch)
    load_s = time.perf_counter() - t0
    rss_after_load = rss_mb()
    items = _items(a)
    # warm-up on a few clips, excluded from the timings (first-call JIT / allocator growth)
    for _, _, p in items[-3:]:
        f(load_16k(p))
    embs, secs, durs, groups = [], [], [], []
    dim = None
    for n, (g, key, p) in enumerate(items):
        if n % 25 == 0:
            guard(a.midrun_floor_mb, a.wait_mem_s * 4, f"(mid-run {a.model})")
        w = load_16k(p)
        t1 = time.perf_counter()
        try:
            e = f(w)
        except Exception:
            e = None
        dt = time.perf_counter() - t1
        if e is not None and dim is None:
            dim = int(np.asarray(e).size)
        embs.append(None if e is None else np.asarray(e, dtype=np.float32).reshape(-1))
        secs.append(dt)
        durs.append(len(w) / SR)
        groups.append(g)
    dim = dim or 0
    arr = np.full((len(items), dim), np.nan, dtype=np.float32)
    for i, e in enumerate(embs):
        if e is not None:
            arr[i] = e
    out = scratch / f"emb_{a.model}.npz"
    np.savez(out, emb=arr, secs=np.array(secs), dur=np.array(durs), group=np.array(groups),
             meta=json.dumps({
                 "model": a.model, "dim": dim, "threads": a.threads, "load_s": round(load_s, 2),
                 "rss_after_load_mb": round(rss_after_load, 1), "peak_rss_mb": round(peak_rss_mb(), 1),
                 "model_bytes": model_files_bytes(a.model, models_dir, scratch),
                 "failures": int(sum(e is None for e in embs)), **info}))
    print(f"{a.model}: {len(items)} clips, dim {dim}, failures {sum(e is None for e in embs)}, "
          f"load {load_s:.1f}s, peak RSS {peak_rss_mb():.0f} MB -> {out.name}")


def cmd_bench(a) -> None:
    import numpy as np

    guard(a.min_avail_mb, 0, "(bench)")
    scratch = Path(a.scratch)
    t0 = time.perf_counter()
    f, info = load_model(a.model, a.threads, scratch / "models", scratch)
    load_s = time.perf_counter() - t0
    rss_load = rss_mb()
    paths = sorted(Path(a.wavs).rglob("*.wav"))
    random.Random(1).shuffle(paths)
    paths = paths[: a.n]
    clips = [load_16k(str(p)) for p in paths]
    for w in clips[:3]:
        f(w)
    ts, ds = [], []
    for w in clips:
        t1 = time.perf_counter()
        f(w)
        ts.append(time.perf_counter() - t1)
        ds.append(len(w) / SR)
    ts, ds = np.array(ts), np.array(ds)
    slope, icpt = np.polyfit(ds, ts, 1)
    print(json.dumps({
        "model": a.model, "threads": a.threads, "n": len(ts), "mean_dur_s": round(float(ds.mean()), 2),
        "load_s": round(load_s, 2), "p50_ms": round(float(np.percentile(ts, 50) * 1e3), 1),
        "p95_ms": round(float(np.percentile(ts, 95) * 1e3), 1),
        "ms_per_audio_s": round(float(slope * 1e3), 1), "fixed_ms": round(float(icpt * 1e3), 1),
        "rss_after_load_mb": round(rss_load, 1), "peak_rss_mb": round(peak_rss_mb(), 1), **info}))


# ----------------------------------------------------------------------------- metrics
def _norm(x):
    import numpy as np

    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-9)


def det_point(gen, imp):
    """EER and the FRR at FAR = 1 % / 0.1 % (accept when score > threshold)."""
    import numpy as np

    gen = np.asarray(gen)
    imp = np.asarray(imp)
    res = {}
    for tag, far_t in (("far1", 0.01), ("far01", 0.001)):
        thr = np.quantile(imp, 1.0 - far_t, method="higher")
        res[f"frr_{tag}"] = float((gen <= thr).mean())
        res[f"thr_{tag}"] = float(thr)
        res[f"far_{tag}"] = float((imp > thr).mean())
    gs, is_ = np.sort(gen), np.sort(imp)
    cand = np.quantile(np.concatenate([gen, imp]), np.linspace(0, 1, 3001))
    far = 1.0 - np.searchsorted(is_, cand, side="right") / len(is_)  # P(imp > t)
    frr = np.searchsorted(gs, cand, side="right") / len(gs)  # P(gen <= t)
    k = int(np.argmin(np.abs(far - frr)))
    res["eer"] = float((far[k] + frr[k]) / 2)
    res["thr_eer"] = float(cand[k])
    return res


def _boot(G, I, rng, n_boot: int):
    """Bootstrap over probe CLIPS (columns); the enrolment draws stay paired."""
    import numpy as np

    out = {"eer": [], "frr_far1": [], "frr_far01": []}
    for _ in range(n_boot):
        gi = rng.integers(0, G.shape[1], G.shape[1])
        ii = rng.integers(0, I.shape[1], I.shape[1])
        r = det_point(G[:, gi].ravel(), I[:, ii].ravel())
        for k in out:
            out[k].append(r[k])
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in out.items()}


def dist_summary(x):
    import numpy as np

    x = np.asarray(x)
    q = np.percentile(x, [1, 5, 25, 50, 75, 95, 99])
    return {"mean": float(x.mean()), "sd": float(x.std()), "p1": q[0], "p5": q[1], "p25": q[2], "p50": q[3],
            "p75": q[4], "p95": q[5], "p99": q[6]}


def dprime(g, i):
    import numpy as np

    g, i = np.asarray(g), np.asarray(i)
    return float((g.mean() - i.mean()) / math.sqrt((g.var() + i.var()) / 2 + 1e-12))


def _kmeans2_largest(E, seed=0):
    """Spherical 2-means; returns a bool mask of the larger cluster (cluster 'A')."""
    import numpy as np
    from sklearn.cluster import KMeans

    X = _norm(E)
    lab = KMeans(n_clusters=2, n_init=10, random_state=seed).fit_predict(X)
    big = int(np.argmax(np.bincount(lab)))
    return lab == big


# ----------------------------------------------------------------------------- eval
def cmd_eval(a) -> None:
    import numpy as np

    scratch = Path(a.scratch)
    idx = json.loads(Path(a.corpus_index).read_text())
    man = json.loads((scratch / "impostors" / "manifest.json").read_text())
    n_c = len(idx["names"])
    cdur = np.array(idx["dur"])
    csess = np.array(idx["session"])
    names = [m for m in MODELS if (scratch / f"emb_{m}.npz").exists()]
    if a.models:
        names = [m for m in names if m in a.models.split(",")]
    data = {}
    for m in names:
        z = np.load(scratch / f"emb_{m}.npz", allow_pickle=False)
        data[m] = {"emb": z["emb"], "secs": z["secs"], "dur": z["dur"], "meta": json.loads(str(z["meta"]))}
    rng = np.random.default_rng(a.seed)

    def getE(m):
        """L2-normalised embeddings; with --center cohort, the PUBLIC cohort mean (far-field
        LibriSpeech enrolment clips, which are never trial probes) is subtracted first."""
        E = _norm(data[m]["emb"])
        if a.center == "cohort":
            coh = np.where((role == "enrol") & (src == "libri"))[0] + n_c
            mu = np.nanmean(E[coh], axis=0)
            E = _norm(E - mu)
        return E

    # ---- manifest views (impostor/public side)
    src = np.array([x["source"] for x in man])
    var = np.array([x["variant"] for x in man])
    role = np.array([x["role"] for x in man])
    bk = np.array([x["bucket"] for x in man])
    spk = np.array([x["speaker"] for x in man])
    bucket_of_dur = lambda d: np.where(d <= 2.0, "s", np.where(d <= 4.0, "m", "l"))  # noqa: E731
    cb = bucket_of_dur(cdur)

    # ---- owner-core: consensus of per-model 2-means "cluster A" (labels are INFERRED, see record)
    finite_all = np.ones(n_c, bool)
    for m in names:
        finite_all &= np.isfinite(data[m]["emb"][:n_c]).all(axis=1)
    inA = {}
    cluster_report = {}
    for m in names:
        E = data[m]["emb"][:n_c]
        mask = np.zeros(n_c, bool)
        mask[finite_all] = _kmeans2_largest(E[finite_all])
        inA[m] = mask
        cluster_report[m] = round(float(mask[finite_all].mean()), 3)
    # The consensus is voted by the models that separate LABELLED public speakers well, not by
    # the incumbent (would favour/penalise it) nor by a model that fails the public check.
    core_names = [m for m in (a.core_models.split(",") if a.core_models else names) if m in names]
    votes = np.sum([inA[m] for m in core_names], axis=0)
    cores = {"strict": finite_all & (votes == len(core_names)),
             "majority": finite_all & (votes > len(core_names) / 2)}

    def pooled(m, core_name, n_draws=30):
        core_mask = cores[core_name]
        E = getE(m)
        Ec, Ei = E[:n_c], E[n_c:]
        ok = core_mask & (cdur >= 2.0)
        sess_ids = [s for s in np.unique(csess[ok]) if (ok & (csess == s)).sum() >= 3]
        gcols = np.where(core_mask)[0]  # genuine probe columns
        imp_ff = np.where((role == "probe") & (var == "farfield"))[0]
        imp_cl = np.where((role == "probe") & (var == "clean") & (src == "libri"))[0]
        r2 = np.random.default_rng(a.seed + 1)  # SAME enrolment draws for every model
        G = np.full((n_draws, len(gcols)), np.nan)
        If = np.zeros((n_draws, len(imp_ff)))
        Ic = np.zeros((n_draws, len(imp_cl)))
        draws = []
        for d in range(n_draws):
            s = sess_ids[r2.integers(len(sess_ids))]
            en = r2.choice(np.where(ok & (csess == s))[0], 3, replace=False)
            draws.append(en)
            c = _norm(Ec[en].mean(axis=0))
            keep = csess[gcols] != s  # leave the enrolment session out
            G[d, keep] = np.nan_to_num(Ec[gcols[keep]] @ c, nan=-1.0)
            If[d] = np.nan_to_num(Ei[imp_ff] @ c, nan=-1.0)
            Ic[d] = np.nan_to_num(Ei[imp_cl] @ c, nan=-1.0)
        flat = lambda M, cols=slice(None): M[:, cols][np.isfinite(M[:, cols])]  # noqa: E731
        Gall, Iall = flat(G), If.ravel()
        out = {"n_enrol_draws": n_draws, "n_genuine_probe_clips": int(len(gcols))}
        out["all"] = det_point(Gall, Iall)
        out["all"]["n_gen"], out["all"]["n_imp"] = int(len(Gall)), int(len(Iall))
        out["dist_gen"], out["dist_imp"] = dist_summary(Gall), dist_summary(Iall)
        out["dprime"] = dprime(Gall, Iall)
        out["clean_impostors"] = det_point(Gall, Ic.ravel())
        out["rate_at_0p82"] = {"frr": float((Gall <= 0.82).mean()), "far": float((Iall > 0.82).mean())}
        gb = bucket_of_dur(cdur[gcols])
        for b in "slm":
            g = flat(G, gb == b)
            ii = If[:, bk[imp_ff] == b].ravel()
            if len(g) >= 20 and len(ii) >= 20:
                out[f"bucket_{b}"] = det_point(g, ii)
                out[f"bucket_{b}"]["n_gen"], out[f"bucket_{b}"]["n_imp"] = int(len(g)), int(len(ii))
        # bootstrap over probe CLIPS (columns), enrolment draws kept paired
        rb = np.random.default_rng(a.seed + 2)
        ci = {"eer": [], "frr_far1": [], "frr_far01": []}
        for _ in range(a.boot):
            gi = rb.integers(0, G.shape[1], G.shape[1])
            ii = rb.integers(0, If.shape[1], If.shape[1])
            r = det_point(flat(G, gi), If[:, ii].ravel())
            for k in ci:
                ci[k].append(r[k])
        out["ci95"] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in ci.items()}
        # corpus clips outside the core (unlabelled): share accepted at this model's FAR=1% threshold
        rest = np.where(finite_all & ~core_mask)[0]
        if len(rest):
            out["unlabelled_rest_accept_at_far1"] = float(np.mean(
                [(Ec[rest] @ _norm(Ec[en].mean(axis=0)) > out["all"]["thr_far1"]).mean() for en in draws]))
        pool = np.concatenate([Gall, Iall])
        np.random.default_rng(a.seed + 3).shuffle(pool)
        out["shuffle_control_eer"] = det_point(pool[: len(Gall)], pool[len(Gall):])["eer"]
        return out

    def public(m):
        E = getE(m)[n_c:]
        e_idx = np.where((role == "enrol") & (src == "libri"))[0]
        speakers = sorted(set(spk[e_idx]))
        out = {}
        for variant in ("farfield", "clean"):
            p_idx = np.where((role == "probe") & (src == "libri") & (var == variant))[0]
            Gs, Is, Gb, Ib = [], [], {b: [] for b in "slm"}, {b: [] for b in "slm"}
            for hold in range(4):
                cents = []
                for s in speakers:
                    ei = [i for i in e_idx if spk[i] == s]
                    use = [e for k, e in enumerate(ei) if k != hold % len(ei)]
                    cents.append(_norm(E[use].mean(axis=0)))
                C = np.stack(cents)
                S = np.nan_to_num(E[p_idx] @ C.T, nan=-1.0)  # probes x speakers
                same = (spk[p_idx][:, None] == np.array(speakers)[None, :])
                g, i_ = S[same], S[~same]
                Gs.append(g)
                Is.append(i_)
                bp = np.repeat(bk[p_idx][:, None], len(speakers), axis=1)
                for b in "slm":
                    Gb[b].append(S[same & (bp == b)])
                    Ib[b].append(S[(~same) & (bp == b)])
            G, I = np.concatenate(Gs), np.concatenate(Is)
            out[variant] = det_point(G, I)
            out[variant]["dprime"] = dprime(G, I)
            out[variant]["n_gen"], out[variant]["n_imp"] = int(len(G)), int(len(I))
            for b in "slm":
                out[variant][f"bucket_{b}"] = det_point(np.concatenate(Gb[b]), np.concatenate(Ib[b]))
        return out

    def timing(m):
        d = data[m]
        sec, dur = d["secs"][:n_c], d["dur"][:n_c]
        ok = finite_all
        t = sec[ok]
        du = dur[ok]
        slope, icpt = np.polyfit(du, t, 1)
        res = {"p50_ms": float(np.percentile(t, 50) * 1e3), "p95_ms": float(np.percentile(t, 95) * 1e3),
               "mean_ms": float(t.mean() * 1e3), "ms_per_audio_s": float(slope * 1e3),
               "fixed_ms": float(icpt * 1e3)}
        for b in "slm":
            tb = t[bucket_of_dur(du) == b]
            if len(tb):
                res[f"p50_ms_{b}"] = float(np.percentile(tb, 50) * 1e3)
        return res

    report = {"center": a.center, "models": names, "core_models": core_names, "core_sizes": {k: int(v.sum()) for k, v in cores.items()},
              "corpus_clips": n_c, "finite_all": int(finite_all.sum()),
              "cluster_A_fraction": cluster_report, "per_model": {}}
    for m in names:
        meta = data[m]["meta"]
        report["per_model"][m] = {
            "meta": meta, "timing": timing(m), "public": public(m),
            "owner_strict": pooled(m, "strict"), "owner_majority": pooled(m, "majority"),
        }
    Path(a.json).write_text(json.dumps(report, indent=1, default=float))
    _print_tables(report)


def _pct(x, d=1):
    return f"{100 * x:.{d}f}%"


def _print_tables(r) -> None:
    ms = r["models"]
    print(f"\n==== scoring: {r['center']} cosine ====")
    print(f"corpus clips {r['corpus_clips']}  embedded-by-all {r['finite_all']}  "
          f"core strict {r['core_sizes']['strict']}  core majority {r['core_sizes']['majority']}")
    print("cluster-A fraction per model:", r["cluster_A_fraction"])
    for title, key in (("OWNER corpus (strict consensus core)", "owner_strict"),
                       ("OWNER corpus (majority core)", "owner_majority")):
        print(f"\n### {title}: all probes vs far-field public + Kokoro impostors")
        print("| model | EER | FRR@FAR1% | FRR@FAR0.1% | d' | FRR/FAR@0.82 | shuffle EER |")
        print("|---|---|---|---|---|---|---|")
        for m in ms:
            o = r["per_model"][m][key]
            a = o["all"]
            print(f"| {m} | {_pct(a['eer'])} | {_pct(a['frr_far1'])} | {_pct(a['frr_far01'])} | {o['dprime']:.2f} | "
                  f"{_pct(o['rate_at_0p82']['frr'])} / {_pct(o['rate_at_0p82']['far'], 2)} | {_pct(o['shuffle_control_eer'])} |")
        for b in "slm":
            print(f"\n{BUCKET_NAMES[b]} probes only ({key})")
            print("| model | EER | FRR@FAR1% | FRR@FAR0.1% | n gen / imp |")
            print("|---|---|---|---|---|")
            for m in ms:
                a = r["per_model"][m][key].get(f"bucket_{b}")
                if a:
                    print(f"| {m} | {_pct(a['eer'])} | {_pct(a['frr_far1'])} | {_pct(a['frr_far01'])} | {a['n_gen']} / {a['n_imp']} |")
    print("\n### PUBLIC LibriSpeech speakers (labels certain), far-field enrol + probes")
    print("| model | EER | FRR@FAR1% | FRR@FAR0.1% | <=2s EER | <=2s FRR@FAR1% | clean-probe EER |")
    print("|---|---|---|---|---|---|---|")
    for m in ms:
        p = r["per_model"][m]["public"]
        f, c = p["farfield"], p["clean"]
        print(f"| {m} | {_pct(f['eer'])} | {_pct(f['frr_far1'])} | {_pct(f['frr_far01'])} | "
              f"{_pct(f['bucket_s']['eer'])} | {_pct(f['bucket_s']['frr_far1'])} | {_pct(c['eer'])} |")
    print("\n### Cost (Jetson CPU, per clip incl. features)")
    print("| model | size MB | dim | p50 ms | p95 ms | ms per audio-s | load s | RSS after load MB | peak RSS MB | runtime |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for m in ms:
        t = r["per_model"][m]["timing"]
        me = r["per_model"][m]["meta"]
        print(f"| {m} | {me['model_bytes'] / 1e6:.1f} | {me['dim']} | {t['p50_ms']:.0f} | {t['p95_ms']:.0f} | "
              f"{t['ms_per_audio_s']:.0f} | {me['load_s']} | {me['rss_after_load_mb']:.0f} | {me['peak_rss_mb']:.0f} | {me['runtime']} |")


# ----------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", default=os.path.expanduser("~/.cache/zoe-speaker-bakeoff"))
    ap.add_argument("--min-avail-mb", type=float, default=2048)
    ap.add_argument("--wait-mem-s", type=float, default=0, help="wait this long for MemAvailable before giving up")
    ap.add_argument("--midrun-floor-mb", type=float, default=1024,
                    help="mid-run pause floor (the process's own RSS already counts against MemAvailable)")
    ap.add_argument("--seed", type=int, default=7)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prep")
    p.add_argument("--libri", required=True, help="LibriSpeech/test-clean directory")
    p.add_argument("--out", required=True, help="output dir (scratch/impostors)")
    p.add_argument("--kokoro-model")
    p.add_argument("--kokoro-voices")
    p.add_argument("--probes-per-bucket", type=int, default=4)
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("index-corpus")
    p.add_argument("--corpus", default="~/.zoe-voice-samples")
    p.add_argument("--out", required=True)
    p.add_argument("--session-gap-s", type=float, default=1800)
    p.set_defaults(fn=cmd_index_corpus)

    p = sub.add_parser("embed")
    p.add_argument("--model", required=True, choices=sorted(MODELS))
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--corpus-index")
    p.set_defaults(fn=cmd_embed)

    p = sub.add_parser("bench")
    p.add_argument("--model", required=True, choices=sorted(MODELS))
    p.add_argument("--wavs", required=True)
    p.add_argument("--n", type=int, default=60)
    p.add_argument("--threads", type=int, default=2)
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("score")
    p.add_argument("--corpus-index", required=True)
    p.add_argument("--models")
    p.add_argument("--boot", type=int, default=200)
    p.add_argument("--core-models", help="comma list of models whose 2-means clusters vote on the owner core")
    p.add_argument("--center", choices=("raw", "cohort"), default="raw",
                   help="raw cosine (what Zoe runs today) or cosine after subtracting a public-cohort mean")
    p.add_argument("--json", default="bakeoff_results.json")
    p.set_defaults(fn=cmd_eval)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
