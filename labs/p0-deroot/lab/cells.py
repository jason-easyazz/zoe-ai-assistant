#!/usr/bin/env python3
"""P0 de-root lab cells (root, inside the lab container, after every unit is up). Prints `CELL <id> PASS|FAIL ...`
lines and one `P0LAB-RESULT {json}` line on the console; run.sh reads them from `docker logs`. Lab-only."""
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/p0/lab")
from standin import unix_http  # noqa: E402

PROXY, RAW = "/run/zoe-docker-proxy/docker.sock", "/var/run/docker.sock"
HELPER = "/usr/local/libexec/zoe-priv/"
EXPECT = {"zoe-data": (8000, "zoe-svc"), "flue-zoe-brain-2x": (3579, "zoe-flue"), "llama-server": (11434, "zoe-model")}
results = []
CONSOLE = open("/proc/1/fd/1", "w")  # PID 1's stdout IS `docker logs`; the journal is not reachable from the host


def say(line):
    print(line, flush=True)
    CONSOLE.write(line + "\n")
    CONSOLE.flush()


def cell(cid, ok, detail=""):
    results.append({"cell": cid, "pass": bool(ok), "detail": str(detail)[:300]})
    say(f"CELL {cid} {'PASS' if ok else 'FAIL'} {detail}")


def run(*argv):
    return subprocess.run(list(argv), capture_output=True, text=True)


def as_user(user, method, url, sock=PROXY):
    code = f"import sys; sys.path.insert(0,'/opt/p0/lab'); from standin import unix_http; " \
           f"print(unix_http({sock!r}, {method!r}, {url!r}))"
    return run("runuser", "-u", user, "--", "python3", "-c", code).stdout.strip()


def gid(name):
    return int(run("getent", "group", name).stdout.split(":")[2])


def denied(v):
    return v is not True


# --- S: each service, probed from INSIDE its own sandbox -------------------------------------------------------
probes = {}
for unit, (port, user) in EXPECT.items():
    for _ in range(60):
        try:
            probes[unit] = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/probe", timeout=5))
            break
        except OSError:
            time.sleep(1)
    p = probes.get(unit)
    if p is None:
        cell(f"S0-{unit}", False, "service never answered /probe")
        continue
    uid = int(run("id", "-u", user).stdout)
    cell(f"S1-{unit}-identity", p["uid"] == uid and gid("sudo") not in p["groups"] and gid("docker") not in p["groups"],
         f"uid={p['uid']} groups={p['groups']}")
    cell(f"S2-{unit}-no-sudo", p["sudo_rc"] != 0, f"sudo -n true rc={p['sudo_rc']}")
    cell(f"S3-{unit}-no-raw-docker", p["docker_sock"] in ("PermissionError", "FileNotFoundError"), p["docker_sock"])
    if unit == "zoe-data":
        cell("S4-zoe-data-proxy-allowlist", (p["proxy_ma_start"], p["proxy_create"], p["proxy_ha_exec"]) == (200, 403, 403),
             f"ma_start={p['proxy_ma_start']} create={p['proxy_create']} ha_exec={p['proxy_ha_exec']}")
    else:
        cell(f"S4-{unit}-no-proxy", p["proxy_ma_start"] == "PermissionError", p["proxy_ma_start"])
    cell(f"S5-{unit}-no-other-secrets", all(denied(v) for v in p["other_secrets"].values()), p["other_secrets"])
    cell(f"S6-{unit}-real-home-hidden", denied(p["real_home_ssh"]), p["real_home_ssh"])
    cell(f"S7-{unit}-checkout-ro", p["checkout_read"] is True and denied(p["checkout_write"]),
         f"read={p['checkout_read']} write={p['checkout_write']}")
    cell(f"S8-{unit}-own-secret", p["own_secret"] is (unit != "llama-server"), f"own_secret={p['own_secret']}")
    cell(f"S9-{unit}-does-its-job", p["job_input"] is True and p["state_write"] in (True, None),
         f"input={p['job_input']} state_write={p['state_write']}")
    cell(f"S10-{unit}-etc-ro", denied(p["etc_write"]), p["etc_write"])
    show = run("systemctl", "show", unit, "-p", "User,NoNewPrivileges,MemorySwapMax,MemoryLow,LimitMEMLOCK").stdout
    cell(f"S11-{unit}-guards-kept", "MemorySwapMax=0" in show and "NoNewPrivileges=yes" in show, show.replace("\n", " "))
cell("S12-llama-gpu-group", gid("video") in probes.get("llama-server", {}).get("groups", []), "video group for /dev/nv*")
log = run("cat", "/var/log/zoe-data/zoe-data.stdout.log").stdout
cell("S13-zoe-data-stdout-capture", "up on" in log, log.strip() or "missing")

# --- Z: the `zoe` login uid after P0-3 -------------------------------------------------------------------------
cell("Z1-zoe-no-sudo", run("runuser", "-u", "zoe", "--", "sudo", "-n", "true").returncode != 0)
allowed = [[HELPER + "zoe-mem-compact", "3"], [HELPER + "zoe-nvmap-read", "free"],
           ["/usr/bin/systemctl", "restart", "zoe-data.service"], ["/usr/bin/systemctl", "stop", "llama-server.service"]]
refused = [["/bin/sh"], ["/usr/bin/bash"], [HELPER + "zoe-mem-compact", "2"], [HELPER + "zoe-nvmap-read", "/etc/shadow"],
           ["/usr/bin/systemctl", "edit", "zoe-data.service"], ["/usr/bin/systemctl", "start", "ssh.service"],
           ["/usr/bin/systemctl", "restart", "zoe-data.service", "--now"], ["/usr/sbin/visudo"], ["/usr/bin/docker"]]
ok = [c for c in allowed if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
bad = [c for c in refused if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
cell("Z2-zoe-sudo-allowlist", len(ok) == len(allowed) and not bad, f"allowed {len(ok)}/{len(allowed)}, leaked={bad}")
cell("Z3-zoe-no-service-secrets", all("Permission denied" in run("runuser", "-u", "zoe", "--", "cat", f).stderr
                                      for f in ("/etc/zoe/env/zoe-data.env", "/etc/zoe/env/flue-zoe-brain-2x.env")))
cell("Z4-zoe-no-raw-docker", as_user("zoe", "GET", "/v1.47/containers/json", RAW) == "PermissionError")
cases = [("POST", "/v1.47/containers/zoe-music-assistant/start", 200), ("POST", "/containers/livekit/stop", 200),
         ("GET", "/v1.47/containers/zoe-database/json", 200), ("POST", "/v1.47/containers/zoe-database/exec", 200),
         ("POST", "/v1.47/exec/" + "a" * 64 + "/start", 200), ("HEAD", "/_ping", 200),
         ("GET", "/v1.47/containers/json?all=1&filters=%7B%22name%22%3A%5B%22zoe%22%5D%7D", 200),
         ("POST", "/v1.47/containers/create?name=zoe-music-assistant", 403),
         ("POST", "/v1.47/containers/create", 403), ("POST", "/v1.47/build", 403), ("POST", "/v1.47/images/create", 403),
         ("POST", "/v1.47/containers/homeassistant/exec", 403), ("POST", "/v1.47/containers/zoe-omnigent/exec", 403),
         ("POST", "/v1.47/containers/zoe-ui/restart", 403), ("GET", "/v1.47/containers/zoe-auth/json", 403),
         ("POST", "/v1.47/containers/zoe-music-assistant/../homeassistant/exec", 403),
         ("POST", "/v1.47/containers/zoe-music-assistant%2F..%2Fhomeassistant/start", 403),
         ("POST", "/v1.47/exec/abc/start", 403), ("DELETE", "/v1.47/containers/zoe-database", 405),
         ("PUT", "/v1.47/containers/zoe-database/archive", 405)]
got = [(m, u, as_user("zoe", m, u)) for m, u, _ in cases]
wrong = [(m, u, g, w) for (m, u, g), (_, _, w) in zip(got, cases) if str(g) != str(w)]
cell("Z5-proxy-allowlist", not wrong, f"{len(cases) - len(wrong)}/{len(cases)} as expected; wrong={wrong}")
helper_bad = [run(HELPER + "zoe-mem-compact", "2").returncode, run(HELPER + "zoe-nvmap-read", "/etc/shadow").returncode,
              run(HELPER + "zoe-mem-compact").returncode, run(HELPER + "zoe-mem-compact", "3", "x").returncode]
cell("Z6-helpers-validate-args", helper_bad == [2, 2, 2, 2], helper_bad)
admin = run("runuser", "-u", "zoe-admin", "--", "sudo", "-n", "true")
cell("Z7-owner-admin-path-needs-password", admin.returncode != 0 and "password" in admin.stderr
     and "(ALL : ALL) ALL" in run("sudo", "-l", "-U", "zoe-admin").stdout, admin.stderr.strip())

# --- C: the instruments can see power when it exists (negative controls) ---------------------------------------
cell("C1-control-zoe-pre-can-sudo", run("runuser", "-u", "zoe-pre", "--", "sudo", "-n", "true").returncode == 0)
cell("C2-control-zoe-pre-reaches-docker", as_user("zoe-pre", "GET", "/v1.47/containers/json", RAW) == "200")
seen = set(run("cat", "/run/p0lab-dockerd.log").stdout.splitlines())
leak = [f"{m} {u}" for m, u, w in cases if w != 200 and f"{m} {u}" in seen]
cell("C3-daemon-saw-only-allowed", "POST /v1.47/containers/zoe-music-assistant/start" in seen and not leak,
     f"daemon saw {len(seen)} distinct requests; leaked={leak}")
vis = run("visudo", "-c")
open("/tmp/broken-sudoers", "w").write("zoe ALL=(root) NOPASSWD /bin/true\n")
cell("C4-visudo-checks-and-catches", vis.returncode == 0 and run("visudo", "-c", "-f", "/tmp/broken-sudoers").returncode != 0,
     vis.stdout.strip().replace("\n", "; "))
ver = run("systemd-analyze", "verify", *(f"/etc/systemd/system/{u}.service" for u in ("zoe-docker-proxy", *EXPECT)))
cell("C5-systemd-analyze-verify", ver.returncode == 0, (ver.stderr or "clean").strip().replace("\n", "; "))

say("P0LAB-RESULT " + json.dumps({"pass": sum(r["pass"] for r in results), "total": len(results),
                                  "cells": results, "probes": probes}))
