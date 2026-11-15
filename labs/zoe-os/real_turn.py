#!/usr/bin/env python3
"""The guarded runner for ONE real brain turn set through the Zoe OS kernel. LAB ONLY.

    labs/zoe-os/run_real_turn.sh --i-own-the-slot [--dry-run] [--out DIR]

It sends a small fixed set of synthetic turns (a handful, with a hard 5-minute wall clock and a hard
per-call token cap) to a tiny lab Flue app whose ONLY tools are the kernel bridge's, which talks to
the already-running llama server. The live brain's one slot is shared with the household, so this
REFUSES unless every guard below passes; each refusal says why and exits 3 (the lock: exit 2).

Guards, in order (``refusals``): the explicit flag; the local-time window 22:00-01:50 (and the repo's
own ``harness_quiet`` hours, panel-yield and brain-window rules, and ``night_busy.sh``); the harness
lock (taken by this process, not waited for); no python voice probe process; the speech service
active; every llama slot idle; MemAvailable above the floor; zoe-data's app log untouched for N seconds
(mtime only, never content). In ``--dry-run`` the window, panel and log-quiet guards are advisory
(they cannot pass in daylight) and everything else is enforced; the model is replaced by a scripted
mock llama on an ephemeral port, so no turn reaches the real brain.

Whatever happens after the lock is taken, ``finally`` stops the lab app (its whole process group), the
bridge, the kernel and releases the lock. Synthetic people and invented words only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).parent
REPO = HERE.parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]

LOCK_PATH = "/tmp/zoe-voice-harness.lock"
LIVE_PORTS = {3578, 3579, 3582, 8000, 11434, 11436, 10201}
PROBE_RE = re.compile(r"^([^ ]*/)?python[0-9.]*( +-[^ ]+)* +[^ ]*(voice_regression_probe|measure_voice|"
                      r"measure_tts|measure_cold_first_turn)\.py")
WINDOW_OPEN, WINDOW_CLOSE = dt.time(22, 0), dt.time(1, 50)     # allowed: 22:00 -> 01:50 (wraps midnight)
BUDGET_MIN = 10
EXIT_LOCK, EXIT_REFUSED = 2, 3
WALL_CAP_S, TURN_CAP_S = 300, 90

# (member, utterance, expected tool or None, what the kernel should say)
TURNS = [("alex", "add milk to the shopping list", "list_add", "done"),
         ("alex", "remind me in ten minutes to call the dentist", "remind_set", "done"),
         ("alex", "set an alarm for seven", "alarm_set", "done"),
         ("kid", "set an alarm for six", "alarm_set", "refused"),
         ("alex", "what a lovely evening", None, None)]


# ---- guards ------------------------------------------------------------------------------------------
@dataclass
class Probes:
    """Every reading a guard takes, injectable. The defaults read the real box (read-only)."""
    now: Callable[[], dt.datetime] = dt.datetime.now
    cmdlines: Callable[[], list[str]] = lambda: _proc_cmdlines()
    slots: Callable[[str], list[dict]] = lambda url: _http_json(url.rstrip("/") + "/slots")
    mem_mb: Callable[[], int] = lambda: _mem_mb()
    log_age_s: Callable[[], float | None] = lambda: _log_age()
    speech_active: Callable[[], bool] = lambda: _speech_active()
    panel_quiet: Callable[[], bool] | None = None          # None: harness_quiet's own (a bounded ssh read)
    night_busy: Callable[[dt.datetime], str | None] | None = None
    night_jobs: Callable[[], str | None] = lambda: _night_jobs()
    voice_hold: Callable[[], str | None] = lambda: _voice_hold()


def _http_json(url: str, timeout: float = 4.0):
    return json.loads(urllib.request.urlopen(url, timeout=timeout).read())


def _proc_cmdlines() -> list[str]:
    out = []
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            out.append(p.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip())
        except OSError:
            pass
    return out


def _mem_mb() -> int:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) // 1024
    return 0


def _log_age() -> float | None:
    p = Path.home() / ".zoe-logs" / "zoe-data.app.log"
    try:
        return time.time() - p.stat().st_mtime            # mtime only: the content is never read
    except OSError:
        return None


def _speech_active() -> bool:
    return subprocess.run(["systemctl", "--user", "is-active", "kokoro-tts"], capture_output=True, text=True,
                          timeout=10).stdout.strip() == "active"


def _voice_hold() -> str | None:
    """The lander's hold file: exists and non-empty = a probe is about to run for the PR it names (never read further)."""
    p = Path.home() / ".zoe/agent-tools/coord/voice-hold"
    try:
        return "the lander's voice-hold is set" if p.stat().st_size > 0 else None
    except OSError:
        return None


def _night_jobs() -> str | None:
    for unit in ("zoe-night-window.service", "zoe-voice-regression.service"):
        st = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10).stdout.strip()
        if st in ("active", "activating", "deactivating"):
            return unit
    return None


def _harness_quiet():
    spec = importlib.util.spec_from_file_location("harness_quiet", REPO / "scripts/maintenance/harness_quiet.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["harness_quiet"] = mod          # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


def in_window(t: dt.time) -> bool:
    return t >= WINDOW_OPEN or t < WINDOW_CLOSE


NIGHT_START, NIGHT_END = dt.time(1, 50), dt.time(4, 55)      # the brain window: NEVER overridable


def window_reason(now: dt.datetime, probes: Probes, daytime_ok: bool = False) -> str | None:
    """The night maintenance span 01:50-04:55 refuses always (and a run that would still be alive at 01:50).
    Outside it, only 22:00-01:50 is allowed unless ``daytime_ok`` (--owner-approved-daytime) lifts the
    07:00-22:00 refusal: the household's waking-hours rules, and nothing else."""
    t = now.time()
    if NIGHT_START <= t < NIGHT_END:
        return f"inside the night maintenance span {NIGHT_START:%H:%M}-{NIGHT_END:%H:%M} (never overridable; it is {now:%H:%M})"
    nxt = dt.datetime.combine(now.date() + (dt.timedelta(days=1) if t >= NIGHT_START else dt.timedelta(0)), NIGHT_START)
    if now + dt.timedelta(minutes=BUDGET_MIN) > nxt:
        return f"a {BUDGET_MIN}-minute run from {now:%H:%M} would still be alive at 01:50"
    if not daytime_ok and not in_window(t):
        return f"outside the allowed window 22:00-01:50 local (it is {now:%H:%M}); --owner-approved-daytime lifts this one rule"
    hq = _harness_quiet()
    if daytime_ok:
        quiet = (probes.panel_quiet or (lambda: hq.panel_quiet(now)))()
        if not quiet:
            return "the panel was used in the last 10 min - yielding to the household"
    else:
        for verdict in (hq.check("real-turn", now=now, panel=probes.panel_quiet),):
            if not verdict.allowed:
                return verdict.reason
    v = hq.brain_window_verdict("real-turn", budget_min=BUDGET_MIN, now=now)
    if not v.allowed:
        return v.reason
    if not daytime_ok:                  # night_busy.sh pauses voice work all day by design; the override skips it
        return (probes.night_busy or _night_busy)(now)
    return None


def night_jobs_reason(probes: Probes) -> str | None:
    active = probes.night_jobs()
    return f"{active} is active (the night window / nightly voice regression owns the box)" if active else None


def _night_busy(now: dt.datetime) -> str | None:
    v = _harness_quiet().night_pause_verdict("real-turn", budget_min=BUDGET_MIN, kind="voice", now=now)
    return None if v.allowed else v.reason


def refusals(args, probes: Probes, *, held_lock: bool) -> list[tuple[str, str]]:
    """Every guard that says no: [(guard, reason)]. Empty means the run may start. ``held_lock`` is
    whether this process holds the harness lock (checked by the caller, which takes it)."""
    out: list[tuple[str, str]] = []
    advisory = args.dry_run

    def no(guard: str, reason: str | None, *, soft: bool = False) -> None:
        if reason:
            out.append((guard + (" (dry-run: advisory)" if soft and advisory else ""), reason))

    no("flag", None if args.i_own_the_slot else "pass --i-own-the-slot: this shares the household's one llama slot")
    soft = [r for r in (window_reason(probes.now(), probes, args.owner_approved_daytime),) if r]
    hold = probes.voice_hold()
    if hold:
        out.append(("voice-hold", hold))
    night = night_jobs_reason(probes)
    if night:
        out.append(("night-jobs", night))
    if soft and advisory:
        print(f"[dry-run] advisory: window: {soft[0]}")
    else:
        no("window", soft[0] if soft else None)
    no("lock", None if held_lock else f"could not take {args.lock}: another harness holds it")
    live = [c for c in probes.cmdlines() if PROBE_RE.match(c)]
    no("probe", f"a voice probe process is running ({len(live)})" if live else None)
    no("speech", None if probes.speech_active() else "kokoro-tts is not active (a landing is between stop and restart)")
    try:
        busy = [s.get("id") for s in probes.slots(args.llama_url) if s.get("is_processing")]
        no("slots", f"llama slot(s) {busy} are processing" if busy else None)
    except Exception as exc:                                   # an unreadable slot is never "idle"
        no("slots", f"cannot read {args.llama_url}/slots ({type(exc).__name__})")
    mem = probes.mem_mb()
    no("memory", f"MemAvailable {mem} MiB is below the {args.mem_floor} MiB floor" if mem < args.mem_floor else None)
    age = probes.log_age_s()
    if age is None or age < args.log_quiet_s:
        if advisory:
            print(f"[dry-run] advisory: zoe-data log: " + ("unreadable" if age is None else f"touched {age:.0f}s ago"))
        else:
            no("log-quiet", "zoe-data's app log is unreadable" if age is None else
               f"zoe-data's app log was touched {age:.0f}s ago (< {args.log_quiet_s}s: the household is active)")
    return out


# ---- the run -------------------------------------------------------------------------------------------
def take_lock(path: str):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def free_port(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def cache_state(llama_url: str, probes: Probes) -> dict:
    """Numbers only: the slot's held-prompt size and the cumulative prompt counters."""
    out: dict = {}
    try:
        slot = probes.slots(llama_url)[0]
        out["slot_prompt_tokens"] = slot.get("n_prompt_tokens")
    except Exception:
        out["slot_prompt_tokens"] = None
    try:
        text = urllib.request.urlopen(llama_url.rstrip("/") + "/metrics", timeout=4).read().decode()
        for key in ("prompt_tokens_total", "prompt_tokens_cached_total"):
            m = re.search(rf"llamacpp:{key}\s+([\d.e+]+)", text)
            out[key] = float(m.group(1)) if m else None
    except Exception:
        out["prompt_tokens_total"] = out["prompt_tokens_cached_total"] = None
    return out


def journal_numbers(since: dt.datetime) -> dict:
    try:
        spec = importlib.util.spec_from_file_location("bcr", REPO / "scripts/maintenance/brain_cache_report.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.journal(since.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as exc:
        return {"error": type(exc).__name__}


def read_turn_stream(app_url: str, sid: str, text: str, timeout: float) -> dict:
    """POST one turn (Seam-A NDJSON), read to the end. Returns the frames and timings."""
    req = urllib.request.Request(
        f"{app_url}/agents/lab/{sid}", data=json.dumps({"kind": "user", "body": text}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"})
    frames, t0, first, error = [], time.perf_counter(), None, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode().strip()
                if not line:
                    continue
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                chunk = json.loads(line)
                if isinstance(chunk, dict):
                    if chunk.get("error"):
                        error = str(chunk["error"])[:200]
                    if chunk.get("done") or chunk.get("error"):
                        break
                    continue
                frames.append(chunk)
    except Exception as exc:
        error = error or type(exc).__name__
    return {"frames": frames, "first_ms": first, "total_ms": (time.perf_counter() - t0) * 1000, "error": error}


def reduce_frames(frames: list[str]) -> list[str]:
    """The RECORDED fixture: tool frames verbatim (synthetic args, the kernel's own sentence); the
    model's free text reduced to length plus a hash."""
    out, text = [], []
    for f in frames:
        if f.startswith("__TOOL__:"):
            out.append(f)
        elif f.startswith("__THINKING__:"):
            out.append(json.dumps(f"__THINKING__:len={len(f) - 13}"))     # never kept
        else:
            text.append(f)
    joined = "".join(text)
    if joined:
        out.append(json.dumps(f"__TEXT__:len={len(joined)} sha256={hashlib.sha256(joined.encode()).hexdigest()[:16]}"))
    return [o if o.startswith("{") or o.startswith('"') else json.dumps(o) for o in out]


def tools_called(frames: list[str]) -> list[str]:
    seen: list[str] = []
    for f in frames:
        if f.startswith("__TOOL__:"):
            p = json.loads(f[len("__TOOL__:"):])
            if p.get("phase") == "start" and p.get("name") not in seen:
                seen.append(p["name"])
    return seen


class Stack:
    """Kernel process + bridge server + lab Flue app, torn down together."""

    def __init__(self, args, tmp: Path) -> None:
        self.args, self.tmp = args, tmp
        self.box = self.bridge = self.server = self.app = None
        self.app_log = tmp / "app.log"

    def up(self, llama_url: str) -> str:
        from brain_rig import BrainBox
        from zoe_os.brain.serve import BridgeServer
        self.box = BrainBox()
        self.box.household()
        self.bridge = self.box.bridge()
        self.token = os.urandom(16).hex()
        self.server = BridgeServer(self.bridge, self.token).start()
        tools_file = self.tmp / "tools.json"
        tools_file.write_text(json.dumps(self.bridge.tools()))
        port = self.args.app_port
        if port in LIVE_PORTS or not free_port(port):
            raise RuntimeError(f"app port {port} is a live port or already in use")
        env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp), "PORT": str(port), "HOST": "127.0.0.1",
               "ZOE_OS_TOOLS_FILE": str(tools_file), "ZOE_OS_BRIDGE_URL": f"http://127.0.0.1:{self.server.port}",
               "ZOE_OS_LAB_LLAMA": llama_url.rstrip("/") + "/v1", "ZOE_OS_LAB_MAX_TOKENS": str(self.args.max_tokens)}
        dist = HERE / "brain-lab-app" / "dist" / "server.mjs"
        if not dist.exists():
            raise RuntimeError("lab app not built: run labs/zoe-os/prep_real_turn.sh first (in daylight)")
        self.app = subprocess.Popen(["node", str(dist)], cwd=HERE / "brain-lab-app", env=env, start_new_session=True,
                                    stdout=open(self.app_log, "w"), stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        for _ in range(80):
            if self.app.poll() is not None:
                raise RuntimeError(f"lab app exited early ({self.app.returncode}); see {self.app_log}")
            try:
                urllib.request.urlopen(url + "/health", timeout=1).read()
                return url
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("lab app did not become healthy in 40 s")

    def down(self) -> None:
        if self.app is not None and self.app.poll() is None:
            try:
                os.killpg(self.app.pid, signal.SIGTERM)
                self.app.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.app.pid, signal.SIGKILL)
                except OSError:
                    pass
        for obj, fn in ((self.server, "stop"), (self.bridge, "close"), (self.box, "close")):
            try:
                obj and getattr(obj, fn)()
            except Exception:
                pass


def run(args, probes: Probes) -> int:
    stamp = probes.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out or Path.home() / ".zoe/agent-tools" / f"real-turn-{stamp}")
    fd = take_lock(args.lock)
    stack = mock = None
    tmp = Path(tempfile.mkdtemp(prefix="zrt-"))
    try:
        bad = refusals(args, probes, held_lock=fd is not None)
        if bad:
            for guard, why in bad:
                print(f"REFUSED [{guard}]: {why}", file=sys.stderr)
            return EXIT_LOCK if any(g == "lock" for g, _ in bad) and len(bad) == 1 else EXIT_REFUSED
        out_dir.mkdir(parents=True, exist_ok=True)
        before, t_start = cache_state(args.llama_url, probes), probes.now()
        stack = Stack(args, tmp)
        llama = args.llama_url
        if args.dry_run:
            from mock_llama import MockLlama
            mock = MockLlama().start()
            llama = mock.url
            before = cache_state(llama, probes)
        app_url = stack.up(llama)
        rows, fixture, deadline = [], [], time.monotonic() + args.wall_cap
        for n, (member, text, expect, kernel_expect) in enumerate(TURNS):
            left = deadline - time.monotonic()
            if left <= 5:
                rows.append({"n": n, "skipped": "wall-clock cap"})
                continue
            turn = stack.box.say(member, text)
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{stack.server.port}/turn", method="POST", headers={"X-Runner": stack.token},
                data=json.dumps({"member": member, "cause": turn.cause}).encode()), timeout=5).read()
            logged = len(stack.server.log)
            got = read_turn_stream(app_url, f"lab-{n}-{os.urandom(3).hex()}", text, min(TURN_CAP_S, left))
            props = stack.server.log[logged:]
            hop = sum(p["hop_ms"] for p in props)
            called = tools_called(got["frames"])
            rows.append({"n": n, "member": member, "expect": expect, "called": called,
                         "right_tool": called == ([expect] if expect else []),
                         "args_valid": bool(props) and all(p["status"] in ("done", "refused", "unconfirmed") and
                                                           "not valid" not in p["say"] for p in props) if props else None,
                         "kernel": [p["status"] for p in props], "kernel_expected": kernel_expect,
                         "model_ms": round(got["total_ms"] - hop), "hop_ms": round(hop, 2),
                         "first_frame_ms": round(got["first_ms"] or 0), "error": got["error"]})
            fixture.append({"turn": n, "text": text, "frames": reduce_frames(got["frames"]), "proposals": props})
        verify = verify_kernel(stack, rows)
        after = cache_state(llama, probes)
        (out_dir / "recorded_turns.json").write_text(json.dumps(
            {"note": "synthetic members and words; model text reduced to length+hash", "turns": fixture}, indent=1))
        (out_dir / "result.json").write_text(json.dumps({"rows": rows, "verify": verify, "before": before,
                                                        "after": after, "dry_run": args.dry_run}, indent=1))
        report(rows, verify, before, after, journal_numbers(t_start) if not args.dry_run else {}, args, out_dir)
        return 0
    finally:
        if mock is not None:
            mock.stop()
        if stack is not None:
            stack.down()
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        subprocess.run(["rm", "-rf", str(tmp)], check=False)


def verify_kernel(stack: Stack, rows: list[dict]) -> dict:
    """Receipts, why, undo, forget: the same checks the scripted test makes, on what really happened."""
    box, panel, v = stack.box, stack.box.client("panel"), {}
    state = box.sup("state")
    v["alex_effects"] = {k: bool(state[k].get("alex")) for k in ("lists", "reminders", "alarms")}
    v["kid_alarm_written"] = bool(state["alarms"].get("kid"))
    try:
        why = panel.explain(person="alex", last="action")
        v["why"] = why["sentence"]
        v["why_ok"] = "read-back confirmed" in why["sentence"]
    except Exception as exc:
        v["why_ok"] = False
        v["why_error"] = type(exc).__name__
    try:
        v["undo_verified"] = bool(panel.undo("alex", actor="alex").verified)
    except Exception as exc:
        v["undo_verified"], v["undo_error"] = False, type(exc).__name__
    first_text = TURNS[0][1].encode()
    v["words_on_disk_before_forget"] = first_text in box_disk(box)
    panel.forget("alex", actor="alex")
    box.sup("pump")
    after = box.sup("state")
    v["alex_effects_after_forget"] = {k: bool(after[k].get("alex")) for k in ("lists", "reminders", "alarms")}
    v["words_on_disk_after_forget"] = first_text in box_disk(box)
    return v


def box_disk(box) -> bytes:
    import glob
    return b"".join(open(p, "rb").read() for p in glob.glob(str(box.base / "kernel" / "zoe-os.db*")))


def report(rows, verify, before, after, journal, args, out_dir) -> None:
    print(f"\n{'DRY RUN (mock llama, no real model)' if args.dry_run else 'REAL RUN'}   out: {out_dir}\n")
    print(f"{'#':<2} {'who':<5} {'expected':<11} {'called':<13} {'right':<6} {'args ok':<8} {'kernel':<12} {'model ms':>8} {'hop ms':>7}")
    for r in rows:
        if "skipped" in r:
            print(f"{r['n']:<2} skipped: {r['skipped']}")
            continue
        print(f"{r['n']:<2} {r['member']:<5} {str(r['expect']):<11} {','.join(r['called']) or '-':<13} "
              f"{str(r['right_tool']):<6} {str(r['args_valid']):<8} {','.join(r['kernel']) or '-':<12} "
              f"{r['model_ms']:>8} {r['hop_ms']:>7}" + (f"  ERROR {r['error']}" if r["error"] else ""))
    print("\nkernel checks:", json.dumps(verify, indent=1))
    d = {k: (None if before.get(k) is None or after.get(k) is None else after[k] - before[k])
         for k in ("prompt_tokens_total", "prompt_tokens_cached_total")}
    print("\ncost to the household (numbers only):")
    print(f"  slot held {before.get('slot_prompt_tokens')} prompt tokens before the run (the household's last prompt): "
          "that is the MOST its next turn can need to re-prefill if the host cache cannot restore it [inferred upper bound]")
    print(f"  this run processed {d['prompt_tokens_total']} prompt tokens and reused {d['prompt_tokens_cached_total']} (counter deltas)")
    print(f"  llama journal over the run: {json.dumps(journal)}")


def main(argv=None, probes: Probes | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--i-own-the-slot", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--owner-approved-daytime", action="store_true",
                    help="lift ONLY the 07:00-22:00 refusal (never the 01:50-04:55 night span)")
    ap.add_argument("--out")
    ap.add_argument("--lock", default=LOCK_PATH)
    ap.add_argument("--llama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--app-port", type=int, default=3591)
    ap.add_argument("--mem-floor", type=int, default=1500)
    ap.add_argument("--log-quiet-s", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--wall-cap", type=int, default=WALL_CAP_S)
    args = ap.parse_args(argv)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: (_ for _ in ()).throw(SystemExit(130)))
    return run(args, probes or Probes())


if __name__ == "__main__":
    sys.exit(main())
