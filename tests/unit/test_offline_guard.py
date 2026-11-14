"""``tools/ci/offline_guard.py``: the pytest plugin that refuses a non-loopback socket (owner constraint: "local
tests must work offline").

Pins: the address classification; ``--offline-tier`` collects only the manifest's files and fails one that dials
out even when it swallows the error, while loopback and AF_UNIX stay usable; without the option only the
manifest's files are guarded (the mode the lane conftests run in); and BOTH lane conftests register the plugin,
which is what arms it in the required ``ci_safe`` lanes. Synthetic throwaway trees only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
CI_DIR = REPO / "tools" / "ci"

_spec = importlib.util.spec_from_file_location("_offline_guard_under_test", CI_DIR / "offline_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

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
}


@pytest.fixture()
def tree(tmp_path):
    """A throwaway 'repo': one lane of synthetic tests, a manifest, and a conftest pointing the plugin at both."""
    (tmp_path / "lane").mkdir()
    for name, body in SYNTHETIC.items():
        (tmp_path / "lane" / name).write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    ci_safe: x\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        import offline_guard, pathlib
        offline_guard.REPO = pathlib.Path({str(tmp_path)!r})
        offline_guard.MANIFEST = offline_guard.REPO / "tier.json"
        """), encoding="utf-8")
    return tmp_path


def _run(tree: Path, tier_file: str, *args: str) -> subprocess.CompletedProcess:
    (tree / "tier.json").write_text(json.dumps({"lanes": {"lane": {f"lane/{tier_file}": {}}}}), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(CI_DIR), "OFFLINE_GUARD": ""}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "offline_guard", "lane", *args],
                          cwd=tree, env=env, capture_output=True, text=True, timeout=120)


def test_offline_tier_option_collects_only_the_manifest(tree):
    """The plain-pytest form (self-heal's ``offline_lane_cmd``): the file that dials out is never collected."""
    r = _run(tree, "test_clean.py", "--offline-tier")
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr


def test_offline_tier_option_fails_a_selected_test_that_dials_out(tree):
    r = _run(tree, "test_dials_out.py", "--offline-tier")
    assert r.returncode == 1 and "3 failed" in r.stdout and "1 passed" not in r.stdout, r.stdout + r.stderr
    for what in ("connect 192.0.2.7:443", "resolve models.example.invalid", "send-udp 192.0.2.7:53"):
        assert f"offline guard: this test reached for the network: {what}" in r.stdout


def test_without_the_option_only_the_manifests_files_are_guarded(tree):
    """The lane mode: a NON-tier test may still dial out (it is not ours to fail); a tier file that does is failed."""
    r = _run(tree, "test_clean.py")
    assert r.returncode == 0 and "4 passed" in r.stdout, r.stdout + r.stderr
    r = _run(tree, "test_dials_out.py")
    assert r.returncode == 1 and "3 failed, 1 passed" in r.stdout, r.stdout + r.stderr


def test_no_manifest_means_no_guard(tree):
    (tree / "conftest.py").write_text((tree / "conftest.py").read_text(encoding="utf-8").replace("tier.json", "absent.json"))
    r = _run(tree, "test_clean.py")
    assert r.returncode == 0 and "4 passed" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("host,loop", [
    ("127.0.0.1", True), ("127.8.9.1", True), ("::1", True), ("localhost", True), ("", True), (None, True),
    ("0.0.0.0", True), ("::ffff:127.0.0.1", True), ("[::1]", True), (b"localhost", True),
    ("192.168.1.10", False), ("10.0.0.1", False), ("8.8.8.8", False), ("huggingface.co", False),
    ("localhost.example.com", False), ("::ffff:8.8.8.8", False), ("fe80::1%eth0", False),
])
def test_loopback_classification(host, loop):
    assert guard.is_loopback(host) is loop


@pytest.mark.parametrize("exe,env,name", [
    ("git", None, "git"), ("/usr/bin/env", None, "env"), ("no-such-program-zz", None, "no-such-program-zz"),
    ("echo hi", None, "echo"), (str(CI_DIR / "offline_guard.py"), None, "repo:tools/ci/offline_guard.py"),
])
def test_started_programs_are_named(exe, env, name):
    assert guard.program_name(exe, env) == name


def test_a_temp_dir_fake_is_a_stub(tmp_path):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert guard.program_name("gh", {"PATH": str(tmp_path)}) == "stub" and guard.program_name(str(fake)) == "stub"


@pytest.mark.parametrize("conftest", ["services/zoe-data/tests/conftest.py", "tests/unit/conftest.py"])
def test_both_lane_conftests_register_the_guard(conftest):
    """Without this registration nothing guards the tier in the required lanes; `-p` is not on their command line."""
    text = (REPO / conftest).read_text(encoding="utf-8")
    assert "ZOE_OFFLINE_GUARD_PLUGIN" in text and "pluginmanager.register" in text


def test_the_guard_is_armed_for_this_lane(pytestconfig):
    """Live proof, in whichever process runs this file: the guard plugin is registered by the lane conftest."""
    assert any(getattr(p, "ZOE_OFFLINE_GUARD_PLUGIN", False) for p in pytestconfig.pluginmanager.get_plugins())
