# Free-tier combination comparison — manual review sheet

> **EXAMPLE OUTPUT — a 4-query SMOKE run, not the real corpus run.** Committed so
> the shape of the instrument's output is reviewable before spending a full run.
> Regenerate the real thing with `python3 eval/run_eval.py --all` (25 queries).
> Raw `results/*.json` are run artifacts and stay gitignored.
>
> Two things already visible in this sample, both worth the operator's eye:
> `jina-extract` has a **16.97 s median** — far outside any voice budget, which is
> why it is an enrichment tier and not a lookup tier. And `ddgs+scrapers` scored
> *lower* than `ddgs` alone (0.667 vs 0.917), which is most likely the keyword
> proxy penalising the Wikipedia extract for crowding result snippets out of the
> token budget — exactly the kind of artefact the automatic score cannot be
> trusted to interpret, and the reason the Verdict column is filled in by hand.

- Run: `20260803T075036Z`  ·  corpus v1  ·  4 queries
- Tier health at run time: `{"scrapers": "ready", "tavily-free": "unconfigured (TAVILY_API_KEY unset)", "ddgs": "ready (opportunistic \u2014 blocks are steady state)", "jina": "ready (enrichment only \u2014 ~20 RPM, domain-restricted)"}`

**How to use this.** The automatic `score` is a keyword smoke signal only —
it catches empty/off-topic output, it does not rank answer quality. Fill in
the Verdict column yourself; that is the data the free-vs-paid decision uses.

## Automatic summary

| combo | ok | blocked | empty | err | mean score | median latency |
|---|---|---|---|---|---|---|
| `scrapers` | 4 | 0 | 0 | 0 | 0.5 | 1.57s |
| `ddgs` | 4 | 0 | 0 | 0 | 0.917 | 3.508s |
| `ddgs+scrapers` | 4 | 0 | 0 | 0 | 0.667 | 4.093s |
| `all-free` | 4 | 0 | 0 | 0 | 0.792 | 9.075s |
| `jina-extract` | 4 | 0 | 0 | 0 | 0.667 | 16.974s |

## Per-query verdicts (operator fills Verdict + Notes)

### `scrapers` — Tier 0 alone — Wikipedia search + structured extract

| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |
|---|---|---|---|---|---|---|---|
| fact-01 | factual | capital of Australia | ok | 1.0 | 1.637s |  |  |
| fact-02 | factual | how tall is Mount Kosciuszko | ok | 0.0 | 1.245s |  |  |
| fact-03 | factual | who wrote the Wind in the Willows | ok | 1.0 | 1.502s |  |  |
| fact-04 | factual | boiling point of water at sea level | ok | 0.0 | 1.666s |  |  |

### `ddgs` — ddgs metasearch (18 engines) alone

| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |
|---|---|---|---|---|---|---|---|
| fact-01 | factual | capital of Australia | ok | 1.0 | 3.112s |  |  |
| fact-02 | factual | how tall is Mount Kosciuszko | ok | 0.6666666666666666 | 3.905s |  |  |
| fact-03 | factual | who wrote the Wind in the Willows | ok | 1.0 | 19.209s |  |  |
| fact-04 | factual | boiling point of water at sea level | ok | 1.0 | 2.798s |  |  |

### `ddgs+scrapers` — ddgs + structured enrichment

| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |
|---|---|---|---|---|---|---|---|
| fact-01 | factual | capital of Australia | ok | 1.0 | 4.741s |  |  |
| fact-02 | factual | how tall is Mount Kosciuszko | ok | 0.6666666666666666 | 3.446s |  |  |
| fact-03 | factual | who wrote the Wind in the Willows | ok | 1.0 | 2.804s |  |  |
| fact-04 | factual | boiling point of water at sea level | ok | 0.0 | 5.398s |  |  |

### `all-free` — Every configured free tier, consensus-merged

| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |
|---|---|---|---|---|---|---|---|
| fact-01 | factual | capital of Australia | ok | 1.0 | 14.135s |  |  |
| fact-02 | factual | how tall is Mount Kosciuszko | ok | 0.6666666666666666 | 3.291s |  |  |
| fact-03 | factual | who wrote the Wind in the Willows | ok | 1.0 | 4.014s |  |  |
| fact-04 | factual | boiling point of water at sea level | ok | 0.5 | 16.843s |  |  |

### `jina-extract` — Jina Reader page extraction (enrichment tier)

| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |
|---|---|---|---|---|---|---|---|
| fact-01 | factual | capital of Australia | ok | 1.0 | 9.179s |  |  |
| fact-02 | factual | how tall is Mount Kosciuszko | ok | 0.6666666666666666 | 16.72s |  |  |
| fact-03 | factual | who wrote the Wind in the Willows | ok | 1.0 | 17.384s |  |  |
| fact-04 | factual | boiling point of water at sea level | ok | 0.0 | 17.227s |  |  |

## Decision

- Best free combination for OPEN LOOKUP: 
- Best free combination for CLAIM CHECK: 
- Is any paid tier worth revisiting, and on what evidence: 
