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
# _CURSOR_MIN_VERSION of 2026.06.02. The pin is a LITERAL constant: the installer always
# targets and verifies exactly this build, and `--check` audits against it too. An env
# CURSOR_AGENT_VERSION override is allowed (for testing/rollback) but it MUST clear the
# minimum — activating a version below CURSOR_MIN_VERSION would break the Cursor harness.
CURSOR_PINNED_VERSION="2026.07.23-e383d2b"
CURSOR_AGENT_VERSION="${CURSOR_AGENT_VERSION:-${CURSOR_PINNED_VERSION}}"
CURSOR_MIN_VERSION="2026.06.02"

# Validate any override against the minimum. The version string format is YYYY.MM.DD-<sha>;
# compare only the leading date part (trailing -<sha> is build metadata, NOT ordered).
if [ "${CURSOR_AGENT_VERSION}" != "${CURSOR_PINNED_VERSION}" ]; then
  if [ "$(printf '%s\n%s\n' "$(date_part "${CURSOR_MIN_VERSION}")" "$(date_part "${CURSOR_AGENT_VERSION}")" | sort -V | head -1)" \
       != "$(date_part "${CURSOR_MIN_VERSION}")" ]; then
    echo "[cursor-agent] FAILED: CURSOR_AGENT_VERSION override '${CURSOR_AGENT_VERSION}' is below CURSOR_MIN_VERSION '${CURSOR_MIN_VERSION}'" >&2
    echo "[cursor-agent] activating a version below the minimum would break the Cursor harness — refusing to proceed." >&2
    exit 1
  fi
  echo "[cursor-agent] override: targeting ${CURSOR_AGENT_VERSION} (not the pin ${CURSOR_PINNED_VERSION})"
fi

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

# Decide whether a fresh staged install is needed. The active symlink must NEVER point at a
# binary that has not been verified to report the pin, so an existing final dir is trusted
# ONLY if it actually reports the pin. An executable-but-wrong-or-partial dir (e.g. a stale
# build, or a half-extracted one whose cursor-agent runs but misreports) is treated as
# invalid and reinstalled from a verified download — it is never relinked as-is.
need_install=1
if [ -x "${TARGET}" ]; then
  existing="$("${TARGET}" --version 2>/dev/null | head -1 || echo "none")"
  if [ "${existing}" = "${CURSOR_AGENT_VERSION}" ]; then
    echo "[cursor-agent] ${CURSOR_AGENT_VERSION} already present and verified"
    need_install=0
  else
    echo "[cursor-agent] existing ${TARGET} reports '${existing}', not '${CURSOR_AGENT_VERSION}' — reinstalling from a verified download" >&2
  fi
fi

if [ "${need_install}" = 1 ]; then
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
  # Replace any prior final dir — partial (failed the -x check) OR wrong-version (failed the
  # --version check) is exactly how we reach this staged path. Remove it so the rename lands
  # cleanly instead of nesting the staging dir inside it, then publish atomically.
  rm -rf "${INSTALL_ROOT:?}/${CURSOR_AGENT_VERSION:?}"
  mv "${staging}" "${INSTALL_ROOT}/${CURSOR_AGENT_VERSION}"
  trap - EXIT
  echo "[cursor-agent] installed ${CURSOR_AGENT_VERSION} -> ${INSTALL_ROOT}/${CURSOR_AGENT_VERSION}"
fi

# Final confirmation via the TARGET binary BEFORE updating the symlink. Validates that the
# published install (or the "already present" path) runs and reports the pin BEFORE making
# it active. If ${TARGET} is executable but reports the wrong version (e.g. an interrupted
# earlier copy), we exit here WITHOUT touching the symlink — never leave the host worse
# than before. `current()` reads the LINKED binary, so test TARGET directly first.
got="$("${TARGET}" --version 2>/dev/null | head -1 || echo "none")"
if [ "${got}" != "${CURSOR_AGENT_VERSION}" ]; then
  echo "[cursor-agent] FAILED: ${TARGET} reports '${got}', expected '${CURSOR_AGENT_VERSION}'" >&2
  echo "[cursor-agent] the install under ${INSTALL_ROOT}/${CURSOR_AGENT_VERSION} looks incomplete —" >&2
  echo "[cursor-agent] remove that directory and re-run to force a clean download." >&2
  exit 1
fi

# Only NOW that ${TARGET} is proven to report the pin do we make it active. This ensures
# an interrupted install or a wrong-version directory never clobbers a working symlink.
mkdir -p "${BIN_DIR}"
ln -sfn "${TARGET}" "${BIN_DIR}/cursor-agent"
echo "[cursor-agent] linked ${BIN_DIR}/cursor-agent -> ${TARGET}"
echo "[cursor-agent] version now: ${got}"
echo "[cursor-agent] recreate zoe-omnigent so the entrypoint re-links it in-container."
