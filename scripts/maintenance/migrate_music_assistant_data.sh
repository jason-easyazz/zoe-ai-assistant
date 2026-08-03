#!/usr/bin/env bash
# Move Music Assistant's live data OUT of the git checkout.
#
# WHY (2026-08-03): `docker-compose.modules.yml` bind-mounted `./data/music-assistant`
# as MA's `/data`, and four of those files (auth.db, library.db and their WALs)
# were TRACKED. Two consequences, both bad:
#
#   * MA rewrites them continuously, so the live checkout was permanently dirty
#     with TRACKED changes — a hard block on the deploy readiness gate that no
#     `--untracked-files` flag can clear. Every deploy refused, forever.
#   * `auth.db` holds provider credentials and was therefore in git history.
#
# The fix is NOT to untrack them in place. `git rm --cached` records a DELETION,
# and `deploy_live.sh` does `git reset --hard`, which APPLIES that deletion to
# disk — destroying the live stores of a running service. (Caught in review on
# PR #1604 before it merged.) Runtime data living inside a directory that gets
# `reset --hard` is the actual defect, so the data moves out first.
#
# WHY THE MIGRATION IS SPLIT IN TWO (review: Codex — this is the load-bearing bit)
# `.github/workflows/deploy.yml` runs on EVERY push to main and does
# `git reset --hard "$target"`. So a commit that deletes the tracked databases is
# applied AUTOMATICALLY on merge, before any operator could run this script —
# documentation of a required order cannot bind automation. Hence:
#
#   STEP A (merged first, NON-DESTRUCTIVE): re-point the bind mount + ship this
#     script. Deleting nothing, CD can deploy it freely. Then run this script and
#     recreate the container: MA now writes to $DEST, and `data/music-assistant`
#     goes STATIC — which already fixes the permanently-dirty deploy block.
#   STEP B (separate PR, later): untrack the now-static files. By then nothing
#     writes there, so `reset --hard` deleting them is harmless.
#
# Each step is independently safe in any order CD chooses to apply it.
#
# SAFE BY CONSTRUCTION:
#   * dry-run by default (scripts/AGENTS.md contract); --execute to act
#   * COPIES, never moves — the original is left untouched as the rollback
#   * refuses to run while the container is up (a live writer would tear the DBs)
#   * verifies every file by checksum before declaring success
#   * idempotent: re-running re-verifies and re-syncs rather than duplicating
set -euo pipefail

SRC="${ZOE_MA_SRC:-/home/zoe/assistant/data/music-assistant}"
DEST="${ZOE_MA_DATA:-/home/zoe/.zoe/music-assistant}"
CONTAINER="${ZOE_MA_CONTAINER:-zoe-music-assistant}"
REPO_ROOT="${ZOE_REPO_ROOT:-/home/zoe/assistant}"
EXECUTE=0
MIRROR=0

usage() {
    cat <<EOF
Usage: migrate_music_assistant_data.sh [--execute]

Copies Music Assistant's live data from the git checkout to a location outside
it, so untracking the databases cannot destroy them on the next deploy.

  (default)   dry-run: report what would happen, touch nothing
  --execute   stop the container, copy, verify, leave the container stopped

  src : $SRC
  dest: $DEST   (override with ZOE_MA_DATA)

After --execute succeeds: deploy the untracking commit, then start the container.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --execute) EXECUTE=1; shift ;;
        --mirror)  MIRROR=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

log() { printf 'ma-migrate: %s\n' "$*"; }

# The whole point is that the data leaves the checkout, so an override that
# points back inside it silently re-creates the hazard — and worse than before,
# because the files would now be gitignored, so nothing would look dirty
# (review: Codex). Compose substitutes the same ${ZOE_MA_DATA}, so this must be
# rejected here rather than trusted. Canonicalise first: `./data/...`,
# symlinks and `..` traversal all have to resolve.
DEST_ABS="$(readlink -m -- "$DEST")"
REPO_ABS="$(readlink -m -- "$REPO_ROOT")"
case "$DEST" in
    /*) ;;
    *)  log "FATAL: destination must be an ABSOLUTE path, got: $DEST"; exit 1 ;;
esac
if [[ "$DEST_ABS" == "$REPO_ABS" || "$DEST_ABS" == "$REPO_ABS"/* ]]; then
    log "FATAL: destination is inside the git checkout: $DEST_ABS"
    log "That is the hazard this migration exists to remove — deploy_live.sh runs"
    log "\`git reset --hard\`, and gitignored runtime data there would be destroyed"
    log "with nothing dirty to warn you. Choose a path outside $REPO_ABS."
    exit 1
fi
DEST="$DEST_ABS"

[[ -d "$SRC" ]] || { log "FATAL: source does not exist: $SRC"; exit 1; }

# The directory alone is NOT evidence the stores are there (review: Codex). Run
# this AFTER the untracking commit has deployed — the exact ordering failure it
# guards — and the databases are gone while ignored settings/playlists/sidecars
# remain, so a directory check passes, remnants get copied, and it prints DONE.
# The operator then restarts MA against an incomplete store. Require the stores.
missing=()
for required in auth.db library.db; do
    sudo test -f "$SRC/$required" || missing+=("$required")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    log "FATAL: source is missing live databases: ${missing[*]}"
    log "$SRC exists but has no stores — this is what it looks like AFTER the"
    log "untracking commit deployed. Copying now would produce an incomplete"
    log "destination that reports success. Restore from ~/.zoe/deploy-backups or"
    log "git history before retrying; do NOT start Music Assistant against it."
    exit 1
fi

# Container state must be KNOWN, not assumed (review: Codex). A failing
# `docker ps` — daemon down, permissions lost, CLI absent — previously had its
# stderr discarded and was indistinguishable from "stopped", so the script would
# copy while Music Assistant was still writing. Checksums would still match
# (each file equals its own copy) while the SQLite set was a torn snapshot:
# verification that proves the wrong property. Fail closed instead.
if ! ps_out="$(docker ps --format '{{.Names}}' 2>&1)"; then
    log "FATAL: cannot determine container state — \`docker ps\` failed:"
    printf '%s\n' "$ps_out" | sed 's/^/ma-migrate:   /' >&2
    log "Refusing to copy: a running writer would produce a torn snapshot that"
    log "still passes checksum verification."
    exit 1
fi
running=0
if printf '%s\n' "$ps_out" | grep -qx "$CONTAINER"; then running=1; fi

log "source     : $SRC ($(sudo du -sh "$SRC" 2>/dev/null | cut -f1))"
log "destination: $DEST"
log "container  : $CONTAINER $([[ $running -eq 1 ]] && echo '(RUNNING)' || echo '(stopped)')"
log "files      : $(sudo find "$SRC" -type f 2>/dev/null | wc -l)"

if [[ "$EXECUTE" -eq 0 ]]; then
    log ""
    log "DRY RUN — nothing changed. Would:"
    log "  1. stop $CONTAINER"
    log "  2. cp -a $SRC/. -> $DEST/   (preserves root-owned files; container writes as root)"
    log "  3. verify every file by sha256"
    log "  4. leave $CONTAINER stopped, original untouched as rollback"
    log ""
    log "Re-run with --execute when ready. THEN deploy, THEN start the container."
    exit 0
fi

# A live writer mid-copy yields torn SQLite files that verify fine and open badly.
if [[ $running -eq 1 ]]; then
    log "stopping $CONTAINER (a live writer would tear the databases)"
    docker stop "$CONTAINER" >/dev/null
    for _ in $(seq 1 30); do
        docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" || break
        sleep 1
    done
    docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" \
        && { log "FATAL: $CONTAINER did not stop"; exit 1; }
    log "stopped"
fi

# DIRECTION SAFETY (review: Greptile). "Idempotent" was wrong: after the deploy
# Music Assistant writes to DEST, so re-running would overlay those newer files
# with the retained pre-migration copies in SRC and silently lose auth, library,
# settings and playlist changes. A re-run is only safe while SRC is still the
# live store. Compare newest mtimes and refuse to go backwards.
if [[ -d "$DEST" ]] && sudo find "$DEST" -type f -print -quit 2>/dev/null | grep -q .; then
    newest_src=$(sudo find "$SRC"  -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
    newest_dst=$(sudo find "$DEST" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
    if [[ -n "$newest_dst" && -n "$newest_src" ]] \
       && awk -v a="$newest_dst" -v b="$newest_src" 'BEGIN{exit !(a>b)}'; then
        log "FATAL: destination has NEWER data than the source."
        log "  newest in dest: $(sudo find "$DEST" -type f -newermt "@$newest_src" -printf '%p\n' 2>/dev/null | head -3 | tr '\n' ' ')"
        log "This means Music Assistant has already been running against $DEST."
        log "Copying now would overwrite live state with the pre-migration snapshot."
        log "The migration is already done — you do not need to re-run it."
        exit 1
    fi
fi

sudo mkdir -p "$DEST"
log "copying (ownership + timestamps preserved)…"
sudo cp -a "$SRC/." "$DEST/"

# ---- (3) stale files in DEST (review: Codex) -----------------------------
# `cp -a` overlays; it never deletes. Verification walked SRC only, so anything
# in DEST that is NOT in SRC survived unseen — a stale WAL/journal or an old
# credential artifact could become part of the store MA starts against. Compare
# full manifests in BOTH directions.
extra=$(comm -13 \
    <(cd "$SRC"  && sudo find . -type f | sort) \
    <(cd "$DEST" && sudo find . -type f | sort))
if [[ -n "$extra" ]]; then
    if [[ "$MIRROR" -eq 1 ]]; then
        log "removing files present in destination but not in source (--mirror):"
        printf '%s\n' "$extra" | sed 's/^/ma-migrate:   /'
        while IFS= read -r rel; do [[ -n "$rel" ]] && sudo rm -f -- "$DEST/${rel#./}"; done <<<"$extra"
    else
        log "FATAL: destination has files the source does not:"
        printf '%s\n' "$extra" | sed 's/^/ma-migrate:   /'
        log "These would join the store Music Assistant starts against — a stale"
        log "WAL/journal or old credential artifact is exactly the risk. Re-run"
        log "with --mirror to delete them, or clear \$DEST and start clean."
        exit 1
    fi
fi

log "verifying by checksum…"
fail=0; checked=0
while IFS= read -r -d '' f; do
    rel="${f#$SRC/}"
    if [[ ! -e "$DEST/$rel" ]]; then
        log "  MISSING at destination: $rel"; fail=1; continue
    fi
    a=$(sudo sha256sum "$f" | awk '{print $1}')
    b=$(sudo sha256sum "$DEST/$rel" | awk '{print $1}')
    if [[ "$a" != "$b" ]]; then log "  CHECKSUM MISMATCH: $rel"; fail=1; else checked=$((checked+1)); fi
done < <(sudo find "$SRC" -type f -print0)

if [[ "$fail" -ne 0 ]]; then
    log "FAILED verification — destination is NOT trustworthy."
    log "The original at $SRC is untouched; do NOT deploy the untracking commit."
    exit 1
fi

log "verified $checked/$checked files"
log ""
log "DONE. Original left in place as rollback."
log "Next, IN THIS ORDER:"
log "  1. deploy the commit that untracks the DBs + re-points the bind mount"
log "  2. ZOE_MA_DATA=$DEST docker compose -f docker-compose.modules.yml up -d music-assistant"
log "     (the resolved destination is spelled out because a one-shot"
log "      \`ZOE_MA_DATA=... ./migrate…\` does not survive this script exiting —"
log "      Compose would otherwise fall back to its default and initialise an"
log "      EMPTY store instead of the verified copy. review: Codex)"
log "  3. confirm: curl -s http://localhost:8095/info"
