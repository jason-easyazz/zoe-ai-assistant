# Flue Zoe Telegram — 2.x (LIVE since the 2026-08-09 cutover)

> **This directory IS the deployed bot.** `flue-zoe-telegram.service` runs this
> build on `:3582`, and `deploy.yml` rebuilds + restarts the unit on any merged
> diff under this path — production, not a lab. The retired beta stays in
> `labs/flue-zoe-telegram/` as the ROLLBACK TARGET only (runbook step 7 at the
> bottom: repoint the unit + carry the epoch map back + revert the deploy
> retarget together). The historical parallel-trial and cutover sections below
> are kept as the record of how the cutover was executed.

Phase 1 of the operator-declared "move to Flue 2" programme, taken on the
**smallest Flue surface in the repo** deliberately: this channel is ~700 lines,
its state is disposable, and every contract it depends on is plain HTTP. It is
the pathfinder for the brain sidecar (`labs/flue-zoe-brain-2x/`, PR #1616),
whose cutover is the one that can take the voice path down.

Ported to `@flue/runtime` **2.0.1** (`@flue/cli`/`@flue/vite` 2.0.1, `vite` 8).
Same external behaviour, same four zoe-data contracts, same `/health` semantics,
same URL shape.

**Verified on this branch:** typecheck clean, `vite build` clean, **40/40 tests**
(all offline — mock Bot API + mock zoe-data, no metered model calls, no real
Telegram sends), and the built `dist/server.mjs` smoke-booted on a throwaway port
with a throwaway store.

---

## Why a sibling directory and not an in-place upgrade

`.github/workflows/deploy.yml` rebuilds and restarts `flue-zoe-telegram.service`
on **any** diff under `labs/flue-zoe-telegram/`. An in-place upgrade would
therefore deploy itself on merge — into a runtime whose store the live process
cannot read. `git diff origin/main -- labs/flue-zoe-telegram/` is **empty** on
this branch, and no workflow glob matches `labs/flue-zoe-telegram-2x/`.

---

## What the port proved

**The four zoe-data contracts are runtime-independent — they did not move.**
`src/brain.ts` is `fetch` + `node:fs` only, so it crossed the 2.x boundary
untouched. `test/brain.test.ts` now asserts them against a real loopback server
(method, path, headers, JSON body) rather than a mocked `globalThis.fetch`:

| call | shape |
|---|---|
| `GET /api/system/resolve-telegram/<id>` | `X-Internal-Token` → `{user_id\|null}` |
| `POST /api/system/telegram/consume-link-token` | `{token, telegram_id, telegram_username}` → `{user_id}` \| 400 |
| `POST /api/system/telegram/register-bot` | `{username}` |
| `POST /api/chat/?stream=false` | `X-Zoe-User-Id` + `X-Internal-Token`, `{message, session_id, channel:'telegram'}` |

**The placeholder agent is now OPTIONAL — measured, not assumed.** On 1.x
`src/agents/zoe.ts` was mandatory: `flue build` discovered agents by directory
and an app with none did not build. 2.x deletes directory discovery, and a build
with `src/agents/` removed entirely succeeds (tried on this branch, then
reverted). It is kept anyway, for two reasons stated in the file: it preserves
the beta auto-router's `/agents/zoe/:id` URL, and it is the only thing on this
surface that exercises the 2.x agent + tool + store wiring at all.

**`'use agent'` registration works here, and the test can tell the difference.**
`test/agent_registration.test.ts` carries three negative controls — mounting with
no runtime configured, a runtime that registered a *different* agent, and the
wrong request body — so the 202 admission means something. (`start({ agents: [] })`
is refused outright: "requires at least one agent".)

**The migration guide's POST body is still wrong, on a second application.** The
guide reads as `{"message": {...}}`; that is rejected. The real shape is the
`DeliveredMessage` fields at top level — `{"kind":"user","body":"..."}`. First
measured on the brain port; independently re-confirmed here.

**The build scan is invisible to the test suite.** `start()` registers agents
explicitly and bypasses the `'use agent'` scan entirely, so a suite can be 100%
green with a directive that the build would have ignored. `smoke-built.sh` exists
for exactly that gap and is the only thing that proves the *built artifact*.

### Changed at port time

| | 1.x | 2.x |
|---|---|---|
| build | `flue build --target node` | `vite build` + `vite.config.ts` with `flue()` |
| dev | `flue dev` (:3583) | `vite dev` (:5173) |
| config import | `@flue/cli/config` | `@flue/runtime/config` (`root`/`output` rejected) |
| routing | `app.route('/', flue())` | `app.route('/agents/zoe', createAgentRouter(ZoeTelegram))` |
| agent | `defineAgent(({id}) => ({model, instructions, tools}))` | `'use agent'` + sync `ZoeTelegram({id})`, `useModel`/`useTool`, instructions = return value |
| agent identity | the **filename** | the **function name**, or `fn.agentName` (pinned to `'zoe'` here) |
| tool args | `run({ input })` | `run({ data })` |
| tool result | bare value | envelope `{ output }` |
| default port | 3582 came from the app's own default | **Flue's default is 3000** — `PORT=3582` is now load-bearing config |

### Improved at port time (behaviour-preserving)

- **`/start` and `/new` are testable.** On 1.x their bodies were inline grammY
  closures in `app.ts`, so the `/new` identity gate had no test. They are now
  `handleStart` / `handleNew` in `src/handler.ts` with injected deps, and the
  negative control — *an unlinked stranger must not be able to rotate a session
  epoch* — is pinned.
- **The whole channel is exercised offline.** `test/helpers/mock-telegram.ts`
  speaks enough Bot API for the real grammY client, so takeover, the 409 →
  `/health` 503 path, command-vs-message ordering, and the reply leaving through
  `sendMessage` are all covered without a token. `TELEGRAM_API_ROOT` is the only
  new env; it defaults to the real service and must never be set on the unit.
- **The epoch file is anchored at the package root**, so running the app by hand
  from another directory no longer starts a second, empty epoch map. Identical
  under the systemd unit (`WorkingDirectory=<package>`).

### Not run (stated plainly)

- **A real Telegram round-trip.** It needs the operator's bot token, and only one
  long-poll consumer may hold a token at a time — running it would fight the live
  bot. It is step 5 of the runbook below.
- **A live zoe-data call.** Every test points at a loopback mock; the smoke points
  at a dead port.

---

## Run it

```sh
cd labs/flue-zoe-telegram-2x
npm install
npm run typecheck
npm test          # 40/40, fully offline
npm run build     # vite build → dist/server.mjs
./smoke-built.sh  # throwaway port + store + mock Bot API
```

On the Jetson the box is often under 1 GB free with the voice brain resident.
Run builds inside a capped scope so a lab build can never squeeze it:

```sh
systemd-run --user --scope -q --collect -p MemoryHigh=700M -p MemoryMax=1200M npm run build
```

---

## Parallel trial (do this FIRST — no cutover, nothing stopped)

The point of a pathfinder is to observe it running **beside** the live 1.x bot,
not to replace it and find out. This sequence keeps the live bot untouched.

**One poller per bot token.** Telegram gives the second `getUpdates` caller a 409
Conflict, so a parallel trial cannot share the live token. Either use a second
BotFather token, or run with the mock Bot API (`TELEGRAM_API_ROOT`) as
`smoke-built.sh` does. Everything else can run concurrently.

1. **Build, and take the shipped config as-is:**
   ```sh
   cd ~/assistant/labs/flue-zoe-telegram-2x && npm ci && npm run build
   cp .env.example .env
   ```
   `.env.example` ships `PORT=33582`, **not** 3582 — it is deliberately
   collision-free so the copy above starts beside the live bot rather than
   fighting it for the port. Leave it alone for the whole trial.
2. **Give it its own token and store** (never the live bot's):
   ```sh
   # in .env: TELEGRAM_BOT_TOKEN=<a SECOND BotFather token>
   ```
   The store path and epoch file already default inside this directory, so the
   1.x `data/` is not touched. Do **not** copy `data/zoe.db` — 2.x stores schema
   v8 and the runtime rejects the beta's v5 before any application code runs.
3. **Run it in the foreground with the env LOADED and the trial flag set:**
   ```sh
   cd ~/assistant/labs/flue-zoe-telegram-2x
   set -a; . ./.env; set +a          # node does NOT read .env by itself
   ZOE_TELEGRAM_TRIAL=1 node dist/server.mjs
   ```
   The `set -a; . ./.env` is not optional. Node loads no `.env` — under systemd
   the unit's `EnvironmentFile=` does it, and there is no unit here. Without it
   `TELEGRAM_BOT_TOKEN` is absent and startup throws before polling, while
   `PORT` and `ZOE_DATA_URL` silently fall back to Flue's defaults.

   Then, from another shell:
   ```sh
   curl -s http://127.0.0.1:33582/health   # {"ok":true,...,"polling":true}
   ```
   **`ZOE_TELEGRAM_TRIAL=1` is not optional for a parallel trial.** Without it the
   trial bot registers its own `@username` with zoe-data, and
   `telegram_link.set_bot_username` keeps only ONE — so every Settings QR / deep
   link in production would start pointing at the temporary bot, and would stay
   pointed there after the trial stops until the live bot or zoe-data restarts.
   With the flag the registration is a logged no-op; everything else still
   exercises the real path. Leave it UNSET at cutover, where registering the new
   bot is the correct behaviour.

   **It also namespaces the zoe-data session ids.** A private chat with the same
   user has the same `chatId` in both bots, so an unflagged trial would produce
   the same `telegram-<chatId>` session id as the live bot — and zoe-data keys
   its in-memory context and persisted `chat_messages` by that id, so every
   trial prompt and reply would be written into the user's PRODUCTION
   conversation and would steer subsequent live turns. With the flag, trial
   turns go to `trial2x-telegram-<chatId>` and production history is untouched.
   The live bot on `:3582` keeps polling its own token throughout; the watchdog
   timer and the deploy health check both read the 1.x directory and are
   unaffected.
4. **Offline sanity without any Telegram traffic at all:**
   ```sh
   ./smoke-built.sh    # exits non-zero on any failure
   ```

Only once the trial is satisfying does the cutover below apply — and that is the
step that stops the live unit and moves the port to 3582.

---

## Cutover runbook (OPERATOR, not an agent)

Prerequisite: this is the *pathfinder*, so do it only when the operator wants
Flue 2 on the Telegram channel — and only after the parallel trial above.
Nothing below is automated and nothing merges it.

**This is where `PORT` changes to 3582.** `.env.example` ships 33582 for the
parallel trial; at cutover the 1.x unit is stopped first, so 3582 is free and the
watchdog/deploy health check (which curl `:3582` literally) keep working.

**What is at risk, honestly:** nothing user-visible. Replies come from zoe-data
keyed by `sessionFor(chatId)` — a string, not a Flue conversation — so Flue's own
store holds only the record of an agent that is never dispatched. The `/new`
epoch map is our own JSON and copies across. The real risks are operational: two
pollers fighting over one bot token, and the watchdog/deploy health check pointed
at the wrong port.

1. **Build the port, once, in place** (it will not have a `dist/` on a fresh
   clone):
   ```sh
   cd ~/assistant/labs/flue-zoe-telegram-2x && npm ci && npm run build
   ```
2. **Stop the old unit FIRST — before copying anything.** One poller per token
   (starting the new one beside the old gets a 409 Conflict and a bot that
   answers nobody), and the epoch copy below is only a consistent snapshot once
   the beta has stopped writing: a `/new` arriving after the copy but before the
   stop advances the beta's map and that increment is lost, so the user's `/new`
   silently un-happens at cutover (cross-review, #1639).
   ```sh
   systemctl --user stop flue-zoe-telegram.service
   ```

3. **Copy the config, plus the epoch history:**
   ```sh
   cp ~/assistant/labs/flue-zoe-telegram/.env ~/assistant/labs/flue-zoe-telegram-2x/.env
   cp ~/assistant/labs/flue-zoe-telegram/data/session_epochs.json \
      ~/assistant/labs/flue-zoe-telegram-2x/data/session_epochs.json
   ```
   **Then set the port explicitly — the copy does not carry one.** The 1.x
   `.env` has no `PORT` key at all (its template defines only the token and the
   Zoe settings; 1.x got 3582 from elsewhere). Flue 2's own default is **3000**,
   so a copied env starts the replacement on the wrong port while the watchdog
   and the deploy health check both curl `:3582` — the old bot is already
   stopped, nothing answers, and the unit restarts forever (cross-review, #1639):
   ```sh
   echo 'PORT=3582' >> ~/assistant/labs/flue-zoe-telegram-2x/.env
   grep '^PORT=' ~/assistant/labs/flue-zoe-telegram-2x/.env   # must print PORT=3582
   ```
   (This is the one place 3582 is correct. `.env.example` ships 33582 for the
   parallel trial; here the 1.x unit is about to be stopped, so 3582 is free.)
   Do **NOT** copy `data/zoe.db` — 2.x stores schema v8, the beta stored v5, and
   the runtime rejects an older database *before any application code runs*.
   Leave the new store to be created fresh.
4. **Point the unit at the port and restart.** Add a drop-in rather than editing
   the tracked template:
   ```sh
   mkdir -p ~/.config/systemd/user/flue-zoe-telegram.service.d
   cat > ~/.config/systemd/user/flue-zoe-telegram.service.d/flue2.conf <<'CONF'
   [Service]
   WorkingDirectory=
   WorkingDirectory=%h/assistant/labs/flue-zoe-telegram-2x
   EnvironmentFile=
   EnvironmentFile=%h/assistant/labs/flue-zoe-telegram-2x/.env
   CONF
   systemctl --user daemon-reload
   systemctl --user start flue-zoe-telegram.service
   ```
   Keep `PORT=3582`: the watchdog timer reads that key out of
   `labs/flue-zoe-telegram/.env` and the deploy health check curls `:3582`
   literally. (Both still read the OLD directory's `.env` for the port — fix
   those two references in the same change, or leave `PORT=3582` in both files.)
5. **Retarget the DEPLOY JOB in the same change — or the live bot silently
   stops tracking `main`.** `.github/workflows/deploy.yml` (the *Rebuild /
   restart Flue Telegram bot* step) hardcodes `labs/flue-zoe-telegram/` in three
   places: the `git diff` change-detection pathspec, the `dist/server.mjs`
   existence check, and the `cd`. After the drop-in above, the unit runs the
   `-2x` directory while deploy still builds and restarts the 1.x one — so a
   merge that changes 2.x source leaves the live `dist/server.mjs` **stale**, and
   the post-deploy health check still gets a 200 from `:3582` because the 2.x
   process is answering it. Green deploy, old code, no signal (cross-review,
   #1639).

   This PR deliberately does **not** touch `deploy.yml` — it is labs-only, and
   editing a workflow for a cutover that has not happened would break the live
   1.x bot's deploy today. Retarget it as part of the cutover commit: replace
   the three `labs/flue-zoe-telegram/` occurrences with
   `labs/flue-zoe-telegram-2x/`. Same for the watchdog timer, which reads `PORT`
   out of the 1.x `.env`.

6. **Verify — one real Telegram round-trip. This is the operator step an agent
   cannot do:**
   ```sh
   curl -s http://127.0.0.1:3582/health   # {"ok":true,...,"polling":true}
   ```
   then message the bot from a **linked** account and confirm the reply reflects
   *your* memory (not a guest answer), and that `/new` still answers "fresh
   conversation". Watch `journalctl --user -u flue-zoe-telegram -f` for
   `polling (took the bot over)` and the absence of a 409.
7. **Rollback = repoint the unit — carry the EPOCH FILE back, and REVERT THE
   DEPLOY RETARGET.** If step 5 landed, `deploy.yml` now builds and restarts
   `-2x`; leaving it that way after rolling the unit back to 1.x means the next
   merge rebuilds a directory nothing runs while the live 1.x `dist/` goes
   stale — the same green-deploy-old-code failure step 5 exists to prevent,
   pointing the other way (cross-review, #1639). Revert that commit as part of
   the rollback, not after it.
   ```sh
   systemctl --user stop flue-zoe-telegram.service
   # /new epochs advanced under 2.x live in the -2x directory. Without this copy
   # the beta reloads its stale map and resumes a conversation the user already
   # ended (cross-review, #1639) — the one piece of state that genuinely crosses
   # the boundary, because it is OUR json, not Flue's store.
   cp ~/assistant/labs/flue-zoe-telegram-2x/data/session_epochs.json \
      ~/assistant/labs/flue-zoe-telegram/data/session_epochs.json
   rm ~/.config/systemd/user/flue-zoe-telegram.service.d/flue2.conf
   systemctl --user daemon-reload
   systemctl --user start flue-zoe-telegram.service
   ```
   `labs/flue-zoe-telegram/data/zoe.db` was never opened by the 2.x process, so
   the beta runtime reads it exactly as it left it. (This is the property the
   brain sidecar does **not** get for free — see below.) The epoch map is the
   exception in both directions: it is copied FORWARD at cutover (step 2) and
   must be copied BACK here, or `/new` silently un-happens.

---

## What this teaches the brain-sidecar Phase 2

1. **The one-way store boundary is a *scoped* risk, not a blanket one.** The
   correct question is not "can we migrate the store?" (no, in either direction)
   but "**what actually reads it?**". Here: nothing user-visible, so a fresh v8
   store costs nothing and rollback is genuinely free. The brain sidecar must
   answer the same question about its own conversations before its cutover is
   scheduled — and it must answer it with a measurement, not an assumption.
2. **Two units read config from a directory path, not from the service.** The
   watchdog timer greps `PORT=` out of `labs/flue-zoe-telegram/.env` and
   `zoe_crash_loop_watch.py` reads `TELEGRAM_BOT_TOKEN` from the same file
   (`scripts/maintenance/zoe_crash_loop_watch.py:35`). Repointing the unit does
   **not** repoint them. Enumerate every consumer of a lab directory's `.env`
   before moving the directory — the brain sidecar should assume it has similar
   hidden readers until proven otherwise.
3. **`PORT` became load-bearing.** 2.x's built server defaults to **3000**;
   the beta's default happened to be what the deploy check and watchdog expect.
   Any 2.x unit that inherits an env file without an explicit `PORT` comes up on
   the wrong port and every health check fails for a reason nothing logs.
4. **The suite cannot see the build scan.** `start()` bypasses `'use agent'`
   entirely. A brain port that is 191/191 green still has not proven the built
   artifact registers anything — keep `smoke-built.sh` in the required path, not
   as a nicety.
5. **Mock the transport, not just the decision.** The 1.x suite stubbed
   `handleIncoming`'s deps and was green; it could not have caught a handler
   registration-order regression, and command-ordering is exactly the kind of
   thing a routing rewrite breaks. A loopback mock of the external API cost ~150
   lines and covers the whole wiring. The brain's equivalent is its mock model —
   already present in #1616, and the right instinct.
6. **What did NOT bite, and it is the useful half of the finding.** No provider
   work, no streaming contract, no auth middleware, no tool-cap, no identity
   plumbing — the Telegram channel needed none of it. So the brain sidecar's
   remaining risk is concentrated almost entirely in the pieces this port never
   touched (`setProvider`/`createProvider`, sentinel streaming, the zoe-data
   client's wire contract), and a green Telegram cutover says nothing about them.
   Do not let this pathfinder's success be read as de-risking those.
