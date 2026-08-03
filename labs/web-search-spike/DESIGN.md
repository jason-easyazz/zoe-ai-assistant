---
type: design-record
status: lab spike — free-only, measurement harness built, corpus run pending
date: 2026-08-03
source: oh-my-pi (MIT) — packages/coding-agent/src/web/
---

# Web lookups + claim-backing for Zoe — design

Zoe should be able to answer "tickets to Bali atm" from the live web, and to
*check herself* when Jason says "are you sure?". This records what mining
oh-my-pi's web layer actually yields, the sidecar-vs-port decision, and the
answer shape a voice product needs.

## Operator decisions (2026-08-03)

1. **FREE-ONLY.** No Tavily PAYGO, no Brave, no paid tiers. Tavily's *free*
   tier (1,000 credits/month ≈ **33 searches/day**) is the primary engine when
   configured; everything else must cost nothing. §8 records the resulting
   tier order.
2. **Choose by MEASUREMENT, not paper reasoning.** Precedent: CloakBrowser
   looked worse on paper for Hermes and tested materially better. So the
   deliverable is not a recommendation dressed as a conclusion — it is
   `eval/`, a comparison harness that runs a fixed corpus against every free
   combination and produces the data. §9.

**Recommendation: Option B — port the pattern to Python inside Zoe** — and
specifically **Option C for the engine tier: use the already-installed `ddgs`
rather than a hand-rolled parser** (§7, revised after measurement). Option A's
premise does not survive a dependency check, and the box has no RAM to spare.

---

## 1. Dependency verdict on the oh-my-pi web layer

**The algorithms are portable. The code is not liftable.** Three independent
blockers, each verified by reading the import graph rather than the README:

| Question | Answer | Evidence |
|---|---|---|
| Depends on `@oh-my-pi/pi-natives` (Rust)? | **Yes, transitively and unavoidably** | `web/` imports `@oh-my-pi/pi-utils` **78×**; `packages/utils/package.json` lists `"@oh-my-pi/pi-natives": "catalog:"` as a hard dependency. `scrapers/types.ts` calls `ptree.combineSignals` — `ptree` is one of the natives-backed modules. |
| Depends on puppeteer? | **Yes, at value level, for every key-free engine** | `duckduckgo/ecosia/startpage/mojeek/google.ts` all import `browserFetch` from `browser-page.ts`, which value-imports `../../../tools/browser/{launch,registry}` → `puppeteer-core`. The `import type { Page }` lines are erased, but the registry import is not. |
| Runs on plain Node? | **No** | `browser-page.ts` calls `Bun.sleep`; `scrapers/types.ts` types a decoder as `Bun.Encoding`. `packages/utils` declares `engines: { bun: ">=1.3.14" }`. |

So "run the TS layer as a sidecar" means shipping a Rust napi module **and**
puppeteer/Chromium **and** Bun onto an aarch64 Jetson, to obtain search logic
that is — measured below — a few hundred lines of regex.

**The genuinely valuable IP, and it all ports:**

1. `duckduckgo.ts` — the no-JS HTML frontend as a POST target, the
   `uddg=` redirect unwrapping, and `anomaly-modal` bot-challenge detection.
   Pure regex, **zero** parsing dependencies. Ported verbatim in
   `websearch/engines.py`.
2. `public.ts` — cross-engine dedup key + **consensus ranking** (rank by how
   many engines returned a URL, then by best per-engine rank) + a **soft/hard
   deadline race**. Ported in `websearch/merge.py`.
3. `scrapers/` — the registry shape: `(url) -> Extract | None`, first handler
   that owns the URL wins. ~10 lines. Ported in `websearch/scrapers.py`.

**What is not worth taking:** ~25 providers of which 19 need API keys; ~75
scrapers overwhelmingly for developer-tool sites (crates.io, hackage, nuget)
a household assistant will never be asked about; and `render.ts`, which
formats results for a large-context coding model — the opposite of what Zoe
needs (§4).

---

## 2. Zoe already has most of this — the real gap is elsewhere

The brief assumed a greenfield build. It is not:

- `services/zoe-data/web_search_provider.py` — live Tavily tier
  (`ZOE_SEARCH_PROVIDER`, `TAVILY_API_KEY`), `httpx`, SSRF-guarded.
- `services/zoe-data/zoe_agent.py` — `_web_search_ddg` (L2851) already chains
  **Tavily → `ddgs` → CloakBrowser**; `web_search`/`web_browse`/
  `deep_web_research` are in `_ALWAYS_ON_TOOLS`, and there is already an
  "are you sure?" path that force-calls `web_search`.
- `services/zoe-data/browser_broker.py` — live, screenshot-only, CloakBrowser
  lazily optional.
- `httpx==0.28.0` and `ddgs>=6.0` are **already declared dependencies**.

The actual gap is that **the Flue brain — the live brain since 2026-07-03
(`ZOE_BRAIN_BACKEND=flue`) — has 19 tools and not one touches the web**, and
`_DISPATCHABLE_INTENTS` (`routers/system.py:2703`) has no web intent. Zoe's web
capability exists in the Python agent that the live brain no longer routes
through. Building a *second* search stack in TypeScript would make that split
permanent.

---

## 3. Sidecar vs port — the decision

| Criterion | A: Bun/Node sidecar | B: port to Python (**recommended**) |
|---|---|---|
| **RAM** | +80–150 MB resident, *plus* Chromium if the browser fallback is real. Measured free memory on the box during this spike: **216–293 MB available of 15.6 GB, with 3.7–5.3 GB already in swap**, and an active RAM-reclamation workstream in PLANS.md. No `flue-*` unit sets `MemoryMax` today, so a new one would also need that discipline retro-fitted. | **0 MB resident.** In-process in zoe-data, which already holds `httpx`. |
| **Dependency surface** | Bun + `@oh-my-pi/pi-natives` (Rust, aarch64 wheels) + puppeteer-core + linkedom + header-generator. | **Zero new dependencies.** Verified: the spike imports only `httpx` (already 0.28.1) and stdlib. |
| **Maintenance** | Tracking an upstream that is one package inside a 19-package Bun monorepo, whose web layer imports 78 symbols from a shared utils package. Not vendorable in a slice. | Copy the *algorithms* once, with attribution. Engine markup drifts either way — and a fixture test catches that in 0.4s. |
| **Latency** | +1 network hop (loopback, ~1 ms) but +cold-start risk and a second process to supervise on a box where the voice brain must never be evicted. | In-process. Measured: Wikipedia 0.46–0.76 s, DDG 1.2–1.7 s. |
| **Fit with what exists** | A second, parallel search stack beside the live Python one. | Extends `web_search_provider.py`, the module whose own header says *"No new dependency: uses httpx, already a service dependency."* |

**Option A is not merely more expensive — its premise fails.** You cannot lift
oh-my-pi's web layer without Bun, Rust natives and puppeteer (§1), so the
sidecar would be a *rewrite in TypeScript*, paying every cost of a resident
process for none of the "just reuse it" benefit.

**Recommendation: B.** Port the three algorithms into
`web_search_provider.py`'s existing chain, and expose it to the Flue brain via
a new read-only `GET /api/web/search` router following the `routers/geo.py`
outbound-fetch precedent (`guarded_urlopen`, rate limit, 429 rather than
queue), guarded by `Depends(require_internal_token)` — the
`/api/memories/for-prompt` pattern. Not via `_DISPATCHABLE_INTENTS`: search
results do not fit its `{intent, ok, result: str}` envelope.

---

## 4. Answer shape for a VOICE product

Two findings drive this, and both cut against copying oh-my-pi's rendering.

### 4a. The packet must be budgeted, not merely short

oh-my-pi's `render.ts` targets a frontier coding model with a huge context
window. Zoe's brain is Gemma 4 E4B at 8k, already carrying a system prompt,
memory packet, tool schemas and history. Ten results of raw markdown would
evict the conversation.

So `websearch/packet.py` enforces the budget in the formatter:

- hard ceiling (default **350 tokens**, conservative 3.2 chars/token divisor);
- ≤4 sources, snippets truncated at a sentence boundary;
- **host, not URL** — the brain says "according to Wikipedia", and must never
  read a URL aloud;
- a structured extract, when available, leads and takes the larger share.

Measured: 10 full results → **≤350 tokens, ≤6 lines**, enforced by a
parametrised test at 120/250/350/600.

### 4b. Claim-backing is a different search from open lookup

Open lookup asks *what is true*. Claim-backing asks *is this specific statement
true* — and **searching the claim verbatim is confirmation bias in query form**:
engines match documents to query terms, so searching "Canberra became capital
in 1913" surfaces pages that share that phrasing, including ones that inherited
the same error. `websearch/claim.py` therefore:

1. strips the hedge to a **neutral topic query** ("are you sure? X is Y" → "X is Y");
2. issues a **contradiction query** by negating the assertion verb ("X is not Y"),
   falling back to `incorrect OR myth OR debunked` when there is no verb;
3. merges both result sets, so a page ranking for *both* gains consensus weight —
   precisely the page that settles it either way;
4. **returns evidence, never a verdict.** A regex that judged truth would be
   worse than the hallucination it replaces. The brain decides.

`is_challenge()` is deliberately narrow — a false positive turns a fresh
question into a slower, differently-shaped claim check.

---

## 5. The finding that should change the plan

**Key-free search is not a reliable foundation, and this is measured, not assumed.**

Of oh-my-pi's key-free engine set, probed from this box on 2026-08-03:

| Engine | Result |
|---|---|
| DuckDuckGo `html/` + `lite/` | **200, real results** (1.31 s / 1.23 s) |
| Startpage | 200 + **captcha** |
| Mojeek | 200 + **`<title>Captcha</title>`** |
| Ecosia | **403** "Ecosia Firewall" |
| Brave | **429** |
| searx.be | 200 + "Verifying your browser…" |

Then, after roughly a dozen requests over a few minutes, **DuckDuckGo itself
began serving HTTP 202 + `anomaly-modal` and stayed blocked for the rest of the
session.** oh-my-pi's own DDG error string says the same thing: *"DuckDuckGo
throttles automated HTML searches from datacenter/shared-egress IPs; configure
a credentialed provider such as Brave, Tavily, Exa, or Kagi."*

External verification (operator, 2026-08-03) confirms this is **documented
steady state, not a bad afternoon**: the ~12-request home-IP block is reported
for `ddgs` across major versions since 2024. So DuckDuckGo availability is a
**bonus, never load-bearing**.

Three consequences:

1. **Single-engine scraping cannot be the foundation.** This is what pushed the
   engine tier onto `ddgs`'s 18-engine metasearch instead of one hand-rolled
   parser (§7).
2. **Tavily FREE is the primary tier**, hard-capped at 33/day (§8), with `ddgs`
   opportunistic beneath it.
3. **Structured scrapers are the reliable tier and were never blocked.** For a
   household assistant, a large share of "are you sure?" claims are
   Wikipedia-shaped facts. **Claim checks hit Wikipedia first and engines
   second** — faster (0.46–0.76 s vs 1.2–1.7 s), more precise, and not subject
   to bot challenges. `check_claim()` implements exactly that ordering.

**Corollary for correctness:** a refusal arrives looking like a success — a bot
challenge is HTTP 200/202 with a plausible body, and `ddgs` reports total
blockage as the literal string `"No results found."`. Either way, code that
merely finds nothing reports "no results", and the brain then tells Jason there
is nothing — when the truth is the lookup failed. Both paths now raise
(`is_blocked()` for raw bodies, `EnginesBlocked` for the `ddgs` tier), each
pinned by a negative-control test.

---

## 6. If this is promoted

Not proposed for merge yet — this is a lab record. The prod shape would be:

1. Fold the block-vs-empty guard and the tier ordering into
   `web_search_provider.py`, alongside the existing `tavily_search_sync`, and
   lift `_web_search_ddg` out of `zoe_agent.py` to join it. **The `ddgs` call
   is the only DuckDuckGo path that survives** (§7).
2. Add `packet.py` as the shared formatter, so every caller is budget-capped.
3. New `services/zoe-data/routers/web.py` — `GET /api/web/search`,
   `Depends(require_internal_token)`, `geo.py` rate-limit + SSRF discipline.
4. New `web_search` / `check_claim` tools in
   `labs/flue-zoe-brain/src/tools/zoe-tools.ts`, plus a `web` group in
   `tool-groups.ts` — **required**, because an ungrouped tool is disclosed on
   *every* call (`tool-groups.ts` L316-320).
5. Flag-dark first (`ZOE_WEB_LOOKUP_ENABLED=false`), and — because this touches
   the brain's tool set on the voice path — **replay-gated against
   `~/.zoe-voice-samples` under `flock /tmp/zoe-voice-harness.lock`** before any
   deploy.

Open question for the operator: whether claim-backing is automatic on "are you
sure?" or explicit opt-in, given it adds 1–3 s to a spoken turn. The free-vs-paid
question is now deferred to `eval/` data rather than argued (§9).

---

## 7. Option C — `ddgs` was already installed, and it wins

**Red-team finding, and it was correct: §1–3 posed a false dichotomy.**
`services/zoe-data/requirements.txt` already ships `ddgs>=6.0` (installed:
**9.14.4**), which has its own `html.duckduckgo.com` parser, a Wikipedia engine,
a ranker and `extract()`. So the real choice was never "lift TypeScript vs
hand-write Python" — there was a third option sitting in the dependency list.

### The measurement that settled it

Taken while `html.duckduckgo.com` was actively serving us 202 + `anomaly-modal`:

```
DDGS().text("capital of Australia", backend="duckduckgo")
    -> DDGSException("No results found.")            (0.75 s)
DDGS().text("capital of Australia", backend="auto")
    -> 5 real results                                 (1.77 s)
```

`ddgs` 9.14.4 is **not** a DuckDuckGo scraper. It is a metasearch aggregator
over 18 engines (`ddgs/engines/`: duckduckgo, bing, brave, google, mojeek,
startpage, wikipedia, yahoo, yandex, grokipedia, …) that fans out on a
`ThreadPoolExecutor`, dedups via `ResultsAggregator` keyed on `href`, and ranks
with `SimpleFilterRanker` (`ddgs/ddgs.py:400-450`). **It answered at the exact
moment our hand-rolled single-engine parser was 100% blocked.**

That is decisive on the operator's own terms — measurement over paper. Our
parser had one engine; `ddgs` has eighteen and already implements the dedup +
ranking we ported from `public.ts`.

### The call

**`ddgs` is the engine tier. The custom DuckDuckGo parser is DELETED** —
`parse_ddg_html`, `parse_ddg_lite`, their transport, and both HTML fixtures are
gone from this branch. Only one DuckDuckGo parser exists in the chain, and it is
the better-tested, better-covered, already-shipped one.

### What survives, and why it is not special pleading

Two gaps are real, both verified against `ddgs` source rather than assumed:

1. **`ddgs` conflates BLOCKED with EMPTY.** `ddgs/ddgs.py:454` is
   `raise DDGSException(err or "No results found.")` — the same exception, and
   frequently the same literal message, whether every engine refused us or the
   query genuinely has no hits. Measured above. Unwrapped, that is precisely the
   failure §5 warns about: Zoe says "there's nothing" when the lookup never
   happened. `engines.search()` disambiguates with a **control query** (a query
   that must have hits; if even that returns nothing we are blocked, not empty)
   and raises `EnginesBlocked` instead. The control verdict is memoised for
   120 s so a failure storm cannot double our request rate into an engine that
   is already throttling us.
2. **`ddgs` gives results no engine attribution.** Nothing in a returned row
   says which of the 18 answered, so the comparison harness cannot report which
   engines are alive. `engines.search_by_backend()` probes each individually for
   `eval/` only — never on the lookup path.

Both are ~40 lines of wrapper over `ddgs`, not a reimplementation of it. That is
the honest boundary: **`ddgs` does the searching; we only add the two things it
does not do.**

---

## 8. Free-only tier order (operator decision)

| tier | what | cost | measured role |
|---|---|---|---|
| **0 — structured scrapers** | Wikipedia + HN JSON APIs | free, unlimited | Never blocked, fastest (0.46–0.76 s). **Tried FIRST for claim checks.** |
| **1 — Tavily FREE** | `api.tavily.com` `search_depth=basic` | 1,000 credits/mo ≈ **33/day** | Primary open-lookup engine *when configured*. **Not configured on this box** — reported as `unconfigured`, never scored as zero. |
| **2 — `ddgs`** | 18-engine metasearch | free | **Opportunistic only.** Home-IP blocks are documented steady state, so availability is a bonus, never load-bearing. |
| **extract — Jina Reader** | `GET https://r.jina.ai/<url>` | free, ~20 RPM | **Enrichment only.** Measured 8.5 s / 133 KB on one Wikipedia page, and **403 `AbuseAlleviationError`** on britannica.com — the anonymous tier is domain-restricted. Never on a spoken critical path. |

The 33/day ceiling is enforced **client-side** (`ZOE_TAVILY_DAILY_BUDGET`,
default 33) because Tavily's API does not return remaining quota. It is a
spend-limiter, not an accountant — it exists so a corpus run cannot burn the
month in an afternoon.

**CloakBrowser note:** `cloakbrowser` 0.3.28 *is* installed, but
`browser_broker.py`'s executor returns a **base64 PNG screenshot only**
(`BrowserEvidence.screenshots`) — no page text — so the broker as it stands
cannot feed a text packet. Text extraction would need
`page.locator("body").inner_text()`, which `mcp_server.py`'s
`cloakbrowser_fetch` already does. Recorded rather than worked around.

---

## 9. The instrument — `eval/`

Per the operator's second decision, the deliverable is the measuring device,
not the verdict. `eval/` runs a **fixed 25-query corpus** (factual, technical,
current-events, local/prices, how-to, and claim pairs with known-true and
known-false claims) against every available free combination, recording per
query: results, latency, status, and a keyword score.

Three properties make it trustworthy rather than merely present:

- **A blocked tier reports BLOCKED**, never a silently shorter list. Status is
  one of `ok | empty | blocked | unconfigured | budget-exhausted | error`.
- **A combination that never succeeded scores `None`, not `0.0`.** Averaging a
  blocked run to zero would rank it "bad" when it is *unmeasured* — a
  distinction the whole exercise depends on.
- **The automatic score is explicitly a smoke signal.** Keyword hit-rate catches
  empty or off-topic output; it cannot rank answer quality. The generated
  markdown sheet has a Verdict column the operator fills in, and that is the
  data the free-vs-paid decision uses.

A 4-query smoke run is committed at `eval/results/EXAMPLE-smoke-run.md`. Two
signals already visible: `jina-extract` has a **16.97 s median** (confirming it
is an enrichment tier, not a lookup tier), and `ddgs+scrapers` scored *lower*
than `ddgs` alone — most likely the keyword proxy penalising the Wikipedia
extract for crowding snippets out of the token budget, which is exactly the kind
of artefact the automatic score must not be trusted to interpret.

**Not run yet: the full corpus.** It is operator-triggered
(`python3 eval/run_eval.py --all`), and the Tavily rows need a key.
