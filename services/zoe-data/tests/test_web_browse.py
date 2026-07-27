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


def test_caps_survive_a_blank_env_value():
    """REGRESSION: the caps were int(os.environ.get(..)) — a blank `KEY=` in .env
    made int('') raise at IMPORT, taking zoe_agent (and the whole brain) down.
    env_int maps blank -> default."""
    from typed_env import env_int
    import os
    os.environ["ZOE_TEST_CAP_BLANK"] = ""
    try:
        assert env_int("ZOE_TEST_CAP_BLANK", 6000) == 6000
    finally:
        os.environ.pop("ZOE_TEST_CAP_BLANK", None)


@pytest.mark.asyncio
async def test_browse_installs_the_redirect_guard(monkeypatch):
    """SSRF: assert_public_url only checks the FIRST url. guard_browser_page must
    be installed before goto so a public page that redirects to loopback /
    RFC1918 / cloud-metadata is aborted pre-connect."""
    import agent_safety
    import importlib.util as ilu

    monkeypatch.setattr(agent_safety, "assert_public_url", lambda u: u)
    monkeypatch.setattr(ilu, "find_spec", lambda name: object())  # pretend installed

    guarded: list = []

    async def fake_guard(page):
        guarded.append(page)
    monkeypatch.setattr(zoe_agent, "guard_browser_page", fake_guard)

    order: list = []

    class _Page:
        url = "https://example.com"
        async def goto(self, url, **kw):
            order.append("goto")
        async def content(self):
            return "<html><body>hi</body></html>"

    class _Ctx:
        async def new_page(self):
            return _Page()
        async def close(self):
            pass

    import sys, types
    fake_mod = types.ModuleType("cloakbrowser")
    async def _launch(**kw):
        order.append("launch")
        return _Ctx()
    fake_mod.launch_context_async = _launch
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake_mod)

    out = await zoe_agent._web_browse("https://example.com")
    assert guarded, "guard_browser_page was NOT installed — redirect SSRF is open"
    assert order == ["launch", "goto"]
    assert "hi" in out


# ── broker surface after the OpenClaw retirement ─────────────────────────────

def test_broker_default_surface_is_zoe_native():
    import browser_broker as bb
    b = bb.create_default_browser_broker()
    backends = [m["backend"] for m in b.capabilities()]
    assert b.default_surface() == "zoeCloak"
    assert not any("openclaw" in x.lower() for x in backends), backends


def test_legacy_hermes_surface_is_honoured_when_registered():
    """A persisted plan naming the old surface must still resolve to it.

    Registers a stub executor rather than relying on cloakbrowser being
    installed — CI has no browser package, and plan_action falls back to the
    default when a requested surface has NO executor (that fallback is what made
    an earlier, environment-dependent version of this test fail in CI only).
    """
    import browser_broker as bb
    b = bb.BrowserBroker(default_surface="zoeCloak")

    async def _stub(plan):
        return {"ok": True}
    b.register_executor("hermesCloak", _stub)

    plan = b.plan_action(action="navigate", params={"url": "https://example.com"},
                         user_id="u", session_id="s", requested_surface="hermesCloak")
    assert plan.selected_surface == "hermesCloak"


def test_unavailable_surface_falls_back_to_default():
    """The flip side: a surface with no executor degrades to the default."""
    import browser_broker as bb
    b = bb.BrowserBroker(default_surface="zoeCloak")
    plan = b.plan_action(action="navigate", params={}, user_id="u", session_id="s",
                         requested_surface="touchPanel")
    assert plan.selected_surface == "zoeCloak"


# ── navigate_to REGRESSION — tested via the module-level helper so it runs in
# CI too (the executor itself needs cloakbrowser, which CI does not install, so
# an executor-level test would silently SKIP and protect nothing).

def test_target_url_accepts_both_spellings():
    """chat.py research passes 'navigate_to'; the MCP tool passes 'url'. The
    surviving Zoe-native executor must honour BOTH or screenshots navigate to ''."""
    import browser_broker as bb
    assert bb.target_url({"url": "https://a.test"}) == "https://a.test"
    assert bb.target_url({"navigate_to": "https://b.test"}) == "https://b.test"
    # explicit url wins when both are present
    assert bb.target_url({"url": "https://a.test", "navigate_to": "https://b.test"}) == "https://a.test"


def test_target_url_empty_when_absent():
    """No target must be an explicit empty -> the executor errors instead of goto('')."""
    import browser_broker as bb
    assert bb.target_url({}) == ""
    assert bb.target_url({"url": "   "}) == ""
    assert bb.target_url({"url": None, "navigate_to": None}) == ""
