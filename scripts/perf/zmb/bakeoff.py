#!/usr/bin/env python3
"""The Hindsight bake-off, as one command the OWNER runs: ``scripts/perf/zmb/bakeoff_window.sh``.

This is the driver behind it. Nothing runs by itself and nothing here was run when it was written: it only becomes a process when the
owner starts the window. What a window does (and ALWAYS undoes, on every exit path):

    preflight   refuse if /tmp/zoe-brain-window.lock is held; wait until the panel has been quiet 10 min (the land_voice_pr.sh check) and no
                landing / samantha bar is running (anchored pgrep); take the lock; refuse if MemAvailable < 1.2 GB
    open        scratch Postgres (compose, 256 MB cap) -> loopback embeddings shim :11501 -> STOP llama-server.service -> Gemma clone :11500
                (the SAME model and flags, generated from `systemctl --user cat llama-server.service`, --parallel 1) -> hindsight-api :18888
    measure     Z0 / Z0-off in the lab, then H1 / H2 / H0 over the real server: store-tier cells on three seeds (time-boxed), recall latency,
                extraction JSON validity, brain-slot seconds per retained turn, RSS (PSS of every candidate PID), non-loopback connects,
                forgetting at t+0 and a REAL t+6 min after the arm's own replay
    restore     stop the clone, Hindsight, the shim and the scratch DB; `systemctl --user start llama-server.service`; poll its /health;
                release the lock. Hard cap 90 min. MemAvailable < 1.2 GB at any point aborts and restores.
    report      run-<date>.json, run-<date>.log, and a DRAFT docs/research/bakeoff-run-<date>.md with the G0-G3 table per arm and the rule's verdict

    --dry-run        print every step and the generated clone command; execute nothing that changes anything
    --restore-only   just put the live brain back (idempotent; safe to run twice; stops only what this tool started)

Every external action goes through ``Host`` (``systemctl``, ``docker``, ``ssh``, ``pgrep``, ``curl`` resolved on PATH), which is how the
tests shim them. docs/knowledge/bakeoff-howto.md is the owner's page.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "perf"))


EXIT_OK, EXIT_REFUSED, EXIT_ABORTED, EXIT_RESTORE_FAILED = 0, 2, 3, 4
UNITS = {"clone": "zoe-bakeoff-gemma.service", "hindsight": "zoe-bakeoff-hindsight.service", "shim": "zoe-bakeoff-embed.service"}
PG_CONTAINER = "zoe-bakeoff-pg"
LOOPBACK_RX = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?")
URL_RX = re.compile(r"https?://[^\s\"']+")
#: Service directives a clone must carry over from the live unit (memory protection, same weights on the CPU/IO scheduler)
CARRY_PROPS = ("MemorySwapMax", "MemoryLow", "LimitMEMLOCK", "CPUWeight", "IOWeight")


class Refused(Exception):
    """A precondition failed before anything was changed (exit 2)."""


class Aborted(Exception):
    """The window was stopped on purpose or by a guard (memory, cap, a dead server); restore runs (exit 3)."""


# ── configuration ────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Cfg:
    bakeoff_dir: Path = Path(os.environ.get("BAKEOFF_DIR", "/home/zoe/.zoe/bakeoff-2026-10"))
    lock_path: str = os.environ.get("BAKEOFF_LOCK", "/tmp/zoe-brain-window.lock")
    unit: str = os.environ.get("BAKEOFF_LIVE_UNIT", "llama-server.service")
    live_port: int = int(os.environ.get("BAKEOFF_LIVE_PORT", "11434"))
    clone_port: int = 11500
    shim_port: int = 11501
    hs_port: int = 18888
    pg_port: int = 55432
    cap_min: float = float(os.environ.get("BAKEOFF_CAP_MIN", "90"))
    reserve_min: float = 7.0                 # kept back for the restore + the report
    min_avail_mb: float = float(os.environ.get("BAKEOFF_MIN_AVAIL_MB", "1200"))
    quiet_s: float = float(os.environ.get("BAKEOFF_QUIET_S", "600"))
    quiet_wait_max_min: float = float(os.environ.get("BAKEOFF_QUIET_WAIT_MIN", "180"))
    quiet_poll_s: float = float(os.environ.get("BAKEOFF_QUIET_POLL_S", "60"))
    sample_s: float = 2.0
    health_wait_s: float = float(os.environ.get("BAKEOFF_HEALTH_WAIT_S", "180"))
    panel_host: str = os.environ.get("BAKEOFF_PANEL_HOST", "zoe-pi")
    panel_log: str = "/home/pi/.zoe-voice/voice.log"
    seeds: tuple = ()
    arms: tuple = ("H1", "H2", "H0")
    docs_dir: Optional[Path] = None
    meminfo: str = os.environ.get("BAKEOFF_MEMINFO", "/proc/meminfo")
    #: local-time maintenance windows (minutes since midnight) a window must not touch: the 01:45-03:15 nightly passes (decision record
    #: section 6) and the 04:18-04:52 nightly window the landing script also waits out. ``BAKEOFF_BLACKOUTS=none`` switches them off (tests).
    blackouts: tuple = () if os.environ.get("BAKEOFF_BLACKOUTS") == "none" else ((105, 195), (258, 292))

    @property
    def compose(self) -> Path:
        return self.bakeoff_dir / "scratch-postgres.compose.yml"

    @property
    def hs_python(self) -> Path:
        return self.bakeoff_dir / "hindsight-venv" / "bin" / "python"

    @property
    def hs_bin(self) -> Path:
        return self.bakeoff_dir / "hindsight-venv" / "bin" / "hindsight-api"

    @property
    def marker(self) -> Path:
        return self.bakeoff_dir / "WINDOW_OPEN"


# ── the host: every external effect goes through here ────────────────────────

@dataclasses.dataclass
class Result:
    rc: int
    out: str = ""


class Host:
    """Real execution. ``run`` resolves the program on PATH (the tests put shims first)."""

    def __init__(self, log: "Callable[[str], None]"):
        self.log = log

    def run(self, argv: "list[str]", timeout: float = 60.0, mutating: bool = True, env: "Optional[dict]" = None) -> Result:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
            return Result(p.returncode, (p.stdout or "") + (p.stderr or ""))
        except subprocess.TimeoutExpired:
            return Result(124, f"timeout after {timeout:.0f}s")
        except OSError as exc:                      # not found, not executable, ...: a failed command, never a crash of the window
            return Result(127, str(exc))

    def sleep(self, s: float) -> None:
        time.sleep(s)

    def now(self) -> float:
        return time.time()

    def mono(self) -> float:
        return time.monotonic()

    def mem_available_mb(self, path: str) -> float:
        try:
            for line in Path(path).read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024.0
        except OSError:
            pass
        return 0.0

    def read(self, path: str) -> str:
        try:
            return Path(path).read_text()
        except OSError:
            return ""


class DryHost(Host):
    """Reads for real (the unit text, the panel log, pgrep); prints every command that would change something and does not run it."""

    def run(self, argv: "list[str]", timeout: float = 60.0, mutating: bool = True, env: "Optional[dict]" = None) -> Result:
        if not mutating:
            return super().run(argv, timeout, mutating, env)
        self.log("DRY-RUN would run: " + shlex.join(argv))
        return Result(0, "")

    def sleep(self, s: float) -> None:
        return None


# ── the clone: generated from the live unit, never hand-written ──────────────

def parse_unit(text: str, home: str) -> "dict[str, Any]":
    """What ``systemctl --user cat <unit>`` says the unit runs: the LAST non-empty ``ExecStart=`` (a drop-in's empty ``ExecStart=``
    resets the list, then re-declares it), ``Environment=`` pairs, and the carried [Service] properties (last definition wins)."""
    execs: "list[str]" = []
    env: "dict[str, str]" = {}
    props: "dict[str, str]" = {}
    lines, i = text.replace("\r", "").split("\n"), 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith(("#", ";", "[")):
            continue
        while line.endswith("\\") and i < len(lines):          # a continued line
            line = line[:-1].rstrip() + " " + lines[i].strip()
            i += 1
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().replace("%h", home)
        if key == "ExecStart":
            if val == "":
                execs.clear()
            else:
                execs.append(val)
        elif key == "Environment":
            for tok in shlex.split(val):
                k, _, v = tok.partition("=")
                env[k] = v
        elif key in CARRY_PROPS:
            props[key] = val
    if not execs:
        raise Refused("the live unit has no ExecStart: cannot generate the clone command")
    return {"argv": shlex.split(execs[-1]), "env": env, "props": props}


def _flag_value(argv: "list[str]", flag: str) -> "Optional[str]":
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def clone_command(unit_text: str, home: str, port: int) -> "dict[str, Any]":
    """The clone's command: the live argv with ``--port`` swapped and ``--parallel 1`` forced (and nothing else changed)."""
    u = parse_unit(unit_text, home)
    live = list(u["argv"])
    argv = list(live)
    for flag, value in (("--port", str(port)), ("--parallel", "1")):
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            argv += [flag, value]
    if _flag_value(argv, "--host") not in ("127.0.0.1", "localhost"):
        raise Refused(f"the live unit binds --host {_flag_value(argv, '--host')!r}; the clone must be loopback (refusing to copy it)")
    for must in ("--model", "--ctx-size", "--spec-type"):
        if must not in argv:
            raise Refused(f"the live ExecStart has no {must}: not the brain unit this tool expects (refusing to guess)")
    diff = [(a, b) for a, b in zip(live, argv) if a != b]
    return {"argv": argv, "live_argv": live, "env": u["env"], "props": u["props"], "diff": diff,
            "model": os.path.basename(_flag_value(argv, "--model") or "")}


def systemd_run(unit: str, argv: "list[str]", *, env: "Optional[dict]" = None, props: "Optional[dict]" = None) -> "list[str]":
    cmd = ["systemd-run", "--user", f"--unit={unit.removesuffix('.service')}", "--collect", "--quiet"]
    cmd += [f"--property={k}={v}" for k, v in (props or {}).items()]
    cmd += [f"--setenv={k}={v}" for k, v in (env or {}).items()]
    return cmd + ["--"] + argv


def strip_inline_comment(line: str) -> str:
    """systemd's ``EnvironmentFile=`` has NO inline comments: ``PORT=18888   # why`` would make the value ``18888   # why``. The example file
    is written for a shell (``. ./hindsight.env``), so a generated file must drop them: a `` #`` outside double quotes starts one."""
    if not line.strip() or line.lstrip().startswith(("#", ";")):
        return line
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote and i > 0 and line[i - 1] in " \t":
            return line[:i].rstrip()
    return line.rstrip()


def hindsight_env(example: str, cfg: Cfg, run_id: str) -> str:
    """The server environment: ``hindsight.env.example`` (every variable checked against the installed package on 2026-10-05) with the
    run's overrides. Refuses any URL that is not loopback: a bake-off that can reach out has already failed G0."""
    over = {
        "HINDSIGHT_API_LLM_BASE_URL": f"http://127.0.0.1:{cfg.clone_port}/v1",
        "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": f"http://127.0.0.1:{cfg.shim_port}/v1",
        "HINDSIGHT_API_EMBEDDINGS_OPENAI_BATCH_SIZE": "8",              # the shim's compute cap
        "HINDSIGHT_API_DATABASE_URL": f"postgresql://hindsight:hindsight@127.0.0.1:{cfg.pg_port}/hindsight",
        "HINDSIGHT_API_PORT": str(cfg.hs_port),
        "HINDSIGHT_API_HOST": "127.0.0.1",
        "HINDSIGHT_API_LLM_TRACE_ENABLED": "true",                      # scratch DB only: the JSON-validity count reads it
        "HINDSIGHT_API_ENABLE_DRY_RUN_EXTRACT": "true",
        "PYTHONPATH": str(cfg.bakeoff_dir / "egress_audit"),             # the in-process egress audit hook (sitecustomize.py)
        "EGRESS_AUDIT_LOG": str(cfg.bakeoff_dir / f"egress-{run_id}.log"),
        "HOME": str(cfg.bakeoff_dir / "hs-home"),
    }
    seen: "set[str]" = set()
    out: "list[str]" = []
    for line in example.splitlines():
        line = strip_inline_comment(line)
        key = line.split("=", 1)[0].strip()
        if key in over and not line.lstrip().startswith("#"):
            out.append(f"{key}={over[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in over.items() if k not in seen]
    text = "\n".join(out) + "\n"
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for url in URL_RX.findall(line):
            if not LOOPBACK_RX.match(url):
                raise Refused(f"the Hindsight environment names a non-loopback URL ({url.split('//')[1].split('/')[0]}): refusing to start it")
    return text


# ── preflight helpers ────────────────────────────────────────────────────────

def parse_panel_age(out: str, now: float) -> "Optional[float]":
    """Seconds since the panel's last wake/follow-up line (``land_voice_pr.sh`` convention); None = no line / unparsable."""
    last = (out or "").strip().splitlines()[-1].strip() if (out or "").strip() else ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return now - dt.datetime.strptime(last[:19], fmt).timestamp()
        except ValueError:
            continue
    return None


# ── the window ───────────────────────────────────────────────────────────────

class Window:
    def __init__(self, cfg: Cfg, host: Host, log: "Callable[[str], None]", *, dry: bool = False, run_id: str = "",
                 measure_fn: "Optional[Callable[[Window], dict]]" = None):
        self.cfg, self.host, self.log, self.dry = cfg, host, log, dry
        self.run_id = run_id or dt.datetime.now().strftime("%Y%m%d-%H%M")
        self.measure_fn = measure_fn
        self.lock_fd: "Optional[int]" = None
        self.opened = False
        self.t0 = host.mono()
        self.mem_floor = float("inf")
        self.abort_flag: "Optional[str]" = None
        self.started: "list[str]" = []           # things this window started, for the log
        self.clone: "dict[str, Any]" = {}
        self.restore_status = "not needed"

    # ── time and memory ──
    def elapsed_min(self) -> float:
        return (self.host.mono() - self.t0) / 60.0

    def time_left_s(self) -> float:
        return max(0.0, (self.cfg.cap_min - self.cfg.reserve_min - self.elapsed_min()) * 60.0)

    def mem(self) -> float:
        m = self.host.mem_available_mb(self.cfg.meminfo)
        self.mem_floor = min(self.mem_floor, m)
        return m

    def guard(self) -> None:
        """Called at every step boundary and between cells: the hard cap and the memory floor stop the window."""
        if self.abort_flag:
            raise Aborted(self.abort_flag)
        m = self.mem()
        if m < self.cfg.min_avail_mb and not self.dry:
            raise Aborted(f"MemAvailable {m:.0f} MB < {self.cfg.min_avail_mb:.0f} MB floor")
        if self.elapsed_min() >= self.cfg.cap_min and not self.dry:
            raise Aborted(f"hard cap {self.cfg.cap_min:.0f} min reached")

    # ── preflight ──
    def probe_lock(self) -> bool:
        """True when the brain-window lock is free right now (released again at once)."""
        fd = os.open(self.cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def take_lock(self) -> None:
        fd = os.open(self.cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise Refused(f"{self.cfg.lock_path} is held (a landing, the samantha bar or another window owns the brain): refusing")
        self.lock_fd = fd

    def release_lock(self) -> None:
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                os.close(self.lock_fd)
            finally:
                self.lock_fd = None

    def panel_age_s(self) -> "Optional[float]":
        grep = f"grep -a 'Wake word detected\\|Follow-up speech detected' {self.cfg.panel_log} | tail -1 | cut -c1-19"
        r = self.host.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", self.cfg.panel_host, grep], timeout=10, mutating=False)
        return parse_panel_age(r.out, self.host.now()) if r.rc == 0 else None

    def landing_running(self) -> str:
        for label, pat in (("a voice-PR landing", r"^bash .*/land_voice_pr\.sh"), ("the samantha bar", r"samantha_bar\.py|samantha_day_sim\.py")):
            if self.host.run(["pgrep", "-f", pat], mutating=False).rc == 0:
                return label
        return ""

    def busy_reason(self) -> str:
        age = self.panel_age_s()
        if age is not None and age < self.cfg.quiet_s:
            return f"the panel had a voice turn {age:.0f}s ago (needs {self.cfg.quiet_s:.0f}s quiet)"
        busy = self.landing_running()
        return f"{busy} is running" if busy else ""

    def wait_until_quiet(self) -> str:
        """Block until the panel has been quiet and nothing is landing. Returns "" (dry-run: the reason it would WAIT)."""
        deadline = self.host.mono() + self.cfg.quiet_wait_max_min * 60.0
        while True:
            why = self.busy_reason()
            if not why:
                return ""
            if self.dry:
                self.log(f"DRY-RUN: would WAIT, polling every {self.cfg.quiet_poll_s:.0f}s: {why}")
                return why
            if self.host.mono() >= deadline:
                raise Refused(f"still not quiet after {self.cfg.quiet_wait_max_min:.0f} min: {why}")
            self.log(f"waiting: {why}")
            self.host.sleep(self.cfg.quiet_poll_s)

    def blackout_conflict(self) -> str:
        """The maintenance window this run (now .. now + cap) would overlap, or ''. Refused, not waited out: the owner picks the time."""
        now = dt.datetime.fromtimestamp(self.host.now())
        start = now.hour * 60 + now.minute
        end = start + self.cfg.cap_min
        for a, b in self.cfg.blackouts:
            for shift in (0, 1440):
                if start < b + shift and end > a + shift:
                    return (f"a {self.cfg.cap_min:.0f}-minute window from {now:%H:%M} would overlap the {a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d} "
                            f"maintenance window; start it at {(b // 60) % 24:02d}:{b % 60:02d} or later (and before {(a // 60 - 2) % 24:02d}:{a % 60:02d})")
        return ""

    def preflight(self) -> None:
        clash = self.blackout_conflict()
        if clash and self.dry:
            self.log("DRY-RUN: a real run started now would be REFUSED: " + clash)
        elif clash:
            raise Refused(clash)
        if not self.probe_lock():
            raise Refused(f"{self.cfg.lock_path} is held: a landing, the bar or another window has the brain. Nothing was started.")
        for need in (self.cfg.compose, self.cfg.hs_bin, self.cfg.hs_python):
            if not need.exists():
                raise Refused(f"missing {need}: the G0 install (docs/knowledge/bakeoff-howto.md) was not done")
        found = self.host.run([str(self.cfg.hs_python), str(REPO / "scripts/perf/zmb/embed_shim.py"), "--find"], mutating=False)
        if found.rc != 0:
            raise Refused("no embedding model on disk for the loopback shim (the router's bge-small is cached under /tmp/fastembed_cache, which a "
                          "reboot clears): " + found.out.strip()[-300:] + " - nothing was started")
        if self.host.run(["systemctl", "--user", "is-active", self.cfg.unit], mutating=False).out.strip() != "active":
            raise Refused(f"{self.cfg.unit} is not active: there is no live brain to take over. Nothing was started.")
        m = self.mem()
        if m < self.cfg.min_avail_mb:
            raise Refused(f"MemAvailable {m:.0f} MB < {self.cfg.min_avail_mb:.0f} MB: refusing to start")
        waiting = self.wait_until_quiet()
        if not self.dry:
            self.take_lock()
            clash = self.blackout_conflict()                 # waiting for a quiet panel may have walked us into a maintenance window
            if clash:
                raise Refused(clash)
            why = self.busy_reason()                       # a voice turn can land while we waited for the lock
            if why:
                raise Refused(f"not quiet after taking the lock: {why}")
        self.log(f"preflight ok: lock {'free (dry-run: not taken)' if self.dry else 'held'}, MemAvailable {m:.0f} MB, "
                 + (f"NOT quiet right now ({waiting})" if waiting else "panel quiet, no landing/bar"))

    # ── open the window ──
    def wait_http(self, url: str, what: str, timeout_s: float, contains: str = "") -> None:
        t_end = self.host.mono() + timeout_s
        if self.dry:
            self.log(f"DRY-RUN: would wait (up to {timeout_s:.0f}s) for {what} at {url}")
            return
        while True:
            self.guard()
            r = self.host.run(["curl", "-sf", "-m", "3", url], mutating=False)
            if r.rc == 0 and (contains in r.out):
                return
            if self.host.mono() >= t_end:
                raise Aborted(f"{what} not healthy at {url} after {timeout_s:.0f}s")
            self.host.sleep(2.0)

    def start_unit(self, key: str, argv: "list[str]", env: "Optional[dict]" = None, props: "Optional[dict]" = None) -> None:
        r = self.host.run(systemd_run(UNITS[key], argv, env=env, props=props))
        if r.rc != 0:
            raise Aborted(f"could not start {UNITS[key]}: {r.out.strip()[:200]}")
        self.started.append(UNITS[key])

    def open_window(self) -> None:
        cfg, host = self.cfg, self.host
        unit_text = host.run(["systemctl", "--user", "cat", cfg.unit], mutating=False).out
        self.clone = clone_command(unit_text, os.environ.get("HOME", "/home/zoe"), cfg.clone_port)
        self.log("clone command (generated from `systemctl --user cat " + cfg.unit + "`): " + shlex.join(self.clone["argv"]))
        self.log("clone differs from the live ExecStart in exactly: " + ", ".join(f"{a} -> {b}" for a, b in self.clone["diff"]))
        env_text = hindsight_env((cfg.bakeoff_dir / "hindsight.env.example").read_text(), cfg, self.run_id)
        env_path = cfg.bakeoff_dir / f"hindsight-{self.run_id}.env"
        if not self.dry:
            env_path.write_text(env_text)
            (cfg.bakeoff_dir / "pgdata").mkdir(parents=True, exist_ok=True)
            cfg.marker.write_text(json.dumps({"pid": os.getpid(), "run_id": self.run_id, "started": dt.datetime.now().isoformat()}))
        self.opened = True                       # from here on, restore is mandatory
        self.log("step 1/6 scratch Postgres (256 MB cap, loopback :%d)" % cfg.pg_port)
        if host.run(["docker", "compose", "-f", str(cfg.compose), "up", "-d"], timeout=180).rc != 0:
            raise Aborted("scratch Postgres did not start")
        self.started.append(PG_CONTAINER)
        for _ in range(30):
            if self.dry:
                self.log("DRY-RUN: would wait for pg_isready in " + PG_CONTAINER)
                break
            self.guard()
            if host.run(["docker", "exec", PG_CONTAINER, "pg_isready", "-U", "hindsight"], mutating=False).rc == 0:
                break
            host.sleep(2.0)
        else:
            raise Aborted("scratch Postgres never became ready")
        self.log("step 2/6 loopback embeddings shim (:%d)" % cfg.shim_port)
        self.start_unit("shim", [str(cfg.hs_python), str(REPO / "scripts/perf/zmb/embed_shim.py"), "--serve", "--port", str(cfg.shim_port)],
                        env={"HF_HUB_OFFLINE": "1"}, props={"MemoryMax": "300M", "MemorySwapMax": "0"})
        self.wait_http(f"http://127.0.0.1:{cfg.shim_port}/health", "the embeddings shim", 90)
        self.guard()
        self.log("step 3/6 STOP the live brain (%s): the voice stack is down until restore" % cfg.unit)
        if host.run(["systemctl", "--user", "stop", cfg.unit], timeout=90).rc != 0:
            raise Aborted(f"could not stop {cfg.unit}")
        self.log("step 4/6 Gemma clone on :%d (same model and flags, --parallel 1)" % cfg.clone_port)
        self.start_unit("clone", self.clone["argv"], env=self.clone["env"], props=self.clone["props"])
        self.wait_http(f"http://127.0.0.1:{cfg.clone_port}/health", "the Gemma clone", max(cfg.health_wait_s, 240.0), contains="ok")
        self.log("step 5/6 hindsight-api (loopback :%d, egress audit hook on)" % cfg.hs_port)
        self.start_unit("hindsight", [str(cfg.hs_bin)], props={"EnvironmentFile": str(env_path), "MemoryMax": "1536M", "MemorySwapMax": "0"},
                        env={"HOME": str(cfg.bakeoff_dir / "hs-home")})
        self.wait_http(f"http://127.0.0.1:{cfg.hs_port}/health", "hindsight-api", max(cfg.health_wait_s, 300.0))   # first start runs the alembic migrations
        self.log("step 6/6 window open at %.1f min; MemAvailable %.0f MB" % (self.elapsed_min(), self.mem()))

    # ── restore: always ──
    def restore(self) -> bool:
        """Put everything back. Idempotent; stops only the units and the container this tool started; never raises."""
        cfg, host = self.cfg, self.host
        self.log("RESTORE: stopping what this window started")
        for key in ("hindsight", "clone", "shim"):
            host.run(["systemctl", "--user", "stop", UNITS[key]], timeout=60)
            host.run(["systemctl", "--user", "reset-failed", UNITS[key]])
        host.run(["docker", "compose", "-f", str(cfg.compose), "down"], timeout=120)
        self.log(f"RESTORE: starting {cfg.unit}")
        host.run(["systemctl", "--user", "start", cfg.unit], timeout=200)
        ok = False
        for _ in range(max(1, int(cfg.health_wait_s / 2))):
            if self.dry:
                self.log(f"DRY-RUN: would poll http://127.0.0.1:{cfg.live_port}/health")
                ok = True
                break
            r = host.run(["curl", "-sf", "-m", "3", f"http://127.0.0.1:{cfg.live_port}/health"], mutating=False)
            if r.rc == 0 and "ok" in r.out:
                ok = True
                break
            host.sleep(2.0)
        self.release_lock()
        if ok:
            if not self.dry and cfg.marker.exists():
                cfg.marker.unlink()
            self.restore_status = f"live brain healthy on :{cfg.live_port}"
            self.log("RESTORE: " + self.restore_status)
        else:
            self.restore_status = f"LIVE BRAIN NOT HEALTHY on :{cfg.live_port} after {cfg.health_wait_s:.0f}s - run `systemctl --user status {cfg.unit}`"
            self.log("RESTORE FAILED: " + self.restore_status)
        return ok

    # ── the whole window ──
    def run(self) -> int:
        rc, result = EXIT_OK, {}
        try:
            self.preflight()
            self.open_window()
            self.guard()
            if self.measure_fn:
                result = self.measure_fn(self)
        except Refused as exc:
            self.log(f"REFUSED: {exc}")
            rc = EXIT_REFUSED
        except Aborted as exc:
            self.log(f"ABORTED: {exc}")
            result = {"aborted": str(exc)}
            rc = EXIT_ABORTED
        except BaseException as exc:  # noqa: BLE001 - ANY failure still restores
            self.log("FAILED: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            self.log(traceback.format_exc()[-1500:])
            result = {"aborted": f"{type(exc).__name__}: {exc}"}
            rc = EXIT_ABORTED
        finally:
            restored = True
            if self.opened or self.lock_fd is not None:
                restored = self.restore()
            if not restored:
                rc = EXIT_RESTORE_FAILED
        self.result = result
        return rc


# ── logging ──────────────────────────────────────────────────────────────────

class Logger:
    def __init__(self, path: "Optional[Path]"):
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            print(line, flush=True)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")


# ── the CLI ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print every step and the generated clone command; change nothing")
    ap.add_argument("--restore-only", action="store_true", help="just restore the live brain (stop the clone, Hindsight, the shim, the scratch DB; start the unit)")
    ap.add_argument("--arms", default="H1,H2,H0", help="Hindsight arms to run, in priority order (Z0 and Z0-off always run in the lab)")
    ap.add_argument("--cap-min", type=float, default=None, help="hard cap in minutes (default 90)")
    ap.add_argument("--docs-dir", type=Path, default=None, help="where the draft markdown goes (default <repo>/docs/research)")
    return ap


def main(argv: "Optional[list[str]]" = None, host_factory: "Optional[Callable[[Callable], Host]]" = None, cfg: "Optional[Cfg]" = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg or Cfg()
    if args.cap_min:
        cfg.cap_min = args.cap_min
    cfg.arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    bad = [a for a in cfg.arms if a not in ("H0", "H1", "H2")]
    if bad:
        print(f"unknown arm(s) {', '.join(bad)} (H0, H1, H2)", file=sys.stderr)
        return EXIT_REFUSED
    cfg.docs_dir = args.docs_dir
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    cfg.bakeoff_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.bakeoff_dir / (f"restore-{stamp}.log" if args.restore_only else f"run-{stamp}.log")
    log = Logger(None if args.dry_run else log_path)
    make_host = host_factory or (lambda lg: DryHost(lg) if args.dry_run else Host(lg))
    host = make_host(log)
    if args.restore_only:
        w = Window(cfg, host, log, run_id=stamp)
        return EXIT_OK if w.restore() else EXIT_RESTORE_FAILED
    from zmb import bakeoff_measure  # noqa: E402 - imports the lab only when a window is really going to measure
    w = Window(cfg, host, log, dry=args.dry_run, run_id=stamp, measure_fn=(bakeoff_measure.dry_plan if args.dry_run else bakeoff_measure.measure))
    w.log_path = log_path
    log(("DRY-RUN " if args.dry_run else "") + f"memory bake-off window {stamp}: arms {','.join(cfg.arms)}, cap {cfg.cap_min:.0f} min, log {log_path}")
    return w.run()


if __name__ == "__main__":
    # run the package copy, so `Aborted` / `Refused` are ONE class however the tool was started
    from zmb.bakeoff import main as _main
    raise SystemExit(_main())
