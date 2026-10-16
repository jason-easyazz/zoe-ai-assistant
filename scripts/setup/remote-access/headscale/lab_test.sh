#!/usr/bin/env bash
# lab_test.sh — hermetic local proof that this Headscale bundle does what the
# README claims. Re-runnable; tears everything down; touches nothing on the host.
#
#   flock -x /tmp/zoe-test-lane.lock ./lab_test.sh        # on the Zoe Jetson
#   ./lab_test.sh --keep                                  # leave the lab up to poke at it
#
# What runs (all in Docker, one INTERNAL bridge network, no route off the box):
#   headscale  the SHIPPED docker-compose.yml service + SHIPPED config/ (only the
#              hostname and the stand-in IP are substituted; checked below)
#   proxy      nginx rendering the SHIPPED nginx-hs.the411.life.conf (TLS from a
#              throwaway CA; production uses Let's Encrypt on the host nginx)
#   dummy      stands in for the Zoe box LAN address: 443, 8007 and an extra 9999
#   box/panel/owner   tailscale 1.104.1 in --tun=userspace-networking, so the
#              host tailscaled, interfaces, routes and DNS are never touched
#
# What it proves: see the check lines. Negative controls are explicit: every
# "refused" result is paired with a positive control showing the refused thing is
# alive and reachable some other way, and the ACL is then widened to show the
# refusal was the ACL and nothing else.
#
# Needs: docker + compose v2, openssl, jq, and >= 1500 MB MemAvailable (the voice
# stack shares the box; this refuses to start below that and aborts below 600 MB).
set -Eeuo pipefail

BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT=hslab
PROD_IP=192.168.1.218          # what the shipped ACL names
STANDIN_IP=10.77.250.40        # the dummy lab address; stands in for PROD_IP
OTHER_IP=10.77.250.41          # a DIFFERENT /32, must not be auto-approved
EXTRA_PORT=9999                # a port the ACL must NOT open
MIN_MEM_MB="${HS_LAB_MIN_MEM_MB:-1500}"
ABORT_MEM_MB="${HS_LAB_ABORT_MEM_MB:-600}"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

PASS=0
FAILN=0
RESULTS=()
WORK=""
DC=()

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

mem_avail_mb() { awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo; }

mem_guard() {
  local m
  m="$(mem_avail_mb)"
  if (( m < ABORT_MEM_MB )); then
    die "MemAvailable fell to ${m} MB (< ${ABORT_MEM_MB}); aborting so the voice stack is not squeezed"
  fi
}

record() { # record PASS|FAIL "description"
  if [[ "$1" == PASS ]]; then PASS=$((PASS + 1)); else FAILN=$((FAILN + 1)); fi
  RESULTS+=("$1  $2")
  printf '  %-4s  %s\n' "$1" "$2"
}

# expect "description" yes|no command...   (yes: command must succeed; no: must fail)
expect() {
  local desc="$1" want="$2" rc=0
  shift 2
  "$@" > /dev/null 2>&1 || rc=$?
  if [[ "$want" == yes && $rc -eq 0 ]] || [[ "$want" == no && $rc -ne 0 ]]; then
    record PASS "$desc"
  else
    record FAIL "$desc (rc=$rc; wanted $want)"
  fi
}

# eventually SECONDS command...   retry every 2 s until it succeeds.
eventually() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@" > /dev/null 2>&1; do
    if (( SECONDS >= deadline )); then return 1; fi
    sleep 2
  done
}

# until_fails SECONDS command...   retry every 2 s until it FAILS.
until_fails() {
  local deadline=$((SECONDS + $1))
  shift
  while "$@" > /dev/null 2>&1; do
    if (( SECONDS >= deadline )); then return 1; fi
    sleep 2
  done
}

hsc() { "${DC[@]}" exec -T headscale headscale "$@" 2> /dev/null; }
tsc() { local s="$1"; shift; "${DC[@]}" exec -T "$s" tailscale "$@"; }

# reachable SERVICE HOST PORT MARKER: true iff a TCP connection made by that
# tailscale node (through tailscaled, i.e. over the tailnet) to HOST:PORT returns MARKER.
reachable() {
  local out
  out="$("${DC[@]}" exec -T "$1" sh -c '(printf "GET / HTTP/1.0\r\n\r\n"; sleep 2) | timeout 8 tailscale nc "$1" "$2" 2> /dev/null' sh "$2" "$3" 2> /dev/null || true)"
  [[ "$out" == *"$4"* ]]
}

# direct SERVICE HOST PORT MARKER: the same, but a plain TCP connect on the lab
# bridge, bypassing the tailnet. This is the positive control for every refusal.
direct() {
  local out
  out="$("${DC[@]}" exec -T "$1" sh -c 'echo hi | timeout 6 nc -w 3 "$1" "$2" 2> /dev/null' sh "$2" "$3" 2> /dev/null || true)"
  [[ "$out" == *"$4"* ]]
}

# listen SERVICE PORT MARKER: background TCP responder inside a client container.
listen() {
  "${DC[@]}" exec -T -d "$1" sh -c 'while true; do echo "$2" | nc -l -p "$1" > /dev/null; done' sh "$2" "$3"
}

node_field() { # node_field NAME JQ-EXPR   (NAME = hostname the node registered with)
  hsc nodes list -o json | jq -r --arg n "$1" '.[] | select(.name == $n) | '"$2" 2> /dev/null
}
route_field() { # route_field NAME JQ-EXPR   from `nodes list-routes`
  hsc nodes list-routes -o json | jq -r --arg n "$1" '.[] | select(.name == $n) | '"$2" 2> /dev/null
}
node_count() { hsc nodes list -o json | jq 'length'; }
node_ip()    { node_field "$1" '.ip_addresses[0]'; }
node_id()    { node_field "$1" '.id'; }

teardown() {
  if [[ -z "${WORK}" ]]; then return 0; fi
  if (( KEEP )); then
    say "--keep: lab left running (project $PROJECT; workdir $WORK). Remove with:"
    say "  HS_LAB_DIR=$WORK docker compose -p $PROJECT -f $BUNDLE/docker-compose.yml -f $BUNDLE/lab/docker-compose.lab.yml down -v --remove-orphans"
    return 0
  fi
  if (( ${#DC[@]} )); then
    "${DC[@]}" down -v --remove-orphans --timeout 5 > /dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  WORK=""
}
trap 'teardown' EXIT

swap_used_mb() { awk '/^SwapTotal/ { t = $2 } /^SwapFree/ { f = $2 } END { print int((t - f) / 1024) }' /proc/meminfo; }

# ---------------------------------------------------------------- preflight --
step "Preflight"
for tool in docker openssl jq flock python3; do
  command -v "$tool" > /dev/null || die "missing $tool"
done
docker compose version > /dev/null 2>&1 || die "docker compose v2 is required"

exec 9> /tmp/zoe-headscale-lab.lock
flock -n 9 || die "another lab_test.sh is already running"

if [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ]]; then
  die "project $PROJECT already has containers; remove them first (docker compose -p $PROJECT down -v)"
fi

mem="$(mem_avail_mb)"
if (( mem < MIN_MEM_MB )); then
  die "MemAvailable is ${mem} MB; this lab needs >= ${MIN_MEM_MB} MB free so the voice stack never swaps. Retry later."
fi
SWAP_BEFORE="$(swap_used_mb)"
say "MemAvailable ${mem} MB (>= ${MIN_MEM_MB}); swap used ${SWAP_BEFORE} MB"

# The host own tailscale must be untouched; record it to compare at the end.
HOST_TS_BEFORE="n/a"
HOST_TSIF_BEFORE="$(ip -br link | grep -ci tailscale || true)"
if command -v tailscale > /dev/null; then HOST_TS_BEFORE="$( (tailscale status 2>&1 || true) | head -1)"; fi

# ------------------------------------------------------------------- render --
step "Render the lab from the SHIPPED files"
WORK="$(mktemp -d -t hslab.XXXXXX)"
mkdir -p "$WORK/config" "$WORK/tls"

# Shipped config.yaml: only the hostname differs.
sed 's#hs\.the411\.life#hs.lab.test#g' "$BUNDLE/config/config.yaml" > "$WORK/config/config.yaml"
# Shipped ACL: only the Zoe box LAN address is swapped for the dummy lab address.
sed "s#192\.168\.1\.218#$STANDIN_IP#g" "$BUNDLE/config/acl.hujson" > "$WORK/config/acl.hujson"
# Shipped nginx snippet: upstream, certificate paths and hostname only.
sed -e 's#127\.0\.0\.1:18080#headscale:8080#' \
    -e 's#/etc/letsencrypt/live/hs\.the411\.life/fullchain\.pem#/lab/tls/server.crt#' \
    -e 's#/etc/letsencrypt/live/hs\.the411\.life/privkey\.pem#/lab/tls/server.key#' \
    -e 's#hs\.the411\.life#hs.lab.test#g' \
    "$BUNDLE/nginx-hs.the411.life.conf" > "$WORK/hs.conf"
cp "$BUNDLE/lab/nginx.lab.conf" "$WORK/nginx.conf"

# Prove the substitutions are the ONLY differences (undo them, expect identity).
expect "lab config.yaml == shipped config.yaml except the hostname" yes \
  diff <(sed 's#hs\.lab\.test#hs.the411.life#g' "$WORK/config/config.yaml") "$BUNDLE/config/config.yaml"
expect "lab acl.hujson == shipped acl.hujson except the stand-in IP" yes \
  diff <(sed "s#$STANDIN_IP#$PROD_IP#g" "$WORK/config/acl.hujson") "$BUNDLE/config/acl.hujson"

# Throwaway CA/cert for hs.lab.test (the production cert comes from certbot).
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 \
  -subj "/CN=hs.lab.test" -addext "subjectAltName=DNS:hs.lab.test" \
  -keyout "$WORK/tls/server.key" -out "$WORK/tls/server.crt" 2> /dev/null
cp "$WORK/tls/server.crt" "$WORK/tls/ca.crt"
chmod 644 "$WORK/tls/server.key"

export HS_LAB_DIR="$WORK"
DC=(docker compose -p "$PROJECT" -f "$BUNDLE/docker-compose.yml" -f "$BUNDLE/lab/docker-compose.lab.yml")
expect "compose config is valid (shipped file + lab override)" yes "${DC[@]}" config -q

# ------------------------------------------------------------------- bring up --
step "Bring up the lab"
mem_guard
"${DC[@]}" up -d --pull missing --wait --wait-timeout 120 headscale proxy dummy box panel owner
mem_guard
say "MemAvailable after bring-up: $(mem_avail_mb) MB"

HS_CID="$("${DC[@]}" ps -q headscale)"
expect "headscale container is healthy (shipped healthcheck)" yes \
  test "$(docker inspect -f '{{.State.Health.Status}}' "$HS_CID")" = healthy
expect "headscale runs with a read-only root filesystem" yes \
  test "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$HS_CID")" = true
expect "headscale runs with every capability dropped" yes \
  test "$(docker inspect -f '{{json .HostConfig.CapDrop}}' "$HS_CID")" = '["ALL"]'
expect "client containers run tailscale 1.104.1" yes \
  test "$(tsc panel version | head -1)" = 1.104.1

count_is()   { [[ "$(node_count)" == "$1" ]]; }
field_is()   { [[ "$(node_field "$1" "$2")" == "$3" ]]; }          # NAME JQ VALUE
route_is()   { [[ "$(route_field "$1" "$2")" == "$3" ]]; }          # NAME JQ VALUE
log_has()    { local o; o="$("${DC[@]}" logs --no-log-prefix "$1" 2> /dev/null || true)"; grep -Eq "$2" <<< "$o"; }
mkkey()      { hsc preauthkeys create --tags "$1" --expiration 1h -o json | jq -r '.key'; }

# ------------------------------------------------------------- control plane --
step "Control plane: shipped policy loaded at boot, then the owner user is created"
expect "boot with an empty tailnet logs the (expected) policy-tests warning but starts" yes \
  log_has headscale "policy tests failed at boot"
expect "embedded DERP + STUN started (config derp.server.enabled)" yes \
  log_has headscale "stun server started"

hsc users create owner > /dev/null
expect "user owner exists" yes test -n "$(hsc users list -o json | jq -r '.[] | select(.name == "owner") | .id')"

KEY_BOX="$(mkkey tag:zoe-box)"
KEY_PANEL="$(mkkey tag:zoe-panel)"
expect "tagged single-use pre-auth keys minted without a user (box, panel)" yes test -n "$KEY_BOX" -a -n "$KEY_PANEL"

# --------------------------------------------------- registration is closed --
step "No open registration: an unapproved node cannot join"
OUT="$(tsc owner up --login-server=https://hs.lab.test --hostname=lab-owner --accept-dns=false --timeout=10s 2>&1 || true)"
expect "keyless tailscale up only produces an approval URL" yes grep -q "/register/" <<< "$OUT"
expect "...and creates no node" yes count_is 0
expect "a bogus pre-auth key is refused" no \
  tsc owner up --login-server=https://hs.lab.test --authkey=hskey-auth-bogusbogusbogus --hostname=lab-owner --accept-dns=false --timeout=15s
expect "...and creates no node" yes count_is 0

# ------------------------------------------------------------------ join -----
step "Join the box (tagged key; advertises ONLY the /32) and the panel (--accept-routes)"
tsc box up --login-server=https://hs.lab.test --authkey="$KEY_BOX" --hostname=lab-box \
  --advertise-routes="$STANDIN_IP/32" --accept-dns=false --timeout=60s > /dev/null
tsc panel up --login-server=https://hs.lab.test --authkey="$KEY_PANEL" --hostname=lab-panel \
  --accept-routes --accept-dns=false --timeout=60s > /dev/null
expect "box and panel joined with their keys" yes eventually 30 count_is 2
expect "box carries tag:zoe-box and is owned by tagged-devices" yes \
  field_is lab-box '(.tags // []) | join(",")' tag:zoe-box
expect "panel carries tag:zoe-panel" yes field_is lab-panel '(.tags // []) | join(",")' tag:zoe-panel
expect "a used single-use key cannot enrol a second node" no \
  tsc owner up --login-server=https://hs.lab.test --authkey="$KEY_BOX" --hostname=lab-owner --accept-dns=false --timeout=15s
expect "...still only two nodes" yes count_is 2

# -------------------------------------------------------- route auto-approval --
step "Route auto-approval is for the /32 only"
expect "the box /32 is auto-approved at join" yes \
  eventually 30 route_is lab-box '(.approved_routes // []) | join(",")' "$STANDIN_IP/32"
tsc box set --advertise-routes="$STANDIN_IP/32,10.77.250.0/24,10.99.99.0/24,$OTHER_IP/32" --advertise-exit-node
expect "extra prefixes are AVAILABLE on the box (wider /24, unrelated /24, another /32, exit node)" yes \
  eventually 30 route_is lab-box '(.available_routes // []) | (index("10.77.250.0/24") != null and index("10.99.99.0/24") != null and index("10.77.250.41/32") != null and index("0.0.0.0/0") != null)' true
sleep 3
expect "NEGATIVE: none of them is auto-approved (approved is still only the /32)" yes \
  route_is lab-box '(.approved_routes // []) | join(",")' "$STANDIN_IP/32"
BOX_ID="$(node_id lab-box)"
hsc nodes approve-routes -i "$BOX_ID" -r "$STANDIN_IP/32,10.99.99.0/24" > /dev/null
expect "an admin CAN approve another prefix by hand (the owner approval path)" yes \
  eventually 20 route_is lab-box '(.approved_routes // []) | sort | join(",")' "$STANDIN_IP/32,10.99.99.0/24"
hsc nodes approve-routes -i "$BOX_ID" -r "$STANDIN_IP/32" > /dev/null
tsc box set --advertise-routes="$STANDIN_IP/32" --advertise-exit-node=false
expect "box restored to the README state: advertises and is approved for the /32 only" yes \
  eventually 30 route_is lab-box '((.approved_routes // []) | join(",")) + "|" + ((.available_routes // []) | join(","))' "$STANDIN_IP/32|$STANDIN_IP/32"
mem_guard

# ------------------------------------------------------------- owner device --
step "Owner laptop: web-auth registration, approved by the admin (personal node)"
"${DC[@]}" exec -T -d owner sh -c 'tailscale up --login-server=https://hs.lab.test --hostname=lab-owner --accept-dns=false > /tmp/up.log 2>&1'
AUTH_ID=""
for _ in $(seq 1 30); do
  AUTH_ID="$("${DC[@]}" exec -T owner sh -c 'sed -n "s#.*/register/\([A-Za-z0-9_-]*\).*#\1#p" /tmp/up.log | head -1' 2> /dev/null || true)"
  [[ -n "$AUTH_ID" ]] && break
  sleep 1
done
expect "the laptop is waiting on an approval URL (auth id captured)" yes test -n "$AUTH_ID"
expect "NEGATIVE: while pending it is NOT in the node list" yes count_is 2
hsc auth register --user owner --auth-id "$AUTH_ID" > /dev/null
expect "after the admin approves, the laptop joins" yes eventually 40 count_is 3
expect "...as a personal node of user owner" yes field_is lab-owner '.user.name' owner

BOX_TS="$(node_ip lab-box)"
PANEL_TS="$(node_ip lab-panel)"
OWNER_TS="$(node_ip lab-owner)"
listen box 22 box-22
listen box 443 box-443
listen panel 22 panel-22
listen panel 443 panel-443
listen owner 22 owner-22
sleep 2

step "Untagged node advertising the same /32 is not auto-approved"
tsc owner set --advertise-routes="$STANDIN_IP/32"
expect "owner (untagged) advertises the /32" yes \
  eventually 30 route_is lab-owner '(.available_routes // []) | join(",")' "$STANDIN_IP/32"
sleep 3
expect "NEGATIVE: ...but it is not approved (auto-approval is bound to tag:zoe-box)" yes \
  route_is lab-owner '(.approved_routes // []) | join(",")' ""
tsc owner set --advertise-routes=

# ------------------------------------------------------------------ data path --
step "Data path: the panel reaches the dummy through the box subnet route"
expect "panel -> dummy:443 over the tailnet" yes eventually 90 reachable panel "$STANDIN_IP" 443 dummy-443
expect "panel -> dummy:8007 over the tailnet" yes eventually 30 reachable panel "$STANDIN_IP" 8007 dummy-8007
expect "the path is the embedded DERP (tailscale ping: via DERP(headscale))" yes \
  grep -q "DERP(headscale)" <<< "$(tsc panel ping --c=3 --timeout=5s "$BOX_TS" 2>&1 || true)"

step "Negative controls: refused, and refused BECAUSE of the ACL"
expect "control: dummy:$EXTRA_PORT is alive (plain connect from the box on the lab bridge)" yes \
  direct box "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
expect "NEGATIVE: panel -> dummy:$EXTRA_PORT (extra port) is refused" no \
  reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
expect "control: the box answers on 22 and 443 locally" yes direct box 127.0.0.1 22 box-22
expect "NEGATIVE: panel -> box tailnet address :22 (direct box access) is refused" no \
  reachable panel "$BOX_TS" 22 box-22
expect "NEGATIVE: panel -> box tailnet address :443 is refused" no reachable panel "$BOX_TS" 443 box-443
expect "control: the owner laptop answers locally" yes direct owner 127.0.0.1 22 owner-22
expect "NEGATIVE: panel -> owner laptop is refused" no reachable panel "$OWNER_TS" 22 owner-22

step "ssh intent: box and owner devices -> panel:22 only"
expect "box -> panel:22 allowed" yes eventually 30 reachable box "$PANEL_TS" 22 panel-22
expect "owner laptop -> panel:22 allowed" yes eventually 30 reachable owner "$PANEL_TS" 22 panel-22
expect "control: panel:443 answers locally" yes direct panel 127.0.0.1 443 panel-443
expect "NEGATIVE: box -> panel:443 refused" no reachable box "$PANEL_TS" 443 panel-443
expect "NEGATIVE: owner laptop -> panel:443 refused" no reachable owner "$PANEL_TS" 443 panel-443
expect "NEGATIVE: owner laptop -> box tailnet address :22 refused" no reachable owner "$BOX_TS" 22 box-22
mem_guard

log_since() { local o; o="$("${DC[@]}" logs --no-log-prefix --since "${3}s" "$1" 2> /dev/null || true)"; grep -Eq "$2" <<< "$o"; }

# ----------------------------------------------------- policy change flow --
# The supported flow (README "Changing the policy"): policy_apply.sh checks the
# candidate OFFLINE against the real users and nodes (--bypass-grpc-and-access-
# database-directly, the only mode that runs the tests block), applies it only if it
# passed, and RESTARTS headscale. The hazard cell below separately proves what a
# rejected hot reload does on the pinned v0.29.5 (old policy stays live) and shows the
# v0.29.4 leak it fixed (upstream PR #3501).
step "Policy change flow: the refusal of dummy:$EXTRA_PORT is the ACL, nothing else"
ACL="$WORK/config/acl.hujson"
CAND="$WORK/config/acl.hujson.new"
cp "$ACL" "$WORK/acl.orig"
apply() { HS_COMPOSE="${DC[*]}" HS_CONFIG_DIR="$WORK/config" "$BUNDLE/policy_apply.sh" "$@" 2>&1; }

# (0) the shipped policy: the offline check really RUNS the tests block and passes.
rc=0; OUT="$(apply --check)" || rc=$?
expect "shipped policy passes the offline check (Policy is valid, headscale back up)" yes test "$rc" -eq 0
expect "...and the check did not warn that the tests were skipped" no grep -qi skip <<< "$OUT"
expect "...and headscale is healthy again (tunnels kept working across the restart)" yes \
  eventually 60 reachable panel "$STANDIN_IP" 443 dummy-443

# (a) widened policy that contradicts the tests block: the check MUST fail (negative
# control that the tests block is executed, not skipped) and nothing is applied.
sed "s#:443,8007\"#:443,8007,$EXTRA_PORT\"#" "$WORK/acl.orig" > "$CAND"
rc=0; OUT="$(apply "$CAND")" || rc=$?
expect "NEGATIVE: a policy violating its tests block fails the offline check" yes test "$rc" -ne 0
expect "...with the tests failure in the output (the tests were executed)" yes grep -q 'test(s) failed' <<< "$OUT"
expect "...and it was NOT applied (live policy file unchanged)" yes cmp -s "$ACL" "$WORK/acl.orig"
expect "...and headscale was restarted on the unchanged policy: dummy:$EXTRA_PORT still refused" no \
  reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
expect "...while 443 still works" yes eventually 60 reachable panel "$STANDIN_IP" 443 dummy-443

# (b) the same widening WITHOUT the tests block passes the check and is applied: the port opens.
sed "s#:443,8007\"#:443,8007,$EXTRA_PORT\"#" "$WORK/acl.orig" | sed '/^  "tests": \[/,/^  \],$/d' > "$CAND"
rc=0; OUT="$(apply "$CAND")" || rc=$?
expect "a widened policy WITHOUT a conflicting test passes the check and is applied" yes test "$rc" -eq 0
expect "POSITIVE CONTROL: with the ACL widened, panel -> dummy:$EXTRA_PORT now works" yes \
  eventually 90 reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT

# (c) restore the shipped policy through the same flow: the port closes again.
cp "$WORK/acl.orig" "$CAND"
rc=0; OUT="$(apply "$CAND")" || rc=$?
expect "restoring the shipped policy passes the check and is applied" yes test "$rc" -eq 0
expect "restored policy closes dummy:$EXTRA_PORT again" yes \
  until_fails 90 reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
expect "...while 443 and 8007 keep working" yes eventually 60 reachable panel "$STANDIN_IP" 443 dummy-443
expect "...and 8007" yes reachable panel "$STANDIN_IP" 8007 dummy-8007

# ------------------------------------------- rejected-policy hazard (#3501) --
step "Rejected-policy hazard: v0.29.5 keeps the old policy live, v0.29.4 leaks"
# A policy that passes its (absent) tests and the user-reference check but fails to
# COMPILE: a nodeAttrs target naming an ambiguous user ("dup@" matches user dup by
# name and user dupb by email). It also widens the ACL by one port.
hsc users create dup > /dev/null
hsc users create dupb --email dup > /dev/null
HAZ="$WORK/acl.hazard"
sed "s#:443,8007\"#:443,8007,$EXTRA_PORT\"#" "$WORK/acl.orig" | sed '/^  "tests": \[/,/^  \],$/d' \
  | sed 's#^  "acls": \[#  "nodeAttrs": [ {"target": ["dup@"], "attr": ["randomize-client-port"]} ],\n  "acls": [#' > "$HAZ"
HS_IMAGE_REF() { docker inspect -f '{{.Config.Image}}' "$("${DC[@]}" ps -q headscale)"; }
expect "headscale runs the pinned v0.29.5 image (tag + digest)" yes \
  grep -q 'headscale:v0.29.5@sha256:780ed2f9' <<< "$(HS_IMAGE_REF)"

# The safe flow rejects it before it is ever applied, on any version.
cp "$HAZ" "$CAND"
rc=0; OUT="$(apply "$CAND")" || rc=$?
expect "policy_apply.sh REJECTS the compile-failing policy (offline check)" yes test "$rc" -ne 0
expect "...and it was not applied (live policy file unchanged)" yes cmp -s "$ACL" "$WORK/acl.orig"
rm -f "$CAND"

# Hot-reload it anyway (what an operator should NOT do). On v0.29.4 the rejected policy
# is left compiled in memory but the per-node filter CACHE is not cleared on that error
# path, so the leak shows up the moment a node-set change invalidates the box's cache.
# The lab causes exactly that: the box advertises a second route and the admin approves
# it, then both nodes reconnect to take full maps. Then: did dummy:9999 open?
BOX_ID="$(node_id lab-box)"
BOX_ROUTES="$STANDIN_IP/32,10.99.99.0/24"
tsc box set --advertise-routes="$BOX_ROUTES"
eventually 30 route_is lab-box '(.available_routes // []) | index("10.99.99.0/24") != null' true || true
hot_reload_hazard() {
  cp "$HAZ" "$ACL"
  "${DC[@]}" kill -s HUP headscale > /dev/null
  sleep 6
  hsc nodes approve-routes -i "$BOX_ID" -r "$BOX_ROUTES" > /dev/null || true
  sleep 4
  tsc box down > /dev/null 2>&1 || true
  tsc box up --login-server=https://hs.lab.test --hostname=lab-box --advertise-routes="$BOX_ROUTES" --accept-dns=false --timeout=60s > /dev/null 2>&1 || true
  tsc panel down > /dev/null 2>&1 || true
  tsc panel up --login-server=https://hs.lab.test --hostname=lab-panel --accept-routes --accept-dns=false --timeout=60s > /dev/null 2>&1 || true
}
restore_shipped() {
  cp "$WORK/acl.orig" "$ACL"
  "${DC[@]}" up -d --force-recreate --wait --wait-timeout 90 headscale > /dev/null 2>&1
  hsc nodes approve-routes -i "$BOX_ID" -r "$STANDIN_IP/32" > /dev/null || true
}

hot_reload_hazard
expect "v0.29.5: the panel is back on the tailnet (443 works)" yes eventually 90 reachable panel "$STANDIN_IP" 443 dummy-443
expect "v0.29.5: the REJECTED widening is NOT live after HUP + reconnect: dummy:$EXTRA_PORT refused" no \
  reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
restore_shipped

# CONTROL: the same experiment on v0.29.4 (image swapped in place; same database).
DC_OLD=("${DC[@]}" -f "$BUNDLE/lab/docker-compose.v0294.yml")
"${DC_OLD[@]}" up -d --wait --wait-timeout 90 headscale > /dev/null 2>&1
expect "control image is v0.29.4" yes grep -q 'headscale:v0.29.4@sha256:8833f828' <<< "$(HS_IMAGE_REF)"
expect "v0.29.4 baseline: panel on the tailnet, dummy:$EXTRA_PORT still refused under the shipped policy" yes \
  eventually 90 reachable panel "$STANDIN_IP" 443 dummy-443
expect "...refused" no reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT
hot_reload_hazard
expect "v0.29.4 CONTROL: the same rejected policy LEAKS (dummy:$EXTRA_PORT reachable after HUP + reconnect)" yes \
  eventually 60 reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT

# Back to the shipped image and policy.
restore_shipped
expect "back on the shipped v0.29.5 image" yes grep -q 'headscale:v0.29.5@sha256:780ed2f9' <<< "$(HS_IMAGE_REF)"
tsc box set --advertise-routes="$STANDIN_IP/32"
expect "shipped policy restored: dummy:$EXTRA_PORT closed again, 443 and 8007 work" yes \
  eventually 90 reachable panel "$STANDIN_IP" 8007 dummy-8007
expect "...dummy:$EXTRA_PORT refused" no reachable panel "$STANDIN_IP" "$EXTRA_PORT" dummy-$EXTRA_PORT

# ------------------------------------------------- DERP / STUN / websocket --
step "Embedded DERP, STUN and the nginx websocket upgrade"
expect "DERP map holds ONLY the embedded region (derp.urls empty)" yes \
  test "$(tsc panel debug derp-map | jq -r '.Regions | keys | join(",")')" = 999
expect "tailscale debug derp headscale succeeds" yes tsc panel debug derp headscale
# STUN is proved with a raw Binding request (lab/stun_probe.py): directly at the
# headscale container, and through the lab UDP relay that stands in for the
# production host publish. (`tailscale netcheck` from the userspace lab client
# reports UDP false even though both probes below are answered, so it is not used
# as the assertion; see the README, "What the lab cannot prove".)
expect "STUN: the embedded DERP answers a Binding request on UDP 3478 (direct)" yes \
  python3 -I "$BUNDLE/lab/stun_probe.py" 10.77.250.2
expect "STUN: ...and through the UDP relay on the proxy" yes \
  python3 -I "$BUNDLE/lab/stun_probe.py" 10.77.250.3
expect "nginx upgraded the control connections (POST /ts2021 -> 101)" yes \
  log_since proxy 'POST /ts2021.* 101' 3600
expect "nginx upgraded DERP connections (/derp -> 101)" yes log_since proxy '/derp.* 101' 3600

# --------------------------------------------------------------- resources --
step "Resources and host isolation"
mem_guard
SWAP_AFTER="$(swap_used_mb)"
say "MemAvailable now $(mem_avail_mb) MB; swap used ${SWAP_BEFORE} -> ${SWAP_AFTER} MB"
HOST_TS_AFTER="n/a"
if command -v tailscale > /dev/null; then HOST_TS_AFTER="$( (tailscale status 2>&1 || true) | head -1)"; fi
expect "host tailscaled untouched (status unchanged: $HOST_TS_BEFORE)" yes test "$HOST_TS_AFTER" = "$HOST_TS_BEFORE"
expect "no new tailscale interface appeared on the host (before: ${HOST_TSIF_BEFORE})" yes test "$(ip -br link | grep -ci tailscale || true)" = "$HOST_TSIF_BEFORE"

# ---------------------------------------------------------------- teardown --
step "Teardown"
teardown
if (( ! KEEP )); then
  expect "no lab container left" yes test -z "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
  expect "no lab network left" yes test -z "$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")"
  expect "no lab volume left" yes test -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
fi

printf '\n== SUMMARY: %d passed, %d failed ==\n' "$PASS" "$FAILN"
printf '%s\n' "${RESULTS[@]}"
if (( FAILN > 0 )); then exit 1; fi
