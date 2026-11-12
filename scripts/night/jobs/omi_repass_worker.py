#!/usr/bin/env python3
"""The ASR + speaker-embedding half of the pendant night re-pass, as a STANDALONE process in its own venv (``~/.zoe/venvs/omi-repass``).

It exists so torch, transformers, faster-whisper and onnxruntime (and their GBs) never enter zoe-data or the night window: the job starts it under
``systemd-run --user --scope -p MemoryMax=... -p MemorySwapMax=0 nice -n 10`` while Zoe sleeps, and reads a JSONL back. It knows nothing about
the database, members, names or labels - the parent (``services/zoe-data/omi_repass.py``) owns every decision.

    omi_repass_worker.py --manifest M.json --out OUT.jsonl      transcribe + embed each item; one JSON line per finished item
    omi_repass_worker.py --probe-gpu                            print what the GPU path could do on this stack (one JSON line), change nothing

Manifest: {"asr": "qwen3-asr"|"parakeet-tdt"|"faster-whisper:large-v3"|... (ASR registry), "diar": "resnet34"|"none" (DIAR registry), "threads": 4, "gpu": true, "models_dir": "...", "chunk_s": 14,
           "hint": "Names: ..." (only faster-whisper uses it: the transformers engines are NOT prompt-biased, the parent normalises names afterwards), "deadline_epoch": 1.7e9, "rtf": 1.0, "zoe_clips": [paths], "items": [{"id": "...", "path": "...", "hint": "(optional, per item)", "diar": false (optional: no speaker windows for this item)}]}
Output lines: {"zoe_ref": [...]} once (when zoe_clips were given), then per item {"id", "ok": true, "dur_s", "speech_s", "asr_s", "emb_s",
           "words": [[w, t0, t1, p]...], "windows": [[t0, t1, [192 floats], rms]...]} or {"id", "ok": false, "why"}, then {"done": true, ...}.
An item is never half-written: if the deadline passes mid-file the item is reported ``why: "deadline"`` and the parent carries it to the next night.
Offline by construction (HF_HUB_OFFLINE=1; the transformers engines load from ``models_dir`` = ~/.zoe/models/omi-repass, fetched at pinned revisions by setup_omi_repass.sh; faster-whisper /
the speaker model is the panel's pinned ResNet34 artefact). Audio and words are never logged.
Exit 0 ok, 3 a model/package is missing, 1 any other failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import resource
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

#: PINNED snapshots (the revisions the bake-off scored). Systran is the only HF org the ASR weights may come from; the speaker model is
MODELS = {
    "large-v3": ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", "c3058b475261292e64a0412df1d2681c06260fab"),
}
#: the SPEAKER step is the panel's own WeSpeaker ResNet34 (int8, pinned artefact + self-test in scripts/setup/zoe_speaker_resnet34.py): one speaker space for the panel, named guests, the first pass
#: and this pass. Measured on hand-checked turns: 97-100 % with per-channel mean removal (ECAPA 89-95 %, the shipped labels 60 %).
R34_SCRIPT = Path(__file__).resolve().parents[2] / "setup" / "zoe_speaker_resnet34.py"
R34_DIR = os.path.expanduser("~/.cache/zoe-speaker-resnet34")
LONG_TURN_S = 8.0          # a turn up to this long is ONE embedding; a longer one gets 1.5 s windows (hop 0.75) that vote. Never 20 s slabs.
MIN_UNIT_S = 0.5
#: the round-2 bake-off winners (official repos; revisions pinned in scripts/setup/setup_omi_repass.sh): directory names under ``models_dir``
TRANSFORMERS_DIRS = {"qwen3-asr": "Qwen3-ASR-1.7B-hf", "parakeet-tdt": "parakeet-tdt-0.6b-v3", "granite-speech": "granite-speech-5.0-2b"}
MODELS_DIR = os.path.expanduser("~/.zoe/models/omi-repass")
CHUNK_MAX_S = 14.0         # the bake-off's VAD chunk length; the parent passes ``chunk_s`` (ZOE_OMI_REPASS_CHUNK_S); never above 30
RATE = 16000
WIN_S, HOP_S = 1.5, 0.75


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def gpu_peak_mb():
    try:
        import torch
        return round(torch.cuda.max_memory_allocated() / 1e6) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001
        return None


def snapshot(repo: str, rev: str) -> str:
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, revision=rev, local_files_only=True)


def probe_gpu() -> int:
    out: dict = {"ctranslate2_cuda_devices": None, "torch_cuda": None, "note": ""}
    try:
        import ctranslate2
        out["ctranslate2"] = ctranslate2.__version__
        out["ctranslate2_cuda_devices"] = int(ctranslate2.get_cuda_device_count())
        out["ctranslate2_compute_types_cpu"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
    except Exception as exc:  # noqa: BLE001
        out["ctranslate2_error"] = type(exc).__name__
    try:
        import torch
        out["torch"] = torch.__version__
        out["torch_cuda"] = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        out["torch_error"] = type(exc).__name__
    out["usable_gpu_path"] = bool(out["ctranslate2_cuda_devices"])
    print(json.dumps(out))
    return 0


class AsrBackend:
    """The ASR seam. A backend turns 16 kHz mono float32 audio into WORDS with times and confidences, steered by a name hint. Nothing else in the re-pass
    knows which engine ran: add one by subclassing and registering it in ``ASR`` (and declaring its peak RSS / RTF in ``omi_repass.BACKENDS``)."""
    name = ""

    def __init__(self, threads: int, gpu: bool):
        self.threads, self.gpu = threads, gpu

    def load(self) -> None:
        raise NotImplementedError

    def device(self) -> str:
        return "cpu"

    def transcribe(self, x, hint: str, deadline) -> "tuple[list, bool]":
        """-> (words [[word, t0, t1, p]...], late). ``late`` True = the deadline passed mid-file (the caller carries the file; words are discarded)."""
        raise NotImplementedError


class FasterWhisper(AsrBackend):
    """faster-whisper (CTranslate2): VAD, word timestamps, beam 5, the hint as initial_prompt. First implementation; the bake-off's winner."""

    def __init__(self, threads: int, gpu: bool, model: str):
        super().__init__(threads, gpu)
        self.model_name, self.use_gpu, self.model = model, False, None

    def load(self) -> None:
        from faster_whisper import WhisperModel
        if self.gpu:
            import ctranslate2
            self.use_gpu = ctranslate2.get_cuda_device_count() > 0
        self.model = WhisperModel(snapshot(*MODELS[self.model_name]), device="cuda" if self.use_gpu else "cpu",
                                  compute_type="float16" if self.use_gpu else "int8", cpu_threads=self.threads)

    def device(self) -> str:
        return "cuda" if self.use_gpu else "cpu"

    def transcribe(self, x, hint: str, deadline):
        segs, _info = self.model.transcribe(x, language="en", vad_filter=True, word_timestamps=True, beam_size=5,
                                            condition_on_previous_text=False, initial_prompt=hint or None)
        words = []
        for s in segs:
            if deadline and time.time() > deadline:
                return [], True
            words.extend([w.word, round(w.start, 3), round(w.end, 3), round(w.probability, 3)] for w in (s.words or []))
        return words, False


def vad_chunks(x) -> list:
    """[(t0, t1)] seconds: the bake-off's chunking (silero via faster-whisper; speech spans of at most ``CHUNK_MAX_S``, gaps under 0.4 s merged, spans under 0.3 s dropped),
    so every engine sees the same pieces."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(x, VadOptions(min_silence_duration_ms=250, max_speech_duration_s=CHUNK_MAX_S, speech_pad_ms=150, threshold=0.4))
    merged: list = []
    for t in ts:
        s, e = t["start"] / RATE, t["end"] / RATE
        if merged and s - merged[-1][1] < 0.4 and e - merged[-1][0] <= CHUNK_MAX_S:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s, e) for s, e in merged if e - s >= 0.3]


class ChunkedAsr(AsrBackend):
    """An engine that returns TEXT per VAD chunk (no word times): the words of a chunk are spread evenly across it with a flat 0.6 confidence; who spoke still comes from the
    speaker units. No name hint is given to the engine (the bake-off: no candidate spells a household name unbiased, and a keyword prompt made one invent a name on non-speech) -
    the parent normalises names afterwards from the household / named-guest lexicon."""
    dev = "cpu"

    def run_chunk(self, x) -> str:
        raise NotImplementedError

    def device(self) -> str:
        return self.dev

    def _device(self, torch) -> str:
        return "cuda" if self.gpu and torch.cuda.is_available() else "cpu"

    last: "list | None" = None          # [[t0, t1, text]] of the last transcribe*: the per-chunk hypothesis (fusion / the contender ledger need it)

    def transcribe(self, x, hint: str, deadline):
        return self.transcribe_chunks(x, vad_chunks(x), deadline)

    def transcribe_chunks(self, x, chunks, deadline):
        """Run THESE chunks (the parent hands a later engine the pivot's chunks, or a rotating sample of them), so every engine's hypothesis lines up chunk for chunk."""
        words: list = []
        rows: list = []
        self.last = None
        for s, e in chunks:
            if deadline and time.time() > deadline:
                return [], True
            text = " ".join((self.run_chunk(x[int(s * RATE):int(e * RATE)]) or "").split())
            rows.append([round(s, 3), round(e, 3), text])
            toks = text.split()
            for i, w in enumerate(toks):
                a, b = s + (e - s) * i / len(toks), s + (e - s) * (i + 1) / len(toks)
                words.append([(" " if words else "") + w, round(a, 3), round(b, 3), 0.6])
        self.last = rows
        return words, False


class Qwen3Asr(ChunkedAsr):
    """Qwen/Qwen3-ASR-1.7B-hf via transformers (the bake-off winner): bfloat16 on the GPU with the default torch allocator. Measured: GPU RTF 0.42 per speech second, ~4.0 GB GPU, ~8.7 GB process RSS, ~14 s load."""

    def load(self) -> None:
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        path = os.path.join(MODELS_DIR, TRANSFORMERS_DIRS["qwen3-asr"])
        if not os.path.isdir(path):
            raise FileNotFoundError("qwen3-asr weights missing")
        self.torch, self.dev = torch, self._device(torch)
        self.p = AutoProcessor.from_pretrained(path)
        self.m = AutoModelForMultimodalLM.from_pretrained(path, dtype=torch.bfloat16).to(self.dev).eval()

    def run_chunk(self, x) -> str:
        inp = self.p.apply_transcription_request(audio=x, language="English").to(self.dev, self.m.dtype)
        with self.torch.inference_mode():
            o = self.m.generate(**inp, max_new_tokens=300, do_sample=False)
        return self.p.decode(o[:, inp["input_ids"].shape[1]:], return_format="transcription_only")[0].strip()


class ParakeetTdt(ChunkedAsr):
    """nvidia/parakeet-tdt-0.6b-v3 via transformers (the budget fallback): GPU RTF 0.08, ~5.7 GB process RSS. ``generate()`` returns an output object on the GPU path: decode ``.sequences``."""

    def load(self) -> None:
        import torch
        from transformers import AutoModelForTDT, AutoProcessor
        path = os.path.join(MODELS_DIR, TRANSFORMERS_DIRS["parakeet-tdt"])
        if not os.path.isdir(path):
            raise FileNotFoundError("parakeet-tdt weights missing")
        self.torch, self.dev = torch, self._device(torch)
        self.p = AutoProcessor.from_pretrained(path)
        self.m = AutoModelForTDT.from_pretrained(path, dtype=torch.float32).to(self.dev).eval()

    def run_chunk(self, x) -> str:
        inp = self.p(x, sampling_rate=RATE, return_tensors="pt").to(self.dev)
        with self.torch.inference_mode():
            o = self.m.generate(**inp)
        seq = getattr(o, "sequences", o)
        return self.p.batch_decode(seq, skip_special_tokens=True)[0].strip()


class WhisperGpu(ChunkedAsr):
    """faster-whisper on the GPU through the hand-built CUDA CTranslate2 (``ZOE_OMI_REPASS_CT2_DIR``: the parent puts its pylib / lib on PYTHONPATH / LD_LIBRARY_PATH before this
    process starts). Chunk by chunk, no VAD of its own, the pinned Systran snapshot. The third ROVER voter (distil-large-v3) and the large-v3 evaluation contender."""

    def __init__(self, threads: int, gpu: bool, model: str):
        super().__init__(threads, gpu)
        self.model_name = model

    def load(self) -> None:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            raise RuntimeError("no CUDA CTranslate2 (set ZOE_OMI_REPASS_CT2_DIR to the CUDA build)")
        from faster_whisper import WhisperModel
        self.dev = "cuda"
        self.m = WhisperModel(snapshot(*MODELS[self.model_name]), device="cuda", compute_type="float16")

    def run_chunk(self, x) -> str:
        segs, _ = self.m.transcribe(x, language="en", beam_size=5, condition_on_previous_text=False, vad_filter=False)
        return " ".join(s.text.strip() for s in segs)


class GraniteSpeech(ChunkedAsr):
    """ibm-granite/granite-speech-5.0-2b via transformers (an evaluation contender). Prompted with the PLAIN transcription instruction: its keyword prompt invented a name on non-speech."""

    def load(self) -> None:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        path = os.path.join(MODELS_DIR, TRANSFORMERS_DIRS["granite-speech"])
        if not os.path.isdir(path):
            raise FileNotFoundError("granite-speech weights missing")
        self.torch, self.dev = torch, self._device(torch)
        self.p = AutoProcessor.from_pretrained(path)
        self.tok = self.p.tokenizer
        self.m = AutoModelForSpeechSeq2Seq.from_pretrained(path, dtype=torch.bfloat16).to(self.dev).eval()

    def run_chunk(self, x) -> str:
        chat = [{"role": "user", "content": "<|audio|>transcribe the speech with proper punctuation and capitalization."}]
        prompt = self.tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)
        inp = self.p(prompt, self.torch.from_numpy(x)[None], device=self.dev, return_tensors="pt").to(self.dev)
        with self.torch.inference_mode():
            o = self.m.generate(**inp, max_new_tokens=300, do_sample=False, num_beams=1)
        return self.tok.batch_decode(o[:, inp["input_ids"].shape[1]:], add_special_tokens=False, skip_special_tokens=True)[0].strip()


#: ASR registry: backend id -> factory(threads, gpu). Ids are what ``ZOE_OMI_NIGHT_ASR`` / the manifest's ``asr`` name.
ASR = {
    "granite-speech": lambda t, g: GraniteSpeech(t, g),
    "whisper-gpu:large-v3": lambda t, g: WhisperGpu(t, g, "large-v3"),
    "whisper-gpu:distil-large-v3": lambda t, g: WhisperGpu(t, g, "distil-large-v3"),
    "qwen3-asr": lambda t, g: Qwen3Asr(t, g),
    "parakeet-tdt": lambda t, g: ParakeetTdt(t, g),
    "faster-whisper:large-v3": lambda t, g: FasterWhisper(t, g, "large-v3"),
    "faster-whisper:distil-large-v3": lambda t, g: FasterWhisper(t, g, "distil-large-v3"),
}
#: diarisation / speaker-window registry (the seam: ``windows(x, speech) -> [(t0, t1, emb, rms)]``); the parent matches the embeddings, so a
#: segmenting engine (pyannote, Sortformer) can slot in by returning windows of its own turns.
DIAR = {"resnet34": lambda t: ResNet34Units(t)}


def read_audio(path: str, resample: bool = False):
    """``resample``: for the Kokoro reference clips only (the sidecar writes 24 kHz WAVs); pendant audio must already be 16 kHz and anything else is refused."""
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    if sr != RATE:
        if not resample or sr <= 0:
            raise ValueError(f"rate {sr}")
        try:
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(int(RATE), int(sr))
            x = resample_poly(x, int(RATE) // g, int(sr) // g).astype("float32")
        except ImportError:
            n = int(len(x) * RATE / sr)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype("float32")
    return np.ascontiguousarray(x)


class ResNet34Units:
    """Speaker units from the ASR's VAD turns. The CAM++ seam is another entry in ``DIAR`` (the bake-off kept it optional)."""

    def __init__(self, threads: int):
        spec = importlib.util.spec_from_file_location("zoe_speaker_resnet34_repass", R34_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.emb = mod.Resnet34Embedder.load(R34_DIR, artifacts_path=str(R34_SCRIPT.parent / mod.ARTIFACTS_FILENAME), threads=1)

    def one(self, x):
        return self.emb.embed(x)          # the cohort-centred unit embedding the profiles live in, or None for a clip too short to embed

    def units(self, x, chunks):
        """[(t0, t1, emb, rms)]: one embedding per turn up to ``LONG_TURN_S``; a longer turn is cut into 1.5 s windows (hop 0.75) whose votes the parent combines."""
        out = []

        def add(a_s, b_s):
            seg = x[int(a_s * RATE):int(b_s * RATE)]
            e = self.one(seg)
            if e is not None:
                out.append((a_s, b_s, e, float((seg.astype("float64") ** 2).mean() ** 0.5)))

        for s_, e_ in chunks:
            if e_ - s_ <= LONG_TURN_S:
                if e_ - s_ >= MIN_UNIT_S:
                    add(s_, e_)
                continue
            t = s_
            while t + MIN_UNIT_S <= e_:
                add(t, min(t + WIN_S, e_))
                t += HOP_S
        return out


def zoe_reference(emb: Embedder, clips: list):
    import numpy as np
    es = []
    for p in clips[:40]:
        try:
            x = read_audio(p, resample=True)
        except Exception:  # noqa: BLE001
            continue
        if len(x) > 2.5 * RATE:
            e = emb.one(x[: 12 * RATE])
            if e is not None:
                es.append(e)
    if not es:
        return None
    m = np.stack([e / (np.linalg.norm(e) or 1.0) for e in es]).mean(0)
    return (m / (np.linalg.norm(m) or 1.0)).tolist()


_SAFE_REASON = re.compile(r"cuda|ctranslate2|out of memory|no such file|not found|cannot open|shared object|symbol|unsupported|driver|compiled|no module named|incompatible|version|invalid (?:model|device)", re.I)


def redact_exc(exc: BaseException, limit: int = 160) -> str:
    """An exception's message made safe for a log line: ALLOWLIST, not blocklist. Paths and URLs become <path>/<url>, key=value pairs and long runs go, and the message is kept only when it
    reads like an environment/setup reason (CUDA, missing library, OOM...); anything else is withheld (the class name is always logged by the caller). One line, capped."""
    t = " ".join(str(exc).split())
    t = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", t, flags=re.I)
    t = re.sub(r"(?:~|\.{0,2})?/[^\s'\"]*", "<path>", t)
    t = re.sub(r"(?i)\b\w*(token|secret|passw\w*|key|authorization|bearer|credential)\w*\b\s*[=:]?\s*\S+", "<redacted>", t)
    t = re.sub(r"\b[A-Za-z0-9+/_-]{24,}\b", "<redacted>", t)
    if not _SAFE_REASON.search(t):
        return "(message withheld)"
    return t[:limit]


def run(manifest: dict, out_path: str) -> int:
    name = manifest.get("asr", "qwen3-asr")
    global MODELS_DIR, CHUNK_MAX_S, R34_DIR
    MODELS_DIR = manifest.get("models_dir") or MODELS_DIR
    R34_DIR = manifest.get("r34_dir") or R34_DIR
    CHUNK_MAX_S = max(2.0, min(30.0, float(manifest.get("chunk_s") or CHUNK_MAX_S)))
    diar = manifest.get("diar", "resnet34")
    if name not in ASR or (diar != "none" and diar not in DIAR):
        print(f"unknown backend {name}/{diar}", file=sys.stderr)
        return 1
    threads = int(manifest.get("threads", 4))
    deadline = float(manifest.get("deadline_epoch") or 0) or None
    rtf = float(manifest.get("rtf") or 1.0)
    t0 = time.time()
    try:
        asr = ASR[name](threads, bool(manifest.get("gpu")))
        asr.load()
        emb = DIAR[diar](threads) if diar != "none" else None          # a later voter / contender needs the pivot's chunks only: no speaker units (and no R34 session in memory)
    except Exception as exc:  # noqa: BLE001 - a model that is not in the cache is a setup problem, not a data one
        print(f"model unavailable: {name}: {type(exc).__name__}: {redact_exc(exc)}", file=sys.stderr)          # the ENGINE and the real reason (2026-11-11: a bare "RuntimeError" hid a missing CUDA CTranslate2)
        return 3
    load_s = time.time() - t0
    done = carried = failed = 0
    audio_s = elapsed = 0.0
    with open(out_path, "a", encoding="utf-8") as out:
        os.chmod(out_path, 0o600)
        if manifest.get("zoe_clips") and emb is not None:
            ref = zoe_reference(emb, manifest["zoe_clips"])
            if ref:
                out.write(json.dumps({"zoe_ref": ref}) + "\n")
                out.flush()
        for item in manifest["items"]:
            rid = item["id"]
            try:
                x = read_audio(item["path"])
                given = item.get("chunks")
                dur = sum(float(c[1]) - float(c[0]) for c in given) if given is not None else len(x) / RATE
                if deadline and time.time() + dur * rtf * 0.5 > deadline:      # not enough time left to be likely to finish it: carry it, start nothing new
                    carried += 1
                    out.write(json.dumps({"id": rid, "ok": False, "why": "deadline"}) + "\n")
                    continue
                ta = time.time()
                asr.last = None
                if given is not None and hasattr(asr, "transcribe_chunks"):
                    words, late = asr.transcribe_chunks(x, [(float(c[0]), float(c[1])) for c in given], deadline)
                else:
                    words, late = asr.transcribe(x, item.get("hint") or manifest.get("hint") or "", deadline)
                if late:
                    carried += 1
                    out.write(json.dumps({"id": rid, "ok": False, "why": "deadline"}) + "\n")
                    out.flush()
                    continue
                asr_s = time.time() - ta
                te = time.time()
                if emb is not None and item.get("diar", True) is not False:       # per item: the member turned "tag voices on my recordings" off
                    turns = [(c[0], c[1]) for c in asr.last] if asr.last else vad_chunks(x)          # the ASR's own VAD turns: one segmentation for words and speakers
                    wins = emb.units(x, turns)
                    speech_s = round(sum(b - a for a, b in turns), 2)
                else:
                    wins, speech_s = [], round(dur, 2)
                emb_s = time.time() - te
                out.write(json.dumps({"id": rid, "ok": True, "dur_s": round(len(x) / RATE, 2), "speech_s": speech_s,
                                      "asr_s": round(asr_s, 2), "emb_s": round(emb_s, 2), "words": words, "chunks": getattr(asr, "last", None),
                                      "windows": [[round(a, 3), round(b, 3), [round(float(v), 4) for v in e], round(r, 5)] for a, b, e, r in wins]}) + "\n")
                done += 1
                audio_s += dur
                elapsed += asr_s + emb_s
            except Exception as exc:  # noqa: BLE001 - one bad file never costs the rest
                failed += 1
                out.write(json.dumps({"id": rid, "ok": False, "why": f"error:{type(exc).__name__}"}) + "\n")
            out.flush()
        out.write(json.dumps({"done": True, "asr": name, "diar": manifest.get("diar", "resnet34"), "device": asr.device(), "gpu_peak_mb": gpu_peak_mb(), "load_s": round(load_s, 1), "files": done,
                              "carried": carried, "failed": failed, "audio_s": round(audio_s, 1), "work_s": round(elapsed, 1),
                              "rtf": round(elapsed / audio_s, 3) if audio_s else None, "peak_rss_mb": round(rss_mb())}) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--out")
    ap.add_argument("--probe-gpu", action="store_true")
    a = ap.parse_args()
    if a.probe_gpu:
        return probe_gpu()
    if not a.manifest or not a.out:
        ap.error("--manifest and --out")
    with open(a.manifest, encoding="utf-8") as fh:
        return run(json.load(fh), a.out)


if __name__ == "__main__":
    sys.exit(main())
