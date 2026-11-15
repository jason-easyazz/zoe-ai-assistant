"""The per-node runner of the supervised gate (selfheal/supervisor.py). Trusted code, but it runs IN
the sandbox: the supervisor copies this file into a fresh runner-owned dir OUTSIDE the tree, checks
its hash, binds it read-only and starts `python -I -B node_runner.py` once per test node.

Its ONLY input is a JSON spec on stdin (never argv or the environment). The proof of a pass is a
token derived from the spec's nonce, computed BEFORE any tree code is imported and written to the
fd the supervisor passed, followed by os._exit(0) (atexit handlers never run). Anything else (an
exit of any code without the token, a skip, an xfail, an xpass, a deselect, zero or extra nodes, a
collection error) is a failure.

Honest limit (README "Supervisor"): from the moment pytest imports the tree, the code under test
runs in THIS interpreter. It can find the token in memory (frames, gc) or rewrite the test it is
called from. The token defeats a blind forgery, not one that targets this runner.
"""
import hashlib
import json
import os
import sys


def main():
    spec = json.loads(sys.stdin.read())
    node, fd, root = spec["node"], spec["fd"], spec["root"]
    token = hashlib.sha256(f"{spec.pop('nonce')}|pass|{node}".encode()).hexdigest().encode()
    for k in [k for k in os.environ if k.startswith(("PYTEST_", "PYTHON"))]:
        del os.environ[k]   # PYTEST_ADDOPTS / PYTEST_PLUGINS / PYTHONSTARTUP...: never from outside
    sys.dont_write_bytecode = True
    sys.pycache_prefix = spec["pyc"]   # no .pyc from the tree is ever read
    sys.path[:] = spec["sys_path"]     # the interpreter's own library dirs only (no cwd, no user env)
    import pytest   # noqa: PLC0415 - imported only after the path is pinned

    class Probe:
        items, reports = [], []

        def pytest_collection_finish(self, session):
            Probe.items = [i.nodeid for i in session.items]

        def pytest_runtest_logreport(self, report):
            Probe.reports.append((report.nodeid, report.when, report.outcome, hasattr(report, "wasxfail")))

    os.chdir(root)
    sys.path.insert(0, root)   # what `python -m pytest` from the tree root would do
    try:
        rc = pytest.main([node, "-p", "no:cacheprovider", "-q", "--rootdir", root], plugins=[Probe()])
    except BaseException:   # noqa: BLE001
        os._exit(3)
    mine = [r for r in Probe.reports if r[0] == node]
    if (int(rc) == 0 and Probe.items == [node] and [r[1] for r in mine] == ["setup", "call", "teardown"]
            and all(r[2] == "passed" and not r[3] for r in mine)):
        os.write(fd, token)
        os._exit(0)
    os._exit(3)


if __name__ == "__main__":
    main()
