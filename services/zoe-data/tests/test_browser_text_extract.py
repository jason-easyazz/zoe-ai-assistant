"""Offline proofs for the browser broker's readability-lite text extraction.

Everything here runs against FIXTURE HTML — no browser, no network, no
cloakbrowser import — so the extraction algorithm is pinned independently of
whether a Chromium is installed or reachable.

The load-bearing test is `test_negative_control_*`: it asserts that the
boilerplate really is present in the fixture, so that if `extract_main_text`
ever degrades back to whole-body `inner_text()` the exclusion assertions go RED
rather than passing vacuously against an empty string.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from browser_broker import (  # noqa: E402
    SETTLE_SPA,
    BrowserBroker,
    ExtractedText,
    SettlePolicy,
    execute_text_extraction,
    extract_main_text,
    fetch_page_text,
    settle_and_extract,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ARTICLE = (FIXTURES / "browser_article_page.html").read_text(encoding="utf-8")
DIVSOUP = (FIXTURES / "browser_divsoup_page.html").read_text(encoding="utf-8")


# --- negative controls -----------------------------------------------------

def test_negative_control_boilerplate_is_actually_in_the_fixture():
    """If this fails the exclusion tests below are meaningless.

    Proves the strings we assert are ABSENT from the extraction are genuinely
    PRESENT in the source HTML — i.e. the extractor is removing them, rather
    than the fixture never having contained them.
    """
    for needle in (
        "Cookie preferences",
        "should-never-appear",
        "All rights reserved",
        "buy cheap hosting",
        "Please enable JavaScript",
        "font-family",
    ):
        assert needle in ARTICLE, f"fixture no longer contains {needle!r}"


def test_negative_control_extractor_can_fail():
    """A whole-document dump WOULD contain the boilerplate.

    The fallback strategy keeps nav/footer text, so this pins that the
    exclusions in `test_article_*` come from main-content SELECTION and not
    from the drop-list alone.
    """
    fragment = "<p>" + ("only one short paragraph here. " * 3) + "</p>"
    out = extract_main_text(fragment)
    assert out.strategy == "fallback:whole-document"
    assert "only one short paragraph" in out.text


# --- main-content selection ------------------------------------------------

def test_article_extracts_main_content():
    out = extract_main_text(ARTICLE)
    assert isinstance(out, ExtractedText)
    assert out.strategy == "semantic:<article>"
    assert "compact open-weight text-to-speech model" in out.text
    assert "removes the network round trip" in out.text
    assert "streaming audio pipeline" in out.text


@pytest.mark.parametrize(
    "boilerplate",
    [
        "Cookie preferences",      # <nav>
        "Sign in",                 # <header>
        "should-never-appear",     # <script>
        "font-family",             # <style>
        "All rights reserved",     # <footer>
        "buy cheap hosting",       # <aside>
        "Please enable JavaScript",  # <noscript>
        "Related article seven",   # link-dense sidebar div
    ],
)
def test_article_excludes_boilerplate(boilerplate):
    assert boilerplate not in extract_main_text(ARTICLE).text


def test_title_is_extracted_and_entities_decoded():
    assert extract_main_text(ARTICLE).title == "Kokoro TTS — a compact neural voice model"
    assert extract_main_text(DIVSOUP).title == "Boiling point of water & altitude"


def test_divsoup_scores_content_over_link_dense_navigation():
    """No <article>/<main> tag: the winner must be chosen by link density."""
    out = extract_main_text(DIVSOUP)
    assert out.strategy == "scored-container"
    assert "Clausius Clapeyron" in out.text
    assert "Create account" not in out.text
    assert "Mobile view" not in out.text


def test_malformed_markup_does_not_raise():
    """The divsoup fixture ends with unclosed tags inside a comment."""
    assert extract_main_text(DIVSOUP).chars > 200


@pytest.mark.parametrize("html", ["", "   ", "\n\t "])
def test_empty_html_is_empty_not_an_error(html):
    out = extract_main_text(html)
    assert out.text == ""
    assert out.chars == 0
    assert out.strategy == "empty"


def test_truncation_reports_itself():
    out = extract_main_text(ARTICLE, text_limit=120)
    assert out.truncated is True
    assert out.chars == 120
    assert len(out.text) == 120

    full = extract_main_text(ARTICLE)
    assert full.truncated is False
    assert full.chars == len(full.text)


def test_paragraph_breaks_survive_and_whitespace_is_collapsed():
    text = extract_main_text(ARTICLE).text
    assert "\n" in text
    assert "  " not in text          # runs of spaces collapsed
    assert "\n\n\n" not in text      # runs of blank lines collapsed
    assert text == text.strip()


# --- callable surfaces -----------------------------------------------------

def test_fetch_page_text_rejects_non_http_scheme():
    """SSRF/scheme guard must refuse BEFORE importing or launching a browser."""
    for bad in ("file:///etc/passwd", "ftp://example.com", "javascript:alert(1)", ""):
        out = asyncio.run(fetch_page_text(bad))
        assert out["ok"] is False
        assert "error" in out


def test_broker_pipeline_reports_missing_executor_cleanly():
    """A broker with no registered executor must not raise for text extraction."""
    broker = BrowserBroker()
    out = asyncio.run(
        execute_text_extraction(broker, url="https://example.com", user_id="u", session_id="s")
    )
    assert out["ok"] is False
    assert "no executor registered" in out["error"]
    assert out["surface"] == "zoeCloak"


def test_text_mode_plan_carries_the_action_and_limit():
    broker = BrowserBroker()
    plan = broker.plan_action(
        action="extract_text",
        params={"url": "https://example.com", "text_limit": 500},
        user_id="u",
        session_id="s",
    )
    assert plan.action == "extract_text"
    assert plan.params["text_limit"] == 500
    assert plan.action_class == "read_only_research"


def test_screenshot_path_is_unchanged_by_default():
    """Back-compat: a default plan must NOT be a text plan.

    chat.py research screenshots and the MCP browser tool depend on the
    executor still returning a PNG for any action other than extract_text.
    """
    broker = BrowserBroker()
    plan = broker.plan_action(
        action="screenshot", params={"navigate_to": "https://example.com"},
        user_id="u", session_id="s",
    )
    assert plan.action not in ("extract_text", "fetch_text")


# --- post-load settle policy ------------------------------------------------
#
# Offline throughout: a FAKE page (duck-typed content / wait_for_load_state /
# wait_for_timeout) plus a FAKE clock that advances ONLY when the page is told
# to wait. No Chromium, no network, no wall-clock sleeping — so these assert
# exact waits and poll counts deterministically rather than approximately.


class _FakePage:
    """Stand-in for a Playwright Page, driving a fake clock.

    ``bodies`` is what successive ``content()`` reads return, so an SPA is
    modelled exactly as it behaves: an empty shell first, real markup once the
    framework bundle has run. The last body repeats forever.
    """

    def __init__(self, bodies, *, idle_after_ms=None):
        self._bodies = list(bodies)
        self.clock = 0.0
        self.calls = []
        self._idle_after_ms = idle_after_ms

    def now(self):
        return self.clock

    async def content(self):
        self.calls.append(("content", None))
        body = self._bodies[0]
        if len(self._bodies) > 1:
            self._bodies.pop(0)
        return body

    async def wait_for_load_state(self, state, timeout=0):
        self.calls.append(("wait_for_load_state", state))
        if self._idle_after_ms is None or self._idle_after_ms > timeout:
            self.clock += timeout / 1000.0
            raise TimeoutError(f"{state} not reached in {timeout}ms")
        self.clock += self._idle_after_ms / 1000.0

    async def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))
        self.clock += ms / 1000.0


SHELL = "<html><head><title>r/perth</title></head><body><div id=root></div></body></html>"
# NOTE: every paragraph must be DISTINCT. `_tidy` de-duplicates repeated lines,
# so a fixture built by multiplying one paragraph collapses back under the
# content floor and the settle looks broken when it is not.
_PARAS = "".join(
    f"<p>Emu Export block price discussion in Geraldton, comment {i}. "
    f"Reply {i} quotes a carton figure from a local bottleshop catalogue.</p>"
    for i in range(12)
)
RENDERED = (
    "<html><head><title>r/perth</title></head><body><article>"
    + _PARAS
    + "</article></body></html>"
)


def _settle(page, policy, **kw):
    return asyncio.run(settle_and_extract(page, policy=policy, now=page.now, **kw))


def test_default_policy_waits_for_nothing():
    """NEGATIVE CONTROL, cost direction: an unasked-for settle must cost ZERO.

    Every existing caller (chat.py research screenshots, the MCP browser tool)
    keeps its current timing only while the default policy performs no waits at
    all. If a default ever creeps in, this goes red.
    """
    page = _FakePage([RENDERED])
    extracted, log = _settle(page, SettlePolicy())

    assert log == []
    assert page.clock == 0.0
    assert [c[0] for c in page.calls] == ["content"]
    assert extracted.chars > 200
    assert SettlePolicy().active is False


def test_spa_shell_is_rescued_by_the_content_floor():
    """The reddit / thespruceeats shape: shell first, real markup after."""
    page = _FakePage([SHELL, RENDERED])
    extracted, log = _settle(page, SETTLE_SPA)

    assert "Emu Export block price discussion" in extracted.text
    assert extracted.chars >= SETTLE_SPA.min_chars
    floor = [e for e in log if e["stage"] == "content-floor"][0]
    assert floor["outcome"] == "reached"
    assert floor["polls"] == 1


def test_negative_control_without_the_settle_the_same_page_yields_the_shell():
    """LOAD-BEARING: proves the SETTLE is what rescues the SPA, not the fixture.

    Same fake page, policy removed. If this ever passes with real content then
    the test above is vacuous — the shell would have sufficed on its own.
    """
    page = _FakePage([SHELL, RENDERED])
    extracted, log = _settle(page, SettlePolicy())

    assert "Emu Export" not in extracted.text
    assert extracted.chars < 1_000
    assert log == []


def test_networkidle_timeout_is_recorded_not_raised():
    """Many live pages never go idle (beacons, sockets, polling). That is NORMAL."""
    page = _FakePage([RENDERED], idle_after_ms=None)
    extracted, log = _settle(page, SettlePolicy(network_idle_ms=8_000))

    assert extracted.chars > 200          # extraction still happened
    assert log[0]["stage"] == "networkidle"
    assert log[0]["outcome"] == "timeout"
    assert log[0]["waited_ms"] == 8_000


def test_networkidle_success_is_recorded_with_the_real_wait():
    page = _FakePage([RENDERED], idle_after_ms=1_200)
    _, log = _settle(page, SettlePolicy(network_idle_ms=8_000))

    assert log[0] == {"stage": "networkidle", "outcome": "idle", "waited_ms": 1_200}


def test_content_floor_gives_up_at_the_ceiling_instead_of_hanging():
    """A page that never renders must cost a BOUNDED amount."""
    page = _FakePage([SHELL])
    policy = SettlePolicy(min_chars=1_000, poll_ms=500, max_wait_ms=2_000)
    extracted, log = _settle(page, policy)

    floor = [e for e in log if e["stage"] == "content-floor"][0]
    assert floor["outcome"] == "gave-up"
    assert floor["polls"] == 4                    # 2000ms ceiling / 500ms poll
    assert page.clock == pytest.approx(2.0)
    assert extracted.chars < 1_000                # best effort is still RETURNED


def test_content_floor_stops_polling_the_moment_the_floor_is_cleared():
    """It must not burn the whole ceiling on a page that rendered quickly."""
    page = _FakePage([SHELL, RENDERED])
    policy = SettlePolicy(min_chars=1_000, poll_ms=500, max_wait_ms=30_000)
    _, log = _settle(page, policy)

    assert log[0]["polls"] == 1
    assert page.clock == pytest.approx(0.5)


def test_content_floor_is_inert_without_a_ceiling():
    """min_chars with no max_wait_ms must not become an unbounded loop."""
    page = _FakePage([SHELL])
    policy = SettlePolicy(min_chars=1_000, max_wait_ms=0)
    _, log = _settle(page, policy)

    assert log == []
    assert policy.active is False


@pytest.mark.parametrize(
    "params,expected",
    [
        ({}, (0, 0, 0)),
        ({"settle_ms": 500}, (0, 500, 0)),
        ({"network_idle_ms": 8000, "settle_min_chars": 1000}, (8000, 0, 1000)),
        ({"settle": {"network_idle_ms": 3000}}, (3000, 0, 0)),
        ({"settle_ms": "not-a-number"}, (0, 0, 0)),
        ({"settle_ms": -5}, (0, 0, 0)),
        ({"settle_ms": None}, (0, 0, 0)),
    ],
)
def test_policy_from_plan_params_is_total(params, expected):
    """Plan params are untrusted JSON: bad values degrade to 0, never raise."""
    p = SettlePolicy.from_params(params)
    assert (p.network_idle_ms, p.settle_ms, p.min_chars) == expected
    assert p.poll_ms > 0


def test_fetch_page_text_still_refuses_bad_schemes_with_a_settle_policy():
    out = asyncio.run(fetch_page_text("file:///etc/passwd", settle=SETTLE_SPA))
    assert out["ok"] is False
