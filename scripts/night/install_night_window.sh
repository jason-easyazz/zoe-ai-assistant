#!/bin/bash
# install_night_window.sh - the OPERATOR installs (and removes) the night window. Nothing else does.
#
#   scripts/night/install_night_window.sh --dry-run     print every step; change nothing
#   scripts/night/install_night_window.sh               copy the two units, daemon-reload, disable zoe-dreaming.timer (the window absorbs dreaming), enable the window timer
#   scripts/night/install_night_window.sh --check       what is installed / enabled right now (read-only)
#   scripts/night/install_night_window.sh --uninstall   disable + remove the two units, re-enable zoe-dreaming.timer if install disabled it
#
# It only ADDS zoe-night-window.service / .timer to ~/.config/systemd/user and flips zoe-dreaming.timer; it edits no existing unit file and no drop-in.
# Why dreaming is disabled: the window runs zoe-nightly-dreaming itself (on the 12B, and on the 4B afterwards if the 12B could not), so a second run at 02:32
# would double the work and race the window for the model. --keep-dreaming-timer installs the window beside it anyway (not recommended).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/systemd"
DST="${NIGHT_UNIT_DIR:-$HOME/.config/systemd/user}"
STATE_DIR="${NIGHT_DIR:-$HOME/.zoe/night-window}"
STATE="$STATE_DIR/INSTALLED"
SYSTEMCTL="${NIGHT_SYSTEMCTL:-systemctl}"
DRY=0; MODE=install; KEEP_DREAMING=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --uninstall) MODE=uninstall ;;
    --check) MODE=check ;;
    --keep-dreaming-timer) KEEP_DREAMING=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done
UNITS=(zoe-night-window.service zoe-night-window.timer)

do_it() { if [ "$DRY" = 1 ]; then echo "DRY-RUN: $*"; else "$@"; fi; }
ctl() { if [ "$DRY" = 1 ]; then echo "DRY-RUN: systemctl --user $*"; else "$SYSTEMCTL" --user "$@"; fi; }
say() { echo "[install_night_window] $*"; }

check() {
  local f u
  say "units in $DST:"
  for u in "${UNITS[@]}"; do [ -e "$DST/$u" ] && echo "  installed  $u" || echo "  missing    $u"; done
  for u in zoe-night-window.timer zoe-dreaming.timer; do
    printf '  %-28s enabled=%s active=%s\n' "$u" "$("$SYSTEMCTL" --user is-enabled "$u" 2>/dev/null || true)" "$("$SYSTEMCTL" --user is-active "$u" 2>/dev/null || true)"
  done
  [ -e "$STATE" ] && { say "install record ($STATE):"; sed 's/^/  /' "$STATE"; } || say "no install record ($STATE)"
  "$SYSTEMCTL" --user list-timers --no-pager 2>/dev/null | grep -E 'night-window|dreaming|NEXT' || true
}

# Put zoe-dreaming.timer back (only when install had switched it off) so a failed install never leaves the night without a schedule.
rollback_dreaming() {
  [ "${1:-0}" = 1 ] || return 0
  ctl enable --now zoe-dreaming.timer || say "WARNING: could not re-enable zoe-dreaming.timer: run  systemctl --user enable --now zoe-dreaming.timer"
}

install() {
  local missing=0 f
  for f in "$HERE/night_window.sh" "$HERE/night_window.py" "$HERE/jobs/night_digest.py" "${UNITS[@]/#/$SRC/}"; do
    [ -e "$f" ] || { echo "MISSING $f" >&2; missing=1; }
  done
  [ "$missing" = 0 ] || exit 1
  [ -x "$HERE/night_window.sh" ] || { echo "$HERE/night_window.sh is not executable" >&2; exit 1; }
  local parked="$HOME/.config/systemd/user/llama-server-12b-deepbrain.service.disabled"
  [ -e "$parked" ] || echo "WARNING: the parked 12B unit $parked is missing: every window will refuse (it generates the 12B command from that file)" >&2
  [ -e "$HOME/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf" ] || echo "WARNING: the 12B QAT model file is missing" >&2
  [ -x "${NIGHT_PY:-$HOME/.zoe/venvs/zoe-data-py312/bin/python}" ] || echo "WARNING: the zoe-data py312 venv is missing: night_window.sh will not start" >&2
  sudo -n true 2>/dev/null || echo "WARNING: passwordless sudo is not available: the window cannot compact RAM and will refuse whenever RAM is fragmented (docs/knowledge/night-window.md)" >&2
  local dreaming_was prior disabled_it=0
  dreaming_was=$("$SYSTEMCTL" --user is-enabled zoe-dreaming.timer 2>/dev/null || true)
  # A reinstall sees the timer this tool already disabled: the ORIGINAL state is in the record, and it must survive (else uninstall would leave neither nightly schedule running).
  prior=""
  [ -e "$STATE" ] && prior=$(sed -n 's/^dreaming_timer_was=//p' "$STATE")
  if [ "$prior" = enabled ] && [ "$dreaming_was" != enabled ]; then dreaming_was=enabled; fi
  do_it mkdir -p "$DST" "$STATE_DIR"
  # The record is written BEFORE anything is flipped, so a failure half way can still be undone by --uninstall.
  if [ "$DRY" = 0 ]; then
    { echo "installed=$(date -Iseconds)"; echo "dreaming_timer_was=${dreaming_was:-unknown}"; echo "dreaming_timer_left_enabled=$KEEP_DREAMING"; } > "$STATE"
  else
    echo "DRY-RUN: would write $STATE (dreaming_timer_was=${dreaming_was:-unknown})"
  fi
  for u in "${UNITS[@]}"; do do_it cp "$SRC/$u" "$DST/$u"; done
  ctl daemon-reload
  if [ "$KEEP_DREAMING" = 0 ] && [ "$dreaming_was" = enabled ]; then
    say "disabling zoe-dreaming.timer: the window runs the dreaming job itself (run --uninstall to undo)"
    disabled_it=1
    ctl disable --now zoe-dreaming.timer || { say "could not disable zoe-dreaming.timer: rolling back"; rollback_dreaming 1; exit 1; }
  fi
  ctl enable --now zoe-night-window.timer || { say "could not enable zoe-night-window.timer: rolling back"; rollback_dreaming "$disabled_it"; exit 1; }
  say "done. Next: $HERE/night_window.sh --dry-run   (the arithmetic for tonight), then  systemctl --user list-timers | grep night"
}

uninstall() {
  local was=""
  [ -e "$STATE" ] && was=$(sed -n 's/^dreaming_timer_was=//p' "$STATE")
  ctl disable --now zoe-night-window.timer || true
  for u in "${UNITS[@]}"; do do_it rm -f "$DST/$u"; done
  ctl daemon-reload
  if [ "$was" = enabled ]; then
    say "re-enabling zoe-dreaming.timer (install had disabled it)"
    ctl enable --now zoe-dreaming.timer
  else
    say "zoe-dreaming.timer was not enabled by this tool's install; left as it is"
  fi
  do_it rm -f "$STATE"
  say "done. If a window was running, '$HERE/night_window.sh --restore-only' wakes whatever it stopped."
}

case "$MODE" in check) check ;; install) install ;; uninstall) uninstall ;; esac
