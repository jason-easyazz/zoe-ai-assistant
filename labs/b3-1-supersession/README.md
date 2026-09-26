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
without it. A real-brain score on this fixture is a prod-wiring gate, not a lab
deliverable (the box has ~700 MiB free and a live voice brain).

## Rules pinned by the unit tests beyond the fixture (PR #1692 review rounds)

**Subject and key.** Two facts merge only through a shared REAL attribute key
(`Fact.key()`: persisted `attribute_key` first, else parsed from text); the key
carries the subject, so `person b works at acme` never merges into `person a
works at acme` and a same-name mother and father are two facts. There is no
text-similarity shortcut: two unkeyed texts (`likes hiking` / `likes biking`) go
to the judge, never merge on wording. The judge is shown only neighbours about
the same subject whose key is unknown or equal to the new fact's — it can never
name, and the controller can never retire or overwrite, another person's fact or
a known different attribute of the same person.

**Values.** `same_value` is "equal after normalisation, or detail APPENDED after
the same head" (`globex` ~ `globex as a senior engineer`; `neil` ~ `neil, spelled
n-e-i-l`). A token added before or inside the head (`york` → `new york`) is a
correction and supersedes; the richer-fact rule (mem0) decides only between two
phrasings of the same value. A bare "moved from X to Y" is a residence change;
"moved jobs from", an org suffix, or switched/changed/went → employer.

**Intervals — the interval rule.** The INCOMING fact's start is the boundary of
what is known. Same value over a DISJOINT window is a repeated occurrence and
ADDs a new interval. Same value over an overlapping window: candidates are
filtered by overlap first, then the richest survives and EVERY other overlapping
same-value duplicate is retired (`also_close`; `works at acme` + `is employed by
acme` collapse — the M9 idle-pass shape); the survivor's window becomes the
UNION of the overlapping same-value windows (Acme [2020, 2025) + Acme [2024, ∞)
→ [2020, ∞)). Every conflicting (different-value, overlapping) row closes at the
boundary, linked to the survivor. If a conflicting row ran in the span BEFORE the
boundary, the survivor's earlier period is never destroyed and never left
overlapping it: the union is SPLIT — the existing row keeps its earlier period as
live history, closed at that conflict's start, and the value continues in a NEW
row from the boundary (Globex 2021–, Acme 2023–, Globex again from 2024 →
Globex [2021, 2023), Acme [2023, 2024), Globex [2024, ∞); `Decision.retime`).
A conflict that predates the survivor leaves it an empty window, never an end
before its start. Conflicts not overlapping the incoming window are not that
fact's business and are left alone. `invalidate` closes an old window at the
successor's start, never extends an earlier end, never leaves an end before the
start.

**Transitions.** "switched from X to Y" writes the open `Y` row and closes any
live same-value `X` row that covers the boundary. The closed `X` "from" row is
written ONLY when the store holds no row for that value at all — a same-value
row over an earlier window already records the occurrence, and inventing another
with no start would claim `X` across every other value in between (Acme 2000–2010,
Globex 2012–2024, "switched from Acme" in 2025). When it is written it starts no
earlier than the end of the latest other-value row that ended before the
boundary; if another value is still open at the boundary there is no room, so
nothing is written and the `Y` side supersedes that row instead.
