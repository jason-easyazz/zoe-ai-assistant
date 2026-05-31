#!/usr/bin/env bash
# Simulate Hermes kanban worker subprocess auth (profile HOME isolation + XDG bridge).
# Exit 0 when gh auth and git push --dry-run succeed in the worker-equivalent env.
set -euo pipefail

OPERATOR_HOME="${HERMES_OPERATOR_HOME:-${HOME:-/home/zoe}}"
OPERATOR_CONFIG="${XDG_CONFIG_HOME:-${OPERATOR_HOME}/.config}"
PROFILE="${1:-zoe-coder}"
# HERMES_HOME in worker spawn is profile-scoped; HERMES_ROOT is the hermes tree root (~/.hermes).
HERMES_ROOT="${HERMES_ROOT:-${OPERATOR_HOME}/.hermes}"
if [[ -n "${HERMES_HOME:-}" && -d "${HERMES_HOME}/profiles/${PROFILE}/home" ]]; then
  HERMES_ROOT="${HERMES_HOME}"
elif [[ -n "${HERMES_HOME:-}" && -d "${HERMES_HOME}/home" ]]; then
  HERMES_ROOT="$(dirname "${HERMES_HOME}")"
fi
PROFILE_HOME="${HERMES_ROOT}/profiles/${PROFILE}/home"
WORKTREE="${2:-}"

if [[ ! -d "${PROFILE_HOME}" ]]; then
  echo "ERROR: profile home missing: ${PROFILE_HOME}" >&2
  exit 1
fi

export HOME="${PROFILE_HOME}"
export XDG_CONFIG_HOME="${OPERATOR_CONFIG}"
export HERMES_HOME="${HERMES_ROOT}/profiles/${PROFILE}"
export PATH="${OPERATOR_HOME}/.local/bin:${OPERATOR_HOME}/bin:/usr/local/bin:/usr/bin:/bin"

echo "=== Worker-sim env ==="
echo "HOME=${HOME}"
echo "XDG_CONFIG_HOME=${XDG_CONFIG_HOME}"
echo "HERMES_HOME=${HERMES_HOME}"
echo

echo "=== gh auth status ==="
gh auth status
echo

if [[ -d "${WORKTREE}/.git" || -f "${WORKTREE}/.git" ]]; then
  echo "=== git push --dry-run (${WORKTREE}) ==="
  git -C "${WORKTREE}" push --dry-run origin HEAD
else
  echo "SKIP: no worktree path (pass as 2nd arg for git push --dry-run); gh auth check above is sufficient"
fi

echo
echo "OK: kanban worker GitHub auth simulation passed"
