"""Per-retailer STORE-LOCATION recipes — "which store is this price for?".

THE PROBLEM, STATED PRECISELY
-----------------------------
`eval/results/emu-export-geraldton-2026-08-03.md` produced a price table and
then had to disown most of it in a caveats section:

    Rows 3-5 are NATIONAL ONLINE prices, not Geraldton shelf prices. ... We
    fetched with no store selected and no WA geolocation, so those are the
    default/unscoped numbers.

A store-less price is not a wrong number — it is a number about a DIFFERENT
QUESTION than the one asked. The operator runs an EDLP bottleshop in Geraldton;
"what does BWS charge somewhere in Australia" does not tell him whether he is
being undercut on Marine Terrace. The failure is the chain's own recurring
theme one level up again: an answer that looks like the answer.

So this package makes the store part of the RESULT rather than an assumption:

    result = fetch_with_location(url, store_ctx=GERALDTON)
    result.attribution.store        -> "BWS Geraldton (store 4707)"
    result.attribution.method       -> "api" | "picker-session" | "store-less"
    result.attribution.confident    -> False when method == "store-less"

`store-less` is never suppressed and never silently upgraded. It is reported,
flagged LOW CONFIDENCE, and allowed — because a store-less number with a warning
beside it is worth more than no number, and worth far less than it looks without
one.

THE THREE RECIPE KINDS, CHEAPEST FIRST
--------------------------------------
====================================================================
kind           what runs                              cost
====================================================================
api            plain httpx + a store id               ~0.3-1.5 s
interaction    one CloakBrowser picker session,       ~30-60 s once
               cookies cached, then plain httpx       per retailer
none           the ordinary chain, store-less         unchanged
====================================================================

`api` is not a nice-to-have. A weekly run over ~40 products across 7 retailers
is 280 fetches; at the browser tier that is hours of Chromium on the box that
runs the voice brain, and at the API tier it is under a minute with no Chromium
at all. Finding the API is the whole economic argument, which is why
`capture.py` (drive the picker once, record what the front end called) comes
before any recipe is written.

WHAT THIS PACKAGE IS NOT
------------------------
It is not wired to zoe-data, systemd, Docker or CI — see `labs/AGENTS.md`. The
production home for a `select_store` capability is `browser_broker.py`'s
plan/executor registry, and that promotion waits on PR #1626 (the broker's text
path) merging first. See the README's promotion section.
"""

from __future__ import annotations

from .cookies import CookieJar, cookie_dir
from .provenance import (
    METHOD_API,
    METHOD_NONE,
    METHOD_PICKER,
    StoreAttribution,
    StoreContext,
    GERALDTON,
)
from .registry import RECIPES, Recipe, domain_of, get_recipe, has_recipe

__all__ = [
    "CookieJar",
    "cookie_dir",
    "GERALDTON",
    "METHOD_API",
    "METHOD_NONE",
    "METHOD_PICKER",
    "RECIPES",
    "Recipe",
    "StoreAttribution",
    "StoreContext",
    "domain_of",
    "get_recipe",
    "has_recipe",
    "fetch_with_location",
    "LocatedRead",
]


def __getattr__(name: str):
    """`fetch_with_location` is imported LAZILY.

    `fetch.py` reaches the chain, which reaches `cloak.py`, which path-imports
    the broker. Importing this package must not do any of that — a caller that
    only wants `get_recipe()` to ask "is there a recipe for this domain?" would
    otherwise pay for the whole browser stack. Same lazy discipline as
    `chain._cloak_fetch`, for the same reason.
    """
    if name in ("fetch_with_location", "LocatedRead"):
        from . import fetch as _fetch

        return getattr(_fetch, name)
    raise AttributeError(name)
