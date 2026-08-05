---
type: measurement-record
status: live run, 2026-08-05 AWST — answers caveat 1 of emu-export-geraldton-2026-08-03
query: "store-scoped Geraldton prices, per retailer, store-less vs store-selected"
---

# Geraldton, store-scoped — what the store picker was actually worth

[`emu-export-geraldton-2026-08-03.md`](emu-export-geraldton-2026-08-03.md)
§Caveats opened with the admission that rows 3–5 were *"NATIONAL ONLINE prices,
not Geraldton shelf prices"* and that the chain *"cannot see [the Geraldton
price] without driving a store picker"*. This run answers that, and the answer
is more interesting than "the numbers were wrong".

Everything below is **measured 2026-08-05 AWST**, AUD, via
`websearch.locations.fetch_with_location`. Prices carry a `StoreAttribution`;
nothing here is a store-less number wearing a store label.

## THE HEADLINE

**For headline block prices, BWS's store-less number was already correct — and
for promotional lines it was wrong by 50c in the direction that matters.**

| product | BWS store-less | BWS Geraldton (4083) | delta |
|---|---|---|---|
| Emu Export 30-block cans 375ml | $67.00 SPECIAL | **$67.00 SPECIAL** | $0.00 |
| Emu Export 6-pack cans | $23.00 | **$23.00** | $0.00 |
| Great Northern Super Crisp 30-block | $70.00 SPECIAL | **$70.00 SPECIAL** | $0.00 |
| Carlton Dry 30-block cans | $83.00 | **$83.00** | $0.00 |
| **Emu Bitter 24-pack cans** | **$22.50 SPECIAL** | **$23.00** (no special) | **+$0.50** |
| **Emu Bitter single can** | **$5.50 SPECIAL** | **$6.00** (no special) | **+$0.50** |

The pattern, stated as a rule the operator can use:

> **BWS's everyday block price is set above the store; its PROMOTIONS are set
> per store.** A store-less read gets the block right and silently imports
> another town's discount.

That is the dangerous shape. A store-less read does not fail loudly on the promo
lines — it returns a real, current, correctly-formatted price that belongs to a
shop 400 km away, and flags it `IsOnSpecial: true` when Geraldton has no such
special. An EDLP operator matching that number would be matching a Perth promo.

**Stock on hand is per-store too**, and differs sharply: the Emu Export block
reads 329 units store-less (Wembley) against **2,339 at Geraldton 4083**, 1,739
at Wonthella, 2,039 at Seacrest, 2,969 at Bluff Point.

## The store-less price was never "national"

BWS's `/apis/ui/Bootstrap` hands an anonymous client a fully-formed session with
`FulfilmentInfo.ClickAndCollectDetails.FulfilmentStoreID` = **"4031"**,
`FulfilmentStoreName` = **"Wembley"**, 252 Cambridge Street, Wembley WA 6014.

So the "store-less" read was never unscoped. It was **Wembley's price**, and
store 4031 reproduces it digit for digit across all six products above. The
2026-08-03 run described those numbers as national; they were one Perth suburb's.

This matters beyond BWS: *"we didn't select a store"* does not mean *"we got a
neutral price"*. It means the retailer chose one for us and did not say so.

## Per-retailer verdicts

| retailer | recipe | store ids | what was measured |
|---|---|---|---|
| **BWS** | **`api`** | 4083 Geraldton, 4143 Wonthella, 4242 Seacrest, 4996 Bluff Point | `/apis/ui/Search/products?searchTerm=…&fulfilmentStoreId=…`, plain httpx, ~1.9 s |
| **Cellarbrations Rigters Geraldton** | **`api`** | single-store domain | `/lines/<slug>.json` → schema.org JSON-LD price, plain httpx, **0.46 s** |
| Thirsty Camel | `none` | — | **the banner has no WA stores at all** (257 stores enumerated) |
| Dan Murphy's | `none` | — | Cloudflare 403 on all 4 API paths its BWS sibling serves openly |
| Liquorland | `none` | — | ShieldSquare captcha, **HTTP 200**, on 11/11 paths |
| First Choice Liquor | `none` | — | byte-identical wall to Liquorland |
| Bottlemart | `none` | — | Cloudflare 403 on 6/6 paths |

### Thirsty Camel is not a competitor

The 2026-08-03 run recorded Thirsty Camel as *"store-locator gated"* — the page
rendered and printed *"To view in store availability and pricing"* instead of a
price, which reads like a picker problem worth solving.

It is not. The site's own backend (`backendRoot` from its `__NEXT_DATA__`)
serves an open, unauthenticated store list. Enumerated in full: **257 stores —
VIC 124, QLD 45, SA 36, TAS 19, NSW 18, NSWBR 11, NT 4, and zero in Western
Australia.** There is no Geraldton Thirsty Camel to select. The right action is
to **drop it from the weekly run**, not to build it a picker.

### Rigters was store-accurate all along, and is now 40× cheaper to read

`cellarbrations.rsgwa.com.au` is titled *"Cellarbrations Rigters Geraldton"* —
the whole domain is one shop, so its prices were already Geraldton prices. The
change is cost: 2026-08-03 read it through **CloakBrowser** because httpx
returned a nav shell. Reading `/lines/<slug>.json` instead returns a typed
schema.org price to **plain httpx in 0.46 s**, no Chromium.

Sampled Geraldton shelf prices: Emu Export Cans 10×375mL **$28.00**, Emu Export
Stubbies 24×375mL **$68.00**, Carlton Dry Cans 10×375mL **$35.00**, Great
Northern Super Crisp 6×375mL **$17.50**. Rigters' online range carries **no
30-can block** — the 2026-08-03 $63.00 came off a specials block, so it needs
re-confirming against the current catalogue before being quoted.

## The trap that cost the previous session its BWS result

The 2026-08-04 session recorded a firm negative: *"store scoping is NOT a
query/header/cookie parameter — 7 query names × 5 headers × 5 cookies all
returned the national price"*. That conclusion was **correct about the endpoint
it tested and wrong about BWS**, and the reason is worth keeping:

1. Those sweeps ran against **`/apis/ui/Product/<stockcode>`**, which ignores a
   store id completely — a garbage id returns the same price as a real one.
   `/apis/ui/Search/products` honours it.
2. The store ids used were **4328** and **4560**, taken from
   `/Search/Suggestion?Key=Geraldton`. Those are **store-locator ids, not
   fulfilment ids**. Passed as `fulfilmentStoreId` they return zero products —
   indistinguishable from a garbage id.

And 4328 looks thoroughly validated: `/StoreLocator/Store?StoreNo=4328` answers
with the right shop, right address, right postcode. The give-away is in that
same response — **its own `StoreNo` field reads `4083`**. Locator id in,
fulfilment id out. The mapping is exact and machine-checkable, so nothing here
needs guessing.

Two id spaces for one shop, one of which silently returns an empty result set
instead of an error, is the same family as the previous session's picker landing
in the site search box: **a wrong answer that is shaped exactly like a right
one**. The negative control is what separates them — a garbage store id must
return *nothing*, and if it returns a price instead, the parameter is being
ignored.

## What was NOT run, and why

**No browser session ran.** The box was at load1 3.2–3.4 with **301 MB
MemAvailable** for most of the window; Chromium needs ~553 MB and shares this
machine with the mlocked voice brain. Four walled retailers (Liquorland, First
Choice, Bottlemart, Dan Murphy's) therefore remain `none`.

That refusal produced one code change. `StoreSession.preflight()` checked
**MemFree** against a 380 MB floor — and MemFree read **532 MB** while
MemAvailable read **301 MB**. The single-floor check would have launched
Chromium into a third of the headroom it thought it had, because on this box
MemFree is the *optimistic* instrument, not the conservative one. `preflight()`
now requires both floors and names both numbers when it refuses.

## Confidence

Every price above is `StoreAttribution(method="api")` with an explicit store id,
except where the table says store-less. `confident` is derived from the method
and has no setter, so no path here can self-certify. The four `none` retailers
produce reads that **cannot** be marked confident.
