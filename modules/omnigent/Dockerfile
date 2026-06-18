# Omnigent meta-harness — runs as a Zoe module behind the existing cloudflared tunnel.
# Base is python:3.12 because omnigent requires Python 3.12.
FROM python:3.12-slim-bookworm

# ── System deps ──────────────────────────────────────────────────────────────
#   git/tmux  : harness runtime (terminal launchers, worktrees)
#   bubblewrap: omnigent's OS sandbox (relaxed at runtime — the container is the boundary)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git tmux bubblewrap \
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

# ── Cursor harness (second-class) ────────────────────────────────────────────
# Requires piping the cursor.com installer, which needs a remote-script permission.
# Left opt-in: uncomment after granting permission, or COPY the host's proven binary in.
# RUN curl https://cursor.com/install -fsS | bash && /root/.local/bin/cursor-agent --version

# ── Locally-built dependency ─────────────────────────────────────────────────
# omnigent depends on cel-expr-python, which ships NO linux/aarch64 wheel and no sdist
# (upstream's build hardcodes amd64 bazelisk). This wheel was compiled from source for
# aarch64 — see wheels/ and README. UV_FIND_LINKS lets uv resolve it locally.
COPY wheels/ /opt/wheels/
ENV UV_FIND_LINKS=/opt/wheels

# ── uv + Omnigent ────────────────────────────────────────────────────────────
# uv installed via pip (avoids piping the astral bootstrap script).
RUN pip install --no-cache-dir uv \
    && uv tool install --force -q --python 3.12 omnigent
ENV PATH="/root/.local/bin:${PATH}"
RUN timeout 30s omnigent --version || echo "WARN: confirm 'omnigent --version' at runtime"

COPY entrypoint.sh /usr/local/bin/omnigent-entrypoint.sh
RUN chmod +x /usr/local/bin/omnigent-entrypoint.sh

WORKDIR /workspace
EXPOSE 6767

# Entrypoint runs the foreground OIDC server AND attaches a host/runner (see entrypoint.sh).
# (Bare `omnigent server` alone has no runner → "no hosts"; `server start` daemonizes/crash-loops.)
CMD ["/usr/local/bin/omnigent-entrypoint.sh"]
