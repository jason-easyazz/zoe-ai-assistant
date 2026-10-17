#!/usr/bin/env bash
# policy_apply.sh — the ONLY supported way to change (or re-check) the ACL policy.
#
#   cp config/acl.hujson config/acl.hujson.new && $EDITOR config/acl.hujson.new
#   ./policy_apply.sh config/acl.hujson.new      # check offline, apply only if it passed
#   ./policy_apply.sh --check                    # re-check the LIVE policy, change nothing
#
# Why check-then-restart rather than edit-and-reload (SIGHUP)? Headscale v0.29.4
# assigned a new policy before compilation finished, so a REJECTED hot reload could
# leave its packet filter live in memory (fixed in v0.29.5, upstream PR #3501; the pin
# is v0.29.5 under an owner-approved age-rule exception, and lab_test.sh proves the
# fixed behaviour and shows the v0.29.4 leak). The flow is kept anyway: a restart builds
# the policy from the file on disk, so only a policy that already PASSED the offline
# check is ever there. (A restart onto a policy whose tests fail only logs a warning
# and serves it, which is why nothing is swapped in before the check passes.)
#
# Why offline: `headscale policy check` evaluates the `tests` block against the real
# users and nodes only with --bypass-grpc-and-access-database-directly (the changelog:
# without it the command warns that the tests were skipped). That mode wants the
# server stopped, so headscale is stopped for the few seconds the check takes. Tunnels
# that are up keep working: nodes hold their last network map.
#
# Env (the lab uses these; production needs none): HS_COMPOSE (default
# "docker compose"), HS_CONFIG_DIR (default "config").
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=${HS_COMPOSE:-docker compose}
CONFIG_DIR=${HS_CONFIG_DIR:-config}
LIVE="$CONFIG_DIR/acl.hujson"

die() { echo "policy_apply: $*" >&2; exit 2; }
# shellcheck disable=SC2086  # COMPOSE is a command line on purpose
dc() { $COMPOSE "$@"; }

case "${1:-}" in
  "") die "usage: $0 config/acl.hujson.new | $0 --check" ;;
  --check) CAND="$LIVE"; APPLY=0 ;;
  *) CAND="$1"; APPLY=1 ;;
esac
[[ -f "$CAND" ]] || die "no such file: $CAND"
[[ "$(dirname "$CAND")" == "$CONFIG_DIR" ]] || die "the candidate must live in $CONFIG_DIR/ (the container only sees that directory)"
NAME="$(basename "$CAND")"

dc stop headscale > /dev/null
rc=0
out="$(dc run --rm --no-deps -T headscale policy check -f "/etc/headscale/$NAME" --bypass-grpc-and-access-database-directly --force 2>&1)" || rc=$?
echo "$out"

# Fail closed: nothing is applied unless the check passed AND really ran the tests.
if (( rc != 0 )) || ! grep -q "Policy is valid" <<< "$out" || grep -qi "skip" <<< "$out"; then
  echo "policy_apply: REJECTED, nothing was applied; restarting with the unchanged live policy" >&2
  dc up -d --wait headscale > /dev/null
  exit 1
fi

if (( APPLY )); then
  cp "$LIVE" "$LIVE.prev"
  mv "$CAND" "$LIVE"
  echo "policy_apply: applied (previous policy kept as $LIVE.prev)"
fi
dc up -d --wait headscale > /dev/null
echo "policy_apply: headscale restarted and healthy"
