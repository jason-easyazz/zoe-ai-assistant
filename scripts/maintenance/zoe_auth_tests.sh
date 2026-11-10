#!/usr/bin/env bash
# zoe-auth's full test suite on the interpreter its container runs. This is the local twin of
# validate.yml's `zoe-auth` job and uses the same pytest command. It works offline after the first run:
# the venv is built once from services/zoe-auth/requirements.txt and rebuilt only when that file changes.
#
#   scripts/maintenance/zoe_auth_tests.sh [pytest args...]
#   scripts/maintenance/zoe_auth_tests.sh --print-python   # build/check the venv, print its interpreter
#
# The Python version comes from the FROM line in services/zoe-auth/Dockerfile, so it cannot drift from the image.
# On the Jetson, run it under the test lane: flock -x /tmp/zoe-test-lane.lock scripts/maintenance/zoe_auth_tests.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
REQS="$REPO/services/zoe-auth/requirements.txt"
want=$(sed -n 's/^FROM python:\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' "$REPO/services/zoe-auth/Dockerfile" | head -n 1)
[ -n "$want" ] || { echo "zoe_auth_tests: no 'FROM python:X.Y' in services/zoe-auth/Dockerfile" >&2; exit 2; }

venv="${AUTH_TEST_VENV:-$HOME/.cache/zoe-auth-tests/py$want}"
stamp=$(sha256sum "$REQS" | cut -d' ' -f1)
if [ ! -x "$venv/bin/python" ] || [ "$(cat "$venv/.reqs-sha256" 2>/dev/null)" != "$stamp" ]; then
  echo "zoe_auth_tests: building $venv (Python $want) from services/zoe-auth/requirements.txt" >&2
  rm -rf "$venv"
  if command -v "python$want" >/dev/null 2>&1; then
    "python$want" -m venv "$venv"
  else
    uv venv --quiet --seed --python "$want" "$venv"
  fi
  "$venv/bin/python" -m pip install --quiet -r "$REQS"
  echo "$stamp" > "$venv/.reqs-sha256"
fi

have=$("$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
[ "$have" = "$want" ] || { echo "zoe_auth_tests: zoe-auth runs Python $want but $venv is $have" >&2; exit 2; }

# affected_tests.py runs its own pytest command for this lane and only needs the interpreter.
if [ "${1:-}" = "--print-python" ]; then
  echo "$venv/bin/python"
  exit 0
fi

cd "$REPO"
PYTHONPATH="$REPO/services/zoe-auth:$REPO/services/zoe-auth/tests" exec "$venv/bin/python" -m pytest \
  -o faulthandler_timeout=240 services/zoe-auth/tests --maxfail=200 -q "$@"
