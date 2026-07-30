#!/usr/bin/env bash
# Run the Omnigent OIDC server AND attach this machine as a host/runner, in one
# container, so a single box gets both the web UI and an agent runner that survives
# restarts. The login token (from `omnigent login`) persists in the omnigent-data
# volume, so the host re-authenticates automatically on every boot.
set -uo pipefail

SERVER_URL="${OMNIGENT_SELF_URL:-http://zoe.local:6767}"

# Clear stale host-daemon tracking from a previous container. These live in the persisted
# omnigent-data volume (host.pid + daemons/*.json) and reference PIDs that died with the old
# container. On a fresh boot no omnigent daemon can be running yet, so any such record is stale;
# left in place, `omnigent host` bails with "a host daemon is already running" and the server
# re-adopts the OLD registration WITHOUT re-probing harness availability — which is why a newly
# installed worker (e.g. pi) shows up as unavailable after an image rebuild. Removing them forces
# a fresh host attach that re-detects the roster. (Keeps host_id — that lives in config.yaml.)
rm -f "${HOME}/.omnigent/host.pid" "${HOME}/.omnigent"/daemons/*.json 2>/dev/null || true

# Make the mounted host cursor-agent resolvable on PATH. Symlink the REAL versioned binary
# (resolved via realpath by the launcher) so its bundled node sits beside it.
# Target /root/.local/bin, NOT /usr/local/bin: the container runs as uid 1000 (see
# `user:` in docker-compose.module.yml) and /usr/local/bin is root-owned 755, so the
# symlink silently failed there — `ln -sf` has no error check, so cursor-agent simply
# never appeared on PATH and the Cursor harness broke with no message. /root/.local/bin
# is owned by 1000 (chowned in the Dockerfile) and already ahead of /usr/local/bin on
# PATH. Failure is now reported rather than swallowed.
# HONOUR THE PIN — NEVER pick the newest. The old `sort -V | tail -1` fallback silently
# selected the highest version directory present, so a host with a newer directory lying
# around (an update, then a rollback to the pin) ran the newer, UNREVIEWED binary — or a
# build below omnigent 0.7.0's per-harness minimum — while install_cursor_agent.sh --check
# reported the pin as satisfied. Which binary actually runs is load-bearing, not cosmetic,
# so newest-wins is gone: use the host's pin, else the repo-declared pin, else fail closed.
#
# DECLARED_PIN must match scripts/setup/install_cursor_agent.sh (CURSOR_PINNED_VERSION).
DECLARED_PIN="2026.07.23-e383d2b"
VERSIONS_ROOT="/root/.local/share/cursor-agent/versions"

# Confirm a candidate binary actually reports the version we expect before trusting it — an
# executable file is not proof of a working, correctly-versioned install.
_reports_pin() {  # <binary> <expected-version>
  local got
  got="$("${1}" --version 2>/dev/null | head -1)" || return 1
  [ "${got}" = "${2}" ]
}

# The host's own symlink is its pin: /home/zoe/.local/bin/cursor-agent -> .../versions/<pinned>/cursor-agent.
# That bin dir is mounted read-only (see compose), but the symlink TARGET is a host path that
# does not exist in-container, so the link itself dangles — read the version out of it and
# select that directory under the mounted versions/ root.
_pin_link=/home/zoe/.local/bin/cursor-agent
cursor_bin=""

# 1) Prefer the host's pin when present and executable in the mounted versions/ root.
if [ -L "${_pin_link}" ]; then
  _pinned_ver="$(basename "$(dirname "$(readlink "${_pin_link}")")")"
  _cand="${VERSIONS_ROOT}/${_pinned_ver}/cursor-agent"
  if [ -x "${_cand}" ]; then
    cursor_bin="${_cand}"
    echo "[entrypoint] cursor-agent pinned by host symlink -> ${_pinned_ver}"
  else
    echo "[entrypoint] host pins cursor-agent ${_pinned_ver} but it is absent or not executable in the mounted versions/ — trying the repo-declared pin ${DECLARED_PIN}" >&2
  fi
fi

# 2) No usable host pin: activate ONLY the repository-declared pin, and verify its reported
#    --version before linking. Never the newest directory.
if [ -z "${cursor_bin}" ]; then
  _cand="${VERSIONS_ROOT}/${DECLARED_PIN}/cursor-agent"
  if [ -x "${_cand}" ] && _reports_pin "${_cand}" "${DECLARED_PIN}"; then
    cursor_bin="${_cand}"
    echo "[entrypoint] cursor-agent set to repo-declared pin -> ${DECLARED_PIN}"
  fi
fi

# 3) FAIL CLOSED. Neither the host pin nor the declared pin yielded a verified binary.
#    Linking an arbitrary version would defeat the pin and risk a below-minimum harness, so
#    refuse rather than guess — a loud non-zero exit beats a silently wrong Cursor harness.
if [ -z "${cursor_bin}" ]; then
  echo "[entrypoint] ERROR: no verified cursor-agent pin available — host pin absent/broken and ${VERSIONS_ROOT}/${DECLARED_PIN}/cursor-agent is missing or unverifiable. Run scripts/setup/install_cursor_agent.sh on the host, then recreate this container." >&2
  exit 1
fi

mkdir -p /root/.local/bin
if ln -sf "${cursor_bin}" /root/.local/bin/cursor-agent; then
  echo "[entrypoint] cursor-agent linked -> ${cursor_bin}"
else
  echo "[entrypoint] WARNING: could not link cursor-agent into /root/.local/bin — the Cursor harness will not resolve" >&2
fi

# GitHub auth for the workers: with the host's gh login mounted read-only at
# ~/.config/gh, `gh` is authenticated; wire git so the workers' `git push` (each opens its
# own PR) uses gh as the https credential helper. No-op if gh or the mounted login is absent.
if command -v gh >/dev/null 2>&1 && [ -f "${HOME}/.config/gh/hosts.yml" ]; then
  gh auth setup-git 2>/dev/null && echo "[entrypoint] gh auth wired for git ($(gh api user --jq .login 2>/dev/null || echo '?'))" \
    || echo "[entrypoint] gh auth setup-git failed (workers may not be able to push)"
else
  echo "[entrypoint] gh CLI or ~/.config/gh/hosts.yml missing — workers cannot push/open PRs"
fi

# Default-workspace patch: Omnigent defaults a session's workspace to the host's HOME
# (/root) when the UI doesn't specify one, and the UI doesn't always let you change it.
# Redirect home-defaulted sessions to OMNIGENT_RUNNER_WORKSPACE (/workspace = the repo).
# Idempotent; degrades to a no-op if the upstream line moves on an Omnigent upgrade.
python3 - <<'PYEOF' || echo "[entrypoint] workspace patch skipped"
import glob, os
for p in glob.glob("/root/.local/share/uv/tools/omnigent/lib/python*/site-packages/omnigent/host/connect.py"):
    s = open(p).read()
    if "ZOE_WORKSPACE_DEFAULT" in s:
        print("[entrypoint] workspace patch already applied"); break
    marker = "workspace = Path(frame.workspace).expanduser()"
    if marker not in s:
        print("[entrypoint] workspace patch marker not found (Omnigent changed?)"); break
    inject = (marker
        + "\n        import os as _zoe_os  # ZOE_WORKSPACE_DEFAULT"
        + "\n        if workspace == Path.home() and _zoe_os.environ.get('OMNIGENT_RUNNER_WORKSPACE'):"
        + "\n            workspace = Path(_zoe_os.environ['OMNIGENT_RUNNER_WORKSPACE']).expanduser().resolve()")
    open(p, "w").write(s.replace(marker, inject, 1))
    print("[entrypoint] workspace patch applied")
PYEOF

# Seed the OpenRouter gateway provider for the `pi` worker (idempotent, append-only).
# pi is polly's review/explore specialist and the only worker that runs gateway models;
# this points it at OpenRouter (MiniMax M3 default) without touching the claude_code /
# codex subscription auth. The key never lands in config.yaml — only an env: ref, resolved
# at runtime from OPENROUTER_API_KEY (passed in via compose). `default: ["pi"]` scopes the
# default to the pi surface only, so the anthropic/openai family defaults are untouched.
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  python3 - <<'PYEOF' || echo "[entrypoint] openrouter provider seed skipped"
import os, yaml
path = os.path.expanduser("~/.omnigent/config.yaml")
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
except FileNotFoundError:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
# omnigent's config.yaml keys providers by NAME under a `providers:` MAPPING (load_providers
# returns {} for anything that isn't a dict). The entry body carries kind + the family block(s),
# NOT a `name` field — the name is the mapping key.
entry = {
    "kind": "gateway", "default": ["pi"],
    "openai": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_ref": "env:OPENROUTER_API_KEY",
        "wire_api": "chat",  # OpenRouter speaks Chat Completions, not the Responses API
        "models": {"default": "minimax/minimax-m3"},
    },
}
provs = cfg.get("providers")
# A non-dict providers block (absent, or an earlier buggy LIST form that omnigent silently
# ignored) is reset to an empty mapping — the only thing it could have held is our own
# non-functional list entry, so nothing usable is lost.
if not isinstance(provs, dict):
    provs = {}
# Create-only: if an `openrouter` provider already exists, leave it ENTIRELY alone — the
# operator owns it after first seed (a changed default model, an added anthropic family, etc.
# must survive reboots). Only seed when absent.
if "openrouter" in provs:
    print("[entrypoint] openrouter provider already present — leaving operator config untouched")
else:
    provs["openrouter"] = entry
    cfg["providers"] = provs
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print("[entrypoint] openrouter provider seeded in ~/.omnigent/config.yaml (pi -> minimax/minimax-m3)")
PYEOF
else
  echo "[entrypoint] OPENROUTER_API_KEY not set — skipping pi/OpenRouter provider seed"
fi

# Foreground web/OIDC server (the process the container's lifetime is tied to).
omnigent server --host 0.0.0.0 --port 6767 --no-open &
server_pid=$!

# Wait for the server to accept connections before attaching the host.
for _ in $(seq 1 60); do
  curl -sf -o /dev/null "http://127.0.0.1:6767/" && break
  sleep 1
done

# Attach as host/runner only once authenticated (token keyed by server URL).
if [ -f /root/.omnigent/auth_tokens.json ]; then
  echo "[entrypoint] attaching host runner -> ${SERVER_URL}"
  omnigent host "${SERVER_URL}" &
else
  echo "[entrypoint] no auth token yet — register the host once with:"
  echo "[entrypoint]   docker exec -it zoe-omnigent omnigent login ${SERVER_URL}"
fi

# Keep the container alive on the server; exit with it.
wait "${server_pid}"
