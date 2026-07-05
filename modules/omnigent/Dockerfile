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

# ── Locally-built dependency ─────────────────────────────────────────────────
# omnigent <0.4.0 depended on cel-expr-python, which ships NO linux/aarch64 wheel (this
# wheel was compiled from source for aarch64 — see wheels/ and README). As of omnigent
# 0.4.0 the dep is EXCLUDED on aarch64 upstream, so the wheel is no longer required; the
# COPY + UV_FIND_LINKS stay as a harmless belt-and-braces for any transitive resolver hit.
COPY wheels/ /opt/wheels/
ENV UV_FIND_LINKS=/opt/wheels

# ── uv + Omnigent ────────────────────────────────────────────────────────────
# uv installed via pip (avoids piping the astral bootstrap script).
# pyyaml is for the entrypoint's idempotent ~/.omnigent/config.yaml seed (the
# OpenRouter gateway provider for the pi worker) — the system python3 the
# entrypoint runs has no yaml otherwise.
# omnigent is PINNED (bump deliberately): an unpinned install is Docker-layer-cached, so
# upgrades silently DON'T happen on rebuild — and unpinned surprise upgrades bit us before
# (the 0.3.0 jump arrived as a side effect of a pi rebuild). 0.4.0 verified on this stack
# 2026-07-05: providers-mapping format, RUNNER_ENV_PASSTHROUGH, header auth, and the
# entrypoint workspace-patch marker all unchanged.
RUN pip install --no-cache-dir uv pyyaml \
    && uv tool install --force -q --python 3.12 omnigent==0.4.0
ENV PATH="/root/.local/bin:${PATH}"
RUN timeout 30s omnigent --version || echo "WARN: confirm 'omnigent --version' at runtime"

COPY entrypoint.sh /usr/local/bin/omnigent-entrypoint.sh
RUN chmod +x /usr/local/bin/omnigent-entrypoint.sh

WORKDIR /workspace
EXPOSE 6767

# Entrypoint runs the foreground OIDC server AND attaches a host/runner (see entrypoint.sh).
# (Bare `omnigent server` alone has no runner → "no hosts"; `server start` daemonizes/crash-loops.)
CMD ["/usr/local/bin/omnigent-entrypoint.sh"]
