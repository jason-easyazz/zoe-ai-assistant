# labs/anticipation - does Zoe offer what a thoughtful person would?

A measuring lab for the anticipation engine (`services/zoe-data/anticipation_situations.py`, `anticipation_rules.py`,
`anticipation_learning.py`; design in `docs/knowledge/anticipation.md`). Pure and offline: no database, no model, no
network, nothing live. It imports the engine's pure modules read-only and is imported by nothing.

    python3 labs/anticipation/run_lab.py          # the numbers
    python3 labs/anticipation/run_lab.py --json   # the same, machine-readable

## What is in it

- `days.py` - 35 invented household-days (two adults, a child, a guest; placeholder names), each HAND-LABELLED with the
  offers a thoughtful human assistant would make. A label is a rule id, or a `+word` for an offer no rule can make (a
  miss by design). `DEV` (18 days) were visible while the rules were written; `HELD` (17) were labelled before the
  harness first ran and nothing was tuned after reading their results.
- `run_lab.py` - the harness. It prints:
  - precision and recall of the offers against the labels (all, DEV, HELD, and after the 2-a-day attention budget);
  - privacy violations, counted by a checker that does not use the engine's own clamp (must be 0; non-zero exits 1);
  - offers per person per day (mean and the worst case);
  - the learning loop: a simulated person answers every surfaced offer (yes when it is in the labels, no otherwise), one
    household-day per simulated day; precision and recall over all days re-measured after 30, 90 and 180 days.

## Reading the numbers

The labels are one person's judgement on invented days: a compass, not a benchmark. Precision is the number that says
"she does not nag"; recall says "she does not miss"; the privacy count is a floor, not a score. A new rule or a changed
threshold is judged by re-running this and reporting before / after on the HELD days.

Adding a day: write the facts, write the labels BEFORE running, add it to `HELD`.

## The numbers (run of 2026-11-14, [lab], deterministic)

| set | days | offers | labels | precision | recall | privacy violations | offers / person / day | most for one person in a day |
|---|---|---|---|---|---|---|---|---|
| all | 35 | 34 | 35 | 0.941 | 0.914 | 0 | 0.324 | 4 |
| DEV | 18 | 20 | 18 | 0.900 | 1.000 | 0 | 0.370 | 3 |
| HELD (never tuned on) | 17 | 14 | 17 | 1.000 | 0.824 | 0 | 0.275 | 4 |
| all, after the 2-a-day budget | 35 | 30 | 35 | 0.933 | 0.800 | 0 | 0.286 | 2 |

False offers: `leave` 1 (a leave-by for a road trip), `wake` 1 (an earlier alarm for a 7 am call after a late night).
Misses: `wake` 1 (a 6 am flight needs a 2:30 am alarm and the pack never proposes one before 3 am), and two things no rule
can make yet (a morning-of chore nudge when the evening has passed; a reminder for an appointment with no address).

Learning, the labelled household (answers yes exactly where the labels do): precision / recall did NOT move - 0.941 /
0.914 after 30, 90 and 180 simulated days (29, 81, 156 answers). The two false offers are each one no among many yeses
for the same rule, and learning is per rule, so nothing goes quiet. The loop does not fix a rule that is right most of the
time; a narrower rule does.

Learning, a household that never wants a leave-by (scored against its own wishes; recall stays 0.885):

| simulated days | answers | offers | precision | rules gone quiet |
|---|---|---|---|---|
| 0 | 0 | 34 | 0.676 | - |
| 30 | 28 | 25 | 0.920 | ava:leave, ben:leave |
| 90 | 70 | 28 | 0.821 | ava:leave, kit:leave |
| 180 | 130 | 24 | 0.958 | ava:leave, ben:leave, kit:leave |

The dip at 90 days is the design: after 60 quiet days a rule is offered once more, and one more no silences it again.

Caveats: 35 days, one labeller who also wrote the rules; DEV recall of 1.0 is what tuning on visible days looks like, so
HELD is the honest row. Nothing here has run against a real calendar.
