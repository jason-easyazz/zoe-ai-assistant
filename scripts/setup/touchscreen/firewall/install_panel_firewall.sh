#!/usr/bin/env bash
# install_panel_firewall.sh - the touch panel's inbound firewall (nftables), with a dry run and an automatic rollback.
#
# Runs ON THE PANEL as root. Design: docs/architecture/panel-remote-access.md section 4.6; record + live commands:
# docs/knowledge/panel-away-p1.md. Admits inbound only from loopback, the tailnet (tailscale0), the home LAN subnet captured
# NOW, replies to the panel's own connections, and DHCP/IPv6-ND/Tailscale's WireGuard port. Outbound is untouched.
#
#   --dry-run (default)  render with the home subnet, syntax-check it with `nft -c`, print it. Changes nothing.
#   --apply              load the table NOW and arm a rollback timer (--rollback-after, default 300 s). Unless --confirm runs
#                        before it fires, the table is deleted again. Nothing is enabled at boot yet, so a reboot rolls back too.
#   --confirm            cancel the rollback timer and enable zoe-panel-firewall.service so the table loads on every boot.
#   --rollback           delete the table, disable the unit, cancel any pending timer. Leaves the rendered file for inspection.
#   --status             show the loaded table, the unit and the timer.
#   --render-only --out FILE   render only (no root, no nft) - used by the tests.
#
# Options: --home-subnet CIDR   the home LAN (default: the IPv4 subnet of the default-route interface; must be private)
#          --rollback-after S   seconds before an unconfirmed --apply undoes itself (60..3600, default 300)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$HERE/zoe-panel-firewall.nft.in"
UNIT_SRC="$HERE/zoe-panel-firewall.service"
ETC_DIR="/etc/zoe-panel-firewall"
RULESET="$ETC_DIR/ruleset.nft"
UNIT_DST="/etc/systemd/system/zoe-panel-firewall.service"
TIMER_UNIT="zoe-panel-firewall-rollback"
TABLE="inet zoe_panel_fw"
NFT="$(command -v nft || echo /usr/sbin/nft)"

MODE="dry-run"
HOME_SUBNET=""
ROLLBACK_AFTER=300
OUT=""

die() { echo "install_panel_firewall: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --confirm) MODE="confirm" ;;
    --rollback) MODE="rollback" ;;
    --status) MODE="status" ;;
    --render-only) MODE="render-only" ;;
    --out) OUT="$2"; shift ;;
    --home-subnet) HOME_SUBNET="$2"; shift ;;
    --rollback-after) ROLLBACK_AFTER="$2"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

detect_home_subnet() {
  local dev cidr
  dev="$(ip -4 -o route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit }}')"
  [ -n "$dev" ] || return 1
  cidr="$(ip -4 -o addr show dev "$dev" 2>/dev/null | awk '{print $4; exit}')"
  [ -n "$cidr" ] || return 1
  python3 -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$cidr"
}

# A home subnet must be a private IPv4 network and never the tailnet range or a default route.
validate_subnet() {
  python3 - "$1" <<'PY'
import ipaddress, sys
try:
    net = ipaddress.ip_network(sys.argv[1], strict=False)
except ValueError:
    sys.exit("not a network: %s" % sys.argv[1])
if net.version != 4:
    sys.exit("the home subnet must be IPv4: %s" % net)
if net.prefixlen < 16:
    sys.exit("refusing a home subnet wider than /16: %s" % net)
if net.subnet_of(ipaddress.ip_network("100.64.0.0/10")):
    sys.exit("that is the tailnet range, not a home LAN: %s" % net)
if not net.is_private:
    sys.exit("refusing a non-private home subnet: %s" % net)
print(net)
PY
}

render() {
  local subnet="$1" dest="$2"
  [ -f "$TEMPLATE" ] || die "template missing: $TEMPLATE"
  python3 - "$TEMPLATE" "$subnet" "$dest" <<'PY'
import sys
template, subnet, dest = sys.argv[1:4]
text = open(template).read()
assert text.count("@HOME_V4@") == 1, "template must name the home subnet exactly once"
open(dest, "w").write(text.replace("@HOME_V4@", subnet))
PY
}

need_root() { [ "$(id -u)" = "0" ] || die "run as root (sudo)"; }

resolve_subnet() {
  if [ -z "$HOME_SUBNET" ]; then
    HOME_SUBNET="$(detect_home_subnet)" || die "could not detect the home subnet - pass --home-subnet CIDR"
    echo "install_panel_firewall: home subnet detected from the default route: $HOME_SUBNET" >&2
  fi
  HOME_SUBNET="$(validate_subnet "$HOME_SUBNET")" || die "bad --home-subnet"
}

cancel_timer() {
  systemctl stop "$TIMER_UNIT.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "$TIMER_UNIT.service" "$TIMER_UNIT.timer" >/dev/null 2>&1 || true
}

case "$MODE" in
  render-only)
    [ -n "$OUT" ] || die "--render-only needs --out FILE"
    [ -n "$HOME_SUBNET" ] || die "--render-only needs --home-subnet"
    HOME_SUBNET="$(validate_subnet "$HOME_SUBNET")" || die "bad --home-subnet"
    render "$HOME_SUBNET" "$OUT"
    ;;

  dry-run)
    need_root
    resolve_subnet
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    render "$HOME_SUBNET" "$tmp"
    "$NFT" -c -f "$tmp" || die "nft rejected the rendered ruleset"
    echo "# --- DRY RUN: nothing changed. This is what --apply would load (home subnet $HOME_SUBNET): ---"
    cat "$tmp"
    if [ -n "${SSH_CONNECTION:-}" ]; then
      echo "# your SSH session comes from ${SSH_CONNECTION%% *}; it survives --apply (established), and NEW SSH sessions"
      echo "# are accepted only from $HOME_SUBNET or the tailnet."
    fi
    ;;

  apply)
    need_root
    case "$ROLLBACK_AFTER" in ''|*[!0-9]*) die "--rollback-after must be seconds" ;; esac
    [ "$ROLLBACK_AFTER" -ge 60 ] && [ "$ROLLBACK_AFTER" -le 3600 ] || die "--rollback-after must be 60..3600"
    resolve_subnet
    install -d -m 0755 "$ETC_DIR"
    render "$HOME_SUBNET" "$RULESET.new"
    "$NFT" -c -f "$RULESET.new" || die "nft rejected the rendered ruleset; nothing loaded"
    mv -f "$RULESET.new" "$RULESET"
    chmod 0644 "$RULESET"
    install -m 0644 "$UNIT_SRC" "$UNIT_DST"
    systemctl daemon-reload
    cancel_timer
    # Arm the rollback BEFORE loading, so there is no window where the table is up with no way back.
    systemd-run --quiet --unit="$TIMER_UNIT" --on-active="$ROLLBACK_AFTER" --timer-property=AccuracySec=1s \
      /bin/sh -c "$NFT delete table $TABLE; echo 'zoe-panel-firewall: rolled back (not confirmed in time)'"
    "$NFT" -f "$RULESET"
    echo "install_panel_firewall: LOADED (home subnet $HOME_SUBNET). It rolls back by itself in $ROLLBACK_AFTER s."
    echo "install_panel_firewall: check you can still reach the panel, then run: sudo $0 --confirm"
    ;;

  confirm)
    need_root
    "$NFT" list table $TABLE >/dev/null 2>&1 || die "the table is not loaded (rolled back already?) - run --apply again"
    cancel_timer
    systemctl enable zoe-panel-firewall.service
    systemctl start zoe-panel-firewall.service
    echo "install_panel_firewall: CONFIRMED - the rollback timer is cancelled and the firewall loads on every boot."
    ;;

  rollback)
    need_root
    cancel_timer
    systemctl disable --now zoe-panel-firewall.service >/dev/null 2>&1 || true
    "$NFT" delete table $TABLE >/dev/null 2>&1 || true
    echo "install_panel_firewall: ROLLED BACK - table removed, unit disabled. ($RULESET kept for inspection.)"
    ;;

  status)
    "$NFT" list table $TABLE 2>/dev/null || echo "table $TABLE: not loaded"
    systemctl is-enabled zoe-panel-firewall.service 2>/dev/null || true
    systemctl list-timers "$TIMER_UNIT.timer" --no-pager 2>/dev/null || true
    ;;
esac
