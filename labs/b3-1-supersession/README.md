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

Regression net: `tests/unit/test_b3_1_supersession_lab.py` (`ci_safe`, pure Python).

## Measured (2026-09-26, fake judge)

| run | accuracy | macro P | macro R | historical | richer | paraphrase |
|---|---|---|---|---|---|---|
| controller | 1.00 | 1.00 | 1.00 | 10/10 | 8/8 | 10/10 |
| no overlap check | 0.80 | 0.90 | 0.85 | **0/10** (all SUPERSEDE) | 8/8 | 10/10 |
| no richer rule | 0.72 | 0.81 | 0.75 | 10/10 | **4/8** (distilled wins) | **0/10** |

The fake judge is exercised by exactly the pairs whose new text has no
recognised framing (`Neil, Person A's dad, says hi`); everything else is decided
without it. A real-brain score on this fixture is a prod-wiring gate, not a lab
deliverable (the box has ~700 MiB free and a live voice brain).
