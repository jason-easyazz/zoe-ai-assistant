"""`fetch_with_location` — the ONE entry the chain's callers use.

WHAT IT GUARANTEES
------------------
1. **Every read carries a `StoreAttribution`.** There is no path out of this
   module that returns a price without saying which store it is for, including
   the paths that could not find out. `store-less` is a value, not a gap.
2. **A domain with NO recipe is untouched.** It goes straight to
   `chain.fetch_url` with the caller's own arguments — same tiers, same floor,
   same `accept`, same provenance — and NOTHING in this package runs: no jar is
   opened, no recipe is consulted beyond the single registry lookup, no browser
   is considered. That is pinned by `test_recipeless_domain_falls_through_untouched`
   with spies on all three, because "we added location support and now every
   fetch is slower/different" is the regression that would matter most.
3. **The cheap path stays cheap.** An `api` recipe is one `httpx` GET. It does
   not import the chain, it does not import `cloak`, and therefore it cannot
   launch or even load Chromium. Same lazy discipline as `chain._cloak_fetch`.

WHY THE CHAIN IS NOT SIMPLY WRAPPED
-----------------------------------
An earlier shape ran the chain first and then "enriched" the result with store
context. That is backwards and measurably wasteful: for BWS the store-scoped
JSON endpoint answers in under a second and the HTML page it would have fetched
costs ~20 s of Chromium and then still has to be parsed for a number the API
already returned typed. When a recipe exists, the recipe IS the fetch.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .cookies import CookieJar
from .provenance import (
    GERALDTON,
    METHOD_API,
    METHOD_NONE,
    METHOD_PICKER,
    StoreAttribution,
    StoreContext,
    store_less,
)
from .registry import API, INTERACTION, NONE, Recipe, get_recipe

#: Timeout for the plain-httpx API tier. Generous enough for a retail API on a
#: slow night, short enough that a hung endpoint does not stall a weekly run.
API_TIMEOUT_S = 20.0

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
}


@dataclass(slots=True)
class LocatedRead:
    """One located read. Either `items` (api) or `text` (page) carries the answer."""

    url: str
    recipe_kind: str
    attribution: StoreAttribution
    ok: bool = False
    #: Structured products, `api` recipes only. See `registry.parse_*`.
    items: list[dict[str, Any]] = field(default_factory=list)
    #: Page text, for the chain-backed paths.
    text: str = ""
    title: str = ""
    tier_served: str | None = None
    #: `chain.Hop` list when the chain ran; empty for the API path.
    provenance: list = field(default_factory=list)
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def confident(self) -> bool:
        return self.ok and self.attribution.confident

    def line(self) -> str:
        head = f"{self.url[:70]} [{self.recipe_kind}]"
        if not self.ok:
            return f"{head} FAILED: {self.error}"
        return f"{head} -> {self.attribution.line()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "ok": self.ok,
            "recipe_kind": self.recipe_kind,
            "attribution": self.attribution.to_dict(),
            "items": self.items,
            "chars": len(self.text),
            "title": self.title,
            "tier_served": self.tier_served,
            "elapsed_s": round(self.elapsed_s, 2),
            "error": self.error,
        }


def _api_get(url: str, headers: dict[str, str], *, cookie: str = "") -> Any:
    """One plain GET returning decoded JSON. Raises on refusal — the caller
    turns that into an attribution, never into a silent empty list."""
    import httpx

    hdrs = {**API_HEADERS, **(headers or {})}
    if cookie:
        hdrs["Cookie"] = cookie
    with httpx.Client(timeout=API_TIMEOUT_S, follow_redirects=True, trust_env=False) as client:
        resp = client.get(url, headers=hdrs)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} from {url[:100]}")
    return resp.json()


def _fetch_api(
    recipe: Recipe,
    sku: str,
    store_ctx: StoreContext,
    *,
    getter: Callable[..., Any] = _api_get,
) -> LocatedRead:
    started = time.monotonic()
    store_id = recipe.store_id_for(store_ctx.suburb)
    url = recipe.api_url.format(sku=sku, store_id=store_id)

    headers = dict(recipe.headers)
    cookie = ""
    if store_id:
        if recipe.store_transport == "header":
            headers[recipe.store_param] = store_id
        elif recipe.store_transport == "cookie":
            cookie = f"{recipe.store_param}={store_id}"

    out = LocatedRead(url=url, recipe_kind=API, attribution=store_less("api call not made yet"))
    try:
        payload = getter(url, headers, cookie=cookie)
    except Exception as exc:  # noqa: BLE001
        out.error = f"{type(exc).__name__}: {str(exc)[:160]}"
        out.attribution = store_less(f"api call failed: {out.error}", asked_for=store_ctx.label())
        out.elapsed_s = time.monotonic() - started
        return out

    out.items = recipe.parse(payload) if recipe.parse else []
    out.ok = bool(out.items)
    out.elapsed_s = time.monotonic() - started

    if store_id:
        out.attribution = StoreAttribution(
            method=METHOD_API,
            store=recipe.store_label_for(store_id) or store_ctx.label(),
            store_id=store_id,
            asked_for=store_ctx.label(),
            detail=f"{recipe.store_transport}:{recipe.store_param or '-'} on {recipe.api_url}",
        )
    else:
        # The recipe is `api`, the endpoint answered — but we have no store id
        # for this locality, so the number is the endpoint's DEFAULT scope. That
        # is exactly a store-less price and must be labelled as one, even though
        # it arrived by the "good" path. The kind of the recipe is not evidence
        # about the kind of the answer.
        out.attribution = store_less(
            f"no store id known for {store_ctx.label()!r} in the {recipe.domain} recipe — "
            f"this is the endpoint's default (unscoped) price",
            asked_for=store_ctx.label(),
        )
    if not out.ok and not out.error:
        out.error = "api responded but the parser found no products"
    return out


def _fetch_cached_session(
    recipe: Recipe,
    url: str,
    store_ctx: StoreContext,
    jar: CookieJar,
    *,
    chain_fetch: Callable[..., Any],
    **chain_kwargs,
) -> LocatedRead:
    """`interaction` recipe with a CACHED store selection: plain httpx + cookies.

    This is the whole point of the jar. When no fresh jar exists the result is
    an honest `store-less` read rather than an automatic Chromium launch: a
    browser session is an operator-scale decision (RAM on the voice box), not
    something a per-product fetch gets to make on its own. `capture.py` /
    `establish_session()` is how the jar gets filled, deliberately and once.
    """
    started = time.monotonic()
    meta = jar.meta(recipe.domain)
    cookie = jar.as_header(recipe.domain, target_url=url)

    out = LocatedRead(url=url, recipe_kind=INTERACTION, attribution=store_less("pending"))
    if not cookie:
        out.attribution = store_less(
            f"no fresh picker session cached for {recipe.domain} — run "
            f"`capture.py --retailer ...` to select {store_ctx.label()}, then re-run",
            asked_for=store_ctx.label(),
        )

    result = chain_fetch(url, **chain_kwargs)
    out.ok = bool(getattr(result, "ok", False))
    out.text = getattr(result, "text", "")
    out.title = getattr(result, "title", "")
    out.tier_served = getattr(result, "tier_served", None)
    out.provenance = list(getattr(result, "provenance", []))
    out.elapsed_s = time.monotonic() - started

    if cookie:
        out.attribution = StoreAttribution(
            method=METHOD_PICKER,
            store=str(meta.get("store_label") or store_ctx.label()),
            store_id=str(meta.get("store_id") or ""),
            asked_for=store_ctx.label(),
            detail=f"cached picker session, age {int(meta.get('age_s') or 0)}s",
            locality_in_text=store_ctx.matches(out.text),
        )
        if out.attribution.locality_in_text is False:
            out.attribution.warnings.append(
                f"the page does not mention {store_ctx.suburb!r} — the cached selection "
                f"may have expired server-side even though the cookie is fresh"
            )
    return out


def fetch_with_location(
    target: str,
    *,
    store_ctx: StoreContext = GERALDTON,
    sku: str = "",
    jar: CookieJar | None = None,
    chain_fetch: Callable[..., Any] | None = None,
    api_getter: Callable[..., Any] = _api_get,
    jar_factory: Callable[[], CookieJar] = CookieJar,
    **chain_kwargs,
) -> LocatedRead:
    """Read `target` with the best store attribution its domain allows.

    `target` is a URL. For an `api` recipe the SKU is taken from `sku=` when
    given, otherwise parsed out of the URL by the recipe's own convention.

    The injectable `chain_fetch` / `api_getter` / `jar_factory` exist for the
    tests: every live effect this function can have goes through one of them,
    so a spy can assert not merely that the right one ran but that the others
    did NOT. See `test_recipeless_domain_falls_through_untouched`.
    """
    recipe = get_recipe(target)

    if recipe is None or recipe.kind == NONE:
        # ---- the untouched path. Nothing below this branch may allocate a jar,
        # consult a recipe, or consider a browser.
        fetcher = chain_fetch or _default_chain_fetch
        started = time.monotonic()
        result = fetcher(target, **chain_kwargs)
        detail = (
            f"no location recipe for this domain"
            if recipe is None
            else f"recipe kind=none: {recipe.reason}"
        )
        out = LocatedRead(
            url=target,
            recipe_kind=NONE,
            attribution=store_less(detail, asked_for=store_ctx.label()),
            ok=bool(getattr(result, "ok", False)),
            text=getattr(result, "text", ""),
            title=getattr(result, "title", ""),
            tier_served=getattr(result, "tier_served", None),
            provenance=list(getattr(result, "provenance", [])),
            elapsed_s=time.monotonic() - started,
        )
        return out

    if recipe.kind == API:
        return _fetch_api(recipe, sku or _sku_from_url(target), store_ctx, getter=api_getter)

    # INTERACTION
    return _fetch_cached_session(
        recipe,
        target,
        store_ctx,
        jar or jar_factory(),
        chain_fetch=chain_fetch or _default_chain_fetch,
        **chain_kwargs,
    )


def _default_chain_fetch(url: str, **kwargs):
    """The real chain, imported LAZILY so the api path never loads it."""
    from ..chain import fetch_url

    return fetch_url(url, **kwargs)


def _sku_from_url(url: str) -> str:
    """Last numeric path/underscore segment. Covers both catalogue shapes seen:

    - `bws.com.au/product/38879/emu-export-lager-cans-375ml`  -> 38879
    - `liquorland.com.au/beer-and-cider/emu-...-375ml_6517858` -> 6517858
    """
    tail = url.split("?", 1)[0].rstrip("/")
    for chunk in reversed(tail.split("/")):
        if "_" in chunk and chunk.rsplit("_", 1)[-1].isdigit():
            return chunk.rsplit("_", 1)[-1]
        if chunk.isdigit():
            return chunk
    return ""
