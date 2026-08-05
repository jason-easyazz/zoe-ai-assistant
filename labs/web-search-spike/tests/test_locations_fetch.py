"""`fetch_with_location` — routing, attribution, and the NEGATIVE CONTROLS.

Nothing here touches the network or a browser: every live effect the function
can have is injected (`chain_fetch`, `api_getter`, `jar_factory`), which is what
makes it possible to assert that the ones that should NOT run did not.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from websearch.locations import registry as R
from websearch.locations.cookies import CookieJar
from websearch.locations.fetch import LocatedRead, _sku_from_url, fetch_with_location
from websearch.locations.provenance import (
    GERALDTON,
    METHOD_API,
    METHOD_NONE,
    METHOD_PICKER,
    StoreContext,
)

FIX = pathlib.Path(__file__).parent / "fixtures" / "locations"


class FakeChainResult:
    def __init__(self, text="page text here", ok=True, tier="httpx"):
        self.ok = ok
        self.text = text
        self.title = "T"
        self.tier_served = tier if ok else None
        self.provenance = ["hop"]


class Spy:
    """Records calls. `.called` is the assertion surface for a negative control."""

    def __init__(self, result=None):
        self.calls: list[tuple] = []
        self.result = result

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.result() if callable(self.result) else self.result

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture()
def tmp_jar(tmp_path):
    return CookieJar(tmp_path)


def add_recipe(monkeypatch, recipe: R.Recipe):
    """Install a recipe for the duration of one test, without mutating the real
    registry — a test that leaked an entry would silently change every later one."""
    monkeypatch.setitem(R.RECIPES, R.host_of(recipe.domain), recipe)
    return recipe


# ==========================================================================
# THE NEGATIVE CONTROL
# ==========================================================================


def test_recipeless_domain_falls_through_untouched(monkeypatch, tmp_path):
    """A domain with NO recipe must reach the ordinary chain with NOTHING added.

    This is the regression that would matter most: "we added store awareness
    and now every unrelated fetch opens a cookie jar / considers a browser /
    behaves differently". Three spies, and two of them must stay cold.
    """
    chain = Spy(lambda: FakeChainResult(text="ordinary page"))
    api = Spy(lambda: {"Products": []})
    jar_spy = Spy(lambda: CookieJar(tmp_path))

    out = fetch_with_location(
        "https://en.wikipedia.org/wiki/Beer",
        store_ctx=GERALDTON,
        chain_fetch=chain,
        api_getter=api,
        jar_factory=jar_spy,
        floor=600,          # caller kwargs must pass through verbatim
        use_jina=False,
    )

    assert chain.called, "the ordinary chain did not run"
    assert not api.called, "the API tier ran for a domain with no recipe"
    assert not jar_spy.called, "a cookie jar was opened for a domain with no recipe"

    (args, kwargs) = chain.calls[0]
    assert args == ("https://en.wikipedia.org/wiki/Beer",)
    assert kwargs == {"floor": 600, "use_jina": False}, "caller kwargs were mutated"

    assert out.recipe_kind == R.NONE
    assert out.text == "ordinary page"
    assert out.ok is True
    assert out.attribution.method == METHOD_NONE
    assert out.attribution.confident is False


def test_a_recipe_less_result_is_flagged_low_confidence_in_words(monkeypatch, tmp_path):
    out = fetch_with_location(
        "https://example.org/thing",
        chain_fetch=Spy(lambda: FakeChainResult()),
        jar_factory=Spy(lambda: CookieJar(tmp_path)),
    )
    assert out.attribution.warnings
    assert "LOW CONFIDENCE" in out.attribution.warnings[0]
    assert "store-less" in out.attribution.line()
    assert "LOW CONFIDENCE" in out.attribution.line()


# ==========================================================================
# api recipes
# ==========================================================================


def endeavour_recipe(**over) -> R.Recipe:
    base = dict(
        domain="bws.com.au",
        retailer="BWS",
        kind=R.API,
        verified="2026-08-04 test",
        api_url="https://api.bws.com.au/apis/ui/Product/{sku}",
        store_transport="none",
        stores={"Geraldton": "4328"},
        parse=R.parse_endeavour,
    )
    base.update(over)
    return R.Recipe(**base)


def test_api_recipe_uses_httpx_only_and_never_the_chain(monkeypatch, tmp_path):
    """The economic argument: an api recipe must not be able to launch Chromium."""
    add_recipe(monkeypatch, endeavour_recipe())
    payload = json.loads((FIX / "bws_product_38879_storeless.json").read_text())
    api = Spy(lambda: payload)
    chain = Spy(lambda: FakeChainResult())
    jar_spy = Spy(lambda: CookieJar(tmp_path))

    out = fetch_with_location(
        "https://bws.com.au/product/38879/emu-export-lager-cans-375ml",
        chain_fetch=chain, api_getter=api, jar_factory=jar_spy,
    )

    assert api.called
    assert not chain.called, "an api recipe reached the page-fetch chain"
    assert not jar_spy.called, "an api recipe opened a cookie jar"
    assert out.recipe_kind == R.API
    assert out.ok
    assert {r["sku"] for r in out.items} == {"38879", "69222", "59747"}
    assert api.calls[0][0][0] == "https://api.bws.com.au/apis/ui/Product/38879"


def test_api_store_id_travels_by_the_declared_transport(monkeypatch):
    add_recipe(monkeypatch, endeavour_recipe(
        api_url="https://api/x/{sku}?store={store_id}", store_transport="query"))
    api = Spy(lambda: {"Products": [{"Stockcode": 1, "Price": 5}]})
    out = fetch_with_location("https://bws.com.au/product/38879/x", api_getter=api)
    assert api.calls[0][0][0] == "https://api/x/38879?store=4328"
    assert out.attribution.method == METHOD_API
    assert out.attribution.store_id == "4328"

    add_recipe(monkeypatch, endeavour_recipe(store_transport="header", store_param="X-Store"))
    api = Spy(lambda: {"Products": [{"Stockcode": 1, "Price": 5}]})
    fetch_with_location("https://bws.com.au/product/38879/x", api_getter=api)
    assert api.calls[0][0][1]["X-Store"] == "4328"

    add_recipe(monkeypatch, endeavour_recipe(store_transport="cookie", store_param="sid"))
    api = Spy(lambda: {"Products": [{"Stockcode": 1, "Price": 5}]})
    fetch_with_location("https://bws.com.au/product/38879/x", api_getter=api)
    assert api.calls[0][1]["cookie"] == "sid=4328"


def test_api_recipe_with_no_store_id_for_the_locality_is_STORE_LESS(monkeypatch):
    """The kind of the RECIPE is not evidence about the kind of the ANSWER.

    An `api` recipe that has no store id for the asked-for town returns the
    endpoint's default scope. That is a store-less price and must be labelled
    one, even though it arrived by the good path.
    """
    add_recipe(monkeypatch, endeavour_recipe(stores={}))
    api = Spy(lambda: json.loads((FIX / "bws_product_38879_storeless.json").read_text()))
    out = fetch_with_location("https://bws.com.au/product/38879/x", api_getter=api)
    assert out.ok
    assert out.items
    assert out.attribution.method == METHOD_NONE
    assert out.attribution.confident is False
    assert "no store id known" in out.attribution.detail


def test_api_failure_is_reported_not_swallowed(monkeypatch):
    add_recipe(monkeypatch, endeavour_recipe())

    def boom(*a, **kw):
        raise RuntimeError("HTTP 503")

    out = fetch_with_location("https://bws.com.au/product/38879/x", api_getter=boom)
    assert out.ok is False
    assert "503" in out.error
    assert out.attribution.method == METHOD_NONE
    assert out.confident is False


def test_api_that_answers_with_no_products_is_not_ok(monkeypatch):
    add_recipe(monkeypatch, endeavour_recipe())
    out = fetch_with_location("https://bws.com.au/product/38879/x",
                              api_getter=Spy(lambda: {"Products": []}))
    assert out.ok is False
    assert "no products" in out.error


# ==========================================================================
# interaction recipes
# ==========================================================================


def interaction_recipe(domain="thirstycamel.com.au") -> R.Recipe:
    return R.Recipe(domain=domain, retailer="TC", kind=R.INTERACTION,
                    verified="2026-08-04 test", picker="thirstycamel")


def test_interaction_without_a_cached_session_is_store_less_and_does_NOT_launch(
    monkeypatch, tmp_jar
):
    """A per-product fetch never gets to decide to launch Chromium.

    That is an operator-scale decision on the box that runs the voice brain.
    Absent a cached session the read still happens (through the ordinary chain)
    and is honestly labelled store-less, with the fix named in the detail.
    """
    add_recipe(monkeypatch, interaction_recipe())
    chain = Spy(lambda: FakeChainResult(text="a page with no Geraldton in it"))

    out = fetch_with_location(
        "https://www.thirstycamel.com.au/product/x", jar=tmp_jar, chain_fetch=chain
    )
    assert chain.called
    assert out.recipe_kind == R.INTERACTION
    assert out.attribution.method == METHOD_NONE
    assert "no fresh picker session" in out.attribution.detail
    assert "capture.py" in out.attribution.detail


def test_interaction_with_a_cached_session_sends_cookies_and_claims_the_store(
    monkeypatch, tmp_jar
):
    add_recipe(monkeypatch, interaction_recipe())
    tmp_jar.save(
        "thirstycamel.com.au",
        [{"name": "store", "value": "77", "domain": ".thirstycamel.com.au"}],
        store_id="77",
        store_label="Thirsty Camel Geraldton",
    )
    chain = Spy(lambda: FakeChainResult(text="Welcome to our Geraldton store. Emu Export $57"))

    out = fetch_with_location(
        "https://www.thirstycamel.com.au/product/x", jar=tmp_jar, chain_fetch=chain
    )
    assert out.attribution.method == METHOD_PICKER
    assert out.attribution.store_id == "77"
    assert out.attribution.store == "Thirsty Camel Geraldton"
    assert out.attribution.locality_in_text is True
    assert out.attribution.confident is True


def test_a_cached_session_whose_page_lacks_the_locality_gets_a_warning(monkeypatch, tmp_jar):
    """Cookie freshness is not proof the SERVER still honours the selection."""
    add_recipe(monkeypatch, interaction_recipe())
    tmp_jar.save(
        "thirstycamel.com.au",
        [{"name": "store", "value": "77", "domain": ".thirstycamel.com.au"}],
        store_id="77",
    )
    out = fetch_with_location(
        "https://www.thirstycamel.com.au/product/x",
        jar=tmp_jar,
        chain_fetch=Spy(lambda: FakeChainResult(text="no locality mentioned anywhere")),
    )
    assert out.attribution.locality_in_text is False
    assert any("may have expired server-side" in w for w in out.attribution.warnings)


def test_a_jar_for_one_retailer_is_not_sent_to_another(monkeypatch, tmp_jar):
    """Belt-and-braces at the FETCH layer, not only inside the jar."""
    add_recipe(monkeypatch, interaction_recipe("thirstycamel.com.au"))
    add_recipe(monkeypatch, interaction_recipe("bottlemart.com.au"))
    tmp_jar.save(
        "thirstycamel.com.au",
        [{"name": "store", "value": "77", "domain": ".thirstycamel.com.au"}],
        store_id="77",
    )
    out = fetch_with_location(
        "https://bottlemart.com.au/product/x",
        jar=tmp_jar,
        chain_fetch=Spy(lambda: FakeChainResult()),
    )
    assert out.attribution.method == METHOD_NONE, "bottlemart inherited thirstycamel's session"


# ==========================================================================
# odds and ends
# ==========================================================================


@pytest.mark.parametrize(
    "url,sku",
    [
        ("https://bws.com.au/product/38879/emu-export-lager-cans-375ml", "38879"),
        ("https://www.liquorland.com.au/beer-and-cider/emu-export-block-can-375ml_6517858", "6517858"),
        ("https://www.danmurphys.com.au/product/DM_38879/emu-export-30-block-cans-375ml", "38879"),
        ("https://example.org/no-numbers-here", ""),
    ],
)
def test_sku_from_url(url, sku):
    assert _sku_from_url(url) == sku


def test_located_read_serialises_its_attribution():
    out = fetch_with_location(
        "https://example.org/x", chain_fetch=Spy(lambda: FakeChainResult())
    )
    d = out.to_dict()
    assert d["attribution"]["confident"] is False
    assert d["attribution"]["method"] == METHOD_NONE
    assert isinstance(out, LocatedRead)


def test_store_context_matching():
    assert GERALDTON.matches("our GERALDTON store") is True
    assert GERALDTON.matches("postcode 6530 WA") is True
    assert GERALDTON.matches("Perth 6000") is False
    assert StoreContext("Wandina", "6530").label() == "Wandina 6530"
