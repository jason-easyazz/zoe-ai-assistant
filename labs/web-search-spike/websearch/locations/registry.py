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

import json
import re
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


def parse_endeavour_search(payload: Any) -> list[dict[str, Any]]:
    """Endeavour `/apis/ui/Search/products` — the STORE-SCOPED sibling.

    Shape differs from `/Product/<stockcode>`: this endpoint nests one level
    deeper, `Products` being a list of pack GROUPS each with its own `Products`
    list. Flattening is safe because every leaf carries its own `Stockcode`.

    THIS is the endpoint that honours a store id; `/Product/<stockcode>` does
    not. See the `bws.com.au` recipe below for the measurement.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return out
    for group in payload.get("Products") or []:
        if not isinstance(group, dict):
            continue
        leaves = group.get("Products")
        for p in (leaves if isinstance(leaves, list) else [group]):
            if not isinstance(p, dict) or not p.get("Stockcode"):
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


_LD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def parse_rsgwa_jsonld(payload: Any) -> list[dict[str, Any]]:
    """Retail Services Group WA (`cellarbrations.rsgwa.com.au`) `/lines/<slug>.json`.

    The endpoint answers `{"html": ..., "modal": ..., "analytics": ...}` — the
    JSON is a transport for markup, not a price API. The price inside it IS
    typed though: a schema.org `Product` block with an `offers.price`. Parsing
    that rather than the rendered `$28.00` means a CSS change cannot silently
    turn a price into an empty string.

    Non-`Product` JSON-LD blocks (BreadcrumbList, Organization) are skipped
    rather than assumed absent — the page ships several.
    """
    out: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        html = payload.get("html") or ""
    elif isinstance(payload, str):
        html = payload
    else:
        return out
    for blob in _LD_RE.findall(html):
        try:
            node = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(node, dict) or node.get("@type") != "Product":
            continue
        offers = node.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            offers = {}
        price = offers.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        url = str(node.get("url") or "")
        out.append(
            {
                "sku": url.rstrip("/").rsplit("/", 1)[-1],
                "name": (node.get("name") or "").strip(),
                "price": price,
                "was_price": None,
                "on_special": False,
                "member_special": False,
                "savings": None,
                "pack": "",
                "unit": offers.get("priceCurrency") or "AUD",
                "stock_on_hand": None,
                "available": "InStock" in str(offers.get("availability") or ""),
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

register(
    Recipe(
        domain="bws.com.au",
        retailer="BWS",
        kind=API,
        verified=(
            "2026-08-05 AWST: /Search/products compared across store-less, 4031, 4083, 4143, "
            "4242, 4996 and a garbage id. Stockcodes 8620/38880 change price with the store "
            "(store-less $22.50/$5.50 IsOnSpecial=True vs Geraldton $23.00/$6.00 "
            "IsOnSpecial=False) and StockOnHand differs per store; the garbage id returns ZERO "
            "products. The same parameter on /Product/<stockcode> changes NOTHING, garbage id "
            "included. Fixtures: bws_search_emu_store4083 / _storeless / _garbage_store."
        ),
        api_url="https://api.bws.com.au/apis/ui/Search/products?searchTerm={sku}&fulfilmentStoreId={store_id}",
        store_transport="query",
        store_param="fulfilmentStoreId",
        # FULFILMENT ids, read from /StoreLocator/Stores/bws?state=WA. NOT the ids the
        # /Search/Suggestion autocomplete returns — see `notes`.
        stores={
            "Geraldton": "4083",          # Centro Stirling, 54 Sanford St, 6530
            "Wonthella": "4143",          # 44 North West Coastal Hwy, 6530
            "Geraldton South": "4242",    # 781 Barrett Drive, Wandina, 6530
            "Bluff Point": "4996",        # 441 Chapman Rd, 6530
        },
        parse=parse_endeavour_search,
        headers={"Origin": "https://bws.com.au", "Referer": "https://bws.com.au/"},
        notes=(
            "TWO TRAPS, both measured.\n"
            "1. `{sku}` here is a SEARCH TERM, not a stockcode: searchTerm=59747 returns zero "
            "   products. Callers pass sku='emu export' and select the pack they want from the "
            "   returned rows by stockcode. A caller that passes a stockcode gets an empty, "
            "   FAILED read — never a wrong number.\n"
            "2. BWS runs TWO id spaces for the same shop. /Search/Suggestion?Key=Geraldton "
            "   returns 4328 and 4560; /StoreLocator/Store?StoreNo=4328 happily answers with the "
            "   correct Geraldton store, so 4328 looks validated. It is not a fulfilment id: "
            "   fulfilmentStoreId=4328 returns zero products, exactly like a garbage id. The "
            "   resolution is exact rather than guessed — that same response's OWN StoreNo field "
            "   reads 4083. Locator id in, fulfilment id out."
        ),
    )
)

register(
    Recipe(
        domain="cellarbrations.rsgwa.com.au",
        retailer="Cellarbrations Rigters Geraldton",
        kind=API,
        verified=(
            "2026-08-05 AWST: GET /lines/<slug>.json answers plain httpx (HTTP 200, no cookies, "
            "no browser) with a schema.org Product carrying offers.price. Emu Export Cans "
            "10x375mL = $28.00. Site <title> is 'Cellarbrations Rigters Geraldton', so the whole "
            "domain is ONE shop. Fixture: rsgwa_line_emu_export_10x375.json."
        ),
        api_url="https://cellarbrations.rsgwa.com.au/lines/{sku}.json",
        store_transport="none",
        stores={"Geraldton": "rigters-geraldton"},
        parse=parse_rsgwa_jsonld,
        notes=(
            "STORE-ACCURATE BY CONSTRUCTION, which is why store_transport is `none` and the "
            "attribution is still `api`. There is no picker to drive and no store id to pass: "
            "this domain serves exactly one shop, the operator's actual competitor on Marine "
            "Terrace. The `stores` entry exists so the locality lookup resolves and the read is "
            "attributed to Geraldton rather than reported store-less.\n"
            "`{sku}` is the URL slug (e.g. `emu-export-can-10x375ml`), discoverable from "
            "/search?q=<term>, which is also plain httpx and server-rendered."
        ),
    )
)

register(
    Recipe(
        domain="thirstycamel.com.au",
        retailer="Thirsty Camel",
        kind=NONE,
        verified=(
            "2026-08-05 AWST: enumerated ALL 257 stores from the site's own backend "
            "(https://production-core-onnsxgivka-ts.a.run.app/stores, the backendRoot declared "
            "in __NEXT_DATA__.runtimeConfig), paginated 10/page over 26 pages. "
            "Fixture: thirstycamel_store_regions.json."
        ),
        reason=(
            "THE BANNER DOES NOT TRADE IN WA. The 257 stores are VIC 124, QLD 45, SA 36, TAS 19, "
            "NSW 18, NSWBR 11, NT 4 — and zero in Western Australia. There is no Geraldton store "
            "to select, so no store-selection recipe can exist. The correct action is not a "
            "better scraper: it is to DROP Thirsty Camel from the Geraldton weekly run. The "
            "2026-08-03 eval read it as a competitor and got 'To view in store availability and "
            "pricing' — which was never a bot wall or a picker problem, just a banner with no "
            "shop within 3,000 km."
        ),
        notes=(
            "Worth keeping registered rather than deleting: the ONLY thing stopping a future run "
            "from re-adding Thirsty Camel is this entry saying, with a citation, why not. Its "
            "backend is wide open (no auth, no bot wall) if the chain ever needs an east-coast "
            "price for a different question."
        ),
    )
)

register(
    Recipe(
        domain="danmurphys.com.au",
        retailer="Dan Murphy's",
        kind=NONE,
        verified=(
            "2026-08-05 AWST: api.danmurphys.com.au returned HTTP 403 (Cloudflare) on ALL FOUR "
            "paths its BWS sibling serves openly — /apis/ui/Product/38879, /Search/products, "
            "/StoreLocator/Stores/dan and /Bootstrap. www.danmurphys.com.au is 403 to httpx too."
        ),
        reason=(
            "Cloudflare-walled to plain httpx on the identical API path that api.bws.com.au "
            "answers in ~0.4 s. Same corporate stack (Endeavour Group), same route names, "
            "different edge policy — so the wall is a deliberate per-brand setting, not a "
            "technical limit. NOT yet demoted to a permanent `none`: the browser tier was not "
            "run this session (the box was at load1 3.2 with 301 MB MemAvailable, below the "
            "floor for launching Chromium beside the voice brain). If the browser tier can reach "
            "it, this becomes `interaction` and the fulfilmentStoreId trick found for BWS is the "
            "first thing to retry on the captured session."
        ),
    )
)

register(
    Recipe(
        domain="liquorland.com.au",
        retailer="Liquorland",
        kind=NONE,
        verified=(
            "2026-08-05 AWST: eleven paths (/, /api/stores, /api/v1/stores, /api/products, "
            "/graphql, /api/store-locator, /stores, /store-locator, /occ/v2/liquorland/stores, "
            "/api/bff/stores, /api/config) ALL returned HTTP 200 with the same ~16 KB body: "
            "<title>ShieldSquare Captcha</title> plus an hCaptcha loader."
        ),
        reason=(
            "Radware/ShieldSquare bot wall that answers HTTP **200**, not 403. This is the "
            "single most dangerous shape in this whole spike — a refusal wearing a success's "
            "clothes. Any tier that trusts a status code, or measures 'did we get bytes back', "
            "scores this as a win and then parses a captcha page for a beer price. There is no "
            "unwalled JSON surface behind it to find; the wall is in front of every path, "
            "including ones that do not exist. Browser tier not run this session (box RAM). "
            "Until it is, a Liquorland number in a Geraldton report is store-less at best."
        ),
    )
)

register(
    Recipe(
        domain="firstchoiceliquor.com.au",
        retailer="First Choice Liquor Market",
        kind=NONE,
        verified=(
            "2026-08-05 AWST: the same eleven paths, the same ~16 KB ShieldSquare captcha body, "
            "the same HTTP 200. Byte-for-byte the Liquorland wall — one platform, two banners."
        ),
        reason=(
            "Radware/ShieldSquare bot wall answering HTTP **200** — identical Coles Liquor "
            "Group platform and identical wall to the liquorland.com.au entry, which carries "
            "the full description of why a refusal wearing a success's clothes is the most "
            "dangerous shape here. Browser tier NOT RUN this session (box RAM), so this "
            "`none` means UNMEASURED at that tier, not tried-and-failed. Registered "
            "separately rather than aliased because they are different shops with different "
            "shelf prices, and the day one of them opens up, the recipes diverge."
        ),
    )
)

register(
    Recipe(
        domain="bottlemart.com.au",
        retailer="Bottlemart",
        kind=NONE,
        verified=(
            "2026-08-05 AWST: /, /api/stores, /wp-json/, /wp-json/wp/v2/pages, /api/store-finder "
            "and /stores all returned HTTP 403 from Cloudflare (~5.7 KB interstitial)."
        ),
        reason=(
            "Cloudflare 403 to plain httpx on every path tried, including the WordPress REST "
            "routes the site's own stack would expose. Honest 403s rather than Liquorland's "
            "200-with-a-captcha, so at least the failure is legible. Browser tier not run this "
            "session (box RAM); that is the next thing to try, and Bottlemart is the highest "
            "priority of the four walled banners because it is a genuine Geraldton competitor "
            "(unlike Thirsty Camel) and unlike Dan Murphy's has no open sibling API to exploit."
        ),
    )
)

