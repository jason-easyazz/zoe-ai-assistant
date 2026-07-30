# Omnigent module (builds & runs on this aarch64/Tegra host)

Meta-harness that orchestrates the agent CLIs (Claude Code, Codex, Cursor, Pi) from one
control plane with a web/mobile UI. Intended to run as a Zoe module behind `zoe-cloudflared`,
mirroring the `agent-zero` module pattern.

## Build/run status (verified 2026-07-29 on this aarch64/Tegra host)

| Component        | arm64 status | Notes |
|------------------|--------------|-------|
| `claude` CLI     | ✅ works      | `@anthropic-ai/claude-code@2.1.220` (**pinned** — see below; min `2.1.161`) |
| `codex` CLI      | ✅ works      | `@openai/codex@0.146.0` (**pinned**; min `0.137.0`) |
| `pi` CLI         | ✅ works      | `@earendil-works/pi-coding-agent@0.82.1` **exact**, `--ignore-scripts` (min `0.79.0`; identical value required in `services/zoe-core/package.json`) |
| `cursor-agent`   | ✅ works      | mounted from the host install (`~/.local/share/cursor-agent`). **omnigent 0.7.0 enforces a minimum** (`_CURSOR_MIN_VERSION`, currently `2026.06.02`) — an older binary reports `version-too-low`, not `false`. Provision it reproducibly with [`scripts/setup/install_cursor_agent.sh`](../../scripts/setup/install_cursor_agent.sh) (pinned; `--check` verifies a host without changing it). |
| **omnigent core**| ✅ works      | `omnigent==0.7.0`, plain PyPI install — no vendored wheel needed |

### The aarch64 blocker (gone upstream — no longer worked around)
Historically `omnigent` depended on `cel-expr-python` (a wrapper around the CEL **C++** engine,
used by the policy system), and upstream published **no `linux aarch64` wheel and no sdist** —
the root cause being their own `release/build_wheel.sh`, which hardcodes `bazelisk-linux-amd64`.
We carried a locally-compiled aarch64 wheel in `wheels/` plus `UV_FIND_LINKS` to work around it.

That is all **retired as of the 0.7.0 bump**. Upstream replaced the dependency with
[`cel-python`](https://pypi.org/project/cel-python/) — a **pure-Python** CEL implementation
(`celpy`, shipped `py3-none-any`) — so there is no platform binary to build and nothing to
vendor. The `wheels/` directory, the `COPY wheels/`, and `UV_FIND_LINKS` were removed rather
than kept as dead scaffolding. Do not resurrect them; if a future omnigent reintroduces a
C-extension CEL dep, git history has the build recipe.

The other deps 0.7.0 added (`protobuf` 6.x, `zstandard`, `python-dateutil`) all publish cp312
manylinux aarch64 wheels, so the image builds from PyPI alone.

### Upgrading the pin
`omnigent==<version>` in the `Dockerfile` is pinned **deliberately** — an unpinned install is
Docker-layer-cached, so upgrades silently don't happen on rebuild, and an unpinned surprise
upgrade has bitten this module before. Before bumping, diff the new wheel against the current
one on the surfaces this module actually couples to (all verified unchanged for 0.4.0 → 0.7.0):

- `onboarding/provider_config.py` — `entrypoint.sh` seeds an OpenRouter `kind: gateway` entry
  into the `providers:` **mapping**; a format change here silently breaks the `pi` worker.
- `host/connect.py` — must still export `OMNIGENT_RUNNER_ENV_PASSTHROUGH`, and must still
  contain the `workspace = Path(frame.workspace).expanduser()` line the entrypoint patches
  (the patch degrades to a no-op if the marker moves, so sessions would silently default to
  `$HOME` instead of `/workspace`).
- `server/auth.py` / `server/ws_origin.py` — `OMNIGENT_AUTH_PROVIDER=header`,
  `OMNIGENT_LOCAL_SINGLE_USER`, `OMNIGENT_WS_ALLOWED_ORIGINS`.
- the `omnigent server` / `omnigent host` CLI commands and the
  `host.pid` / `daemons/*.json` / `auth_tokens.json` state files the entrypoint manages.

**Reading `configured_harnesses` — 0.7.0 changed its TYPE.** `GET /v1/hosts` returned
plain booleans up to 0.4.0; 0.7.0 returns `bool | str`, where the strings are failure
reasons (`"binary-missing"`, `"version-too-low"`, `"needs-auth"`). A truthiness test
therefore counts BROKEN harnesses as available — it reported "20 -> 23, none lost" on the
0.7.0 bump when the truth was **20 -> 18 with Cursor lost**. Always compare with `is True`:

```bash
curl -s http://127.0.0.1:6767/v1/hosts | python3 -c "
import json,sys; h=json.load(sys.stdin)['hosts'][0]['configured_harnesses']
print('usable:', sorted(k for k,v in h.items() if v is True))
print('broken:', {k:v for k,v in h.items() if not isinstance(v,bool)})"
```

### Harness CLI pins — what is and is not guaranteed

All three npm CLIs are **exact** pins, at both sites for `pi`
(`modules/omnigent/Dockerfile` and `services/zoe-core/package.json`). A caret range was
tried and rejected on review: zoe-core has no committed lockfile, so `^0.82.1` lets a
clean build pull an unreviewed `0.82.x` and lets the two consumers drift apart — which
defeats the whole "bumps are deliberate" invariant these pins exist to enforce. The
convenience of one edit instead of two is not worth reopening surprise upgrades.
`cursor-agent` is pinned separately in `scripts/setup/install_cursor_agent.sh` because
it is a host install, not an image dependency.

Exact pins buy determinism at the cost of **intentional staleness**: `claude` and
`codex` now freeze until someone bumps them. Review them alongside each omnigent
upgrade — that is already the moment their MINIMUMS can move
(`onboarding/harness_install.py`), so it is the natural cadence. Pin-bump procedure
for ANY harness CLI (Claude/Codex/Pi/Cursor): (a) review the upstream release
diff/changelog for the version range, (b) verify end-to-end (an actual agent/brain
dispatch, not just `<cli> --version` starts), and (c) record what was checked next
to the pin. `npm view <pkg> version` alone is NOT sufficient — a green `--version`
proves nothing about API shape changes or RPC event surfaces the client depends on.

**The zoe-core manifest is a DECLARATION, not an installer.** `services/zoe-core` has no
committed lockfile and no `node_modules`, and `zoe_core_client.py` launches the bare
command `pi` from `PATH` — so the version that actually runs as the brain is the
**host-global** npm install, not whatever `package.json` says. Bumping the manifest
without `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@<pin>` on the
host changes nothing at runtime; bumping the host without the manifest leaves the
declaration lying. Move both, and keep them equal to the omnigent Dockerfile's pin.

Bumping `pi` across a `0.x` MINOR (as 0.79 -> 0.82 was) is not safe by inspection:
zoe-core's `extensions/{abilities,memory,provider-local-gemma,soul}.ts` import
`ExtensionAPI` from it. Diff that type before bumping. For 0.79.3 -> 0.82.1 it was
verified **purely additive** — 6 lines added, 0 removed (three new `on()` overloads,
`registerEntryRenderer`, `registerProvider`) — and those four files import nothing
else from pi.

**Pi 0.82.1 compatibility verification (2026-07-30):** Zoe's brain client
(`services/zoe-data/zoe_core_client.py`) invokes pi with: `--mode rpc`,
`--provider`, `--model`, `-e <extension>`, `--no-extensions`, `--no-skills`,
`--no-prompt-templates`, `--no-themes`, `--no-context-files`, `--thinking off`.
All flags confirmed present in `pi --help` output for installed 0.82.1. The RPC
mode and all disable flags are documented and stable. No breaking changes found
in CLI surface or RPC event shapes between 0.79.3 and 0.82.1.

**RAM gate:** check available memory before building. An image build alongside the ~6 GB
`llama-server` can OOM the live brain (`docs/knowledge/memory-pressure-profile.md`); the
cgroup guards do not cover CUDA/NvMap. Build in a quiet window, or stop the brain first and
restore it after.

## Container user — runs as uid 1000 (zoe), not root

The repo is bind-mounted at `/workspace`, so whatever uid the container runs as is the
uid that owns files it writes into the host's **live checkout**. It ran as root until
2026-07-26; the result was 38 root-owned entries under `/home/zoe/assistant/.git` —
loose objects, `refs/heads/omni/*`, reflogs, and `.git/config` itself — after which every
zoe-side `git fetch` failed to take a lock on ~60 refs. Omnigent had also written a
**local** `credential.helper = store --file=/workspace/.git/.gh_credentials` into the
shared `.git/config`; `/workspace` does not exist outside the container, so every zoe git
push failed with `unable to get credential storage lock ... No such file or directory`.

Matching uids stops that at the source. `user: "1000:1000"` in the compose file, and the
image chowns `/root` to 1000 (inside the install RUN, so it costs no extra layer) and
gives uid 1000 a passwd entry with `--home-dir /root`. `HOME=/root` is set explicitly
because a numeric `user:` otherwise makes Docker default `HOME` to `/`.

### One-time migration (required — a plain restart is NOT enough)

**All FOUR** writable named volumes were written by root and stay root-owned across a
rebuild, so the new uid cannot write its own state until they are chowned. Every one of
them is a persisted login/token store or live state — `omnigent-claude`, `omnigent-codex`
AND `omnigent-cursor` all hold agent credentials, and `omnigent-data` holds host/runner
state. Miss any of them and that worker cannot refresh, so it silently loses
authentication rather than failing loudly. Do all three steps together:

```bash
# Resolve everything FIRST — every command below depends on it, and every variable here
# exists because assuming it silently targeted the WRONG thing at least once.
#
#  * PROJECT: compose derives it from the working directory, so the same commands run
#    from a different directory address a DIFFERENT volume set — they create and chown
#    empty replacements while the real root-owned volumes are untouched. The container's
#    own label is the only authoritative answer. `:?` aborts rather than letting an empty
#    -p select the default project.
#  * REPO: absolute paths so the block is safe from any cwd.
#  * --env-file: the normal bring-up uses it (see above). Without it OPENROUTER_API_KEY
#    and OMNIGENT_WS_ALLOWED_ORIGINS come back unset, breaking pi gateway auth and tunnel
#    CSRF checks with no obvious link to the uid change.
REPO=$(git -C /home/zoe/assistant rev-parse --show-toplevel)
PROJECT=$(docker inspect zoe-omnigent --format '{{index .Config.Labels "com.docker.compose.project"}}')
: "${PROJECT:?could not read the compose project label — is zoe-omnigent running?}"
echo "compose project = $PROJECT   repo = $REPO"

# NOTE on ZOE_WORKTREE_ROOT: compose reads ONLY the --env-file below, while
# host-native zoe-data also loads services/zoe-data/.env (service file wins for
# the app). If you ever override the worktree root, set it HERE in $REPO/.env —
# setting it only in services/zoe-data/.env moves the queue's worktrees while
# leaving this container's mount on the default, and the prepared branch goes
# invisible in-container (#1582). Do NOT just add a second --env-file: compose
# hard-fails (exit 15) when the file is absent, and services/zoe-data/.env is
# gitignored/optional.
COMPOSE="docker compose -p $PROJECT --env-file $REPO/.env -f $REPO/modules/omnigent/docker-compose.module.yml"

# STOP FIRST, before the build. The build takes minutes, and a still-running root
# container keeps writing root-owned files into the bind-mounted checkout for all of it —
# recreating the exact problem this migration exists to fix.
$COMPOSE stop omnigent
$COMPOSE build omnigent
# Chown through Compose, not host paths: hard-coding /var/lib/docker/volumes/<project>_*
# assumes a rootful daemon, the default data-root, and a known prefix — any of which can
# be wrong, and the failure mode is chowning nothing (or the wrong volumes) silently.
$COMPOSE run --rm --no-deps --user 0:0 --entrypoint sh omnigent \
  -c 'chown -R 1000:1000 /root/.omnigent /root/.claude /root/.codex /root/.cursor'
# The pipeline-journal FILE bind (see compose) requires the file to EXIST on the
# host before up: docker materialises an absent bind source as a DIRECTORY,
# which breaks the store. Idempotent — safe to re-run.
mkdir -p /home/zoe/.zoe && touch /home/zoe/.zoe/engineering_pipeline_runs.jsonl
$COMPOSE up -d omnigent
```

Stop before chowning: a still-running root container writes new root-owned files into the
volumes underneath you. The read-only binds (`/root/.config/gh`, `/root/.config/zoe` —
the Greptile key for the closeout merge loop — `/root/.local/share/cursor-agent`, the
`/home/zoe/...` tool paths) need nothing — they come from zoe-owned host paths and uid
1000 can already read them.

`omnigent-data` is ~12 GB, so the chown is slow but metadata-only. **Check RAM before
building** — the box gates on it, and an image build alongside the 6 GB `llama-server`
can OOM the live brain (`docs/knowledge/memory-pressure-profile.md`).

Verify afterwards, in this order — a green container is not proof:

```bash
docker exec zoe-omnigent id                       # expect uid=1000(zoe)
for d in .omnigent .claude .codex .cursor; do \
  docker exec zoe-omnigent touch /root/$d/.wtest && echo "$d writable"; done
find /home/zoe/assistant/.git -not -user zoe      # expect no new entries over time
# Pipeline journal reachable, kill switch NOT: the journal bind must be a
# writable FILE (a directory here means the container came up before the host
# file existed — re-run the touch from bring-up and recreate the container),
# and the host executor's emergency stop must be invisible in-container — the
# workload the switch controls must not be able to disable it (#1582).
docker exec zoe-omnigent sh -c \
  'test -f /root/.zoe/engineering_pipeline_runs.jsonl && test -w /root/.zoe/engineering_pipeline_runs.jsonl \
   && ! test -e /root/.zoe/multica_dispatch_paused && echo "journal ok, kill switch isolated"'
```

```bash
# The actual point of the change: new files in the bind-mounted repo must land
# zoe-owned. Nothing else proves the fix worked — a green container does not.
find /home/zoe/assistant/.git -not -user zoe -not -name '.gh_credentials' | wc -l
```

Expect that count to stay at 0 as omnigent works. Before the switch it reached 47 in
about an hour. If it climbs again, the container is still writing as root and `user:`
did not take — check `docker exec zoe-omnigent id` before anything else.

If omnigent cannot write any of the four state dirs, that volume's chown was missed —
revert by removing `user:` and restarting while it is investigated.

## Auth — OAuth subscriptions (not API keys)
The harnesses authenticate with the **subscription logins** (Claude Pro/Max, ChatGPT/Codex,
Cursor), not metered API keys.

**Critical gotcha:** if `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are set in the environment, the
CLIs use the **API (metered billing) and ignore the subscription**. So those env vars are
deliberately *absent* from the compose file. Don't add them back.

**One-time login** (tokens then persist in the credential volumes and auto-refresh):
```bash
docker exec -it zoe-omnigent claude            # /login in the TUI → paste the code
docker exec -it zoe-omnigent codex login       # "Sign in with ChatGPT"
docker exec -it zoe-omnigent env NO_OPEN_BROWSER=1 cursor-agent login   # prints a URL to open
docker exec -it zoe-omnigent cursor-agent status   # verify
```
Each is a headless device/paste flow (no browser in the container). Alternatively, copy an
existing login in from a machine where you're already signed in: `~/.claude`, `~/.codex`,
`~/.cursor` → the matching `omnigent-*` volume.

**Security:** these credential files are bearer tokens to your paid accounts, living in Docker
volumes on a host behind a public tunnel. Treat them like the rest of the secret/env topology —
volume-only, never in the image or in env, and gate the tunnel with Cloudflare Access.

**Heads-up:** using personal subscription OAuth inside an automated server is a gray area on
some providers (device limits, ToS, session invalidation). Worth confirming per provider before
leaning on it for unattended runs.

## Workers (polly roster) + the `pi` / OpenRouter gateway
`polly` (Omnigent's bundled orchestrator) delegates to three workers: `claude_code`
(claude-native), `codex` (codex-native), and **`pi`** (`@earendil-works/pi-coding-agent`, the
review/explore specialist and the only worker that runs gateway models). All three CLIs are
installed in the image; a worker is "available" only if its binary is on PATH (`pi: true` shows
in `GET /v1/hosts → configured_harnesses`).

`pi` here is a **separate, vanilla install** of the same upstream agent as `services/zoe-core`'s
brain — pinned to `0.82.1` to match core, but with **no** Zoe extensions / Gemma provider / soul.
It does not share state or creds with core's Pi.

`pi` is wired to **OpenRouter** (default model `minimax/minimax-m3` — tool-calling + 1M context,
a third distinct vendor for genuine cross-vendor review). Wiring:
- `entrypoint.sh` idempotently seeds a `kind: gateway` provider into `~/.omnigent/config.yaml`
  (`base_url: https://openrouter.ai/api/v1`, `wire_api: chat` — OpenRouter has no Responses API,
  `default: ["pi"]` so claude_code/codex subscription auth is untouched).
- The key is `OPENROUTER_API_KEY` in the repo `.env` (gitignored), passed into the container via
  compose; config.yaml stores only an `env:OPENROUTER_API_KEY` ref, never the value.
- **The key must reach the RUNNER** (where pi's provider resolves): omnigent's runner env is
  allowlisted and only forwards a fixed credential set (`ANTHROPIC_*`/`OPENAI_*`/`GEMINI_*`/`GIT_*`),
  NOT `OPENROUTER_API_KEY`. Without forwarding, pi fails to boot with *"Set the variable in the
  environment."* `OMNIGENT_RUNNER_ENV_PASSTHROUGH=OPENROUTER_API_KEY` (compose) is omnigent's
  documented knob to forward extra `env:` refs to the runner.
- Change the default model by editing the seed in `entrypoint.sh`; polly can also override
  per-dispatch with `args.model`.

## GitHub access for the workers (push + open PRs)
Each worker opens its OWN PR, so they need `gh` + git push auth. `gh` is installed in the image;
the host's gh login is mounted **read-only** at `/root/.config/gh` and the entrypoint runs
`gh auth setup-git` so the workers' `git push` uses it. NOTE: this is the operator's personal token
(broad `repo` scope). To narrow blast radius, replace the host's gh login with a **fine-grained PAT**
scoped to this repo (`contents` + `pull-requests`: write) — the mount path is unchanged.

## Server login — Cloudflare Access (header mode)
The Omnigent web UI is gated **externally by Cloudflare Access** on the tunnel; Omnigent itself
runs auth-less and trusts every request as the reserved `local` user. Wiring:
- Omnigent runs with `OMNIGENT_AUTH_PROVIDER=header` + `OMNIGENT_LOCAL_SINGLE_USER=1`. The
  header provider resolves header-less requests to the reserved `"local"` identity
  (`omnigent/server/auth.py`), so the runner tunnel is always accepted (no token to expire).
- **Cloudflare Access is the gate**: `https://buildzoe.the411.life` → `zoe-cloudflared` →
  `http://zoe-omnigent:6767` (both on `zoe-network`). Unauthenticated requests 302 to
  `the411.cloudflareaccess.com`.
- **Bring up with the repo .env** (journal pre-create first — an absent bind source
  materialises as a directory and breaks the pipeline store; idempotent):
  `mkdir -p /home/zoe/.zoe && touch /home/zoe/.zoe/engineering_pipeline_runs.jsonl && docker compose --env-file ../../.env -f docker-compose.module.yml up -d`
- **The host port is `127.0.0.1:6767` only** (host-local debugging), NOT published to the LAN —
  in header mode the server is auth-less, so a LAN-published port would let any LAN device act as
  `local` with the mounted workspace + agent credentials. The Access-gated tunnel is unaffected:
  cloudflared reaches Omnigent over the internal `zoe-network` (`http://zoe-omnigent:6767`), not
  the host port. To use Omnigent on the LAN, go through the tunnel (`buildzoe.the411.life`).

### Why not OIDC over the tunnel
Omnigent's OIDC issuer/redirect are pinned to the LAN origin `http://zoe.local:6767`. Reached
through Cloudflare (`buildzoe.the411.life`, HTTPS) the login crosses origins: the
`__Host-ap_auth_state` cookie is set on the Cloudflare host but the OIDC callback returns to
`http://zoe.local`, so the cookie never comes back → **`{"error":"Missing auth state cookie"}`**
(`omnigent/server/routes/auth.py`). A single OIDC client can't straddle both origins. OIDC
(`OMNIGENT_AUTH_PROVIDER=oidc`, the commented block in the compose) works only via the LAN
`http://zoe.local:6767` path, where `zoe-auth` seeds the `omnigent` client
(`services/zoe-auth/oidc/startup.py`, secret `OMNIGENT_OIDC_CLIENT_SECRET`).

## Server start / runner
- CMD is the foreground server: `omnigent server --host 0.0.0.0 --port 6767 --no-open`
  (bare `server` is the documented Docker entrypoint; `server start` daemonizes and crash-loops).
- Still TODO for actually running agents: register a **host** (`omnigent host`) — the server is
  only the control plane; "no hosts" until one is registered.
- Tunnel: add the `buildzoe.the411.life` ingress to `config/cloudflared-config.yml` →
  `http://zoe-omnigent:6767`, and gate it with a **Cloudflare Access** policy (required — the
  server is auth-less in header mode; see *Server login* above).

## Code-intel tooling (Serena / codebase-memory / opensrc)
The container has the Zoe repo at `/workspace` but originally **none** of the code-intel
tooling, so the MCP servers wired in the repo's `.mcp.json` (host paths
`/home/zoe/.local/bin/...`) could not resolve inside it (audit fix #4,
`docs/agent-setup-audit.md`).

**Approach: mount, don't rebuild.** The compose mounts the host's tool installs **read-only
at their identical host paths**, so the absolute paths in the root `.mcp.json` and
`.codex/config.toml` resolve verbatim:

| Mount (host → container, ro) | Provides |
|---|---|
| `/home/zoe/.local/bin` | `serena`, `codebase-memory-mcp`, `opensrc` launchers |
| `/home/zoe/.local/share/uv` | serena's venv (`tools/serena-agent`) + its uv-managed CPython |
| `/home/zoe/.cursor-server/.../opensrc` | the real `opensrc` aarch64 binary the symlink targets |
| `/home/zoe/.opensrc` | opensrc's source cache |

`PATH` is prepended with `/home/zoe/.local/bin` so the tools resolve as bare commands too.
`codebase-memory-mcp` is a self-contained static aarch64 ELF; `serena` and `opensrc` are
symlinks whose targets are covered by the mounts above.

**Container `.mcp.json`:** the tracked `modules/omnigent/.mcp.json` is bind-mounted (read-only)
over `/workspace/.mcp.json`, so Claude Code with `--project /workspace` auto-loads it.
`codebase-memory` runs in-container from the read-only bin mount above; **serena does not**.

**Serena is the host's SHARED server — never a stdio spawn here.** The old config gave serena
a `command` + `--transport stdio`, so every agent session started its own server: ~900 MB RSS
each, on a 15.6 GB box that also runs llama-server + Kokoro. That pressure starved the deploy
gate and contributed to llama-server CUDA-OOM crashes. The entry is now
`{"type": "http", "url": "http://172.28.0.1:9121/mcp"}` — the host's `serena-mcp.service`,
reached over `zoe-codeintel`:

- `zoe-codeintel` is an `internal` Docker network (subnet pinned to `172.28.0.0/24`) declared
  in `docker-compose.module.yml`, with exactly one member: this container, pinned at
  `172.28.0.2`. `zoe-network` is unchanged, so cloudflared still reaches `zoe-omnigent:6767`.
- Serena itself still binds `127.0.0.1` only. The root units
  `scripts/setup/systemd/system/serena-bridge.{socket,service}` proxy the gateway address to
  that loopback port, with `IPAddressDeny=any` / `IPAddressAllow=172.28.0.2/32`.
- **The access list — not the network — is what scopes it.** Measured 2026-07-22: any container
  on any bridge can reach a HOST address (gateways included), because host-local delivery goes
  through INPUT while Docker's isolation rules live in FORWARD. Cross-network access to
  *container* addresses is blocked; host addresses are not.
- Serena's `--project` is the host checkout `/home/zoe/assistant`, which is the same tree as
  `/workspace` (`../../:/workspace`), so its relative paths resolve identically in-container.

Install the bridge units before recreating this container — `scripts/setup/systemd/README.md`
has the commands and the mandatory negative control. `tests/unit/modules/test_omnigent_mcp_config.py`
fails if a stdio serena comes back or the pinned addresses drift apart.

**Repo rules:** the repo-root `CLAUDE.md` (tracked, `@AGENTS.md`-includes the hub) is visible
at `/workspace/CLAUDE.md`, so Claude-in-container reads the rules.

**Apply (operator, one-time):** these mounts change the container definition, so a
`docker compose ... up -d` recreate is required to pick them up — the running container was
intentionally NOT recreated by the change:
```bash
# journal pre-create first — absent bind source materialises as a directory (idempotent)
mkdir -p /home/zoe/.zoe && touch /home/zoe/.zoe/engineering_pipeline_runs.jsonl
docker compose --env-file ../../.env -f docker-compose.module.yml up -d   # recreates with mounts
docker exec zoe-omnigent serena --help >/dev/null && echo serena-ok
docker exec zoe-omnigent codebase-memory-mcp --help >/dev/null && echo cbm-ok
docker exec zoe-omnigent opensrc --version
```
Verified against the exact base image (`python:3.12-slim-bookworm`) in a throwaway `--rm`
container: all three resolve and run with only these mounts (no image rebuild).
