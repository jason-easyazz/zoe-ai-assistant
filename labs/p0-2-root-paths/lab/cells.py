#!/usr/bin/env python3
"""P0-2 root-path lab cells (root, inside the lab container). Prints `CELL <id> PASS|FAIL detail` lines and one
`P0LAB-RESULT {json}` line on stdout; run.sh reads them. Synthetic names and contents only. Lab-only.

The box modelled: AFTER P0-3 (`zoe` has no sudo binary rule and no Docker socket), BEFORE P0-2. Each class of
section 2.6 (and the classes the 2026-11-14 inventory added) gets three cells: (a) the route exists: `zoe` makes
uid 0 write a marker into /root (0700 root) or read a root-only canary; (b) the P0-2 fix is applied with the
shipped deliverable and the same attempt fails, with a negative control that removes ONE piece of the fix and
reopens the route; (c) the service still does its job."""
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/opt/p02/lab")
import runtime  # noqa: E402

D = "/opt/p02/deliverables"
PATHS = [sys.executable, "-I", f"{D}/p0-2/zoe-p0-2-paths"]
HELPER = "/usr/local/libexec/zoe-priv/zoe-omnigent-exec"
MAN = "/opt/p02/lab.manifest"
results = []


def cell(cid, ok, detail=""):
    results.append({"cell": cid, "pass": bool(ok), "detail": str(detail)[:400]})
    print(f"CELL {cid} {'PASS' if ok else 'FAIL'} {detail}"[:600], flush=True)


def run(*argv, **kw):
    return subprocess.run(list(argv), capture_output=True, text=True, **kw)


def zoe(cmd):
    """A shell command as the service user `zoe` (no sudo rule, no Docker group: the post-P0-3 identity)."""
    return run("runuser", "-u", "zoe", "--", "sh", "-c", cmd)


def root_marker(path):
    """True when path exists and is owned by uid 0: proof that root wrote it (it sits in /root, 0700 root)."""
    try:
        return os.stat(path).st_uid == 0
    except FileNotFoundError:
        return False


def rm(*paths):
    for p in paths:
        if os.path.islink(p) or os.path.isfile(p):
            os.unlink(p)
        elif os.path.isdir(p):
            shutil.rmtree(p)


def zoe_tree(path, files):
    """A bind source the way it is on the box today: under /home/zoe, zoe-owned, 0775."""
    os.makedirs(path, exist_ok=True)
    for rel, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(path, rel)), exist_ok=True)
        with open(os.path.join(path, rel), "w") as fh:
            fh.write(text)
    run("chown", "-R", "zoe:zoe", path)
    run("chmod", "-R", "u+rwX,g+rwX,o+rX", path)


def paths(*args):
    return run(*PATHS, *args)


def spec(name, src, cmd, mode="rw", **kw):
    dst = f"/ctr/{name}/" + ("config.yaml" if cmd[0] == "rtc" else "vol")
    return {"name": name, "binds": [(src, dst, mode)], "cmd": [cmd[0], dst, *cmd[1:]], **kw}


def write(path, text, mode=None):
    with open(path, "w") as fh:
        fh.write(text)
    if mode is not None:
        os.chmod(path, mode)


# --- setup: the service user, the lab manifest, root-only canary --------------------------------------------
os.makedirs("/srv", exist_ok=True)
write("/root/p02-canary", "CANARY-root-only\n", 0o600)
write("/opt/p02/lab/payload", "written by the service user's payload\n", 0o644)
write(MAN, """dir  lab-hub   /home/zoe/lab/hub-config /srv/zoe/lab-hub          root root 0700
dir  lab-auth  /home/zoe/lab/auth-data  /srv/zoe/lab-auth         root root 0700
dir  lab-media /home/zoe/lab/media      /srv/zoe/lab-media        root root 0700
file lab-rtc   /home/zoe/lab/rtc.yaml   /srv/zoe/lab-rtc/config.yaml root root 0600
exec lab-unit  /usr/local/bin/lab-unit-helper.py /usr/local/bin/lab-unit-helper.py root root 0755
""")
os.makedirs("/home/zoe/lab", exist_ok=True)
run("chown", "zoe:zoe", "/home/zoe/lab")

# --- C: instrument controls ---------------------------------------------------------------------------------
c1 = zoe("echo x > /root/p02-direct")
cell("C1-zoe-cannot-write-root-directly", c1.returncode != 0 and not root_marker("/root/p02-direct"), c1.stderr.strip())
cell("C2-zoe-has-no-sudo-or-docker-group", run("sh", "-c", "command -v sudo").returncode != 0
     and "docker" not in run("id", "-nG", "zoe").stdout.split(), run("id", "-nG", "zoe").stdout.strip())

# --- R1: a PRIVILEGED container loads code from a config dir the service user can write (2.6 #1) -----------
HUB_OLD = "/home/zoe/lab/hub-config"
zoe_tree(HUB_OLD, {"configuration.yaml": "name: lab hub\n", "custom_components/light.py": "LEGIT = True\n"})
EVIL = "open('/root/p02-{tag}', 'w').write('root effect')\n"
zoe(f"printf %s \"{EVIL.format(tag='R1')}\" > {HUB_OLD}/custom_components/p02_evil.py")
rc, out = runtime.start(spec("hub-old", HUB_OLD, ["hub"], privileged=True))
cell("R1a-route-exists-privileged-code-load", root_marker("/root/p02-R1"), f"rc={rc} {out}")
# X7: the move's data copy carries a plant across: the rehearsal reviews the listing before copying.
write("/opt/p02/copytest.manifest", "dir lab-copytest /home/zoe/lab/hub-config /srv/zoe/lab-copytest root root 0700\n")
paths("apply", "/opt/p02/copytest.manifest", "--container-stopped")
cell("X7-copy-carries-a-plant-so-review-first", os.path.exists("/srv/zoe/lab-copytest/custom_components/p02_evil.py"),
     "a file the service user planted in the old tree arrives in the new one")
rm("/srv/zoe/lab-copytest", f"{HUB_OLD}/custom_components/p02_evil.py", "/root/p02-R1")
ap = paths("apply", MAN, "--only", "lab-hub", "--container-stopped")
HUB_NEW = "/srv/zoe/lab-hub"
w = zoe(f"printf %s \"{EVIL.format(tag='R1b')}\" > {HUB_NEW}/custom_components/p02_evil.py")
rc, out = runtime.start(spec("hub-new", HUB_NEW, ["hub"], privileged=True))
cell("R1b-fix-root-owned-config-closes", ap.returncode == 0 and w.returncode != 0 and not root_marker("/root/p02-R1b"),
     f"apply={ap.stdout.strip()} zoe-write={w.stderr.strip()[:120]} start rc={rc} (still privileged: ownership alone closes it)")
run("chown", "zoe", HUB_NEW)  # NEGATIVE CONTROL: one component (the source dir) back to the service user
zoe(f"cd {HUB_NEW} && mv custom_components cc.away && mkdir custom_components && "
    f"printf %s \"{EVIL.format(tag='R1nc')}\" > custom_components/p02_evil.py")
runtime.start(spec("hub-new", HUB_NEW, ["hub"], privileged=True))
cell("R1b-nc-source-dir-owned-by-zoe-reopens", root_marker("/root/p02-R1nc"), "zoe renamed custom_components away")
rm(f"{HUB_NEW}/custom_components", "/root/p02-R1nc")
os.rename(f"{HUB_NEW}/cc.away", f"{HUB_NEW}/custom_components")
run("chown", "root", HUB_NEW)
rc, out = runtime.start(spec("hub-new", HUB_NEW, ["hub"], privileged=True))
storage = zoe(f"cat {HUB_NEW}/.storage/core")
cell("R1c-hub-still-works", rc == 0 and "plugins=1" in out and os.path.exists(f"{HUB_NEW}/.storage/core")
     and storage.returncode != 0, f"rc={rc} {out}; zoe reading its state: {storage.stderr.strip()[:80]}")
v = paths("verify", MAN, "--only", "lab-hub")
cell("R1-verify-pass-after-fix", v.returncode == 0, v.stdout.strip())

# --- R5 (new): a DORMANT container definition still binds the OLD path, which stays for the rollback week ----
REG = {"hub-new": spec("hub-new", HUB_NEW, ["hub"], privileged=True),
       "hub-dormant": spec("hub-dormant", HUB_OLD, ["hub"], privileged=True)}


def inspect_lines(reg):
    return "".join("|".join(json.dumps(x) for x in (
        "/" + n, "exited", s.get("privileged", False), s.get("user", ""), None, None,
        [{"Type": "bind", "Source": b[0], "Destination": b[1], "RW": b[2] == "rw"} for b in s["binds"]])) + "\n"
        for n, s in reg.items())


def start_all(reg):
    return [runtime.start(s) for s in reg.values()]


zoe(f"printf %s \"{EVIL.format(tag='R5')}\" > {HUB_OLD}/custom_components/p02_evil.py")
start_all(REG)
ins = run(*PATHS, "inspect", "-", input=inspect_lines(REG))
cell("R5a-route-exists-dormant-definition-on-old-path", root_marker("/root/p02-R5") and ins.returncode == 1
     and "FAIL hub-dormant" in ins.stdout, ins.stdout.strip().replace("\n", " / ")[:300])
rm("/root/p02-R5")
dormant = REG.pop("hub-dormant")  # the fix: `docker rm` of the stopped definition
ins = run(*PATHS, "inspect", "-", input=inspect_lines(REG))
runs = start_all(REG)
cell("R5b-fix-remove-dormant-definition-closes", not root_marker("/root/p02-R5") and ins.returncode == 0,
     ins.stdout.strip())
REG["hub-dormant"] = dormant  # NEGATIVE CONTROL: the definition comes back (e.g. a rollback compose up)
start_all(REG)
cell("R5b-nc-definition-back-reopens", root_marker("/root/p02-R5"), "dormant definition started again")
REG.pop("hub-dormant")
rm("/root/p02-R5", f"{HUB_OLD}/custom_components/p02_evil.py")
runs = start_all(REG)
cell("R5c-live-hub-still-works", all(r[0] == 0 for r in runs), runs)

# --- R2: a bind SOURCE is re-resolved as root at every start; a crash under a restart policy pulls it (2.6 #2)
AUTH_OLD = "/home/zoe/lab/auth-data"
zoe_tree(AUTH_OLD, {"app.conf": "synthetic-auth-conf\n"})
s_old = spec("auth-old", AUTH_OLD, ["data", "3"])
box = {}
t = threading.Thread(target=lambda: box.setdefault("runs", runtime.supervise(s_old, restarts=2)))
t.start()
time.sleep(1.0)
sw = zoe(f"cd /home/zoe/lab && mv auth-data auth-data.old && ln -s /root auth-data && touch auth-data.old/crash")
t.join(60)
runs = box.get("runs", [])
cell("R2a-route-exists-bind-swap-via-crash-restart", root_marker("/root/p02-state") and len(runs) >= 2,
     f"swap rc={sw.returncode}; starts={[(r[0], r[1][:40]) for r in runs]}")
rm("/root/p02-state", AUTH_OLD)
os.rename(AUTH_OLD + ".old", AUTH_OLD)
rm(f"{AUTH_OLD}/crash")
# X5: the move refuses an old path that resolves through a symlink (the copy runs as root).
os.rename(AUTH_OLD, AUTH_OLD + ".real")
os.symlink("/root", AUTH_OLD)
x5 = paths("apply", MAN, "--only", "lab-auth", "--container-stopped")
cell("X5-apply-refuses-symlinked-old-path", x5.returncode != 0 and not os.path.exists("/srv/zoe/lab-auth"),
     x5.stderr.strip()[:200])
os.unlink(AUTH_OLD)
os.rename(AUTH_OLD + ".real", AUTH_OLD)
ap = paths("apply", MAN, "--only", "lab-auth", "--container-stopped")
AUTH_NEW = "/srv/zoe/lab-auth"
# the COMPLETE swap the negative control below proves works when a parent is zoe-owned: rename the source away and
# leave a symlink to /root in its place. With the root-owned chain it must be refused at the first step, the source
# must still be the real directory, and the legitimate service must still start from it.
sw = zoe(f"cd /srv/zoe && mv lab-auth lab-auth.away && ln -s /root lab-auth")
rc_b, out_b = runtime.start(spec("auth-new", AUTH_NEW, ["data"]))  # a reboot: root starts it again
cell("R2b-fix-root-owned-chain-closes", ap.returncode == 0 and sw.returncode != 0
     and not os.path.islink(AUTH_NEW) and os.path.isdir(AUTH_NEW) and not os.path.lexists("/srv/zoe/lab-auth.away")
     and rc_b == 0 and "synthetic-auth-conf" in out_b and not root_marker("/root/p02-state"),
     f"zoe: {sw.stderr.strip()[:160]}; start rc={rc_b}")
run("chown", "zoe", "/srv/zoe")  # NEGATIVE CONTROL: one PARENT component owned by the service user
zoe(f"cd /srv/zoe && mv lab-auth lab-auth.away && ln -s /root lab-auth")
runtime.start(spec("auth-new", AUTH_NEW, ["data"]))
cell("R2b-nc-parent-owned-by-zoe-reopens", root_marker("/root/p02-state"), "zoe renamed the source and left a symlink")
rm("/root/p02-state", AUTH_NEW)
os.rename("/srv/zoe/lab-auth.away", AUTH_NEW)
run("chown", "root", "/srv/zoe")
rc, out = runtime.start(spec("auth-new", AUTH_NEW, ["data"]))
cell("R2c-data-service-still-works", rc == 0 and "synthetic-auth-conf" in out and os.path.exists(f"{AUTH_NEW}/p02-state"),
     f"rc={rc} {out}")

# --- R2f: the same re-resolution for a single-FILE bind (the realtime-media server's config, B7) -----------
RTC_OLD = "/home/zoe/lab/rtc.yaml"
write(RTC_OLD, "rtc: synthetic\n", 0o664)
run("chown", "zoe:zoe", RTC_OLD)
zoe(f"rm {RTC_OLD} && ln -s /root/p02-canary {RTC_OLD}")
rc, out = runtime.start(spec("rtc-old", RTC_OLD, ["rtc"], mode="ro"))
cell("R2f-a-route-exists-file-bind-swap-discloses-root-file", "CANARY-root-only" in out, out)
os.unlink(RTC_OLD)
write(RTC_OLD, "rtc: synthetic\n", 0o664)
run("chown", "zoe:zoe", RTC_OLD)
ap = paths("apply", MAN, "--only", "lab-rtc", "--container-stopped")
RTC_NEW = "/srv/zoe/lab-rtc/config.yaml"
sw = zoe(f"rm -f {RTC_NEW}; ln -sf /root/p02-canary {RTC_NEW}")
rc, out = runtime.start(spec("rtc-new", RTC_NEW, ["rtc"], mode="ro"))
cell("R2f-b-fix-closes", ap.returncode == 0 and sw.returncode != 0 and "CANARY" not in out, f"{out}; zoe: {sw.stderr.strip()[:100]}")
run("chown", "zoe", "/srv/zoe/lab-rtc")  # NEGATIVE CONTROL: the file's directory owned by the service user
zoe(f"mv {RTC_NEW} {RTC_NEW}.away && ln -s /root/p02-canary {RTC_NEW}")
rc, nc = runtime.start(spec("rtc-new", RTC_NEW, ["rtc"], mode="ro"))
cell("R2f-b-nc-dir-owned-by-zoe-reopens", "CANARY-root-only" in nc, nc)
os.unlink(RTC_NEW)
os.rename(RTC_NEW + ".away", RTC_NEW)
run("chown", "root", "/srv/zoe/lab-rtc")
rc, out = runtime.start(spec("rtc-new", RTC_NEW, ["rtc"], mode="ro"))
cell("R2f-c-media-server-still-reads-its-config", rc == 0 and "rtc: synthetic" in out, out)

# --- R3 (new): an UNPRIVILEGED root container + a bind the service user can traverse = host root -------------
# (any route to code as container root: a set-uid file it writes into the bind runs as root on the host)
MEDIA_OLD = "/home/zoe/lab/media"
zoe_tree(MEDIA_OLD, {"app.conf": "synthetic-media\n"})
rc, out = runtime.start(spec("media-old", MEDIA_OLD, ["plant"]))
x = zoe(f"{MEDIA_OLD}/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3a-route-exists-setuid-plant-from-unprivileged-root-container", root_marker("/root/p02-R3"),
     f"{out}; zoe exec rc={x.returncode}")
rm("/root/p02-R3", f"{MEDIA_OLD}/p02-cp")
ap = paths("apply", MAN, "--only", "lab-media", "--container-stopped")
MEDIA_NEW = "/srv/zoe/lab-media"
rc, out = runtime.start(spec("media-new", MEDIA_NEW, ["plant"]))  # container root compromised by ANY route
x = zoe(f"{MEDIA_NEW}/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-1-fix-source-not-traversable-closes", ap.returncode == 0 and not root_marker("/root/p02-R3"),
     f"{out}; zoe exec: {x.stderr.strip()[:100]}")
os.chmod(MEDIA_NEW, 0o755)  # NEGATIVE CONTROL: the source traversable again
zoe(f"{MEDIA_NEW}/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-1-nc-traversable-reopens", root_marker("/root/p02-R3"), "mode 0755")
os.chmod(MEDIA_NEW, 0o700)
rm("/root/p02-R3", f"{MEDIA_NEW}/p02-cp")
# Layer 2: the container runs as its own non-root uid with no capabilities (the source traversable on purpose).
os.makedirs("/srv/zoe/lab-media2", exist_ok=True)
os.chown("/srv/zoe/lab-media2", 3000, 3000)
os.chmod("/srv/zoe/lab-media2", 0o755)
rc, out = runtime.start(spec("media2", "/srv/zoe/lab-media2", ["plant"], user="3000:3000", cap_drop_all=True,
                             no_new_privileges=True))
x = zoe("/srv/zoe/lab-media2/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-2-fix-non-root-container-closes", not root_marker("/root/p02-R3"), f"{out}; zoe exec: {x.stderr.strip()[:100]}")
rm("/srv/zoe/lab-media2/p02-cp")
runtime.start(spec("media2", "/srv/zoe/lab-media2", ["plant"]))  # NEGATIVE CONTROL: back to root
zoe("/srv/zoe/lab-media2/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-2-nc-root-container-reopens", root_marker("/root/p02-R3"), "user 0:0, default caps")
rm("/root/p02-R3", "/srv/zoe/lab-media2/p02-cp")
# Layer 3: /srv/zoe nosuid,nodev (srv-zoe.mount, read verbatim), root container, traversable source.
unit = dict(line.split("=", 1) for line in open(f"{D}/p0-2/srv-zoe.mount").read().splitlines()
            if "=" in line and not line.startswith("#"))
run("mount", "--bind", unit["What"], unit["Where"])
mnt = run("mount", "-o", "remount," + unit["Options"], unit["Where"])
opts = [ln.split()[5] for ln in open("/proc/self/mountinfo") if ln.split()[4] == "/srv/zoe"]
cell("X8-srv-zoe-mount-is-nosuid-nodev", mnt.returncode == 0 and opts and "nosuid" in opts[-1] and "nodev" in opts[-1],
     f"{opts} {mnt.stderr.strip()}")
rc, out = runtime.start(spec("media2", "/srv/zoe/lab-media2", ["plant"]))
x = zoe("/srv/zoe/lab-media2/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-3-fix-nosuid-mount-closes", "uid=0" in out and not root_marker("/root/p02-R3"), f"{out}; zoe exec rc={x.returncode}")
run("umount", "/srv/zoe")  # NEGATIVE CONTROL: without the mount the same planted file works
zoe("/srv/zoe/lab-media2/p02-cp /opt/p02/lab/payload /root/p02-R3")
cell("R3b-3-nc-no-mount-reopens", root_marker("/root/p02-R3"), "mount removed")
rm("/root/p02-R3", "/srv/zoe/lab-media2")
rc, out = runtime.start(spec("media-new", MEDIA_NEW, ["data"]))
cell("R3c-media-service-still-works", rc == 0 and "synthetic-media" in out, f"rc={rc} {out}")

# --- R4 (new, live class): a ROOT-run unit executes a file the service user owns (and 2.6 #3, the runner) ----
UNIT = "/usr/local/bin/lab-unit-helper.py"
GOOD = "import os; os.makedirs('/var/lib/lab-unit', exist_ok=True); open('/var/lib/lab-unit/ran', 'w').write('ok')\n"
write(UNIT, GOOD, 0o775)
os.chown(UNIT, 1000, 1000)
zoe(f"printf %s \"{EVIL.format(tag='R4')}\" >> {UNIT}")
run("python3", "-B", UNIT)  # the unit starting at boot (or after a crash under Restart=on-failure)
cell("R4a-route-exists-root-unit-runs-zoe-owned-file", root_marker("/root/p02-R4"), "")
rm("/root/p02-R4")
write(UNIT, GOOD)
ap = paths("apply", MAN, "--only", "lab-unit", "--container-stopped")
w = zoe(f"printf %s \"{EVIL.format(tag='R4')}\" >> {UNIT}")
run("python3", "-B", UNIT)
cell("R4b-fix-root-owned-file-closes", ap.returncode == 0 and w.returncode != 0 and not root_marker("/root/p02-R4"),
     w.stderr.strip()[:120])
os.chown(UNIT, 1000, 1000)  # NEGATIVE CONTROL 1: the file back to the service user
zoe(f"printf %s \"{EVIL.format(tag='R4')}\" >> {UNIT}")
run("python3", "-B", UNIT)
cell("R4b-nc1-file-owned-by-zoe-reopens", root_marker("/root/p02-R4"), "")
rm("/root/p02-R4")
write(UNIT, GOOD)
os.chown(UNIT, 0, 0)
# NEGATIVE CONTROL 2 (every component, the runner shape of 2.6 #3): a root-owned job in a zoe-owned directory.
os.makedirs("/home/zoe/lab/runner", exist_ok=True)
run("chown", "zoe:zoe", "/home/zoe/lab/runner")
write("/home/zoe/lab/runner/job.py", GOOD, 0o755)
write("/opt/p02/runner.manifest", "exec lab-runner /home/zoe/lab/runner/job.py /home/zoe/lab/runner/job.py root root 0755\n")
vr = paths("verify", "/opt/p02/runner.manifest")
zoe(f"cd /home/zoe/lab/runner && mv job.py job.away && printf %s \"{EVIL.format(tag='R4')}\" > job.py")
run("python3", "-B", "/home/zoe/lab/runner/job.py")
cell("R4b-nc2-root-file-in-zoe-dir-reopens-and-verify-catches", root_marker("/root/p02-R4") and vr.returncode == 1,
     vr.stdout.strip()[:200])
rm("/root/p02-R4", "/var/lib/lab-unit")
r = run("python3", "-B", UNIT)
cell("R4c-unit-still-works", r.returncode == 0 and os.path.exists("/var/lib/lab-unit/ran"), r.stderr.strip()[:100])

# --- O: the agent-orchestrator exec helper (B4) -------------------------------------------------------------
os.makedirs(os.path.dirname(HELPER), exist_ok=True)
shutil.copy(f"{D}/libexec/zoe-omnigent-exec", HELPER)
os.chmod(HELPER, 0o755)


def helper(*argv):
    before = open("/run/p02-docker.log").read().splitlines() if os.path.exists("/run/p02-docker.log") else []
    r = run(HELPER, *argv)
    after = open("/run/p02-docker.log").read().splitlines() if os.path.exists("/run/p02-docker.log") else []
    return r.returncode, [json.loads(x) for x in after[len(before):]]


rc, calls = helper("-i", "-w", "/tmp/review-1", "zoe-omnigent", "timeout", "-k", "5", "10", "codex", "exec", "-")
cell("O1-allowed-command-reaches-docker-as-1000", rc == 0 and calls == [["exec", "-u", "1000:1000", "-i", "-w",
     "/tmp/review-1", "zoe-omnigent", "timeout", "-k", "5", "10", "codex", "exec", "-"]], calls)
rc, calls = helper("-d", "zoe-omnigent", "sh", "-c", "cd / && omnigent run x", "")
cell("O1b-detached-sh-keeps-args-exactly", rc == 0 and calls == [["exec", "-u", "1000:1000", "-d", "zoe-omnigent",
     "sh", "-c", "cd / && omnigent run x", ""]], calls)
refused = {"O2-other-container-refused": ["homeassistant", "sh"],
           "O3-other-user-refused": ["-u", "0", "zoe-omnigent", "sh"],
           "O3b-long-user-flag-refused": ["--user=root", "zoe-omnigent", "sh"],
           "O4-privileged-flag-refused": ["--privileged", "zoe-omnigent", "sh"],
           "O4b-env-flag-refused": ["-e", "A=1", "zoe-omnigent", "sh"],
           "O4c-relative-or-dotdot-workdir-refused": ["-w", "/tmp/../root", "zoe-omnigent", "sh"],
           "O5-program-outside-allowlist-refused": ["zoe-omnigent", "bash", "-c", "id"],
           "O5b-absolute-program-refused": ["zoe-omnigent", "/bin/sh", "-c", "id"]}
for cid, argv in refused.items():
    rc, calls = helper(*argv)
    cell(cid, rc == 2 and calls == [], f"rc={rc} docker calls={calls}")

# --- X: the shipped public manifest, verbatim, on synthetic copies of the design's paths --------------------
PUB = f"{D}/p0-2/move.manifest"
items = [ln.split() for ln in open(PUB) if ln.strip() and not ln.startswith("#")]
for kind, name, old, new, *_ in items:
    if kind == "dir":
        zoe_tree(old, {"synthetic.txt": f"synthetic {name}\n"})
    else:
        os.makedirs(os.path.dirname(old), exist_ok=True)
        write(old, f"synthetic {name}\n", 0o664)
        os.chown(old, 1000, 1000)
ap = [paths("apply", PUB, "--only", it[1], "--container-stopped") for it in items]
v = paths("verify", PUB)
cell("X1-public-manifest-applies-and-verifies", all(a.returncode == 0 for a in ap) and v.returncode == 0
     and v.stdout.count("PASS") == len(items), v.stdout.strip().replace("\n", " / ")[:400])
os.chmod("/srv/zoe", 0o777)  # NEGATIVE CONTROL: one shared parent world-writable
v2 = paths("verify", PUB)
os.chmod("/srv/zoe", 0o755)
cell("X2-verify-catches-a-writable-parent", v2.returncode == 1 and v2.stdout.count("FAIL") == len(items),
     v2.stdout.splitlines()[0][:200] if v2.stdout else v2.stderr)
again = paths("apply", PUB, "--container-stopped")
cell("X3-apply-is-idempotent", again.returncode == 0 and again.stdout.count("re-asserted") == len(items)
     and paths("verify", PUB).returncode == 0, again.stdout.strip().replace("\n", " / ")[:300])
write("/srv/zoe/smb-drop/after-apply.txt", "written after the move\n", 0o600)   # data the revert must keep
rv = paths("revert", PUB, "--only", "smb-drop")
v3 = paths("verify", PUB, "--only", "smb-drop")
kept = [d for d in glob.glob("/srv/zoe/smb-drop.reverted-*")
        if open(os.path.join(d, "after-apply.txt")).read() == "written after the move\n"]
cell("X4-revert-renames-new-and-leaves-old", rv.returncode == 0 and v3.returncode == 1
     and not os.path.lexists("/srv/zoe/smb-drop") and len(kept) == 1
     and os.path.exists("/home/zoe/smb-drop/synthetic.txt"), f"{rv.stdout.strip()} kept={kept}")

summary = {"pass": sum(r["pass"] for r in results), "total": len(results), "cells": results}
print("P0LAB-RESULT " + json.dumps(summary), flush=True)
sys.exit(0 if summary["pass"] == summary["total"] else 1)
