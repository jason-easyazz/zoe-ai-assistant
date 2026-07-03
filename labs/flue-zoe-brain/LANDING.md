# LANDING — activate_abilities fallback fix (#965, merged — measurement pending)

Operator checklist for the on-box half of #965's verification. The sandbox the
change was built in cannot reach the live services (zoe-data :8000, llama-server
:11434), so the measurement below is the operator's half — it has not been run
yet.

## What changed in #965 (already unit-tested, 38/38 green)

- `src/agents/zoe.ts` — imperative activator doctrine appended to the
  instructions (group catalogue + "you have NO weather/calendar/… knowledge of
  your own; activate first; never claim un-returned data").
- `src/tools/zoe-tools.ts` — activator input schema pinned as a dead-simple
  single-enum object, with the enum annotated.
- `src/tools/tool-groups.ts` — `GROUP_SUMMARY` exported (shared by tool
  description + instructions); widened triggers: washing/laundry/outside →
  weather, "anything on <day>" / "am I free" → calendar.

## On-box measurement (operator)

1. Build and start the sidecar (from `labs/flue-zoe-brain/`, hand-started, lab
   discipline — do NOT touch the live services):

   ```bash
   npm install && npm run build
   ZOE_BRAIN_OPEN=1 ZOE_BRAIN_USER_ID=family-admin ZOE_BRAIN_ALLOW_WRITES=false \
     ZOE_DATA_URL=http://127.0.0.1:8000 PORT=3579 node dist/server.mjs
   ```

2. Run ~10 trigger-free prompts (indirect phrasings that contain no group
   keyword — e.g. "can I dry things on the line right now?", "have we got much
   on at the end of the week?") via `POST /agents/zoe/<sid>`.

3. Score tool_start events via `GET /agents/zoe/<sid>` — count sessions where
   `activate_abilities` (or a pre-disclosed group tool) fired before the
   answer, and read every reply for fabricated tool claims.

4. Re-run `parity/recall_reliability.py` to confirm the doctrine addition did
   not regress recall.

5. **Acceptance = ≥50% activator fire on the trigger-free set, ZERO fabricated
   tool claims, recall ≥90%.**

6. Kill the sidecar by PORT ONLY when done:

   ```bash
   lsof -ti tcp:3579 | xargs -r kill
   ```

   **NEVER `pkill -f`** (it can take out unrelated node processes), and
   **NEVER restart :8000 (zoe-data) or :11434 (llama-server)** as part of this
   test.

## Housekeeping note

A stale git-worktree entry `~/.worktrees/flue-fallback` (branch
`flue/activator-fallback`) exists on the box only as prunable metadata from an
earlier attempt. The operator may inspect it and prune it
(`git worktree prune` / `git worktree remove`); nothing in this PR depends on
it.
