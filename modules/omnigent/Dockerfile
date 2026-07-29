# Omnigent meta-harness — runs as a Zoe module behind the existing cloudflared tunnel.
# Base is python:3.12 because omnigent requires Python 3.12.
FROM python:3.12-slim-bookworm

# ── System deps ──────────────────────────────────────────────────────────────
#   git/tmux  : harness runtime (terminal launchers, worktrees)
#   bubblewrap: omnigent's OS sandbox (relaxed at runtime — the container is the boundary)
#   lsof      : the startup orphan-runner sweep shells out to `lsof` to find PIDs
#               holding abandoned conv-*.sock files. Without it, an unclean host
#               reboot that leaves stale /tmp/omnigent/ap-* sockets makes the
#               sweep raise FileNotFoundError → lifespan startup fails → the
#               container crash-loops forever (observed 2026-07-03: 63 restarts).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git tmux bubblewrap lsof \
    && rm -rf /var/lib/apt/lists/*

# ── Node 22 ──────────────────────────────────────────────────────────────────
# Copied from the official image rather than piping the NodeSource bootstrap script.
# bookworm base matches node:22-bookworm-slim, so the shared libs line up.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# ── Harness CLIs (verified to run on arm64/Tegra) ────────────────────────────
# Separate layers so a failure pinpoints the culprit; the --version calls smoke-test them.
RUN npm install -g @anthropic-ai/claude-code && claude --version
RUN npm install -g @openai/codex && codex --version
# Pi — polly's 3rd worker (review/explore specialist; the only worker that can run gateway
# models). Same upstream agent as zoe-core's brain (@earendil-works/pi-coding-agent), pinned to
# match core's ^0.79.3, but a SEPARATE vanilla install (no Zoe extensions/Gemma/soul). The
# zoe-data pi_runtime_probe installs it with --ignore-scripts; mirror that here.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@^0.79.3 && pi --version

# ── GitHub CLI ───────────────────────────────────────────────────────────────
# The workers (claude_code / codex / pi) each open their OWN PR, so they need `gh`
# + git push auth. Install gh from its official apt repo (downloads a keyring, not a
# piped script). The credential itself is NOT baked in — the host's gh login is mounted
# read-only at runtime (compose) and the entrypoint runs `gh auth setup-git`.
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* && gh --version

# ── Cursor harness (second-class) ────────────────────────────────────────────
# Requires piping the cursor.com installer, which needs a remote-script permission.
# Left opt-in: uncomment after granting permission, or COPY the host's proven binary in.
# RUN curl https://cursor.com/install -fsS | bash && /root/.local/bin/cursor-agent --version

# ── uv + Omnigent ────────────────────────────────────────────────────────────
# uv installed via pip (avoids piping the astral bootstrap script).
# pyyaml is for the entrypoint's idempotent ~/.omnigent/config.yaml seed (the
# OpenRouter gateway provider for the pi worker) — the system python3 the
# entrypoint runs has no yaml otherwise.
# omnigent is PINNED (bump deliberately): an unpinned install is Docker-layer-cached, so
# upgrades silently DON'T happen on rebuild — and unpinned surprise upgrades bit us before
# (the 0.3.0 jump arrived as a side effect of a pi rebuild).
#
# RELEASE AGE — OPERATOR ACCEPTANCE ON RECORD (AGENTS.md "avoid dependencies younger than
# ~14 days unless the user explicitly accepts the risk"). 0.7.0 was published 2026-07-27 and
# pinned here 2026-07-29, i.e. 2 days old. The operator asked for "the latest version"
# explicitly, was told the release was 2 days old before the build, and reaffirmed by
# directing the PR to merge. Accepted deliberately, not by omission. The rule's target is
# adopting brand-NEW packages; omnigent is a long-standing pinned dependency being moved
# forward, and the jump was de-risked by diffing every coupled surface (below) before
# building. Rollback is one line: set this pin back to 0.4.0 and rebuild.
#
# 0.7.0 verified on this stack 2026-07-29 (0.4.0 -> 0.7.0, a 3-release jump). Every surface
# this module actually couples to was diffed against the 0.4.0 wheel first:
#   * onboarding/provider_config.py is BYTE-IDENTICAL, so entrypoint.sh's OpenRouter
#     `kind: gateway` seed (providers mapping, wire_api, api_key_ref) still parses.
#   * host/connect.py still exports OMNIGENT_RUNNER_ENV_PASSTHROUGH, and still carries the
#     `workspace = Path(frame.workspace).expanduser()` marker the entrypoint patches.
#   * server/auth.py keeps OMNIGENT_AUTH_PROVIDER=header + OMNIGENT_LOCAL_SINGLE_USER ->
#     reserved "local" user; ws_origin.py keeps OMNIGENT_WS_ALLOWED_ORIGINS.
#   * the `server` and `host` CLI commands, host.pid/daemons/auth_tokens.json all unchanged.
# The one BEHAVIOUR change: ANTHROPIC_MODEL joined omnigent's own default runner-credential
# forward set, so our compose passthrough of it is now redundant (kept — see compose).
#
# aarch64 note: the cel-expr-python problem is GONE, not worked around. 0.7.0 replaced it
# with `cel-python` (a pure-Python `celpy`, py3-none-any), so there is no platform wheel to
# vendor any more — the old COPY wheels/ + UV_FIND_LINKS scaffolding was removed with this
# bump. The other new required deps (protobuf 6.x, zstandard, python-dateutil) all publish
# cp312 manylinux aarch64 wheels.
# The chown runs in THIS SAME RUN, not a later one. A separate `RUN chown -R /root`
# layer would re-record every changed file — /root is ~1.3 GB image-side, so that is
# ~1.3 GB of pure duplication in the image. Chowning before the layer is committed
# costs nothing.
#
# WHY 1000: the container runs as host uid 1000 (zoe) — see `user:` in
# docker-compose.module.yml. HOME stays /root (every mount, the uv tool install, and
# the entrypoint's ~/.omnigent config seed all target it), so /root must be owned by
# the runtime user or omnigent cannot write its own state.
RUN pip install --no-cache-dir uv pyyaml \
    && uv tool install --force -q --python 3.12 omnigent==0.7.0
# A numeric `user:` has no /etc/passwd entry, and several tools (git, uv, shells)
# fall back to "?" or fail outright when getpwuid() misses. Give uid 1000 a real
# entry whose home is /root so ~ expands correctly inside the container.
RUN groupadd --gid 1000 zoe \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /root --shell /bin/bash zoe
ENV PATH="/root/.local/bin:${PATH}"
# Smoke test BEFORE the chown, and chown in the SAME layer. The smoke test runs as
# root and writes caches/metadata under /root (uv, python), so chowning earlier would
# leave exactly those files root-owned and unwritable by the runtime uid — the failure
# would surface later as omnigent unable to update its own tool state. Everything root
# writes under /root must happen before this line.
RUN (timeout 30s omnigent --version || echo "WARN: confirm 'omnigent --version' at runtime") \
    && chown -R 1000:1000 /root \
    && chmod 750 /root

COPY entrypoint.sh /usr/local/bin/omnigent-entrypoint.sh
RUN chmod +x /usr/local/bin/omnigent-entrypoint.sh

WORKDIR /workspace
EXPOSE 6767

# Entrypoint runs the foreground OIDC server AND attaches a host/runner (see entrypoint.sh).
# (Bare `omnigent server` alone has no runner → "no hosts"; `server start` daemonizes/crash-loops.)
CMD ["/usr/local/bin/omnigent-entrypoint.sh"]
