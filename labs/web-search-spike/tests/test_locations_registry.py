"""The recipe registry + the Endeavour parser, against REAL captured responses.

Every fixture under `tests/fixtures/locations/` is a trimmed but verbatim
response captured on 2026-08-04 (see each file's `_fixture_note`). Testing the
parser against a hand-written dict would only prove the parser agrees with my
idea of the shape, which is the thing most likely to be wrong.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from websearch.locations import registry as R

FIX = pathlib.Path(__file__).parent / "fixtures" / "locations"


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------- recipe shape


def test_every_registered_recipe_is_verified_and_well_formed():
    """A recipe without a `verified` string is a guess. See registry docstring."""
    assert R.RECIPES, "the registry is empty — no retailer was measured"
    for domain, rec in R.RECIPES.items():
        assert rec.kind in R.KINDS, domain
        assert rec.verified.strip(), f"{domain} has no `verified` provenance"
        assert "2026" in rec.verified, f"{domain}: `verified` should name the run date"
        if rec.kind == R.API:
            assert rec.api_url and rec.parse is not None, domain
        if rec.kind == R.INTERACTION:
            assert rec.picker, domain
        if rec.kind == R.NONE:
            assert rec.reason, f"{domain}: kind=none MUST say why"


def test_recipe_rejects_a_missing_verified_field():
    with pytest.raises(ValueError, match="verified"):
        R.Recipe(domain="x.com", retailer="X", kind=R.NONE, verified="", reason="r")


def test_recipe_rejects_an_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        R.Recipe(domain="x.com", retailer="X", kind="magic", verified="2026-08-04")


def test_kind_none_must_state_a_reason():
    """`none` is a RESULT, and a result with no reason is not usable by a human."""
    with pytest.raises(ValueError, match="MUST say why"):
        R.Recipe(domain="x.com", retailer="X", kind=R.NONE, verified="2026-08-04")


def test_api_kind_requires_a_url_and_interaction_requires_a_picker():
    with pytest.raises(ValueError, match="api_url"):
        R.Recipe(domain="x.com", retailer="X", kind=R.API, verified="2026-08-04")
    with pytest.raises(ValueError, match="picker"):
        R.Recipe(domain="x.com", retailer="X", kind=R.INTERACTION, verified="2026-08-04")


# ------------------------------------------------------------ domain lookup


def test_lookup_matches_subdomains_but_not_lookalikes():
    assert R.domain_of("https://bws.com.au/product/38879/x") == "bws.com.au"
    assert R.domain_of("https://www.bws.com.au/x") == "bws.com.au"
    assert R.domain_of("https://api.bws.com.au/apis/ui/Product/38879") == "bws.com.au"
    # the lookalike must NOT inherit the recipe
    assert R.domain_of("https://notbws.com.au/x") == ""
    assert R.get_recipe("https://notbws.com.au/x") is None


def test_unknown_domain_has_no_recipe_and_does_not_raise():
    assert R.get_recipe("https://example.org/anything") is None
    assert R.has_recipe("https://example.org/anything") is False
    assert R.get_recipe("") is None
    assert R.get_recipe("not a url at all") is None


def test_store_id_lookup_is_case_insensitive_and_misses_cleanly():
    rec = R.Recipe(
        domain="x.com", retailer="X", kind=R.API, verified="2026-08-04 test",
        api_url="https://x/{sku}", stores={"Geraldton": "4328"}, parse=lambda p: [],
    )
    assert rec.store_id_for("geraldton") == "4328"
    assert rec.store_id_for("GERALDTON") == "4328"
    assert rec.store_id_for("Perth") == ""
    assert rec.store_id_for("") == ""
    assert rec.store_label_for("4328") == "Geraldton"
    assert rec.store_label_for("9999") == ""


# ------------------------------------------------------- the Endeavour parser


def test_parse_endeavour_returns_every_pack_size_not_just_the_first():
    """A pack GROUP, not a product.

    Emu Export 38879 resolves to three rows — a single can, a 6-pack and the
    30-block — and they are three different prices. Returning only the first
    would answer a question about a can when the operator asked about a block,
    which is a wrong number that looks entirely right.
    """
    rows = R.parse_endeavour(load("bws_product_38879_storeless.json"))
    by_sku = {r["sku"]: r for r in rows}
    assert set(by_sku) == {"38879", "69222", "59747"}
    assert by_sku["38879"]["price"] == 6       # single can
    assert by_sku["69222"]["price"] == 23      # pack of 6
    assert by_sku["59747"]["price"] == 69      # the 30-block
    assert all(r["pack"] == "375ML" for r in rows)
    assert all("Emu Export" in r["name"] for r in rows)


def test_parse_endeavour_carries_the_special_flags():
    rows = R.parse_endeavour(load("bws_product_38879_storeless.json"))
    row = next(r for r in rows if r["sku"] == "59747")
    for key in ("on_special", "member_special", "was_price", "savings", "stock_on_hand"):
        assert key in row
    assert isinstance(row["on_special"], bool)
    assert isinstance(row["member_special"], bool)


@pytest.mark.parametrize("payload", [None, {}, [], "", {"Products": None}, {"Products": ["junk"]}])
def test_parse_endeavour_never_raises_on_junk(payload):
    """A parser that raises turns a shape change into a crash mid-run."""
    assert R.parse_endeavour(payload) == []


def test_store_suggestion_fixture_is_how_the_ids_were_discovered():
    """Documents the discovery path — and it led somewhere WRONG. See below."""
    data = load("bws_store_suggestion_geraldton.json")
    stores = {s["StoreId"]: s["StoreName"] for s in data["Stores"]["Suggestions"]}
    assert "4328" in stores
    assert stores["4328"] == "Geraldton"
    assert all(s["State"] == "WA" for s in data["Stores"]["Suggestions"])


def test_the_autocomplete_ids_are_NOT_fulfilment_ids():
    """The trap that cost the previous session its whole BWS result.

    `/Search/Suggestion?Key=Geraldton` offers 4328, and 4328 is a real id: ask
    `/StoreLocator/Store?StoreNo=4328` and it answers with the correct
    Geraldton shop, address and all. Everything about it looks confirmed.

    It is a store-LOCATOR id. Passed as `fulfilmentStoreId` it returns zero
    products, indistinguishable from a garbage id. The resolution is not a
    guess: that same locator response's OWN `StoreNo` field reads 4083, so
    locator id in, fulfilment id out.
    """
    resolved = load("bws_storelocator_4328_resolves_to_4083.json")
    assert resolved["StoreNo"] == "4083", "asked for 4328, the response identifies itself as 4083"
    assert resolved["Suburb"] == "Geraldton" and resolved["Postcode"] == "6530"

    suggested = {s["StoreId"] for s in load("bws_store_suggestion_geraldton.json")["Stores"]["Suggestions"]}
    fulfilment = {s["StoreNo"] for s in load("bws_wa_stores_geraldton_region.json")["Stores"]}
    # The two id spaces overlap only partly — which is exactly why one can be
    # mistaken for the other and why a lookup must never assume they are equal.
    assert "4328" in suggested and "4328" not in fulfilment
    assert "4083" in fulfilment

    rec = R.get_recipe("https://bws.com.au/")
    for label, sid in rec.stores.items():
        assert sid in fulfilment, f"{label}={sid} is not a fulfilment id in the WA store list"


def test_bws_store_ids_are_all_geraldton_and_click_and_collect():
    """A store id is only useful if it is the right town AND actually trades online."""
    by_no = {s["StoreNo"]: s for s in load("bws_wa_stores_geraldton_region.json")["Stores"]}
    rec = R.get_recipe("https://bws.com.au/")
    assert rec.kind == R.API and rec.store_transport == "query"
    assert rec.store_param == "fulfilmentStoreId"
    assert rec.stores, "the BWS recipe must carry at least one Geraldton store id"
    for label, sid in rec.stores.items():
        store = by_no[sid]
        assert store["Postcode"] == "6530", f"{label}={sid} is not a Geraldton (6530) store"
        assert store["IsClickAndCollectEnabled"] is True, f"{label}={sid} does not trade online"


# ------------------------------------------- the store-scoped search endpoint


def test_search_parser_flattens_the_extra_group_nesting():
    """`/Search/products` nests one level deeper than `/Product/<stockcode>`.

    A parser written against the flatter Product shape returns nothing here,
    silently — no exception, just an empty price list.
    """
    rows = R.parse_endeavour_search(load("bws_search_emu_store4083.json"))
    by_sku = {r["sku"]: r for r in rows}
    assert "59747" in by_sku, "the 30-block must survive the flattening"
    assert by_sku["59747"]["price"] == 67
    assert all(r["name"] and r["sku"] for r in rows)

    # It also tolerates the FLATTER /Product/<stockcode> shape, on purpose: the
    # per-leaf fallback means one parser reads both Endeavour responses rather
    # than the caller having to know which endpoint answered. Asserted rather
    # than left implicit, because it is the kind of tolerance that gets
    # "tidied" away by someone who reads the docstring and not the fallback.
    flat = {r["sku"]: r for r in R.parse_endeavour_search(load("bws_product_38879_storeless.json"))}
    assert set(flat) == {"38879", "69222", "59747"}
    assert flat["59747"]["price"] == 69  # the older capture; see that fixture's note


@pytest.mark.parametrize("payload", [None, {}, [], "", {"Products": None}, {"Products": ["junk"]},
                                     {"Products": [{"Products": None}]}, {"Products": [{"Products": ["x"]}]}])
def test_search_parser_never_raises_on_junk(payload):
    assert R.parse_endeavour_search(payload) == []


def test_the_store_id_actually_changes_the_price():
    """THE CLAIM the bws recipe rests on, pinned to two real captures.

    Without this the recipe is only asserting that a parameter was ACCEPTED.
    Accepted-and-ignored is the default behaviour of most query strings, and it
    is what `/Product/<stockcode>` does with this very parameter.
    """
    geraldton = {r["sku"]: r for r in R.parse_endeavour_search(load("bws_search_emu_store4083.json"))}
    storeless = {r["sku"]: r for r in R.parse_endeavour_search(load("bws_search_emu_storeless.json"))}

    # 8620 (Emu Bitter pack) is on promotion for the anonymous default store and
    # is NOT on promotion in Geraldton. Same query, same second, different shop.
    assert storeless["8620"]["price"] == 22.5 and storeless["8620"]["on_special"] is True
    assert geraldton["8620"]["price"] == 23 and geraldton["8620"]["on_special"] is False

    # ...and the headline 30-block is the SAME in both, which is the other half
    # of the finding: the store knob moves promotions, not the everyday block.
    assert geraldton["59747"]["price"] == storeless["59747"]["price"] == 67


def test_an_unknown_store_id_returns_nothing_rather_than_a_default_price():
    """The negative control. Without it, 'the parameter works' is unfalsifiable.

    A garbage store must produce an EMPTY result. If it produced the default
    price instead, every store-scoped read would silently be a store-less read
    wearing a store id.
    """
    payload = load("bws_search_emu_garbage_store.json")
    assert payload.get("SearchResultsCount") == 0
    assert R.parse_endeavour_search(payload) == []


# --------------------------------------------------------- the Rigters parser


def test_rsgwa_parser_reads_the_typed_jsonld_price_not_the_rendered_one():
    rows = R.parse_rsgwa_jsonld(load("rsgwa_line_emu_export_10x375.json"))
    assert len(rows) == 1
    row = rows[0]
    assert row["price"] == 28.0 and isinstance(row["price"], float)
    assert "Emu Export" in row["name"]
    assert row["available"] is True
    assert row["sku"] == "emu-export-can-10x375ml"


@pytest.mark.parametrize("payload", [None, {}, [], 3, {"html": ""}, {"html": "<p>no ld+json</p>"},
                                     {"html": '<script type="application/ld+json">{bad json</script>'}])
def test_rsgwa_parser_never_raises_on_junk(payload):
    assert R.parse_rsgwa_jsonld(payload) == []


def test_rsgwa_parser_ignores_non_product_jsonld_blocks():
    """The page ships a BreadcrumbList too; picking the first block is a bug."""
    html = ('<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>'
            '<script type="application/ld+json">{"@type":"Product","name":"X",'
            '"url":"https://x/lines/slug","offers":{"@type":"Offer","price":"9.50"}}</script>')
    rows = R.parse_rsgwa_jsonld({"html": html})
    assert [r["name"] for r in rows] == ["X"]
    assert rows[0]["price"] == 9.5


# ------------------------------------------------------- the `none` verdicts


def test_thirsty_camel_is_none_because_the_banner_has_no_wa_stores():
    """`none` earns its place only when the reason is checkable."""
    rec = R.get_recipe("https://www.thirstycamel.com.au/product/x")
    assert rec.kind == R.NONE
    tally = load("thirstycamel_store_regions.json")
    assert tally["total"] == 257
    assert "WA" not in tally["regions"], "a WA store would invalidate this recipe's whole reason"
    assert sum(tally["regions"].values()) == tally["total"]


def test_every_none_recipe_names_what_was_actually_tried():
    """A `none` that just says 'blocked' cannot be re-tested by the next agent.

    Each reason must carry the evidence: a status code, a path count, or a
    measured store tally — something a later run can go and check.
    """
    for domain, rec in R.RECIPES.items():
        if rec.kind != R.NONE:
            continue
        blob = (rec.reason + " " + rec.verified).lower()
        assert any(t in blob for t in ("403", "200", "captcha", "257", "http")), domain
        assert "2026" in rec.verified, domain


def test_walled_retailers_are_not_quietly_marked_confident():
    """The four walled banners must be `none`, so their reads stay LOW CONFIDENCE."""
    for host in ("liquorland.com.au", "firstchoiceliquor.com.au",
                 "bottlemart.com.au", "danmurphys.com.au"):
        assert R.get_recipe("https://www." + host + "/x").kind == R.NONE
