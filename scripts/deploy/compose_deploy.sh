#!/bin/bash
# P0-1 deploy seam (docs/architecture/p0-deroot.md section 4.4): every Docker step deploy.yml runs, through the
# owner's root helper when it is installed, else exactly the commands deploy.yml runs today.
#   compose_deploy.sh zoe-auth [--build] | zoe-ui | modules | logs zoe-auth|zoe-ui
# Run from the live checkout after deploy.yml's reset to origin/main; the commit is that checkout's HEAD, handed to
# the helper on stdin (the helper re-checks it is on main and deploys from its own root-owned export, never this tree).
# Route (one fixed line): helper | legacy. The helper's exit 6 ("not ready": a bind source not yet moved to /srv/zoe
# by P0-2, or no root 0600 /etc/zoe/compose.env) falls back to legacy, which still works while `zoe` has the docker
# group; after P0-3 legacy fails loudly. Any other helper exit is the result.
set -euo pipefail
HELPER=/usr/local/libexec/zoe-priv/zoe-compose-deploy
CHECKOUT="$(cd "$(dirname "$0")/../.." && pwd)"
NGINX_IMAGE="nginx@sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60"   # = docker-compose.yml zoe-ui

usage() { echo "usage: compose_deploy.sh zoe-auth [--build] | zoe-ui | modules | logs zoe-auth|zoe-ui" >&2; exit 2; }
case "${1:-} ${2:-}" in
  "zoe-auth "|"zoe-auth --build"|"zoe-ui "|"modules "|"logs zoe-auth"|"logs zoe-ui") ;;
  *) usage ;;
esac
[ "$#" -le 2 ] || usage

legacy() {
  cd "$CHECKOUT"
  case "$1" in
    zoe-auth) docker compose up -d --no-deps ${2:+--build} zoe-auth ;;
    zoe-ui)
      docker run --rm --add-host zoe-auth:127.0.0.1 --add-host host.docker.internal:127.0.0.1 \
        -v "$PWD/services/zoe-ui/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
        -v "$PWD/services/zoe-ui/nginx.d:/etc/nginx/zoe:ro" \
        -v "$PWD/ssl:/etc/nginx/ssl:ro" \
        "$NGINX_IMAGE" nginx -t
      docker compose up -d --no-deps zoe-ui
      docker restart zoe-ui ;;
    modules)
      docker compose -f docker-compose.modules.yml config -q
      local running pending pending_file targets="" svc
      running="$(docker compose -f docker-compose.modules.yml ps --services --status running)"
      pending_file="${ZOE_DEPLOY_STATE_DIR:-$HOME/.zoe/deploy}/module-recreate-pending"
      pending="$(cat "$pending_file" 2>/dev/null || true)"
      for svc in $(docker compose -f docker-compose.modules.yml config --services); do
        if printf '%s\n' "$running" "$pending" | grep -qxF "$svc"; then targets="$targets $svc"; fi
      done
      if [ -z "$targets" ]; then echo "no running module containers to recreate."; return 0; fi
      echo "recreating changed module containers:$targets"
      mkdir -p "$(dirname "$pending_file")"
      # shellcheck disable=SC2086
      printf '%s\n' $targets > "$pending_file"
      # shellcheck disable=SC2086
      docker compose -f docker-compose.modules.yml up -d --no-deps --wait --wait-timeout 180 $targets
      rm -f "$pending_file" ;;
    logs)
      if [ "$2" = zoe-auth ]; then docker compose logs --tail=80 zoe-auth; else docker logs --tail=40 zoe-ui; fi ;;
  esac
}

if [ -f "$HELPER" ] && [ ! -L "$HELPER" ] && [ -x "$HELPER" ]; then
  echo "priv-route zoe-compose-deploy=helper"
  rc=0
  if [ "$1" = logs ]; then
    sudo -n "$HELPER" "$@" || rc=$?
  else
    sha="$(git -C "$CHECKOUT" rev-parse HEAD)"
    printf '%s\n' "$sha" | sudo -n "$HELPER" "$@" || rc=$?
  fi
  [ "$rc" -eq 6 ] || exit "$rc"
  echo "zoe-compose-deploy is not ready (exit 6: P0-2 bind moves or /etc/zoe/compose.env pending): legacy path"
fi
echo "priv-route zoe-compose-deploy=legacy"
legacy "$@"
