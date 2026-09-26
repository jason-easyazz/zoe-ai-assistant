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
# so newest-wins is gone: use the host's pin (verified), else the repo-declared pin
# (verified), else fail closed — NON-FATALLY: link nothing and warn, so the server still
# boots with Cursor simply unavailable rather than the container restart-looping.
#
# DECLARED_PIN must match scripts/setup/install_cursor_agent.sh (CURSOR_PINNED_VERSION).
# The three paths below carry their production defaults; the CURSOR_* env overrides exist
# only so the selection logic can be exercised hermetically in tests (same pattern as
# OMNIGENT_SELF_URL above). The container never sets them.
DECLARED_PIN="2026.07.23-e383d2b"
VERSIONS_ROOT="${CURSOR_VERSIONS_ROOT:-/root/.local/share/cursor-agent/versions}"
LINK_DIR="${CURSOR_LINK_DIR:-/root/.local/bin}"

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
_pin_link="${CURSOR_PIN_LINK:-/home/zoe/.local/bin/cursor-agent}"
cursor_bin=""

# 1) Prefer the host's pin — but accept it ONLY if the binary it points at actually reports
#    the declared pin. A host symlink can point at a stale/below-minimum build (e.g. an old
#    2026.01.28), and an `-x` test alone would happily link it; verify --version before
#    trusting it, exactly as the declared-pin fallback below does. Clear any stale in-container
#    link first so a broken host pin cannot leave an old version resolvable.
rm -f "${LINK_DIR}/cursor-agent" 2>/dev/null || true
if [ -L "${_pin_link}" ]; then
  _pinned_ver="$(basename "$(dirname "$(readlink "${_pin_link}")")")"
  _cand="${VERSIONS_ROOT}/${_pinned_ver}/cursor-agent"
  if [ -x "${_cand}" ] && _reports_pin "${_cand}" "${DECLARED_PIN}"; then
    cursor_bin="${_cand}"
    echo "[entrypoint] cursor-agent pinned by host symlink -> ${_pinned_ver}"
  else
    echo "[entrypoint] host pins cursor-agent ${_pinned_ver} but it is absent or does not report the declared pin ${DECLARED_PIN} in the mounted versions/ — trying the repo-declared pin directly" >&2
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

# 3) FAIL CLOSED, but NON-FATAL. Neither the host pin nor the declared pin yielded a verified
#    binary. Linking an arbitrary/below-minimum version would defeat the pin, so link NOTHING
#    — but do NOT exit: the omnigent server must still boot, just with the Cursor harness
#    unavailable (omnigent reports it so and everything else keeps working). A hard exit here
#    would restart-loop the whole container. Clear any stale active symlink first so no wrong
#    version is ever left resolvable on PATH.
if [ -z "${cursor_bin}" ]; then
  rm -f "${LINK_DIR}/cursor-agent" 2>/dev/null || true
  echo "[entrypoint] WARNING: no verified cursor-agent pin available — host pin absent/unverified and ${VERSIONS_ROOT}/${DECLARED_PIN}/cursor-agent is missing or does not report ${DECLARED_PIN}. Cursor harness UNAVAILABLE; the server is still starting. Run scripts/setup/install_cursor_agent.sh on the host, then recreate this container." >&2
else
  mkdir -p "${LINK_DIR}"
  if ln -sf "${cursor_bin}" "${LINK_DIR}/cursor-agent"; then
    echo "[entrypoint] cursor-agent linked -> ${cursor_bin}"
  else
    echo "[entrypoint] WARNING: could not link cursor-agent into ${LINK_DIR} — the Cursor harness will not resolve" >&2
  fi
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

# Codex MCP config seed — the Codex counterpart of the bind-mounted /workspace/.mcp.json.
# /root/.codex/config.toml lives in the persisted omnigent-codex VOLUME and nothing in the
# repo used to own it: it was hand-patched live on 2026-09-25 (serena `command =` stdio
# spawn -> shared-server `url =`), so a volume reset silently reverted to a private ~700 MB
# Serena per Codex session. This rewrites ONLY the managed [mcp_servers.<name>] tables from
# the baked template (see codex-mcp.toml) and keeps everything else in the file — Codex's
# own hooks.state trusted hashes, any other server — verbatim. Idempotent: an already-current
# file is not touched. NON-FATAL by design: an unparseable file, a bad template or a merge
# that would not round-trip leaves the file alone with a warning; the server still boots.
# The two overrides exist only so tests can exercise this hermetically (same pattern as
# the CURSOR_* seams above); the container never sets them.
CODEX_CONFIG_PATH="${CODEX_CONFIG_PATH:-${HOME}/.codex/config.toml}" \
CODEX_MCP_TEMPLATE="${CODEX_MCP_TEMPLATE:-/usr/local/share/zoe-omnigent/codex-mcp.toml}" \
python3 - <<'PYEOF' || echo "[entrypoint] codex mcp seed skipped (python error)" >&2
import os, re, shutil, sys, time
try:
    import tomllib
except ModuleNotFoundError:  # py<3.11 — hermetic tests on a 3.10 host; the image is 3.12
    import tomli as tomllib

cfg_path = os.environ["CODEX_CONFIG_PATH"]
tpl_path = os.environ["CODEX_MCP_TEMPLATE"]
TAG = "[entrypoint] codex mcp seed:"


def bail(msg):  # never fatal: the omnigent server must still boot
    print(f"{TAG} {msg}", file=sys.stderr)
    sys.exit(0)


try:
    with open(tpl_path, encoding="utf-8") as fh:
        tpl_text = fh.read()
    want = tomllib.loads(tpl_text)["mcp_servers"]
except Exception as exc:  # noqa: BLE001
    bail(f"template {tpl_path} unusable ({exc}) — leaving {cfg_path} untouched")
serena = want.get("serena", {})
if "command" in serena or "url" not in serena:
    bail("template must attach serena by url with no command — refusing to seed")

try:
    with open(cfg_path, encoding="utf-8") as fh:
        cur_text = fh.read()
except FileNotFoundError:
    cur_text = ""
try:
    have = tomllib.loads(cur_text).get("mcp_servers", {})
except Exception as exc:  # noqa: BLE001
    bail(f"{cfg_path} does not parse ({exc}) — leaving it untouched; fix it by hand")

if all(have.get(name) == body for name, body in want.items()):
    print(f"{TAG} already current ({', '.join(want)})")
    sys.exit(0)

# Drop every managed table (and any sub-table of it) textually; keep every other line.
# The name must match EXACTLY, bare (`serena`, `serena.env` = its sub-table) or quoted
# (`"serena"`): a quoted key such as `"serena.debug"` is a DIFFERENT server, not a
# sub-table, and must survive (Greptile, #1700).
names = "|".join(re.escape(n) for n in want)
managed = re.compile(
    r'^\s*\[\s*mcp_servers\s*\.\s*(?:(?:' + names + r')|"(?:' + names + r')")\s*(?:\.[^\]]*)?\]'
)
any_header = re.compile(r"^\s*\[")
kept, skipping = [], False
for line in cur_text.splitlines(keepends=True):
    if any_header.match(line):
        skipping = bool(managed.match(line))
    if not skipping:
        kept.append(line)
new_text = "".join(kept).rstrip("\n")
new_text = (new_text + "\n\n" if new_text else "") + tpl_text.rstrip("\n") + "\n"

try:
    got = tomllib.loads(new_text).get("mcp_servers", {})
except Exception as exc:  # noqa: BLE001
    bail(f"merged config would not parse ({exc}) — leaving {cfg_path} untouched")
if not all(got.get(name) == body for name, body in want.items()):
    bail(f"merged config does not carry the managed servers verbatim — leaving {cfg_path} untouched")
unmanaged_before = {n: b for n, b in have.items() if n not in want}
unmanaged_after = {n: b for n, b in got.items() if n not in want}
if unmanaged_before != unmanaged_after:
    bail(f"merge would alter an unmanaged server ({sorted(set(unmanaged_before) ^ set(unmanaged_after)) or 'body changed'}) — leaving {cfg_path} untouched")

os.makedirs(os.path.dirname(cfg_path) or ".", exist_ok=True)
if cur_text:
    shutil.copy2(cfg_path, f"{cfg_path}.bak-{time.strftime('%Y%m%d%H%M%S')}")
tmp = f"{cfg_path}.tmp-{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(new_text)
os.replace(tmp, cfg_path)
print(f"{TAG} rewrote managed [mcp_servers.*] tables in {cfg_path} ({', '.join(want)})")
PYEOF

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
