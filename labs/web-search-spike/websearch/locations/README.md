---
type: lab-record
status: 7 of 7 retailers measured — 2 api recipes, 5 kind=none; browser tier still unrun
date: 2026-08-05
---

# `websearch/locations/` — per-retailer store-location recipes

**Deliverable target:** "Geraldton prices for a product list, one command,
store-accurate provenance."

The problem this exists for is stated in
[`eval/results/emu-export-geraldton-2026-08-03.md`](../../eval/results/emu-export-geraldton-2026-08-03.md)
§Caveats: BWS / Liquorland / First Choice / Dan Murphy's were all read
**store-less**, so their numbers answer *"what does this banner charge
somewhere in Australia"* rather than *"what does the Geraldton shop charge"*.
For an EDLP operator on Marine Terrace those are different questions, and the
second is the only one worth money.

## Status — all 7 retailers measured

Every retailer now has a `verified` recipe. Two are `api`; five are `none`, each
naming what was tried. The money result is
[`eval/results/geraldton-store-scoped-2026-08-05.md`](../../eval/results/geraldton-store-scoped-2026-08-05.md).

| retailer | kind | how |
|---|---|---|
| **BWS** | `api` | `/apis/ui/Search/products?searchTerm=…&fulfilmentStoreId=…`, plain httpx ~1.9 s. Stores 4083 Geraldton, 4143 Wonthella, 4242 Seacrest, 4996 Bluff Point |
| **Cellarbrations Rigters Geraldton** | `api` | `/lines/<slug>.json` → schema.org JSON-LD price, plain httpx **0.46 s**. Single-store domain, so store-accurate by construction |
| Thirsty Camel | `none` | **no WA stores exist** — all 257 enumerated from the site's own backend |
| Dan Murphy's | `none` | Cloudflare 403 on all 4 API paths its BWS sibling serves openly |
| Liquorland | `none` | ShieldSquare captcha served with **HTTP 200** on 11/11 paths |
| First Choice Liquor | `none` | byte-identical wall to Liquorland (one Coles platform, two banners) |
| Bottlemart | `none` | Cloudflare 403 on 6/6 paths |

### THE FINDING: the store knob IS a query parameter — on the other endpoint

The 2026-08-04 session concluded the opposite ("7 query × 5 header × 5 cookie
candidates, all returning the same price; scoping is server-side session
state"). That was right about `/apis/ui/Product/<stockcode>`, which ignores a
store id entirely, and wrong about BWS. Two things had to be corrected together,
and getting either one wrong reproduces the original negative:

1. **The endpoint.** `/Search/products` honours `fulfilmentStoreId`;
   `/Product/<stockcode>` ignores it — a garbage id there returns the normal
   price.
2. **The id space.** BWS runs **two ids per shop**.
   `/Search/Suggestion?Key=Geraldton` offers **4328**; the fulfilment id is
   **4083**. Passing 4328 returns zero products — identical to a garbage id.

4328 is not a typo and not a dead id: `/StoreLocator/Store?StoreNo=4328` answers
with the correct Geraldton store, address and all, which is exactly why it
survived a session of scrutiny. The resolution is in that same response —
**its own `StoreNo` field reads `4083`**. Locator id in, fulfilment id out.

> A store id that validates on one endpoint and silently returns an empty set on
> another is the same failure family as a picker that lands in the search box.
> The **negative control** is what separates them: a garbage store id must
> return *nothing*. If it returns a price, the parameter is being ignored.

### The other correction: "store-less" was never national

`/apis/ui/Bootstrap` hands an anonymous client a session already pinned to
`FulfilmentStoreID` **4031, "Wembley"** (252 Cambridge St, Wembley WA 6014), and
store 4031 reproduces the "store-less" prices digit for digit. Not selecting a
store does not get a neutral price — it gets one the retailer picked silently.

### NOT YET RUN — the honest gap

- **No browser session ran this round.** The box sat at load1 3.2–3.4 with
  **301 MB MemAvailable** against Chromium's ~553 MB. The four walled banners
  (Liquorland, First Choice, Bottlemart, Dan Murphy's) are `none` because the
  browser tier is unmeasured, **not** because it was tried and failed — each
  `reason` says so.
- That refusal produced a real fix: `preflight()` read **MemFree 532 MB** while
  **MemAvailable was 301 MB**, so the old single floor would have launched. Both
  floors are now checked (`DEFAULT_MIN_AVAILABLE_MB`).
- The BWS **picker** is still uncaptured, and is now much less interesting: the
  query parameter makes a picker session unnecessary for BWS.
- **Rigters has no 30-can block online.** The 2026-08-03 $63.00 came off a
  specials block; re-confirm before quoting it.

## What is here

| file | what |
|---|---|
| `session.py` | `StoreSession` — a **held-open** CloakBrowser context that clicks, types, and **captures every response**. Borrows the broker's extractor, `SettlePolicy` and SSRF guards; vendors nothing. Refuses to launch under 380 MB MemFree **or 700 MB MemAvailable** or over load1 3.0, and hard-caps page loads. |
| `capture.py` | Operator-run **discovery** harness. One picker dance per retailer, prints every JSON call + cookies + localStorage. Output is read by a human; it never writes a recipe. |
| `registry.py` | The recipe registry, keyed by registrable domain, matched on a **label boundary**. `Recipe` refuses an entry without `verified`, and a `kind=none` entry must say *why*. |
| `fetch.py` | `fetch_with_location(url, store_ctx)` — the one entry a caller uses. |
| `provenance.py` | `StoreAttribution`: `api` / `picker-session` / `store-less`, with `confident` **derived** from the method so no path can self-certify. |
| `cookies.py` | Per-retailer jar. Domain-filtered **on write and again on read**; stale jars return nothing rather than a stale store. |
| `pickers.py` | `establish_session()` — the deliberate, operator-scale act of running a picker once and banking the cookies. |

## The three recipe kinds

| kind | what runs | cost |
|---|---|---|
| `api` | plain httpx + a store id | ~0.3–1.5 s |
| `interaction` | one CloakBrowser picker session, cookies cached, then plain httpx | ~30–60 s **once per retailer per session** |
| `none` | the ordinary chain, store-less | unchanged, flagged LOW CONFIDENCE |

A weekly run over ~40 products × 7 retailers is 280 fetches. At the browser
tier that is hours of Chromium beside the mlocked voice brain; at the API tier
it is under a minute with no Chromium at all. **Finding the API is the entire
economic argument**, which is why `capture.py` comes before any recipe.

Measured against that argument, the current registry costs **zero Chromium
launches**: both `api` retailers answer plain httpx, and the five `none`
retailers do not get a picker session at all. A weekly Geraldton run over the
two measured retailers is a couple of seconds.

**A caveat that is easy to get wrong:** for the BWS recipe the `{sku}` slot is a
**search term**, not a stockcode — `searchTerm=59747` returns zero products. A
caller that passes a stockcode gets a **FAILED** read ("api responded but the
parser found no products"), never a wrong number. Pass `sku="emu export"` and
select the pack you want from the returned rows by stockcode.

## Safety properties, each pinned by a test

- **A recipe-less domain is untouched.** It reaches `chain.fetch_url` with the
  caller's own kwargs, and no jar is opened, no API tier runs, no browser is
  considered — three spies, two of which must stay cold
  (`test_recipeless_domain_falls_through_untouched`).
- **A store-less price can never claim confidence**, whoever builds it
  (`test_store_less_is_never_confident`). `confident` is a derived property
  with no setter.
- **An `api` recipe with no store id for the locality still returns
  `store-less`** — the kind of the *recipe* is not evidence about the kind of
  the *answer*.
- **Cookies never cross domains**, asserted in both directions including a
  deliberately **poisoned jar file**, because the file on disk is not a trusted
  input (`test_cookie_jar_never_leaks_across_domains`).
- **A per-product fetch cannot launch Chromium.** An `interaction` recipe with
  no cached session degrades to a labelled store-less read that names
  `establish_session()` as the fix.

## Running it

```bash
cd labs/web-search-spike
python3 -m pytest tests/test_locations_*.py -q     # offline, no network, no Chromium

export ZOE_BROWSER_BROKER_PATH=/path/to/#1626-worktree/services/zoe-data/browser_broker.py
python3 -m websearch.locations.capture --list
systemd-run --user --scope -p MemoryMax=1536M -- \
    python3 -m websearch.locations.capture --retailer bws --out /tmp/cap-bws.json
```

`ZOE_STORE_COOKIE_DIR` relocates the jar (default `~/.zoe-store-sessions`,
outside the repo — these are live third-party session cookies and are neither
committable nor reviewable).

## Promotion — explicitly BLOCKED on #1626

The production home for this is `services/zoe-data/browser_broker.py`'s
**plan/executor registry**, which grows a `select_store` action alongside
`extract_text`. That promotion waits on **PR #1626** (the broker's text path)
merging first — `session.py` path-imports `fetch_page_text`/`SETTLE_SPA`/
`settle_and_extract` from it exactly as `cloak.py` does, so there is one
implementation, not two. **This PR does not touch `services/`.**
