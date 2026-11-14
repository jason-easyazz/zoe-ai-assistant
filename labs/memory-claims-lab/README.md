# memory-claims-lab

The measurement behind the memory redesign (`docs`: design note section G). The permanent golden set of memory-correction cases lives in
`services/zoe-data/tests/fixtures/memory_claims_golden.json` (schema, ratchet and oracle checks: `tests/test_memory_claims_golden.py`).

`run_baseline.py` drives TODAY'S production path for every case against in-memory doubles and reports:

- PASS: today's observable outcome (rows after, stale re-filing, kind decision, filed health event, log content) equals the case's `expect`.
- FAIL: it differs. The `detail` column says which part.
- NOT_RUNNABLE: the path the case needs does not exist yet (`needs_registry`, `needs_marker`).

```
python3 labs/memory-claims-lab/run_baseline.py                 # text table
python3 labs/memory-claims-lab/run_baseline.py --json out.json  # plus JSON
python3 labs/memory-claims-lab/run_baseline.py --write-baseline # re-record baseline-today.json (review the diff)
```

Run under `flock -x -w 14000 /tmp/zoe-test-lane.lock` like any heavy command. It is a report and is not collected by pytest; the one test that
runs it is `test_the_baseline_runner_reproduces_the_committed_score`. Registry expectations (`registry`, `side_effects`) are recorded for the
redesign and are not scored here. Synthetic data only.

## The redesigned path: `run_claims.py`

`run_claims.py` drives every case through the idle claim pass, the validator and the guarded writer (mode `on`, corrections
and health events) on a scratch store, with a SCRIPTED model that returns the oracle claim and the blind row readings in
`oracle_readings.json`. Its score is what the code does with an honest reader's output: an upper bound for a real model,
to be read beside `baseline-today.json`. The last recorded run is `score-claims.json`.

```
python3 labs/memory-claims-lab/run_claims.py                # text table
python3 labs/memory-claims-lab/run_claims.py --write-score  # re-record score-claims.json (review the diff)
```

Lab-only choices (also in the module text): turn `t` of a case is taken as said `t` seconds later, the case's `flag_turns` are
handed over as marker ids (`trigger` reports how many the old detector alone flags), the value registry is an in-memory
double of the writer's seam, and health events are matched by content. `oracle_readings.json` is written WITHOUT looking at
the correction: a row that says "a dentist appointment" is read as `dentist appointment`.
