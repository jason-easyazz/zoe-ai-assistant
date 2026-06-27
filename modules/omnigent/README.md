# Omnigent module (builds & runs on this aarch64/Tegra host)

Meta-harness that orchestrates the agent CLIs (Claude Code, Codex, Cursor, Pi) from one
control plane with a web/mobile UI. Intended to run as a Zoe module behind `zoe-cloudflared`,
mirroring the `agent-zero` module pattern.

## Build/run status (verified 2026-06-17 on this aarch64/Tegra host)

| Component        | arm64 status | Notes |
|------------------|--------------|-------|
| `claude` CLI     | ✅ works      | `@anthropic-ai/claude-code` 2.1.179, npm install clean |
| `codex` CLI      | ✅ works      | `@openai/codex` codex-cli 0.140.0, npm install clean |
| `cursor-agent`   | ✅ works      | already installed on host (`~/.local/bin`) |
| **omnigent core**| ✅ works      | installs via a locally-built aarch64 wheel — see below |

### The blocker (resolved)
`omnigent` depends on `cel-expr-python` (a wrapper around the CEL C++ engine, used by the
policy system). Upstream publishes **no `linux aarch64` wheel and no sdist** — and the root
cause is in their own `release/build_wheel.sh`, which hardcodes `bazelisk-linux-amd64`.

**Fix:** we compiled the wheel from source for aarch64 (cel-cpp 0.15 + Abseil + Protobuf 33 +
antlr4 via Bazel), swapping in arm64 bazelisk. The result is vendored at
`wheels/cel_expr_python-0.1.2-cp312-cp312-linux_aarch64.whl`; the Dockerfile points `uv` at it
via `UV_FIND_LINKS`. Verified: `omnigent 0.1.1` runs in the built image on this Tegra host.

To rebuild the wheel (e.g. on a version bump): clone `github.com/cel-expr/cel-python`, install
`bazelisk-linux-arm64` as `bazel`, copy `release/*` to the repo root, set the version in
`pyproject.toml`, then `pip wheel . --no-build-isolation`. Cap memory if building on the live
box (we used a 4 GB / `--jobs=1` container so it couldn't starve the running stack). Worth
upstreaming as an aarch64 CI target.

The `Dockerfile` here is otherwise correct and builds fine up to the omnigent install step; it
would succeed on an x86_64 host (or once the aarch64 wheel lands).

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

## Server login — Cloudflare Access (header mode)
The Omnigent web UI is gated **externally by Cloudflare Access** on the tunnel; Omnigent itself
runs auth-less and trusts every request as the reserved `local` user. Wiring:
- Omnigent runs with `OMNIGENT_AUTH_PROVIDER=header` + `OMNIGENT_LOCAL_SINGLE_USER=1`. The
  header provider resolves header-less requests to the reserved `"local"` identity
  (`omnigent/server/auth.py`), so the runner tunnel is always accepted (no token to expire).
- **Cloudflare Access is the gate**: `https://buildzoe.the411.life` → `zoe-cloudflared` →
  `http://zoe-omnigent:6767` (both on `zoe-network`). Unauthenticated requests 302 to
  `the411.cloudflareaccess.com`.
- **Bring up with the repo .env**:
  `docker compose --env-file ../../.env -f docker-compose.module.yml up -d`
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

**Container `.mcp.json`:** the root `.mcp.json` pins serena to `--project /home/zoe/assistant`,
but inside the container the repo lives at `/workspace`. Use the tracked container-relative
`modules/omnigent/.mcp.json` (serena `--project /workspace`) for Claude-in-container — copy it
to `/workspace/.mcp.json` (or to the agent's cwd) if the host root `.mcp.json` is not the one
that should be active inside the container.

**Repo rules:** the repo-root `CLAUDE.md` (tracked, `@AGENTS.md`-includes the hub) is visible
at `/workspace/CLAUDE.md`, so Claude-in-container reads the rules.

**Apply (operator, one-time):** these mounts change the container definition, so a
`docker compose ... up -d` recreate is required to pick them up — the running container was
intentionally NOT recreated by the change:
```bash
docker compose --env-file ../../.env -f docker-compose.module.yml up -d   # recreates with mounts
docker exec zoe-omnigent serena --help >/dev/null && echo serena-ok
docker exec zoe-omnigent codebase-memory-mcp --help >/dev/null && echo cbm-ok
docker exec zoe-omnigent opensrc --version
```
Verified against the exact base image (`python:3.12-slim-bookworm`) in a throwaway `--rm`
container: all three resolve and run with only these mounts (no image rebuild).
