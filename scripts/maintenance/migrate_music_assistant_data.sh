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
#   STEP A: re-point the bind mount + ship this script. NOT "safe to deploy
#     whenever" — that claim was wrong (review: Codex). The four databases are
#     still TRACKED in step A, so CD's `git reset --hard` replaces the LIVE
#     auth.db/library.db with the older COMMITTED versions. No deletion needed;
#     the rollback alone is the data loss. So the migration must run BEFORE
#     step A reaches the box:
#       1. run this script --execute (stops MA, copies to $DEST, leaves stopped)
#       2. merge step A; CD deploys it. data/music-assistant gets rolled back to
#          the committed state — now harmless, MA no longer reads that path.
#       3. start MA against $DEST with the verified copy.
#     Leaving MA STOPPED between 1 and 3 is deliberate: it cannot write to the
#     doomed path, nor serve rolled-back data.
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
it, so the mount can be re-pointed without a deploy destroying live data.

  (default)   dry-run: report what would happen, touch nothing
  --execute   stop the container, copy, verify, leave the container stopped

  src : $SRC
  dest: $DEST   (override with ZOE_MA_DATA)

After --execute succeeds:
  1. deploy STEP A — it RE-POINTS THE BIND MOUNT ONLY. It does NOT untrack the
     databases; auth.db/library.db and their WALs stay tracked at that commit,
     so the credentials remain in git history and CD keeps rolling those paths
     back. Untracking is a separate STEP B, still outstanding.
  2. ZOE_MA_DATA=<dest> docker compose -f docker-compose.modules.yml up -d music-assistant
  3. confirm: curl -s http://localhost:8095/info
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
# Test the ORIGINAL path first (review: Codex). `readlink -m` resolves the link,
# and DEST is later replaced by its target — so the `-L "$DEST"` guard further
# down could never fire for exactly the links it was written to catch, and
# --mirror would erase the link's TARGET as root.
if [[ -L "$DEST" ]]; then
    log "FATAL: destination path is a symlink: $DEST -> $(readlink -- "$DEST")"
    log "Refusing: clearing or copying would act on the link's target."
    exit 1
fi
DEST_ABS="$(readlink -m -- "$DEST")"
SRC_ABS="$(readlink -m -- "$SRC")"
REPO_ABS="$(readlink -m -- "$REPO_ROOT")"
case "$DEST" in
    /*) ;;
    *)  log "FATAL: destination must be an ABSOLUTE path, got: $DEST"; exit 1 ;;
esac

# Containment must be checked in BOTH directions, and against the source as well
# as the repo (review: Codex, who reproduced the SRC=DEST case: both databases
# deleted). A one-way "is DEST inside REPO" test passes for an ANCESTOR such as
# `/home/zoe` — and the script would then report a non-empty destination, advise
# `--mirror`, and `sudo rm -rf` every top-level entry under it, including the
# checkout and the very databases being migrated. The guard would do the damage
# it exists to prevent.
contains() {  # contains PARENT CHILD -> 0 if CHILD is PARENT or beneath it
    # The filesystem root needs its own case (review: Codex). With PARENT="/"
    # the naive pattern becomes `//*`, which matches nothing — so `ZOE_MA_DATA=/`
    # slipped past BOTH ancestor guards and --mirror would have run `rm -rf` over
    # every top-level entry on the box. Strip any trailing slash first so
    # `/home/zoe/` behaves like `/home/zoe` too.
    local parent="${1%/}" child="${2%/}"
    if [[ -z "$parent" ]]; then          # PARENT was "/" (or "")
        [[ "$child" == /* ]]             # everything lives under the root
    else
        [[ "$child" == "$parent" || "$child" == "$parent"/* ]]
    fi
}
if [[ "$DEST_ABS" == "$SRC_ABS" ]]; then
    log "FATAL: destination and source are the same path: $DEST_ABS"; exit 1
fi
if contains "$DEST_ABS" "$SRC_ABS"; then
    log "FATAL: destination is an ANCESTOR of the source."
    log "  dest: $DEST_ABS"
    log "  src : $SRC_ABS"
    log "Clearing it (--mirror) would delete the source databases themselves."
    exit 1
fi
if contains "$SRC_ABS" "$DEST_ABS"; then
    log "FATAL: destination is inside the source: $DEST_ABS"; exit 1
fi
if contains "$REPO_ABS" "$DEST_ABS"; then
    log "FATAL: destination is inside the git checkout: $DEST_ABS"
    log "That is the hazard this migration exists to remove — deploy_live.sh runs"
    log "\`git reset --hard\`, and gitignored runtime data there would be destroyed"
    log "with nothing dirty to warn you. Choose a path outside $REPO_ABS."
    exit 1
fi
if contains "$DEST_ABS" "$REPO_ABS"; then
    log "FATAL: destination is an ANCESTOR of the git checkout: $DEST_ABS"
    log "Clearing it (--mirror) would delete the checkout at $REPO_ABS."
    exit 1
fi
DEST="$DEST_ABS"
SRC="$SRC_ABS"

[[ -d "$SRC" ]] || { log "FATAL: source does not exist: $SRC"; exit 1; }

# The directory alone is NOT evidence the stores are there (review: Codex). Run
# this AFTER the untracking commit has deployed — the exact ordering failure it
# guards — and the databases are gone while ignored settings/playlists/sidecars
# remain, so a directory check passes, remnants get copied, and it prints DONE.
# The operator then restarts MA against an incomplete store. Require the stores.
# `test -f` FOLLOWS symlinks, so a symlinked store would be accepted here — then
# `cp -a` preserves the link rather than the data, and the `find -type f`
# checksum walk skips it entirely, so the script could report "verified 1/1" and
# DONE without ever verifying auth.db. A relative link may also resolve to a
# different file once copied, and an absolute link into the checkout would leave
# the live data behind the very path this migration retires. Require REGULAR,
# non-symlink files. (review: Codex)
missing=(); notregular=()
for required in auth.db library.db; do
    if sudo test -L "$SRC/$required"; then
        notregular+=("$required -> $(sudo readlink -- "$SRC/$required")")
    elif ! sudo test -f "$SRC/$required"; then
        missing+=("$required")
    fi
done
if [[ ${#notregular[@]} -gt 0 ]]; then
    log "FATAL: source database is a symlink, not a regular file:"
    printf '  %s\n' "${notregular[@]}" | sed 's/^/ma-migrate: /'
    log "cp -a would copy the LINK, and the checksum walk skips links — the run"
    log "could report success having verified nothing. Resolve it to a real file"
    log "(or point ZOE_MA_SRC at the directory that actually holds the stores)."
    exit 1
fi
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
    if [[ -d "$DEST" && -n "$(sudo find "$DEST" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        if [[ "$MIRROR" -eq 1 ]]; then
            log "  0. DELETE these existing destination entries (--mirror):"
            sudo find "$DEST" -mindepth 1 -maxdepth 1 -printf '  %y %p\n' 2>/dev/null \
                | sed 's/^/ma-migrate:   /'
        else
            log "  !! destination is NOT empty and --mirror was not given — the"
            log "     real run would REFUSE. Entries present:"
            sudo find "$DEST" -mindepth 1 -maxdepth 1 -printf '  %y %p\n' 2>/dev/null \
                | sed 's/^/ma-migrate:   /'
        fi
    fi
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
if [[ -d "$DEST" && -n "$(sudo find "$DEST" -type f -print -quit 2>/dev/null)" ]]; then
    # `sort -rn | head -1` gives sort a SIGPIPE once head exits; with
    # `set -euo pipefail` that is exit 141 and the script dies — AFTER Music
    # Assistant has been stopped, leaving the service down and no diagnostic.
    # Codex reproduced it with a 10,000-file directory. awk consumes the whole
    # stream, so nothing is ever signalled. Every `find | head` in this script
    # has the same hazard and uses `awk 'NR<=N'` for the same reason; the
    # emptiness probes use command substitution rather than `| grep -q`.
    # (review: Codex)
    newest_src=$(sudo find "$SRC"  -type f -printf '%T@\n' 2>/dev/null | awk 'BEGIN{m=0}{if($1>m)m=$1}END{print m+0}')
    newest_dst=$(sudo find "$DEST" -type f -printf '%T@\n' 2>/dev/null | awk 'BEGIN{m=0}{if($1>m)m=$1}END{print m+0}')
    if [[ -n "$newest_dst" && -n "$newest_src" ]] \
       && awk -v a="$newest_dst" -v b="$newest_src" 'BEGIN{exit !(a>b)}'; then
        log "FATAL: destination has NEWER data than the source."
        log "  newest in dest: $(sudo find "$DEST" -type f -newermt "@$newest_src" -printf '%p\n' 2>/dev/null | awk 'NR<=3' | tr '\n' ' ')"
        log "This means Music Assistant has already been running against $DEST."
        log "Copying now would overwrite live state with the pre-migration snapshot."
        log "The migration is already done — you do not need to re-run it."
        exit 1
    fi
fi

# COPY INTO A CLEAN DESTINATION, ALWAYS (review: Codex).
#
# The overlay design kept producing distinct bugs — stale leftovers, then stale
# symlinks, then the serious one: a symlink in DEST at a name that ALSO exists
# in SRC. GNU cp -a follows a destination symlink, so `sudo cp` would overwrite
# whatever it points at, AS ROOT, leaving the link in place; the name-based
# manifest sees a match rather than an "extra", and sha256sum follows the link
# too, so the script would still print DONE. That is a root-privileged arbitrary
# write dressed as a successful migration.
#
# Rather than enumerate the hazards, remove the class: the destination is either
# absent/empty, or --mirror clears it first, and the copy then lands in a
# freshly-created empty directory. --remove-destination is belt-and-braces for
# any path this reasoning missed.
if [[ -e "$DEST" ]]; then
    if [[ -L "$DEST" ]]; then
        log "FATAL: destination is itself a symlink: $DEST"
        log "Refusing — the copy would write through it as root."
        exit 1
    fi
    if [[ ! -d "$DEST" ]]; then
        log "FATAL: destination exists and is not a directory: $DEST"; exit 1
    fi
    if [[ -n "$(sudo find "$DEST" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
        if [[ "$MIRROR" -eq 1 ]]; then
            # Destructive maintenance must print the candidate list BEFORE acting
            # (scripts/AGENTS.md; review: Codex — a generic message then silent
            # deletion gave the operator nothing to review).
            log "--mirror will DELETE these destination entries:"
            sudo find "$DEST" -mindepth 1 -maxdepth 1 -printf '  %y %p\n' 2>/dev/null \
                | sed 's/^/ma-migrate: /'
            log "clearing non-empty destination (--mirror) so the copy lands clean"
            sudo find "$DEST" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        else
            log "FATAL: destination is not empty: $DEST"
            sudo find "$DEST" -mindepth 1 -maxdepth 1 -printf '  %p\n' 2>/dev/null | awk 'NR<=10' | sed 's/^/ma-migrate: /'
            log "Copying into it risks writing THROUGH a symlink as root and"
            log "leaving stale entries MA would open. Re-run with --mirror to"
            log "clear it first, or remove it yourself."
            exit 1
        fi
    fi
fi

sudo mkdir -p "$DEST"
log "copying (ownership + timestamps preserved) into a clean destination…"
sudo cp -a --remove-destination "$SRC/." "$DEST/"

# ---- (3) stale files in DEST (review: Codex) -----------------------------
# `cp -a` overlays; it never deletes. Verification walked SRC only, so anything
# in DEST that is NOT in SRC survived unseen — a stale WAL/journal or an old
# credential artifact could become part of the store MA starts against. Compare
# full manifests in BOTH directions.
# ALL entry types, not just regular files (review: Codex, who reproduced it with
# a stale `stale.db-wal` symlink that survived --execute while the script printed
# DONE). `-type f` hides symlinks, dirs, FIFOs and device nodes from BOTH sides
# of the comparison — and a symlinked sidecar is precisely the kind of thing MA
# would then follow when opening its store. `-mindepth 1` so the root itself is
# not reported.
# Process substitution hides producer failure: bash does not propagate the exit
# status of <(...) to comm, so a failing `sudo find` yields an EMPTY source
# manifest, every destination entry looks "extra", and --mirror deletes valid
# data. Materialise both, check status, and refuse an empty source manifest —
# the source is known non-empty by this point, so empty means the enumeration
# failed. (review: Codex)
_man_src="$(mktemp)"; _man_dst="$(mktemp)"
trap 'rm -f "$_man_src" "$_man_dst"' EXIT
if ! (cd "$SRC" && sudo find . -mindepth 1 | sort) > "$_man_src"; then
    log "FATAL: could not enumerate the source at $SRC"; exit 1
fi
if ! (cd "$DEST" && sudo find . -mindepth 1 | sort) > "$_man_dst"; then
    log "FATAL: could not enumerate the destination at $DEST"; exit 1
fi
if [[ ! -s "$_man_src" ]]; then
    log "FATAL: source manifest is EMPTY — enumeration failed."
    log "Refusing: every destination entry would look extra and be deleted."
    exit 1
fi
extra=$(comm -13 "$_man_src" "$_man_dst")
if [[ -n "$extra" ]]; then
    if [[ "$MIRROR" -eq 1 ]]; then
        log "removing files present in destination but not in source (--mirror):"
        printf '%s\n' "$extra" | sed 's/^/ma-migrate:   /'
        # Reverse order so children go before their parents, and -rf so a stale
        # directory or symlink is removed rather than skipped.
        while IFS= read -r rel; do
            [[ -n "$rel" ]] && sudo rm -rf -- "$DEST/${rel#./}"
        done < <(printf '%s\n' "$extra" | sort -r)
    else
        # Should be unreachable now the destination is cleared before copying;
        # kept as a tripwire in case that guarantee is ever weakened.
        log "FATAL: destination has files the source does not:"
        printf '%s\n' "$extra" | sed 's/^/ma-migrate:   /'
        log "These would join the store Music Assistant starts against — a stale"
        log "WAL/journal or old credential artifact is exactly the risk. Re-run"
        log "with --mirror to delete them, or clear \$DEST and start clean."
        exit 1
    fi
fi

log "verifying by checksum…"
# Materialise the walk and check its status BEFORE iterating (review: Codex).
# `find … || true` masks a producer that emitted some paths and then failed on
# an I/O or traversal error: `checked` ends up non-zero, so the zero-count guard
# below does not fire, and the script reports every-file verification when later
# files were never read at all. A partial walk must be as fatal as an empty one.
_filelist="$(mktemp)"
trap 'rm -f "$_man_src" "$_man_dst" "$_filelist"' EXIT
if ! sudo find "$SRC" -type f -print0 > "$_filelist"; then
    log "FATAL: source walk failed part-way — cannot promise every-file verification."
    log "Destination is NOT trustworthy; do not start Music Assistant against it."
    exit 1
fi
fail=0; checked=0
while IFS= read -r -d '' f; do
    rel="${f#$SRC/}"
    if [[ ! -e "$DEST/$rel" ]]; then
        log "  MISSING at destination: $rel"; fail=1; continue
    fi
    a=$(sudo sha256sum "$f" | awk '{print $1}')
    b=$(sudo sha256sum "$DEST/$rel" | awk '{print $1}')
    if [[ "$a" != "$b" ]]; then log "  CHECKSUM MISMATCH: $rel"; fail=1; else checked=$((checked+1)); fi
done < "$_filelist"

# Same masked-producer hazard as the manifests: a failing enumeration would
# verify ZERO files and still report success. The source has ≥2 databases by
# construction (checked at start), so zero checked means the walk failed.
if [[ "$checked" -eq 0 ]]; then
    log "FATAL: verified 0 files — the source enumeration failed."
    log "Destination is NOT trustworthy; do not start Music Assistant against it."
    exit 1
fi

if [[ "$fail" -ne 0 ]]; then
    log "FAILED verification — destination is NOT trustworthy."
    log "The original at $SRC is untouched; do NOT deploy step A yet."
    exit 1
fi

log "verified $checked/$checked files"
log ""
log "DONE. Original left in place as rollback."
log "Next, IN THIS ORDER:"
log "  1. deploy STEP A — it RE-POINTS THE BIND MOUNT ONLY."
log "     It does NOT untrack the databases: auth.db, library.db and their WALs"
log "     are still tracked at this commit, so the credentials remain in git"
log "     history and CD will keep rolling those paths back. Untracking is a"
log "     SEPARATE step B, still outstanding after this. (review: Codex)"
log "  2. ZOE_MA_DATA=$DEST docker compose -f docker-compose.modules.yml up -d music-assistant"
log "     (the resolved destination is spelled out because a one-shot"
log "      \`ZOE_MA_DATA=... ./migrate…\` does not survive this script exiting —"
log "      Compose would otherwise fall back to its default and initialise an"
log "      EMPTY store instead of the verified copy. review: Codex)"
log "  3. confirm: curl -s http://localhost:8095/info"
