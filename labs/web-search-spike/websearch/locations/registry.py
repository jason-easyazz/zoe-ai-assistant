"""The recipe registry: one entry per retailer domain, keyed by domain.

A RECIPE IS A MEASUREMENT, NOT A PLAN
-------------------------------------
Every entry here carries a `verified` string naming the date and what was
actually run. An entry without one is not a recipe, it is a guess, and a guess
that produces a plausible number is the failure mode this whole spike keeps
running into. `test_every_recipe_is_verified` refuses an unverified entry.

The three kinds, and what makes a retailer land in each:

- **`api`** — the site's own front end calls a JSON endpoint that accepts a
  store identifier, and that endpoint answers a plain `httpx` client. This is
  the prize: sub-second, no Chromium, and the store id makes the attribution
  exact rather than inferred.
- **`interaction`** — no such endpoint is reachable, but the store CAN be
  selected by driving the picker; the resulting session cookies are cached per
  retailer so the dance is paid once per session rather than once per product.
- **`none`** — neither works. The domain falls through to the ordinary chain
  and every price from it is flagged LOW CONFIDENCE. `none` is a real, useful
  answer: it tells the operator to phone that store instead of trusting a
  number, which is better than a confident wrong number.

DOMAIN KEYS ARE REGISTRABLE DOMAINS, MATCHED ON A LABEL BOUNDARY
----------------------------------------------------------------
`www.bws.com.au`, `bws.com.au` and `api.bws.com.au` are one retailer and one
key: `bws.com.au`. Matching is `host == key or host.endswith("." + key)` — the
same label-boundary rule as `cookies.in_scope`, and for the same reason: a
substring match would hand `notbws.com.au` the BWS recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .cookies import host_of, in_scope

#: Recipe kinds. See the module docstring.
API = "api"
INTERACTION = "interaction"
NONE = "none"

KINDS = (API, INTERACTION, NONE)


@dataclass(frozen=True, slots=True)
class Recipe:
    """How to get a STORE-ACCURATE price out of one retailer."""

    domain: str
    retailer: str
    kind: str
    #: Date + what was run. Mandatory — see the module docstring.
    verified: str

    # ---- kind == "api" -------------------------------------------------
    #: URL template. `{sku}` and `{store_id}` are substituted.
    api_url: str = ""
    #: How the store id travels: "query" | "header" | "cookie" | "path" | "none".
    store_transport: str = "none"
    #: Parameter/header/cookie NAME carrying the store id.
    store_param: str = ""
    #: locality label -> the retailer's own store id. Discovered, never guessed.
    stores: dict[str, str] = field(default_factory=dict)
    #: Callable(json) -> list[dict] of {name, price, was_price, sku, pack}.
    parse: Callable[[Any], list[dict[str, Any]]] | None = None
    #: Extra headers the endpoint needs (measured, not assumed).
    headers: dict[str, str] = field(default_factory=dict)

    # ---- kind == "interaction" ----------------------------------------
    #: Name of the picker script in `pickers.py`.
    picker: str = ""

    # ---- kind == "none" ------------------------------------------------
    #: Why not. This is read by a human deciding whether to phone the store.
    reason: str = ""

    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.domain}: unknown kind {self.kind!r}; expected one of {KINDS}")
        if not self.verified.strip():
            raise ValueError(f"{self.domain}: a recipe MUST carry `verified` — see registry docstring")
        if self.kind == API and not self.api_url:
            raise ValueError(f"{self.domain}: kind={API} needs an api_url")
        if self.kind == INTERACTION and not self.picker:
            raise ValueError(f"{self.domain}: kind={INTERACTION} needs a picker name")
        if self.kind == NONE and not self.reason:
            raise ValueError(f"{self.domain}: kind={NONE} MUST say why — that is the deliverable")

    def store_id_for(self, locality: str) -> str:
        """Store id for a locality label, or "" when this recipe has none."""
        if not locality:
            return ""
        low = locality.strip().lower()
        for label, sid in self.stores.items():
            if label.strip().lower() == low:
                return sid
        return ""

    def store_label_for(self, store_id: str) -> str:
        for label, sid in self.stores.items():
            if sid == store_id:
                return label
        return ""


# ------------------------------------------------------------------- parsers
#
# One per API-kind retailer. Kept as plain functions over already-decoded JSON
# so every one of them is testable against a committed fixture with no network:
# `tests/fixtures/locations/*.json` are real captured responses, trimmed.


def parse_endeavour(payload: Any) -> list[dict[str, Any]]:
    """Endeavour Group (BWS, Dan Murphy's) `/apis/ui/Product/<stockcode>`.

    The response is a PACK GROUP: one `Products` entry per pack size sharing a
    parent stockcode — for Emu Export that is the single can (38879), the
    6-pack (69222) and the 30-block (59747). Returning only the first would
    silently answer about a can when the operator asked about a block, so all
    of them come back and the caller picks by stockcode or pack size.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    for p in payload.get("Products") or []:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "sku": str(p.get("Stockcode") or ""),
                "name": (p.get("Name") or "").strip(),
                "price": p.get("Price"),
                "was_price": p.get("WasPrice"),
                "on_special": bool(p.get("IsOnSpecial")),
                "member_special": bool(p.get("IsEdrSpecial")),
                "savings": p.get("SavingsAmount"),
                "pack": p.get("PackageSize"),
                "unit": p.get("Unit"),
                "stock_on_hand": p.get("StockOnHand"),
                "available": p.get("IsAvailable"),
            }
        )
    return out


# ------------------------------------------------------------------ registry
#
# POPULATED FROM MEASUREMENT ONLY. See `docs`-style notes in each entry: the
# `verified` field names the run that established the entry, and `capture.py`
# is how a new one is established.

RECIPES: dict[str, Recipe] = {}


def register(recipe: Recipe) -> Recipe:
    key = host_of(recipe.domain)
    if key in RECIPES:
        raise ValueError(f"duplicate recipe for {key}")
    RECIPES[key] = recipe
    return recipe


def domain_of(url_or_host: str) -> str:
    """The registry KEY for a URL, or "" when no recipe covers it."""
    host = host_of(url_or_host)
    if not host:
        return ""
    for key in RECIPES:
        if in_scope(host, key):
            return key
    return ""


def get_recipe(url_or_host: str) -> Recipe | None:
    """The recipe covering this URL, or None. NEVER raises, never guesses."""
    key = domain_of(url_or_host)
    return RECIPES.get(key) if key else None


def has_recipe(url_or_host: str) -> bool:
    return get_recipe(url_or_host) is not None


# =========================================================================
# THE ENTRIES. Every one of these was established by `capture.py` + `verify.py`
# on the date in its `verified` field. Do not add one from reading a site.
# =========================================================================

