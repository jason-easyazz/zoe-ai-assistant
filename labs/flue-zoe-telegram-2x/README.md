# Flue Zoe Telegram — 2.x port (lab-only, PARALLEL, nothing deploys)

> **This is a PARALLEL port, not a replacement.** `labs/flue-zoe-telegram/`
> stays on `@flue/*@1.0.0-beta.6` and remains the deployed
> `flue-zoe-telegram.service` on `:3582`. Nothing in this directory is built,
> restarted, or reached by anything today. **Cutover is a deliberate, separate
> operator step** — the runbook is at the bottom.

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

## Cutover runbook (OPERATOR, not an agent)

Prerequisite: this is the *pathfinder*, so do it only when the operator wants
Flue 2 on the Telegram channel. Nothing below is automated and nothing merges it.

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
2. **Copy the config, plus the epoch history:**
   ```sh
   cp ~/assistant/labs/flue-zoe-telegram/.env ~/assistant/labs/flue-zoe-telegram-2x/.env
   cp ~/assistant/labs/flue-zoe-telegram/data/session_epochs.json \
      ~/assistant/labs/flue-zoe-telegram-2x/data/session_epochs.json
   ```
   Do **NOT** copy `data/zoe.db` — 2.x stores schema v8, the beta stored v5, and
   the runtime rejects an older database *before any application code runs*.
   Leave the new store to be created fresh.
3. **Stop the old unit first — one poller per token.** Starting the new one
   beside the old gets a 409 Conflict and a bot that answers nobody:
   ```sh
   systemctl --user stop flue-zoe-telegram.service
   ```
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
5. **Verify — one real Telegram round-trip. This is the operator step an agent
   cannot do:**
   ```sh
   curl -s http://127.0.0.1:3582/health   # {"ok":true,...,"polling":true}
   ```
   then message the bot from a **linked** account and confirm the reply reflects
   *your* memory (not a guest answer), and that `/new` still answers "fresh
   conversation". Watch `journalctl --user -u flue-zoe-telegram -f` for
   `polling (took the bot over)` and the absence of a 409.
6. **Rollback = repoint the unit. The old store is untouched:**
   ```sh
   systemctl --user stop flue-zoe-telegram.service
   rm ~/.config/systemd/user/flue-zoe-telegram.service.d/flue2.conf
   systemctl --user daemon-reload
   systemctl --user start flue-zoe-telegram.service
   ```
   `labs/flue-zoe-telegram/data/zoe.db` was never opened by the 2.x process, so
   the beta runtime reads it exactly as it left it. (This is the property the
   brain sidecar does **not** get for free — see below.)

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
