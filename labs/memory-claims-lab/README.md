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
