# plan-loop lab

The frozen corpus and harness for the bounded plan-act-verify loop (`services/zoe-data/plan_loop*.py`, flag-dark
`ZOE_PLAN_LOOP`; record: [docs/knowledge/plan-loop.md](../../docs/knowledge/plan-loop.md)).

- `corpus.json` - 55 invented multi-part asks across reminders, lists, calendar, timers, memory and the house, with the plan a
  scripted planner returns for each, tool faults (fail, reject, lie, hang, write then hang, error, silent write, injected read)
  and the outcome that must come out. Includes the adversarial set: a child's grown-up action inside a two-part ask, a guest, an
  unrecognised voice, an unverified member, deleting / emptying a household list, the same tool twice, a step that names
  another member, planted instructions in a list item and a calendar title, an eight-step plan, a medical turn, a planner that
  answers in prose.
- `harness.py` - runs every ask through the real planner seam, gate, executor core, renderer and write-claim guard with in-memory
  tool fakes, and prints the numbers. Ground truth is the fake world (`World.effects`), not the loop's own bookkeeping.
- `test_harness.py` - the corpus comes out as frozen with zero false claims; two negative controls (no receipt check, no gate)
  that the harness must catch.

```
python3 labs/plan-loop/harness.py            # table + numbers
python3 labs/plan-loop/harness.py --replies  # also what was said
python3 labs/plan-loop/harness.py --check    # exit 1 on a false claim, a repeat send, or an ask not as frozen
```

No live service, database, network or model. The flag is set for the harness process only.

What the lab does NOT measure: the real Gemma brain's plans (the planner here is scripted), the real tools' receipts against a
real database, and latency on the box. Those are the shadow-mode numbers (`PLAN_LOOP_PLAN` / `PLAN_LOOP` log lines).
