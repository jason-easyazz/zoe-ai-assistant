#!/usr/bin/env python3
"""Build and verify the WeSpeaker ResNet34 INT8 artifacts the panel daemon needs (flag-dark switch, record below).

Record: docs/research/speaker-resnet34-optimisation-2026-10.md (the spec). Operator steps:
docs/knowledge/speaker-resnet34-switch.md. The panel module whose pins this tool produces and checks is
scripts/setup/zoe_speaker_resnet34.py.

Why a tool: the static INT8 QDQ file is NOT published anywhere - it is derived. So it is rebuilt from the UPSTREAM FP32 file
(sha256-pinned) and the public LibriSpeech dev-clean calibration set (sha256-pinned), by the same harness the record used
(``speaker_bakeoff.py prep-cohort`` + ``quantize``), and the result is checked against the pinned digest. The 256-float
cohort-mean centring vector is then recomputed from the public far-field cohort WITH THE DEPLOYED INT8 MODEL (the vector is
a property of the model + ORT pair, not of the FP32 file). Nothing here touches household audio, the live services or the GPU.

Sub-commands:

  fetch     download the upstream FP32 model (and, with --devclean, the LibriSpeech dev-clean archive) into --dest, verified
            against the pinned sha256 BEFORE the file is published; never overwrites a file that already verifies.
  build     fetch -> prep-cohort -> quantize -> verify the INT8 digest -> centre (one scratch dir, exclusive harness lock is
            the caller's job: flock -x /tmp/zoe-voice-harness.lock nice -n 10 ...).
  centre    embed the far-field cohort with the INT8 model and (re)write the artifacts JSON (centring vector + self-test
            reference + provenance). Prints the digest to pin in zoe_speaker_resnet34.py (CENTRE_SHA256).
  verify    what the panel does at start-up, runnable anywhere the files are: pin the model, load the artifacts, run the
            self-test. Exit 0 = the embedder would be accepted, 3 = it would be refused.

Memory: refuses to start a model step below --min-avail-mb of MemAvailable (this box runs the live voice brain).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETUP = HERE.parent / "setup"
BAKEOFF = HERE / "speaker_bakeoff.py"

DEVCLEAN_URL = "https://www.openslr.org/resources/12/dev-clean.tar.gz"
DEVCLEAN_SHA256 = "76f87d090650617fca0cac8f88b9416e0ebf80350acb97b343a85fa903728ab3"
DEVCLEAN_MD5 = "42e2234ba48799c1f50f24a7926300a1"      # as published by OpenSLR (cross-check, not the pin)
INT8_SCRATCH_NAME = "rn34_qdq_u8s8_mm.onnx"            # the name the record's harness runs use
FP32_SCRATCH_NAME = "wespeaker_en_voxceleb_resnet34.onnx"


def load_panel_module():
    spec = importlib.util.spec_from_file_location("zoe_speaker_resnet34", SETUP / "zoe_speaker_resnet34.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("zoe_speaker_resnet34", mod)
    spec.loader.exec_module(mod)
    return mod


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mem_available_mb() -> float:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return float("inf")


def guard(min_mb: float, what: str) -> None:
    have = mem_available_mb()
    if have < min_mb:
        sys.exit("refusing %s: MemAvailable %.0f MB < %.0f MB (the live voice brain needs it)" % (what, have, min_mb))


def download_verified(url: str, dest: Path, sha256: str) -> bool:
    """Fetch ``url`` to ``dest`` unless ``dest`` already has the pinned digest. The bytes are verified in a temp file
    BEFORE they replace anything. Returns True when a download happened."""
    if dest.is_file() and sha256_file(dest) == sha256:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        got = sha256_file(tmp)
        if got != sha256:
            raise SystemExit("%s: sha256 %s... != pinned %s... - discarded" % (dest.name, got[:12], sha256[:12]))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def cmd_fetch(a) -> None:
    z = load_panel_module()
    dest = Path(a.dest)
    did = download_verified(z.FP32_URL, dest / FP32_SCRATCH_NAME, z.FP32_SHA256)
    print(json.dumps(dict(fp32=str(dest / FP32_SCRATCH_NAME), downloaded=did, sha256=z.FP32_SHA256)))
    if a.devclean:
        did = download_verified(DEVCLEAN_URL, dest / "dev-clean.tar.gz", DEVCLEAN_SHA256)
        print(json.dumps(dict(devclean=str(dest / "dev-clean.tar.gz"), downloaded=did, sha256=DEVCLEAN_SHA256)))


def cohort_farfield(cohort: Path) -> list:
    man = json.loads((cohort / "manifest.json").read_text())
    rows = [m for m in man if m.get("variant") == "farfield"]
    if len(rows) < 200 or len(set(m["speaker"] for m in rows)) < 40:
        sys.exit("cohort has %d far-field clips from %d speakers; the record wants 200+ from 40+ (7d)"
                 % (len(rows), len(set(m["speaker"] for m in rows))))
    return rows


def cmd_centre(a) -> None:
    import numpy as np

    guard(a.min_avail_mb, "centre")
    z = load_panel_module()
    model = Path(a.model)
    digest = sha256_file(model)
    if digest != z.MODEL_SHA256 and not a.allow_new_model:
        sys.exit("model sha256 %s... != the pin %s... - pass --allow-new-model only when you are deliberately introducing a "
                 "new model file (and then bump the model id's version and the pins)" % (digest[:12], z.MODEL_SHA256[:12]))
    import onnxruntime as ort

    stub = dict(centre=dict(vector=[0.0] * z.EMBED_DIM), selftest={})
    emb = z.Resnet34Embedder(z.make_session(str(model)), stub)
    cohort = Path(a.cohort)
    raws = []
    for m in cohort_farfield(cohort):
        x, _ = z.wav_to_float((cohort / m["path"]).read_bytes())
        r = emb.embed_raw(x)
        if r is None:
            sys.exit("a cohort clip produced no embedding: " + m["path"])
        raws.append(r)
    mu = np.mean(np.asarray(raws, dtype=np.float64), axis=0).astype(np.float32)
    clip = z.synthetic_clip(3.0)
    emb.embed_raw(clip)
    ref = emb.embed_raw(clip)
    ms = []
    for _ in range(5):
        t0 = time.perf_counter()
        emb.embed_raw(clip)
        ms.append((time.perf_counter() - t0) * 1e3)
    vec = [float(v) for v in mu]
    out = dict(
        schema=1, model_id=z.MODEL_ID,
        note="PUBLIC data only: a mean of LibriSpeech embeddings and the embedding of a synthetic tone signal. No household audio or voiceprint.",
        model=dict(file=z.MODEL_FILENAME, sha256=digest, fp32_sha256=z.FP32_SHA256, fp32_url=z.FP32_URL,
                   quantisation="static INT8 QDQ, U8 activations, S8 per-channel weights, MinMax over 100 shuffled (Random(3)) cohort clips",
                   built_with=dict(onnxruntime=ort.__version__, numpy=np.__version__)),
        centre=dict(version=z.CENTRE_VERSION, dim=z.EMBED_DIM, vector=vec, sha256_f32le=z.centre_digest(vec),
                    n_clips=len(raws), n_speakers=len(set(m["speaker"] for m in cohort_farfield(cohort))),
                    definition="mean of the L2-normalised embeddings of the far-field cohort; applied as l2(l2(e) - mean)",
                    provenance=dict(corpus="LibriSpeech dev-clean (speaker-disjoint from test-clean)", url=DEVCLEAN_URL,
                                    licence="CC BY 4.0", citation="Panayotov, Chen, Povey, Khudanpur. LibriSpeech: an ASR corpus based on public domain audio books. ICASSP 2015",
                                    archive_sha256=DEVCLEAN_SHA256, archive_md5_openslr=DEVCLEAN_MD5,
                                    prep="speaker_bakeoff.py prep-cohort --per-speaker 6 (seed 7): crops across three length buckets, synthetic far-field variant",
                                    derivative="a 256-number statistic of public speech; not audio, not any person's voiceprint")),
        selftest=dict(signal="zoe_speaker_resnet34.synthetic_clip(3.0)", seconds=3.0, reference=[float(v) for v in ref],
                      expected_ms=110.0, min_cosine=z.SELFTEST_MIN_COSINE, max_factor=z.SELFTEST_MAX_FACTOR,
                      measured_here_ms_median=round(sorted(ms)[len(ms) // 2], 1)),
    )
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(dict(out=a.out, centre_sha256=out["centre"]["sha256_f32le"], model_sha256=digest, clips=len(raws),
                          matches_pin=out["centre"]["sha256_f32le"] == z.CENTRE_SHA256)))


def cmd_verify(a) -> None:
    z = load_panel_module()
    try:
        emb = z.Resnet34Embedder.load(a.model_dir, artifacts_path=a.artifacts, verify=False)
        report = z.selftest(emb)
    except z.Unavailable as exc:
        print(json.dumps(dict(ok=False, refused=str(exc))))
        sys.exit(3)
    print(json.dumps(dict(ok=True, model_id=z.MODEL_ID, **report)))


def bakeoff_cmd(scratch: Path, *args: str) -> list:
    return [sys.executable, str(BAKEOFF), "--scratch", str(scratch)] + list(args)


def build_plan(scratch: Path, devclean_dir: Path) -> list:
    """The harness steps the record used (section 9), as argument vectors. Pure so a test can pin them."""
    return [
        bakeoff_cmd(scratch, "prep-cohort", "--libri", str(devclean_dir), "--out", str(scratch / "cohort"), "--per-speaker", "6"),
        bakeoff_cmd(scratch, "quantize", "--src", FP32_SCRATCH_NAME, "--dst", INT8_SCRATCH_NAME, "--mode", "static",
                    "--format", "qdq", "--act-type", "uint8", "--w-type", "int8", "--calib", "minmax"),
    ]


def cmd_build(a) -> None:
    import tarfile

    guard(a.min_avail_mb, "build")
    z = load_panel_module()
    scratch = Path(a.scratch)
    models = scratch / "models"
    download_verified(z.FP32_URL, models / FP32_SCRATCH_NAME, z.FP32_SHA256)
    archive = scratch / "dl" / "dev-clean.tar.gz"
    download_verified(DEVCLEAN_URL, archive, DEVCLEAN_SHA256)
    libri = scratch / "dl" / "LibriSpeech"
    if not (libri / "dev-clean").is_dir():
        with tarfile.open(archive) as tf:
            tf.extractall(scratch / "dl", filter="data")
    for cmd in build_plan(scratch, libri / "dev-clean"):
        print("+", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
    int8 = models / INT8_SCRATCH_NAME
    got = sha256_file(int8)
    print(json.dumps(dict(int8=str(int8), sha256=got, pinned=z.MODEL_SHA256, reproduced=got == z.MODEL_SHA256)))
    if got != z.MODEL_SHA256:
        print("NOTE: quantisation is not byte-reproducible on this toolchain (onnx/onnxruntime versions differ from the record). "
              "The pinned file is the one the profiles and the centring vector were built with; either obtain that exact file or "
              "treat this as a NEW model (new digest, new centring vector, bump the id's version) and re-enrol.", file=sys.stderr)
        if not a.allow_new_model:
            sys.exit(4)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    dst = out / z.MODEL_FILENAME
    dst.write_bytes(int8.read_bytes())
    os.chmod(dst, 0o644)
    cmd_centre(argparse.Namespace(model=str(dst), cohort=str(scratch / "cohort"), out=str(out / z.ARTIFACTS_FILENAME),
                                  allow_new_model=a.allow_new_model, min_avail_mb=a.min_avail_mb))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-avail-mb", type=float, default=2048)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--dest", required=True)
    p.add_argument("--devclean", action="store_true")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("build")
    p.add_argument("--scratch", default=os.path.expanduser("~/.cache/zoe-speaker-bakeoff"))
    p.add_argument("--out", required=True, help="where the INT8 model + artifacts JSON are written (then pushed to the panel)")
    p.add_argument("--allow-new-model", action="store_true")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("centre")
    p.add_argument("--model", required=True)
    p.add_argument("--cohort", required=True, help="prep-cohort output dir (holds manifest.json)")
    p.add_argument("--out", required=True)
    p.add_argument("--allow-new-model", action="store_true")
    p.set_defaults(fn=cmd_centre)
    p = sub.add_parser("verify")
    p.add_argument("--model-dir", default=None)
    p.add_argument("--artifacts", default=None)
    p.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    if not hasattr(a, "min_avail_mb"):
        a.min_avail_mb = 2048
    a.fn(a)


if __name__ == "__main__":
    main()
