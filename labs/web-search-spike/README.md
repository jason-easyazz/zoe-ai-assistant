---
type: lab-record
status: free-only reframe done; comparison harness built; FULL corpus run DONE 2026-08-03 (eval/results/20260803T112640Z.md) — operator Verdict column still empty
date: 2026-08-03
---

# web-search-spike — live web lookups + claim-backing (FREE-ONLY)

Lab spike for giving Zoe live web lookups ("tickets to Bali atm") and
claim-backing ("are you sure?"), mining **oh-my-pi** (MIT) for its search
provider + scraper design.

**Nothing here is wired to zoe-data, systemd, Docker or CI.** Hand-run only.
Full design record + evidence: **[DESIGN.md](DESIGN.md)**.

## Operator decisions (2026-08-03)

1. **FREE-ONLY** — no Tavily PAYGO, no Brave, no paid tiers. Tavily's *free*
   tier (33 searches/day) is primary when configured; everything else is free.
2. **Choose by MEASUREMENT, not paper reasoning.** So the headline deliverable
   is `eval/` — a comparison harness that runs a fixed corpus against every free
   combination — not a recommendation dressed as a conclusion.

## Tier order

| tier | what | role |
|---|---|---|
| 0 | structured scrapers (Wikipedia, HN JSON APIs) | never blocked, fastest (0.46–0.76 s) — **tried first for claim checks** |
| 1 | Tavily **free** (33/day, locally capped) | primary open lookup *when configured* — **not configured on this box** |
| 2 | `ddgs` 18-engine metasearch | **opportunistic** — home-IP blocks are documented steady state |
| extract | Jina Reader (keyless, ~20 RPM) | **enrichment only** — 16.97 s median measured, domain-restricted |

## What's here

| File | What |
|---|---|
| `websearch/engines.py` | `ddgs` wrapper: blocked-vs-empty disambiguation + per-backend provenance. **No custom DDG parser** — see below. |
| `websearch/tavily.py` | Tavily free tier with a client-side 33/day budget guard. |
| `websearch/extract.py` | Jina Reader keyless page extraction; CloakBrowser availability probe. |
| `websearch/merge.py` | Cross-**tier** dedup + consensus ranking + soft/hard deadline (oh-my-pi `public.ts`). |
| `websearch/scrapers.py` | URL-matched structured scrapers: Wikipedia + Hacker News. Pure JSON, no DOM library. |
| `websearch/packet.py` | Token-budgeted result packet for the Gemma 4 E4B brain. **Not** in oh-my-pi. |
| `websearch/claim.py` | Claim-backing query shaping — neutral + contradiction queries. Evidence, never a verdict. |
| `eval/` | **The instrument.** Fixed 26-query corpus + combination runner + operator scoring sheet. Full-run report: `eval/results/20260803T112640Z.md`. |
| `demo.py`, `probe_engines.py` | Live demo; engine reachability matrix. |
| `tests/` | 45 offline tests. No network. |

## How to run

Needs only `httpx` and `ddgs`, both already zoe-data dependencies — no venv.

```bash
cd labs/web-search-spike

python3 -m pytest tests -q          # 45 offline tests, ~0.7s, no network
python3 demo.py                     # live demo across the free tiers
python3 demo.py --claim "Bali is in Thailand"
python3 probe_engines.py            # which raw engines answer from this box

python3 eval/run_eval.py --list     # combinations + live tier health
python3 eval/run_eval.py --all --limit 4   # smoke run
python3 eval/run_eval.py --all      # the real 26-query corpus run (~35 min)
```

**Tests are LAB-only and carry no `ci_safe` marker.** `pytest.ini` sets
`testpaths = services/zoe-data/tests`, so `labs/` is outside every CI lane;
marking them would claim coverage they do not have. The `ddgs` tier is tested
through an injected fake searcher, so the suite is deterministic whether or not
engines are reachable.

## Findings

1. **oh-my-pi's web layer is not liftable.** `web/` imports `@oh-my-pi/pi-utils`
   78× and that package hard-depends on `@oh-my-pi/pi-natives` (Rust); every
   key-free engine value-imports the puppeteer browser registry; `Bun.sleep` /
   `Bun.Encoding` pin it to Bun. The *algorithms* port; the code does not.

2. **`ddgs` beat my hand-rolled parser, measured — so the parser was deleted.**
   Red-team was right that §1–3 posed a false dichotomy: `ddgs>=6.0` is already
   in `requirements.txt`. Taken while DuckDuckGo was actively blocking us:

   ```
   DDGS().text(q, backend="duckduckgo") -> DDGSException("No results found.")
   DDGS().text(q, backend="auto")       -> 5 real results in 1.77 s
   ```

   `ddgs` 9.14.4 is a **metasearch aggregator over 18 engines** with its own
   dedup and ranker — not a DuckDuckGo scraper. It answered when my
   single-engine parser was 100% dead. `parse_ddg_html`/`parse_ddg_lite` and
   both HTML fixtures are **gone**; one DuckDuckGo parser survives, and it is
   the already-shipped one.

3. **What `ddgs` does NOT do, verified in its source, is worth ~40 lines.**
   (a) `ddgs/ddgs.py:454` raises `DDGSException(err or "No results found.")` for
   **both** "every engine blocked" and "no hits" — so unwrapped, Zoe tells Jason
   "there's nothing" when the lookup never happened. We disambiguate with a
   control query. (b) Results carry no engine attribution, so the harness cannot
   see which engines are alive; `search_by_backend()` probes each, for `eval/`
   only.

4. **Key-free scraping is a fallback, never a foundation.** Only DuckDuckGo
   answered directly (Startpage/Mojeek captcha, Ecosia 403, Brave 429, searx.be
   challenge), then DDG itself blocked after ~12 requests (HTTP **202** +
   `anomaly-modal`) for **>20 min**. Operator-confirmed as documented steady
   state for home IPs since 2024.

5. **A refusal looks like a success.** Mojeek returned 200 with
   `<title>Captcha</title>`; DDG returns 202 with a plausible body; `ddgs`
   returns the literal string "No results found.". Every path now raises rather
   than reporting zero results, each pinned by a negative-control test.

6. **Jina Reader is enrichment, not lookup.** Keyless and dependency-free, but
   **16.97 s median** in the harness, 133 KB for one Wikipedia page, and **403
   `AbuseAlleviationError`** on britannica.com — the anonymous tier is
   domain-restricted.

7. **`browser_broker.py` cannot feed a text packet.** CloakBrowser 0.3.28 is
   installed, but the broker's executor returns a base64 **PNG screenshot only**
   — no page text. Text would need `page.locator("body").inner_text()`, as
   `mcp_server.py`'s `cloakbrowser_fetch` already does. Recorded, not worked
   around.

8. **The result packet had to be built, not borrowed.** oh-my-pi renders for a
   large-context coding model; Zoe's 8k Gemma needs a hard ceiling. 10 results
   → ≤350 tokens, ≤6 lines, hosts not URLs.

9. **Several of my own bugs were silent, not loud** — a double-quote-only regex
   that returned 0 results instead of erroring; two `claim.py` regexes that
   failed on trailing punctuation; a fixture trim that truncated the top result;
   and a test whose own first assertion raised. Every one produced
   plausible-looking output. This is why the negative controls exist.

## Measured

| | |
|---|---|
| `ddgs` metasearch (`backend=auto`) | 1.77 s while single-engine DDG was blocked |
| Wikipedia Action API | 0.46–0.76 s |
| HN Algolia API | 0.58 s |
| Jina Reader | **16.97 s median**, 133 KB/page, 403 on some domains |
| Smoke-run medians (4 queries) | scrapers 1.57 s · ddgs 3.51 s · ddgs+scrapers 4.09 s · all-free 9.08 s · jina 16.97 s |
| **Full-run medians (26 queries, 2026-08-03)** | scrapers **1.20 s** (p90 1.32) · ddgs **4.20 s** (p90 13.69) · ddgs+scrapers **2.94 s** (p90 4.77) · all-free **3.37 s** (p90 13.43) · jina **9.13 s** (p90 19.60, max 31.87) |
| Full-run auto-scores (smoke signal only) | scrapers 0.388 · ddgs 0.917 · ddgs+scrapers 0.881 · all-free 0.833 · jina 0.599; Tavily tiers **unconfigured, unmeasured** |
| Claim check (`check_claim`, 4 fresh samples) | **11.9–26.6 s wall** — the worst returned **0 results**, both shaped queries deadline-exceeded (prior 15.7 s was a mid-range draw) |
| `ddgs` blocks in the full run | **0** across ~104 calls — the documented home-IP block did not reproduce that day |
| Live `look_up()` end to end | 5.28 s, **312 tokens**, 6 results, Wikipedia extract leading |
| Tavily free ceiling | 1,000 credits/mo ≈ **33/day**, enforced client-side |
| New runtime dependencies | **0** (`httpx` + `ddgs` already shipped) |
| Resident RAM added | **0** (in-process; box had 216–293 MB free of 15.6 GB) |
| Offline test suite | 45 tests, 0.69 s, no network |

Negative controls run: disabling block detection, the consensus sort, and the
packet budget cap each turned the relevant tests **red**; restoring them
returned the suite to green.
