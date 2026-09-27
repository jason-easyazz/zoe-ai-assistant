#!/usr/bin/env bash
# Print the Python interpreter the zoe-data user service is CONFIGURED to run —
# what systemd will exec on the next restart — so deploy-time dependency installs
# and Alembic migrations run under the SAME interpreter as the service (B0.7).
#
# The answer comes from systemd (`systemctl --user show -p ExecStart`), not from
# "does the 3.12 venv exist": the venv is built BEFORE the cutover drop-in is
# installed and is deliberately left in place AFTER a rollback, so its presence
# says nothing about what runs. Keying on it would install into a venv the
# service is not using and starve the interpreter it IS using.
#
# Usage:
#   scripts/deploy/zoe_data_python.sh             # -> /usr/bin/python3  |  ~/.zoe/venvs/zoe-data-py312/bin/python
#   scripts/deploy/zoe_data_python.sh --venv-dir  # -> the venv's sys.prefix, or an EMPTY line when not a venv
# Env: ZOE_DATA_UNIT (default zoe-data.service).
#
# Fails closed (exit 1, message on stderr) when the unit is unknown, the user bus
# is unreachable, or ExecStart does not name an executable Python — a deploy
# must not guess which interpreter to feed.
set -euo pipefail

die() { echo "zoe_data_python: $*" >&2; exit 1; }

mode="path"
case "${1:-}" in
  "") ;;
  --venv-dir) mode="venv-dir" ;;
  -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "unknown argument: $1 (use --venv-dir or none)" ;;
esac

unit="${ZOE_DATA_UNIT:-zoe-data.service}"
# A deploy runner's shell has no XDG_RUNTIME_DIR; without it `systemctl --user`
# cannot reach the user bus (same reason deploy.yml's restart step exports it).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

raw="$(systemctl --user show -p ExecStart --value "$unit")" \
  || die "cannot query $unit via the user bus (XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR)"
# `show` prints `{ path=/usr/bin/python3 ; argv[]=... ; ... }` per ExecStart; an
# unknown unit prints nothing and still exits 0. Type=simple allows exactly one.
paths="$(sed -nE 's/^\{ path=([^ ;]+) ;.*/\1/p' <<<"$raw")"
[[ -n "$paths" ]] || die "$unit has no ExecStart (unit not installed or not loaded?)"
[[ "$(wc -l <<<"$paths")" -eq 1 ]] || die "$unit has more than one ExecStart: $(tr '\n' ' ' <<<"$paths")"
py="$paths"
[[ -x "$py" ]] || die "$unit ExecStart interpreter is not executable: $py"

# Prove it IS a Python (a wrapper-script ExecStart would otherwise be fed pip
# args) and classify it in the same call: a venv has sys.prefix != sys.base_prefix.
# The tagged output is the proof — an executable that exits 0 printing nothing
# (/bin/true) must not be mistaken for a system Python.
probe="$("$py" -c 'import sys; print("zoe-py", sys.prefix if sys.prefix != sys.base_prefix else "")' 2>/dev/null)" \
  || die "$unit ExecStart is not a working Python interpreter: $py"
[[ "$probe" == "zoe-py "* ]] || die "$unit ExecStart is not a Python interpreter: $py"
venv="${probe#zoe-py }"

if [[ "$mode" == "venv-dir" ]]; then
  printf '%s\n' "$venv"
else
  printf '%s\n' "$py"
fi
