"""The OFFLINE test tier stays hermetic (owner constraint: "local tests must work offline").

``tools/ci/offline_tier.json`` selects the tests a Zoe box runs with no network; ``tools/ci/offline_guard.py``
refuses a non-loopback socket; ``tools/ci/offline_tier.py`` lists, verifies and measures. This file pins:

* the manifest is sound (its files exist, are ``ci_safe`` and sit in their lane, and it states a budget);
* the guard can say red: synthetic tests that connect or resolve off-box FAIL even when they swallow the
  error, loopback and AF_UNIX stay usable, and ``verify`` exits 1 naming the test; a write under ``HOME`` or a
  started program outside the base set fails ``verify`` too;

The plugin's own pins (the ``--offline-tier`` option, loopback classification, conftest registration) are in
``test_offline_guard.py``.

Synthetic throwaway trees only; the real tier is not re-run here (its tests already run, guarded, in their lanes).
"""
from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
CI_DIR = REPO / "tools" / "ci"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}_under_test", CI_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tier = _load("offline_tier")
guard = _load("offline_guard")

SYNTHETIC = {
    "test_clean.py": """
        import socket, socketserver, threading
        import pytest
        pytestmark = pytest.mark.ci_safe
        def test_loopback_and_unix_are_fine(tmp_path):
            srv = socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler)
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
            socket.create_connection(srv.server_address, 5).close()
            srv.shutdown(); srv.server_close()
            assert socket.getaddrinfo("localhost", 80)
            a, b = socket.socketpair(); a.sendall(b"x"); assert b.recv(1) == b"x"; a.close(); b.close()
        """,
    "test_dials_out.py": """
        import socket
        import pytest
        pytestmark = pytest.mark.ci_safe
        def test_swallows_a_connect():
            try:
                socket.create_connection(("192.0.2.7", 443), 0.2)     # TEST-NET-1: never routable
            except OSError:
                pass
        def test_swallows_a_lookup():
            try:
                socket.getaddrinfo("models.example.invalid", 443)
            except OSError:
                pass
        def test_udp_sendto():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.sendto(b"x", ("192.0.2.7", 53))
            except OSError:
                pass
            finally:
                s.close()
        """,
    "test_leaves_the_sandbox.py": """
        import pathlib, subprocess
        import pytest
        pytestmark = pytest.mark.ci_safe
        def test_writes_home_and_starts_a_tool():
            (pathlib.Path.home() / ".zoe").mkdir(exist_ok=True)
            (pathlib.Path.home() / ".zoe" / "state.json").write_text("{}")
            try:
                subprocess.run(["jq", "--version"], capture_output=True)
            except OSError:
                pass
            subprocess.run(["git", "--version"], capture_output=True, check=True)      # in the base set
        """,
}


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A throwaway 'repo' with one lane, and the tool pointed at it."""
    lane = tmp_path / "lane"
    lane.mkdir()
    for name, body in SYNTHETIC.items():
        (lane / name).write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    ci_safe: x\n", encoding="utf-8")
    monkeypatch.setattr(tier, "REPO", tmp_path)
    monkeypatch.setattr(tier, "LANES", {"lane": {"root": "lane", "pythonpath": [str(CI_DIR)], "args": []}})
    return tmp_path


def _manifest(tree: Path, files: list[str], **extra) -> Path:
    entry = {"s": 0.1, "rss_mb": 50, "tests": 1, "covers": []}
    man = {"schema": 1, "budget_s": 600, "budget_rss_mb": 4096, "measured_s": 1.0, "measured_on": {"machine": "x"},
           "modules": {"total": 0, "covered": 0}, "uncovered": [], "lanes": {"lane": {f: entry for f in files}}, **extra}
    path = tree / "tier.json"
    tier.write_manifest(man, path)
    assert json.loads(path.read_text(encoding="utf-8")) == man          # the hand-rolled writer emits valid JSON
    return path


def _verify(tree: Path, files: list[str], *args: str, **extra) -> tuple[int, dict]:
    out = tree / "result.json"
    rc = tier.main(["verify", "--manifest", str(_manifest(tree, files, **extra)), "--json", str(out), "--no-netns", *args])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def test_a_hermetic_selection_verifies_green(tree, capsys):
    rc, res = _verify(tree, ["lane/test_clean.py"])
    assert rc == 0 and res["problems"] == [], res
    lane = res["lanes"]["lane"]
    assert lane["counts"]["passed"] == 1 and lane["wall_s"] > 0 and lane["peak_rss_mb"] > 0     # time AND memory reported


def test_a_test_that_dials_out_fails_verify_by_name(tree, capsys):
    rc, res = _verify(tree, ["lane/test_clean.py", "lane/test_dials_out.py"])
    assert rc == 1
    text = "\n".join(res["problems"])
    assert "test_swallows_a_connect: connect 192.0.2.7:443" in text
    assert "test_swallows_a_lookup: resolve models.example.invalid" in text
    assert "test_udp_sendto: send-udp 192.0.2.7:53" in text
    assert res["lanes"]["lane"]["counts"] == {"passed": 1, "failed": 3, "error": 0, "skipped": 0, "xfailed": 0}


def test_home_writes_and_external_binaries_fail_verify(tree, capsys):
    rc, res = _verify(tree, ["lane/test_leaves_the_sandbox.py"])
    text = "\n".join(res["problems"])
    assert rc == 1 and "wrote under HOME: ~/.zoe/state.json" in text and "started external binary jq" in text
    assert "git" not in text and res["lanes"]["lane"]["counts"]["passed"] == 1      # the test itself passed: verify caught it


def test_missing_file_and_budget_are_failures(tree, capsys):
    rc, res = _verify(tree, ["lane/test_clean.py", "lane/test_gone.py"])
    assert rc == 1 and "lane/test_gone.py: selected file is missing" in res["problems"]
    rc, res = _verify(tree, ["lane/test_clean.py"], budget_s=0)
    assert rc == 0 and res["over_budget"]                                # reported, not fatal, by default
    rc, res = _verify(tree, ["lane/test_clean.py"], "--enforce-budget", budget_s=0)
    assert rc == 1 and any("budget" in p for p in res["problems"])


def test_committed_manifest_is_sound():
    man = json.loads(tier.MANIFEST.read_text(encoding="utf-8"))
    assert set(man["lanes"]) == set(tier.LANES) and man["budget_s"] > 0 and man["budget_rss_mb"] > 0
    files = [(lane, f) for lane, fs in man["lanes"].items() for f in fs]
    assert files, "an empty tier pins nothing"
    for lane, f in files:
        p = REPO / f
        assert p.is_file(), f"{f}: selected file is gone - re-run `tools/ci/offline_tier.py select`"
        assert f.startswith(tier.LANES[lane]["root"] + "/"), f"{f} is not in lane {lane}"
        assert "ci_safe" in p.read_text(encoding="utf-8"), f"{f} lost its ci_safe marker"
    assert guard.tier_files() == frozenset(f for _, f in files)      # the lanes arm the guard for exactly these
    assert man["modules"]["covered"] + len(man["uncovered"]) == man["modules"]["total"]
    assert sum(len(v["covers"]) for fs in man["lanes"].values() for v in fs.values()) == man["modules"]["covered"]
