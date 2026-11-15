#!/usr/bin/env bash
# Optional kernel operations. No env files are sourced and no private values printed.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/zoe_os_kernel.conf"
CHECKOUT="$(cd "$HERE/../.." && pwd)"
health() { timeout -k 1 3 python3 -B "$HERE/zoe_os_kernel_ops.py" health "$KERNEL_STATE" "${1:-write}" 2>/dev/null; }
installed() {
  timeout -k 1 2 systemctl --user list-unit-files "$KERNEL_UNIT" 2>/dev/null | grep -q "^$KERNEL_UNIT "
}
case "${1:-}" in
  start)
    exec /usr/bin/python3 -B "$CHECKOUT/$KERNEL_SOURCE_REL/authority.py" --dir "$KERNEL_STATE" --db "$KERNEL_DB"
    ;;
  health) health "${2:-write}" ;;
  report)
    if ! installed && [ ! -e "$KERNEL_STATE/k.sock" ] && [ ! -f "$KERNEL_DB" ]; then
      echo 'kernel: not installed'; exit 0
    fi
    state=$(timeout -k 1 2 systemctl --user is-active "$KERNEL_UNIT" 2>/dev/null || true)
    case "$state" in active|inactive|failed|activating|deactivating) ;; *) state=unknown;; esac
    if health read; then status=ok; else status=dead; fi
    # A restart that did not come back leaves this marker: the main service is then running on its fallback path,
    # and with the shadow or authority switch on a member delete fails closed until the kernel is back. Show it.
    if [ -e "$HOME/.zoe/deploy/kernel-update-pending" ]; then update=pending; else update=none; fi
    echo "kernel: unit=$state health=$status update=$update"
    if [ "$update" = pending ] || { [ "$state" != active ] && installed; }; then
      echo 'kernel: ATTENTION kernel is not serving; Zoe is on her fallback path and member delete will fail closed while a kernel switch is on'
    fi
    if [ -f "$KERNEL_DB" ]; then
      size=$(stat -c %s "$KERNEL_DB" 2>/dev/null || echo unknown)
      modified=$(stat -c %Y "$KERNEL_DB" 2>/dev/null || echo 0)
      echo "kernel: database=present bytes=$size age_s=$(( $(date +%s) - modified ))"
    else echo 'kernel: database=absent'; fi
    ;;
  backup)
    # Always called, also with no database: the ops helper still ages out old archives (and is silent when there is nothing).
    exec timeout -k 5 120 python3 -B "$HERE/zoe_os_kernel_ops.py" backup "$KERNEL_DB" "${ZOE_BACKUP_DIR:-$HOME/.zoe-backups}/zoe-os"
    ;;
  deploy)
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    installed || { echo 'kernel: not installed; skipping'; exit 0; }
    timeout -k 1 2 systemctl --user is-enabled --quiet "$KERNEL_UNIT" || { echo 'kernel: not enabled; skipping'; exit 0; }
    pending="$HOME/.zoe/deploy/kernel-update-pending"
    mkdir -p "$(dirname "$pending")"
    alarm() {
      touch "$pending"
      echo '::warning::kernel unavailable or update deferred; main service retains its fallback path'
      if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        echo 'WARNING: optional Zoe OS kernel update failed or deferred. Check kernel unit and health; main service uses fallback.' >> "$GITHUB_STEP_SUMMARY"
      fi
    }
    if [ -e "$HOME/.zoe/night-window/WINDOW_OPEN" ]; then alarm; exit 0; fi
    cd "$CHECKOUT"
    if [ ! -e "$pending" ] && [ -n "${DEPLOY_BASE_SHA:-}" ] \
       && git diff --quiet "$DEPLOY_BASE_SHA" HEAD -- "$KERNEL_SOURCE_REL/" scripts/maintenance/zoe_os_kernel* \
       && health; then
      echo 'kernel: unchanged and healthy'; exit 0
    fi
    touch "$pending"
    if ! timeout -k 1 20 systemctl --user restart "$KERNEL_UNIT"; then alarm; exit 0; fi
    for attempt in 1 2 3 4 5; do
      if health; then rm -f "$pending"; echo 'kernel: healthy after restart'; exit 0; fi
      sleep 1
    done
    alarm
    ;;
  *) echo 'usage: zoe_os_kernel.sh start|health [read]|report|backup|deploy' >&2; exit 2 ;;
esac
