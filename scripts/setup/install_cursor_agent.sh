#!/usr/bin/env bash
# Provision the host's cursor-agent at a PINNED version.
#
# WHY THIS EXISTS: the zoe-omnigent container does not install cursor-agent — it
# bind-mounts the host's install read-only (see modules/omnigent/docker-compose.module.yml)
# and the entrypoint symlinks the PINNED version onto PATH — the host's own pin symlink when
# present, else the repo-declared pin, never the newest (see modules/omnigent/entrypoint.sh).
# That mount is deliberate
# (a proven arm64 binary plus the host's login, instead of piping an installer into
# the image), but it left the VERSION untracked: nothing in the repo said which build
# a host should have, so a rebuild on any other machine silently kept whatever was
# already there.
#
# That is not cosmetic. omnigent >=0.7.0 enforces a per-harness minimum
# (`_CURSOR_MIN_VERSION` in onboarding/harness_install.py) and reports an older binary
# in GET /v1/hosts as the STRING "version-too-low", not `false` — so the Cursor harness
# goes unusable while a truthiness check still counts it as present. That is exactly
# how a five-month-old 2026.01.28 binary survived unnoticed until the 0.7.0 upgrade.
#
# Keep the pin (CURSOR_PINNED_VERSION) at or above omnigent's minimum, and bump it in the
# same pass as an omnigent upgrade (that is when the minimum can move).
#
# Usage:
#   scripts/setup/install_cursor_agent.sh            # install the pinned version
#   scripts/setup/install_cursor_agent.sh --check    # report only, non-zero if wrong
#
# After installing, recreate the container so the entrypoint re-links the binary:
#   docker compose -p omnigent --env-file .env -f modules/omnigent/docker-compose.module.yml up -d omnigent
set -euo pipefail

# Pinned deliberately — see the note above. Verified against omnigent 0.7.0's
# _CURSOR_MIN_VERSION of 2026.06.02. The pin is a LITERAL constant so `--check` always audits
# a host against the TRUE pin. An override is honoured for the INSTALL path only, behind a
# separately-named flag (CURSOR_AGENT_INSTALL_VERSION) — it must never weaken the audit.
CURSOR_PINNED_VERSION="2026.07.23-e383d2b"
CURSOR_AGENT_VERSION="${CURSOR_AGENT_INSTALL_VERSION:-${CURSOR_PINNED_VERSION}}"
CURSOR_MIN_VERSION="2026.06.02"

INSTALL_ROOT="${HOME}/.local/share/cursor-agent/versions"
BIN_DIR="${HOME}/.local/bin"
TARGET="${INSTALL_ROOT}/${CURSOR_AGENT_VERSION}/cursor-agent"

current() { "${BIN_DIR}/cursor-agent" --version 2>/dev/null | head -1 || echo "none"; }

# Compare the leading YYYY.MM.DD only; the trailing -<sha> is build metadata and is
# NOT ordered, so a lexical compare of the whole string would be wrong.
date_part() { printf '%s' "${1%%-*}"; }

if [ "${1:-}" = "--check" ]; then
  cur="$(current)"
  echo "installed: ${cur}"
  echo "pinned:    ${CURSOR_PINNED_VERSION}"
  echo "minimum:   ${CURSOR_MIN_VERSION} (omnigent 0.7.0)"
  [ "${cur}" = "${CURSOR_PINNED_VERSION}" ] && { echo "OK — matches the pin"; exit 0; }
  # Still acceptable if it clears omnigent's floor; report the drift either way.
  if [ "$(printf '%s\n%s\n' "$(date_part "${CURSOR_MIN_VERSION}")" "$(date_part "${cur}")" | sort -V | head -1)" \
       = "$(date_part "${CURSOR_MIN_VERSION}")" ] && [ "${cur}" != "none" ]; then
    echo "DRIFT — above omnigent's minimum but not the pinned build"; exit 1
  fi
  echo "TOO OLD — below omnigent's minimum; the Cursor harness will report version-too-low"
  exit 1
fi

if [ -x "${TARGET}" ]; then
  echo "[cursor-agent] ${CURSOR_AGENT_VERSION} already present"
else
  # The official installer resolves its own version, so it cannot be pointed at a pin;
  # fetch the versioned artifact directly instead. `cursor-agent update` is NOT usable
  # here — on arm64 it exits 1 with no message (measured 2026-07-29).
  case "$(uname -m)" in
    aarch64|arm64) ARCH="arm64" ;;
    x86_64|amd64)  ARCH="x64" ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
  esac
  URL="https://downloads.cursor.com/lab/${CURSOR_AGENT_VERSION}/linux/${ARCH}/agent-cli-package.tar.gz"
  echo "[cursor-agent] downloading ${CURSOR_AGENT_VERSION} (${ARCH})"
  # Stage into a temp dir that is a SIBLING of the final versions/ path, so publishing is a
  # single atomic rename on the same filesystem. The previous `cp -a` straight into the final
  # directory was NOT atomic: an interrupted copy left a partial final dir whose executable
  # cursor-agent then satisfied the "already present" check above forever. A mktemp under
  # $TMPDIR (a different mount) would make `mv` fall back to a non-atomic copy — the same
  # hazard — so stage beside the target. The leading dot keeps it out of `versions/*` globs.
  mkdir -p "${INSTALL_ROOT}"
  staging="$(mktemp -d "${INSTALL_ROOT}/.stage-${CURSOR_AGENT_VERSION}.XXXXXX")"
  trap 'rm -rf "${staging}"' EXIT
  curl -fSL "${URL}" | tar --strip-components=1 -xzf - -C "${staging}"
  chmod +x "${staging}/cursor-agent"
  # PROVE the staged binary runs and reports the pin BEFORE installing it. Verifying the
  # staged copy (not a post-link binary) means a mismatch or a broken extract aborts here
  # with any existing install left untouched — nothing half-written ever reaches versions/.
  # A binary that cannot report its version collapses to "none", which will not match.
  staged_ver="$("${staging}/cursor-agent" --version 2>/dev/null | head -1 || echo "none")"
  if [ "${staged_ver}" != "${CURSOR_AGENT_VERSION}" ]; then
    echo "[cursor-agent] FAILED: staged binary reports '${staged_ver}', expected '${CURSOR_AGENT_VERSION}' — not installing" >&2
    exit 1
  fi
  # A prior interrupted run may have left a partial (non-executable) final dir — that is
  # exactly how TARGET failed the -x check above. Remove it so the rename lands cleanly
  # instead of nesting the staging dir inside it, then publish atomically.
  rm -rf "${INSTALL_ROOT:?}/${CURSOR_AGENT_VERSION:?}"
  mv "${staging}" "${INSTALL_ROOT}/${CURSOR_AGENT_VERSION}"
  trap - EXIT
  echo "[cursor-agent] installed ${CURSOR_AGENT_VERSION} -> ${INSTALL_ROOT}/${CURSOR_AGENT_VERSION}"
fi

mkdir -p "${BIN_DIR}"
ln -sfn "${TARGET}" "${BIN_DIR}/cursor-agent"
echo "[cursor-agent] linked ${BIN_DIR}/cursor-agent -> ${TARGET}"

# Final confirmation via the LINKED binary — proves the symlink resolves and the published
# install runs. This also covers the "already present" path, which skips staging entirely.
# `current()` collapses any failure to the string "none", which will not match the pin.
got="$(current)"
if [ "${got}" != "${CURSOR_AGENT_VERSION}" ]; then
  echo "[cursor-agent] FAILED: linked binary reports '${got}', expected '${CURSOR_AGENT_VERSION}'" >&2
  echo "[cursor-agent] the install under ${INSTALL_ROOT}/${CURSOR_AGENT_VERSION} looks incomplete —" >&2
  echo "[cursor-agent] remove that directory and re-run to force a clean download." >&2
  exit 1
fi
echo "[cursor-agent] version now: ${got}"
echo "[cursor-agent] recreate zoe-omnigent so the entrypoint re-links it in-container."
