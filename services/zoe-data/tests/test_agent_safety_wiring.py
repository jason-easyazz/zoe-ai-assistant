"""Wiring / behavioral tests proving the agent_safety guards are actually
applied at the call sites flagged by the 2026-06-28 security audit:

* ``zoe_agent._bash`` executes via argv (no shell) — injection cannot run.
* ``research_evidence`` page fetches are SSRF-gated.
* ``routers.system`` panel proxies are SSRF-gated.

The zoe_agent / routers.system imports pull heavier optional deps, so those
sections ``importorskip`` cleanly in the slim CI environment; the core policy is
already proven hermetically in test_agent_safety.py. No external network is used.
"""

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.ci_safe


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── zoe_agent._bash: argv exec neutralises injection ──────────────────────────

def test_bash_runs_legit_and_blocks_injection(tmp_path):
    zoe_agent = pytest.importorskip("zoe_agent")

    # (a) legit command still works
    out = _run(zoe_agent._bash("echo hello-zoe"))
    assert "hello-zoe" in out

    out = _run(zoe_agent._bash('python3 -c "print(6*7)"'))
    assert "42" in out

    # (b) shell-injection payload must NOT execute its second command.
    marker = tmp_path / f"pwned-{uuid.uuid4().hex}"
    assert not marker.exists()

    res = _run(zoe_agent._bash(f"echo ok; touch {marker}"))
    assert "blocked" in res.lower()
    assert not marker.exists(), "injection via ';' created the marker file"

    res = _run(zoe_agent._bash(f"echo ok && touch {marker}"))
    assert "blocked" in res.lower()
    assert not marker.exists(), "injection via '&&' created the marker file"

    # (c) chaining appended after a python3 -c payload is neutralised by argv
    # exec even though the code arg itself is exempt from the metachar scan.
    res = _run(zoe_agent._bash(f'python3 -c "print(1)" ; touch {marker}'))
    assert not marker.exists(), "injection after python3 -c created the marker file"

    # (d) non-allowlisted command rejected
    res = _run(zoe_agent._bash("curl http://evil.example"))
    assert "blocked" in res.lower()


# ── research_evidence: SSRF gate on result-page fetches ───────────────────────

def test_research_evidence_blocks_internal_fetch():
    research_evidence = pytest.importorskip("research_evidence")
    # Internal/metadata targets resolve to "" (SSRFBlocked caught) without any
    # network call.
    assert research_evidence._fetch_page_price(
        "http://169.254.169.254/latest/meta-data/", 1.0
    ) == ""
    assert research_evidence._fetch_page_price("http://127.0.0.1:6379/", 1.0) == ""


# ── routers.system: panel proxy SSRF gate ─────────────────────────────────────

def test_proxy_reload_skips_blocked_host(monkeypatch):
    pytest.importorskip("fastapi")
    system = pytest.importorskip("routers.system")

    posted_urls = []

    class _FakeResp:
        status_code = 200

        def json(self):
            return {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *a, **k):
            posted_urls.append(url)
            return _FakeResp()

    monkeypatch.setattr(system.httpx, "AsyncClient", _FakeClient)

    # Blocked host (cloud metadata) → no outbound POST attempted.
    _run(system._proxy_reload_to_pi("169.254.169.254"))
    assert posted_urls == []

    # Loopback also blocked by default.
    _run(system._proxy_reload_to_pi("127.0.0.1"))
    assert posted_urls == []

    # Legit default LAN panel host → POST is made to the panel agent.
    default_host = os.environ.get("ZOE_PI_HOST", "192.168.1.61")
    _run(system._proxy_reload_to_pi(default_host))
    assert len(posted_urls) == 1
    assert default_host in posted_urls[0]
    assert posted_urls[0].endswith("/reload")
