"""The SUPERVISED gate: the verdict comes from OUTSIDE the process that runs the engine's code.

Why (README "Supervisor"): the JUnit gate reads a report written by the very pytest process that
imports the engine's code, so that code can forge it. Here the supervisor (this module, in the
runner process, which never imports tree code) owns everything the verdict depends on:

- the NODE LIST and the baseline come from the BASE commit (a pytest run over base code only);
- the candidate tree is MATERIALIZED fresh: the base commit plus exactly the policy-checked files,
  so nothing the engine left unchecked (ignored files, .pyc, caches) exists in it;
- the base test files that hold those nodes are restored from the base commit, and every pinned
  file (each base test file run, every conftest.py / pytest.ini / pyproject.toml / setup.cfg /
  tox.ini / sitecustomize.py / usercustomize.py / *.pth anywhere in the tree) must hash equal to
  the base blob, or the gate refuses;
- each node runs in its OWN sandboxed process, the tree read-only, through `node_runner.py`; a
  node passed only when that process exits 0 AND wrote the token for a per-spawn nonce the
  process got on stdin (never argv or env) to the fd the supervisor passed.

Not closed (stated in the README): code under test shares the runner's interpreter, so a change
written to attack this runner (find the token in memory, rewrite the calling test) still passes.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import gitops
from .detectors import node_statuses
from .safety import Refused
from .sandbox import Sandbox

RUNNER = Path(__file__).with_name("node_runner.py")
RUNNER_SHA = hashlib.sha256(RUNNER.read_bytes()).hexdigest()
PIN_NAMES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
             "sitecustomize.py", "usercustomize.py"}
FAIL_RC = 3


def is_pinned_name(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in PIN_NAMES or name.endswith(".pth")


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def pin_diff(repo: str, base_sha: str, tree: str, test_files=()) -> list:
    """Paths whose pinned content differs from the base commit (added, removed or changed)."""
    want = {}
    for entry in filter(None, gitops.git(repo, "ls-tree", "-r", "-z", base_sha).split("\0")):
        meta, path = entry.split("\t", 1)
        if is_pinned_name(path) or path in test_files:
            want[path] = meta.split()[2]
    got = {}
    for d, dirs, files in os.walk(tree):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            full = os.path.join(d, f)
            rel = os.path.relpath(full, tree)
            if is_pinned_name(rel) or rel in test_files:
                got[rel] = "symlink" if os.path.islink(full) else _blob(Path(full).read_bytes())
    return sorted(p for p in set(want) | set(got) if want.get(p) != got.get(p))


def materialize(cfg, base_sha: str, src: str, files: list, dst: str, restore: list) -> None:
    """A fresh worktree at the base commit plus exactly the checked `files` from `src` (deleted ones
    deleted), then the base copies of the `restore` test files."""
    gitops.add_worktree(cfg.repo, dst, base=base_sha)
    for f in files:
        s, d = Path(src) / f, Path(dst) / f
        if d.is_symlink() or d.exists():
            d.unlink()
        if s.is_symlink():
            d.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(os.readlink(s), d)
        elif s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    if restore:
        gitops.git(dst, "checkout", base_sha, "--", *restore)


def lib_path() -> list:
    """The interpreter's own library dirs: what the node runner may import from (never a tree)."""
    roots = {os.path.realpath(sys.base_prefix), os.path.realpath(sys.prefix)}
    out = []
    for p in sys.path:
        rp = os.path.realpath(p) if p else ""
        if rp and os.path.isdir(rp) and ("site-packages" in rp or "dist-packages" in rp or
                                         any(rp == r or rp.startswith(r + "/lib") for r in roots)):
            out.append(rp)
    return list(dict.fromkeys(out))


def expected_token(nonce: str, node: str) -> bytes:
    return hashlib.sha256(f"{nonce}|pass|{node}".encode()).hexdigest().encode()


def run_node(cfg, tree: str, node: str) -> str:
    """'passed' only when the node's own sandboxed process exits 0 AND wrote the nonce's token;
    otherwise the reason class (timeout, rc=N, no_proof, runner_hash)."""
    rdir, scratch = tempfile.mkdtemp(prefix="selfheal-runner-"), tempfile.mkdtemp(prefix="selfheal-node-")
    r, w = os.pipe()
    try:
        runner = os.path.join(rdir, "node_runner.py")
        shutil.copyfile(RUNNER, runner)
        os.chmod(runner, 0o444)
        if hashlib.sha256(Path(runner).read_bytes()).hexdigest() != RUNNER_SHA:
            return "runner_hash"
        nonce = secrets.token_hex(32)
        spec = json.dumps({"nonce": nonce, "node": node, "fd": w, "root": os.path.realpath(tree),
                           "sys_path": lib_path(), "pyc": os.path.join(scratch, "pyc")})
        rc, out = Sandbox(cfg).run([cfg.python, "-I", "-B", runner], tree, cfg.gate_timeout_s, tree_ro=True,
                                   ro=[rdir], scratch=scratch, stdin=spec, pass_fds=(w,))
        os.close(w)
        w = -1
        got = b""
        while chunk := os.read(r, 4096):
            got += chunk
            if len(got) > 4096:
                break
        if rc is None:
            return out   # timeout | worktree_quota
        if rc != 0:
            return f"rc={rc}"
        return "passed" if hmac.compare_digest(got, expected_token(nonce, node)) else "no_proof"
    finally:
        for fd in (r, w):
            if fd >= 0:
                os.close(fd)
        shutil.rmtree(rdir, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


@contextlib.contextmanager
def lane_lock(cfg):
    """Hold the test-lane lock ONCE for the whole gate (not per node, so no other lane interleaves)."""
    if not cfg.test_lock:
        yield
        return
    with open(cfg.test_lock, "a") as fh:
        end = time.monotonic() + cfg.test_lock_wait_s
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > end:
                    raise Refused("test_lock_timeout", "") from None
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def gate_supervised_pytest(cfg, wt, changed, ticket, base_wt=None):
    """Every node of the relevant BASE test files must pass after the heal, each in its own process,
    and the reproduction must fail at base under the same runner and pass after."""
    from .gates import relevant_tests   # noqa: PLC0415 (gates imports this module)
    repro = ticket.evidence.get("nodeid", "")
    if not base_wt:
        return "fail", "no_base"
    base_tests = relevant_tests(base_wt, changed, ticket)
    if not repro or not base_tests:
        return "fail", "no_relevant_test"
    sb = Sandbox(cfg)
    with lane_lock(cfg):
        # base code only: a JUnit report from it is trusted (the engine's code is not in this tree)
        _rc, out, xml = sb.run_report([cfg.python, "-m", "pytest", "-p", "no:cacheprovider", "-q", *base_tests],
                                      base_wt, cfg.gate_timeout_s)
        if xml is None:
            return "fail", "no_baseline"
        nodes = sorted(node_statuses(xml))
        if node_statuses(xml).get(repro) not in ("failure", "error"):
            return "fail", "test_weakened:repro_not_failing_at_base"
        if run_node(cfg, base_wt, repro) == "passed":
            return "fail", "test_weakened:repro_passes_at_base_supervised"
        tree = str(Path(cfg.worktree_root) / f"heal-{ticket.id}-supervised")
        try:
            materialize(cfg, ticket.base_sha, wt, changed, tree, base_tests)
            pinned = pin_diff(cfg.repo, ticket.base_sha, tree, base_tests)
            if pinned:
                return "fail", f"pinned_changed:files={len(pinned)}"
            verdicts = {n: run_node(cfg, tree, n) for n in nodes}
        finally:
            gitops.remove_worktree(cfg.repo, tree)
    bad = sorted({v for v in verdicts.values() if v != "passed"})
    failed = sum(v != "passed" for v in verdicts.values())
    if failed:
        return "fail", f"supervised:nodes={len(nodes)},failed={failed},repro={verdicts[repro]},why={'+'.join(bad)}"
    return "pass", f"supervised:nodes={len(nodes)},passed={len(nodes)}"


def supervised_repro(cfg, tree: str, ticket) -> tuple[bool, str]:
    """The verify step of a full + local landing: the reproduction node, supervised, in a fresh tree
    whose reproducing files were restored from the base commit."""
    nodeid = ticket.evidence.get("nodeid", "")
    if not nodeid:
        return False, "supervised,node=no_nodeid"
    if pin_diff(cfg.repo, ticket.base_sha, tree, ticket.repro_files):
        return False, "supervised,pinned_changed"
    v = run_node(cfg, tree, nodeid)
    return v == "passed", f"supervised,node={v}"
