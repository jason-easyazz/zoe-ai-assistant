# [lab] One experiment, then stop

1. Read AGENTS.md, README.md, surface.json, windows.json and runs/<run>/ledger.jsonl.
2. Use the same seed, budget and run directory. Pick ONE untried (knob, value)
   from surface.json; keep all other knobs at the ledger's current best.
3. Apply it only to the candidate dictionary. Never edit runtime files, env files,
   safety guards, auth, erase/forget, model selection or AGENTS.md.
4. Inside a declared window, run the fake measure command in README with
   `--max-experiments 1`. The driver chooses the next seeded, untried idea,
   measures paired turns, reads the verdict, appends one numeric ledger row,
   and writes a proposal on KEEP. It owns those steps; do not append twice.
5. KEEP means retain the candidate in the lab's best dictionary. DISCARD means
   restore the previous dictionary. ABANDON means no evidence; stop the run.
6. Read the row and proposal. Report milliseconds and verdict, labelled [lab].
   Stop. A later invocation may try the next idea inside a window.

No coding engine is invoked by the driver. Any agent can follow this file.
No real replay is authorized by these instructions. Never auto-apply proposals.
