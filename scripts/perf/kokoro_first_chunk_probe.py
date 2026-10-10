#!/usr/bin/env python3
"""Kokoro first-chunk probe (2026-10-10): per-stage profile + lever A/B + windowed-decoder equivalence.

Evidence for docs/knowledge/kokoro-streaming-first-chunk-2026-10-10.md. It loads its OWN Kokoro
(~2.3 GB), so it REFUSES to run unless ZOE_PERF=1 and the live sidecar is stopped. Run it inside the
shared lock, in a short sidecar-stop window with a trap that restarts kokoro-tts:

    flock /tmp/zoe-voice-harness.lock bash -c 'trap "systemctl --user start kokoro-tts" EXIT; \
      systemctl --user stop kokoro-tts; ZOE_PERF=1 ~/.zoe/venvs/kokoro-py310/bin/python \
      scripts/perf/kokoro_first_chunk_probe.py OUTDIR 20'

then poll :10201/health for pipeline_loaded and device=cuda. Verdict: nothing here beat the floor
(see the doc); the script is kept so the numbers can be re-measured.
"""
import os
import sys
import urllib.request

if os.environ.get("ZOE_PERF") != "1":
    print("ZOE_PERF!=1 - skipping (this probe loads a second Kokoro).")
    sys.exit(0)
try:  # a live sidecar holds ~2.3 GB; a second load can OOM the running brain
    urllib.request.urlopen("http://127.0.0.1:10201/health", timeout=2)
    print("kokoro sidecar is UP on :10201 - refusing to load a second Kokoro. Stop it inside the lock first.")
    sys.exit(2)
except Exception:
    pass
import os, sys, json, time, random, statistics as st, threading, urllib.request, wave, struct
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "backend:cudaMallocAsync")
import torch
import torch.nn.functional as F
from kokoro import KPipeline

OUT = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
WAVDIR = os.path.join(OUT, "wav")
os.makedirs(WAVDIR, exist_ok=True)
LOG = open(os.path.join(OUT, "results.jsonl"), "a")


def emit(**kw):
    LOG.write(json.dumps(kw) + "\n"); LOG.flush(); print(kw, flush=True)


TEXTS = {
    5: "The kitchen lights are on now",
    10: "I checked your calendar and you are free after lunch today",
    15: "Sure, I can set a reminder for the dentist appointment on Thursday morning at nine",
    25: "Here is what I found: the weather tomorrow looks mild with a light breeze, a high of twenty four degrees, and only a small chance of rain later in the evening",
}
LL = "http://127.0.0.1:11434"
stop = threading.Event()


def load():
    while not stop.is_set():
        try:
            r = urllib.request.Request(LL + "/completion", json.dumps({"prompt": "Write a long story about a lighthouse keeper.", "n_predict": 200, "temperature": 0.7, "cache_prompt": True}).encode(), {"Content-Type": "application/json"})
            urllib.request.urlopen(r, timeout=60).read()
        except Exception:
            time.sleep(1)


p = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device="cuda")
m = p.model
dev = m.device
pack = p.load_voice("af_sky").to(dev)
SPEED = 1.0
SR = 24000


def sync():
    torch.cuda.synchronize()


def phon(text):
    _, tokens = p.g2p(text)
    for gs, ps, tks in p.en_tokenize(tokens):
        if ps:
            return ps


# --------------------------------------------------------------------------- text side
@torch.no_grad()
def text_side(ps, speed=1.0, timers=None):
    """Everything before the decoder. Returns asr, F0, N, s, ref (mirrors KModel.forward_with_tokens)."""
    def tk(name):
        if timers is not None:
            sync(); t = time.perf_counter(); timers[name] = timers.get(name, 0) + (t - tk.t) * 1000; tk.t = t
    ref_s = pack[len(ps) - 1]
    ids = [m.vocab.get(c) for c in ps]
    ids = [i for i in ids if i is not None]
    input_ids = torch.LongTensor([[0, *ids, 0]]).to(dev)
    if timers is not None:
        sync(); tk.t = time.perf_counter()
    il = torch.full((1,), input_ids.shape[-1], device=dev, dtype=torch.long)
    mask = torch.arange(il.max()).unsqueeze(0).expand(1, -1).type_as(il)
    mask = torch.gt(mask + 1, il.unsqueeze(1)).to(dev)
    bert = m.bert(input_ids, attention_mask=(~mask).int())
    d_en = m.bert_encoder(bert).transpose(-1, -2)
    tk("bert")
    s = ref_s[:, 128:]
    d = m.predictor.text_encoder(d_en, s, il, mask)
    x, _ = m.predictor.lstm(d)
    dur = torch.sigmoid(m.predictor.duration_proj(x)).sum(axis=-1) / speed
    pred_dur = torch.round(dur).clamp(min=1).long().squeeze()
    tk("predictor_dur")
    idx = torch.repeat_interleave(torch.arange(input_ids.shape[1], device=dev), pred_dur)
    aln = torch.zeros((input_ids.shape[1], idx.shape[0]), device=dev)
    aln[idx, torch.arange(idx.shape[0])] = 1
    aln = aln.unsqueeze(0)
    en = d.transpose(-1, -2) @ aln
    F0, Nn = m.predictor.F0Ntrain(en, s)
    tk("F0N")
    t_en = m.text_encoder(input_ids, il, mask)
    asr = t_en @ aln
    tk("text_encoder")
    return asr, F0, Nn, ref_s[:, :128], pred_dur


# --------------------------------------------------------------------------- generator with injected source
@torch.no_grad()
def make_source(gen, F0):
    f0 = gen.f0_upsamp(F0[:, None]).transpose(1, 2)
    har_source, _, _ = gen.m_source(f0)
    return har_source.transpose(1, 2).squeeze(1)  # [1, samples]


@torch.no_grad()
def gen_forward(gen, x, s, har_source, timers=None):
    har_spec, har_phase = gen.stft.transform(har_source)
    har = torch.cat([har_spec, har_phase], dim=1)
    for i in range(gen.num_upsamples):
        x = F.leaky_relu(x, negative_slope=0.1)
        xs_ = gen.noise_res[i](gen.noise_convs[i](har), s)
        x = gen.ups[i](x)
        if i == gen.num_upsamples - 1:
            x = gen.reflection_pad(x)
        x = x + xs_
        acc = None
        for j in range(gen.num_kernels):
            r = gen.resblocks[i * gen.num_kernels + j](x, s)
            acc = r if acc is None else acc + r
        x = acc / gen.num_kernels
    x = F.leaky_relu(x)
    x = gen.conv_post(x).float()
    spec = torch.exp(x[:, : gen.post_n_fft // 2 + 1, :])
    phase = torch.sin(x[:, gen.post_n_fft // 2 + 1:, :])
    return gen.stft.inverse(spec, phase)


@torch.no_grad()
def decode(asr, F0c, Nn, s, har_source, half=False, timers=None):
    dec = m.decoder
    with torch.autocast("cuda", dtype=torch.float16, enabled=half):
        F0 = dec.F0_conv(F0c.unsqueeze(1)); Nv = dec.N_conv(Nn.unsqueeze(1))
        x = torch.cat([asr, F0, Nv], axis=1)
        x = dec.encode(x, s)
        asr_res = dec.asr_res(asr)
        res = True
        for blk in dec.decode:
            if res:
                x = torch.cat([x, asr_res, F0, Nv], axis=1)
            x = blk(x, s)
            if blk.upsample_type != "none":
                res = False
        if timers is not None:
            sync(); t = time.perf_counter(); timers["dec_pre_gen"] = timers.get("dec_pre_gen", 0) + (t - timers["_t"]) * 1000; timers["_t"] = t
        out = gen_forward(dec.generator, x, s, har_source)
    if timers is not None:
        sync(); t = time.perf_counter(); timers["generator"] = timers.get("generator", 0) + (t - timers["_t"]) * 1000; timers["_t"] = t
    return out.float().reshape(-1)


FR = 600  # samples per frame


@torch.no_grad()
def synth_whole(text, half=False, har=None, ret_parts=False):
    ps = phon(text)
    asr, F0c, Nn, s, _ = text_side(ps, SPEED)
    if har is None:
        har = make_source(m.decoder.generator, F0c)
    a = decode(asr, F0c, Nn, s, har, half=half)
    return a.cpu()


@torch.no_grad()
def synth_first_window(text, W, C, har=None):
    """Time to first PCM: text side (whole utterance) + source + first windowed decode + cpu copy."""
    ps = phon(text)
    asr, F0c, Nn, s, _ = text_side(ps, SPEED)
    har = make_source(m.decoder.generator, F0c)
    Tf = asr.shape[-1]
    hi = min(Tf, W + C)
    return windowed_piece(asr, F0c, Nn, s, har, 0, min(W, Tf), 0, hi).cpu()


@torch.no_grad()
def windowed_piece(asr, F0c, Nn, s, har, a, b, lo, hi):
    out = decode(asr[:, :, lo:hi], F0c[:, 2 * lo:2 * hi], Nn[:, 2 * lo:2 * hi], s, har[:, FR * lo:FR * hi])
    off = (a - lo) * FR
    return out[off: off + (b - a) * FR]


@torch.no_grad()
def synth_windowed(text, W, C, har=None, W0=None):
    ps = phon(text)
    asr, F0c, Nn, s, _ = text_side(ps, SPEED)
    if har is None:
        har = make_source(m.decoder.generator, F0c)
    Tf = asr.shape[-1]
    pieces = []; a = 0; first = True
    while a < Tf:
        w = (W0 if (first and W0) else W)
        b = min(Tf, a + w)
        lo = max(0, a - C); hi = min(Tf, b + C)
        pieces.append(windowed_piece(asr, F0c, Nn, s, har, a, b, lo, hi))
        a = b; first = False
    return torch.cat(pieces).cpu()


def wavwrite(path, a):
    a = a.float().clamp(-1, 1).mul(32767).to(torch.int16).reshape(-1).tolist()
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(struct.pack(f"<{len(a)}h", *a))


# metrics
def logspec(a):
    a = a.float().to(dev)
    S = torch.stft(a, 1024, 256, window=torch.hann_window(1024, device=dev), return_complex=True).abs()
    return 20 * torch.log10(S + 1e-5)


def lsd(a, b):
    n = min(len(a), len(b))
    A, B = logspec(a[:n]), logspec(b[:n])
    # ignore bins below noise floor on both sides so silence differences do not dominate
    msk = (torch.maximum(A, B) > -60)
    d = (A - B)[msk]
    return float(d.pow(2).mean().sqrt())


def rms(a):
    return float(a.float().pow(2).mean().sqrt())


def seam(a, W, C, bounds):
    """largest |sample jump| at window boundaries relative to the median |sample jump| overall."""
    d = (a[1:] - a[:-1]).abs()
    med = float(d.median()) + 1e-9
    vals = [float(d[bd - 1]) for bd in bounds if 0 < bd < len(d)]
    return (max(vals) / med) if vals else 0.0


def timeit(fn, n, warm=3):
    ts = []
    for i in range(n + warm):
        sync(); t = time.perf_counter(); fn(); sync(); dt = (time.perf_counter() - t) * 1000
        if i >= warm:
            ts.append(dt)
    return ts


def summ(v):
    s = sorted(v); return dict(n=len(v), med=round(st.median(v), 1), p10=round(s[len(v) // 10], 1), p90=round(s[int(len(v) * .9)], 1), min=round(min(v), 1))


# ---------------------------------------------------------------- 0. equivalence of my path vs KPipeline (fixed seeds)
for k, text in TEXTS.items():
    torch.manual_seed(1); ref = torch.cat([r.audio for r in p(text, voice="af_sky", speed=1.0)])
    torch.manual_seed(1); mine = synth_whole(text)
    torch.manual_seed(2); other = synth_whole(text)
    emit(exp="custom_vs_kpipeline", words=k, len_ref=len(ref), len_mine=len(mine), lsd_same_seed=round(lsd(ref, mine), 3), lsd_other_seed=round(lsd(ref, other), 3), rms_ref=round(rms(ref), 4), rms_mine=round(rms(mine), 4))

# ---------------------------------------------------------------- 1. stage profile (synced, idle then under decode)
def stage_profile(tag):
    for k, text in TEXTS.items():
        rows = []
        for i in range(N + 3):
            T = {}
            sync(); t0 = time.perf_counter()
            ps = phon(text); t1 = time.perf_counter(); T["g2p"] = (t1 - t0) * 1000
            asr, F0c, Nn, s, _ = text_side(ps, SPEED, timers=T)
            sync(); T["_t"] = time.perf_counter()
            har = make_source(m.decoder.generator, F0c)
            sync(); t = time.perf_counter(); T["source"] = (t - T["_t"]) * 1000; T["_t"] = t
            a = decode(asr, F0c, Nn, s, har, timers=T)
            sync(); t = time.perf_counter()
            a.cpu(); sync(); T["cpu_copy"] = (time.perf_counter() - t) * 1000
            if i >= 3:
                rows.append(T)
        keys = [x for x in rows[0] if x != "_t"]
        emit(exp="stage_profile", cond=tag, words=k, frames=int(asr.shape[-1]), **{x: round(st.median([r[x] for r in rows]), 1) for x in keys})


def lever_ab(tag):
    for k, text in TEXTS.items():
        arms = {}
        def base():
            torch.cuda.empty_cache(); list(p(text, voice="af_sky", speed=1.0))
        def noec():
            list(p(text, voice="af_sky", speed=1.0))
        def custom():
            synth_whole(text)
        def fp16():
            synth_whole(text, half=True)
        def firstwin():
            synth_first_window(text, 8, 8)
        fns = dict(base=base, base_noec=noec, custom_fp32=custom, custom_fp16=fp16, first_window_W8C8=firstwin)
        res = {a: [] for a in fns}
        names = list(fns)
        for i in range(N + 3):
            random.shuffle(names)
            for nme in names:
                sync(); t = time.perf_counter(); fns[nme](); sync(); dt = (time.perf_counter() - t) * 1000
                if i >= 3:
                    res[nme].append(dt)
        emit(exp="lever_ab", cond=tag, words=k, **{a: summ(v) for a, v in res.items()})


stage_profile("idle")
lever_ab("idle")
# cudnn.benchmark: per-shape autotune; shapes vary per utterance, so report cold per-new-shape cost too
torch.backends.cudnn.benchmark = True
for k, text in TEXTS.items():
    cold = timeit(lambda: synth_whole(text), 1, warm=0)
    warm = timeit(lambda: synth_whole(text), 10, warm=2)
    emit(exp="cudnn_benchmark", words=k, first_ever_ms=round(cold[0], 1), same_shape_med=summ(warm))
torch.backends.cudnn.benchmark = False

# ---------------------------------------------------------------- 2. windowed decoder: audio equivalence + seams
for k, text in TEXTS.items():
    torch.manual_seed(5)
    ps = phon(text)
    asr, F0c, Nn, s, _ = text_side(ps, SPEED)
    har = make_source(m.decoder.generator, F0c)
    whole = decode(asr, F0c, Nn, s, har).cpu()
    torch.manual_seed(77); har2 = make_source(m.decoder.generator, F0c)
    whole2 = decode(asr, F0c, Nn, s, har2).cpu()  # control: different excitation noise
    half = decode(asr, F0c, Nn, s, har, half=True).cpu()
    emit(exp="equiv_control", words=k, frames=int(asr.shape[-1]), samples=len(whole), lsd_other_noise=round(lsd(whole, whole2), 3), lsd_fp16=round(lsd(whole, half), 3), rms_whole=round(rms(whole), 4), rms_fp16=round(rms(half), 4))
    wavwrite(f"{WAVDIR}/{k}w_A_whole.wav", whole)
    wavwrite(f"{WAVDIR}/{k}w_fp16.wav", half)
    for W, C in ((8, 4), (8, 8), (8, 16), (16, 8), (16, 16), (24, 16)):
        torch.manual_seed(5)
        win = synth_windowed(text, W, C, har=har)
        Tf = asr.shape[-1]
        bounds = [i * W * FR for i in range(1, Tf // W + 1)]
        emit(exp="equiv_window", words=k, W=W, C=C, len_ratio=round(len(win) / len(whole), 4), lsd_vs_whole=round(lsd(whole, win), 3), rms_ratio=round(rms(win) / rms(whole), 4), seam_jump_ratio=round(seam(win, W, C, bounds), 2), ctl_seam_jump_ratio=round(seam(whole, W, C, bounds), 2))
        if (W, C) in ((8, 8), (16, 16)):
            wavwrite(f"{WAVDIR}/{k}w_B_win_W{W}_C{C}.wav", win)
    # NEGATIVE CONTROL: no context, per-window instance norm only, hard concatenation
    win0 = synth_windowed(text, 8, 0, har=har)
    emit(exp="negctl_window_C0", words=k, lsd_vs_whole=round(lsd(whole, win0), 3), rms_ratio=round(rms(win0) / rms(whole), 4))
    wavwrite(f"{WAVDIR}/{k}w_neg_C0.wav", win0)

# ---------------------------------------------------------------- 3. under concurrent llama decode
th = threading.Thread(target=load, daemon=True); th.start(); time.sleep(3)
stage_profile("decode")
lever_ab("decode")
stop.set(); th.join(timeout=70)
emit(exp="done")
