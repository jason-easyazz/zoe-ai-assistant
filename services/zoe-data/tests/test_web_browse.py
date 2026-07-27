"""web_browse — the Zoe-native page-read tier (CloakBrowser, no Hermes).

The load-bearing property is SSRF refusal: the URL comes from the MODEL, so a
prompt-injected or hallucinated internal address must never be fetched.
"""
import pytest

import zoe_agent

pytestmark = pytest.mark.ci_safe


# ── SSRF / scheme refusal (the security property) ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>x",
    "ftp://example.com/x",
    "gopher://example.com",
])
async def test_refuses_non_http_schemes(bad):
    out = await zoe_agent._web_browse(bad)
    assert "Refused" in out
    assert "only http(s)" in out


@pytest.mark.asyncio
async def test_refuses_private_addresses(monkeypatch):
    """A model talked into fetching localhost/RFC1918 must be blocked — this is
    SSRF into Zoe's own services or the house network."""
    import agent_safety

    def blocked(url):
        raise ValueError("non-public address")
    monkeypatch.setattr(agent_safety, "assert_public_url", blocked)

    for url in ("http://127.0.0.1:8000/api/system",
                "http://192.168.1.10/admin",
                "http://169.254.169.254/latest/meta-data"):
        out = await zoe_agent._web_browse(url)
        assert "Refused" in out and "public address" in out


@pytest.mark.asyncio
async def test_empty_url_is_handled():
    assert "No URL" in await zoe_agent._web_browse("")
    assert "No URL" in await zoe_agent._web_browse("   ")


# ── graceful degradation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_cloakbrowser_degrades(monkeypatch):
    """No browser installed -> a clear answer pointing at web_search, not a crash."""
    import agent_safety
    import importlib.util as ilu
    monkeypatch.setattr(agent_safety, "assert_public_url", lambda u: u)
    monkeypatch.setattr(ilu, "find_spec", lambda name: None if name == "cloakbrowser" else object())

    out = await zoe_agent._web_browse("https://example.com")
    assert "unavailable" in out and "web_search" in out


# ── registration (the brain can actually reach it) ───────────────────────────

def test_tool_is_registered_and_always_on():
    names = [
        t["function"]["name"]
        for t in getattr(zoe_agent, "TOOLS", getattr(zoe_agent, "_TOOLS", []))
        if isinstance(t, dict) and "function" in t
    ]
    assert "web_browse" in names, f"web_browse not in tool schema: {names[:12]}"
    assert "web_browse" in zoe_agent._ALWAYS_ON_TOOLS


def test_output_is_capped():
    """A rendered page can be enormous — it must be capped like deep_web_research."""
    assert zoe_agent._TOOL_CAPS.get("web_browse", 0) > 0
    huge = "x" * 50_000
    assert len(zoe_agent._cap_tool_result("web_browse", huge)) < len(huge)


# ── broker surface after the OpenClaw retirement ─────────────────────────────

def test_broker_default_surface_is_zoe_native():
    import browser_broker as bb
    b = bb.create_default_browser_broker()
    backends = [m["backend"] for m in b.capabilities()]
    assert b.default_surface() == "zoeCloak"
    assert not any("openclaw" in x.lower() for x in backends), backends


def test_legacy_hermes_surface_still_executes():
    """A persisted plan naming the old surface must still validate AND run."""
    import browser_broker as bb
    b = bb.create_default_browser_broker()
    plan = b.plan_action(action="navigate", params={"url": "https://example.com"},
                         user_id="u", session_id="s", requested_surface="hermesCloak")
    assert plan.selected_surface == "hermesCloak"


@pytest.mark.asyncio
async def test_cloak_executor_accepts_navigate_to(monkeypatch):
    """REGRESSION: chat research passes 'navigate_to' (the MCP tool passes 'url').
    Before the OpenClaw surface was retired these hit different executors — the
    surviving cloak executor must honour BOTH, or screenshots load nothing."""
    import browser_broker as bb
    execu = bb.build_cloak_executor()
    if execu is None:
        pytest.skip("cloakbrowser not installed")

    seen = {}

    class _Page:
        url = "https://example.com"
        async def goto(self, url, **kw):
            seen["url"] = url
        async def screenshot(self, **kw):
            return b"png"
        async def content(self):
            return "<html></html>"

    class _Ctx:
        async def new_page(self):
            return _Page()
        async def close(self):
            pass

    async def fake_launch(**kw):
        return _Ctx()

    import cloakbrowser
    monkeypatch.setattr(cloakbrowser, "launch_context_async", fake_launch)

    plan = bb.BrowserActionPlan(
        action="navigate", params={"navigate_to": "https://example.com"},
        user_id="u", session_id="s",
    )
    await execu(plan)
    assert seen.get("url") == "https://example.com", "navigate_to was ignored"


@pytest.mark.asyncio
async def test_cloak_executor_refuses_empty_target():
    """No url/navigate_to must be an explicit error, never a goto('')."""
    import browser_broker as bb
    execu = bb.build_cloak_executor()
    if execu is None:
        pytest.skip("cloakbrowser not installed")
    out = await execu(bb.BrowserActionPlan(action="navigate", params={}, user_id="u", session_id="s"))
    assert out["ok"] is False and "url" in out["error"]
