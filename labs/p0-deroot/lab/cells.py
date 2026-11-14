#!/usr/bin/env python3
"""P0 de-root lab cells (root, inside the lab container, after every unit is up). Prints `CELL <id> PASS|FAIL ...`
lines and one `P0LAB-RESULT {json}` line to /run/p0lab-cells.txt; run.sh reads it with `docker exec`. Lab-only."""
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
OUT = open("/run/p0lab-cells.txt", "w")  # run.sh reads it with `docker exec`; /run/p0lab-cells.done marks the end


def say(line):
    print(line, flush=True)
    OUT.write(line + "\n")
    OUT.flush()


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
    # zoe-data and Flue read the checkout read-only; the model tier is not given it at all (no bind)
    sees = p["checkout_read"] is True if unit != "llama-server" else p["checkout_read"] == "FileNotFoundError"
    cell(f"S7-{unit}-checkout-ro", sees and denied(p["checkout_write"]),
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
         ("GET", "/v1.47/containers/zoe-database/json", 200), ("POST", "/v1.47/containers/zoe-database/exec", 403),
         ("POST", "/v1.47/exec/" + "a" * 64 + "/start", 403), ("HEAD", "/_ping", 200),
         ("POST", "/v1.23/containers/livekit/start", 403),
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

# --- P: P0-1 rewired callers (helpers installed, grant REMOVED for `zoe`; grant PRESENT for `zoe-pre`) -------------
import os  # noqa: E402
import shutil  # noqa: E402

DEPLOY = HELPER + "zoe-compose-deploy"
DLOG = "/run/p0lab-docker-cli.log"
SHAS = dict(line.split() for line in open("/opt/p0/shas"))
CO = "/opt/p0/checkout"
for f, mode in ((DLOG, 0o666), ("/run/p0lab-scenario", 0o644)):
    open(f, "w").close()
    os.chmod(f, mode)


def scen(name):
    open("/run/p0lab-scenario", "w").write(name)


def dcalls():
    return [json.loads(x)["argv"] for x in open(DLOG).read().splitlines() if x.strip()]


def fresh():
    open(DLOG, "w").close()


def as_run(user, argv, stdin=None, env=None):
    return subprocess.run(["runuser", "-u", user, "--", "env", *(env or []), *argv], input=stdin,
                          capture_output=True, text=True, timeout=120)


def ups(calls):
    return [c for c in calls if c[:1] == ["compose"] and "up" in c]


class moved:
    """The helper ABSENT: moved out of the zoe-priv dir for the block, put back after."""
    def __init__(self, name):
        self.src, self.dst = HELPER + name, "/root/away-" + name

    def __enter__(self):
        shutil.move(self.src, self.dst)

    def __exit__(self, *a):
        shutil.move(self.dst, self.src)


# The kernel's vm sysctls are NOT namespaced: a write from this container would drop the HOST's caches. Before any
# cell executes a memory helper, both files are bind-mounted over with lab files (SYS_ADMIN, this container's own
# mount namespace) and the redirection is PROVEN by inode; unproven = the executing cells do not run (they FAIL).
VM = {"/proc/sys/vm/drop_caches": "/run/p0lab-drop_caches", "/proc/sys/vm/compact_memory": "/run/p0lab-compact_memory"}
for real, fake in VM.items():
    open(fake, "w").close()
    run("mount", "--bind", fake, real)
SAFE = all((os.stat(r).st_dev, os.stat(r).st_ino) == (os.stat(f).st_dev, os.stat(f).st_ino) for r, f in VM.items())
cell("P0-vm-sysctls-redirected", SAFE, "both vm files are lab files" if SAFE else "NOT redirected: executing cells skipped")


def vm_reset():
    for f in VM.values():
        open(f, "w").close()


def vm_read():
    return tuple(open(f).read().strip() for f in VM.values())


# P1: the sudoers lines for the deploy helper are exact argv; nothing around them is allowed
d_ok = [[DEPLOY, "zoe-auth"], [DEPLOY, "zoe-auth", "--build"], [DEPLOY, "zoe-ui"], [DEPLOY, "modules"],
        [DEPLOY, "logs", "zoe-auth"], [DEPLOY, "logs", "zoe-ui"]]
d_bad = [[DEPLOY], [DEPLOY, "zoe-ui", "--build"], [DEPLOY, "logs", "homeassistant"], [DEPLOY, "modules", "x"],
         [DEPLOY, "zoe-auth;id"], [DEPLOY, "../zoe-auth"], [DEPLOY, "zoe-auth", SHAS["main"]],
         [DEPLOY, "zoe-auth", "--build", "--pull"], ["/usr/bin/docker", "compose", "up"]]
ok = [c for c in d_ok if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
bad = [c for c in d_bad if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
inst = open(DEPLOY).read().splitlines()
deliv = open("/opt/p0/deliverables/libexec/zoe-compose-deploy").read().splitlines()
odiff = [(a, b) for a, b in zip(inst, deliv) if a != b]
cell("P1-deploy-sudoers-exact", len(ok) == len(d_ok) and not bad and len(inst) == len(deliv) and len(odiff) == 1
     and odiff[0][1].startswith("ORIGIN = ") and odiff[0][0] == 'ORIGIN = "file:///opt/p0/origin.git"',
     f"allowed {len(ok)}/{len(d_ok)}, leaked={bad}; installed copy differs from the deliverable in {len(odiff)} line(s)")

# P2: the helper's OWN validation (as root, so sudo is not what refuses): bad argv and bad stdin -> 2, no docker call
fresh()
argv_bad = [[], ["zoe-auth", "--build", "x"], ["zoe-ui", "--build"], ["logs", "homeassistant"], ["logs"],
            ["zoe-auth;id"], ["../zoe-auth"], ["modules", "--build"], ["ZOE-AUTH"]]
stdin_bad = ["", "abc\n", SHAS["main"].upper() + "\n", SHAS["main"] + " x\n", SHAS["main"] + "0\n", "main\n",
             "$(id)\n", "../" + SHAS["main"][3:] + "\n", SHAS["main"] + "\n" + SHAS["off"] + "\n"]
rcs = [subprocess.run([DEPLOY, *a], input=SHAS["main"] + "\n", capture_output=True, text=True).returncode for a in argv_bad]
rcs += [subprocess.run([DEPLOY, "zoe-auth"], input=s, capture_output=True, text=True).returncode for s in stdin_bad]
cell("P2-deploy-helper-validates", set(rcs) == {2} and not dcalls(), f"rcs={rcs} docker_calls={len(dcalls())}")

# P3: every verb works as `zoe` through sudo with the broad grant removed (sha on main, binds "moved")
scen("ready")
res = {}
for verb in (["zoe-auth"], ["zoe-auth", "--build"], ["zoe-ui"], ["modules"], ["logs", "zoe-auth"]):
    fresh()
    r = as_run("zoe", ["sudo", "-n", DEPLOY, *verb], stdin=None if verb[0] == "logs" else SHAS["main"] + "\n")
    res[" ".join(verb)] = (r.returncode, dcalls(), r.stderr.strip()[-160:])
tree = "/var/lib/zoe-compose-deploy/trees/" + SHAS["main"]
up_auth = ups(res["zoe-auth"][1])
st = os.lstat(tree)
checks = {
    "auth": res["zoe-auth"][0] == 0 and len(up_auth) == 1 and up_auth[0][-1] == "zoe-auth" and "--build" not in up_auth[0]
            and up_auth[0][up_auth[0].index("--project-name") + 1] == "assistant"
            and up_auth[0][up_auth[0].index("--project-directory") + 1] == tree,
    "build": res["zoe-auth --build"][0] == 0 and "--build" in ups(res["zoe-auth --build"][1])[0],
    "ui": res["zoe-ui"][0] == 0 and [c[0] for c in res["zoe-ui"][1] if c[0] in ("run", "restart")] == ["run", "restart"]
          and "nginx" in res["zoe-ui"][1][1] and ups(res["zoe-ui"][1])[0][-1] == "zoe-ui",
    "modules": res["modules"][0] == 0 and ups(res["modules"][1])[0][-1] == "music-assistant"
               and "--wait" in ups(res["modules"][1])[0],
    "logs": res["logs zoe-auth"][0] == 0 and res["logs zoe-auth"][1] == [["logs", "--tail", "80", "zoe-auth"]],
    "tree-root-owned": st.st_uid == 0 and not st.st_mode & 0o022 and os.path.isfile(tree + "/services/zoe-ui/nginx.conf"),
}
cell("P3-deploy-verbs-without-grant", all(checks.values()),
     {k: v for k, v in checks.items()} | {"rcs": {k: v[0] for k, v in res.items()}, "err": res["zoe-auth"][2]})

# P4: a commit that is not on main (or unknown) is refused BEFORE any compose call
fresh()
r_off = as_run("zoe", ["sudo", "-n", DEPLOY, "zoe-auth"], stdin=SHAS["off"] + "\n")
r_unknown = as_run("zoe", ["sudo", "-n", DEPLOY, "zoe-auth"], stdin="0" * 40 + "\n")
cell("P4-deploy-refuses-off-main", (r_off.returncode, r_unknown.returncode) == (3, 3) and not dcalls(),
     f"off={r_off.returncode} unknown={r_unknown.returncode} docker_calls={len(dcalls())}")

# P5: not ready (bind under /home, a symlink in a bind source, a rw bind into the tree, a loose env file) -> 6, no up
rc6 = {}
for sc in ("home", "symlink", "rwtree"):
    scen(sc)
    fresh()
    svc = "zoe-ui" if sc == "rwtree" else "zoe-auth"
    rc6[sc] = (as_run("zoe", ["sudo", "-n", DEPLOY, svc], stdin=SHAS["main"] + "\n").returncode, len(ups(dcalls())))
scen("ready")
os.chmod("/etc/zoe/compose.env", 0o644)
fresh()
rc6["envfile"] = (as_run("zoe", ["sudo", "-n", DEPLOY, "zoe-auth"], stdin=SHAS["main"] + "\n").returncode, len(ups(dcalls())))
os.chmod("/etc/zoe/compose.env", 0o600)
cell("P5-deploy-refuses-swappable-binds", all(v == (6, 0) for v in rc6.values()), rc6)

# P6: a planted symlink where the export goes is refused, never followed (root planted: zoe cannot reach the dir)
fresh()
shutil.move(tree, tree + ".kept")
os.symlink("/tmp", tree)
r_ln = subprocess.run([DEPLOY, "zoe-auth"], input=SHAS["main"] + "\n", capture_output=True, text=True)
os.unlink(tree)
shutil.move(tree + ".kept", tree)
cell("P6-deploy-planted-tree-symlink", r_ln.returncode == 5 and not dcalls(),
     f"rc={r_ln.returncode} {r_ln.stderr.strip()[-120:]}")

# P7: the deploy SEAM (scripts/deploy/compose_deploy.sh) picks helper / legacy / fallback-on-not-ready
seam = CO + "/scripts/deploy/compose_deploy.sh"
fresh()
r_h = as_run("zoe", ["bash", seam, "zoe-auth"])
h_ok = r_h.returncode == 0 and "priv-route zoe-compose-deploy=helper" in r_h.stdout and "--project-directory" in ups(dcalls())[0]
with moved("zoe-compose-deploy"):
    fresh()
    r_l = as_run("zoe-pre", ["bash", seam, "zoe-auth", "--build"])
    l_ok = r_l.returncode == 0 and "=legacy" in r_l.stdout and ups(dcalls()) == [["compose", "up", "-d", "--no-deps", "--build", "zoe-auth"]]
scen("home")
fresh()
r_f = as_run("zoe-pre", ["bash", seam, "zoe-auth"])
f_ok = r_f.returncode == 0 and "=helper" in r_f.stdout and "=legacy" in r_f.stdout \
       and ups(dcalls()) == [["compose", "up", "-d", "--no-deps", "zoe-auth"]]
scen("ready")
cell("P7-deploy-seam-routes", h_ok and l_ok and f_ok,
     f"helper={h_ok} legacy={l_ok} not-ready-fallback={f_ok} err={(r_h.stderr + r_l.stderr + r_f.stderr).strip()[-200:]}")

# P8: brain_cold_start.sh (llama-server's ExecStartPre), verbatim, three ways
bcs = CO + "/scripts/maintenance/brain_cold_start.sh"


def cold(user):
    env = [f"BRAIN_COLD_START_STATE_DIR=/tmp/bcs-{user}", "BRAIN_COLD_START_MAX_S=1", "BRAIN_COLD_START_NO_SYSTEMD_RUN=1",
           "BRAIN_COLD_START_HEALTH_URL=http://127.0.0.1:1/health"]
    out = as_run(user, ["bash", bcs], env=env).stdout
    time.sleep(2.5)            # the detached drop loop is bounded by MAX_S=1: let it end before the next case
    return out


if SAFE:
    vm_reset()
    out_h = cold("zoe")
    vm_h = vm_read()
    with moved("zoe-mem-compact"):
        vm_reset()
        out_l = cold("zoe-pre")
        vm_l = vm_read()
        vm_reset()
        out_none = cold("zoe")
        vm_none = vm_read()
    c8 = {"helper-no-grant (root wrote the drop)": "zoe-mem-compact=helper" in out_h and "SKIPPED" not in out_h and vm_h[0] == "1",
          "legacy-with-grant": "zoe-mem-compact=legacy" in out_l and "SKIPPED" not in out_l and vm_l[0] == "1",
          "neither (P0-3 before P0-0): skipped, nothing written": "zoe-mem-compact=legacy" in out_none
          and "SKIPPED" in out_none and vm_none == ("", "")}
    cell("P8-cold-start-routes", all(c8.values()), c8)
else:
    cell("P8-cold-start-routes", False, "not run: the vm sysctls could not be redirected")

# P9: the shared routing module (night window, bake-off, engine bench; omi_repass uses the same argv) with REAL sudo
probe = ("import sys, subprocess; sys.path.insert(0, %r); import zoe_priv as p; "
         "rc = lambda a: subprocess.run(a, capture_output=True).returncode; "
         "r = subprocess.run(p.route('zoe-mem-compact', ('3',), ['sudo','-n','sh','-c','true'])[1], capture_output=True, text=True); "
         "n = subprocess.run(p.route('zoe-nvmap-read', ('free',), ['sudo','-n','true'])[1], capture_output=True, text=True); "
         "print(p.route('zoe-mem-compact', ('3',), ['x'])[0], p.can_run(rc, 'zoe-mem-compact', ('3',)), "
         "p.can_run(rc, 'zoe-nvmap-read', ('clients',)), 'password' in r.stderr + n.stderr, 'not allowed' in r.stderr + n.stderr)"
         % (CO + "/scripts/lib"))
if SAFE:
    vm_reset()
    p_h = as_run("zoe", ["python3", "-c", probe]).stdout.split()
    vm9 = vm_read()
    with moved("zoe-mem-compact"), moved("zoe-nvmap-read"):
        p_l = as_run("zoe-pre", ["python3", "-c", probe]).stdout.split()
        p_n = as_run("zoe", ["python3", "-c", probe]).stdout.split()
    c9 = {"helper, no grant: routed, allowed, run as root (3 = drop all + compact)":
          p_h == ["helper", "True", "True", "False", "False"] and vm9 == ("3", "1"),
          "legacy with grant": p_l[:3] == ["legacy", "True", "True"],
          "neither: can_run is False (the window refuses loudly)": p_n[:3] == ["legacy", "False", "False"]}
    cell("P9-zoe-priv-routes", all(c9.values()), {"helper": p_h, "vm": vm9, "legacy": p_l, "none": p_n})
else:
    cell("P9-zoe-priv-routes", False, "not run: the vm sysctls could not be redirected")

# P11: zoe-db-exec (database work without proxied exec): exact sudoers verbs, its own validation, the argv it runs
DBX = HELPER + "zoe-db-exec"
x_ok = [[DBX, "isready"], [DBX, "dump", "zoe"], [DBX, "dump", "multica"], [DBX, "dumpall-globals"], [DBX, "psql", "zoe"],
        [DBX, "psql-at", "multica"], [DBX, "restore-list"], [DBX, "restore-schema"], [DBX, "restore-data"]]
x_bad = [[DBX], [DBX, "dump", "postgres"], [DBX, "psql", "zoe", "-c"], [DBX, "exec"], [DBX, "isready", "-h", "x"]]
ok11 = [c for c in x_ok if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
bad11 = [c for c in x_bad if run("runuser", "-u", "zoe", "--", "sudo", "-n", "-l", *c).returncode == 0]
self_rc = [subprocess.run([DBX, *a], input=i, capture_output=True, text=True).returncode
           for a, i in ((["dump", "zoe"], "Bad;name\n"), (["dump", "zoe"], "*\n"), (["psql", "x"], ""), (["sh"], ""))]
fresh()
r11 = as_run("zoe", ["sudo", "-n", DBX, "dump", "zoe"], stdin="learned_voiceprints\n")
c11 = dcalls()
want = ["exec", "-i", "-u", "postgres", "zoe-database", "pg_dump", "-U", "zoe", "-Fc",
        "--exclude-table-data=learned_voiceprints", "zoe"]
cell("P11-db-exec", len(ok11) == len(x_ok) and not bad11 and set(self_rc) == {2} and r11.returncode == 0
     and c11 == [want] and c11[0] and json.loads(open(DLOG).read().splitlines()[0])["uid"] == 0,
     f"allowed {len(ok11)}/{len(x_ok)} leaked={bad11} self={self_rc} rc={r11.returncode} calls={c11}")

# P10: the Docker proxy carries exactly what each rewired Docker caller needs, and refuses what they used to do
x = "a" * 64
need = {"zoe-data ma_ondemand / ma_idle_reap": [("HEAD", "/_ping"), ("GET", "/v1.47/containers/zoe-music-assistant/json"),
                                                ("POST", "/v1.47/containers/zoe-music-assistant/start"),
                                                ("POST", "/v1.47/containers/zoe-music-assistant/stop")],
        "zoe-data voice_livekit": [("GET", "/v1.47/containers/livekit/json"), ("POST", "/v1.47/containers/livekit/start"),
                                   ("POST", "/v1.47/containers/livekit/stop")],
        "backup / drill (inspect only; their database work is zoe-db-exec)": [("GET", "/v1.47/containers/zoe-database/json")],
        "health (zoe_health, platform_health_check)": [("GET", "/v1.47/version"), ("GET", "/v1.47/containers/json")]}
refuse = [("GET", "/v1.47/info"), ("GET", "/v1.47/containers/homeassistant/json"),
          ("GET", "/v1.47/containers/zoe-auth/logs"), ("POST", "/v1.47/containers/zoe-ui/restart"),
          ("POST", "/v1.47/containers/create?name=zoe-restore-drill"), ("GET", "/v1.47/containers/zoe-database/stats"),
          ("POST", "/v1.47/containers/zoe-omnigent/exec"), ("POST", "/v1.47/containers/zoe-database/exec"),
          ("POST", f"/v1.47/exec/{x}/start"), ("GET", f"/v1.47/exec/{x}/json")]
miss = [(k, m, u) for k, reqs in need.items() for m, u in reqs if as_user("zoe", m, u) != "200"]
leak = [(m, u) for m, u in refuse if as_user("zoe", m, u) not in ("403", "405")]
seen2 = set(run("cat", "/run/p0lab-dockerd.log").stdout.splitlines())
reached = [f"{m} {u}" for m, u in refuse if f"{m} {u}" in seen2]
cell("P10-proxy-covers-rewired-callers", not miss and not leak and not reached,
     f"needed {sum(map(len, need.values())) - len(miss)}/{sum(map(len, need.values()))}; missing={miss}; leaked={leak}; reached-daemon={reached}")

say("P0LAB-RESULT " + json.dumps({"pass": sum(r["pass"] for r in results), "total": len(results),
                                  "cells": results, "probes": probes}))
OUT.close()
open("/run/p0lab-cells.done", "w").close()
