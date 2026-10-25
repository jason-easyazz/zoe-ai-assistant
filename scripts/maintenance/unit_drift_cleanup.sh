#!/usr/bin/env bash
# Host-side unit hygiene: archive .bak/.disabled cruft and remove RETIRED units. Dry-run by default.
#
#   scripts/maintenance/unit_drift_cleanup.sh              # show what would happen
#   scripts/maintenance/unit_drift_cleanup.sh --execute    # do it
#
# SAFE by construction: it never stops, restarts or edits an ACTIVE unit, and never deletes anything
# (every file is moved/copied into ~/.zoe/unit-backups/<date>/ first). A retired unit is removed only when
# it is (1) not active, (2) not linked from any *.wants/*.requires dir, (3) named by no other unit's
# dependency/ordering line (continuation lines joined). Anything failing a check is SKIPPED and reported.
#
# Deliberately NOT in RETIRED: zoe-dreaming.service/.timer. They are retired in unit_inventory.py, but
# scripts/night/install_night_window.sh --uninstall re-enables zoe-dreaming.timer as its rollback target;
# remove them only together with that script's rollback path.
#
# Cruft that repo code references by path is PROTECTED and never moved (see PROTECTED below).
#
# Class-b units (stale installs to refresh from the templates) are NOT touched here: refreshing restarts
# a live service, which is an owner / next-deploy step. See docs/knowledge/unit-drift-2026-10-25.md.
set -u
EXECUTE=0
[ "${1:-}" = "--execute" ] && EXECUTE=1
UNITDIR="${ZOE_UNIT_DIR:-$HOME/.config/systemd/user}"
BACKUP="${ZOE_UNIT_BACKUP_DIR:-$HOME/.zoe/unit-backups/$(date +%F)}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
REPO="${ZOE_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Cruft files other code READS by path (the night window reads the .disabled deepbrain unit). Never moved.
# Extra names come from repo code: any `systemd/user/<name>` path under scripts/ or services/ (this script excluded).
PROTECTED="llama-server-12b-deepbrain.service.disabled"
RETIRED="bonsai-server.service flue-zoe-brain.service llama-server-e2b-fallback.service zoe-12b-postboot.service
zoe-brain-boot-check.service zoe-keepwarm.service zoe-keepwarm.timer zoe-purge-presence.service zoe-purge-presence.timer"

say() { printf '%s\n' "$*"; }
run() { if [ "$EXECUTE" = 1 ]; then "$@"; else say "  [dry-run] $*"; fi; }

say "unit dir: $UNITDIR"
say "backup:   $BACKUP   ($([ "$EXECUTE" = 1 ] && echo EXECUTE || echo dry-run))"
if [ "$EXECUTE" = 1 ] && ! mkdir -p "$BACKUP"; then say "ABORT: cannot create backup dir $BACKUP"; exit 1; fi

protected_names() {
  printf '%s\n' $PROTECTED
  grep -rhoE 'systemd/user/[A-Za-z0-9_.@-]+' "$REPO/scripts" "$REPO/services" \
    --exclude=unit_drift_cleanup.sh --exclude-dir=node_modules --exclude-dir=.git 2>/dev/null \
    | sed 's|^systemd/user/||'
}
PROT_LIST="$(protected_names | sort -u)"

failed=0
say "== 1. .bak / .disabled files -> backup (moved, never deleted)"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if printf '%s\n' "$PROT_LIST" | grep -qxF "$(basename "$f")"; then
    say "  PROTECTED (referenced by repo code, left in place)  $(basename "$f")"; continue
  fi
  say "  $(stat -c '%y' "$f" | cut -d' ' -f1)  $(basename "$f")"
  # an earlier run's copy is the recovery copy: never overwrite it (and never count a skipped move as moved)
  if [ "$EXECUTE" = 1 ] && [ -e "$BACKUP/$(basename "$f")" ]; then
    say "  $(basename "$f"): SKIP (a copy already exists in $BACKUP; left in place)"; failed=1; continue
  fi
  run mv -n "$f" "$BACKUP/" || { say "  $(basename "$f"): FAILED to move"; failed=1; }
done < <(find "$UNITDIR" -maxdepth 1 -type f \( -name '*.bak*' -o -name '*.disabled' \) | sort)

# Prints the reason unit $1 may not be removed (empty = eligible). Pair siblings are handled by the caller.
blocked_reason() {
  local u="$1" stem state f r
  stem="${u%.*}"
  state="$("$SYSTEMCTL" --user is-active "$u" 2>&1)"
  # Only an explicit stopped state allows removal; a bus error / unknown state is NOT evidence of inactivity.
  case "$state" in
    inactive|failed) ;;
    *) echo "SKIP (state unknown or not stopped: $state)"; return;;
  esac
  if find "$UNITDIR" -name "$u" \( -path '*.wants/*' -o -path '*.requires/*' \) 2>/dev/null | grep -q .; then
    echo "SKIP (linked from a .wants/.requires dir)"; return
  fi
  # Every installed unit file of ANY type, plus drop-ins and real files inside .wants/.requires dirs.
  r="$(find "$UNITDIR" -type f \( -name '*.service' -o -name '*.timer' -o -name '*.target' -o -name '*.path' \
        -o -name '*.socket' -o -name '*.mount' -o -name '*.automount' -o -name '*.swap' -o -name '*.slice' \
        -o -name '*.scope' -o -path '*.d/*.conf' -o -path '*.wants/*' -o -path '*.requires/*' \) | sort | while IFS= read -r f; do
            case "$(basename "$f")" in "$u") continue;; esac
            # Same-stem RETIRED siblings (foo.timer for foo.service) are judged as a pair by the caller.
            case "$(basename "$f")" in "$stem".*) case " $RETIRED_ALL " in *" $(basename "$f") "*) continue;; esac;; esac
            case "$(basename "$(dirname "$f")")" in "$u".d) continue;; esac
            sed -e ':a' -e '/\\$/N; s/\\\n/ /; ta' "$f" \
              | grep -E "^[[:space:]]*(Requires|Wants|After|Before|BindsTo|PartOf|Requisite|Upholds|Conflicts|OnFailure|Unit)[[:space:]]*=([^#]*[[:space:]])?${u//./\\.}([[:space:]]|\$)" \
              | sed "s|^|$f: |"
          done)"
  [ -z "$r" ] || echo "SKIP (referenced): $r"
}

say "== 2. retired units -> back up, disable, remove"
RETIRED_ALL="$(echo $RETIRED)"
removed=0
for u in $RETIRED; do
  stem="${u%.*}"
  [ -e "$UNITDIR/$u" ] || { say "  $u: not installed"; continue; }
  why="$(blocked_reason "$u")"
  # A service+timer pair is ONE decision: if any installed same-stem retired sibling is blocked, keep both.
  if [ -z "$why" ]; then
    for sib in $RETIRED; do
      [ "$sib" != "$u" ] && [ "${sib%.*}" = "$stem" ] && [ -e "$UNITDIR/$sib" ] || continue
      sw="$(blocked_reason "$sib")"
      if [ -n "$sw" ]; then why="SKIP (paired unit $sib is not eligible: $sw)"; break; fi
    done
  fi
  if [ -n "$why" ]; then say "  $u: $why"; continue; fi
  state="$("$SYSTEMCTL" --user is-active "$u" 2>&1)"
  say "  $u: state=$state enabled=$("$SYSTEMCTL" --user is-enabled "$u" 2>&1) -> backup + remove"
  # Backups must succeed BEFORE anything is disabled or removed; a failed copy skips this unit.
  if [ "$EXECUTE" = 1 ]; then
    # an earlier run's copy is the recovery copy: refuse rather than overwrite/merge it
    if [ -e "$BACKUP/$u" ] || { [ -d "$UNITDIR/$u.d" ] && [ -e "$BACKUP/$u.d" ]; }; then
      say "  $u: SKIP (a backup copy already exists in $BACKUP; nothing removed)"; failed=1; continue
    fi
    if ! cp -a "$UNITDIR/$u" "$BACKUP/" || { [ -d "$UNITDIR/$u.d" ] && ! cp -a "$UNITDIR/$u.d" "$BACKUP/"; }; then
      say "  $u: SKIP (backup failed; nothing removed)"; failed=1; continue
    fi
  else
    say "  [dry-run] cp -a $UNITDIR/$u $BACKUP/"
  fi
  run "$SYSTEMCTL" --user disable "$u" >/dev/null 2>&1 || true
  ok=1
  run rm -f "$UNITDIR/$u" || ok=0
  if [ -d "$UNITDIR/$u.d" ]; then run rm -rf "$UNITDIR/$u.d" || ok=0; fi
  if [ "$EXECUTE" = 1 ] && { [ -e "$UNITDIR/$u" ] || [ -e "$UNITDIR/$u.d" ]; }; then ok=0; fi
  if [ "$ok" = 1 ]; then removed=$((removed + 1)); else say "  $u: FAILED to remove (backup kept in $BACKUP)"; failed=1; fi
done
if [ "$removed" -gt 0 ]; then
  run "$SYSTEMCTL" --user daemon-reload || { say "FAILED: daemon-reload (the manager may still hold the removed units)"; failed=1; }
fi
say "done ($removed retired unit(s) $([ "$EXECUTE" = 1 ] && echo removed || echo would be removed))"
[ "$failed" = 0 ] || exit 1
