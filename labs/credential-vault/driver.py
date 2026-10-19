#!/usr/bin/env python3
"""Host side of the credential-vault lab (V0 + V1). Hand-run only, through run.sh (which holds the gates).

Brings up ONE internal Docker network (no route out) with three memory-capped containers:
  vault   vaultwarden/server 1.37.4, pinned by digest, non-root, TLS from the lab CA     (256 MB, no swap)
  sites   the test sites + witness (lab/sites.py), unprivileged                          (96 MB)
  client  the lab: the members' and Zoe's bw clients, the broker, its browser, its phone (768 MB, no swap)
runs the phases, measures, and ALWAYS tears everything down (containers, network, volumes; the image unless
--keep-image), then asserts nothing is left. A watchdog aborts the run if MemAvailable drops below 700 MB or the
night window starts. Synthetic credentials only; nothing touches a live service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
BIN = LAB / ".bin"
BW_VERSION = "2026.9.1"
BW_ZIP = f"bw-oss-linux-arm64-{BW_VERSION}.zip"
BW_SHA256 = "6c2765f91d5bbba9864195c9a304a683bcc0bfa39cfd85ce9423142631e111a6"
BW_URL = f"https://github.com/bitwarden/clients/releases/download/cli-v{BW_VERSION}/{BW_ZIP}"
VW_IMAGE = "vaultwarden/server:1.37.4@sha256:efb3cde962015fcc036b2ea625242248611943b27212ebf4392841fb30fad055"
IMAGE = "cvlab-client:local"
TAG = "cvlab-" + secrets.token_hex(3)
NET, VW, SITES, CLIENT = f"{TAG}-net", f"{TAG}-vault", f"{TAG}-sites", f"{TAG}-client"
VOL_PKI, VOL_VW = f"{TAG}-pki", f"{TAG}-vwdata"
MEM_ABORT_MB = 700

RESULT: dict = {"tag": TAG, "phases": {}, "cells": [], "measure": {}}
ABORT = threading.Event()


def sh(*args, check=True, timeout=600, input=None) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout, input=input)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args[:4]))} rc={r.returncode}: {r.stderr[-800:]}")
    return r


def mem_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def night_window_active() -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", "zoe-night-window.service"], capture_output=True, text=True)
    return r.stdout.strip() in ("active", "activating")


PIECE_MAX_S = 900   # a piece holds the shared test-lane lock; it never holds it longer than this


_CIDS: dict = {}


def _cgroup_current(name: str) -> int | None:
    try:
        if name not in _CIDS:
            r = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", name], capture_output=True, text=True)
            if r.returncode != 0:
                return None
            _CIDS[name] = r.stdout.strip()
        return int(Path(f"/sys/fs/cgroup/system.slice/docker-{_CIDS[name]}.scope/memory.current").read_text()) >> 20
    except (OSError, ValueError):
        return None


def watchdog() -> None:
    low = 0
    started = time.monotonic()
    while not ABORT.is_set():
        m = mem_mb()
        low = low + 1 if m < MEM_ABORT_MB else 0
        overtime = time.monotonic() - started > PIECE_MAX_S
        if low >= 2 or night_window_active() or overtime:
            print(f"WATCHDOG: aborting (MemAvailable {m} MB, night window {night_window_active()}, "
                  f"over {PIECE_MAX_S} s {overtime})", flush=True)
            ABORT.set()
            for c in (CLIENT, SITES, VW):
                subprocess.run(["docker", "kill", c], capture_output=True)
            return
        # this kernel (5.15) has no cgroup memory.peak, so sample memory.current every 2 s and keep the maximum
        for name in (CLIENT, VW):
            cur = _cgroup_current(name)
            if cur is not None:
                key = f"sampled_peak_mb_{name.rsplit('-', 1)[1]}"
                RESULT["measure"][key] = max(RESULT["measure"].get(key, 0), cur)
        time.sleep(2)


# -- setup ---------------------------------------------------------------------------------------------------------
def ensure_bw() -> Path:
    BIN.mkdir(exist_ok=True)
    out = BIN / "bw"
    stamp = BIN / f"bw.{BW_VERSION}.ok"
    if out.exists() and stamp.exists():
        return out
    z = BIN / BW_ZIP
    if not z.exists():
        with urllib.request.urlopen(BW_URL, timeout=120) as r, open(z, "wb") as fh:
            shutil.copyfileobj(r, fh)
    digest = hashlib.sha256(z.read_bytes()).hexdigest()
    if digest != BW_SHA256:
        z.unlink()
        raise SystemExit(f"bw zip sha256 mismatch: {digest}")
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        if names != ["bw"]:
            raise SystemExit(f"unexpected bw zip contents: {names}")
        out.write_bytes(zf.read("bw"))
    out.chmod(0o755)
    stamp.write_text(digest + "\n")
    return out


def build() -> None:
    ctx = Path(tempfile.mkdtemp(prefix="cvlab-ctx-"))
    try:
        shutil.copy(LAB / "Dockerfile", ctx / "Dockerfile")
        shutil.copytree(LAB / "lab", ctx / "lab", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "services" / "zoe-vaultd" / "zoe_vaultd", ctx / "zoe_vaultd",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ensure_bw(), ctx / "bw")
        sh("nice", "-n", "10", "docker", "build", "-q", "-t", IMAGE, str(ctx), timeout=3600)
    finally:
        shutil.rmtree(ctx, ignore_errors=True)


def vw_env(**extra) -> list[str]:
    env = {
        "ROCKET_PORT": "8443", "ROCKET_TLS": '{certs="/pki/vw/server.crt",key="/pki/vw/server.key"}',
        "DOMAIN": "https://vault.lab.test:8443", "SIGNUPS_ALLOWED": "false", "INVITATIONS_ALLOWED": "true",
        "PUSH_ENABLED": "false", "DISABLE_ICON_DOWNLOAD": "true", "ICON_SERVICE": "internal", "LOG_LEVEL": "warn",
        "SENDS_ALLOWED": "false", "ORG_EVENTS_ENABLED": "false",
    }
    env.update(extra)
    out = []
    for k, v in env.items():
        if v is not None:
            out += ["-e", f"{k}={v}"]
    return out


def start_vw(**extra) -> None:
    subprocess.run(["docker", "rm", "-f", VW], capture_output=True)
    sh("docker", "run", "-d", "--name", VW, "--network", NET, "--network-alias", "vault.lab.test",
       "-u", "1000:1000", "--memory", "256m", "--memory-swap", "256m", "--read-only", "--tmpfs", "/tmp:size=16m",
       # nocopy: otherwise Docker populates the empty volume from the image's /data and resets its owner to root
       "-v", f"{VOL_VW}:/data:nocopy", "-v", f"{VOL_PKI}:/pki:ro", *vw_env(**extra), VW_IMAGE)


def container_mem(name: str) -> dict:
    """RSS of the container's main process (host /proc) and the cgroup's current and peak memory."""
    out = {}
    try:
        pid = sh("docker", "inspect", "-f", "{{.State.Pid}}", name).stdout.strip()
        cid = sh("docker", "inspect", "-f", "{{.Id}}", name).stdout.strip()
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                out[line.split(":")[0]] = int(line.split()[1]) // 1024
        for cg in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope",):
            for f in ("memory.current", "memory.peak"):
                p = Path(cg, f)
                if p.exists():
                    out[f] = int(p.read_text().strip()) // (1024 * 1024)
    except (OSError, RuntimeError, ValueError):
        pass
    return out


def dexec(*args, timeout=3600) -> str:
    """docker exec into the client; stream CELL lines; keep the CVLAB-RESULT line."""
    proc = subprocess.Popen(["docker", "exec", CLIENT, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    lines = []
    deadline = time.monotonic() + timeout
    for line in proc.stdout:
        lines.append(line)
        if line.startswith(("CELL ", "seeded", "TRASH", "IDS", "BACKDATED")):
            print(line.rstrip(), flush=True)
        if time.monotonic() > deadline or ABORT.is_set():
            proc.kill()
            break
    proc.wait()
    text = "".join(lines)
    for line in lines:
        if line.startswith("CVLAB-RESULT "):
            res = json.loads(line[len("CVLAB-RESULT "):])
            RESULT["phases"][res["phase"]] = {"pass": res["pass"], "total": res["total"]}
            RESULT["cells"] += [dict(c, phase=res["phase"]) for c in res["cells"]]
            RESULT["measure"].update(res["measure"])
    with open(LAB / "last-console.log", "a") as fh:
        fh.write(f"===== {' '.join(args[1:])} rc={proc.returncode}\n" + text[-200000:])
    if proc.returncode != 0:
        tail = "".join(lines[-6:]).strip()
        raise RuntimeError(f"phase {' '.join(args[1:])} exited rc={proc.returncode}: {tail[-500:]}")
    return text


def host_cell(name: str, ok: bool, detail: str, nc: str | None = None) -> None:
    RESULT["cells"].append({"cell": name, "pass": bool(ok), "detail": detail, "nc": nc, "phase": "host"})
    print(f"CELL {'PASS' if ok else 'FAIL'} {name} :: {detail}" + (f" || NC: {nc}" if nc else ""), flush=True)


# -- read-only host probes -------------------------------------------------------------------------------------------
def ftpm_probe() -> dict:
    """Read-only: what TPM the box exposes and whether this (non-root) uid could even open it. Never opens it."""
    out = {"devices": {}, "tpm2_tools": shutil.which("tpm2_unseal") is not None, "uid_groups": os.getgroups()}
    for dev in ("/dev/tpm0", "/dev/tpmrm0"):
        try:
            st = os.stat(dev)
            out["devices"][dev] = {"mode": oct(st.st_mode & 0o777), "uid": st.st_uid, "gid": st.st_gid,
                                   "readable_by_me": os.access(dev, os.R_OK | os.W_OK)}
        except OSError as e:
            out["devices"][dev] = type(e).__name__
    for f in ("tpm_version_major", "device/description", "device/driver"):
        p = Path("/sys/class/tpm/tpm0", f)
        try:
            out[f] = os.path.basename(os.readlink(p)) if p.is_symlink() else p.read_text().strip()
        except OSError:
            pass
    try:
        out["l4t_release"] = Path("/etc/nv_tegra_release").read_text().splitlines()[0][:120]
    except OSError:
        pass
    return out


def hermetic_check() -> None:
    probe = ("import socket,json\nout={}\nfor h,p in (('1.1.1.1',443),('8.8.8.8',53),('140.82.112.3',443)):\n"
             "  s=socket.socket(); s.settimeout(3)\n  try: s.connect((h,p)); out[h]='CONNECTED'\n"
             "  except OSError as e: out[h]=type(e).__name__\n  finally: s.close()\n"
             "try: out['dns:example.com']=socket.gethostbyname('example.com')\n"
             "except OSError as e: out['dns:example.com']=type(e).__name__\nprint(json.dumps(out))")
    res = {}
    for c in (CLIENT, SITES):
        r = sh("docker", "exec", c, "python3", "-c", probe, check=False)
        res[c.rsplit("-", 1)[1]] = json.loads(r.stdout.strip() or "{}")
    connected = [k for v in res.values() for k, x in v.items() if x == "CONNECTED"]
    resolved = [v.get("dns:example.com") for v in res.values() if re.match(r"^\d+\.", str(v.get("dns:example.com")))]
    RESULT["measure"]["hermetic"] = res
    host_cell("H1 hermetic network", not connected,
              f"from the client and the sites: no TCP connection leaves the internal network {res}"
              + (f"; NOTE example.com resolved ({resolved}) but nothing connects" if resolved else ""))


# -- the run -------------------------------------------------------------------------------------------------------
PIECES = ("v0", "A", "B", "C", "D", "E", "F")   # each piece: bring up, seed, its cells, tear down (one lane-lock hold, <= ~10 min)


def piece_arg() -> str:
    args = sys.argv[1:]
    piece = args[args.index("--piece") + 1] if "--piece" in args else ""
    if piece not in PIECES:
        raise SystemExit(f"--piece must be one of {PIECES}")
    return piece


def combine() -> int:
    """Merge the per-piece results into last-result.json (cells keep their piece)."""
    out = {"pieces": {}, "cells": [], "measure": {}}
    for piece in PIECES:
        f = LAB / f"last-result-{piece}.json"
        if not f.exists():
            out["pieces"][piece] = "missing"
            continue
        r = json.loads(f.read_text())
        out["pieces"][piece] = {k: r.get(k) for k in ("started", "duration_s", "pass", "total", "teardown_clean",
                                                      "mem_available_at_start_mb")}
        out["cells"] += [dict(c, piece=piece) for c in r["cells"]]
        out["measure"][piece] = r["measure"]
    passed = sum(1 for c in out["cells"] if c["pass"])
    out["pass"], out["total"] = passed, len(out["cells"])
    (LAB / "last-result.json").write_text(json.dumps(out, indent=1))
    for c in out["cells"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'} [{c['piece']}] {c['cell']}")
    missing = [k for k, v in out["pieces"].items() if v == "missing"]
    print(f"{passed}/{len(out['cells'])} cells pass across pieces" + (f"; MISSING pieces {missing}" if missing else ""))
    return 0 if passed == len(out["cells"]) and not missing else 1


def main() -> int:
    if "--combine" in sys.argv:
        return combine()
    keep_image = "--keep-image" in sys.argv
    piece = piece_arg()
    RESULT["piece"] = piece
    t_start = time.monotonic()
    RESULT["started"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    RESULT["mem_available_at_start_mb"] = mem_mb()
    RESULT["measure"]["ftpm_probe"] = ftpm_probe()
    (LAB / "last-console.log").write_text("")
    build()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        sh("docker", "network", "create", "--internal", NET)
        sh("docker", "volume", "create", VOL_PKI)
        sh("docker", "volume", "create", VOL_VW)
        sh("docker", "run", "--rm", "--network", "none", "-v", f"{VOL_PKI}:/pki", IMAGE,
           "python3", "/opt/cvlab/lab/pki.py", "/pki")
        sh("docker", "run", "--rm", "--network", "none", "-v", f"{VOL_VW}:/data", IMAGE, "chown", "1000:1000", "/data")
        start_vw(SIGNUPS_ALLOWED="true")       # seeding only: the family registers; cells run with signups off
        aliases = []
        sys.path.insert(0, str(LAB / "lab"))
        from hosts import SITE_HOSTS
        for h in SITE_HOSTS:
            aliases += ["--network-alias", h]
        sh("docker", "run", "-d", "--name", SITES, "--network", NET, *aliases, "-u", "2100:2100",
           "--sysctl", "net.ipv4.ip_unprivileged_port_start=0", "--memory", "96m", "--memory-swap", "96m",
           "-v", f"{VOL_PKI}:/pki:ro", IMAGE, "python3", "/opt/cvlab/lab/sites.py")
        sh("docker", "run", "-d", "--name", CLIENT, "--network", NET, "--memory", "768m", "--memory-swap", "768m",
           "--tmpfs", "/run:rw,mode=755,size=192m", "--tmpfs", "/tmp:rw,size=128m", "--shm-size", "64m",
           # --init: an init that REAPS orphans. Under a bare `sleep` as PID 1, every killed browser's renderers
           # stayed as zombies until the 512-pid limit wedged Chromium (run 8: "Page.enable timeout" from then on)
           "-v", f"{VOL_PKI}:/pki:ro", "--pids-limit", "512", "--init", IMAGE, "sleep", "infinity")
        hermetic_check()
        RESULT["measure"]["versions"] = {
            "vaultwarden_image": VW_IMAGE, "bw": BW_VERSION + " (OSS build, sha256 " + BW_SHA256[:16] + "...)",
            "bw_reported": sh("docker", "exec", CLIENT, "bw", "--version", check=False).stdout.strip(),
            "chromium": sh("docker", "exec", CLIENT, "chromium", "--version", check=False).stdout.strip(),
            "psl_sha256": sh("docker", "exec", CLIENT, "sha256sum", "/usr/share/publicsuffix/public_suffix_list.dat",
                             check=False).stdout.split()[0:1],
            "publicsuffix_pkg": sh("docker", "exec", CLIENT, "dpkg-query", "-W", "publicsuffix", check=False
                                   ).stdout.strip()}

        dexec("python3", "/opt/cvlab/lab/v0.py", "seed")
        RESULT["measure"]["vaultwarden_mem_after_seed"] = container_mem(VW)
        if ABORT.is_set():
            raise RuntimeError("aborted by the watchdog")
        start_vw()                              # production-like: signups off from here on
        if piece != "v0":
            dexec("python3", "/opt/cvlab/lab/v1.py", piece, timeout=1500)
            RESULT["measure"]["vaultwarden_mem_final"] = container_mem(VW)
            RESULT["measure"]["client_container_mem"] = container_mem(CLIENT)
            RESULT["measure"]["sites_container_mem"] = container_mem(SITES)
            raise StopIteration
        dexec("python3", "/opt/cvlab/lab/v0.py", "cells")
        RESULT["measure"]["vaultwarden_mem_after_v0"] = container_mem(VW)

        marker = sh("docker", "exec", CLIENT, "python3", "-c",
                    "import json;print(json.load(open('/run/cvlab/state.json'))['marker'])").stdout.strip()
        r = sh("docker", "run", "--rm", "--network", "none", "-v", f"{VOL_VW}:/data:ro", "-e", f"CVLAB_MARKER={marker}",
               IMAGE, "python3", "/opt/cvlab/lab/v0.py", "scan-db", "/data")
        hits = json.loads(r.stdout.split("SCANDB ", 1)[1])["hits"]
        host_cell("V3c-db canary not in Vaultwarden's data", not hits,
                  "the run's canary marker is in no file under Vaultwarden's /data (db.sqlite3, -wal, keys): items "
                  "are client-side encrypted", nc=None if not hits else f"hits: {hits}")

        # -- trash retention: backdate three trashed items, restart with a 5 s purge schedule -------------------
        ids = json.loads(sh("docker", "exec", CLIENT, "python3", "/opt/cvlab/lab/v0.py", "ids").stdout.split("IDS ", 1)[1])
        sh("docker", "stop", VW)
        spec = ",".join(f"{ids[k]}={d}" for k, d in (("t400", 400), ("t31", 31), ("t29", 29)))
        sh("docker", "run", "--rm", "--network", "none", "-u", "1000:1000", "-v", f"{VOL_VW}:/data", IMAGE,
           "python3", "/opt/cvlab/lab/v0.py", "backdate", "/data/db.sqlite3", spec)
        trash = {}
        for label, days in (("unset", None), ("30", "30")):
            start_vw(TRASH_PURGE_SCHEDULE="*/5 * * * * *", JOB_POLL_INTERVAL_MS="1000", TRASH_AUTO_DELETE_DAYS=days)
            time.sleep(20)
            out = sh("docker", "exec", CLIENT, "python3", "/opt/cvlab/lab/v0.py", "trash").stdout
            trash[label] = json.loads(out.split("TRASH ", 1)[1])
        RESULT["measure"]["trash_retention"] = trash
        host_cell("M7 trash retention (TRASH_AUTO_DELETE_DAYS)",
                  trash["unset"] == {"t400": True, "t31": True, "t29": True}
                  and trash["30"] == {"t400": False, "t31": False, "t29": True},
                  f"unset: items trashed 400/31/29 days ago all kept {trash['unset']}; =30: the 400- and 31-day ones "
                  f"purged, the 29-day one kept {trash['30']} (purge job forced to every 5 s for the measurement)")
    except StopIteration:
        pass
    except Exception as exc:
        host_cell("lab completed", False, f"{type(exc).__name__}: {str(exc)[:400]}")
    finally:
        ABORT.set()
        with open(LAB / "last-console.log", "a") as fh:
            for c in (VW, SITES):
                r = subprocess.run(["docker", "logs", "--tail", "60", c], capture_output=True, text=True)
                fh.write(f"===== docker logs {c}\n{r.stdout[-6000:]}{r.stderr[-6000:]}\n")
        for c in (CLIENT, SITES, VW):
            subprocess.run(["docker", "rm", "-f", c], capture_output=True)
        subprocess.run(["docker", "network", "rm", NET], capture_output=True)
        for v in (VOL_PKI, VOL_VW):
            subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)
        if not keep_image:
            subprocess.run(["docker", "rmi", "-f", IMAGE], capture_output=True)
        left = sh("docker", "ps", "-aq", "--filter", f"name={TAG}", check=False).stdout.strip()
        left += sh("docker", "network", "ls", "-q", "--filter", f"name={TAG}", check=False).stdout.strip()
        left += sh("docker", "volume", "ls", "-q", "--filter", f"name={TAG}", check=False).stdout.strip()
        RESULT["teardown_clean"] = not left
        print("teardown: " + ("everything removed" if not left else f"LEFT BEHIND: {left}"), flush=True)
    RESULT["duration_s"] = int(time.monotonic() - t_start)
    passed = sum(1 for c in RESULT["cells"] if c["pass"])
    RESULT["pass"], RESULT["total"] = passed, len(RESULT["cells"])
    out = LAB / f"last-result-{piece}.json"
    out.write_text(json.dumps(RESULT, indent=1))
    print(f"{passed}/{len(RESULT['cells'])} cells pass; result in {out}", flush=True)
    return 0 if passed == len(RESULT["cells"]) and RESULT["teardown_clean"] and RESULT["cells"] else 1


if __name__ == "__main__":
    sys.exit(main())
