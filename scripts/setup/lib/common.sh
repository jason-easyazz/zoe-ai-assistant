#!/usr/bin/env bash
# Shared helpers for Zoe installers (install-jetson.sh, install-pi.sh).
# Source this file: source "$(dirname "$0")/lib/common.sh"
#
# Provides: coloured logging, command/preflight checks, secret generation,
# and idempotent .env key setting. No side effects on source.

# Colour vars are consumed by scripts that source this lib.
# shellcheck disable=SC2034
# --- colours (disabled when not a tty) --------------------------------------
if [[ -t 1 ]]; then
  C_RED='\033[0;31m'; C_GRN='\033[0;32m'; C_YEL='\033[1;33m'
  C_BLU='\033[0;34m'; C_DIM='\033[2m'; C_NC='\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_DIM=''; C_NC=''
fi

log()   { printf '%b\n' "${C_BLU}==>${C_NC} $*"; }
ok()    { printf '%b\n' "  ${C_GRN}✓${C_NC} $*"; }
warn()  { printf '%b\n' "  ${C_YEL}⚠${C_NC} $*" >&2; }
err()   { printf '%b\n' "${C_RED}✗ $*${C_NC}" >&2; }
die()   { err "$@"; exit 1; }
step()  { printf '\n%b\n' "${C_BLU}── $* ─────────────────────────────────────────${C_NC}"; }

# require_cmd <cmd> [install-hint]
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found.${2:+ $2}"
}

# have_cmd <cmd> — soft check, returns 0/1
have_cmd() { command -v "$1" >/dev/null 2>&1; }

# confirm <prompt> — returns 0 on yes. Auto-yes when ASSUME_YES=1.
confirm() {
  [[ "${ASSUME_YES:-0}" == "1" ]] && return 0
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

# gen_secret [hex-bytes] — strong random token (default 24 bytes → 48 hex chars)
gen_secret() {
  local bytes="${1:-24}"
  if have_cmd openssl; then
    openssl rand -hex "$bytes"
  else
    python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$bytes"
  fi
}

# env_set <file> <KEY> <value> — idempotently set KEY=value in an env file.
# Updates in place if the key exists, appends otherwise. Value is written
# verbatim (caller quotes if needed).
env_set() {
  local file="$1" key="$2" value="$3"
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    # Use a non-slash delimiter so values with '/' (URLs) are safe.
    python3 - "$file" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path).read().splitlines()
out = []
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}")
    else:
        out.append(line)
open(path, "w").write("\n".join(out) + "\n")
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# env_get <file> <KEY> — echo the current value (empty if unset/blank)
env_get() {
  grep -E "^${2}=" "$1" 2>/dev/null | head -1 | cut -d= -f2-
}

# env_is_placeholder <value> — true if empty or a replace-with-* placeholder
env_is_placeholder() {
  [[ -z "$1" || "$1" == replace-with-* || "$1" == replace-me* ]]
}
