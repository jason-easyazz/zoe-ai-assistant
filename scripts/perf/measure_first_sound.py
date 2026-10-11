#!/usr/bin/env python3
"""End-of-speech -> first-sound probe for brain turns (blueprint D10), on the LIVE services.

Times the server-side chain a panel turn pays after the daemon closes the recording: a spoken clip (Kokoro-made, 0.32 s
lead + 0.85 s tail) -> Moonshine STT (in-process: a second copy) -> router + tiers (dry) -> ``brain_streaming`` (Flue sidecar,
llama-server, replay envelope on) -> the stream loop's own first-unit rules and tool filler (shared code in ``voice_tts`` /
``voice_first_sound``) -> Kokoro first chunk. Every prompt runs under every flag CONDITION (paired, order rotated, a fresh
session per turn so the llama prefix cache cannot confound the A/B). ``first_audio_s`` = clip POST -> first audio ready
(text, tool filler or acknowledgement); the Pi's tail / deliver / sink are CARRIED constants, added only in ``e2e_est_s``.
No write can happen (replay envelope, ``allow_writes=False``); ``.env`` is read like tests/replay_samples.py and never
printed. Gates (refusal = exit 2): ZOE_PERF=1; no night-window WINDOW_OPEN; no land_queue / land_voice_pr / docs_merge_chain;
not 01:45-03:15; MemAvailable >= --min-mem-mb at the start; the shared harness lock (held = exit 3; --wait queues, and a landing
that starts mid-run makes the probe hand the lock back).

    ZOE_PERF=1 $(bash scripts/deploy/zoe_data_python.sh) scripts/perf/measure_first_sound.py --wait --json ab.json
    ZOE_PERF=1 ... --ear-check /tmp/ear   # Kokoro only: A_<n>.wav = a reply as today, B_<n>.wav = as ZOE_FIRST_SOUND_CLAUSE
                                          # would send it (clause, then the rest; both trimmed as the Pi daemon does)
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import contextlib
import datetime as dt
import fcntl
import io
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
from service_dir import resolve_service_dir  # noqa: E402

LOCK = "/tmp/zoe-voice-harness.lock"
WINDOW_OPEN = Path.home() / ".zoe" / "night-window" / "WINDOW_OPEN"
LANDING_PGREP = r"^(/bin/)?[b]ash .*/(land_queue|land_voice_pr|docs_merge_chain)\.sh"
LEAD_S, TAIL_S = 0.32, 0.85          # follow-up pre-roll; endpoint tail (config floor 0.8 s + slack)
CARRIED_DELIVER_S, CARRIED_SINK_S = 0.084, 0.080   # Pi side, panel-ttfa-breakdown-2026-09-28.md
DEFAULT_CONDITIONS = ("off:;clause:ZOE_FIRST_SOUND_CLAUSE=1;"
                      "clause+ack:ZOE_FIRST_SOUND_CLAUSE=1,ZOE_FIRST_SOUND_TOOL_ACK=1")

# Generic on purpose: no household fact is quoted and no reply text is recorded.
SHAPES = {
    "memory": ["What do you know about my family?", "What did I tell you about my work?",
               "Do you remember what I said about my plans for the weekend?",
               "What do you remember about my favourite food?", "Who are the people I have mentioned lately?",
               "What did we talk about yesterday?", "What do you know about my kids?",
               "What is something you remember about me?", "What did I say about my last trip?",
               "Remind me what I told you about the garden."],
    # the router does not fulfil these, so the brain must call a tool (reads; writes are isolated)
    "tool": ["Can you check what is on my calendar tomorrow and tell me if I am free for lunch?",
             "Look at my shopping list and tell me what I still need for tacos.",
             "Can you see if I have any notes about the car service?",
             "Tell me what my week looks like and which day is the quietest.",
             "How long until my next reminder goes off?",
             "Is there anything on my calendar this week that clashes with a dentist visit?",
             "Can you look through my notes and tell me what I wrote about the holiday?",
             "Tell me which of my reminders are coming up soon and which are overdue.",
             "Do I have anything on tomorrow morning that I should prepare for?",
             "What is on my calendar after lunch today, and do I have time for a walk?",
             "Have a look at my reminders and tell me which one is next.",
             "Can you see what I have planned for the weekend?"],
    "chat": ["Tell me something interesting about octopuses.",
             "I am feeling a bit tired today, any tips for an afternoon slump?",
             "What is a good way to explain gravity to a six year old?", "Give me a quick idea for a fun dinner.",
             "Why is the sky blue?", "Can you suggest a good name for a pet goldfish?",
             "What is the difference between a crocodile and an alligator?",
             "Help me think of a nice way to say thank you to a neighbour.",
             "Is it better to run in the morning or the evening?", "How do bees make honey?"],
    # QUALITY shapes (opt in with --shapes; the latency default stays memory/tool/chat). "fact" has ground truth in FACT_EXPECT
    # (a style prompt that buys a fast opening with a wrong answer is the Little Gemma paper's "confidently wrong" failure);
    # "care" has none: read the replies for warmth.
    "fact": ["What is the capital city of Australia?", "How many legs does a spider have?",
             "What is the boiling point of water in degrees Celsius at sea level?", "Which planet is closest to the sun?",
             "How many days are there in a leap year?", "What gas do plants take in from the air?",
             "Who wrote the play Romeo and Juliet?", "What is twelve times eleven?",
             "How many minutes are in three hours?", "What is the largest ocean on Earth?"],
    "care": ["I had a really rough day at work and I just need to vent.", "I am nervous about my appointment tomorrow.",
             "My dog is not well and I am worried about her.", "I just got some good news and I am so happy!",
             "I feel a bit lonely tonight.", "I forgot my mum's birthday and I feel terrible."],
}
DEFAULT_SHAPES = ("memory", "tool", "chat")
FACT_EXPECT = {"What is the capital city of Australia?": r"canberra", "How many legs does a spider have?": r"\beight\b|\b8\b",
               "What is the boiling point of water in degrees Celsius at sea level?": r"\b100\b|one hundred",
               "Which planet is closest to the sun?": r"mercury", "How many days are there in a leap year?": r"\b366\b|three hundred (and )?sixty.six",
               "What gas do plants take in from the air?": r"carbon dioxide|co2",
               "Who wrote the play Romeo and Juliet?": r"shakespeare", "What is twelve times eleven?": r"\b132\b|one hundred (and )?thirty.two",
               "How many minutes are in three hours?": r"\b180\b|one hundred (and )?eighty", "What is the largest ocean on Earth?": r"pacific"}
STAGES = ("stt_s", "route_s", "tiers_s", "packet_build_s", "post_to_first_delta_s", "tool_sentinel_s", "unit_ready_s",
          "kokoro_s", "first_sound_s", "filler_sound_s", "ack_sound_s", "first_audio_s")
_LLAMA_RE = re.compile(r"(prompt eval time|eval time) =\s+([\d.]+) ms /\s+(\d+) (?:tokens|runs)")
_TIMERS: dict = {}
# --drain: after the first sound is timed, keep reading the stream to its end so the REPLY LENGTH can be compared across
# arms (a style prompt must not buy a fast opening with a curt answer). --keep-text also stores the first unit and the reply
# in the rows: those can quote household memory, so write --json outside the repo and never commit it.
DRAIN = {"on": False, "text": False}


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def gate_refusals(min_mem_mb: int, now=None) -> list:
    out, now = [], now or dt.datetime.now()
    if WINDOW_OPEN.exists():
        out.append(f"{WINDOW_OPEN} exists - a 12B night window owns the box")
    if subprocess.run(["pgrep", "-f", LANDING_PGREP], capture_output=True).returncode == 0:
        out.append("a PR landing is running (land_queue / land_voice_pr / docs_merge_chain)")
    if now.replace(hour=1, minute=15, second=0) <= now <= now.replace(hour=3, minute=15, second=0):
        out.append("inside the nightly window 01:45-03:15 (-30 min)")
    avail = next((int(ln.split()[1]) // 1024 for ln in open("/proc/meminfo") if ln.startswith("MemAvailable:")), 0)
    if avail < min_mem_mb:
        out.append(f"MemAvailable {avail} MB < {min_mem_mb} MB")
    return out


def _load_env(path: Path) -> None:
    """tests/replay_samples._load_env's contract: setdefault, never printed."""
    if path.exists():
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _build_clip(wav24: bytes) -> bytes:
    with wave.open(io.BytesIO(wav24), "rb") as wf:
        rate, width, ch, pcm = wf.getframerate(), wf.getsampwidth(), wf.getnchannels(), wf.readframes(wf.getnframes())
    if ch != 1:
        pcm = audioop.tomono(pcm, width, 0.5, 0.5)
    pcm, _ = audioop.ratecv(pcm, width, 1, rate, 16000, None)
    pcm = b"\x00\x00" * int(16000 * LEAD_S) + pcm + b"\x00\x00" * int(16000 * TAIL_S)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1), wf.setsampwidth(2), wf.setframerate(16000), wf.writeframes(pcm)
    return buf.getvalue()


def llama_timings(since: float, until: float) -> list:
    """llama-server's print_timing lines inside [since, until] (journald): prefill_n = tokens re-evaluated (small =
    prefix-cache hit), decode ms / tokens; one entry per model call, stamped ``at`` (unix)."""
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        out = subprocess.run(["journalctl", "--user", "-u", "llama-server", "--no-pager", "-o", "short-unix",
                              "--since", dt.datetime.fromtimestamp(since - 1).strftime(fmt),
                              "--until", dt.datetime.fromtimestamp(until + 3).strftime(fmt)],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:  # noqa: BLE001
        return []
    calls = []
    for line in out.splitlines():
        m = _LLAMA_RE.search(line)
        if m and m.group(1) == "prompt eval time":
            calls.append({"at": float(line.split(" ", 1)[0]), "prefill_ms": float(m.group(2)), "prefill_n": int(m.group(3))})
        elif m and calls:
            calls[-1].update({"decode_ms": float(m.group(2)), "decode_n": int(m.group(3))})
    return calls


def stamp_payload_ready() -> None:
    """Pure observation: stamp the moment the seam hands the sidecar its payload (packet build ends, POST begins)."""
    import zoe_flue_client as zfc

    orig = zfc._request_payload
    zfc._request_payload = lambda m: (_TIMERS.__setitem__("@payload", time.monotonic()), orig(m))[1]


async def wait_slot_idle(timeout: float = 3.0) -> None:
    """An aborted generation may still hold the llama slot; the next turn must not queue behind it."""
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/slots", timeout=2) as r:
                if not any(s.get("is_processing") for s in json.loads(r.read().decode())):
                    return
        except Exception:  # noqa: BLE001
            return
        await asyncio.sleep(0.1)


async def prepare(prompt: str) -> dict:
    """The part no flag touches, once per prompt: build the clip and transcribe it (timed)."""
    from routers import voice_tts as vt   # the code under test (this worktree's)

    wav24 = await vt._synthesize_kokoro_sidecar(prompt)
    if not wav24:
        return {"error": "kokoro unavailable for clip build"}
    clip = _build_clip(wav24)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(clip)
    t0 = time.monotonic()
    try:
        transcript = (await vt._transcribe_audio_impl(tmp.name) or "").strip()
    finally:
        os.unlink(tmp.name)
    stt_s = round(time.monotonic() - t0, 3)
    if not transcript:
        return {"error": "empty transcript", "stt_s": stt_s}
    return {"transcript": transcript, "stt_s": stt_s, "clip_s": round(len(clip) / 32000.0, 2)}


async def run_turn(shape: str, prep: dict, user: str, cond: str, session: str) -> dict:
    """The post-STT chain under the flags now in os.environ; ``first_*`` are seconds from the clip POST (the shared STT
    time is added back, so conditions compare like for like)."""
    from routers import voice_tts as vt
    import fast_tiers as ft
    import semantic_router as sr
    import voice_first_sound as fs
    from brain_dispatch import brain_streaming

    transcript = prep["transcript"]
    rec: dict = {"shape": shape, "cond": cond, "stt_s": prep["stt_s"], "clip_s": prep["clip_s"], "wall": [time.time()]}
    t_stt = time.monotonic()
    t_post = t_stt - prep["stt_s"]
    rr = sr.route(transcript)
    t_route = time.monotonic()
    res = await ft.resolve(transcript, user, session, channel="voice", router_decision=rr, allow_writes=False,
                           extra_ctx={"panel_id": "replay-ttfa"})
    rec.update(route_s=round(t_route - t_stt, 3), tiers_s=round(time.monotonic() - t_route, 3), routed=rr.get("routed"))
    if res is not None:   # answered with no brain: the loop synthesizes the reply's first sentence
        ok = await vt._synthesize_kokoro_sidecar((vt._split_sentences(res.reply) or [res.reply])[0])
        rec.update(path=f"tier:{res.tier or 'tier1.5'}:{res.domain}",
                   first_audio_s=round(time.monotonic() - t_post, 3) if ok else None, no_audio=not ok)
        return rec

    kw, _packet = await vt._voice_brain_kwargs(session, user, transcript, rr)
    _TIMERS.clear()
    t_kw = time.monotonic()
    stream = brain_streaming(transcript, session, user_id=user, voice_mode=True, replay_isolation=True, **kw)
    rec.update(await time_first_sound(stream, rr, vt, fs, t_post=t_post, t_kw=t_kw))
    reply = rec.pop("_reply", "")
    if shape == "fact" and DRAIN["on"]:   # ground truth: the reply must contain the expected answer
        norm = re.sub(r"[^a-z ]", "", transcript.lower())
        key = next((rx for q, rx in FACT_EXPECT.items() if re.sub(r"[^a-z ]", "", q.lower()) == norm), None)
        rec["fact_ok"] = bool(re.search(key, reply, re.I)) if key else None
    await wait_slot_idle()
    rec["wall"].append(time.time())
    return rec


async def close_stream(stream) -> None:
    """Close the brain stream deterministically: breaking out of ``async for`` does not run the generator's cleanup at once, and a
    ``Prefetched`` wrapper has a first pull in flight. Best effort - the probe never fails on a close."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # noqa: BLE001
        pass


async def time_first_sound(stream, rr, vt, fs, *, t_post: float, t_kw: float) -> dict:
    """The brain-path half of one turn: drive ``stream`` by the live loop's own rules (voice_tts._generate_voice_stream) and stamp when
    SOUND started. A stamp is taken ONLY when synthesis returned audio (a failed Kokoro call is not a fast reply); each failed synthesis
    is named in ``synth_failed`` and a turn that never made a sound has ``first_audio_s`` None and ``no_audio`` True. The stream is
    closed in a ``finally`` (plain and prefetched) once the first audio is timed, so the next sample never inherits this one's work."""
    t_filler = t_ack = t_first = t_tool = t_unit = None
    audio_started = filler_done = False
    failed: list = []
    buf, tools, unit = "", [], ""
    seen = ""   # every text delta, for --drain
    kokoro_s = None
    ack = fs.dispatch_ack(rr)
    try:
        if ack:   # ZOE_FIRST_SOUND_TOOL_ACK: spoken while the brain request is already in flight
            stream, filler_done = fs.prefetch(stream), True
            if await vt._synthesize_kokoro_sidecar(ack):
                audio_started, t_ack = True, time.monotonic()
            else:
                failed.append("ack")
        async for delta in stream:   # the stream loop's own rules, in order
            if not delta:
                continue
            now = time.monotonic()
            if delta.startswith(vt._VOICE_TOOL_SENTINEL_PREFIXES):
                name = vt._voice_tool_name_from_sentinel(delta)
                t_tool = t_tool or (now if delta.startswith("__TOOL__:") else None)
                if name and name not in tools:
                    tools.append(name)
                if name and not filler_done and not audio_started and not buf and vt._voice_tool_filler_enabled():
                    filler_done = True
                    if await vt._synthesize_kokoro_sidecar(vt._voice_tool_filler(name)):
                        audio_started, t_filler = True, time.monotonic()
                    else:
                        failed.append("filler")
                continue
            t_first, buf, unit, seen = t_first or now, buf + delta, "", seen + delta
            if vt._fast_first_audio_enabled() and not audio_started:   # once sound has started the loop only cuts sentences
                unit, buf = vt._extract_first_unit(buf)
                unit = unit or ""
            if not unit:
                ready, buf = vt._extract_complete_sentences(buf)
                unit = ready[0] if ready else ""
            if unit:
                t_unit = time.monotonic()
                break
        if not unit and buf.strip():
            unit, t_unit = buf.strip(), time.monotonic()
        t_audio = None
        if unit:
            t0 = time.monotonic()
            if await vt._synthesize_kokoro_sidecar(unit):   # the brain keeps decoding meanwhile, as in the live loop
                t_audio = time.monotonic()
                kokoro_s = round(t_audio - t0, 3)
            else:
                failed.append("first_unit")
        if DRAIN["on"]:
            async for delta in stream:
                if delta and not delta.startswith(vt._VOICE_TOOL_SENTINEL_PREFIXES):
                    seen += delta
    finally:
        await close_stream(stream)
    payload_at = _TIMERS.get("@payload")
    rel = lambda t: round(t - t_post, 3) if t else None   # noqa: E731
    out = dict(path="brain:tool" if tools else "brain", tools=tools, first_unit_chars=len(unit),
               packet_build_s=round(payload_at - t_kw, 3) if payload_at else None,
               post_to_first_delta_s=round(t_first - payload_at, 3) if t_first and payload_at else None,
               tool_sentinel_s=round(t_tool - t_kw, 3) if t_tool else None,
               unit_ready_s=round(t_unit - t_kw, 3) if t_unit else None,
               first_sound_s=rel(t_audio), filler_sound_s=rel(t_filler), ack_sound_s=rel(t_ack), synth_failed=failed)
    if kokoro_s is not None:
        out["kokoro_s"] = kokoro_s
    out["first_audio_s"] = min([x for x in (out["first_sound_s"], out["filler_sound_s"], out["ack_sound_s"]) if x is not None],
                               default=None)
    out["no_audio"] = out["first_audio_s"] is None
    out["first_unit_words"] = len(unit.split())
    if DRAIN["on"]:
        out["_reply"] = seen
        out["reply_chars"], out["reply_words"] = len(seen.strip()), len(seen.split())
        if DRAIN["text"]:
            out["first_unit"], out["reply"] = unit, seen.strip()
    return out


def summarize(rows: list) -> dict:
    out = {}
    for cond in sorted({r.get("cond") for r in rows if r.get("cond")}):
        for shape in SHAPES:
            rs = [r for r in rows if r.get("cond") == cond and r.get("shape") == shape and not r.get("error")]
            all_brain = [r for r in rs if str(r.get("path", "")).startswith("brain")]
            # a turn that never made a sound (Kokoro returned nothing) is REPORTED, never averaged in as a fast reply
            brain = [r for r in all_brain if not r.get("no_audio") and r.get("first_audio_s") is not None]
            first = _median([r.get("first_audio_s") for r in brain])
            out[f"{cond}/{shape}"] = {
                "turns": len(rs), "brain_turns": len(brain), "no_audio_turns": len(all_brain) - len(brain),
                "synth_failed": sum(len(r.get("synth_failed") or ()) for r in all_brain), "tool_turns": sum(1 for r in brain if r.get("tools")),
                "ack_fired": sum(1 for r in brain if r.get("ack_sound_s")),
                "median_s": {k: _median([r.get(k) for r in brain]) for k in STAGES},
                "clip_s": _median([r.get("clip_s") for r in rs]), "first_unit_chars": _median([r.get("first_unit_chars") for r in brain]),
                "first_unit_words": _median([r.get("first_unit_words") for r in brain]),
                "reply_words": _median([r.get("reply_words") for r in brain]),
                "fact_ok": [sum(1 for r in brain if r.get("fact_ok") is True), sum(1 for r in brain if r.get("fact_ok") is not None)],
                "e2e_est_s": round(first + TAIL_S + CARRIED_DELIVER_S + CARRIED_SINK_S, 3) if first else None}
    return out


def parse_conditions(spec: str) -> dict:
    """``name:KEY=V,KEY=V;name2:...`` -> {name: {KEY: V}}. An empty flag list is the baseline."""
    conds = {}
    for part in spec.split(";"):
        name, _, flags = part.partition(":")
        conds[name.strip()] = dict(kv.split("=", 1) for kv in flags.split(",") if "=" in kv)
    return conds


EAR_REPLIES = ["Sure, I can help with that, and I will keep it short.",
               "Honestly, I think the best thing you can do is rest, drink some water, and take a short walk later.",
               "You have two things tomorrow, a dentist visit at nine and lunch with your sister at noon.",
               "Octopuses have three hearts, and two of them only pump blood through the gills."]


async def ear_check(out: Path) -> None:
    import numpy as np
    from voice_first_sound import extract_first_clause
    from tts_waterfall import _synthesize_kokoro_sidecar as synth

    def pcm(wav: bytes):
        with wave.open(io.BytesIO(wav), "rb") as wf:
            return wf.getparams(), np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    def trim(a):   # what the Pi daemon does to every chunk (zoe_voice_daemon._trim_chunk_silence): 20 ms lead, 130 ms tail
        loud = np.where(np.abs(a) > max(int(np.abs(a).max() * 0.02), 96))[0]
        return a[max(0, loud[0] - 480): loud[-1] + 1 + 3120]

    out.mkdir(parents=True, exist_ok=True)
    for n, reply in enumerate(EAR_REPLIES, 1):
        clause, rest = extract_first_clause(reply, min_chars=24, min_words=4)
        whole, a, b = await synth(reply), await synth(clause or ""), await synth(rest)
        if whole and a and b:
            params = pcm(whole)[0]
            for name, frames in (("A", trim(pcm(whole)[1])), ("B", np.concatenate([trim(pcm(a)[1]), trim(pcm(b)[1])]))):
                with wave.open(str(out / f"{name}_{n}.wav"), "wb") as wf:
                    wf.setparams(params)
                    wf.writeframes(frames.tobytes())
            print(f"{n}: B = {clause!r} + {rest!r}")


async def amain(args) -> int:
    _load_env(resolve_service_dir(None) / ".env")
    code_dir = REPO / "services" / "zoe-data"
    sys.path.insert(0, str(code_dir))
    os.chdir(code_dir)
    if args.ear_check:
        await ear_check(Path(args.ear_check))
        return 0
    os.environ.setdefault("ZOE_ROUTER_ENABLED", "1")
    from db_pool import init_pool
    await init_pool()
    import semantic_router as sr
    sr.warm()
    stamp_payload_ready()
    conds, stamp = parse_conditions(args.conditions), int(time.time())
    managed = {k for flags in conds.values() for k in flags} | {"ZOE_FIRST_SOUND_CLAUSE", "ZOE_FIRST_SOUND_TOOL_ACK",
                                                                  "ZOE_FIRST_SOUND_NARRATION_EARLY"}
    saved = {k: os.environ.get(k) for k in managed}

    def apply(flags: dict) -> None:   # every managed flag is reset first, so a condition never inherits another's
        for k in managed:
            os.environ.pop(k, None)
        os.environ.update(flags)

    shapes = [x for x in args.shapes.split(",") if x in SHAPES]
    plan = [(s, SHAPES[s][i]) for i in range(max(len(SHAPES[x]) for x in shapes))
            for s in shapes if i < len(SHAPES[s])]   # round-robin over shapes: a time-budget cut hits all evenly
    plan = plan[args.offset:]
    plan = plan[: args.limit] if args.limit else plan
    print(f"first-sound probe: {len(plan)} prompts x {list(conds)}, sessions=replay-ttfa-<cond>-{stamp}-<n>", flush=True)
    rows, t_start = [], time.monotonic()
    try:
        apply({})
        await run_turn("chat", await prepare("Hello there, how are you today?"), args.user, "warm", f"replay-ttfa-warm-{stamp}")
        for n, (shape, prompt) in enumerate(plan, 1 + args.offset):   # n keeps counting across chunks: the rotation continues
            waited = 0.0
            if gate_refusals(0):   # a landing / window started (memory is checked at the start only: this process is part
                fcntl.flock(args.lock_fh, fcntl.LOCK_UN)   # of what it measures): hand the lock back, never race it
                while gate_refusals(0) and waited < 1800:
                    await asyncio.sleep(10)
                    waited += 10
                await asyncio.to_thread(fcntl.flock, args.lock_fh, fcntl.LOCK_EX)
            t_start += waited
            if time.monotonic() - t_start > 1100:
                print(f"!! time budget {1100}s reached after {n - 1} prompts", file=sys.stderr)
                break
            try:
                prep = await prepare(prompt)
            except Exception as exc:  # noqa: BLE001
                prep = {"error": f"{type(exc).__name__}: {exc}"}
            if prep.get("error"):
                rows.append({"shape": shape, "cond": "prep", **prep})
                continue
            names = list(conds)
            names = names[n % len(names):] + names[:n % len(names)]   # rotate the order: no condition always goes first
            for cond in names:
                apply(conds[cond])
                try:
                    rec = await run_turn(shape, prep, args.user, cond, f"replay-ttfa-{cond}-{stamp}-{n}")
                except Exception as exc:  # noqa: BLE001
                    rec = {"shape": shape, "cond": cond, "error": f"{type(exc).__name__}: {exc}"}
                rec["n"] = n   # the prompt's place in the plan: the key that pairs the conditions
                rows.append(rec)
                print(f"[{n:3}/{len(plan)}] {shape:6} {cond:11} {str(rec.get('path', rec.get('error'))):26} "
                      f"stt={rec.get('stt_s')} first_audio={rec.get('first_audio_s')}", flush=True)
                await asyncio.sleep(0.5)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    spans = [r["wall"] for r in rows if len(r.get("wall", [])) == 2]
    if spans:   # llama's own timings: one journal read for the whole run, attributed to turns by wall time
        calls = llama_timings(min(a for a, _ in spans), max(b for _, b in spans))
        for r in rows:
            if len(r.get("wall", [])) == 2:
                r["llama"] = [c for c in calls if r["wall"][0] - 0.2 <= c["at"] <= r["wall"][1] + 1.5]
    summary = summarize(rows)
    print(json.dumps(summary, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps({
            "when": dt.datetime.now().isoformat(timespec="seconds"), "conditions": conds,
            "carried": {"tail_s": TAIL_S, "deliver_s": CARRIED_DELIVER_S, "sink_s": CARRIED_SINK_S},
            "summary": summary, "rows": rows}, indent=1))
    return 0


@contextlib.contextmanager
def _environment_restored():
    snap = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snap)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="stop after N planned prompts")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N planned prompts (run a long plan in chunks under the 1100 s budget)")
    ap.add_argument("--shapes", default=",".join(DEFAULT_SHAPES), help="comma list from memory,tool,chat,fact,care")
    ap.add_argument("--user", default="jason", help="user id for memory READS")
    ap.add_argument("--conditions", default=DEFAULT_CONDITIONS, help="'name:KEY=V,KEY=V;name2:...'; each prompt runs under all")
    ap.add_argument("--json")
    ap.add_argument("--drain", action="store_true", help="read each reply to its end (after timing) and record reply_chars/words")
    ap.add_argument("--keep-text", action="store_true", help="with --drain: also store first unit + reply text in --json (private: never commit)")
    ap.add_argument("--min-mem-mb", type=int, default=1500)
    ap.add_argument("--wait", action="store_true", help="queue (up to 50 min) for the gates and the lock instead of refusing")
    ap.add_argument("--ear-check", metavar="DIR", help="write A/B Kokoro pairs for ZOE_FIRST_SOUND_CLAUSE to DIR and exit")
    args = ap.parse_args()
    DRAIN["on"], DRAIN["text"] = args.drain or args.keep_text, args.keep_text
    if os.environ.get("ZOE_PERF") != "1":
        print("ZOE_PERF != 1 - skipping the live first-sound probe (set ZOE_PERF=1 to run).")
        return 0
    args.lock_fh, waited = open(LOCK, "a+"), 0
    while True:
        # Gates first, WITHOUT the lock: land_voice_pr takes the same lock for its replay, so holding it while waiting for
        # a landing to end would deadlock both. Then the lock (--wait queues), then the gates again.
        while args.wait and gate_refusals(args.min_mem_mb) and waited < 3000:
            time.sleep(5)
            waited += 5
        try:
            fcntl.flock(args.lock_fh, fcntl.LOCK_EX | (0 if args.wait else fcntl.LOCK_NB))
        except OSError:
            print(f"harness lock {LOCK} is held", file=sys.stderr)
            return 3
        refusals = gate_refusals(args.min_mem_mb)
        if not refusals or not args.wait or waited >= 3000:
            break
        fcntl.flock(args.lock_fh, fcntl.LOCK_UN)
    for r in refusals:
        print(f"refused: {r}", file=sys.stderr)
    if refusals:
        return 2
    os.nice(5)
    with _environment_restored():          # amain loads the service .env and pins flags: none of it outlives the run
        return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
