---
type: design-record
status: lab spike — recommendation pending operator review
date: 2026-08-03
source: oh-my-pi (MIT) — packages/coding-agent/src/web/
---

# Web lookups + claim-backing for Zoe — design

Zoe should be able to answer "tickets to Bali atm" from the live web, and to
*check herself* when Jason says "are you sure?". This records what mining
oh-my-pi's web layer actually yields, the sidecar-vs-port decision, and the
answer shape a voice product needs.

**Recommendation: Option B — port the pattern to Python inside Zoe.** Reasons
below; the short version is that Option A's premise (lift the TS layer and run
it) does not survive a dependency check, and the box has no RAM to spare.

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

Three consequences:

1. **`public.ts`'s consensus fan-out is worth less than it looks here.** It
   assumes five engines answer; on this box one does. The dedup + consensus
   code is still right (it merges the two DDG endpoints, and would merge Tavily
   with DDG), but multi-engine agreement is not available for free.
2. **Tavily should stay the primary tier**, with key-free DDG as the free
   fallback — which is exactly the chain `zoe_agent.py` already implements. The
   spike strengthens the existing design rather than replacing it.
3. **Structured scrapers are the reliable tier and were never blocked.** For a
   household assistant, a large share of "are you sure?" claims are
   Wikipedia-shaped facts. **Claim checks should hit Wikipedia first and the
   search engines second** — faster (0.46–0.76 s vs 1.2–1.7 s), more precise,
   and not subject to bot challenges.

**Corollary for correctness:** a bot challenge arrives as a *success* status
with a plausible body. A parser that merely finds no matches reports "no
results", and the brain then tells Jason there is nothing — when the truth is
the lookup failed. `is_blocked()` raises `EngineBlocked` instead, and a
negative-control test asserts it (`test_bot_challenge_raises_rather_than_returning_empty`).

---

## 6. If this is promoted

Not proposed for merge yet — this is a lab record. The prod shape would be:

1. Lift `_web_search_ddg` out of `zoe_agent.py` into `web_search_provider.py`,
   adding this spike's DDG parser, consensus merge and `EngineBlocked`
   detection to the existing Tavily → ddgs → CloakBrowser chain.
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

Open questions for the operator: whether to fund a Tavily key (the free tier is
1k searches/month) or accept DDG's unreliability; and whether claim-backing
should be automatic on "are you sure?" or an explicit opt-in, given it adds
1–3 s to a spoken turn.
