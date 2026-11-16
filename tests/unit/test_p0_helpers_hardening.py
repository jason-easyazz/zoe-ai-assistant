"""P0-1 audit of the root helpers (docs/architecture/p0-deroot.md section 5.2): what a root helper reads must be
nothing a non-root user can write. Pinned here without root and without Docker:
  - zoe-compose-deploy treats STDIN as hostile (one bounded read, a deadline, exact charset, no trailing data);
  - no caller environment reaches its children (an inherited DOCKER_HOST / COMPOSE_FILE / GIT_* is dropped), git
    reads no system/global config or hooks, HOME and the docker config dir are the root-owned state dir;
  - a bind source under a user-writable directory or behind a symlink is refused, so a swap between the check and
    the container start would need root;
  - zoe-db-exec takes fixed verbs only, validates its dump exclusion list strictly, and execs unprivileged.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

LIBEXEC = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "p0-deroot" / "libexec"
DEPLOY = LIBEXEC / "zoe-compose-deploy"
DBX = LIBEXEC / "zoe-db-exec"
SHA = "89abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(scope="module")
def dh():
    name = "zoe_compose_deploy_hardening"
    loader = importlib.machinery.SourceFileLoader(name, str(DEPLOY))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def deploy(stdin: bytes, argv=("zoe-auth",), timeout=30):
    return subprocess.run([sys.executable, "-I", str(DEPLOY), *argv], input=stdin, capture_output=True, timeout=timeout)


@pytest.mark.parametrize("stdin", [SHA.encode() + b"\n\n", SHA.encode() + b"\nx", SHA.encode() + b"\n" + b"0" * 4096,
                                   SHA.encode() + b"\r\n", SHA.encode()[:-1] + b"\x00\n", b"\n" + SHA.encode(),
                                   b" " + SHA.encode(), SHA.encode() + SHA.encode(), b"\xff" * 41, b""])
def test_hostile_stdin_is_refused(stdin):
    r = deploy(stdin)
    assert r.returncode == 2 and b"usage" in r.stderr


def sandboxed_deploy(tmp_path):
    """A copy of the helper whose state dir and env file live in tmp_path, so even a run as root touches no live
    state: it passes the stdin check, then stops at the state-dir check (non-root) or the absent env file (root),
    both before any fetch, export or container call."""
    text = DEPLOY.read_text()
    subs = {'STATE = "/var/lib/zoe-compose-deploy"': f'STATE = "{tmp_path}/state"',
            'ENV_FILE = "/etc/zoe/compose.env"': f'ENV_FILE = "{tmp_path}/absent.env"'}
    for old, new in subs.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    p = tmp_path / "zoe-compose-deploy"
    p.write_text(text)
    return p


@pytest.mark.parametrize("stdin", [SHA.encode(), SHA.encode() + b"\n"])
def test_negative_control_an_exact_sha_passes_the_stdin_check(stdin, tmp_path):
    h = sandboxed_deploy(tmp_path)
    r = subprocess.run([sys.executable, "-I", str(h), "zoe-auth"], input=stdin, capture_output=True, timeout=30)
    assert r.returncode in (5, 6) and b"usage" not in r.stderr, r.stderr   # a later check, never the usage error


def test_stdin_that_never_ends_hits_the_deadline():
    p = subprocess.Popen([sys.executable, "-I", str(DEPLOY), "zoe-auth"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdin.write(b"89ab")          # a partial line, and the pipe held open
    p.stdin.flush()
    t0 = time.monotonic()
    try:
        rc = p.wait(timeout=30)
    finally:
        p.stdin.close()
    assert rc == 2 and time.monotonic() - t0 < 30
    assert b"in time" in p.stderr.read()


def test_no_caller_environment_reaches_the_children(dh, tmp_path, monkeypatch):
    seen = tmp_path / "env.txt"
    fake = tmp_path / "docker"
    fake.write_text(f"#!/bin/sh\nenv > {seen}\n")
    fake.chmod(0o755)
    monkeypatch.setattr(dh, "DOCKER", str(fake))
    for k, v in {"DOCKER_HOST": "tcp://evil:2375", "COMPOSE_FILE": "/tmp/evil.yml", "COMPOSE_PROJECT_NAME": "x",
                 "DOCKER_CONFIG": "/tmp/evil", "GIT_DIR": "/tmp/evil", "XDG_CONFIG_HOME": "/tmp/evil",
                 "LD_PRELOAD": "/tmp/evil.so"}.items():
        monkeypatch.setenv(k, v)
    cwd, mask = os.getcwd(), os.umask(0o022)
    try:
        assert dh.main(["logs", "zoe-auth"]) == 0
    finally:
        os.chdir(cwd)
        os.umask(mask)
    env = dict(line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line)
    for k in ("DOCKER_HOST", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "GIT_DIR", "XDG_CONFIG_HOME", "LD_PRELOAD"):
        assert k not in env, k
    assert env["HOME"] == dh.STATE and env["DOCKER_CONFIG"].startswith(dh.STATE)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_git_fetch_is_pinned(dh):
    text = DEPLOY.read_text()
    assert dh.ORIGIN == "https://github.com/jason-easyazz/zoe-ai-assistant.git"
    for needle in ('"protocol.allow=never"', 'f"protocol.{scheme}.allow=always"', '"core.hooksPath=/dev/null"',
                   'os.chdir("/")', "os.environ"):
        assert (needle in text) is (needle != "os.environ"), needle


def test_a_user_writable_parent_is_refused(dh, tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    why = dh.unswappable(str(d))          # tmp_path is owned by this (non-root) user: anyone running as it could swap
    assert why and ("not root-owned" in why or "writable" in why)


def _fake_fs(monkeypatch, dh, last_mode):
    """unswappable() sees /srv/zoe/x with every parent root-owned 0755 and the last component of `last_mode`, so only
    the last component's own type can cause a refusal (a real tmp_path has a user-owned parent, which is refused
    first and would let a symlink test pass without the symlink check)."""
    def lstat(p):
        mode = last_mode if p == "/srv/zoe/x" else stat.S_IFDIR | 0o755
        return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(dh, "os", types.SimpleNamespace(lstat=lstat, path=os.path))


def test_a_symlinked_source_is_refused(dh, monkeypatch):
    _fake_fs(monkeypatch, dh, stat.S_IFLNK | 0o777)
    assert "symlink" in dh.unswappable("/srv/zoe/x")


def test_negative_control_the_same_path_as_a_directory_passes(dh, monkeypatch):
    _fake_fs(monkeypatch, dh, stat.S_IFDIR | 0o755)
    assert dh.unswappable("/srv/zoe/x") is None
    monkeypatch.undo()
    assert dh.unswappable("/usr/share") is None          # and a real root-owned path passes


def test_a_swap_between_check_and_use_needs_root(dh):
    """The check does not re-open the path later; it proves no unprivileged uid can rename any component (every
    directory above the source is root-owned and not group/world-writable), so a swap needs root. Modelled: each
    shape a non-root swap needs is refused."""
    assert dh.unswappable("/tmp/x") is not None                     # world-writable parent (sticky or not)
    assert dh.unswappable("/usr/share/../share") is not None        # unnormalised
    assert dh.unswappable("relative/x") is not None


def _prune_rig(dh, tmp_path, monkeypatch, ps_rc=0, inspect_rc=0, mounted=()):
    state = tmp_path / "state"
    trees = state / "trees"
    trees.mkdir(parents=True)
    names = [f"{i:040x}" for i in range(1, 7)]           # six exports, oldest first
    for i, n in enumerate(names):
        (trees / n).mkdir()
        os.utime(trees / n, (1000 + i, 1000 + i))
    srcs = [f"{trees}/{names[m]}/services/zoe-ui/nginx.conf" for m in mounted] + ["/srv/zoe/data"]
    (tmp_path / "mounts.txt").write_text("\n".join(srcs) + "\n")
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\n"
                    f'case "$1" in ps) echo c1; exit {ps_rc};; inspect) cat "{tmp_path}/mounts.txt"; exit {inspect_rc};; esac\n')
    fake.chmod(0o755)
    monkeypatch.setattr(dh, "STATE", str(state))
    monkeypatch.setattr(dh, "DOCKER", str(fake))
    return trees, names


def test_prune_keeps_an_old_export_a_container_still_mounts(dh, tmp_path, monkeypatch):
    trees, names = _prune_rig(dh, tmp_path, monkeypatch, mounted=(0,))
    dh.prune_trees(names[-1])
    left = sorted(p.name for p in trees.iterdir())
    assert names[0] in left, "the oldest export is still bind-mounted by a container: it must survive"
    assert names[1] not in left and names[2] not in left, "unreferenced old exports are still pruned (control)"
    assert sorted(names[3:] + [names[0]]) == left


@pytest.mark.parametrize("ps_rc,inspect_rc", [(1, 0), (0, 1)])
def test_prune_keeps_everything_when_mounts_cannot_be_listed(dh, tmp_path, monkeypatch, ps_rc, inspect_rc):
    trees, names = _prune_rig(dh, tmp_path, monkeypatch, ps_rc=ps_rc, inspect_rc=inspect_rc)
    dh.prune_trees(names[-1])
    assert sorted(p.name for p in trees.iterdir()) == names


# ── zoe-db-exec ──────────────────────────────────────────────────────────────────────────────────────────────

def dbx_copy(tmp_path):
    """The helper with its ONE exec line pointed at echo, so a valid request shows the argv it would run."""
    text = DBX.read_text()
    old = 'X="docker exec -i -u postgres $C"'
    assert text.count(old) == 1
    p = tmp_path / "zoe-db-exec"
    p.write_text(text.replace(old, 'X="echo EXEC docker exec -i -u postgres $C"'))
    return p


@pytest.mark.parametrize("argv", [[], ["dump"], ["dump", "postgres"], ["psql", "zoe", "-c"], ["psql", "template1"],
                                  ["isready", "x"], ["exec"], ["sh"], ["restore-list", "-d", "zoe"],
                                  ["dump", "zoe;id"], ["psql-at"], ["dumpall-globals", "--clean"]])
def test_db_exec_refuses_argv(argv):
    r = subprocess.run(["sh", str(DBX), *argv], input="", capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.parametrize("stdin", ["Users\n", "a;b\n", "a b$(id)\n", "../etc\n", "a" * 64 + "\n", "*\n",
                                   "".join(f"t{i}\n" for i in range(65)), "t\n" * 2100, "-n\n"])
def test_db_exec_dump_exclusion_list_is_validated(tmp_path, stdin):
    r = subprocess.run(["sh", str(dbx_copy(tmp_path)), "dump", "zoe"], input=stdin, capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 2 and "EXEC" not in r.stdout


def test_db_exec_valid_requests_exec_unprivileged_in_one_container(tmp_path):
    h = dbx_copy(tmp_path)
    r = subprocess.run(["sh", str(h), "dump", "zoe"], input="learned_voiceprints\nomi_voice_clusters\n",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.split() == [
        "EXEC", "docker", "exec", "-i", "-u", "postgres", "zoe-database", "pg_dump", "-U", "zoe", "-Fc",
        "--exclude-table-data=learned_voiceprints", "--exclude-table-data=omi_voice_clusters", "zoe"]
    r = subprocess.run(["sh", str(h), "psql-at", "multica"], input="SELECT 1", capture_output=True, text=True, timeout=30)
    assert r.stdout.split()[:7] == ["EXEC", "docker", "exec", "-i", "-u", "postgres", "zoe-database"]
    assert "--privileged" not in DBX.read_text() and "-u root" not in DBX.read_text()


def test_db_exec_drops_the_callers_docker_environment():
    text = DBX.read_text()
    assert "unset DOCKER_HOST DOCKER_CONTEXT DOCKER_CONFIG" in text and "\ncd /\n" in text and "set -f" in text
