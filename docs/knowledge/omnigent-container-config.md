---
type: knowledge
title: Omnigent container config — where each file comes from, login renewal, polly-lane auth policy
description: How zoe-omnigent's configuration reaches the container (image, compose bind, entrypoint seed, or a persisted volume nothing tracks), the B0.11 Codex MCP seed that keeps Serena on the shared server across recreates, the Claude/Codex login-renewal steps (Claude refresh token runs to 2026-10-11), and the policy note to move the polly lane off claude-sdk subscription OAuth.
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
- **Status:** policy decision pending with the operator; not implemented by B0.11. Until it is
  decided, the login renewal above keeps the lane running.

Related: [Omnigent cross-review](omnigent-cross-review.md) (what polly does),
[omp builder-lane fence](omp-builder-fence-install.md) (the other in-container harness fence),
`modules/omnigent/README.md` (bring-up, pins, code-intel mounts).
