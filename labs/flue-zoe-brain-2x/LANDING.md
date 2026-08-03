# LANDING — operator on-box checklists (sandbox cannot reach live services)

## Per-request identity verification (multi-user)

The sidecar now threads the trusted, seam-forwarded `user_id` (request body)
through an `AsyncLocalStorage` (`src/request-identity.ts`) so every tool call in a
turn acts as **that** user, not the process-wide `ZOE_BRAIN_USER_ID`. Offline
proof is pinned in `test/request_identity.test.ts` + `test/route_identity.test.ts`
(90/90 green). Live half — prove two identities produce two different memory
recalls against the real Gemma brain + zoe-data (scratch port only; do NOT touch
the live `:3578` sidecar):

```bash
npm install && npm run build
ZOE_BRAIN_OPEN=1 ZOE_BRAIN_ALLOW_WRITES=false \
  ZOE_DATA_URL=http://127.0.0.1:8000 PORT=3579 node dist/server.mjs
```

The cleanest proof is at the tool layer (bypasses 4B tool-call flakiness and
zoe-data's message-relevance filter, both of which muddy an end-to-end recall
comparison):

```bash
ZOE_DATA_URL=http://127.0.0.1:8000 node --experimental-strip-types -e '
const { runWithUserId } = await import("./src/request-identity.ts");
const { zoeTools } = await import("./src/tools/zoe-tools.ts");
const recall = zoeTools.find(t => t.name === "recall_memory");
console.log("ADMIN  =>", await runWithUserId("family-admin", () => recall.run({ input: { query: "birthday" } })));
console.log("NOBODY =>", await runWithUserId("zzz-nobody-probe", () => recall.run({ input: { query: "birthday" } })));
'
```

Expect the `family-admin` line to carry real stored memories and the `NOBODY`
line to be `"I don't have anything stored about that yet."` — same tool, same
query, differing only in the bound identity. (An HTTP `?wait=result` recall with
`user_id` in the body also works, but the 4B may not always call the tool or may
fabricate on top of an empty packet, so prefer the tool-layer probe as the gate.)

Kill by port when done: `lsof -ti tcp:3579 | xargs -r kill` (never `pkill -f`).

## Sentinel stream verification (Seam-A streaming mode)

The sidecar now emits the prod text-delta + `__TOOL__`/`__THINKING__` sentinel
stream (cutover blocker #3, docs/architecture/zoe-flue-integration.md §10) as
NDJSON via content negotiation. The byte contract is pinned offline
(`test/sentinel_stream.test.ts`, 59/59 green); this is the live half — eyeball
sentinels + deltas against the real Gemma brain.

1. Build and start the sidecar on the scratch port (hand-started, lab
   discipline — do NOT touch the live services):

   ```bash
   npm install && npm run build
   ZOE_BRAIN_OPEN=1 ZOE_BRAIN_USER_ID=family-admin ZOE_BRAIN_ALLOW_WRITES=false \
     ZOE_DATA_URL=http://127.0.0.1:8000 PORT=3579 node dist/server.mjs
   ```

2. Curl the streaming mode with a prompt that forces a tool call (`-N`
   disables curl buffering so you see deltas arrive live):

   ```bash
   curl -N -sS -X POST \
     -H 'Accept: application/x-ndjson' -H 'Content-Type: application/json' \
     -d '{"message":"whats the weather looking like right now?"}' \
     'http://127.0.0.1:3579/agents/zoe/sentinel-check-1'
   ```

3. Eyeball the stream (one JSON value per line, in order):
   - `"__TOOL__:{\"phase\": \"start\", ...}"` then `phase=args` BEFORE the
     tool result — this is the line the voice filler (#844) keys off;
   - `phase=result` after the tool runs;
   - then plain-string text deltas arriving incrementally (not one blob);
   - a final `{"done": true}` line.

4. Confirm the whole-result mode still answers identically (regression guard):

   ```bash
   curl -sS -X POST -H 'Content-Type: application/json' \
     -d '{"message":"whats the weather looking like right now?"}' \
     'http://127.0.0.1:3579/agents/zoe/sentinel-check-2?wait=result'
   ```

5. Kill the sidecar by PORT ONLY when done — **NEVER `pkill -f`**, and
   **NEVER restart :8000 (zoe-data) or :11434 (llama-server)**:

   ```bash
   lsof -ti tcp:3579 | xargs -r kill
   ```

## activate_abilities fallback fix (#965, merged — measurement pending)

Operator checklist for the on-box half of #965's verification. The sandbox the
change was built in cannot reach the live services (zoe-data :8000, llama-server
:11434), so the measurement below is the operator's half — it has not been run
yet.

### What changed in #965 (already unit-tested, 38/38 green)

- `src/agents/zoe.ts` — imperative activator doctrine appended to the
  instructions (group catalogue + "you have NO weather/calendar/… knowledge of
  your own; activate first; never claim un-returned data").
- `src/tools/zoe-tools.ts` — activator input schema pinned as a dead-simple
  single-enum object, with the enum annotated.
- `src/tools/tool-groups.ts` — `GROUP_SUMMARY` exported (shared by tool
  description + instructions); widened triggers: washing/laundry/outside →
  weather, "anything on <day>" / "am I free" → calendar.

### On-box measurement (operator)

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

### Housekeeping note

A stale git-worktree entry `~/.worktrees/flue-fallback` (branch
`flue/activator-fallback`) exists on the box only as prunable metadata from an
earlier attempt. The operator may inspect it and prune it
(`git worktree prune` / `git worktree remove`); nothing in this PR depends on
it.

## Live write verification (operator)

The sandbox half now proves the write tools' HTTP method/path/payloads against a
fake zoe-data server. The live half must be run on the box because this sandbox
cannot reach zoe-data `:8000` or llama-server `:11434`.

**Safety rails:**

- Use a NON-admin demo user only. Do **not** run this against
  `family-admin` or Jason's real data.
- Start only the lab sidecar on `:3579` with `ZOE_BRAIN_ALLOW_WRITES=true`.
- Do **not** restart zoe-data `:8000` or llama-server `:11434`.
- Kill the sidecar by port only when done. Never use `pkill -f`.

1. Create or pick a disposable non-admin demo user ID that is allowed to use
   lists, reminders, calendar, and notes in zoe-data. Record it as
   `DEMO_USER_ID`.

2. Build and start the sidecar from `labs/flue-zoe-brain/`:

   ```bash
   npm install && npm run build
   DEMO_USER_ID=<non-admin-demo-user>
   ZOE_BRAIN_OPEN=1 ZOE_BRAIN_USER_ID="$DEMO_USER_ID" ZOE_BRAIN_ALLOW_WRITES=true \
     ZOE_DATA_URL=http://127.0.0.1:8000 PORT=3579 node dist/server.mjs
   ```

3. In a separate shell, use `POST /agents/zoe/<sid>` on `:3579` to perform one
   reversible write per family:

   - list: add `flue write path test milk` to the shopping list
   - reminder: remind me to `delete flue reminder test` tomorrow at 9am
   - calendar: add `Flue write path cleanup test` tomorrow at 9:15am
   - note: create note `Flue write path test` with content
     `Delete after verification`
   - timer: set a 1 minute timer named `flue write path test`; this is expected
     to fail closed unless zoe-data has gained a real timer confirmation path

4. Verify each persisted write via zoe-data read paths as the same demo user:

   - lists: read the shopping list and confirm the test item exists
   - reminders: list reminders and confirm the test reminder exists
   - calendar: show tomorrow's calendar and confirm the test event exists
   - notes: read/search notes and confirm the test note exists
   - timer: confirm the sidecar did **not** falsely claim a real timer started
     unless the backend returned an explicit non-canned timer confirmation

5. Clean up immediately using the demo user's normal UI/API flows: remove the
   list item, delete/complete the reminder, delete the calendar event, and
   delete the note. Re-read each family to prove cleanup.

6. Kill the sidecar by port only:

   ```bash
   lsof -ti tcp:3579 | xargs -r kill
   ```

7. Record the live result in the cutover handoff: demo user ID, timestamp, each
   write family, readback evidence, cleanup evidence, and whether timer remained
   fail-closed.
