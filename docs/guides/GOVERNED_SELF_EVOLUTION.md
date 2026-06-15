# Governed Self-Evolution

Zoe can improve its own software only through a governed engineering loop. The
agent or executor is never the workflow authority.

## Contract

- Multica is the operator-facing ticket system.
- Zoe owns process state in an append-only journal.
- Executors such as Hermes, Pi Agent, or future agents receive one bounded phase.
- A phase advances only after Zoe records required evidence.
- Broad tickets block the parent and produce a split packet instead of continuing.
- PR changes merge only after CI, Greptile confidence, and review-thread gates pass.
- If an external review gate is unavailable or cancelled, dispatch stays paused
  and the PR does not merge.
- Risky or destructive work requires operator approval.

## Current Executor

Hermes Kanban remains the current executor adapter. New v4 engineering runs create
one ready phase at a time. Legacy v2/v3 Kanban chains are still recognized while
existing board work drains.

## Future Executor Swap

Pi Agent can be evaluated later as another executor adapter. It should not replace
Zoe's journal, outcome matrix, Multica ticket contract, or evidence gates. A safe
swap means:

- the same Multica issue produces the same journal events;
- the same evidence requirements gate phase advancement;
- the same Greptile/CI merge guard applies;
- the executor can be disabled without corrupting run state.

## Not Allowed

- no unattended recursive self-modification;
- no prompt-only phase advancement;
- no full Kanban chain creation for new v4 runs;
- no production dispatch re-enable until controlled E2E scenarios pass.
