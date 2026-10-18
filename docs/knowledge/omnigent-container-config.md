---
type: knowledge
title: Omnigent container config — where each file comes from, login renewal, polly-lane auth policy
description: How zoe-omnigent's configuration reaches the container (image, compose bind, entrypoint seed, or a persisted volume nothing tracks), the B0.11 Codex MCP seed that keeps Serena on the shared server across recreates, why the in-container CLI auto-updaters are disabled and how to update a CLI (bump the pin, rebuild, recreate), the Claude/Codex login-renewal steps (Claude refresh token runs to 2026-10-11), and the policy note to move the polly lane off claude-sdk subscription OAuth.
---

# Omnigent container config

The `zoe-omnigent` container (`modules/omnigent/`) is configured from four different
places, and the failure class behind B0.11 is confusing them: a file that lives in a
**persisted volume** survives restarts and recreates, so a live hand-patch *looks* durable,
but nothing tracked reproduces it — the moment the volume is reset (or the file is regenerated
by the tool that owns it) the patch is gone and nothing alarms.

## Where each config surface comes from

| In-container path | Source | Survives recreate? | Tracked? |
|---|---|---|---|
| `/usr/local/bin/omnigent-entrypoint.sh` | image (`Dockerfile` COPY of `entrypoint.sh`) | rebuilt with the image | yes |
| `/usr/local/share/zoe-omnigent/codex-mcp.toml` | image (`Dockerfile` COPY of `codex-mcp.toml`) | rebuilt with the image | yes |
| `/workspace/.mcp.json` (Claude Code MCP) | compose bind of `modules/omnigent/.mcp.json`, read-only | yes (re-read from the repo) | yes |
| `/root/.omnigent/config.yaml` | `omnigent-data` volume; the entrypoint seeds the `openrouter` provider once (create-only) | yes | seed only |
| `/root/.codex/config.toml` (Codex MCP + Codex state) | `omnigent-codex` volume; **since B0.11 the entrypoint rewrites the managed `[mcp_servers.*]` tables from the baked template on every boot** | yes | managed tables only |
| `/root/.claude/.credentials.json`, `/root/.codex/auth.json` | `omnigent-claude` / `omnigent-codex` volumes, written by the one-time logins | yes | never (bearer tokens) |
| `/root/.claude.json` (Claude Code first-run/trust state; NOT the token) | container writable layer — in no volume | **no** (reset by a recreate) | no |
| `/usr/local/lib/node_modules/{@anthropic-ai/claude-code,@openai/codex,...}`, `/etc/claude-code/managed-settings.json` | image (`Dockerfile`), root-owned and read-only to uid 1000 | rebuilt with the image | yes |
| everything under `environment:` | `docker-compose.module.yml` (+ the repo `.env` for the interpolated values) | applied on recreate | yes |

Evidence for the B0.11 case (read-only `docker inspect` / `docker exec`, 2026-09-26):
`/root/.codex` is the named volume `omnigent_omnigent-codex`; no Dockerfile, compose or
entrypoint line wrote `config.toml`; the file carried a `config.toml.bak-20260925` whose
serena table was the stdio spawn (`command = ".../serena"`, `--transport stdio`) — i.e. the
2026-09-25 fix was a hand edit inside the volume with no tracked source.

## The Codex MCP seed (B0.11)

- **Template:** `modules/omnigent/codex-mcp.toml` — serena `url = "http://172.28.0.1:9121/mcp"`
  (the host's shared `serena-mcp.service` over the `zoe-codeintel` gateway, same URL as
  `.mcp.json`), codebase-memory as the in-container raw binary (stdio-only; the container has
  no systemd user bus, so the host's capping wrapper could only fall back).
- **Applied by:** `entrypoint.sh` on every boot. It parses the current file, and if the managed
  tables already equal the template it does nothing (log line `codex mcp seed: already
  current`). Otherwise it strips the managed `[mcp_servers.serena]` / `[mcp_servers.codebase-memory]`
  tables (and their sub-tables) textually, appends the template, re-parses to prove the result
  round-trips, backs up the old file as `config.toml.bak-<timestamp>` and replaces atomically.
  Everything else — Codex's `hooks.state` trusted hashes, any other server — is kept verbatim.
- **Fails safe, never fatal:** an unparseable file, a template that would reintroduce a serena
  `command =`, or a merge that does not round-trip leaves the file untouched with a stderr
  warning; the omnigent server still boots.
- **Pinned by:** `tests/unit/modules/test_omnigent_mcp_config.py` (template equals `.mcp.json`,
  Dockerfile COPY and entrypoint default path agree), `tests/unit/test_agent_mcp_memory_bounds.py`
  (`test_omnigent_container_codex_serena_attaches_by_url`), and the hermetic
  `tests/unit/test_omnigent_entrypoint_codex_mcp.py` (legacy stdio file rewritten with
  `hooks.state` kept; idempotent; unparseable left alone; spawning template refused).
- **Applying it to the live box** needs an image rebuild + recreate (the entrypoint and the
  template are baked). From `modules/omnigent/`, after checking RAM and dropping caches per
  `modules/AGENTS.md`:

  ```bash
  docker compose --env-file ../../.env -f docker-compose.module.yml up -d --build
  docker logs zoe-omnigent 2>&1 | grep "codex mcp seed"        # expect: already current (the volume was hand-patched 09-25)
  docker exec zoe-omnigent cat /root/.codex/config.toml | grep -A1 'mcp_servers.serena'   # url = ..., no command
  pgrep -af "serena start-mcp-server" | wc -l                   # 1 = only the shared server
  ```

  `already current` on the first boot is the expected proof: the seed found the hand-patch and
  left it, and from now on a reset volume converges to the same state on its own.

## Updating a harness CLI — the in-container auto-updater is disabled

**Symptom (2026-10-11):** Claude Code in `zoe-omnigent` reported that auto-update failed
(no write permission). **Cause:** `modules/omnigent/Dockerfile` installs
`@anthropic-ai/claude-code` globally as **root** into `/usr/local/lib/node_modules`, but the
container runs as uid 1000 (`user: "1000:1000"` in the compose file), so the updater can
never write its own install. Even if it could, a self-update would move the CLI off the
reviewed pin and be lost on the next recreate, so the right fix is to turn it off, not to
make the directory writable.

**What is set** (the three agree; none is a secret):

| Where | Setting | Effect |
|---|---|---|
| `Dockerfile` `ENV` + compose `environment:` | `DISABLE_AUTOUPDATER=1` | stops Claude Code's background update check (Claude Code docs, *Advanced setup → Disable auto-updates*) |
| same | `DISABLE_UPDATES=1` | additionally blocks `claude update` / `claude install`, i.e. every update path (same page) |
| `Dockerfile` → `/etc/claude-code/managed-settings.json` (`{"env": {...}}`) | the same two keys | the documented Linux managed-settings location. Belt-and-braces: omnigent's runner builds each worker's environment from an allowlist (see `OMNIGENT_RUNNER_ENV_PASSTHROUGH` in the compose file), so a container env var is not guaranteed to reach the `claude` process the runner spawns; the settings file does not depend on that |
| same as the first row | `PI_SKIP_VERSION_CHECK=1` | pi never self-updates in the background (`pi update` is an explicit command); this only disables its pi.dev latest-version request (pi `docs/environment-variables.md`, verified in 0.87.1) |

**Codex:** also notify-only; its switch is the `config.toml` key
`check_for_update_on_startup = false` ("set to false only when updates are centrally managed",
Codex config reference). That file is the `omnigent-codex` volume's entrypoint-managed
`config.toml`, not an env var, so it is **not** changed here; it cannot self-update a
root-owned install either. Revisit only if its startup notice becomes a nuisance.

**Verify after the rebuild** (read-only): `docker exec zoe-omnigent claude doctor` should list
`Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)` (or the managed-settings source) in
place of the old no-write-permission notice.

**To update a CLI** the only path is: (1) pick the version (`npm view @anthropic-ai/claude-code
dist-tags` — the pin follows `stable`; keep it at or above the omnigent minimum in
`onboarding/harness_install.py`, claude >= 2.1.161); (2) change the pin in
`modules/omnigent/Dockerfile` **and** the mention in `modules/omnigent/README.md`
(`grep -rn 'claude-code@' .`; pi is pinned in three places, see
`tests/unit/test_harness_pin_agreement.py`); (3) rebuild the image and recreate the container;
(4) prove it with a real dispatch, per the pin-bump procedure in `modules/omnigent/README.md`.

**Review before the first bump past 2.1.220** (from the 2026-09-25 state review): headless runs
prompt, then deny after 2 minutes, on substitution-`rm`; `"type":"sdk"` MCP entries are skipped
(ours are `url`/stdio); AGENTS.md is read natively when no CLAUDE.md exists (Zoe's import shim
still wins). The 2.1.220 -> 2.1.287 bump is **not yet exercised against a live polly dispatch**;
the real-dispatch check under *After merge* is that proof.

### After merge (operator)

Rebuild the `zoe-omnigent` image and recreate the container. Check RAM first (the build
alongside the 6 GB `llama-server` can OOM the live brain) and drop caches per
`modules/AGENTS.md`. `-p` pins the existing compose project so the recreate replaces
`zoe-omnigent` instead of colliding with its `container_name`:

```bash
git -C /home/zoe/assistant fetch origin && git -C /home/zoe/assistant merge --ff-only origin/main   # no stash
REPO=$(git -C /home/zoe/assistant rev-parse --show-toplevel)
PROJECT=$(docker inspect zoe-omnigent --format '{{index .Config.Labels "com.docker.compose.project"}}')
COMPOSE="docker compose -p $PROJECT --env-file $REPO/.env -f $REPO/modules/omnigent/docker-compose.module.yml"
$COMPOSE build omnigent                 # re-resolves claude-code@2.1.287 (the pin changed, so the layer rebuilds)
$COMPOSE up -d omnigent                 # recreates the container from the new image + compose env
docker exec zoe-omnigent claude --version                 # 2.1.287 (Claude Code)
docker exec zoe-omnigent claude doctor                    # Auto-updates: disabled
docker exec zoe-omnigent claude -p "reply with the single word ok"   # login survived + a turn completes
```

Then confirm `claude_code` is available for the host in the Omnigent UI / `GET /v1/hosts`, and
run one real polly `claude_code` dispatch before relying on the new version.

**The login is kept — by a volume.** Compose mounts the named volume `omnigent-claude` at
`/root/.claude`, and the OAuth tokens live in `/root/.claude/.credentials.json` (see the table
above). A rebuild + recreate replaces the container and image, not named volumes, so the
Claude login (and Codex's `omnigent-codex`, Cursor's `omnigent-cursor`) are untouched; no
re-login is expected. **One caveat:** Claude Code also keeps a small state file at
`/root/.claude.json` (a sibling of the directory, per its docs), which is **not** in any
volume and is reset by a recreate. It holds first-run/onboarding state and per-project trust,
not the token; if the `claude -p` check above unexpectedly prompts or reports a logged-out
state, that is the file to suspect, and the fix is the renewal below.

## Login renewal (operator)

The harnesses run on **subscription OAuth**, not API keys (see `modules/omnigent/README.md`
→ *Auth*). The tokens live only in the credential volumes and are the top operational risk
of the Omnigent lane: when they lapse, every `claude_code` / `claude-sdk` dispatch fails
within seconds with a logged-out `failure_reason` (the 2026-07-22 and 2026-08-18 incidents).

- **Claude:** the refresh token recorded on 2026-09-25 runs to **2026-10-11** — renew before
  then. Inside the container (headless paste flow, no browser):
  ```bash
  docker exec -it zoe-omnigent claude          # in the TUI: /login → open the URL elsewhere → paste the code
  docker exec -i zoe-omnigent python3 - <<'PY' # -i: the heredoc is stdin; without it nothing prints (no secrets printed)
  import json, datetime
  o = json.load(open('/root/.claude/.credentials.json'))['claudeAiOauth']
  print(datetime.datetime.fromtimestamp(o['expiresAt']/1000, datetime.UTC), o.get('subscriptionType'))
  PY
  docker exec zoe-omnigent claude -p "reply with the single word ok"   # the real proof: a turn completes
  ```
  Then re-check the polly roster in the Omnigent UI (host → harness `claude_code` available).
- **Codex:** `docker exec -it zoe-omnigent codex login` ("Sign in with ChatGPT"); `auth.json`
  in the same volume holds the tokens.
- **Cursor:** `docker exec -it zoe-omnigent env NO_OPEN_BROWSER=1 cursor-agent login`, then
  `cursor-agent status`.
- Put the next Claude renewal on the calendar when you do this one; nothing in the stack
  alarms ahead of expiry — the executor only reports the failure after it happens.

## Policy note — move the polly lane off `claude-sdk` subscription OAuth

Recorded in the 2026-09-25 state review (§9 item 8): polly's `claude-sdk` harness runs the
Agent SDK on the container's consumer-subscription OAuth, which now sits on the wrong side of
Anthropic's stated policy ("developers using the Agent SDK should use API key
authentication"); the native `claude` CLI harness on a subscription login is explicitly fine.

- **Direction:** route the polly lane through the native `claude_code` harness, or give the
  `claude-sdk` harness its own API key.
- **Constraint if an API key is chosen:** it must be scoped to that harness only. Putting
  `ANTHROPIC_API_KEY` in the container `environment:` flips *every* Claude CLI in the container
  to metered billing and silently ignores the subscription (the compose file says so and
  deliberately omits it); the key would have to reach the SDK harness alone (e.g. an
  omnigent provider entry with an `env:` ref plus `OMNIGENT_RUNNER_ENV_PASSTHROUGH`, the pattern
  the `pi`/OpenRouter seed uses).
- **Status (2026-10-18):** decided and built, awaiting the owner's key. The key is named
  `ZOE_OMNIGENT_ANTHROPIC_API_KEY` (never `ANTHROPIC_API_KEY`), lives in an out-of-repo env
  file read by compose `env_file`, and reaches the SDK harness through omnigent's
  `auth: {type: api_key, api_key: $VAR}` block, toggled by
  `scripts/maintenance/omnigent_claude_auth_mode.py` — exactly the "env: ref plus
  `OMNIGENT_RUNNER_ENV_PASSTHROUGH`" shape above. Owner steps and the measured root cause of the
  OAuth refresh failures: [omnigent-cross-review.md](omnigent-cross-review.md#polly-claude-auth--root-cause-and-the-fix-2026-10-18).
  The stored-login renewal above stays valid as the fallback credential.

Related: [Omnigent cross-review](omnigent-cross-review.md) (what polly does),
[omp builder-lane fence](omp-builder-fence-install.md) (the other in-container harness fence),
`modules/omnigent/README.md` (bring-up, pins, code-intel mounts).
