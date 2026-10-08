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

Before every model start (the clone, its reflection restarts, the live brain on restore) physical memory is compacted with ``sudo -n`` when it is available, and the free-block
counts of /proc/buddyinfo are logged; a fragmented box without sudo is refused before anything is stopped (the 2026-10-08 NvMap abort; docs/knowledge/bakeoff-howto.md).

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
#: the in-process egress hook (``sitecustomize.py``) lives in the repo, so the instrument is reviewed and tested like the rest
EGRESS_AUDIT_DIR = REPO / "scripts" / "perf" / "zmb" / "egress_audit"
#: RAM lab 2026-10-06 (docs/research/bakeoff-ram-latency-optimisation-2026-10-06.md): an import trim for the Hindsight server (MCP, Gemini, the OTLP exporter are never used on
#: a loopback, OpenAI-provider-only server). It chains the egress hook, so the G0 egress instrument is unchanged. Off with BAKEOFF_LEAN=0.
LEAN_IMPORTS_DIR = REPO / "scripts" / "perf" / "zmb" / "lean_imports"
LEAN_STUBS = "fastmcp,mcp,google.genai,google.oauth2,google.auth,google.api_core,opentelemetry.exporter.otlp"


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
    #: TEST HOOK (default OFF; never set it for a real window). ``BAKEOFF_SKIP_BRAIN_STOP=1`` runs the whole window against the LIVE brain on
    #: ``live_port`` instead of a clone: the brain is neither stopped nor restarted, the clone is not started, Hindsight's LLM points at the live port.
    #: It exists so the window's phases, artifact, report and restore paths can be proven on the real stack in daylight. Numbers measured this way
    #: (brain-slot seconds, G1) share the slot with the household and are NOT bake-off results; the report says so at the top.
    skip_brain_stop: bool = os.environ.get("BAKEOFF_SKIP_BRAIN_STOP") == "1"
    #: TEST HOOK (default 0 = off): ``BAKEOFF_SMOKE_CELLS=N`` limits each Hindsight arm to ONE seed of N store cells spread over the axes, and the
    #: validity / slot phases to a handful of retains. The report is then marked a smoke run (nothing in it is a verdict).
    smoke_cells: int = int(os.environ.get("BAKEOFF_SMOKE_CELLS", "0") or 0)
    #: RAM lab 2026-10-06, all measured on the real server (the report names each saving): ``lean`` = migration isolation + a 1..2 connection pool + the import trim + docstring-free
    #: bytecode + one BLAS thread on the Hindsight server, and full graph optimisation + one malloc arena on the embeddings shim (about -85 MB of the stack, recall latency unchanged).
    #: ``BAKEOFF_LEAN=0`` restores run 1/2's exact settings. ``shim_model`` (``BAKEOFF_SHIM_MODEL``: auto = bge-small, ``minilm`` = zoe-data's live embedder) is NOT changed by default:
    #: switching it would move every H arm's recall away from run 1's. ``hm_shared_embedder``: the HM verbatim tier asks the shim for its vectors instead of loading its own ONNX session.
    lean: bool = os.environ.get("BAKEOFF_LEAN", "1") != "0"
    shim_model: str = os.environ.get("BAKEOFF_SHIM_MODEL", "auto")
    hm_shared_embedder: bool = os.environ.get("BAKEOFF_HM_SHARED_EMBEDDER", "1") != "0"
    health_wait_s: float = float(os.environ.get("BAKEOFF_HEALTH_WAIT_S", "180"))
    panel_host: str = os.environ.get("BAKEOFF_PANEL_HOST", "zoe-pi")
    panel_log: str = "/home/pi/.zoe-voice/voice.log"
    seeds: tuple = ()
    #: priority order; MPA = MemPalace operated by the AGENT (the clone brain calls its tools), HMA = MPA as the episodic tier + Hindsight (concise + observations) as the reflective tier,
    #: ZMA = Zoe's live stack (Z0e: MemoryService over Chroma + MiniLM, the authority classes, the forget ledger, the nightly passes) with MemPalace integrated
    arms: tuple = ("H1", "H2", "HM", "MPA", "HMA", "ZMA", "H0")
    #: the REFLECTION PHASE (optional, runs only if time remains): the clone is restarted with this ``--ctx-size`` (the live unit's other flags unchanged; KV cache type as live) and ONLY the
    #: reflection (K) work runs against it, for the variants ``H2@32k`` and ``HMA@32k``. ``BAKEOFF_REFLECT_CTX``; 0 switches the phase off.
    reflect_ctx: int = int(os.environ.get("BAKEOFF_REFLECT_CTX", "32768") or 0)
    #: the PARKED 12B deep-brain unit: only READ (``host.read``) to generate the 12B reflection clone from its ExecStart; never enabled, edited or started
    deep_unit: str = os.environ.get("BAKEOFF_DEEP_UNIT", "/home/zoe/.config/systemd/user/llama-server-12b-deepbrain.service.disabled")
    #: user units stopped ONLY for the 12B reflection pair (to make room for it) and started again right after on EVERY exit path; default EMPTY: nothing else is ever stopped.
    #: ``BAKEOFF_REFLECT_STOP_UNITS=kokoro-tts.service`` (a voice-only sidecar, down-time anyway during the window) frees about 2.3 GB.
    reflect_stop_units: tuple = tuple(u.strip() for u in os.environ.get("BAKEOFF_REFLECT_STOP_UNITS", "").split(",") if u.strip())
    #: Physical-memory fragmentation guard (see COMPACT_DROP). ``BAKEOFF_DROP_CACHES=0`` keeps compaction but skips dropping the page cache (compaction alone is the minimum);
    #: ``BAKEOFF_MIN_CONTIG_BLOCKS`` = how many free blocks of order >= 9 (2 MB) a clone start needs (the 2026-10-08 abort had 11; a healthy box has hundreds).
    drop_caches: bool = os.environ.get("BAKEOFF_DROP_CACHES", "1") != "0"
    buddyinfo: str = os.environ.get("BAKEOFF_BUDDYINFO", "/proc/buddyinfo")
    contig_order: int = 9
    contig_min_blocks: int = int(os.environ.get("BAKEOFF_MIN_CONTIG_BLOCKS", "64"))
    pid_wait_s: float = 30.0
    docs_dir: Optional[Path] = None
    meminfo: str = os.environ.get("BAKEOFF_MEMINFO", "/proc/meminfo")
    #: local-time maintenance windows (minutes since midnight) a window must not touch: the 01:45-03:15 nightly passes (decision record
    #: section 6) and the 04:18-04:52 nightly window the landing script also waits out. ``BAKEOFF_BLACKOUTS=none`` switches them off (tests).
    blackouts: tuple = () if os.environ.get("BAKEOFF_BLACKOUTS") == "none" else ((105, 195), (258, 292))

    @property
    def llm_port(self) -> int:
        """The port Hindsight's LLM calls go to: the clone's, or (test hook) the live brain's."""
        return self.live_port if self.skip_brain_stop else self.clone_port

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

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def file_size(self, path: str) -> "Optional[int]":
        try:
            return Path(path).stat().st_size
        except OSError:
            return None


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


def clone_command(unit_text: str, home: str, port: int, ctx_size: "Optional[int]" = None) -> "dict[str, Any]":
    """The clone's command: the live argv with ``--port`` swapped and ``--parallel 1`` forced (and nothing else changed). ``ctx_size`` is used ONLY by the
    optional reflection phase (``--ctx-size 32768``); the window's own clone never passes it, so it keeps the live unit's context."""
    u = parse_unit(unit_text, home)
    live = list(u["argv"])
    argv = list(live)
    for flag, value in (("--port", str(port)), ("--parallel", "1")) + ((("--ctx-size", str(ctx_size)),) if ctx_size else ()):
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


def deep_clone_command(unit_text: str, home: str, port: int, ctx_size: int) -> "dict[str, Any]":
    """The 12B reflection clone, generated from the PARKED deep-brain unit's text: its ExecStart verbatim with ONLY these overridden: ``--host 127.0.0.1``, ``--port``,
    ``--ctx-size``, ``--parallel 1``, and the vision flags dropped (``--mmproj <file>`` and ``--no-mmproj-offload``: no vision is needed). Everything else (the binary, the
    model, ``--cache-type-k/v q8_0``, ``--flash-attn``, ``--jinja``, ``--chat-template-kwargs``, ``--mlock``, ``--n-gpu-layers``) and the unit's Environment / carried properties stay."""
    u = parse_unit(unit_text, home)
    live = list(u["argv"])
    argv: "list[str]" = []
    i = 0
    while i < len(live):
        if live[i] == "--mmproj":
            i += 2
            continue
        if live[i] == "--no-mmproj-offload":
            i += 1
            continue
        argv.append(live[i])
        i += 1
    for flag, value in (("--host", "127.0.0.1"), ("--port", str(port)), ("--ctx-size", str(ctx_size)), ("--parallel", "1")):
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            argv += [flag, value]
    model = _flag_value(argv, "--model")
    if not model:
        raise Refused("the parked 12B unit has no --model: not the unit this tool expects (refusing to guess)")
    return {"argv": argv, "live_argv": live, "env": u["env"], "props": u["props"], "binary": argv[0], "model_path": model, "model": os.path.basename(model),
            "diff": [(a, b) for a, b in zip(live, argv) if a != b]}


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
        "HINDSIGHT_API_LLM_BASE_URL": f"http://127.0.0.1:{cfg.llm_port}/v1",
        "HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL": f"http://127.0.0.1:{cfg.shim_port}/v1",
        "HINDSIGHT_API_EMBEDDINGS_OPENAI_BATCH_SIZE": "8",              # the shim's compute cap
        "HINDSIGHT_API_DATABASE_URL": f"postgresql://hindsight:hindsight@127.0.0.1:{cfg.pg_port}/hindsight",
        "HINDSIGHT_API_PORT": str(cfg.hs_port),
        "HINDSIGHT_API_HOST": "127.0.0.1",
        "HINDSIGHT_API_LLM_TRACE_ENABLED": "true",                      # scratch DB only: the JSON-validity count reads it
        "HINDSIGHT_API_ENABLE_DRY_RUN_EXTRACT": "true",
        "PYTHONPATH": str(EGRESS_AUDIT_DIR),                             # the in-process egress audit hook (sitecustomize.py; it also blocks uvloop)
        "PYTHONDONTWRITEBYTECODE": "1",                                  # the hook directory is in the repo: leave no bytecode behind
        "ORT_DISABLE_TELEMETRY": "1",                                    # onnxruntime >= 1.30 uploads telemetry to Microsoft from C: the Python hook cannot see it
        "EGRESS_AUDIT_LOG": str(cfg.bakeoff_dir / f"egress-{run_id}.log"),
        "HOME": str(cfg.bakeoff_dir / "hs-home"),
    }
    if cfg.lean:
        over.update({"HINDSIGHT_API_MIGRATION_ISOLATION": "true",        # alembic / SQLAlchemy / psycopg2 run in a child, not in the long-lived server (-12 MB measured)
                     "HINDSIGHT_API_DB_POOL_MIN_SIZE": "1", "HINDSIGHT_API_DB_POOL_MAX_SIZE": "2",     # 2 asyncpg connections, not 8 (-4 MB server, -14 MB Postgres backends)
                     "PYTHONPATH": f"{LEAN_IMPORTS_DIR}:{EGRESS_AUDIT_DIR}", "ZMB_LEAN_STUBS": LEAN_STUBS,    # -44 MB: MCP, Gemini and the OTLP exporter are never imported
                     "PYTHONOPTIMIZE": "2", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
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


# ── physical-memory fragmentation (the 2026-10-08 abort) ─────────────────────

#: A Jetson's NvMap needs CONTIGUOUS physical blocks for a CUDA allocation. After ~24 h of test / agent churn the page cache fragments RAM: MemAvailable says 6 GB,
#: ``/proc/buddyinfo`` has no free block at order >= 10, and a freshly started llama-server dies in seconds with ``cudaMalloc failed: out of memory`` /
#: ``NvMapMemAllocInternalTagged ... error 12`` (the Gemma clone one second after the live brain stopped; then the live brain itself, on restore). The cure is to compact
#: before every model start; the operator command below is exactly what the window runs (and what it tells the owner to run when sudo is not passwordless).
COMPACT_DROP = "sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory"
COMPACT_ONLY = "echo 1 > /proc/sys/vm/compact_memory"


def compact_script(drop_caches: bool = True) -> str:
    return COMPACT_DROP if drop_caches else COMPACT_ONLY


def operator_compact_command(drop_caches: bool = True) -> str:
    return f"sudo sh -c '{compact_script(drop_caches)}'"


def parse_buddyinfo(text: str) -> "dict[int, int]":
    """``/proc/buddyinfo`` -> free blocks per order, summed over every node / zone. ``{}`` when unreadable. A block of order N is 2^N pages (order 9 = 2 MB on 4 KB pages)."""
    out: "dict[int, int]" = {}
    for line in (text or "").splitlines():
        m = re.search(r"zone\s+\S+\s+((?:\d+\s*)+)$", line.strip())
        if not m:
            continue
        for order, n in enumerate(int(x) for x in m.group(1).split()):
            out[order] = out.get(order, 0) + n
    return out


def blocks_at_or_above(frag: "dict[int, int]", min_order: int) -> int:
    return sum(n for o, n in frag.items() if o >= min_order)


def frag_summary(frag: "dict[int, int]", lo: int = 9, hi: int = 12) -> str:
    if not frag:
        return "buddyinfo unreadable"
    return "free blocks " + " ".join(f"o{o}={frag.get(o, 0)}" for o in range(lo, hi + 1)) + f" (order{lo}+ total {blocks_at_or_above(frag, lo)})"


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
        self.t0_epoch = host.now()
        self.mem_floor = float("inf")
        self.abort_flag: "Optional[str]" = None
        self.started: "list[str]" = []           # things this window started, for the log
        self.clone: "dict[str, Any]" = {}
        self.unit_text = ""
        self.stopped_extra: "list[str]" = []        # the user units the reflection phase stopped (cfg.reflect_stop_units) and has not started again yet
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
        if self.cfg.skip_brain_stop and not self.dry and self.opened:
            self.guard_panel()

    def guard_panel(self) -> None:
        """TEST HOOK only (the brain stays live): the household shares the brain slot, so a voice turn that STARTED after the window did ends it.
        Polled at most every 30 s. A panel that cannot be reached says nothing (same convention as ``land_voice_pr.sh``)."""
        now = self.host.mono()
        if now - getattr(self, "_panel_polled", -1e9) < 30.0:
            return
        self._panel_polled = now
        age = self.panel_age_s()
        if age is not None and age < self.elapsed_min() * 60.0 + 30.0:
            raise Aborted(f"a voice turn started {age:.0f}s ago while the live brain is shared (BAKEOFF_SKIP_BRAIN_STOP): stopping the window")

    # ── physical-memory fragmentation ──
    def fragmentation(self) -> "dict[int, int]":
        """Free blocks per order (sum of zones) from ``cfg.buddyinfo``; ``{}`` = unreadable."""
        return parse_buddyinfo(self.host.read(self.cfg.buddyinfo))

    def contiguous_ok(self, min_order: "Optional[int]" = None, min_blocks: "Optional[int]" = None, frag: "Optional[dict[int, int]]" = None) -> bool:
        """True when at least ``min_blocks`` free blocks of order >= ``min_order`` exist. An unreadable buddyinfo cannot be judged: True (the log line says it was unreadable)."""
        frag = self.fragmentation() if frag is None else frag
        if not frag:
            return True
        return blocks_at_or_above(frag, self.cfg.contig_order if min_order is None else min_order) >= (self.cfg.contig_min_blocks if min_blocks is None else min_blocks)

    def log_frag(self, where: str) -> "dict[int, int]":
        frag = self.fragmentation()
        self.log(f"fragmentation {where}: {frag_summary(frag, self.cfg.contig_order, self.cfg.contig_order + 3)}"
                 + ("" if self.contiguous_ok(frag=frag) else f" - FRAGMENTED (< {self.cfg.contig_min_blocks} blocks of order {self.cfg.contig_order}+)"))
        return frag

    def sudo_ok(self) -> bool:
        """Passwordless sudo available (``sudo -n true``)? A read-only probe, cached for the window."""
        if getattr(self, "_sudo", None) is None:
            self._sudo = self.host.run(["sudo", "-n", "true"], timeout=10, mutating=False).rc == 0
        return self._sudo

    def compact_memory(self, where: str) -> bool:
        """Drop the page cache and compact physical memory, ONLY when ``sudo -n true`` works; buddyinfo is logged before and after. Never raises. False = not done."""
        try:
            if not self.sudo_ok():
                self.log(f"compact_memory {where}: SKIPPED - passwordless sudo is not available. Run by hand: {operator_compact_command(self.cfg.drop_caches)}")
                return False
            before = self.log_frag(f"before compaction ({where})")
            r = self.host.run(["sudo", "-n", "sh", "-c", compact_script(self.cfg.drop_caches)], timeout=120)
            if self.dry:                                    # DryHost printed the command and ran nothing: there is no "after" to report
                return True
            if r.rc != 0:
                self.log(f"compact_memory {where}: FAILED rc={r.rc}: {r.out.strip()[:200]}")
                return False
            after = self.log_frag(f"after compaction ({where})")
            if before and after:
                self.log(f"compact_memory {where}: order{self.cfg.contig_order}+ blocks {blocks_at_or_above(before, self.cfg.contig_order)} -> {blocks_at_or_above(after, self.cfg.contig_order)}")
            return True
        except Exception as exc:  # noqa: BLE001 - compaction is an optimisation of the start, never a reason to crash the window or its restore
            self.log(f"compact_memory {where}: ERROR {type(exc).__name__}: {exc}")
            return False

    def wait_pid_gone(self, unit: str) -> None:
        """Poll ``systemctl --user show -p MainPID`` until it is 0 (the process, and with it its NvMap allocations, is gone), bounded by ``cfg.pid_wait_s``."""
        if self.dry:
            return
        t_end = self.host.mono() + self.cfg.pid_wait_s
        while True:
            out = self.host.run(["systemctl", "--user", "show", "-p", "MainPID", unit], mutating=False).out
            m = re.search(r"MainPID=(\d+)", out or "")
            if not m or int(m.group(1)) == 0:
                return
            if self.host.mono() >= t_end:
                self.log(f"{unit} MainPID {m.group(1)} still present after {self.cfg.pid_wait_s:.0f}s: compacting anyway")
                return
            self.host.sleep(1.0)

    def before_model_start(self, where: str, unit: str) -> None:
        """Called after a model server was stopped and before the next one starts: wait for the old process to be gone, compact, and warn loudly when RAM is still fragmented."""
        self.wait_pid_gone(unit)
        self.compact_memory(where)
        frag = self.fragmentation()
        if frag and not self.contiguous_ok(frag=frag):
            self.log(f"WARNING fragmentation {where}: only {blocks_at_or_above(frag, self.cfg.contig_order)} free blocks of order {self.cfg.contig_order}+ "
                     f"(< {self.cfg.contig_min_blocks}): the model start is likely to die on cudaMalloc / NvMap error 12. Operator fix: {operator_compact_command(self.cfg.drop_caches)}")

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

    #: What counts as "the brain is busy with someone else's work". ANCHORED to the interpreter / shell that RUNS the script: an unanchored
    #: ``pgrep -f samantha_bar.py`` also matches an editor, a ``tail -f`` or the very shell that was asked (and misses ``samantha_bar_conv.py``,
    #: the conversation bar, and the voice regression probe the landing script runs under ``/tmp/zoe-voice-harness.lock``).
    BUSY_PATTERNS = (
        ("a voice-PR landing", r"^bash .*/land_voice_pr\.sh"),
        ("the samantha bar", r"^\S*python\S*( -\S+)* \S*(samantha_bar|samantha_bar_conv|samantha_day_sim)\.py"),
        ("a voice regression probe", r"^\S*python\S*( -\S+)* \S*voice_regression_probe\.py"),
    )
    HARNESS_LOCK = os.environ.get("BAKEOFF_HARNESS_LOCK", "/tmp/zoe-voice-harness.lock")

    def harness_lock_held(self) -> bool:
        """The voice harness (regression probe / replay) serialises on this flock file; a held lock = a probe is using the brain."""
        try:
            fd = os.open(self.HARNESS_LOCK, os.O_RDONLY)
        except OSError:
            return False                                   # no file: nobody ever took it
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)

    def landing_running(self) -> str:
        for label, pat in self.BUSY_PATTERNS:
            if self.host.run(["pgrep", "-f", pat], mutating=False).rc == 0:
                return label
        if self.harness_lock_held():
            return f"the voice harness ({self.HARNESS_LOCK} is held)"
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
        frag0 = self.log_frag("at preflight")
        have_sudo = self.sudo_ok()
        if not self.contiguous_ok(frag=frag0) and not have_sudo:
            msg = (f"physical memory is fragmented ({blocks_at_or_above(frag0, self.cfg.contig_order)} free blocks of order {self.cfg.contig_order}+, need {self.cfg.contig_min_blocks}) "
                   f"and passwordless sudo is not available, so the window cannot compact it: the Gemma clone would die on cudaMalloc / NvMap error 12 (the 2026-10-08 abort). "
                   f"Run this, then start the window again: {operator_compact_command(self.cfg.drop_caches)}")
            if not self.dry:
                raise Refused(msg)
            self.log("DRY-RUN: a real run started now would be REFUSED: " + msg)
        waiting = self.wait_until_quiet()
        if not self.dry:
            self.take_lock()
            clash = self.blackout_conflict()                 # waiting for a quiet panel may have walked us into a maintenance window
            if clash:
                raise Refused(clash)
            why = self.busy_reason()                       # a voice turn can land while we waited for the lock
            if why:
                raise Refused(f"not quiet after taking the lock: {why}")
        contig = f"order{self.cfg.contig_order}+ blocks {blocks_at_or_above(frag0, self.cfg.contig_order)}" if frag0 else "buddyinfo unreadable"
        if have_sudo:
            self.compact_memory("at preflight")
            if not self.dry and frag0:
                frag1 = self.fragmentation()
                contig += f" -> {blocks_at_or_above(frag1, self.cfg.contig_order)}"
                if not self.contiguous_ok(frag=frag1):
                    raise Refused(f"physical memory is still fragmented after compaction ({contig}, need {self.cfg.contig_min_blocks}): the clone would die on cudaMalloc / NvMap error 12. "
                                  f"Stop the heaviest consumers (tests, agents) or reboot, then start again. Nothing was stopped.")
            elif self.dry:
                contig += " (dry-run: compaction not executed)"
        self.log(f"preflight ok: lock {'free (dry-run: not taken)' if self.dry else 'held'}, MemAvailable {m:.0f} MB, {contig}, "
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
        self.unit_text = unit_text                 # kept for the optional reflection restart (read-only: the live unit is never edited)
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
        shim_argv = [str(cfg.hs_python), str(REPO / "scripts/perf/zmb/embed_shim.py"), "--serve", "--port", str(cfg.shim_port)]
        shim_env = {"HF_HUB_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1"}
        if cfg.shim_model != "auto":
            shim_argv += ["--model", cfg.shim_model]
        if cfg.lean:
            shim_env.update({"ZMB_ORT_OPT": "all", "MALLOC_ARENA_MAX": "1"})          # measured: -15 MB serving, -22% single-text latency (docs/research/bakeoff-ram-latency-optimisation-2026-10-06.md)
        self.start_unit("shim", shim_argv, env=shim_env, props={"MemoryMax": "300M", "MemorySwapMax": "0"})
        self.wait_http(f"http://127.0.0.1:{cfg.shim_port}/health", "the embeddings shim", 90)
        self.guard()
        if cfg.skip_brain_stop:
            self.log(f"step 3/6 SKIPPED: BAKEOFF_SKIP_BRAIN_STOP=1 (test hook): {cfg.unit} stays UP and Hindsight talks to it on :{cfg.live_port}")
            self.log("step 4/6 SKIPPED: no Gemma clone (the live brain's single slot is shared with the household: this is not a bake-off measurement)")
            self.wait_http(f"http://127.0.0.1:{cfg.live_port}/health", "the live brain", 20, contains="ok")
        else:
            self.log("step 3/6 STOP the live brain (%s): the voice stack is down until restore" % cfg.unit)
            if host.run(["systemctl", "--user", "stop", cfg.unit], timeout=90).rc != 0:
                raise Aborted(f"could not stop {cfg.unit}")
            self.log_frag("after step 3 (live brain stopped)")
            self.before_model_start("before step 4 (Gemma clone)", cfg.unit)
            self.log("step 4/6 Gemma clone on :%d (same model and flags, --parallel 1)" % cfg.clone_port)
            self.log_frag("before step 4 (clone start)")
            self.start_unit("clone", self.clone["argv"], env=self.clone["env"], props=self.clone["props"])
            self.wait_http(f"http://127.0.0.1:{cfg.clone_port}/health", "the Gemma clone", max(cfg.health_wait_s, 240.0), contains="ok")
        self.log("step 5/6 hindsight-api (loopback :%d, egress audit hook on)" % cfg.hs_port)
        self.start_unit("hindsight", [str(cfg.hs_bin)], props={"EnvironmentFile": str(env_path), "MemoryMax": "1536M", "MemorySwapMax": "0"},
                        env={"HOME": str(cfg.bakeoff_dir / "hs-home")})
        self.wait_http(f"http://127.0.0.1:{cfg.hs_port}/health", "hindsight-api", max(cfg.health_wait_s, 300.0))   # first start runs the alembic migrations
        self.check_egress_hook(cfg.bakeoff_dir / f"egress-{self.run_id}.log")
        self.log("step 6/6 window open at %.1f min; MemAvailable %.0f MB" % (self.elapsed_min(), self.mem()))

    def swap_clone(self, spec: "dict[str, Any]", what: str, wait_s: float) -> None:
        """Stop THIS window's clone and start ``spec`` under the same unit name and port (so ``restore`` stops it like any clone), then poll its /health. The live unit is
        never touched: every spec is GENERATED from unit text (``systemctl --user cat`` for the 4B, the parked file's text for the 12B)."""
        cfg = self.cfg
        self.log(f"restarting the clone as {what}: " + shlex.join(spec["argv"]))
        self.host.run(["systemctl", "--user", "stop", UNITS["clone"]], timeout=90)
        self.host.run(["systemctl", "--user", "reset-failed", UNITS["clone"]])
        self.before_model_start(f"before the clone restart as {what}", UNITS["clone"])
        self.start_unit("clone", spec["argv"], env=spec["env"], props=spec["props"])
        self.wait_http(f"http://127.0.0.1:{cfg.clone_port}/health", f"the Gemma clone ({what})", wait_s, contains="ok")

    def stop_extra_units(self, units: "tuple[str, ...]") -> None:
        """Stop exactly the units the owner listed in ``cfg.reflect_stop_units`` (never anything else), remembering each so ``start_extra_units`` / ``restore`` bring it back."""
        for u in units:
            if self.host.run(["systemctl", "--user", "is-active", u], mutating=False).out.strip() != "active":
                self.log(f"{u} was not active before the window: left alone (it is not started afterwards either)")      # restore the host to its pre-window state, never past it
                continue
            self.log(f"stopping {u} for the 12B reflection pair (it is started again right after, on every exit path)")
            self.host.run(["systemctl", "--user", "stop", u], timeout=60)
            if u not in self.stopped_extra:
                self.stopped_extra.append(u)

    def start_extra_units(self) -> bool:
        """Start every unit ``stop_extra_units`` stopped and poll it until active. Idempotent, never raises (it runs inside ``finally`` and in ``restore``)."""
        ok = True
        for u in list(self.stopped_extra):
            self.host.run(["systemctl", "--user", "start", u], timeout=120)
            up = False
            for _ in range(max(1, int(self.cfg.health_wait_s / 2))):
                if self.host.run(["systemctl", "--user", "is-active", u], mutating=False).out.strip() == "active":
                    up = True
                    break
                self.host.sleep(2.0)
            self.log(f"{u} started again: {'active' if up else 'NOT ACTIVE after ' + format(self.cfg.health_wait_s, '.0f') + 's'}")
            ok = ok and up
            if up:
                self.stopped_extra.remove(u)                 # a unit that never became active stays listed: restore() retries it (and may stop the clone first)
        return ok

    def restart_clone(self, ctx_size: "Optional[int]") -> None:
        """The 4B clone again from the live unit's text, with ``--ctx-size`` swapped (``None`` = the live context: the way back after the reflection phase)."""
        spec = clone_command(self.unit_text, os.environ.get("HOME", "/home/zoe"), self.cfg.clone_port, ctx_size)
        self.swap_clone(spec, f"4B at {'--ctx-size ' + str(ctx_size) if ctx_size else 'the live context'}", max(self.cfg.health_wait_s, 240.0))

    # ── restore: always ──
    def restore_compaction(self) -> None:
        """Before the live brain is started again: compact (the clone that just died may have left RAM fragmented, and the live brain failed to restart the same way on 2026-10-08).
        Without sudo and with fragmented RAM this is a loud ALARM naming the command, never a silent failure. Never raises."""
        try:
            self.log_frag("before restarting the live brain")
            if self.sudo_ok():
                self.wait_pid_gone(UNITS["clone"])
                self.compact_memory("before restarting the live brain")
            elif not self.contiguous_ok():
                self.log(f"RESTORE ALARM: RAM is fragmented ({frag_summary(self.fragmentation(), self.cfg.contig_order, self.cfg.contig_order + 3)}) and passwordless sudo is not available: "
                         f"{self.cfg.unit} is likely to fail with cudaMalloc out of memory / NvMap error 12. Run: {operator_compact_command(self.cfg.drop_caches)} "
                         f"and then: systemctl --user start {self.cfg.unit}")
            else:
                self.log("RESTORE: sudo is not available; RAM is not fragmented, so no compaction is needed")
        except Exception as exc:  # noqa: BLE001 - restore never raises
            self.log(f"RESTORE ALARM: compaction before the live brain start errored ({type(exc).__name__}: {exc}); if it fails to start run: {operator_compact_command(self.cfg.drop_caches)}")

    def restore(self) -> bool:
        """Put everything back. Idempotent; stops only the units and the container this tool started; never raises."""
        cfg, host = self.cfg, self.host
        self.log("RESTORE: stopping what this window started")
        for key in ("hindsight", "clone", "shim"):
            if key == "clone" and cfg.skip_brain_stop:
                continue                                    # nothing was started under that name
            host.run(["systemctl", "--user", "stop", UNITS[key]], timeout=60)
            host.run(["systemctl", "--user", "reset-failed", UNITS[key]])
        if self.stopped_extra:
            self.start_extra_units()                        # a unit the reflection phase stopped for the 12B pair comes back whatever happened - AFTER the clone (maybe the 12B) is stopped
        host.run(["docker", "compose", "-f", str(cfg.compose), "down"], timeout=120)
        if cfg.skip_brain_stop:
            self.log(f"RESTORE: {cfg.unit} was never stopped (test hook): checking it is still healthy")
        else:
            self.restore_compaction()
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

    def check_egress_hook(self, log_path: Path) -> None:
        """The G0 egress gate is only as good as its hook, and run 1's hook was silently blind. The server connects to Postgres and the
        model server before it is healthy, so a live hook has written ``hook-loaded`` and at least one connect by now. If not, the window
        would end with the egress gate NOT MEASURED: stop now (the brain is back in seconds) rather than 80 minutes from now."""
        if self.dry:
            self.log(f"DRY-RUN: would require {log_path.name} to hold hook-loaded and a connect once hindsight-api is healthy")
            return
        text = ""
        for _ in range(10):
            text = self.host.read(str(log_path))
            if "hook-loaded" in text and " connect " in text:
                break
            self.host.sleep(1.5)
        n = sum(1 for ln in text.splitlines() if " connect " in ln or " getaddrinfo " in ln)
        if "hook-loaded" not in text or not n:
            raise Aborted(f"the egress hook is not live ({log_path.name}: {'no log' if not text else 'no connect seen'}): the egress gate would "
                          "be not measured. See egress_audit/sitecustomize.py (the server must start without uvloop)")
        self.log(f"egress hook live: {n} connect/DNS event(s) already logged in {log_path.name}")

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
    ap.add_argument("--arms", default="H1,H2,HM,MPA,HMA,ZMA,H0", help="arms to run, in priority order (Z0, Z0-off and Z0e always run in the lab)")
    ap.add_argument("--cap-min", type=float, default=None, help="hard cap in minutes (default 90)")
    ap.add_argument("--docs-dir", type=Path, default=None, help="where the draft markdown goes (default <repo>/docs/research)")
    return ap


def main(argv: "Optional[list[str]]" = None, host_factory: "Optional[Callable[[Callable], Host]]" = None, cfg: "Optional[Cfg]" = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg or Cfg()
    if args.cap_min:
        cfg.cap_min = args.cap_min
    cfg.arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    bad = [a for a in cfg.arms if a not in ("H0", "H1", "H2", "HM", "MPA", "HMA", "ZMA")]
    if bad:
        print(f"unknown arm(s) {', '.join(bad)} (H0, H1, H2, HM, MPA, HMA, ZMA)", file=sys.stderr)
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
    if cfg.skip_brain_stop or cfg.smoke_cells:
        log("TEST HOOK ACTIVE" + (": BAKEOFF_SKIP_BRAIN_STOP=1 (the live brain is NOT stopped; no clone)" if cfg.skip_brain_stop else "")
            + (f"; BAKEOFF_SMOKE_CELLS={cfg.smoke_cells} (one seed, {cfg.smoke_cells} cells per arm, short validity / slot phases)" if cfg.smoke_cells else "")
            + ": nothing this run reports is a bake-off result")
    return w.run()


if __name__ == "__main__":
    # run the package copy, so `Aborted` / `Refused` are ONE class however the tool was started
    from zmb.bakeoff import main as _main
    raise SystemExit(_main())
