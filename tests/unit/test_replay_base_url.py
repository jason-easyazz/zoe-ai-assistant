"""A LOCAL voice replay never targets ZOE_BASE_URL and never resolves a name unbounded.

From 2026-09-30 the nightly probe hung to its 30 min timeout: ``replay_samples.py
--stt remote`` took ``ZOE_BASE_URL=http://zoe.local`` (the PUBLIC URL; another LAN
device owns that mDNS name) and blocked in ``getaddrinfo``; the probe's skip
diagnosis then resolved the same name (runbook §20). The replay helpers are
ast-extracted (importing replay_samples chdirs and imports the flue client).
``no_names`` fails the test on any hostname lookup; ``test_instrument_fires`` is
its negative control.
"""
from __future__ import annotations

import ast
import importlib.util
import ipaddress
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
LOOP, PUBLIC = "http://127.0.0.1:8000", "http://zoe.local"

_tree = ast.parse((REPO / "services/zoe-data/tests/replay_samples.py").read_text())
_fns = [n for n in _tree.body if isinstance(n, ast.FunctionDef)
        and n.name in ("_replay_base_url", "_check_resolvable")]
RS: dict = {"os": os, "socket": socket, "threading": threading, "ipaddress": ipaddress}
exec(compile(ast.Module(body=_fns, type_ignores=[]), "replay_samples", "exec"), RS)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("vrp_base_url", "scripts/maintenance/voice_regression_probe.py")


@pytest.fixture
def no_names(monkeypatch):
    """Numeric hosts are refused (no real I/O); a hostname FAILS the test —
    AssertionError is not OSError, so _port_open cannot swallow it."""
    def fake(host, *a, **k):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise AssertionError(f"getaddrinfo called on hostname {host!r}")
        raise ConnectionRefusedError("fake")
    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_BASE_URL", PUBLIC)
    monkeypatch.setenv("ZOE_DEVICE_TOKEN", "x")
    monkeypatch.delenv("ZOE_REPLAY_BASE_URL", raising=False)
    (tmp_path / ".env").write_text(f"ZOE_BASE_URL={PUBLIC}\n")
    return tmp_path


def test_default_is_loopback_despite_public_base(env):
    assert RS["_replay_base_url"](None) == LOOP


def test_override_then_explicit_flag_wins(env, monkeypatch):
    monkeypatch.setenv("ZOE_REPLAY_BASE_URL", "http://127.0.0.2:8000")
    assert RS["_replay_base_url"](None) == "http://127.0.0.2:8000"
    assert RS["_replay_base_url"]("http://10.0.0.5:8000") == "http://10.0.0.5:8000"


def test_numeric_hosts_never_resolve(no_names):
    RS["_check_resolvable"](LOOP)
    RS["_check_resolvable"]("http://[::1]:8000")


def test_hanging_lookup_fails_loudly_within_bound(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: release.wait(30))
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match=r"'zoe\.local' \(no answer within 0\.2s\)"):
        RS["_check_resolvable"](PUBLIC, timeout=0.2)
    assert time.monotonic() - t0 < 5
    release.set()


def test_failed_and_answering_lookups(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(RuntimeError, match="Name or service not known"):
        RS["_check_resolvable"](PUBLIC)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    RS["_check_resolvable"]("http://localhost:8000")


def test_instrument_fires(no_names):
    """Negative control: the old diagnosis call shape IS caught."""
    with pytest.raises(AssertionError, match="zoe.local"):
        vrp._port_open("zoe.local", 80)


def test_diagnosis_reports_public_base_without_resolving(env, no_names):
    obs = "; ".join(vrp._diagnose_skip(str(env), "remote"))
    assert "zoe-data 127.0.0.1:8000 REFUSED" in obs
    assert f"ZOE_BASE_URL={PUBLIC} (public URL; not used by the replay)" in obs


def test_hostname_override_is_reported_not_resolved(env, no_names, monkeypatch):
    monkeypatch.setenv("ZOE_REPLAY_BASE_URL", "http://zoe.local:8000")
    assert "zoe-data zoe.local:8000 NOT PROBED" in "; ".join(vrp._diagnose_skip(str(env), "remote"))


def test_probe_and_measure_voice_pass_loopback_explicitly(env, monkeypatch):
    seen = {}

    def stop(cmd, *a, **k):
        seen["cmd"] = cmd
        raise RuntimeError("stop")

    monkeypatch.setattr(vrp.subprocess, "run", stop)
    for stt, expect in (("remote", True), ("inprocess", False)):
        with pytest.raises(RuntimeError, match="stop"):
            vrp.run_measure(1, str(env), "jason", 10, stt)
        c = seen["cmd"]
        assert (c[c.index("--base-url") + 1] == LOOP) if expect else ("--base-url" not in c)
    mv = _load("mv_base_url", "scripts/perf/measure_voice.py")
    (env / "tests").mkdir()
    (env / "tests" / "replay_samples.py").write_text("")
    monkeypatch.setattr(mv, "_run_and_report", stop)
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(sys, "argv", ["mv", "--service-dir", str(env), "--stt", "remote",
                                      "--base-url", LOOP])
    with pytest.raises(RuntimeError, match="stop"):
        mv.main()
    assert seen["cmd"][seen["cmd"].index("--base-url") + 1] == LOOP
