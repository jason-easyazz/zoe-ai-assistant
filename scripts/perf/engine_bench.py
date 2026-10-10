#!/usr/bin/env python3
"""engine_bench.py - head-to-head of Zoe's live llama-server against Little Gemma, on THIS box.

Question it answers (docs/knowledge/little-gemma-engine-bench-2026-10-10.md): would swapping the
serving ENGINE (llama.cpp b11194 -> cortexist/little-gemma `run-cuda-i8`) make Zoe better, on Zoe's
real prompt shape, with Zoe's real GGUFs. It MEASURES; it never deploys and never touches a live
unit file. The only live-service action is the brain-stop window (`window`), run under the shared
harness lock, with a trap + a systemd dead-man timer that restart the brain regardless.

    # 0. while the live brain is up (read-only: /apply-template + /tokenize only)
    python3 scripts/perf/engine_bench.py prep --out $OUT --repo . --ids16k
    # 1. one bounded brain-stop window (<= ~20 min). ALWAYS under the lock:
    flock /tmp/zoe-voice-harness.lock python3 scripts/perf/engine_bench.py window \\
        --out $OUT --lg-bin $LG/run-cuda-i8 --arms llama-prod,llama-greedy,lg-n4-greedy,... --budget-min 19
    # 2. tables
    python3 scripts/perf/engine_bench.py summarize --out $OUT

Method (the instrument is verified, see the record): both engines get the IDENTICAL system turn (Zoe's
live ZOE_INSTRUCTIONS + the three always-on tool declarations, rendered by llama-server's own chat
template) and identical single-line user turns. llama-server is a SECOND instance of the same binary
with the live unit's exact flags on another port, driven over /completion (raw text, so the tool-call
parser cannot confound TTFT) with cache_prompt on (prod regime: the system prefix is cached).
Little Gemma loads the system turn once via -sys (its only prefix cache) and each conversation is one
socket connection. Pass 0 is a discarded warmup; measured passes reverse the conversation order.
Memory is nvmap + anonymous RSS (the Jetson-honest figure; MemAvailable under-counts nvmap).
NvMap does not reclaim page cache: the harness drops the cache while an engine (and the brain) loads, else the
large cudaMalloc fails with error 12. Build little-gemma out of tree first (cmake -DLG_CUDA_ARCH=87, target run-cuda-i8).
"""
from __future__ import annotations

import argparse, json, os, re, shlex, signal, socket, statistics, subprocess, sys, threading, time
import http.client, urllib.request
from pathlib import Path

HOME = Path.home()
BRAIN_URL = "http://127.0.0.1:11434"
BENCH_PORT = 11435
MODEL = HOME / "models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
MTP = HOME / "models/gemma4-e4b-qat/mtp-gemma-4-E4B-it.gguf"
GPU_FREQ = Path("/sys/devices/platform/17000000.gpu/devfreq/17000000.gpu/cur_freq")
INSTR_TS = "labs/flue-zoe-brain-2x/src/agents/zoe.ts"
SOUL_TS = "labs/flue-zoe-brain-2x/src/soul.ts"
GROUPS_TS = "labs/flue-zoe-brain-2x/src/tools/tool-groups.ts"

# ---------------------------------------------------------------- workload ---------------------------
FACTS = [
    "Alex is the household owner and prefers short spoken answers", "Sam is Alex's partner and works night shifts at the hospital on Tuesdays and Thursdays",
    "Emma is their daughter and turns nine on the 21st of November", "Emma loves horses, drawing, and anything with glitter; she dislikes loud parties",
    "Emma is allergic to tree nuts and mildly lactose intolerant", "the family dog is a beagle called Biscuit who needs walking before eight",
    "Alex is learning the cello and practises Sunday mornings", "Sam is vegetarian but eats fish on Fridays", "Alex's mother Margaret lives in Perth and calls on Sundays",
    "the weekly shop is done on Saturday morning at the markets", "Sam's birthday is the third of March and Sam hates surprise parties",
    "Alex gave up coffee in September and drinks green tea instead", "the lounge room lights are dimmed to thirty percent after dinner",
    "Emma's school pick-up is at ten past three, Wednesdays are late pick-up", "Alex mentioned feeling stretched thin at work since the reorganisation",
    "Sam wants to repaint the hallway a soft green before Christmas", "the family usually goes camping at Easter near the river",
    "Emma's best friend is Priya who lives two streets away", "Alex prefers the 24 hour clock on the kitchen panel but 12 hour when spoken",
    "Biscuit is afraid of thunder and hides in the bathroom", "Sam is training for a ten kilometre fun run in December",
    "Alex asked Zoe to remember that the car service is due in November", "Emma has a violin lesson every second Thursday",
    "the kitchen panel plays soft jazz while cooking if nobody picks something", "Alex dislikes being told to relax; prefers a practical suggestion",
    "last week Alex said the afternoon slump is the hardest part of the day", "Sam's sister Jo is visiting for a long weekend in two weeks",
    "the household uses Australian date order and metric units", "Emma is learning to ride a bike without training wheels",
    "Alex said Friday dinners should need little time in the kitchen when guests are over",
]
NOTE_SENTENCES = [
    "Kickoff meeting: the group agreed the new rollout should start with the smallest site and widen only after two clean weeks.",
    "Priya will own the checklist and has asked everyone to send corrections before Wednesday evening.",
    "There was a long discussion about whether the old reporting spreadsheet can be retired; the compromise is to keep it read-only until March.",
    "Budget: the first quarter is under by about six percent, mostly because two contractors started late.",
    "Risks raised: the supplier lead time has slipped from three weeks to five, and one of the sites has no spare capacity on Fridays.",
    "Action for Sam: confirm the delivery window with the supplier and report back at the next stand-up.",
    "Action for Alex: draft the one page summary for the leadership team, plain language, no jargon, under two hundred words.",
    "Feedback from the pilot users was mostly positive; the main complaint is the login step, which takes too long on older tablets.",
    "The team decided against a big launch event and will do quiet, site by site announcements instead.",
    "Open question: who answers support calls on public holidays; nobody volunteered and it was left for the next meeting.",
    "Training: two short video sessions will be recorded, each under ten minutes, with captions for accessibility.",
    "Decision: the pilot continues for another fortnight and the go or no-go call is on the nineteenth.",
]


def build_conversations() -> list[dict]:
    mem = ("zoe-identity: you are talking with Alex (adult, household owner). zoe-memory (most relevant first): "
           + " | ".join(f"{i + 1}. {f}" for i, f in enumerate(FACTS)) + " | end of memory. ")
    note = " ".join((NOTE_SENTENCES[i % len(NOTE_SENTENCES)] + f" (item {i + 1})") for i in range(38))  # LG refuses a line > ~(8192 - system tokens) BYTES
    return [
        {"id": "c01", "cls": "chat", "turns": ["hey zoe, morning. how are you doing today?",
                                               "i didn't sleep great honestly, any idea how to get through the afternoon slump?"]},
        {"id": "c02", "cls": "chat", "turns": ["we're having people over on friday and i have no idea what to cook",
                                               "yeah something that doesn't need me in the kitchen the whole time"]},
        {"id": "c03", "cls": "chat", "turns": ["can you tell me a fun fact", "huh, i didn't know that. is it actually true?"]},
        {"id": "c04", "cls": "long", "turns": ["tell me a short bedtime story about a robot who is afraid of the dark, about a hundred words"]},
        {"id": "c05", "cls": "long", "turns": ["explain in about a hundred words how a refrigerator moves heat from the inside to the room"]},
        {"id": "c06", "cls": "long", "turns": ["give me a simple weeknight recipe for chicken and rice, with the steps, keep it quick"]},
        {"id": "c07", "cls": "ctx", "turns": [mem + "so given all that, what's a good birthday plan for emma that she'd actually enjoy?",
                                              "ok and what should i avoid?"]},
        {"id": "c08", "cls": "ctx", "turns": ["here are my notes from the meeting: " + note + " can you sum that up in two sentences?"]},
        {"id": "c09", "cls": "chat", "turns": ["i'm feeling a bit overwhelmed with everything this week",
                                               "thanks. it just helps to say it out loud", "ok i'm going to go take a walk"]},
    ]


# ---------------------------------------------------------------- prep (live brain, read-only) --------
def _post(path: str, body: dict, timeout=60) -> dict:
    req = urllib.request.Request(BRAIN_URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def extract_instructions(repo: Path) -> dict:
    """ZOE_INSTRUCTIONS + group names, evaluated from the live TS sources with node's strip-types."""
    import tempfile, shutil
    zoe = (repo / INSTR_TS).read_text().split("\n")
    a = next(i for i, l in enumerate(zoe) if l.startswith("export const ACTIVATOR_DOCTRINE"))
    b = next(i for i, l in enumerate(zoe) if l.startswith("export const ZOE_INSTRUCTIONS"))
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(repo / SOUL_TS, Path(d) / "soul.ts"); shutil.copy(repo / GROUPS_TS, Path(d) / "tool-groups.ts")
        (Path(d) / "gen.ts").write_text(
            "import { GROUP_SUMMARY, GROUP_NAMES } from './tool-groups.ts';\nimport { ZOE_SOUL } from './soul.ts';\n"
            + "\n".join(zoe[a:b + 1]) + "\nconsole.log(JSON.stringify({sys: ZOE_INSTRUCTIONS, names: GROUP_NAMES, summary: GROUP_SUMMARY}));\n")
        r = subprocess.run(["node", "--experimental-strip-types", str(Path(d) / "gen.ts")], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def tool_decls(d: dict) -> list[dict]:
    fn = lambda name, desc, params: {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}
    return [
        fn("get_time", "Get the current time and date. Use when the user asks what time it is, what today's date or day is, or anything that needs the current wall clock.", {"type": "object", "properties": {}}),
        fn("recall_memory", "Recall what Zoe knows about the user - their stored name, facts, preferences, relationships, and context. This is your ONLY source of what's actually stored about the user; you do NOT know it on your own. You MUST call this tool BEFORE you ever say you remember, know, or DON'T remember/know anything about the user. Use it whenever the user asks what you know/remember about them, their name, their preferences, or whenever personal context would help.", {"type": "object", "properties": {"query": {"type": "string"}}}),
        fn("activate_abilities", "Unlock a group of additional tools when the user asks for something none of your currently available tools can do. Groups: " + d["summary"] + ". After it returns, call the unlocked tool you need.",
           {"type": "object", "properties": {"group": {"type": "string", "enum": d["names"], "description": "The ability group to unlock - one of: " + ", ".join(d["names"]) + "."}}, "required": ["group"]}),
    ]


def ntok(text: str) -> int:
    return len(_post("/tokenize", {"content": text, "add_special": False, "parse_special": True})["tokens"])


def cmd_prep(a) -> None:
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    d = extract_instructions(Path(a.repo))
    rendered = _post("/apply-template", {"messages": [{"role": "system", "content": d["sys"]}, {"role": "user", "content": "hi"}],
                                         "tools": tool_decls(d)})["prompt"]
    m = re.match(r"<\|turn>system\n(.*?)<turn\|>\n<\|turn>user\n", rendered, re.S)
    system = m.group(1)
    convs = build_conversations()
    for c in convs:
        assert all("\n" not in t for t in c["turns"]), "LG turns are lines"
        c["turn_tokens"] = [ntok(t) for t in c["turns"]]
        c["turn_bytes"] = [len(t.encode()) for t in c["turns"]]
    wl = {"system": system, "system_tokens": ntok("<|turn>system\n" + system + "<turn|>\n"), "system_bytes": len(system.encode()),
          "instructions_chars": len(d["sys"]), "convs": convs}
    (out / "workload.json").write_text(json.dumps(wl, indent=1))
    (out / "sys.txt").write_text(system)
    print(json.dumps({"system_tokens": wl["system_tokens"], "system_bytes": wl["system_bytes"],
                      "turns": [(c["id"], c["turn_tokens"], c["turn_bytes"]) for c in convs]}))
    if a.ids16k:  # own corpus-selected 16K draft-vocab list (the authors' lists are not published)
        from collections import Counter
        cnt: Counter = Counter()
        text = "".join(p.read_text(errors="ignore") for p in sorted((Path(a.repo) / "docs").rglob("*.md"))[:400])[:3_000_000]
        for i in range(0, len(text), 40_000):
            cnt.update(_post("/tokenize", {"content": text[i:i + 40_000], "add_special": False, "parse_special": False}, 120)["tokens"])
        ids = list(range(512)) + [t for t, _ in cnt.most_common() if t >= 512]
        ids = ids[:16384]
        import struct
        (out / "ids16k.bin").write_bytes(b"".join(struct.pack("<I", t) for t in ids))
        print("ids16k:", len(ids), "distinct corpus ids:", len(cnt))


# ---------------------------------------------------------------- measurement helpers ------------------
def meminfo(key="MemAvailable") -> int:
    for l in open("/proc/meminfo"):
        if l.startswith(key + ":"):
            return int(l.split()[1])
    return -1


def nvmap_kb(pid: int) -> int:
    try:
        o = subprocess.run(["sudo", "-n", "cat", "/sys/kernel/debug/nvmap/iovmm/clients"], capture_output=True, text=True, timeout=10).stdout
        return sum(int(re.match(r"(\d+)K", l.split()[-1]).group(1)) for l in o.splitlines() if re.search(rf"\s{pid}\s", l))
    except Exception:
        return -1


def smaps(pid: int) -> dict:
    r = {}
    try:
        for l in open(f"/proc/{pid}/smaps_rollup"):
            k, _, v = l.partition(":")
            if k in ("Rss", "Pss", "Anonymous", "Locked"):
                r[k] = int(v.split()[0])
    except OSError:
        pass
    return r


class Guard(threading.Thread):
    """Samples MemAvailable (min watermark) and SIGKILLs the engine if the box is about to OOM."""
    def __init__(self, proc: subprocess.Popen, floor_kb=600_000):
        super().__init__(daemon=True)
        self.proc, self.floor, self.stop_ev, self.min_kb, self.tripped = proc, floor_kb, threading.Event(), 10**9, False

    def run(self):
        while not self.stop_ev.is_set():
            m = meminfo()
            self.min_kb = min(self.min_kb, m)
            if m < self.floor:
                self.tripped = True
                try: os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError: pass
                return
            time.sleep(0.05)


def drop_caches(level=1) -> None:
    subprocess.run(["sudo", "-n", "sh", "-c", f"echo {level} > /proc/sys/vm/drop_caches"], check=False, timeout=30)


class DropLoop(threading.Thread):
    """Jetson NvMap will not reclaim page cache: a multi-GB cudaMalloc fails (error 12) once the model file has
    filled the cache, even with 6 GB 'available' (measured 2026-10-10: it blocked an engine load AND the brain
    restore for 14 min). Dropping the cache continuously while an engine loads keeps MemFree real."""
    def __init__(self):
        super().__init__(daemon=True); self.ev = threading.Event()
    def run(self):
        while not self.ev.is_set():
            drop_caches(); time.sleep(0.25)


# ---------------------------------------------------------------- engines ----------------------------
def unit_argv() -> list[str]:
    """The live unit's effective ExecStart (the drop-in's last `ExecStart=` wins), %h expanded."""
    txt = subprocess.run(["systemctl", "--user", "cat", "llama-server"], capture_output=True, text=True, check=True).stdout
    blocks = re.findall(r"^ExecStart=(.*?)(?=^\S|\Z)", txt, re.S | re.M)
    cmd = [b for b in blocks if b.strip()][-1].replace("\\\n", " ").replace("%h", str(HOME))
    argv = shlex.split(cmd)
    i = argv.index("--port"); argv[i + 1] = str(BENCH_PORT)
    return argv


class Llama:
    def __init__(self, out: Path, greedy: bool):
        self.greedy, self.out = greedy, out
        argv = unit_argv()
        env = dict(os.environ, LD_LIBRARY_PATH=str(HOME / "llama.cpp-b11194/build-jetson/bin"))
        self.log = open(out / "llama.log", "w")
        self.argv = argv
        self.proc = subprocess.Popen(argv, env=env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(240):
            if self.proc.poll() is not None: raise RuntimeError("llama-server exited: see llama.log")
            try:
                if b'"ok"' in urllib.request.urlopen(f"http://127.0.0.1:{BENCH_PORT}/health", timeout=2).read(): break
            except Exception: time.sleep(1)
        else: raise RuntimeError("llama-server never healthy")
        self.history: dict[str, str] = {}

    def begin(self, system: str): self.system = system; self.text = "<|turn>system\n" + system + "<turn|>\n"

    def turn(self, user: str) -> dict:
        self.text += "<|turn>user\n" + user + "<turn|>\n<|turn>model\n"
        body = {"prompt": self.text, "n_predict": 700, "stream": True, "cache_prompt": True}
        if self.greedy: body.update(temperature=0.0, top_k=1)
        c = http.client.HTTPConnection("127.0.0.1", BENCH_PORT, timeout=120)
        t_send = time.perf_counter()
        c.request("POST", "/completion", json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse(); t_first = t_last = None; parts, fin = [], {}
        for raw in r:
            if not raw.startswith(b"data: "): continue
            j = json.loads(raw[6:]); now = time.perf_counter()
            if j.get("content"):
                if t_first is None: t_first = now
                t_last = now; parts.append(j["content"])
            if j.get("stop"): fin = j
        c.close()
        text = "".join(parts); tm = fin.get("timings", {})
        self.text += text + "<turn|>\n"
        return {"text": text, "t_first": (t_first - t_send) if t_first else None,
                "decode_s": (t_last - t_first) if t_first and t_last else None, "n_out": tm.get("predicted_n"),
                "srv_decode_tps": tm.get("predicted_per_second"), "prompt_n": tm.get("prompt_n"),
                "prefill_s": (tm.get("prompt_ms") or 0) / 1000, "srv_prefill_tps": tm.get("prompt_per_second"),
                "cached": fin.get("tokens_cached"), "evaluated": fin.get("tokens_evaluated"),
                "draft_n": tm.get("draft_n"), "draft_acc": tm.get("draft_n_accepted")}

    def end_conv(self): pass
    def stop(self):
        try: os.killpg(self.proc.pid, signal.SIGTERM); self.proc.wait(30)
        except Exception:
            try: os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError: pass


class LG:
    TURN_RE = re.compile(r"turn: (\d+) in ([\d.]+)s \(([\d.]+) tok/s\), (\d+) out ([\d.]+)s \(([\d.]+) tok/s\), ttft ([\d.]+)s")
    MTP_RE = re.compile(r"mtp:\s+accepted (\d+)/(\d+) drafts")

    def __init__(self, out: Path, binpath: str, n: int | None, temp: float | None, ids: Path | None, sysfile: Path):
        self.sock_path = str(out / "lg.sock")
        argv = [binpath, "-m", str(MODEL), "-sys", str(sysfile), "-s", self.sock_path]
        if n is not None: argv[3:3] = ["-mtp", str(MTP)]
        if temp: argv += ["-temp", str(temp), "-seed", "1"]
        env = dict(os.environ)
        if n is not None: env["LG_MTP_N"] = str(n)
        if ids: env["LG_MTP_IDS"] = str(ids)
        self.err_path = out / "lg.stderr"; self.errf = open(self.err_path, "w")
        self.argv = argv
        self.proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=self.errf, start_new_session=True)
        for _ in range(300):
            if self.proc.poll() is not None: raise RuntimeError("little-gemma exited: " + self.err_path.read_text()[-500:])
            if "listening on" in self.err_path.read_text(errors="ignore"): break
            time.sleep(1)
        else: raise RuntimeError("little-gemma never listening")
        self.sock = None; self.seen = 0

    def begin(self, system: str):
        self.sock = socket.socket(socket.AF_UNIX); self.sock.settimeout(120); self.sock.connect(self.sock_path)

    def _stats(self):
        for _ in range(100):
            ms = self.TURN_RE.findall(self.err_path.read_text(errors="ignore"))
            if len(ms) > self.seen: break
            time.sleep(0.05)
        txt = self.err_path.read_text(errors="ignore")
        ms = self.TURN_RE.findall(txt); self.seen = len(ms)
        mt = self.MTP_RE.findall(txt)
        return ms[-1] if ms else None, (mt[-1] if mt else None)

    def turn(self, user: str) -> dict:
        t_send = time.perf_counter(); self.sock.sendall(user.encode() + b"\n")
        buf = b""; t_first = t_last = None
        while True:
            chunk = self.sock.recv(65536)
            if not chunk: break
            now = time.perf_counter()
            if t_first is None: t_first = now
            t_last = now; buf += chunk
            if b"<turn|>" in buf: break
        s, mt = self._stats()
        text = buf.decode(errors="replace")
        r = {"text": text.replace("<turn|>", ""), "t_first": (t_first - t_send) if t_first else None,
             "decode_s": (t_last - t_first) if t_first and t_last else None}
        if s:
            r.update(prompt_n=int(s[0]), prefill_s=float(s[1]), srv_prefill_tps=float(s[2]), n_out=int(s[3]),
                     srv_decode_tps=float(s[5]), srv_ttft=float(s[6]))
        if mt: r.update(draft_acc=int(mt[0]), draft_n=int(mt[1]))
        return r

    def end_conv(self):
        try: self.sock.close()
        except OSError: pass
        self.sock = None; time.sleep(0.3)

    def stop(self):
        try: os.killpg(self.proc.pid, signal.SIGTERM); self.proc.wait(20)
        except Exception:
            try: os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError: pass


# ---------------------------------------------------------------- arms ----------------------------
def arm_spec(name: str):
    """llama-prod | llama-greedy | lg-plain-greedy | lg-n{2,3,4}-{greedy,t07} | lg-n4-sel-greedy"""
    if name.startswith("llama"): return ("llama", name == "llama-greedy", None, None, False)
    m = re.fullmatch(r"lg-(plain|n(\d))(-sel)?-(greedy|t07)", name)
    if not m: raise SystemExit("bad arm " + name)
    return ("lg", False, None if m.group(1) == "plain" else int(m.group(2)), 0.7 if m.group(4) == "t07" else None, bool(m.group(3)))


def run_arm(name: str, out: Path, wl: dict, a) -> dict:
    kind, greedy, n, temp, sel = arm_spec(name)
    adir = out / name; adir.mkdir(exist_ok=True)
    base = meminfo(); gpu0 = GPU_FREQ.read_text().strip()
    t0 = time.time()
    dl = DropLoop(); dl.start()
    try:
        eng = Llama(adir, greedy) if kind == "llama" else LG(adir, a.lg_bin, n, temp, (out / "ids16k.bin") if sel else None, out / "sys.txt")
    finally:
        dl.ev.set()
    guard = Guard(eng.proc); guard.start()
    load_s = time.time() - t0
    rows, convs = [], wl["convs"]
    try:
        for p in range(a.passes + 1):                       # pass 0 = warmup, discarded
            order = convs if p % 2 == 1 or p == 0 else list(reversed(convs))
            for c in order:
                eng.begin(wl["system"])
                for ti, u in enumerate(c["turns"]):
                    if guard.tripped or eng.proc.poll() is not None: raise RuntimeError("engine died / OOM guard tripped")
                    r = eng.turn(u); r.update(arm=name, pass_=p, conv=c["id"], cls=c["cls"], turn=ti + 1, user_tokens=c["turn_tokens"][ti])
                    rows.append(r)
                eng.end_conv()
            if p == 0:
                mem = {"nvmap_kb": nvmap_kb(eng.proc.pid), **smaps(eng.proc.pid), "memavail_after_load_kb": meminfo()}
        mem.update(nvmap_kb_end=nvmap_kb(eng.proc.pid), smaps_end=smaps(eng.proc.pid))
    finally:
        guard.stop_ev.set(); gpu1 = GPU_FREQ.read_text().strip(); eng.stop(); time.sleep(3)
    meta = {"arm": name, "argv": eng.argv, "load_s": load_s, "memavail_base_kb": base, "memavail_min_kb": guard.min_kb,
            "mem": mem, "gpu_hz_start": gpu0, "gpu_hz_end": gpu1, "tripped": guard.tripped}
    (adir / "rows.json").write_text(json.dumps(rows)); (adir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def brain_health() -> bool:
    try: return b"ok" in urllib.request.urlopen(BRAIN_URL + "/health", timeout=3).read()
    except Exception: return False


def cmd_window(a) -> None:
    out = Path(a.out); wl = json.loads((out / "workload.json").read_text())
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)
    t_start = time.time(); log("WINDOW START: stopping llama-server (brain)")
    # dead-man switch: restart the brain even if this process is SIGKILLed
    subprocess.run(["systemd-run", "--user", "--quiet", "--unit=zoe-bench-brain-restart", f"--on-active={int(a.budget_min * 60 + 300)}s",
                    "systemctl", "--user", "start", "llama-server"], check=False)
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        subprocess.run(["systemctl", "--user", "stop", "llama-server"], check=True, timeout=120)
        time.sleep(4)
        if subprocess.run(["pgrep", "-f", "llama-server.*--port 11434"], capture_output=True).stdout.strip():
            raise RuntimeError("brain still/again running; refusing to load a second E4B")
        log(f"brain stopped; MemAvailable={meminfo() // 1024} MB")
        for arm in a.arms.split(","):
            if (time.time() - t_start) / 60 > a.budget_min - a.arm_min:
                log(f"SKIP {arm}: window budget exhausted"); continue
            if meminfo() < a.min_free_kb: log(f"SKIP {arm}: MemAvailable {meminfo() // 1024} MB too low"); continue
            log(f"ARM {arm} ...")
            try:
                m = run_arm(arm, out, wl, a); log(f"ARM {arm} done: load {m['load_s']:.0f}s, min MemAvail {m['memavail_min_kb'] // 1024} MB")
            except Exception as e:                    # one bad arm must not eat the window
                log(f"ARM {arm} FAILED: {str(e)[-300:]!r}")
                subprocess.run(["pkill", "-f", "^[^ ]*run-cuda-i8 -m"], check=False)
                subprocess.run(["pkill", "-f", f"^[^ ]*llama-server .*--port {BENCH_PORT}"], check=False); time.sleep(3)
    finally:
        log("restoring brain")
        subprocess.run(["pkill", "-f", "^[^ ]*run-cuda-i8 -m"], check=False)
        subprocess.run(["pkill", "-f", f"^[^ ]*llama-server .*--port {BENCH_PORT}"], check=False)
        time.sleep(3)
        dl = DropLoop(); dl.start()
        for attempt in range(4):                      # NvMap can refuse the first tries; keep dropping the cache
            subprocess.run(["systemctl", "--user", "start", "llama-server"], check=False, timeout=400)
            if brain_health(): break
            log(f"brain not healthy after start attempt {attempt + 1}; retrying")
            subprocess.run(["systemctl", "--user", "stop", "llama-server"], check=False, timeout=60)
        dl.ev.set()
        subprocess.run(["systemctl", "--user", "stop", "zoe-bench-brain-restart.timer"], check=False, capture_output=True)
        log(f"WINDOW END: brain /health ok={brain_health()}; downtime {(time.time() - t_start) / 60:.1f} min")


# ---------------------------------------------------------------- summarize -------------------------
def med(v): v = [x for x in v if x is not None]; return statistics.median(v) if v else float("nan")


def cmd_summarize(a) -> None:
    out = Path(a.out); lines = []
    arms = sorted(p.name for p in out.iterdir() if (p / "rows.json").exists())
    ref = {}
    hdr = "| arm | decode tok/s (all, n_out>=8) | chat | long | TTFT turn1 s | TTFT turn2 s | prefill tok/s (>=300 new tok) | accept | nvmap+anon MB | min MemAvail MB |"
    lines += [hdr, "|" + "---|" * 10]
    for arm in arms:
        rows = [r for r in json.loads((out / arm / "rows.json").read_text()) if r["pass_"] > 0]
        meta = json.loads((out / arm / "meta.json").read_text())
        dec = lambda rs: (sum(r["n_out"] - 1 for r in rs) / sum(r["decode_s"] for r in rs)) if rs else float("nan")
        ok = [r for r in rows if r.get("n_out") and r["n_out"] >= 8 and r.get("decode_s")]
        per_prompt = {}
        for r in ok: per_prompt.setdefault((r["conv"], r["turn"]), []).append((r["n_out"] - 1) / r["decode_s"])
        pp = [statistics.median(v) for v in per_prompt.values()]
        t1 = [r["t_first"] for r in rows if r["turn"] == 1 and r["cls"] in ("chat",)]
        t2 = [r["t_first"] for r in rows if r["turn"] == 2 and r["cls"] in ("chat",)]
        pf = [r["srv_prefill_tps"] for r in rows if (r.get("prompt_n") or 0) >= 300 and r.get("srv_prefill_tps")]
        da = sum(r.get("draft_acc") or 0 for r in rows); dn = sum(r.get("draft_n") or 0 for r in rows)
        mem = meta["mem"]; mb = ((mem.get("nvmap_kb") or 0) + (mem.get("Anonymous") or 0)) / 1024
        lines.append(f"| {arm} | {dec(ok):.1f} (median/prompt {med(pp):.1f}, {min(pp, default=0):.1f}-{max(pp, default=0):.1f}, n={len(ok)}) "
                     f"| {dec([r for r in ok if r['cls'] == 'chat']):.1f} | {dec([r for r in ok if r['cls'] == 'long']):.1f} "
                     f"| {med(t1):.3f} | {med(t2):.3f} | {med(pf):.0f} | {(da / dn if dn else float('nan')):.2f} | {mb:.0f} | {meta['memavail_min_kb'] // 1024} |")
        ref[arm] = {(r["conv"], r["turn"], r["pass_"]): r["text"] for r in rows}
    lines += ["", "Output identity (first measured pass, same conv/turn): common-prefix chars / length, and exact equality", ""]
    names = list(ref)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            ks = [k for k in ref[x] if k in ref[y] and k[2] == 1]
            eq = sum(ref[x][k].strip() == ref[y][k].strip() for k in ks)
            def cp(s, t):
                n = 0
                while n < min(len(s), len(t)) and s[n] == t[n]: n += 1
                return n
            lines.append(f"- {x} vs {y}: exact {eq}/{len(ks)}; median common-prefix {med([cp(ref[x][k].strip(), ref[y][k].strip()) for k in ks]):.0f} chars")
    (out / "summary.md").write_text("\n".join(lines)); print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep"); p.add_argument("--out", required=True); p.add_argument("--repo", default="."); p.add_argument("--ids16k", action="store_true")
    w = sub.add_parser("window"); w.add_argument("--out", required=True); w.add_argument("--lg-bin", required=True); w.add_argument("--arms", required=True)
    w.add_argument("--passes", type=int, default=2); w.add_argument("--budget-min", type=float, default=19.0)
    w.add_argument("--arm-min", type=float, default=2.5, help="do not start an arm with less than this left"); w.add_argument("--min-free-kb", type=int, default=5_000_000)
    s = sub.add_parser("summarize"); s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"prep": cmd_prep, "window": cmd_window, "summarize": cmd_summarize}[a.cmd](a)


if __name__ == "__main__":
    main()
