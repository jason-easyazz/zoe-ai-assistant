"""Store attribution — the LOW-CONFIDENCE flagging that the whole package is for."""

from __future__ import annotations

import pytest

from websearch.locations.provenance import (
    METHOD_API,
    METHOD_NONE,
    METHOD_PICKER,
    StoreAttribution,
    StoreContext,
    store_less,
)


def test_store_less_is_never_confident():
    """Confidence is DERIVED from the method, so no construction path can lie.

    An earlier shape let the caller pass `confidence=`; whichever code path was
    most eager would then have declared itself confident. There is deliberately
    no setter to attack here — this test exists to keep it that way.
    """
    att = StoreAttribution(method=METHOD_NONE, store="BWS Geraldton", store_id="4328")
    assert att.confident is False, "a store-less read claimed confidence"
    assert not hasattr(StoreAttribution, "confident_setter")
    with pytest.raises(AttributeError):
        att.confident = True  # type: ignore[misc]


def test_store_less_materialises_its_warning_even_when_nobody_asks():
    att = store_less("no recipe")
    assert att.warnings
    assert "LOW CONFIDENCE" in att.warnings[0]
    assert "Geraldton shelf price may differ" in att.warnings[0]


def test_store_less_does_not_duplicate_its_warning():
    att = StoreAttribution(method=METHOD_NONE, warnings=[])
    first = list(att.warnings)
    att.__post_init__()
    assert att.warnings == first


@pytest.mark.parametrize("method,expected", [
    (METHOD_API, True), (METHOD_PICKER, True), (METHOD_NONE, False),
])
def test_confidence_by_method(method, expected):
    assert StoreAttribution(method=method).confident is expected


def test_unknown_method_is_rejected_loudly():
    with pytest.raises(ValueError, match="unknown attribution method"):
        StoreAttribution(method="probably-geraldton")


def test_line_reads_like_something_a_human_would_accept():
    api = StoreAttribution(method=METHOD_API, store="BWS Geraldton", store_id="4328",
                           detail="query:storeNo")
    assert api.line() == "BWS Geraldton [4328] via api (query:storeNo)"
    assert "LOW CONFIDENCE" in store_less("no picker session").line()


def test_serialisation_carries_the_flag_and_the_warnings():
    d = store_less("nope", asked_for="Geraldton WA 6530").to_dict()
    assert d["confident"] is False
    assert d["method"] == METHOD_NONE
    assert d["asked_for"] == "Geraldton WA 6530"
    assert d["warnings"]


def test_store_context_label_skips_empty_parts():
    assert StoreContext("Geraldton", "6530", "WA").label() == "Geraldton WA 6530"
    assert StoreContext("Geraldton", "6530").label() == "Geraldton 6530"
