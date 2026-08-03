---
type: measurement-record
status: live end-to-end run, 2026-08-03
query: "best price for Emu Export Blocks in Geraldton Western Australia"
---

# Emu Export 30-block, Geraldton WA — the live query, end to end

The Part C run: one real commercial question driven through the whole chain —
`ddgs` discovery -> structured scrapers -> per-URL read with CloakBrowser
fallback on blocks — with the tier that served each price recorded.

Everything below is **measured on 2026-08-03 (AWST)**, prices in **AUD**, and
each line names the tier that produced it. Where a price could not be got, the
row says which store's page defeated which tier — that is a result, not a gap.

## THE ANSWER

**Cheapest Emu Export 30 x 375mL block found: $59.99 at Con's Liquor,
Geraldton.**

| # | Store | Price | Pack | Tier that served it | Notes |
|---|---|---|---|---|---|
| 1 | **Con's Liquor, Geraldton** | **$59.99** | 30 x 375mL block | **httpx** (cheap tier — no browser needed) | Listed "205 in stock", SKU 17579. A physical Geraldton competitor with a real per-product page. |
| 2 | **Cellarbrations (Rigters), Geraldton** | **$63.00** (was $72.00, save $9.00) | 30 x 375mL cans | **cloakbrowser** | On the store's current beer-specials block. httpx returned a 2,333-char nav shell with no prices; the browser tier rendered the specials list. |
| 3 | Liquorland (online) | **$64.00** (was $67.00, save $3.00) | Block Can 375mL, Pack (30) | **cloakbrowser** | httpx BLOCKED — Cloudflare challenge body served with HTTP 200. |
| 3= | First Choice Liquor (online) | **$64.00** (was $67.00, save $3.00) | Block Can 375mL, Pack (30) | **cloakbrowser** | Identical page and identical price to Liquorland — same Coles Liquor catalogue, SKU 6517858. |
| 5 | BWS (online) | **$67.00** | "Case" (= 30 cans; also Can $6, Pack (6) $23) | **cloakbrowser** | httpx BLOCKED with HTTP 403 (Akamai). |
| — | Dan Murphy's | **no price on the page** | — | cloakbrowser rendered it (2,757 chars) | The product article rendered fully — description, specs, 138 reviews — with **no price element**. Endeavour prices this SKU per store/member; the number is not in the document. |
| — | Coles | **"Currently unavailable"** | — | httpx AND cloakbrowser both rendered it | Not a block: the page loads and explicitly says the line is unavailable. Coles also states "Product price is displayed based on location information." |
| — | Thirsty Camel | **store-locator gated** | — | httpx AND cloakbrowser both rendered it | The page renders and then says, in place of a price: *"To view in store availability and pricing for this product:"*. See "What defeated what". |
| — | Bottlemart | **store-locator gated** | — | cloakbrowser (httpx 403) | Landing page renders promotions only. Product pricing sits behind "MY STORE / Your Store". |

### Secondary, lower-confidence sighting

`ddgs` discovery surfaced a Facebook post from **Ravenswood Hotel Bottlemart**
advertising *"Emu Export Blocks – only $57.99"*. It is **not used in the ranking
above** and should not be: Ravenswood is ~400 km from Geraldton, the post
carries no date we could verify, and Facebook was never fetched. It is recorded
because a Bottlemart member store running $57.99 makes it plausible that the
**Geraldton Bottlemart is under $59.99** — which is a reason to check that store
by phone, not a price to quote.

## Caveats — read these before acting on the table

1. **Rows 3-5 are NATIONAL ONLINE prices, not Geraldton shelf prices.** BWS,
   Liquorland, First Choice and Dan Murphy's all price per store. BWS's own page
   says "Check availability in your local store"; Coles says the price "is
   displayed based on location information". We fetched with no store selected
   and no WA geolocation, so those are the default/unscoped numbers. **The
   Geraldton shelf price at those banners may differ, and the chain cannot see
   it without driving a store picker.**
2. **Only rows 1 and 2 are genuinely Geraldton.** Con's Liquor and Cellarbrations
   Rigters are physical Geraldton stores publishing per-store prices. Those are
   the two numbers a Geraldton operator should treat as competitive intelligence.
3. **Member-only pricing is invisible here.** Dan Murphy's showed "MEMBER OFFER"
   pricing on adjacent products (e.g. Swan Draught $57.90 member / $57.95
   non-member). A logged-out fetch systematically misses member prices, which at
   the majors are often the advertised ones.
4. **Specials are dated and this is a snapshot.** The Cellarbrations $63.00 is a
   "save $9.00" special; the Liquorland/First Choice $64.00 is "was $67.00".
   Both expire. Re-run before quoting.
5. **`$67` at BWS is labelled "Case".** BWS product 38879 and Dan Murphy's
   DM_38879 are the same SKU, and Dan Murphy's slug is
   `emu-export-30-block-cans-375ml`, so "Case" = the 30-can block. That is an
   inference from two pages agreeing, not from a label that says "30".
6. **Tavily contributed nothing.** Its free daily budget was already spent
   (33/33) before this run. See below — that is a designed degradation, and it
   is recorded rather than silent.

## What defeated what — the tier attribution

This is the part the fallback chain exists to make sayable.

| Store | httpx | jina | cloakbrowser |
|---|---|---|---|
| Con's Liquor (Geraldton) | **PRICE** | blocked (Cloudflare "Checking the site connection security") | PRICE |
| Cellarbrations (Geraldton) | thin shell, no prices | rendered but price not near target | **PRICE** |
| Liquorland | **BLOCKED** — Cloudflare challenge at HTTP 200 | 18k chars of nav chrome, no price | **PRICE** |
| First Choice | **BLOCKED** — Cloudflare challenge at HTTP 200 | price present | **PRICE** |
| BWS | **BLOCKED** — HTTP 403 (Akamai) | 20k chars, 278 `$` tokens, **no product price** | **PRICE** |
| Dan Murphy's | **BLOCKED** — HTTP 403 | target absent entirely | rendered, no price in document |
| Coles | rendered | 37 chars ("A 1x1 image, likely be a tacker probe") | rendered — "Currently unavailable" |
| Thirsty Camel | rendered, no price | 37 chars | rendered, no price |
| Bottlemart | **BLOCKED** — HTTP 403 | rendered | rendered, no product price |

**Two stores are genuinely ungettable without JS-heavy store-locator
interaction, and it is worth naming them precisely:**

- **Thirsty Camel** (`thirstycamel.com.au/product/emu-export-can-block/c4911a11ea`)
  defeated **every tier including CloakBrowser**. The page is not walled — all
  three tiers rendered it — and it simply does not contain a price. In place of
  one it prints *"To view in store availability and pricing for this product:"*
  followed by a store picker. A browser that only navigates cannot beat this;
  it needs a browser that **clicks**, selects a WA store, and waits for the
  re-render. That is a different capability from the one measured tonight.
- **Bottlemart** (`bottlemart.com.au`) is the same class, one step earlier: it
  blocks plain httpx with a 403, CloakBrowser renders the landing page fine, and
  the landing page contains promotions and a "MY STORE" selector — no catalogue
  and no product URL to fall back to. `ddgs` could not surface a Bottlemart
  product URL either, which is consistent: there may not be one.

**Dan Murphy's is a third, distinct failure and should not be lumped in.** It is
not locator-gated and not walled at the browser tier — the article rendered
completely. The price is simply not in the DOM for a logged-out, store-less
session. That is an *account/geo* limit, not a *rendering* limit.

## The chain's own trail, verbatim

Running the real `fetch_url()` over the same URLs — this is the provenance a
caller gets, not a reconstruction:

```
bws-product      httpx: blocked (HTTP 403) [0 chars, 0.2s]
              -> cloakbrowser: ok (semantic:<main>; settle: networkidle=timeout;
                 settle=slept) [8607 chars, 20.6s]                      SERVED

liquorland       httpx: thin (255 chars < floor 600) [255 chars, 0.6s]
              -> cloakbrowser: ok (scored-container; settle: …) [4111 chars]  SERVED

cellarbrations   httpx: thin (759 chars cleared the floor but FAILED the
                 caller's accept()) [759 chars, 0.7s]
              -> cloakbrowser: ok (scored-container; settle: networkidle=idle;
                 settle=slept) [4915 chars, 8.1s]                       SERVED

consliquor       httpx: ok (HTTP 200, 258580B html, scored-container)
                 [1513 chars, 1.3s]                                     SERVED
                 -- no browser launched
```

Four of the twelve corpus URLs were served by `httpx` alone, so **no Chromium
was launched for a third of the corpus**. That is the cost control working in
the wild rather than only in the spy test — and on the box that runs the voice
brain, it is the property that makes the tier acceptable at all.

The `cellarbrations` line is the one worth staring at. Its httpx read cleared
the 600-character floor and would have been ACCEPTED — 759 characters of store
chrome with no price in it — had the caller not supplied `accept=`. The chain
would have reported a confident success and served the wrong bytes. That is the
harness's own headline rule (a refusal must not look like a success) reappearing
one level up, as a *threshold* that can be satisfied without the thing it stands
for.

## Tavily degradation — the designed path, and it was exercised

The operator's expectation held: Tavily was budget-exhausted for the day and the
chain degraded past it cleanly.

```
tier_status()["tavily-free"] -> "ready (0/33 left today)"
_search_tiers()             -> failures["tavily-free"] =
    "budget-exhausted (local daily cap 33/33 spent) — degrading to the free
     tiers, as designed"
```

This is a **behaviour change made during this task**, and it matters. Before it,
Tavily's three distinct non-answers — never configured, budget spent, genuine
API failure — all arrived as one `TavilyX: ...` string out of `fan_out`, so the
one state the chain is *designed* to survive was indistinguishable from the two
that indicate something is wrong. `budget-exhausted` is now pre-checked and
labelled before the fan-out, which also means the exhausted tier is not even
dispatched: no wasted request, no wasted 10 s deadline slot.

Discovery therefore ran on `ddgs` alone, and `ddgs` was healthy throughout —
zero `EnginesBlocked` across the whole session. Per DESIGN.md §5 that is a bonus,
not something to plan around.

## Reproducing

```bash
cd labs/web-search-spike
export ZOE_BROWSER_BROKER_PATH=/path/to/feat-browser-broker-text-extraction/services/zoe-data/browser_broker.py

python3 eval/run_botwall.py --tier httpx
python3 eval/run_botwall.py --tier jina
systemd-run --user --scope -p MemoryMax=1536M -- python3 eval/run_botwall.py --tier cloakbrowser
python3 eval/run_botwall.py --report
```

A run writes the full extracted text for every URL/tier pair to
`eval/results/botwall-text/` (~360 kB over 40 files) and the raw per-tier
verdicts to `eval/results/botwall-store.json`. **Neither is committed** —
`.gitignore` already excludes `*.json`, and 40 files of scraped third-party
retail copy is neither reviewable in a PR nor ours to vendor. What IS committed
is the corpus (the inputs), `botwall-20260803T141253Z.md` (the rendered table,
including a 200-character sample and the price tokens found per tier), and this
record. Re-running regenerates the rest in about 12 minutes.
