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
# Class-b units (stale installs to refresh from the templates) are NOT touched here: refreshing restarts
# a live service, which is an owner / next-deploy step. See docs/knowledge/unit-drift-2026-10-25.md.
set -u
EXECUTE=0
[ "${1:-}" = "--execute" ] && EXECUTE=1
UNITDIR="${ZOE_UNIT_DIR:-$HOME/.config/systemd/user}"
BACKUP="${ZOE_UNIT_BACKUP_DIR:-$HOME/.zoe/unit-backups/$(date +%F)}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
RETIRED="bonsai-server.service flue-zoe-brain.service llama-server-e2b-fallback.service zoe-12b-postboot.service
zoe-brain-boot-check.service zoe-keepwarm.service zoe-keepwarm.timer zoe-purge-presence.service zoe-purge-presence.timer"

say() { printf '%s\n' "$*"; }
run() { if [ "$EXECUTE" = 1 ]; then "$@"; else say "  [dry-run] $*"; fi; }

say "unit dir: $UNITDIR"
say "backup:   $BACKUP   ($([ "$EXECUTE" = 1 ] && echo EXECUTE || echo dry-run))"
if [ "$EXECUTE" = 1 ] && ! mkdir -p "$BACKUP"; then say "ABORT: cannot create backup dir $BACKUP"; exit 1; fi

say "== 1. .bak / .disabled files -> backup (moved, never deleted)"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  say "  $(stat -c '%y' "$f" | cut -d' ' -f1)  $(basename "$f")"
  run mv -n "$f" "$BACKUP/"
done < <(find "$UNITDIR" -maxdepth 1 -type f \( -name '*.bak*' -o -name '*.disabled' \) | sort)

say "== 2. retired units -> back up, disable, remove"
removed=0
failed=0
for u in $RETIRED; do
  stem="${u%.*}"
  [ -e "$UNITDIR/$u" ] || { say "  $u: not installed"; continue; }
  state="$("$SYSTEMCTL" --user is-active "$u" 2>&1)"
  # Only an explicit stopped state allows removal; a bus error / unknown state is NOT evidence of inactivity.
  case "$state" in
    inactive|failed) ;;
    *) say "  $u: SKIP (state unknown or not stopped: $state)"; continue;;
  esac
  if find "$UNITDIR" -name "$u" \( -path '*.wants/*' -o -path '*.requires/*' \) 2>/dev/null | grep -q .; then
    say "  $u: SKIP (linked from a .wants/.requires dir)"; continue
  fi
  refs="$(for f in "$UNITDIR"/*.service "$UNITDIR"/*.timer "$UNITDIR"/*.d/*.conf; do
            [ -f "$f" ] || continue
            case "$(basename "$f")" in "$u"|"$stem".*) continue;; esac
            case "$(basename "$(dirname "$f")")" in "$u".d) continue;; esac
            sed -e ':a' -e '/\\$/N; s/\\\n/ /; ta' "$f" \
              | grep -E "^[[:space:]]*(Requires|Wants|After|Before|BindsTo|PartOf|Requisite|Upholds|Conflicts|OnFailure|Unit)=([^#]*[[:space:]])?${u//./\\.}([[:space:]]|\$)" \
              | sed "s|^|$f: |"
          done)"
  if [ -n "$refs" ]; then say "  $u: SKIP (referenced): $refs"; continue; fi
  say "  $u: state=$state enabled=$("$SYSTEMCTL" --user is-enabled "$u" 2>&1) -> backup + remove"
  # Backups must succeed BEFORE anything is disabled or removed; a failed copy skips this unit.
  if [ "$EXECUTE" = 1 ]; then
    if ! cp -a "$UNITDIR/$u" "$BACKUP/" || { [ -d "$UNITDIR/$u.d" ] && ! cp -a "$UNITDIR/$u.d" "$BACKUP/"; }; then
      say "  $u: SKIP (backup failed; nothing removed)"; failed=1; continue
    fi
  else
    say "  [dry-run] cp -a $UNITDIR/$u $BACKUP/"
  fi
  run "$SYSTEMCTL" --user disable "$u" >/dev/null 2>&1 || true
  run rm -f "$UNITDIR/$u"
  [ -d "$UNITDIR/$u.d" ] && run rm -rf "$UNITDIR/$u.d"
  removed=$((removed + 1))
done
if [ "$removed" -gt 0 ]; then run "$SYSTEMCTL" --user daemon-reload; fi
say "done ($removed retired unit(s) $([ "$EXECUTE" = 1 ] && echo removed || echo would be removed))"
[ "$failed" = 0 ] || exit 1
