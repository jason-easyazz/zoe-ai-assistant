#!/usr/bin/env python3
"""The ASR + speaker-embedding half of the pendant night re-pass, as a STANDALONE process in its own venv (``~/.zoe/venvs/omi-repass``).

It exists so faster-whisper, torch and speechbrain (and their ~2.5 GB) never enter zoe-data or the night window: the job starts it under
``systemd-run --user --scope -p MemoryMax=... -p MemorySwapMax=0 nice -n 10`` while Zoe sleeps, and reads a JSONL back. It knows nothing about
the database, members, names or labels - the parent (``services/zoe-data/omi_repass.py``) owns every decision.

    omi_repass_worker.py --manifest M.json --out OUT.jsonl      transcribe + embed each item; one JSON line per finished item
    omi_repass_worker.py --probe-gpu                            print what the GPU path could do on this stack (one JSON line), change nothing

Manifest: {"asr": "faster-whisper:large-v3"|"faster-whisper:distil-large-v3" (ASR registry), "diar": "ecapa" (DIAR registry), "threads": 4, "gpu": false, "hint": "Names: ...", "deadline_epoch": 1.7e9,
           "rtf": 1.0, "zoe_clips": [paths], "items": [{"id": "...", "path": "...", "hint": "(optional, per item)"}]}
Output lines: {"zoe_ref": [...]} once (when zoe_clips were given), then per item {"id", "ok": true, "dur_s", "speech_s", "asr_s", "emb_s",
           "words": [[w, t0, t1, p]...], "windows": [[t0, t1, [192 floats], rms]...]} or {"id", "ok": false, "why"}, then {"done": true, ...}.
An item is never half-written: if the deadline passes mid-file the item is reported ``why: "deadline"`` and the parent carries it to the next night.
Offline by construction (HF_HUB_OFFLINE=1, models resolved from the pinned snapshots in the HF cache). Audio and words are never logged.
Exit 0 ok, 3 a model/package is missing, 1 any other failure.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

#: PINNED snapshots (the revisions the bake-off scored). Systran is the only HF org the ASR weights may come from; the speaker model is
#: speechbrain's own ECAPA (the bake-off's embedder; the weights are the same file the lab used).
MODELS = {
    "large-v3": ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", "c3058b475261292e64a0412df1d2681c06260fab"),
}
ECAPA = ("speechbrain/spkrec-ecapa-voxceleb", "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286")
RATE = 16000
WIN_S, HOP_S, MIN_WIN_S = 1.5, 0.75, 0.8


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


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


# ── the 12B's OWN audio (gemma4-12b-audio) ───────────────────────────────────────────────────────────────────────────────
#: VAD chunking IDENTICAL to the round-2 bake-off's ``chunk.py`` (silero via faster-whisper): speech spans of at most ``CHUNK_MAX_S`` (<= 30; 14 in the bake-off), gaps under
#: 0.4 s merged while the chunk stays short, spans under 0.3 s dropped.
CHUNK_MAX_S = 14.0     # the parent passes ``chunk_s`` in the manifest (ZOE_OMI_REPASS_CHUNK_S); never above 30
AUDIO_PROMPT = ("Transcribe this audio verbatim, exactly as spoken, in English. Output only the spoken words: no summary, no commentary, no timestamps and no speaker labels. "
                "If there is no intelligible speech, output nothing.")
REFUSAL_STARTS = ("there is no", "there's no", "i cannot", "i can't", "i'm sorry", "i am sorry", "the audio", "this audio", "no speech", "[silence", "(silence")


def vad_chunks(x) -> list:
    """[(t0, t1)] seconds: the bake-off's chunking, so every engine sees the same pieces."""
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


def wav_bytes(x) -> bytes:
    """16 kHz mono 16-bit WAV of float32 samples."""
    import io
    import wave
    import numpy as np
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def audio_prompt(hint: str) -> str:
    return AUDIO_PROMPT + (" " + hint if hint else "")


def clean_reply(text: str, dur_s: float) -> str:
    """The model's text -> the transcript, or "" for a refusal / description / runaway loop (more than 7 words a second is a loop, not speech)."""
    t = " ".join((text or "").replace("\n", " ").split()).strip().strip('"').strip()
    low = t.lower()
    if not t or low.startswith(REFUSAL_STARTS):
        return ""
    if len(t.split()) > 7 * max(dur_s, 1.0) + 10:
        return ""
    return t


def gemma_transcribe(base_url: str, wav: bytes, dur_s: float, hint: str = "", model: str = "gemma", timeout_s: float = 300.0) -> dict:
    """ONE chunk to a llama-server that has the audio mmproj: OpenAI ``input_audio`` (base64 16 kHz mono WAV). Returns {"text", "completion_tokens", "prompt_tokens", "el"}."""
    import base64
    import urllib.request
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": int(min(500, 40 + 9 * dur_s)), "stream": False,
                       "messages": [{"role": "user", "content": [{"type": "text", "text": audio_prompt(hint)},
                                                                  {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"}}]}]}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        d = json.loads(r.read())
    u = d.get("usage") or {}
    return {"text": clean_reply(((d.get("choices") or [{}])[0].get("message") or {}).get("content", ""), dur_s), "completion_tokens": u.get("completion_tokens"),
            "prompt_tokens": u.get("prompt_tokens"), "el": time.time() - t0}


class Gemma12bAudio(AsrBackend):
    """The night 12B's own audio encoder: VAD chunks of at most 30 s go to the 12B the window has ALREADY loaded (``--mmproj`` on the CPU), asking for a verbatim transcript with the name
    hint. The model returns TEXT only, so word times are spread evenly across each chunk and every word's confidence is a flat 0.6 (no per-word score exists); who spoke still comes from
    ECAPA windows. This backend shares the 12B's memory and speed (decode-bound) - the window runs it AFTER the 12B load."""

    def __init__(self, threads: int, gpu: bool, url: str = "", model: str = "gemma"):
        super().__init__(threads, gpu)
        self.url, self.model = url or os.environ.get("GEMMA_SERVER_URL", ""), model or os.environ.get("MEMORY_DIGEST_MODEL", "") or "gemma"

    def load(self) -> None:
        import urllib.request
        if not self.url or ":11434" in self.url:
            raise RuntimeError("no 12B url (the live 4B on :11434 is never used for this)")
        with urllib.request.urlopen(self.url.rstrip("/").removesuffix("/v1") + "/health", timeout=10) as r:
            if r.status != 200:
                raise RuntimeError("12B not healthy")

    def device(self) -> str:
        return "12b-gpu+mmproj-cpu"

    def transcribe(self, x, hint: str, deadline):
        words: list = []
        for s, e in vad_chunks(x):
            if deadline and time.time() > deadline:
                return [], True
            seg = x[int(s * RATE):int(e * RATE)]
            text = gemma_transcribe(self.url, wav_bytes(seg), e - s, hint, self.model)["text"]
            toks = text.split()
            for i, w in enumerate(toks):
                a = s + (e - s) * i / len(toks)
                b = s + (e - s) * (i + 1) / len(toks)
                words.append([(" " if words else "") + w, round(a, 3), round(b, 3), 0.6])
        return words, False


#: ASR registry: backend id -> factory(threads, gpu). Ids are what ``ZOE_OMI_NIGHT_ASR`` / the manifest's ``asr`` name.
ASR = {
    "faster-whisper:large-v3": lambda t, g: FasterWhisper(t, g, "large-v3"),
    "faster-whisper:distil-large-v3": lambda t, g: FasterWhisper(t, g, "distil-large-v3"),
    "gemma4-12b-audio": lambda t, g: Gemma12bAudio(t, g),
}
#: diarisation / speaker-window registry (the seam: ``windows(x, speech) -> [(t0, t1, emb, rms)]``); the parent matches the embeddings, so a
#: segmenting engine (pyannote, Sortformer) can slot in by returning windows of its own turns.
DIAR = {"ecapa": lambda t: Embedder(t)}


def read_audio(path: str):
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    if sr != RATE:
        raise ValueError(f"rate {sr}")
    return np.ascontiguousarray(x)


class Embedder:
    def __init__(self, threads: int):
        import torch
        torch.set_num_threads(max(1, min(2, threads)))
        from speechbrain.inference.speaker import EncoderClassifier
        src = snapshot(*ECAPA)
        save = os.path.expanduser("~/.zoe/omi-repass/ecapa")
        os.makedirs(save, exist_ok=True)
        self.torch = torch
        self.clf = EncoderClassifier.from_hparams(source=src, savedir=save, run_opts={"device": "cpu"})

    def one(self, x):
        with self.torch.no_grad():
            e = self.clf.encode_batch(self.torch.from_numpy(x).float().unsqueeze(0))
        return e.squeeze().numpy()

    def windows(self, x, speech):
        """1.5 s windows (0.75 s hop) over the speech spans; a trailing remainder under 0.8 s is dropped. Returns [(t0, t1, emb, rms)]."""
        out = []
        for sp in speech:
            a, b = int(sp["start"]), int(sp["end"])
            t = a
            while t < b:
                e = min(t + int(WIN_S * RATE), b)
                if e - t < MIN_WIN_S * RATE:          # a span (or a trailing remainder) too short for a window: nothing to embed, on to the next span
                    break
                seg = x[t:e]
                out.append((t / RATE, e / RATE, self.one(seg), float((seg.astype("float64") ** 2).mean() ** 0.5)))
                t += int(HOP_S * RATE)
        return out


def zoe_reference(emb: Embedder, clips: list):
    import numpy as np
    es = []
    for p in clips[:40]:
        try:
            x = read_audio(p)
        except Exception:  # noqa: BLE001
            continue
        if len(x) > 2.5 * RATE:
            es.append(emb.one(x[: 12 * RATE]))
    if not es:
        return None
    m = np.stack([e / (np.linalg.norm(e) or 1.0) for e in es]).mean(0)
    return (m / (np.linalg.norm(m) or 1.0)).tolist()


def run(manifest: dict, out_path: str) -> int:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    name = manifest.get("asr", "faster-whisper:large-v3")
    if name not in ASR or manifest.get("diar", "ecapa") not in DIAR:
        print(f"unknown backend {name}/{manifest.get('diar')}", file=sys.stderr)
        return 1
    threads = int(manifest.get("threads", 4))
    deadline = float(manifest.get("deadline_epoch") or 0) or None
    rtf = float(manifest.get("rtf") or 1.0)
    global CHUNK_MAX_S
    CHUNK_MAX_S = max(2.0, min(30.0, float(manifest.get("chunk_s") or CHUNK_MAX_S)))
    if manifest.get("llm_url"):
        os.environ["GEMMA_SERVER_URL"] = manifest["llm_url"]
    t0 = time.time()
    try:
        asr = ASR[name](threads, bool(manifest.get("gpu")))
        asr.load()
        emb = DIAR[manifest.get("diar", "ecapa")](threads)
    except Exception as exc:  # noqa: BLE001 - a model that is not in the cache is a setup problem, not a data one
        print(f"model unavailable: {type(exc).__name__}", file=sys.stderr)
        return 3
    load_s = time.time() - t0
    done = carried = failed = 0
    audio_s = elapsed = 0.0
    with open(out_path, "a", encoding="utf-8") as out:
        os.chmod(out_path, 0o600)
        if manifest.get("zoe_clips"):
            ref = zoe_reference(emb, manifest["zoe_clips"])
            if ref:
                out.write(json.dumps({"zoe_ref": ref}) + "\n")
                out.flush()
        for item in manifest["items"]:
            rid = item["id"]
            try:
                x = read_audio(item["path"])
                dur = len(x) / RATE
                if deadline and time.time() + dur * rtf * 0.5 > deadline:      # not enough time left to be likely to finish it: carry it, start nothing new
                    carried += 1
                    out.write(json.dumps({"id": rid, "ok": False, "why": "deadline"}) + "\n")
                    continue
                ta = time.time()
                words, late = asr.transcribe(x, item.get("hint") or manifest.get("hint") or "", deadline)
                if late:
                    carried += 1
                    out.write(json.dumps({"id": rid, "ok": False, "why": "deadline"}) + "\n")
                    out.flush()
                    continue
                asr_s = time.time() - ta
                te = time.time()
                speech = get_speech_timestamps(x, VadOptions(min_silence_duration_ms=300))
                wins = emb.windows(x, speech)
                emb_s = time.time() - te
                out.write(json.dumps({"id": rid, "ok": True, "dur_s": round(dur, 2), "speech_s": round(sum(s["end"] - s["start"] for s in speech) / RATE, 2),
                                      "asr_s": round(asr_s, 2), "emb_s": round(emb_s, 2), "words": words,
                                      "windows": [[round(a, 3), round(b, 3), [round(float(v), 4) for v in e], round(r, 5)] for a, b, e, r in wins]}) + "\n")
                done += 1
                audio_s += dur
                elapsed += asr_s + emb_s
            except Exception as exc:  # noqa: BLE001 - one bad file never costs the rest
                failed += 1
                out.write(json.dumps({"id": rid, "ok": False, "why": f"error:{type(exc).__name__}"}) + "\n")
            out.flush()
        out.write(json.dumps({"done": True, "asr": name, "diar": manifest.get("diar", "ecapa"), "device": asr.device(), "load_s": round(load_s, 1), "files": done,
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
