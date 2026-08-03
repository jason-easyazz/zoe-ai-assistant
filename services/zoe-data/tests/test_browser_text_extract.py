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
    BrowserBroker,
    ExtractedText,
    execute_text_extraction,
    extract_main_text,
    fetch_page_text,
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
