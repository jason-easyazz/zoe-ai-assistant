#!/usr/bin/env bash
# Compatibility wrapper for Multica daemon -> OpenClaw config introspection.
# Multica CLI 0.3.19 calls `openclaw config get --json` to read the full
# resolved config. OpenClaw 2026.5.12 requires a path argument for `config get`,
# so this exact call is handled by printing the active config file. All other
# OpenClaw invocations delegate to the real binary.
set -euo pipefail
REAL_OPENCLAW="${REAL_OPENCLAW:-/home/zoe/.nvm/versions/node/v22.22.0/bin/openclaw}"
if [[ "$#" -eq 3 && "$1" == "config" && "$2" == "get" && "$3" == "--json" ]]; then
  cat "${OPENCLAW_CONFIG_PATH:-/home/zoe/.openclaw/openclaw.json}"
  exit 0
fi
exec "$REAL_OPENCLAW" "$@"
