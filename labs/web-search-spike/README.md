---
type: lab-record
status: spike complete — recommendation pending operator review
date: 2026-08-03
---

# web-search-spike — live web lookups + claim-backing

Lab spike for giving Zoe live web lookups ("tickets to Bali atm") and
claim-backing ("are you sure?"), mining **oh-my-pi** (MIT) for its search
provider + scraper design.

**Nothing here is wired to zoe-data, systemd, Docker or CI.** Hand-run only.
The design record and the recommendation are in **[DESIGN.md](DESIGN.md)**.

## Recommendation in one line

**Port the pattern to Python inside zoe-data (Option B), not a Bun sidecar
(Option A)** — oh-my-pi's web layer cannot be lifted (it needs Bun + Rust
natives + puppeteer), the box has ~250 MB free RAM, and Zoe already ships
`httpx` + a live Tavily→ddgs→CloakBrowser chain this extends. See DESIGN.md §1–3.

## What's here

| File | What |
|---|---|
| `websearch/engines.py` | Key-free DuckDuckGo `html/` + `lite/` — plain `httpx`, regex parsing, bot-challenge detection. No browser. |
| `websearch/merge.py` | Cross-engine dedup key, consensus ranking, soft/hard deadline fan-out (from oh-my-pi `public.ts`). |
| `websearch/scrapers.py` | URL-matched structured scrapers: Wikipedia + Hacker News. Pure JSON, no DOM library. |
| `websearch/packet.py` | Token-budgeted result packet for the Gemma 4 E4B brain. **Not** in oh-my-pi — see DESIGN.md §4a. |
| `websearch/claim.py` | Claim-backing query shaping — neutral + contradiction queries. Evidence, never a verdict. |
| `demo.py` | Live demo (read-only network). |
| `probe_engines.py` | Engine reachability matrix — how the DESIGN.md §5 findings were measured. |
| `capture_fixtures.py` | Re-record the offline fixtures when engine markup drifts. |
| `tests/` | 41 offline tests. No network. |

## How to run

Needs only `httpx`, which zoe-data already ships — no venv, no install.

```bash
cd labs/web-search-spike

python3 -m pytest tests -q        # 44 offline tests, ~0.6s, no network
python3 demo.py --replay          # full pipeline on recorded fixtures, no network
python3 demo.py                   # live demo (read-only GETs/POSTs)
python3 demo.py "capital of Australia"
python3 demo.py --claim "Bali is in Thailand"
python3 probe_engines.py          # which key-free engines answer from this box
python3 capture_fixtures.py       # re-record fixtures after markup drift
```

**These tests are LAB-only and carry no `ci_safe` marker.** `pytest.ini` sets
`testpaths = services/zoe-data/tests`, so `labs/` is outside every CI lane;
marking them would claim coverage they do not have.

## Findings

1. **oh-my-pi's web layer is not liftable.** `web/` imports `@oh-my-pi/pi-utils`
   78× and that package hard-depends on `@oh-my-pi/pi-natives` (Rust); every
   key-free engine value-imports the puppeteer browser registry; `Bun.sleep` /
   `Bun.Encoding` pin it to Bun. The *algorithms* port in a few hundred lines —
   which is what this spike did. (DESIGN.md §1)

2. **Only DuckDuckGo answers from this box.** Startpage and Mojeek serve
   captchas, Ecosia 403s, Brave 429s, searx.be challenges. So oh-my-pi's
   five-engine consensus fan-out has one engine to work with here.

3. **DuckDuckGo then blocked us mid-spike.** After ~12 requests in a few
   minutes it began serving **HTTP 202 + `anomaly-modal`** and stayed blocked
   for the rest of the session. Key-free search is a *fallback* tier, not a
   foundation — keep Tavily primary (as `zoe_agent.py` already does).

4. **HTTP 200 is not proof of results.** Mojeek returned 200 with
   `<title>Captcha</title>`; DDG returns 202 with a normal-looking body. A
   parser that merely finds no matches would report "no results" and the brain
   would tell Jason there is nothing — when the lookup actually failed.
   `is_blocked()` raises `EngineBlocked`; a negative-control test pins it.

5. **Structured scrapers were never blocked** and are ~2× faster (Wikipedia
   0.46–0.76 s vs DDG 1.2–1.7 s). Most household "are you sure?" claims are
   Wikipedia-shaped, so **claim checks should hit Wikipedia first**, search
   second.

6. **The result packet had to be built, not borrowed.** oh-my-pi renders for a
   large-context coding model; Zoe's 8k Gemma needs a hard ceiling. 10 results
   → ≤350 tokens, ≤6 lines, hosts not URLs.

7. **Three of my own bugs were silent, not loud** — DDG `lite/` uses
   single-quoted attributes with `class` after `href` (a double-quote regex
   matched nothing and returned **0 results rather than an error**); two
   `claim.py` regexes failed on trailing punctuation ("really?"); and the first
   fixture-trim anchored on the inner `<a>` instead of the enclosing
   `<div class="result">`, silently truncating the top hit out of the recorded
   page. Every one produced plausible-looking output. All three are now pinned
   by tests. The `ddg_html.html` fixture in this commit was captured with the
   buggy trim and is missing its first result — re-run `capture_fixtures.py`
   (now fixed) when DDG is reachable.

## Measured

| | |
|---|---|
| DDG `html/` / `lite/` (when not blocked) | 1.31 s / 1.23 s, 10 results each |
| Wikipedia Action API | 0.46–0.76 s |
| HN Algolia API | 0.58 s |
| Packet size, 10 results in | ≤350 tokens, ≤6 lines |
| Replayed end-to-end packet (`demo.py --replay`) | **267 tokens**, Wikipedia extract + 2 corroborated sources |
| DDG block duration once tripped | **>20 min**, still blocked at end of session |
| New runtime dependencies | **0** (`httpx` already shipped) |
| Resident RAM added | **0** (in-process; box had 216–293 MB free of 15.6 GB) |
| Offline test suite | 44 tests, 0.64 s, no network |

Negative controls run: disabling `is_blocked`, the consensus sort, and the
packet budget cap each turned the relevant tests **red**; restoring them
returned all 44 to green.
