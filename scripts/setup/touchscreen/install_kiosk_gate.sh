#!/usr/bin/env bash
# install_kiosk_gate.sh - put the kiosk's local status gate in front of Zoe on the panel. Runs ON THE PANEL as root.
#
# What it does (docs/knowledge/panel-away-p1.md): installs zoe-kiosk-gate.py + its unit, installs the tracked
# start-kiosk.sh, and adds "gate_url" to /opt/TouchKio/config.json so the kiosk browser starts on the gate's
# "can't reach Zoe" screen instead of the launcher looping. It does NOT restart the kiosk: run
# `sudo systemctl restart zoe-kiosk` when you are ready to see it. The kiosk credential stays OFF until you set
# ZOE_KIOSK_CREDENTIAL=1 in /etc/zoe-kiosk-gate.env AND ZOE_PANEL_KIOSK_CREDENTIAL=1 on zoe-data.
#
#   --dry-run (default)  show what would change; changes nothing
#   --apply              back up + install + enable the gate + set gate_url (kiosk not restarted)
#   --rollback           remove gate_url, disable the gate (the launcher falls back to its legacy path)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OPT="/opt/TouchKio"
CONFIG="$OPT/config.json"
GATE_URL="http://127.0.0.1:7781/"
MODE="dry-run"
case "${1:-}" in
  --dry-run|"") MODE="dry-run" ;;
  --apply) MODE="apply" ;;
  --rollback) MODE="rollback" ;;
  *) echo "usage: $0 [--dry-run|--apply|--rollback]" >&2; exit 2 ;;
esac

set_gate_url() {
  python3 - "$CONFIG" "$1" <<'PY'
import json, sys
path, value = sys.argv[1], sys.argv[2]
data = json.load(open(path))
if value:
    data["gate_url"] = value
else:
    data.pop("gate_url", None)
with open(path + ".tmp", "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
  mv -f "$CONFIG.tmp" "$CONFIG"
}

if [ "$MODE" = "dry-run" ]; then
  python3 -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$HERE/zoe-kiosk-gate.py"
  bash -n "$HERE/start-kiosk.sh"
  echo "DRY RUN - nothing changed. --apply would:"
  echo "  back up $OPT/start-kiosk.sh and $CONFIG to *.bak-<timestamp>"
  echo "  install $HERE/zoe-kiosk-gate.py -> $OPT/zoe-kiosk-gate.py"
  echo "  install $HERE/start-kiosk.sh    -> $OPT/start-kiosk.sh"
  echo "  install $HERE/systemd/zoe-kiosk-gate.service -> /etc/systemd/system/ and enable --now"
  echo "  set \"gate_url\": \"$GATE_URL\" in $CONFIG (the kiosk is NOT restarted)"
  exit 0
fi

[ "$(id -u)" = "0" ] || { echo "run as root (sudo)" >&2; exit 1; }

if [ "$MODE" = "rollback" ]; then
  [ -f "$CONFIG" ] && set_gate_url ""
  systemctl disable --now zoe-kiosk-gate.service >/dev/null 2>&1 || true
  echo "rolled back: gate_url removed, gate disabled. Restart the kiosk to apply: sudo systemctl restart zoe-kiosk"
  echo "(the previous launcher, if you want it back, is the newest $OPT/start-kiosk.sh.bak-*)"
  exit 0
fi

ts="$(date +%Y%m%d-%H%M%S)"
[ -f "$OPT/start-kiosk.sh" ] && cp -p "$OPT/start-kiosk.sh" "$OPT/start-kiosk.sh.bak-$ts"
[ -f "$CONFIG" ] && cp -p "$CONFIG" "$CONFIG.bak-$ts"
install -m 0755 "$HERE/zoe-kiosk-gate.py" "$OPT/zoe-kiosk-gate.py"
install -m 0755 "$HERE/start-kiosk.sh" "$OPT/start-kiosk.sh"
install -m 0644 "$HERE/systemd/zoe-kiosk-gate.service" /etc/systemd/system/zoe-kiosk-gate.service
systemctl daemon-reload
systemctl enable --now zoe-kiosk-gate.service
set_gate_url "$GATE_URL"
chown pi:pi "$CONFIG" 2>/dev/null || true
for i in $(seq 1 10); do
  python3 -c "import urllib.request; urllib.request.urlopen('${GATE_URL}status', timeout=1)" >/dev/null 2>&1 && break
  sleep 1
done
echo "installed (backups *.bak-$ts). Gate status: $(python3 -c "import urllib.request; print(urllib.request.urlopen('${GATE_URL}status', timeout=2).read().decode())" 2>&1 | head -c 300)"
echo "restart the kiosk to start on the gate: sudo systemctl restart zoe-kiosk"
