---
type: lab-record
status: WIP — BWS discovery DONE (api-kind confirmed); 5 retailers not yet captured
date: 2026-08-04
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

## Status — WIP, checkpointed mid-discovery

Committed under a reboot checkpoint order. What is **done and measured** vs
what is **not yet run** is split explicitly below; nothing in the "not yet"
column is guessed at in code, because `Recipe` refuses an entry without a
`verified` string.

### DONE — measured 2026-08-04 AWST

| thing | result |
|---|---|
| **BWS is `api`-kind** | `GET https://api.bws.com.au/apis/ui/Product/<stockcode>` answers **plain httpx**, no cookies, no browser, ~0.4 s, typed JSON with `Price`/`WasPrice`/`IsOnSpecial`/`PackageSize` per pack size |
| **Geraldton BWS store ids DISCOVERED** | `GET /apis/ui/Search/Suggestion?Key=Geraldton` → **4328** "Geraldton" (Centro Stirling, 54 Sanford St), **4083** "Geraldton" (same address), **4560** "Geraldton South (Seacrest)", Wandina WA. All plain httpx. |
| **Full WA store list endpoint** | `GET /apis/ui/StoreLocator/Stores/bws?state=WA&type=allstores&Max=500` (captured from the real `/storelocator` page) |
| **Single store detail** | `GET /apis/ui/StoreLocator/Store?Division=bws&StoreNo=4328` → name/address/trading hours, plain httpx |
| **Dan Murphy's API is Cloudflare-walled to httpx** | `api.danmurphys.com.au/apis/ui/Product/38879` → **HTTP 403** interstitial, unlike its BWS sibling on the identical path. Same corporate stack, different edge policy — that asymmetry is itself a finding. |
| **The store knob is NOT a query/header/cookie parameter** | Measured, not assumed: 7 query names × 5 header names × 5 cookie names against `Product/38879`, comparing the 30-block price (stockcode 59747). **Every one returned `$69`** — identical to the no-context baseline. The scoping is server-side session state, so it must be established, not passed. |

### NOT YET RUN — the honest gap

- The BWS **set-store** call is still uncaptured. Two picker attempts both fell
  into the **site search** box instead (`/search?searchTerm=6530`, then
  `/search?searchTerm=Geraldton` — "No results for 6530"). Recorded rather than
  quietly retried: an input that accepts your text and returns a plausible page
  is the same "refusal wearing a success's clothes" this spike keeps meeting,
  and only the **network capture** exposed it — there was no store call at all
  in either log. Next attempt should drive the `/storelocator` **map/result
  card**, not a text input.
- **Liquorland, First Choice, Thirsty Camel, Bottlemart, Dan Murphy's**: capture
  scripts are written (`capture.py`) but **not yet run**. No recipes registered.
- **Cellarbrations/Rigters** store-accuracy: not yet verified.
- `registry.RECIPES` is therefore **empty**, and two registry tests fail by
  design saying exactly that ("the registry is empty — no retailer was
  measured"). That is the instrument working, not a broken build.

## What is here

| file | what |
|---|---|
| `session.py` | `StoreSession` — a **held-open** CloakBrowser context that clicks, types, and **captures every response**. Borrows the broker's extractor, `SettlePolicy` and SSRF guards; vendors nothing. Refuses to launch under 380 MB MemFree or over load1 3.0, and hard-caps page loads. |
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
