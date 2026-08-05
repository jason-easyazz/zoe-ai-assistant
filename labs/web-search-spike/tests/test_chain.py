"""Offline proofs for the page-read FALLBACK CHAIN (`websearch/chain.py`).

Everything here runs against CANNED tier responses — no network, no Chromium,
no `cloakbrowser` import — so the fallback POLICY is pinned independently of
whether any tier is reachable tonight.

Two negative controls carry the file, and they point in opposite directions:

  `test_negative_control_without_fallback_a_blocked_url_returns_nothing`
      Remove the fallback -> the blocked URL yields no content. Proves the
      later tiers are what rescue it, not the fixture being trivially readable.

  `test_cloakbrowser_never_launches_when_the_cheap_tier_succeeded`
      The expensive direction, and the one that protects the live voice brain:
      a healthy httpx read must leave the browser tier COMPLETELY untouched —
      not called, and (`test_..._is_not_even_imported`) not imported.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from websearch.chain import (  # noqa: E402
    BLOCKED,
    CONTENT_FLOOR_CHARS,
    ERROR,
    OK,
    THIN,
    FetchResult,
    Hop,
    classify,
    fetch_url,
    fetch_urls,
)
from websearch.direct import TierBlocked, TierFailed  # noqa: E402
from websearch.engines import EnginesBlocked, is_blocked  # noqa: E402
from websearch.extract import ExtractUnavailable, Page  # noqa: E402

FULL = "Emu Export 30 x 375mL block. $59.99 each at the Geraldton store. " * 20
SHELL = "Enable JavaScript to continue."


# --- canned tiers ----------------------------------------------------------

class _Spy:
    """A tier that records every call, so 'never launched' is provable."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def __call__(self, url, *, text_limit=20_000, **kw):
        self.calls.append(url)
        outcome = self.behaviour
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _page(text, tier, detail="canned"):
    return Page(url="https://x/", text=text, tier=tier, elapsed_s=0.1, detail=detail)


#: CloakBrowser is always LAST, so a 2-tier chain is httpx -> cloakbrowser,
#: never httpx -> jina. Getting this wrong would silently test the wrong hop.
_NAMES = {1: ["httpx"], 2: ["httpx", "cloakbrowser"], 3: ["httpx", "jina", "cloakbrowser"]}


def _chain(*behaviours):
    """Build (tiers, spies) from a list of per-tier behaviours."""
    spies = [_Spy(b) for b in behaviours]
    return list(zip(_NAMES[len(behaviours)], spies)), spies


# --- the load-bearing negative controls -------------------------------------

def test_cloakbrowser_never_launches_when_the_cheap_tier_succeeded():
    """COST CONTROL. A ~553 MB Chromium on a box with ~200 MB free is the most
    dangerous thing this chain can do. It must not happen speculatively."""
    tiers, spies = _chain(_page(FULL, "httpx"), _page(FULL, "jina"), _page(FULL, "cloakbrowser"))
    out = fetch_url("https://ok.example/", tiers=tiers)

    assert out.ok and out.tier_served == "httpx"
    assert spies[0].calls == ["https://ok.example/"]
    assert spies[1].calls == [], "jina was called despite httpx succeeding"
    assert spies[2].calls == [], "CLOAKBROWSER LAUNCHED ON A HEALTHY PAGE"
    assert len(out.provenance) == 1
    assert out.fell_back is False


def test_cloakbrowser_is_not_even_imported_on_a_healthy_read():
    """Stronger than 'not called': `cloak.py` loads the broker module FROM DISK
    at import, so a speculative import is itself real work."""
    for mod in ("websearch.cloak", "zoe_browser_broker"):
        sys.modules.pop(mod, None)

    tiers, _ = _chain(_page(FULL, "httpx"))
    assert fetch_url("https://ok.example/", tiers=tiers).ok
    assert "zoe_browser_broker" not in sys.modules


def test_negative_control_without_fallback_a_blocked_url_returns_nothing():
    """Remove the fallback and the SAME canned wall yields no content.

    If this ever passes with real text, the fallback tests below are vacuous —
    the page would have been readable without any fallback at all.
    """
    wall = TierBlocked("HTTP 403")
    only_httpx, _ = _chain(wall)
    out = fetch_url("https://walled.example/", tiers=only_httpx)

    assert out.ok is False
    assert out.tier_served is None
    assert out.text == ""
    assert out.provenance[0].verdict == BLOCKED

    # ...and WITH the fallback, the identical wall is rescued.
    with_fallback, _ = _chain(wall, _page(FULL, "cloakbrowser"))
    rescued = fetch_url("https://walled.example/", tiers=with_fallback)
    assert rescued.ok and rescued.tier_served == "cloakbrowser"
    assert "Emu Export" in rescued.text


# --- fallback triggers ------------------------------------------------------

@pytest.mark.parametrize(
    "refusal,expected_verdict",
    [
        (TierBlocked("HTTP 403"), BLOCKED),
        (TierBlocked("HTTP 407"), BLOCKED),
        (TierBlocked("HTTP 429"), BLOCKED),
        (TierBlocked("challenge body at HTTP 200"), BLOCKED),
        (EnginesBlocked("no engine answered"), BLOCKED),
        (ExtractUnavailable("jina anonymous tier refused this domain (403): x"), BLOCKED),
        (ExtractUnavailable("jina rate limit (429, ~20 RPM anonymous): x"), BLOCKED),
        (TierFailed("httpx ReadTimeout: ..."), ERROR),
        (ValueError("something unexpected"), ERROR),
    ],
)
def test_every_refusal_shape_falls_through_and_is_classified(refusal, expected_verdict):
    tiers, spies = _chain(refusal, _page(FULL, "cloakbrowser"))
    out = fetch_url("https://x/", tiers=tiers)

    assert out.ok and out.tier_served == "cloakbrowser"
    assert out.provenance[0].verdict == expected_verdict
    assert spies[1].calls == ["https://x/"], "the fallback tier was not reached"


def test_a_thin_result_falls_through_even_though_it_did_not_raise():
    """The SPA shape: HTTP 200, plausible body, no content. A refusal wearing
    a success's clothes — exactly the `EnginesBlocked` trap one layer down."""
    tiers, _ = _chain(_page(SHELL, "httpx"), _page(FULL, "cloakbrowser"))
    out = fetch_url("https://spa.example/", tiers=tiers)

    assert out.provenance[0].verdict == THIN
    assert f"floor {CONTENT_FLOOR_CHARS}" in out.provenance[0].detail
    assert out.tier_served == "cloakbrowser"


def test_accept_predicate_escalates_a_page_that_cleared_the_floor_but_lacks_the_answer():
    """MEASURED REGRESSION, 2026-08-03.

    `cellarbrations.rsgwa.com.au` gave plain httpx 759 chars — over the 600
    floor, so the chain accepted it and stopped. Those 759 chars had no prices;
    CloakBrowser's 4,915 had the one the query was about. A size threshold is a
    proxy, and a proxy can be satisfied without the thing it proxies for.
    """
    chrome = "Store hours. Delivery info. Contact us. " * 25   # >600 chars, no price
    tiers, spies = _chain(_page(chrome, "httpx"), _page(FULL, "cloakbrowser"))

    out = fetch_url("https://cellarbrations.example/", tiers=tiers,
                    accept=lambda t: "emu export" in t.lower())

    assert out.tier_served == "cloakbrowser"
    assert "Emu Export" in out.text
    assert out.provenance[0].verdict == THIN
    assert "FAILED the caller's accept()" in out.provenance[0].detail
    assert spies[1].calls, "the fallback tier was never reached"


def test_negative_control_without_accept_the_same_page_wrongly_wins():
    """The other half: proves `accept` is what fixes it, not the fixture."""
    chrome = "Store hours. Delivery info. Contact us. " * 25
    tiers, spies = _chain(_page(chrome, "httpx"), _page(FULL, "cloakbrowser"))

    out = fetch_url("https://cellarbrations.example/", tiers=tiers)

    assert out.tier_served == "httpx", "the floor alone should (wrongly) accept this"
    assert "Emu Export" not in out.text
    assert spies[1].calls == []


def test_accept_can_only_make_the_chain_TRY_HARDER_never_lower_the_bar():
    """SAFETY PROPERTY. A permissive predicate must not promote a page the
    floor rejected — otherwise `accept` becomes a way to disable the floor."""
    tiers, spies = _chain(_page(SHELL, "httpx"), _page(FULL, "cloakbrowser"))

    out = fetch_url("https://spa.example/", tiers=tiers, accept=lambda t: True)

    assert out.provenance[0].verdict == THIN
    assert "< floor" in out.provenance[0].detail, "the floor must be checked FIRST"
    assert out.tier_served == "cloakbrowser"
    assert spies[1].calls, "an always-true accept() must not short-circuit the floor"


def test_accept_is_not_consulted_when_absent():
    """Default behaviour is exactly as before — no predicate, floor only."""
    tiers, _ = _chain(_page(FULL, "httpx"))
    out = fetch_url("https://x/", tiers=tiers)
    assert out.tier_served == "httpx"
    assert "accept()" not in out.provenance[0].detail


def test_the_floor_is_a_parameter_not_a_hidden_constant():
    tiers, spies = _chain(_page(SHELL, "httpx"), _page(FULL, "cloakbrowser"))
    out = fetch_url("https://spa.example/", tiers=tiers, floor=10)

    assert out.tier_served == "httpx", "a low floor must accept the short page"
    assert spies[1].calls == []
    assert out.floor == 10


# --- provenance is never silent ---------------------------------------------

def test_provenance_records_every_hop_in_order():
    tiers, _ = _chain(
        TierBlocked("HTTP 403"), _page(SHELL, "jina"), _page(FULL, "cloakbrowser")
    )
    out = fetch_url("https://bws.example/product", tiers=tiers)

    assert [h.tier for h in out.provenance] == ["httpx", "jina", "cloakbrowser"]
    assert [h.verdict for h in out.provenance] == [BLOCKED, THIN, OK]
    assert out.blocked_by == ["httpx"]
    assert out.fell_back is True

    trail = out.trail()
    for expected in ("httpx: blocked", "HTTP 403", "jina: thin", "cloakbrowser: ok"):
        assert expected in trail

    dumped = out.to_dict()
    assert len(dumped["provenance"]) == 3
    assert dumped["tier_served"] == "cloakbrowser"


def test_a_successful_first_hop_still_records_its_hop():
    """Provenance must never be EMPTY — 'nothing recorded' and 'nothing
    happened' have to stay distinguishable."""
    tiers, _ = _chain(_page(FULL, "httpx"))
    out = fetch_url("https://x/", tiers=tiers)
    assert len(out.provenance) == 1
    assert out.provenance[0].verdict == OK


def test_total_failure_reports_which_wall_stopped_which_tier():
    """Every tier refused. That is a FINDING, and the trail is the finding."""
    tiers, _ = _chain(
        TierBlocked("HTTP 403"),
        ExtractUnavailable("jina anonymous tier refused this domain (403): x"),
        ExtractUnavailable("cloakbrowser: CloakBrowser text extraction failed: Timeout 30000ms"),
    )
    out = fetch_url("https://hard.example/", tiers=tiers)

    assert out.ok is False and out.tier_served is None
    assert out.blocked_by == ["httpx", "jina"]
    assert out.provenance[2].verdict == ERROR
    assert "Timeout" in out.provenance[2].detail


def test_best_effort_thin_text_is_returned_but_not_marked_served():
    """Better to hand back 200 chars PLUS the trail than nothing plus the trail
    — but it must not claim a tier served it."""
    tiers, _ = _chain(_page(SHELL, "httpx"), TierBlocked("HTTP 403"))
    out = fetch_url("https://x/", tiers=tiers)

    assert out.ok is False
    assert out.tier_served is None
    assert SHELL in out.text


# --- classification units ----------------------------------------------------

def test_classify_boundary_is_inclusive_of_the_floor():
    assert classify(_page("x" * CONTENT_FLOOR_CHARS, "t"), CONTENT_FLOOR_CHARS)[0] == OK
    assert classify(_page("x" * (CONTENT_FLOOR_CHARS - 1), "t"), CONTENT_FLOOR_CHARS)[0] == THIN


def test_classify_counts_STRIPPED_characters():
    """Whitespace is not content; an SPA shell padded with newlines is still thin."""
    padded = "  \n\n  " + "x" * 10 + "  \n\n  "
    assert classify(_page(padded, "t"), 100)[0] == THIN


# --- the block detector it depends on ---------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "<html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>",
        "<div id='px-captcha'>please verify</div>",
        "Request unsuccessful. Incapsula incident ID: 123",
        "<p>Pardon Our Interruption</p>",
        "<title>Captcha</title>",
        "<script src='/anomaly.js'>",
        "Please enable JavaScript and cookies to continue",
        "Access to this page has been denied",
    ],
)
def test_commercial_bot_walls_are_detected(body):
    assert is_blocked(body) is True


@pytest.mark.parametrize(
    "body",
    [
        # NEGATIVE CONTROL for the detector: a false positive escalates a
        # perfectly readable page to a Chromium launch, so ordinary prose that
        # merely MENTIONS these ideas must not trip it.
        "<h1>How CAPTCHA systems work</h1><p>A captcha is a challenge-response test.</p>",
        "<p>This product is currently blocked from online sale in WA.</p>",
        "<p>Our robots are busy picking your order.</p>",
        "<p>Access denied to under-18s. Please present ID at the counter.</p>",
        "<p>Emu Export 30 block $59.99. Add to cart.</p>",
        "",
    ],
)
def test_ordinary_pages_are_not_mistaken_for_bot_walls(body):
    assert is_blocked(body) is False


# --- sequencing --------------------------------------------------------------

def test_fetch_urls_is_sequential_and_returns_one_result_per_url():
    """Never two Chromiums at once — the ordering is the safety property."""
    order: list[str] = []

    def _tier(url, *, text_limit=20_000, **kw):
        order.append(url)
        return _page(FULL, "httpx")

    urls = ["https://a/", "https://b/", "https://c/"]
    out = fetch_urls(urls, tiers=[("httpx", _tier)])

    assert order == urls
    assert [r.url for r in out] == urls
    assert all(r.tier_served == "httpx" for r in out)


def test_fetch_url_never_raises_even_when_a_tier_explodes():
    def _boom(url, *, text_limit=20_000, **kw):
        raise RuntimeError("kaboom")

    out = fetch_url("https://x/", tiers=[("httpx", _boom)])
    assert isinstance(out, FetchResult)
    assert out.ok is False
    assert out.provenance[0].verdict == ERROR
    assert "kaboom" in out.provenance[0].detail


def test_hop_line_is_readable():
    assert Hop("httpx", BLOCKED, "HTTP 403", 0, 0.4).line() == "httpx: blocked (HTTP 403) [0 chars, 0.4s]"
