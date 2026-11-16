"""Lab stand-in for the container runtime (root, INSIDE the lab container only). Lab-only.

What it reproduces from Docker, because the routes depend on exactly these behaviours:
  - a bind SOURCE is resolved by mount(2), as root, at EVERY start, following symlinks (`mount --bind` here);
  - a restart policy re-runs that resolution after a crash, with no request from anyone;
  - "unprivileged root container": uid 0, Docker's default capability set as the bounding set, the rest of the
    filesystem read-only, writes only through its binds; "privileged": uid 0, every capability, the host
    filesystem writable (a privileged container can mount the host disk, so this is the honest stand-in);
  - `user:`, `cap_drop: [ALL]` and `no-new-privileges` map to setpriv --reuid/--regid, --bounding-set -all,
    --no-new-privs.
It does NOT reproduce images, networks or cgroups; nothing here needs them.
"""
import shlex
import subprocess

DOCKER_DEFAULT_CAPS = ("chown,dac_override,fowner,fsetid,kill,setgid,setuid,setpcap,net_bind_service,net_raw,"
                       "sys_chroot,mknod,audit_write,setfcap")
SVC = "/opt/p02/lab/svc.py"


def script(spec):
    """The shell that builds one start of the container in a private mount namespace."""
    lines = ["set -e"]
    for src, dst, mode in spec["binds"]:
        lines.append(f"mount --bind {shlex.quote(src)} {shlex.quote(dst)}")
        if mode == "ro":
            lines.append(f"mount -o remount,bind,ro {shlex.quote(dst)}")
    if not spec.get("privileged"):
        lines.append("mount -o remount,bind,ro /")
    priv = ["setpriv"]
    uid = spec.get("user", "0:0")
    if uid != "0:0":
        u, g = uid.split(":")
        priv += ["--reuid", u, "--regid", g, "--clear-groups"]
    if spec.get("privileged"):
        pass
    elif spec.get("cap_drop_all"):
        priv += ["--bounding-set", "-all", "--inh-caps", "-all"]
    else:
        priv += ["--bounding-set", "-all," + ",".join("+" + c for c in DOCKER_DEFAULT_CAPS.split(","))]
    if spec.get("no_new_privileges"):
        priv.append("--no-new-privs")
    argv = (priv + ["--"] if len(priv) > 1 else []) + ["python3", "-B", SVC, *spec["cmd"]]
    lines.append("exec " + " ".join(shlex.quote(a) for a in argv))
    return "\n".join(lines)


def prepare(spec):
    """Create the mount points (outside the namespace, before any start)."""
    for src, dst, _ in spec["binds"]:
        is_file = subprocess.run(["test", "-f", src]).returncode == 0
        subprocess.run(["mkdir", "-p", dst.rsplit("/", 1)[0] if is_file else dst], check=True)
        if is_file:
            subprocess.run(["touch", dst], check=True)


def start(spec, timeout=20):
    """One start. Returns (rc, stdout+stderr)."""
    prepare(spec)
    p = subprocess.run(["unshare", "--mount", "--propagation", "private", "sh", "-c", script(spec)],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def supervise(spec, restarts=3, timeout=20):
    """A restart policy (`unless-stopped` / `on-failure`): re-start on a non-zero exit, up to `restarts` times.
    Every start re-resolves every bind source. Returns the list of (rc, output) per start."""
    runs = [start(spec, timeout)]
    while runs[-1][0] != 0 and len(runs) <= restarts:
        runs.append(start(spec, timeout))
    return runs
