#!/usr/bin/env python3
"""harness_scratch.py - a THROWAWAY Zoe for the harnesses, so the bar, the day-sim and the person bench never write the live DB (P-2).

What ``up`` builds (``down`` removes every piece; ``status`` measures RAM):

  * a scratch PostgreSQL: the SAME image as ``zoe-database`` (its image id, so the same digest), on its OWN docker bridge network
    (``zoe-harness-scratch-<tag>``, not ``zoe-network``), published on 127.0.0.1 only, capped (``--memory``, no swap, cpus, pids),
    fsync off (nothing in it is worth keeping), migrated to head by THIS checkout alembic chain - the schema is cloned from the
    migrations, never from the live database, so nothing is read from or written to the live one.
  * with ``--serve``: a second zoe-data (harness mode, its own port) and a second Flue brain sidecar (its own port and session DB)
    wired to each other. The pair gets fresh tokens (the live service refuses them), a scratch HOME / palace / logs, the live
    service FLAGS (the ``ZOE_*`` names from the service env files, minus anything that looks like a secret), and every
    house-control endpoint (Home Assistant, Music Assistant, the panel, LiveKit, ...) pointed at a black hole. The brain itself
    (llama-server) and the router sidecar are shared compute, as for any harness turn - the harness quiet hours still apply.

Guards: ``assert_not_live`` refuses a DSN on the live server (same host class and port as the live ``POSTGRES_URL``, or the default
port 5432 on this box) - every harness calls it before its first DB write. ``up`` refuses below a ``MemAvailable`` floor.
Secrets: the scratch password and tokens live only in the 0600 state file (``~/.cache/zoe/harness-scratch/<tag>.json``) and the
child environments; nothing prints them. Env NAMES only in any output.

CLI::

    harness_scratch.py up [--tag T] [--serve] [--service-dir D]     # prints the state file path and the RAM figures
    harness_scratch.py status [--tag T]
    harness_scratch.py down [--tag T]
    harness_scratch.py check-dsn --dsn-env NAME [--service-dir D]   # exit 2 when the DSN in env NAME is the live database

Pinned by ``tests/unit/test_harness_scratch.py`` (pure parts, CI) and ``ZOE_SCRATCH_PG_TEST=1`` (the real stack, opt-in).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
PREFIX = "zoe-harness-scratch"
LIVE_CONTAINER = "zoe-database"
LIVE_DEFAULT_PORT = 5432
STATE_DIR = Path.home() / ".cache" / "zoe" / "harness-scratch"
PG_MEMORY = "384m"
PG_CPUS = "1.5"
PG_USER = "zoe_scratch"
PG_DB = "zoe_scratch"
PG_ARGS = ("-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off", "-c", "shared_buffers=32MB",
           "-c", "max_connections=40", "-c", "work_mem=4MB")
#: MemAvailable floors (MB) before ``up``: Postgres alone, and the whole served stack (measured; see docs/knowledge/harness-scratch-db.md)
MIN_MEM_PG_MB = 600
MIN_MEM_SERVE_MB = 1900
BLACK_HOLE = "http://127.0.0.1:9"
#: house-control and agent endpoints the scratch zoe-data must never reach (defaults in code point at the real ones)
HOUSE_URLS = ("ZOE_HA_BRIDGE_URL", "ZOE_HA_URL", "HA_BASE_URL", "HA_URL", "HOMEASSISTANT_URL", "MUSIC_ASSISTANT_URL",
              "ZOE_YTMUSIC_POTOKEN_URL", "LIVEKIT_URL", "LIVEKIT_TURN_URL", "ZOE_AUTH_URL", "ZOE_KOKORO_SIDECAR_URL",
              "HERMES_API_URL", "OPENCLAW_GATEWAY_URL", "OPENCLAW_GATEWAY_HEALTH_URL", "ZOE_OMNIGENT_URL", "MULTICA_BASE_URL",
              "ZOE_CHEAP_PR_AGENT_URL", "ZOE_PUBLIC_URL", "MULTICA_WEBHOOK_TARGET_URL", "ZOE_OPENCLAW_GW",
              "GREPTILE_MCP_URL", "OTEL_EXPORTER_OTLP_ENDPOINT", "ZOE_RIG_LOGIN_URL")
#: names the scratch zoe-data uses to call ITSELF (their code defaults are the live :8000)
SELF_URLS = ("ZOE_SELF_URL", "ZOE_DATA_URL", "ZOE_CHAT_URL", "ZOE_BASE_URL", "ZOE_CORE_DATA_URL", "ZOE_REPLAY_BASE_URL")
#: shared compute the harness turn needs (kept from the live env when present)
COMPUTE_URLS = ("GEMMA_SERVER_URL", "LLAMA_SERVER_URL", "ZOE_LLAMA_URL", "ZOE_ROUTER_SIDECAR_URL")
SECRET_RE = re.compile(r"(TOKEN|SECRET|PASSW|_KEY$|_KEYS$|^KEY_|_KEY_|CREDENTIAL|COOKIE|PRIVATE|SALT|SIGNING|_DSN$)", re.I)
#: a flag that names a live LOCATION (a URL, a directory, a file, a database) is never copied: the scratch instance gets its own
LOCATION_RE = re.compile(r"(_URL|_URLS|_HOST|_HOSTS|_PORT|_DIR|_DIRS|_PATH|_FILE|_DB|_ROOT|_HOME|_SOCKET|_ADDR)$")
#: model caches under ~/.cache the scratch HOME links to (read-only use: the models are already there)
SHARED_MODEL_CACHES = ("chroma", "huggingface")
_LOCAL_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", "0.0.0.0", "zoe-database", "host.docker.internal", ""))


class Refused(RuntimeError):
    """The scratch stack refuses (a live DSN, too little memory, docker missing)."""


def parse_env_file(path: Path) -> dict:
    """KEY=VALUE lines (the systemd EnvironmentFile subset). Never logs a value."""
    out: dict = dict()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            out[key] = value.strip().strip('"').strip("'")
    return out


def service_env(service_dir: Path) -> dict:
    """What systemd hands the live zoe-data: the repo-root .env, then the service .env (which wins)."""
    root = Path(service_dir).resolve().parents[1]
    env = parse_env_file(root / ".env")
    env.update(parse_env_file(Path(service_dir) / ".env"))
    return env


def dsn_identity(dsn: str) -> tuple:
    """(host class, port, database) of a postgres URL; ``local`` for any loopback / the compose service name."""
    parts = urlsplit(dsn or "")
    host = (parts.hostname or "").lower()
    try:
        port = parts.port or LIVE_DEFAULT_PORT
    except ValueError:
        port = -1
    db = (parts.path or "/").lstrip("/") or ""
    return ("local" if host in _LOCAL_HOSTS else host, port, db)


def is_live_dsn(dsn: str, live_dsn: Optional[str]) -> bool:
    """True when ``dsn`` reaches the live database server: the live DSN host and port (any database on that server), or the
    default 5432 on this box. An unparsable DSN counts as live (refuse what cannot be proven scratch)."""
    host, port, _db = dsn_identity(dsn)
    if port <= 0 or not dsn:
        return True
    if host == "local" and port == LIVE_DEFAULT_PORT:
        return True
    if live_dsn:
        lhost, lport, _ldb = dsn_identity(live_dsn)
        if (host, port) == (lhost, lport):
            return True
    return False


def assert_not_live(dsn: str, live_dsn: Optional[str]) -> None:
    if is_live_dsn(dsn, live_dsn):
        raise Refused("refusing: that DSN reaches the live database server (harnesses write only to a scratch database)")


def flag_env(live_env: dict) -> dict:
    """The live service FLAGS a faithful harness instance needs: ``ZOE_*`` names that do not look like secrets and are not
    endpoints this module sets itself."""
    skip = set(HOUSE_URLS) | set(SELF_URLS) | set(COMPUTE_URLS) | {"ZOE_INTERNAL_TOKEN", "ZOE_BRAIN_TOKEN", "ZOE_FLUE_BRAIN_URL"}
    return dict((k, v) for k, v in live_env.items()
                if k.startswith("ZOE_") and k not in skip and not SECRET_RE.search(k) and not LOCATION_RE.search(k)
                and "://" not in v)


def harness_env(live_env: dict, *, root: Path, dsn: str, token: str, brain_token: str, flue_url: str, self_url: str) -> dict:
    """The scratch zoe-data environment: the live FLAGS, scratch everything else. Pure (tests pin it)."""
    env = dict(PATH="/usr/local/bin:/usr/bin:/bin", LANG="C.UTF-8", LC_ALL="C.UTF-8", TZ=os.environ.get("TZ") or "Australia/Perth")
    env.update(flag_env(live_env))
    for name in COMPUTE_URLS:
        if live_env.get(name):
            env[name] = live_env[name]
    env.setdefault("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1")
    env.update(
        HOME=str(root / "home"), POSTGRES_URL=dsn, ZOE_INTERNAL_TOKEN=token, ZOE_BRAIN_TOKEN=brain_token, ZOE_FLUE_BRAIN_URL=flue_url,
        MEMPALACE_DATA_DIR=str(root / "palace"), ZOE_DATA_DB=str(root / "zoe.db"), ZOE_LOG_DIR=str(root / "logs"),
        ZOE_ROUTER_RETRAIN_DIR=str(root / "router_retrain"), ZOE_VOICE_SAMPLE_DIR=str(root / "voice-samples"),
        ZOE_VOICE_SAVE_AUDIO="0", ZOE_MULTICA="false", ZOE_HARNESS="1", ZOE_HARNESS_INSTANCE="1", ZOE_PI_HOST="invalid.",
        JETSON_AGENT_MODE="true", MALLOC_ARENA_MAX="2",
        # no egress: every outbound HTTP(S) client that honours the proxy variables is sent to the black hole; loopback stays
        # direct (the brain, the router, the scratch Flue and Postgres). A model the instance would DOWNLOAD (the STT model, for
        # one: MOONSHINE_VOICE_CACHE is empty, so STT stays cold and costs no RAM) fails instead.
        HTTP_PROXY=BLACK_HOLE, HTTPS_PROXY=BLACK_HOLE, http_proxy=BLACK_HOLE, https_proxy=BLACK_HOLE,
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
        MOONSHINE_VOICE_CACHE=str(root / "no-stt-models"),
        LD_LIBRARY_PATH=os.environ.get("LD_LIBRARY_PATH") or "/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu",
    )
    for name in HOUSE_URLS:
        env[name] = BLACK_HOLE
    for name in SELF_URLS:
        env[name] = self_url
    return env


def flue_env(flue_live_env: dict, *, root: Path, port: int, data_url: str, token: str, brain_token: str) -> dict:
    """The scratch Flue sidecar environment: its live flags, a scratch session DB, and its tool calls aimed at the SCRATCH zoe-data."""
    env = dict(PATH=str(Path.home() / ".nvm" / "current" / "bin") + ":/usr/local/bin:/usr/bin:/bin", LANG="C.UTF-8",
               TZ=os.environ.get("TZ") or "Australia/Perth", HOME=str(root / "home"))
    env.update((k, v) for k, v in flue_live_env.items()
               if k.startswith("ZOE_") and not SECRET_RE.search(k) and not LOCATION_RE.search(k) and "://" not in v)
    if flue_live_env.get("ZOE_BRAIN_BASE_URL"):
        env["ZOE_BRAIN_BASE_URL"] = flue_live_env["ZOE_BRAIN_BASE_URL"]       # the llama-server: shared compute
    env.update(PORT=str(port), ZOE_DATA_URL=data_url, ZOE_INTERNAL_TOKEN=token, ZOE_BRAIN_TOKEN=brain_token,
               ZOE_BRAIN_DB=str(root / "flue" / "zoe-brain.db"))
    return env


# -- side effects -----------------------------------------------------------------

def _run(cmd: list, *, timeout: float = 120.0, env: Optional[dict] = None, cwd: Optional[Path] = None, check: bool = True):
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if check and res.returncode != 0:
        raise Refused(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed (rc {res.returncode}): {_redact(res.stderr)[-400:]}")
    return res


def _redact(text: str) -> str:
    return re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", r"\1***@", text or "")


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def rss_mb(pid: Optional[int]) -> Optional[float]:
    """Resident memory of a process AND its children (uvicorn has none; node may), MB."""
    if not pid:
        return None
    total = 0
    pids = [int(pid)]
    try:
        kids = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        pids += [int(k) for k in kids]
    except OSError:
        pass
    for p in pids:
        try:
            for line in Path(f"/proc/{p}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            continue
    return round(total / 1024.0, 1) if total else None


def _start_time(pid: int) -> Optional[str]:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def live_image() -> str:
    """The image id of the live database container: the scratch one runs the SAME image (same digest)."""
    res = _run(["docker", "inspect", "-f", "{{.Image}}", LIVE_CONTAINER], timeout=20)
    image = res.stdout.strip()
    if not image.startswith("sha256:"):
        raise Refused(f"cannot read the image of {LIVE_CONTAINER}")
    return image


def pg_mem_mb(container: str) -> Optional[float]:
    res = _run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container], timeout=30, check=False)
    m = re.match(r"\s*([\d.]+)\s*([KMG]i?B)", res.stdout or "")
    if not m:
        return None
    scale = {"KiB": 1 / 1024, "KB": 1 / 1000, "MiB": 1.0, "MB": 1.0, "GiB": 1024.0, "GB": 1000.0}
    return round(float(m.group(1)) * scale.get(m.group(2), 1.0), 1)


def state_path(tag: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", tag or ""):
        raise Refused("a tag is 1-31 lower-case letters, digits and dashes")
    return STATE_DIR / f"{tag}.json"


def _write_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    path = state_path(state["tag"])
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def load_state(tag: str) -> Optional[dict]:
    try:
        return json.loads(state_path(tag).read_text())
    except (OSError, ValueError):
        return None


def _wait(pred, timeout_s: float, what: str) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            if pred():
                return
        except Exception:  # noqa: BLE001 - not ready yet
            pass
        time.sleep(1.0)
    raise Refused(f"{what} not ready after {int(timeout_s)} s")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        return False


def _service_python(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    try:
        from service_python import resolve_service_python  # type: ignore[import]

        return resolve_service_python(None)[0] or sys.executable
    except Exception:  # noqa: BLE001
        return sys.executable


def _spawn(cmd: list, *, env: dict, cwd: Path, log: Path) -> dict:
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    time.sleep(0.2)
    return {"pid": proc.pid, "start": _start_time(proc.pid), "cmd": cmd[:3]}


def sweep_stale(log=print) -> list:
    """Stacks whose harness process is gone (a SIGKILLed run leaves its container, network and the zoe-data + Flue pair behind):
    tear them down. A harness tag ends with its owner pid (``scratch_stack_for``); a tag without one (``manual``) is left alone."""
    gone = []
    for path in (sorted(STATE_DIR.glob("*.json")) if STATE_DIR.is_dir() else []):
        m = re.search(r"-(\d+)$", path.stem)
        if not m or Path(f"/proc/{m.group(1)}").exists():
            continue
        down(path.stem, log=log)
        gone.append(path.stem)
    return gone


def up(tag: str, *, service_dir: Path, serve: bool = False, python: Optional[str] = None, min_mem_mb: Optional[int] = None,
       log=print) -> dict:
    """Start the scratch stack and write its state. Anything half-built is torn down on failure."""
    if not shutil.which("docker"):
        raise Refused("docker is not available")
    sweep_stale(log)
    floor = min_mem_mb if min_mem_mb is not None else (MIN_MEM_SERVE_MB if serve else MIN_MEM_PG_MB)
    avail = mem_available_mb()
    if avail < floor:
        raise Refused(f"MemAvailable {avail} MB is below the {floor} MB floor for this stack")
    if load_state(tag):
        raise Refused(f"scratch stack {tag} is already up (run down first)")
    service_dir = Path(service_dir).resolve()
    live_env = service_env(service_dir)
    image = live_image()
    name = f"{PREFIX}-{tag}"
    root = STATE_DIR / tag
    for sub in ("home/.cache", "palace", "logs", "flue", "router_retrain", "voice-samples", "no-stt-models"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for cache in SHARED_MODEL_CACHES:          # the memory embedder loads from the real model cache (read), never downloads
        real, link = Path.home() / ".cache" / cache, root / "home" / ".cache" / cache
        if real.is_dir() and not link.exists():
            link.symlink_to(real, target_is_directory=True)
    port = free_port()
    password = secrets.token_hex(16)
    dsn = f"postgresql://{PG_USER}:{password}@127.0.0.1:{port}/{PG_DB}"
    assert_not_live(dsn, live_env.get("POSTGRES_URL"))
    state = {"tag": tag, "root": str(root), "container": name, "network": name, "image": image, "pg_port": port, "dsn": dsn,
             "created_at": time.time(), "procs": {}, "serve": serve, "mem_available_before_mb": avail}
    _write_state(state)
    try:
        _run(["docker", "network", "create", "--driver", "bridge", "--label", f"zoe.harness.scratch={tag}", name], timeout=30)
        env_file = root / "pg.env"
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"POSTGRES_USER={PG_USER}\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB={PG_DB}\n")
        _run(["docker", "run", "-d", "--rm", "--name", name, "--network", name, "--label", f"zoe.harness.scratch={tag}",
              "--memory", PG_MEMORY, "--memory-swap", PG_MEMORY, "--cpus", PG_CPUS, "--pids-limit", "256", "--shm-size", "64m",
              "--env-file", str(env_file), "-p", f"127.0.0.1:{port}:5432", image, "postgres", *PG_ARGS], timeout=60)
        env_file.unlink(missing_ok=True)
        _wait(lambda: _run(["docker", "exec", name, "pg_isready", "-U", PG_USER, "-d", PG_DB, "-h", "127.0.0.1"],
                           timeout=10, check=False).returncode == 0, 90, "scratch postgres")
        py = _service_python(python)
        t0 = time.monotonic()
        _run([py, "-m", "alembic", "upgrade", "head"], timeout=900, cwd=service_dir,
             env=dict(PATH="/usr/local/bin:/usr/bin:/bin", HOME=str(Path.home()), LANG="C.UTF-8", POSTGRES_URL=dsn))
        state["migrate_s"] = round(time.monotonic() - t0, 1)
        log(f"scratch postgres {name} on 127.0.0.1:{port} migrated to head in {state['migrate_s']} s")
        if serve:
            _serve(state, service_dir=service_dir, live_env=live_env, python=py, log=log)
        state["ram"] = measure(state)
        _write_state(state)
        return state
    except BaseException:
        down(tag, log=log)
        raise


def _serve(state: dict, *, service_dir: Path, live_env: dict, python: str, log=print) -> None:
    root = Path(state["root"])
    token, brain_token = secrets.token_hex(24), secrets.token_hex(24)
    zport, fport = free_port(), free_port()
    self_url, flue_url = f"http://127.0.0.1:{zport}", f"http://127.0.0.1:{fport}"
    flue_dir = Path(service_dir).resolve().parents[1] / "labs" / "flue-zoe-brain-2x"
    node = shutil.which("node", path=str(Path.home() / ".nvm" / "current" / "bin")) or shutil.which("node")
    if not node or not (flue_dir / "dist" / "server.mjs").is_file():
        raise Refused("the Flue sidecar build (labs/flue-zoe-brain-2x/dist) or node is missing")
    fenv = flue_env(parse_env_file(flue_dir / ".env"), root=root, port=fport, data_url=self_url, token=token, brain_token=brain_token)
    state["procs"]["flue"] = _spawn([node, "dist/server.mjs"], env=fenv, cwd=flue_dir, log=root / "logs" / "flue.log")
    zenv = harness_env(live_env, root=root, dsn=state["dsn"], token=token, brain_token=brain_token, flue_url=flue_url,
                       self_url=self_url)
    assert_not_live(zenv["POSTGRES_URL"], live_env.get("POSTGRES_URL"))
    state["procs"]["zoe_data"] = _spawn([python, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(zport)],
                                        env=zenv, cwd=Path(service_dir), log=root / "logs" / "zoe-data.log")
    state.update(base_url=self_url, flue_url=flue_url, token=token, brain_token=brain_token, zoe_data_port=zport, flue_port=fport)
    _write_state(state)
    _wait(lambda: _http_ok(self_url + "/health"), 240, "scratch zoe-data")
    log(f"scratch zoe-data on {self_url}, scratch Flue on {flue_url}")


def measure(state: dict) -> dict:
    procs = state.get("procs") or {}
    out = {"pg_mb": pg_mem_mb(state["container"]), "mem_available_mb": mem_available_mb()}
    for name, p in procs.items():
        out[name + "_mb"] = rss_mb(p.get("pid")) if _start_time(p.get("pid") or 0) == p.get("start") else None
    out["total_mb"] = round(sum(v for k, v in out.items() if k.endswith("_mb") and k != "mem_available_mb" and v), 1)
    return out


def _stop(p: dict) -> None:
    pid = int(p.get("pid") or 0)
    if not pid or _start_time(pid) != p.get("start"):          # gone, or the pid now belongs to someone else: never signal it
        return
    for sig, wait_s in ((signal.SIGTERM, 20.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:
            if _start_time(pid) != p.get("start"):
                return
            time.sleep(0.25)


def down(tag: str, *, log=print, keep_root: bool = False) -> bool:
    """Tear the stack down: processes (only the ones this stack started), the container, the network, the scratch files."""
    state = load_state(tag) or {"tag": tag, "container": f"{PREFIX}-{tag}", "network": f"{PREFIX}-{tag}", "root": str(STATE_DIR / tag)}
    for p in (state.get("procs") or {}).values():
        _stop(p)
    _run(["docker", "rm", "-f", state["container"]], timeout=60, check=False)
    _run(["docker", "network", "rm", state["network"]], timeout=30, check=False)
    if not keep_root:
        root = Path(state.get("root") or "")
        if root.is_dir() and STATE_DIR in root.parents:
            shutil.rmtree(root, ignore_errors=True)
    state_path(tag).unlink(missing_ok=True)
    left = _run(["docker", "ps", "-aq", "--filter", f"label=zoe.harness.scratch={tag}"], timeout=20, check=False).stdout.strip()
    log(f"scratch stack {tag} down" + (" (a container is still listed)" if left else ""))
    return not left


class ScratchStack:
    """``with ScratchStack(service_dir, serve=True) as st:`` - the harness entry point. ``st.dsn`` / ``st.base_url`` / ``st.token``
    are the scratch ones; the stack is torn down on exit, whatever happened inside."""

    def __init__(self, service_dir: Path, *, serve: bool = True, tag: Optional[str] = None, log=print):
        self.service_dir, self.serve, self.log = Path(service_dir), serve, log
        self.tag = tag or f"h{os.getpid()}"
        self.state: dict = {}

    def __enter__(self) -> "ScratchStack":
        self.state = up(self.tag, service_dir=self.service_dir, serve=self.serve, log=self.log)
        return self

    def __exit__(self, *exc) -> None:
        self.state["ram_at_end"] = measure(self.state) if self.state else None
        down(self.tag, log=self.log)

    @property
    def dsn(self) -> str:
        return self.state["dsn"]

    @property
    def base_url(self) -> str:
        return self.state.get("base_url") or ""

    @property
    def token(self) -> str:
        return self.state.get("token") or ""

    def public(self) -> dict:
        """What a harness may write into its results: no DSN, no token."""
        keep = ("tag", "image", "pg_port", "zoe_data_port", "flue_port", "migrate_s", "ram", "ram_at_end", "mem_available_before_mb")
        return dict((k, self.state.get(k)) for k in keep if k in self.state)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("up", "status", "down"):
        sp = sub.add_parser(name)
        sp.add_argument("--tag", default="manual")
        if name == "up":
            sp.add_argument("--serve", action="store_true", help="also start a scratch zoe-data + Flue pair")
            sp.add_argument("--service-dir", default=str(REPO / "services" / "zoe-data"))
            sp.add_argument("--python", default=None)
            sp.add_argument("--min-mem-mb", type=int, default=None)
    cd = sub.add_parser("check-dsn")
    cd.add_argument("--dsn-env", required=True, help="the NAME of an env var holding the DSN (the value is never printed)")
    cd.add_argument("--service-dir", default=str(REPO / "services" / "zoe-data"))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "up":
            st = up(args.tag, service_dir=Path(args.service_dir), serve=args.serve, python=args.python, min_mem_mb=args.min_mem_mb)
            print(json.dumps({"state_file": str(state_path(args.tag)), "ram": st.get("ram"), "migrate_s": st.get("migrate_s"),
                              "base_url": st.get("base_url")}, indent=1))
        elif args.cmd == "status":
            st = load_state(args.tag)
            if not st:
                print(f"scratch stack {args.tag} is not up")
                return 1
            print(json.dumps({"ram": measure(st), "pg_port": st.get("pg_port"), "base_url": st.get("base_url")}, indent=1))
        elif args.cmd == "down":
            return 0 if down(args.tag) else 1
        elif args.cmd == "check-dsn":
            live = service_env(Path(args.service_dir)).get("POSTGRES_URL")
            if is_live_dsn(os.environ.get(args.dsn_env, ""), live):
                print(f"{args.dsn_env}: LIVE (refused)")
                return 2
            print(f"{args.dsn_env}: scratch")
    except Refused as exc:
        print(f"harness_scratch: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
