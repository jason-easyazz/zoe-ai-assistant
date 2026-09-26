# B3.1 lab — bi-temporal supersession + keep-the-richer-fact

Lab spike for program item B3.1 (`docs/architecture/beat-the-bar-2026-program.md`).
Design: `docs/architecture/b3-1-bitemporal-supersession.md`. **Flag-dark, no prod
wiring, no model, no I/O.** This is a record, not a contract.

## What is here

| file | role |
|---|---|
| `bitemporal.py` | the deterministic core: `Fact` (four timestamps + `superseded_by` + `attribute_key`), `intervals_overlap` (Graphiti), `invalidate` (never delete), `attribute_key` (the M7 normalisation), `richer` (mem0), `split_transition`, `reconcile` (the ADD/UPDATE/SUPERSEDE/NONE controller with an injectable `judge`), `apply` (in-memory store, prod write order) |
| `fake_judge.py` | the LLM step's stand-in: same signature/contract as the future Gemma judge, crude entity-overlap heuristic; plus a hallucinating and a crashing judge for the negative controls |
| `fixture.py` | 50 SYNTHETIC pairs (Person A/B/C, invented employers/towns) — 10 correction, 10 paraphrase, 8 richer-vs-distilled, 10 disjoint-history, 7 transition, 5 unrelated |
| `run_fixture.py` | scores the controller + both negative controls; `python3 run_fixture.py` |

| `test_supersession_lab.py` | the regression net (pure Python, hand-run) — it lives HERE, not under `tests/`, because `pytest.ini` `testpaths` and both CI lanes (`validate.yml`'s `pytest tests/unit -m ci_safe`, whose collection imports every `tests/unit` module, and `self-hosted-tests.yml`'s `pytest tests/unit`) never collect `labs/`; that is what keeps the labs contract (lab code is never imported or executed by CI) true |

## Run locally

```bash
# from the repo root (worktree), niced — the box runs a live voice brain
nice -n 15 python3 -m pytest labs/b3-1-supersession -q -p no:cacheprovider
nice -n 15 python3 labs/b3-1-supersession/run_fixture.py   # scoreboard + both negative controls
```

Run both before pushing any change under this directory; the numbers below must
not move, and every negative-control test must still assert its specific wrong
outcome (break the rule it guards → it goes red).

## Measured (2026-09-26, fake judge)

| run | accuracy | macro P | macro R | historical | richer | paraphrase |
|---|---|---|---|---|---|---|
| controller | 1.00 | 1.00 | 1.00 | 10/10 | 8/8 | 10/10 |
| no overlap check | 0.80 | 0.90 | 0.85 | **0/10** (all SUPERSEDE) | 8/8 | 10/10 |
| no richer rule | 0.72 | 0.81 | 0.75 | 10/10 | **4/8** (distilled wins) | **0/10** |

The fake judge is exercised by exactly the pairs whose new text has no
recognised framing (`Neil, Person A's dad, says hi`); everything else is decided
without it. The judge is shown only neighbours about the same subject, so it can
never name — and the controller can never retire or overwrite — another
person's fact.

Rules pinned by the unit tests beyond the fixture (review round 1, PR #1692):
near-duplicate text merges only with a matching subject + attribute key; the
same value over a disjoint window is a repeated occurrence (a new interval, not
a duplicate); `invalidate` closes the old window at the successor's start (never
extends it, never leaves an end before the start); a bare "moved from X to Y" is
a residence change ("moved jobs from" / an org suffix → employer); and a
supersession closes EVERY overlapping contradicting live row (`also_close`), so
a duplicate left by an interrupted new-row-first write is swept on the next
reconcile of that fact. Round 2: the near-dup path needs a REAL matching key
(two unkeyed texts go to the judge, never merge on similarity); the judge is
never shown a known, different attribute of the same person; same-value
candidates are filtered by overlap before richness; `Fact.key()` (persisted
`attribute_key` first) is what reconciliation groups by; the sweep closes
conflicting rows at the INCOMING fact's start, not the surviving row's.

Round 3 — the **interval rule** for a same-value match with conflicting rows:
the INCOMING fact's start is the boundary of what is known. Every conflicting
row closes there; the surviving same-value row's window becomes the UNION of
the incoming window and every overlapping same-value duplicate (Acme [2020,
2025) + Acme [2024, ∞) → [2020, ∞)); and if a closed conflict ran in the span
before the boundary, the survivor yields that span and starts at the boundary —
so no two contradicting rows are ever valid at the same instant. Conflicts that
do not overlap the incoming window are not that fact's business and are left
alone. Also: `_merge` retires EVERY overlapping same-value duplicate (`works at
acme` + `is employed by acme` collapse to the richest, the M9 idle-pass shape);
`same_value` is "equal, or detail APPENDED after the same head" — a token added
before/inside the head (`york` → `new york`) is a correction; and a transition's
closed `from` side is suppressed only by a live same-value row that covers the
transition boundary, never by a disjoint historical occurrence. A real-brain score on this fixture is a prod-wiring gate, not a lab
deliverable (the box has ~700 MiB free and a live voice brain).
