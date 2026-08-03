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
    """Documents the discovery path, and pins the ids the recipe depends on.

    If BWS renumbers Geraldton, this test still passes (it reads the fixture)
    but `test_bws_store_ids_match_the_registry` below fails — which is the
    correct split: the fixture is a historical record, the registry is a claim
    about now.
    """
    data = load("bws_store_suggestion_geraldton.json")
    stores = {s["StoreId"]: s["StoreName"] for s in data["Stores"]["Suggestions"]}
    assert "4328" in stores
    assert stores["4328"] == "Geraldton"
    assert all(s["State"] == "WA" for s in data["Stores"]["Suggestions"])


def test_bws_store_ids_match_the_registry():
    rec = R.get_recipe("https://bws.com.au/")
    if rec is None or rec.kind != R.API:
        pytest.skip("bws recipe is not api-kind in this build")
    data = load("bws_store_suggestion_geraldton.json")
    discovered = {s["StoreId"] for s in data["Stores"]["Suggestions"]}
    for label, sid in rec.stores.items():
        assert sid in discovered, f"{label}={sid} is not in the captured suggestion response"
