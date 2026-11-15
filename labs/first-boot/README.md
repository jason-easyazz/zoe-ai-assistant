# labs/first-boot: the first-boot simulator

Drives the first-boot state machine (`services/zoe-data/first_boot.py`), the probe that reads it from the tables
(`first_boot_probe.py`) and the flow script (`lexicons_data/first_boot.json` + `first_boot_en.json`) against a throwaway
SQLite store, with a scripted person. Design: [docs/architecture/first-boot.md](../../docs/architecture/first-boot.md).

Lab only: hand-run, never imported by a service, never in CI. It opens no live database, loads no model, makes no network
call, and sets the few environment variables it simulates (`ZOE_TIMEZONE`, `ZOE_FORGET_LEDGER_SALT`) only in its own process,
restoring them afterwards.

```bash
python3 labs/first-boot/sim.py            # every scenario; the mechanisms each step needs are assumed to exist
python3 labs/first-boot/sim.py --today    # steps whose mechanism is 'missing' today fail, so you see where a buyer stops
python3 labs/first-boot/sim.py --json     # the full report, with each scenario's trail
python3 labs/first-boot/test_sim.py       # the lab's own checks (plain asserts, no pytest needed)
```

On the Zoe box, run it under the light lock: `flock -x -w 14000 /tmp/zoe-precheck.lock python3 labs/first-boot/sim.py`.

## What it reports

| number | meaning | must be |
|---|---|---|
| `ready` / `acts->ready` | the household is working (every blocking item done, every deferrable one done or deferred), and how many step actions it took | ready in every scenario where the person stays |
| `offers` | steps offered, including re-offers after a pause | |
| `declared` | the script's declared step budgets plus 15 s per extra turn. DECLARED, not measured: no person and no panel were timed | core flow at most 600 s. The ideal person's WHOLE flow, with every deferrable step taken, is 1140 s declared: well past ten minutes, so the deferrable steps must stay deferrable |
| `dead` | a behaviour with no forward transition, or a loop past 80 offers | 0 |
| `help` | the person hit the help card: stuck without outside help | as low as possible; listed by step |
| `parks` | setup paused (silence past the retries, walking away) | |
| `redone` | a step done again after a power loss (state lost) | 0 |

## Scenarios

`scenarios.py`: ideal, says_later, silences, mistakes, child_answers, walks_away, walks_away_for_good, power_loss,
owner_refuses_first. All people are synthetic.
