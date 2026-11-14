"""P0-1: every privileged caller goes through a fixed root helper when it is installed, else keeps its old command
(docs/architecture/p0-deroot.md section 5; the hermetic proof with real sudo is labs/p0-deroot, P cells).

Pinned here, without root and without touching the machine (``sudo`` and ``docker`` are fakes on PATH, the helper
directory is a temp dir):
  - scripts/lib/zoe_priv.py's allowlist IS the sudoers allowlist (a widening on either side fails);
  - route / can_run choose helper vs legacy by the helper's presence, and the probe is ``sudo -n -l <helper>``;
  - zoe-compose-deploy refuses every argv and stdin outside its fixed set before it touches anything, and its bind
    checks refuse symlinks, writable ancestors, rw binds into the tree and sources outside tree and /srv/zoe;
  - each rewired caller (bake-off / night window, cold start, MTP window, engine bench, HA export, restore drill,
    the deploy seam) picks the helper when present and today's command when absent, and logs one fixed route line;
  - each Docker caller only uses calls the allowlisted proxy permits.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
P0 = REPO / "scripts" / "setup" / "p0-deroot"
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import zoe_priv  # noqa: E402
from test_p0_deroot_files import proxy_rules  # noqa: E402

DEPLOY_HELPER = P0 / "libexec" / "zoe-compose-deploy"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _load(name: str, path: Path):
    if name in sys.modules:                  # one module object per process (other test files load the same ones)
        return sys.modules[name]
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                  # dataclasses resolve their module through sys.modules
    loader.exec_module(mod)
    return mod


@pytest.fixture
def privdir(tmp_path, monkeypatch):
    """A helper dir with nothing installed; ``install(name)`` drops an executable stand-in in it."""
    d = tmp_path / "zoe-priv"
    d.mkdir()
    monkeypatch.setattr(zoe_priv, "PRIV_DIR", str(d))

    def install(name):
        p = d / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
        return str(p)
    return install


# ── the allowlist is ONE list ─────────────────────────────────────────────────────────────────────────────────

def sudoers_priv_argv(text: str) -> "set[tuple[str, tuple[str, ...]]]":
    joined = re.sub(r"\\\n\s*", "", text)
    out = set()
    for line in joined.splitlines():
        m = re.match(r"Cmnd_Alias\s+\w+\s*=\s*(.+)", line.split("#", 1)[0].strip())
        for cmd in (c.split() for c in (m.group(1).split(",") if m else [])):
            if cmd and cmd[0].startswith("/usr/local/libexec/zoe-priv/"):
                out.add((cmd[0].rsplit("/", 1)[1], tuple(cmd[1:])))
    return out


def module_priv_argv(allowed) -> "set[tuple[str, tuple[str, ...]]]":
    return {(n, a) for n, args in allowed.items() for a in args}


def test_zoe_priv_allowlist_is_the_sudoers_allowlist():
    assert module_priv_argv(zoe_priv.ALLOWED) == sudoers_priv_argv((P0 / "sudoers.d" / "60-zoe-deroot").read_text())


def test_negative_control_a_widened_module_list_is_caught():
    wider = dict(zoe_priv.ALLOWED)
    wider["zoe-mem-compact"] = wider["zoe-mem-compact"] | {("2",)}
    assert module_priv_argv(wider) != sudoers_priv_argv((P0 / "sudoers.d" / "60-zoe-deroot").read_text())


def test_every_sudoers_helper_exists_in_the_repo():
    for name, _ in sudoers_priv_argv((P0 / "sudoers.d" / "60-zoe-deroot").read_text()):
        assert (P0 / "libexec" / name).is_file(), name


# ── routing ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_route_helper_when_installed_else_legacy(privdir):
    legacy = ["sudo", "-n", "sh", "-c", "x"]
    assert zoe_priv.route("zoe-mem-compact", ("3",), legacy) == (zoe_priv.LEGACY, legacy)
    assert zoe_priv.route("zoe-ha-config-export", ("storage",), None) == (zoe_priv.NONE, None)
    p = privdir("zoe-mem-compact")
    assert zoe_priv.route("zoe-mem-compact", ("3",), legacy) == (zoe_priv.HELPER, ["sudo", "-n", p, "3"])


def test_a_symlink_or_non_executable_is_not_an_install(privdir, tmp_path):
    target = privdir("zoe-nvmap-read")
    os.rename(target, tmp_path / "real")
    os.symlink(tmp_path / "real", target)
    assert not zoe_priv.installed("zoe-nvmap-read")
    os.unlink(target)
    Path(target).write_text("x")
    Path(target).chmod(0o644)
    assert not zoe_priv.installed("zoe-nvmap-read")


@pytest.mark.parametrize("name,args", [("zoe-mem-compact", ("2",)), ("zoe-mem-compact", ("3", "x")),
                                       ("zoe-nvmap-read", ("/etc/shadow",)), ("zoe-ha-config-export", ()),
                                       ("zoe-restore-drill-pg", ("psql", "postgres")),
                                       ("zoe-compose-deploy", ("zoe-ui", "--build")),
                                       ("zoe-compose-deploy", ("logs", "homeassistant")), ("zoe-evil", ("x",))])
def test_helper_argv_refuses_outside_the_allowlist(name, args):
    with pytest.raises(ValueError):
        zoe_priv.helper_argv(name, *args)


def test_route_lines_carry_fixed_words_only():
    assert zoe_priv.route_line("zoe-mem-compact", "helper") == "priv-route zoe-mem-compact=helper"
    with pytest.raises(ValueError):
        zoe_priv.route_line("zoe-mem-compact", "/etc/shadow")


def test_can_run_asks_the_policy_for_the_helper_and_falls_back_to_sudo_true(privdir):
    seen = []
    assert zoe_priv.can_run(lambda a: seen.append(a) or 0, "zoe-mem-compact", ("3",))
    p = privdir("zoe-mem-compact")
    assert not zoe_priv.can_run(lambda a: seen.append(a) or 1, "zoe-mem-compact", ("3",))
    assert seen == [["sudo", "-n", "true"], ["sudo", "-n", "-l", p, "3"]]


# ── the deploy helper's own validation ────────────────────────────────────────────────────────────────────────

def run_helper(argv, stdin=SHA + "\n"):
    return subprocess.run([sys.executable, "-I", str(DEPLOY_HELPER), *argv], input=stdin, capture_output=True,
                          text=True, timeout=30)


@pytest.mark.parametrize("argv", [[], ["zoe-auth", "--build", "x"], ["zoe-ui", "--build"], ["logs"],
                                  ["logs", "homeassistant"], ["modules", "--build"], ["zoe-auth;id"], ["../zoe-auth"],
                                  ["ZOE-AUTH"], ["zoe-auth", SHA]])
def test_deploy_helper_refuses_argv(argv):
    r = run_helper(argv)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.parametrize("stdin", ["", "abc\n", SHA.upper() + "\n", SHA + " x\n", SHA + "0\n", "main\n",
                                   "$(id)\n", SHA + "\n" + SHA + "\n", "\xff" * 40])
def test_deploy_helper_refuses_stdin(stdin):
    r = run_helper(["zoe-auth"], stdin)
    assert r.returncode == 2 and "usage" in r.stderr


def test_negative_control_a_valid_request_gets_past_validation():
    r = run_helper(["zoe-auth"])          # as a non-root user it then fails on the root-owned state dir, never with 2
    assert r.returncode != 2 and "usage" not in r.stderr


@pytest.fixture(scope="module")
def dh():
    return _load("zoe_compose_deploy_under_test", DEPLOY_HELPER)


def test_unswappable_needs_root_owned_ancestors_and_no_symlink(dh):
    assert dh.unswappable("/usr/share") is None
    assert "group/world-writable" in dh.unswappable("/tmp/anything")
    assert dh.unswappable("/usr/../etc") is not None and dh.unswappable("relative") is not None
    if os.path.islink("/bin"):
        assert "symlink" in dh.unswappable("/bin/sh")


@pytest.mark.parametrize("vol,ok", [
    ({"type": "bind", "source": "/usr/share", "target": "/x", "read_only": True}, True),
    ({"type": "bind", "source": "/usr/share", "target": "/x"}, False),                  # rw into the reviewed tree
    ({"type": "bind", "source": "/home/zoe/assistant/data", "target": "/x"}, False),    # outside tree and /srv/zoe
    ({"type": "bind", "source": "/srv/zoe/never-made-here", "target": "/x"}, False),    # cannot be proven
    ({"type": "volume", "source": "named", "target": "/x"}, True),
])
def test_check_service_binds(dh, vol, ok):
    cfg = {"services": {"zoe-auth": {"volumes": [vol]}}}
    if ok:
        assert dh.check_service(cfg, "zoe-auth", "/usr")
    else:
        with pytest.raises(SystemExit) as e:
            dh.check_service(cfg, "zoe-auth", "/usr")
        assert e.value.code == 6


def test_check_service_refuses_privileged(dh):
    with pytest.raises(SystemExit) as e:
        dh.check_service({"services": {"zoe-auth": {"privileged": True}}}, "zoe-auth", "/usr")
    assert e.value.code == 6


def test_deploy_helper_trusts_only_fixed_paths(dh):
    assert dh.ORIGIN.startswith("https://github.com/") and dh.PROJECT == "assistant"
    assert dh.ENV_FILE == "/etc/zoe/compose.env" and dh.GIT == "/usr/bin/git" and dh.DOCKER == "/usr/bin/docker"
    assert "os.environ" not in DEPLOY_HELPER.read_text()          # sudo strips it anyway; nothing may be read from it


# ── the rewired callers ───────────────────────────────────────────────────────────────────────────────────────

class FakeHost:
    def __init__(self, rc=0, out=""):
        self.calls, self.rc, self.out = [], rc, out

    def run(self, argv, timeout=60.0, mutating=True, env=None):
        self.calls.append(list(argv))
        return types.SimpleNamespace(rc=self.rc, out=self.out)


@pytest.fixture
def bk():
    from zmb import bakeoff
    return bakeoff


def window(cls, host, logs, drop=True):
    w = cls.__new__(cls)
    w.cfg = types.SimpleNamespace(drop_caches=drop, contig_order=9)
    w.host, w.log, w.dry = host, logs.append, False
    w.log_frag = lambda where: {}
    return w


@pytest.mark.parametrize("installed", [False, True])
def test_bakeoff_and_night_window_compaction_route(bk, privdir, installed):
    p = privdir("zoe-mem-compact") if installed else None
    host, logs = FakeHost(), []
    w = window(bk.Window, host, logs)
    assert w.compact_memory("test") is True
    probe, call = host.calls[0], host.calls[1]
    if installed:
        assert probe == ["sudo", "-n", "-l", p, "3"] and call == ["sudo", "-n", p, "3"]
        assert "priv-route zoe-mem-compact=helper" in logs
    else:
        assert probe == ["sudo", "-n", "true"] and call == ["sudo", "-n", "sh", "-c", bk.COMPACT_DROP]
        assert "priv-route zoe-mem-compact=legacy" in logs
    w2 = window(bk.Window, FakeHost(), [], drop=False)
    w2.compact_memory("x")
    assert w2.host.calls[1][-1] == ("0" if installed else bk.COMPACT_ONLY)


def test_negative_control_without_the_probe_change_a_post_p0_3_box_could_not_compact(bk, privdir):
    """After P0-3 `sudo -n true` fails and the helper is allowed: the window must still be able to compact."""
    privdir("zoe-mem-compact")

    class PostP03(FakeHost):
        def run(self, argv, **kw):
            self.calls.append(list(argv))
            return types.SimpleNamespace(rc=1 if argv == ["sudo", "-n", "true"] else 0, out="")
    w = window(bk.Window, PostP03(), [])
    assert w.sudo_ok() is True
    assert ["sudo", "-n", "true"] not in w.host.calls


@pytest.mark.parametrize("installed", [False, True])
def test_night_window_nvmap_reads_route(privdir, installed):
    import night_window as nw
    p = privdir("zoe-nvmap-read") if installed else None
    host, logs = FakeHost(out="Max allocatable IOVMM memory: 2339717120 bytes\n"), []
    w = window(nw.NightWindow, host, logs)
    w._sudo = True
    assert w.nvmap_free_mib() == pytest.approx(2231.3, abs=0.1)
    w.nvmap_client_mib(123)
    if installed:
        assert host.calls == [["sudo", "-n", p, "free"], ["sudo", "-n", p, "clients"]]
    else:
        assert host.calls == [["sudo", "-n", "cat", nw.NVMAP_FREE], ["sudo", "-n", "cat", nw.NVMAP_CLIENTS]]
    assert logs == [f"priv-route zoe-nvmap-read={'helper' if installed else 'legacy'}"]


@pytest.mark.parametrize("installed", [False, True])
def test_engine_bench_routes(privdir, monkeypatch, installed):
    eb = _load("engine_bench_under_test", REPO / "scripts" / "perf" / "engine_bench.py")
    p = privdir("zoe-mem-compact") if installed else None
    seen = []
    monkeypatch.setattr(eb.subprocess, "run", lambda argv, **kw: seen.append(argv))
    eb.drop_caches()
    eb.drop_caches(3)
    assert seen[0] == (["sudo", "-n", p, "1"] if installed else ["sudo", "-n", "sh", "-c", "echo 1 > /proc/sys/vm/drop_caches"])
    assert seen[1] == ["sudo", "-n", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"]


FAKE_SUDO = """#!/bin/sh
echo "$*" >> "$FAKE_SUDO_LOG"
exit 0
"""


def shell_copy(src: Path, tmp: Path, const: str, new: str) -> Path:
    """The script with ONE constant line re-pointed at a temp path (asserted to match exactly once)."""
    text = src.read_text()
    assert text.count(const) == 1, const
    dst = tmp / src.name
    dst.write_text(text.replace(const, new))
    dst.chmod(0o755)
    return dst


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("installed", [False, True])
def test_brain_cold_start_routes_probe_and_drop(tmp_path, installed):
    helper = tmp_path / "zoe-mem-compact"
    if installed:
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o755)
    script = shell_copy(REPO / "scripts" / "maintenance" / "brain_cold_start.sh", tmp_path,
                        "MEM_COMPACT=/usr/local/libexec/zoe-priv/zoe-mem-compact", f"MEM_COMPACT={helper}")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "sudo").write_text(FAKE_SUDO)
    (bindir / "sudo").chmod(0o755)
    log = tmp_path / "sudo.log"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_SUDO_LOG": str(log),
           "BRAIN_COLD_START_HEALTH_URL": "http://127.0.0.1:1/health", "BRAIN_COLD_START_STATE_DIR": "/proc/nope/x",
           "BRAIN_COLD_START_NO_SYSTEMD_RUN": "1"}
    r = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    lines = log.read_text().splitlines()
    if installed:
        assert lines[:2] == [f"-n -l {helper} 1", f"-n {helper} 1"] and "zoe-mem-compact=helper" in r.stdout
    else:
        assert lines[:2] == ["-n true", "-n sh -c echo 1 > /proc/sys/vm/drop_caches"] and "zoe-mem-compact=legacy" in r.stdout


def test_mtp_window_routes_its_drop():
    text = (REPO / "scripts" / "maintenance" / "mtp_vocab_trim_window.sh").read_text()
    assert 'sudo -n "$MEM_COMPACT" 1' in text and "priv-route zoe-mem-compact=$ROUTE" in text
    assert "sudo -n sh -c 'sync; echo 1 > /proc/sys/vm/drop_caches'" in text      # the legacy path is kept


@pytest.mark.parametrize("state", ["absent", "installed", "override"])
def test_ha_export_and_drill_routes(tmp_path, monkeypatch, state):
    sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
    hb = _load("zoe_host_snapshot", REPO / "scripts" / "maintenance" / "zoe_host_snapshot.py")
    drill = _load("zoe_restore_drill", REPO / "scripts" / "maintenance" / "zoe_restore_drill.py")
    for var in ("ZOE_HA_EXPORT_CMD", "ZOE_DRILL_HELPER"):
        monkeypatch.delenv(var, raising=False)
    ha, dp = tmp_path / "zoe-ha-config-export", tmp_path / "zoe-restore-drill-pg"
    monkeypatch.setattr(hb, "HELPER_INSTALLED", ha)
    monkeypatch.setattr(drill, "HELPER_INSTALLED", dp)
    if state == "installed":
        ha.write_text("x")
        dp.write_text("x")
    if state == "override":
        monkeypatch.setenv("ZOE_HA_EXPORT_CMD", "stand-in")
        monkeypatch.setenv("ZOE_DRILL_HELPER", "stand-in")
    route, cmd = hb.ha_route()
    sb = drill.Sandbox.default()
    expect = {"absent": ("none", None, "legacy", ["sh", str(drill.HELPER_REPO)]),
              "installed": ("helper", ["sudo", "-n", str(ha), "storage"], "helper", ["sudo", "-n", str(dp)]),
              "override": ("override", ["stand-in", "storage"], "override", ["stand-in"])}[state]
    assert (route, cmd, sb.route, sb.base) == expect


# ── the deploy seam ───────────────────────────────────────────────────────────────────────────────────────────

FAKE_HELPER = """#!/bin/sh
{ echo "ARGS $*"; [ "$1" = logs ] || echo "STDIN $(cat)"; } >> "$SEAM_LOG"
exit "${HELPER_RC:-0}"
"""
FAKE_SEAM_SUDO = """#!/bin/sh
[ "$1" = -n ] && shift
exec "$@"
"""
FAKE_DOCKER = """#!/bin/sh
echo "DOCKER $*" >> "$SEAM_LOG"
"""


@pytest.fixture
def seam(tmp_path):
    co = tmp_path / "co"
    (co / "scripts" / "deploy").mkdir(parents=True)
    helper = tmp_path / "zoe-compose-deploy"
    script = shell_copy(REPO / "scripts" / "deploy" / "compose_deploy.sh", co / "scripts" / "deploy",
                        "HELPER=/usr/local/libexec/zoe-priv/zoe-compose-deploy", f"HELPER={helper}")
    git = ["git", "-C", str(co), "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run(git[:3] + ["init", "-q"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sha = subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("sudo", FAKE_SEAM_SUDO), ("docker", FAKE_DOCKER)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    log = tmp_path / "seam.log"
    log.write_text("")

    def go(*args, helper_rc=None, present=True, extra_env=None):
        if present:
            helper.write_text(FAKE_HELPER)
            helper.chmod(0o755)
        elif helper.exists():
            helper.unlink()
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "SEAM_LOG": str(log),
               "ZOE_DEPLOY_STATE_DIR": str(tmp_path / "state"), **(extra_env or {})}
        if helper_rc is not None:
            env["HELPER_RC"] = str(helper_rc)
        log.write_text("")
        r = subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True, timeout=60)
        return r, log.read_text().splitlines()
    go.sha = sha
    return go


def test_seam_uses_the_helper_with_the_sha_on_stdin(seam):
    r, lines = seam("zoe-auth", "--build")
    assert r.returncode == 0 and "=helper" in r.stdout and lines == ["ARGS zoe-auth --build", f"STDIN {seam.sha}"]


def test_seam_logs_verb_has_no_stdin(seam):
    r, lines = seam("logs", "zoe-auth")
    assert r.returncode == 0 and lines == ["ARGS logs zoe-auth"]


def test_seam_falls_back_only_on_not_ready(seam):
    r, lines = seam("zoe-auth", helper_rc=6)
    assert r.returncode == 0 and "=legacy" in r.stdout and lines[-1] == "DOCKER compose up -d --no-deps zoe-auth"
    r, lines = seam("zoe-auth", helper_rc=3)                     # not on main: the answer, never a fallback
    assert r.returncode == 3 and not any(x.startswith("DOCKER") for x in lines)


def test_seam_legacy_is_todays_deploy_commands(seam):
    r, lines = seam("zoe-ui", present=False)
    docker = [x for x in lines if x.startswith("DOCKER")]
    assert r.returncode == 0 and "=legacy" in r.stdout
    assert docker[0].startswith("DOCKER run --rm --add-host zoe-auth:127.0.0.1") and docker[0].endswith("nginx -t")
    assert docker[1:] == ["DOCKER compose up -d --no-deps zoe-ui", "DOCKER restart zoe-ui"]


@pytest.mark.parametrize("args", [[], ["zoe-ui", "--build"], ["logs", "homeassistant"], ["modules", "x"],
                                  ["zoe-auth", "--build", "x"]])
def test_seam_refuses_other_verbs(seam, args):
    r, lines = seam(*args)
    assert r.returncode == 2 and lines == []


# ── Docker callers stay inside the proxy allowlist ────────────────────────────────────────────────────────────

#: docker subcommand -> the API calls the CLI makes (on top of the version ping), checked against the proxy rules
SUBCOMMAND_API = {
    "inspect": lambda c: [("GET", f"/v1.47/containers/{c}/json")],
    "start": lambda c: [("POST", f"/v1.47/containers/{c}/start")],
    "stop": lambda c: [("POST", f"/v1.47/containers/{c}/stop")],
    "ps": lambda c: [("GET", "/v1.47/containers/json")],
    "version": lambda c: [("GET", "/v1.47/version")],
}
#: caller -> (its docker subcommands, the container they target). zoe-data's are docker CLI calls with `docker`
#: as argv[0] and inherit DOCKER_HOST from the user manager (environment.d), so no code change routes them.
DOCKER_CALLERS = {
    "services/zoe-data/ma_ondemand.py": ({"inspect", "start"}, "zoe-music-assistant"),
    "services/zoe-data/routers/voice_livekit.py": ({"inspect", "start", "stop"}, "livekit"),
    "scripts/maintenance/ma_idle_reap.py": ({"inspect", "stop"}, "zoe-music-assistant"),
    "scripts/maintenance/postgres-nightly-backup.sh": ({"inspect"}, "zoe-database"),
    "scripts/maintenance/pg_dump_manifest.py": (set(), "zoe-database"),
    "scripts/maintenance/zoe_offbox.py": (set(), "zoe-database"),
    "scripts/maintenance/multica_runtime_heartbeat.py": (set(), "zoe-database"),
    "scripts/maintenance/zoe_health.py": ({"ps"}, "zoe-database"),
    "scripts/maintenance/platform_health_check.sh": ({"version", "ps"}, "zoe-database"),
    "scripts/maintenance/zoe_restore_drill.py": ({"inspect"}, "zoe-database"),
}
_PY_SUB = re.compile(r"""(?:["']docker["']\s*,\s*["']|_docker(?:_cmd)?\(\s*["'])([a-z]+)""")
_SH_SUB = re.compile(r"""(?:^|[;&|(!]|\$\(|\bthen|\bif|\btimeout\s+\S+)\s*docker\s+(?:-[a-z]\s+\S+\s+)?([a-z]+)""", re.M)


def docker_subcommands_text(text: str, shell: bool) -> "set[str]":
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    return set((_SH_SUB if shell else _PY_SUB).findall(code))


def docker_subcommands(path: Path) -> "set[str]":
    return docker_subcommands_text(path.read_text(), path.suffix == ".sh")


@pytest.mark.parametrize("caller", sorted(DOCKER_CALLERS))
def test_docker_caller_uses_only_proxy_allowed_calls(caller):
    subs, container = DOCKER_CALLERS[caller]
    found = docker_subcommands(REPO / caller)
    if "exec" in found:      # the proxy refuses exec: its only allowed home is the legacy branch of a zoe-db-exec route
        assert "zoe-db-exec" in (REPO / caller).read_text(), f"{caller}: docker exec without the zoe-db-exec route"
        found = found - {"exec"}
    assert found <= set(SUBCOMMAND_API), f"{caller}: docker {sorted(found - set(SUBCOMMAND_API))} is not proxied"
    rules = proxy_rules()
    for sub in subs:
        for method, path in [("HEAD", "/_ping")] + SUBCOMMAND_API[sub](container):
            assert any(p.fullmatch(path) for p in rules.get(method, [])), (caller, method, path)


def test_negative_control_the_old_health_check_would_be_refused():
    old = 'docker info >/dev/null\ndocker inspect --format x "$name"\n'
    rules = proxy_rules()
    assert {"info", "inspect"} <= docker_subcommands_text(old, shell=True)
    assert not any(p.fullmatch("/v1.47/info") for p in rules.get("GET", []))
    assert not any(p.fullmatch("/v1.47/containers/homeassistant/json") for p in rules.get("GET", []))


def test_platform_health_check_needs_no_refused_call():
    text = (REPO / "scripts" / "maintenance" / "platform_health_check.sh").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "docker info" not in code and "docker inspect" not in code


def test_docker_host_switch_is_an_example_not_a_default():
    conf = (P0 / "environment.d" / "60-zoe-docker-proxy.conf").read_text()
    runner = (P0 / "examples" / "runner.env.example").read_text()
    for t in (conf, runner):
        assert [ln for ln in t.splitlines() if ln and not ln.startswith("#")] == \
            ["DOCKER_HOST=unix:///run/zoe-docker-proxy/docker.sock"]
    for caller in DOCKER_CALLERS:          # nobody hard-codes either socket: the CLI reads DOCKER_HOST itself
        code = (REPO / caller).read_text()
        assert "docker.sock" not in code and "DOCKER_HOST" not in code, caller


# ── zoe-database work goes through zoe-db-exec (the proxy refuses exec) ──────────────────────────────────────

@pytest.mark.parametrize("installed", [False, True])
def test_db_route_helper_only_for_the_fixed_container(privdir, installed):
    p = privdir("zoe-db-exec") if installed else None
    legacy = ["docker", "exec", "zoe-database", "pg_isready"]
    assert zoe_priv.db_route("zoe-database", ("isready",), legacy) == \
        (("helper", ["sudo", "-n", p, "isready"]) if installed else ("legacy", legacy))
    assert zoe_priv.db_route("other-db", ("isready",), legacy) == ("legacy", legacy)     # the helper is fixed to one


@pytest.mark.parametrize("installed", [False, True])
def test_health_probe_routes_pg_isready(privdir, installed):
    sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
    health = _load("zoe_health", REPO / "scripts" / "maintenance" / "zoe_health.py")
    p = privdir("zoe-db-exec") if installed else None
    seen = []

    def run(argv, timeout=5.0):
        seen.append(argv)
        return 0, ""
    health.probe(http=lambda port: (True, "x"), run=run, night_open=False)
    assert (["sudo", "-n", p, "isready"] if installed else ["docker", "exec", "zoe-database", "pg_isready", "-U", "zoe", "-q"]) in seen


@pytest.mark.parametrize("installed", [False, True])
def test_manifest_routes_its_reads(privdir, installed):
    sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
    man = _load("pg_dump_manifest", REPO / "scripts" / "maintenance" / "pg_dump_manifest.py")
    p = privdir("zoe-db-exec") if installed else None
    assert man._db("zoe-database", ("restore-list",), ["docker", "x"]) == (["sudo", "-n", p, "restore-list"] if installed else ["docker", "x"])
    assert man._db("zoe-database", None, ["docker", "y"]) == ["docker", "y"]        # a db the helper does not take


@pytest.mark.parametrize("installed", [False, True])
def test_heartbeat_routes_and_moves_variables_into_validated_set_lines(privdir, monkeypatch, installed):
    sys.path.insert(0, str(REPO / "scripts" / "maintenance"))
    hb = _load("multica_runtime_heartbeat", REPO / "scripts" / "maintenance" / "multica_runtime_heartbeat.py")
    p = privdir("zoe-db-exec") if installed else None
    monkeypatch.setattr(hb, "WORKSPACE_ID", "0123abcd-0123-4567-89ab-0123456789ab")
    seen = []
    monkeypatch.setattr(hb.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["input"])) or
                        types.SimpleNamespace(returncode=0, stdout="UPDATE 1", stderr=""))
    hb._update_runtime("zoe", True)
    cmd, sql = seen[0]
    if installed:
        assert cmd == ["sudo", "-n", p, "psql", "multica"] and sql.startswith("\\set ws '0123abcd-")
    else:
        assert cmd[:4] == ["docker", "exec", "-i", hb.DB_CONTAINER] and not sql.startswith("\\set")
    monkeypatch.setattr(hb, "WORKSPACE_ID", "x'; DROP TABLE t; --")                    # negative control: never inlined
    seen.clear()
    hb._update_runtime("zoe", True)
    assert seen[0][0][:2] == ["docker", "exec"] and "DROP" not in seen[0][1]


def test_backup_and_health_shell_callers_route_db_exec():
    backup = (REPO / "scripts" / "maintenance" / "postgres-nightly-backup.sh").read_text()
    assert 'sudo -n "$DB_EXEC" dump "$1"' in backup and "printf '%s\\n' $BIOMETRIC_TABLES" in backup
    assert "priv-route zoe-db-exec=$ROUTE" in backup
    phc = (REPO / "scripts" / "maintenance" / "platform_health_check.sh").read_text()
    assert 'sudo -n "$DB_EXEC" isready' in phc
