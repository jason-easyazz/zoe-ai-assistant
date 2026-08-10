#!/usr/bin/env bash
# ============================================================================
# install-jetson.sh — one-command install for the Zoe host (NVIDIA Jetson Orin)
# ============================================================================
# Orchestrates the manual README steps, idempotently:
#   1. Preflight (docker, compose, python3, git, curl; warn if not a Jetson)
#   2. Generate .env secrets (never bakes rotatable secrets by hand)
#   3. Start the Docker spine (db, auth, ui, home assistant)
#   4. Run PostgreSQL migrations (alembic + auth DDL, via scripts/deploy/migrate.sh)
#   5. Download the canonical Gemma 4 E4B-QAT + MTP brain
#   6. Install + enable the host-native systemd spine (llama-server, zoe-data, kokoro-tts);
#      opt-in units (flue-executor, flue-zoe-brain-2x) are installed as inert templates only
#   7. Health-gate the result
#
# Re-runnable: existing .env / models / running services are left in place.
#
# Usage:
#   scripts/setup/install-jetson.sh [options]
# Options:
#   -y, --yes         non-interactive (assume yes to prompts)
#   --skip-models     don't download the GGUF brain (e.g. models already staged)
#   --skip-docker     don't touch the Docker layer (implies --skip-migrations)
#   --skip-systemd    don't install/enable host-native units
#   --skip-migrations don't run DB migrations (e.g. an externally-managed DB)
#   --with-router     also fetch the two-stage router decoder model
#   --models-dir DIR  override the brain model directory
#   --llama-bin PATH  path to the llama-server binary (default: the Jetson build path)
#   -h, --help        show this help
# ============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/setup/lib/common.sh
source "${ROOT_DIR}/scripts/setup/lib/common.sh"

ASSUME_YES=0
SKIP_MODELS=0
SKIP_DOCKER=0
SKIP_SYSTEMD=0
SKIP_MIGRATIONS=0
WITH_ROUTER=0
MODELS_DIR="${HOME}/models/gemma4-e4b-qat"

DOCKER_SPINE=(zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge)
SYSTEMD_SPINE=(llama-server zoe-data kokoro-tts)
# Opt-in units: their templates are installed (the *.service glob below copies
# them) but they are DELIBERATELY never auto-enabled — each sits behind a
# default-OFF seam and needs an explicit operator go-live step. flue-executor is
# the Multica queue consumer (executor migration Phase 2); it ships inert — not
# enabled, ZOE_EXECUTOR_DISPATCH defaults dry, single lane. The installer never
# touches the shared Multica dispatch pause sentinel: arming that pause is an
# operator go-live action, not a provisioning side effect (it is also read by the
# live zoe-data poll loop, so recreating it on reinstall would halt live dispatch).
OPTIONAL_UNITS=(flue-executor flue-zoe-brain-2x)
LLAMA_BIN="${HOME}/llama.cpp/build-jetson-new/bin/llama-server"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1; shift ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --skip-systemd) SKIP_SYSTEMD=1; shift ;;
    --skip-migrations) SKIP_MIGRATIONS=1; shift ;;
    --with-router) WITH_ROUTER=1; shift ;;
    --models-dir) MODELS_DIR="$2"; shift 2 ;;
    --llama-bin) LLAMA_BIN="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown argument: $1 (try --help)" ;;
  esac
done
export ASSUME_YES

# docker compose v2 (subcommand) with a fallback to legacy docker-compose.
compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@";
  else docker-compose "$@"; fi
}

# ── 1. Preflight ────────────────────────────────────────────────────────────
step "Preflight"
require_cmd docker "Install Docker + the Compose plugin first."
docker compose version >/dev/null 2>&1 || have_cmd docker-compose \
  || die "Docker Compose plugin (or docker-compose) is required."
require_cmd python3
require_cmd git
require_cmd curl
ok "core tools present"

if have_cmd nvidia-smi || [[ -f /etc/nv_tegra_release ]]; then
  ok "NVIDIA/Jetson platform detected"
else
  warn "No NVIDIA/Jetson platform detected — the LLM + voice steps assume CUDA."
  warn "See HARDWARE_COMPATIBILITY.md; continuing (host services may need tuning)."
fi

# ── 2. Secrets / .env ───────────────────────────────────────────────────────
step "Environment (.env)"
ROOT_ENV="${ROOT_DIR}/.env"
DATA_ENV="${ROOT_DIR}/services/zoe-data/.env"

if [[ -f "$ROOT_ENV" ]]; then
  ok "root .env already exists — leaving it untouched"
else
  cp "${ROOT_DIR}/.env.example" "$ROOT_ENV"
  db_pass="$(gen_secret 18)"
  pg_url="postgresql://zoe:${db_pass}@localhost:5432/zoe"
  aps_url="postgresql+psycopg2://zoe:${db_pass}@localhost:5432/zoe"
  env_set "$ROOT_ENV" POSTGRES_PASSWORD "$db_pass"
  env_set "$ROOT_ENV" POSTGRES_URL "$pg_url"
  env_set "$ROOT_ENV" POSTGRES_APSCHEDULER_URL "$aps_url"
  env_set "$ROOT_ENV" OPENCLAW_GATEWAY_TOKEN "$(gen_secret 24)"
  env_set "$ROOT_ENV" KEEPER_AUTH_TOKEN "$(gen_secret 24)"
  env_set "$ROOT_ENV" ZOE_INTERNAL_TOKEN "$(gen_secret 32)"
  ok "generated root .env with fresh DB password + service tokens"
  warn "User-supplied values are still blank in $ROOT_ENV:"
  warn "  ANTHROPIC_API_KEY / OPENAI_API_KEY (only if using cloud agent tiers)"
  warn "  HA_ACCESS_TOKEN (create in Home Assistant → Profile → Security)"
fi

if [[ ! -f "$DATA_ENV" ]]; then
  cp "${ROOT_DIR}/services/zoe-data/.env.example" "$DATA_ENV"
  # Mirror the DB URLs so host-native zoe-data + alembic agree with the root env.
  pg_url="$(env_get "$ROOT_ENV" POSTGRES_URL)"
  aps_url="$(env_get "$ROOT_ENV" POSTGRES_APSCHEDULER_URL)"
  [[ -n "$pg_url" ]] && env_set "$DATA_ENV" POSTGRES_URL "$pg_url"
  [[ -n "$aps_url" ]] && env_set "$DATA_ENV" POSTGRES_APSCHEDULER_URL "$aps_url"
  ok "generated services/zoe-data/.env"
else
  ok "services/zoe-data/.env already exists — leaving it untouched"
fi

# ── 3. Docker spine ─────────────────────────────────────────────────────────
if [[ "$SKIP_DOCKER" == "0" ]]; then
  step "Docker spine"
  ( cd "$ROOT_DIR" && compose up -d "${DOCKER_SPINE[@]}" )
  ok "started: ${DOCKER_SPINE[*]}"

  log "Waiting for zoe-database to become healthy…"
  for i in $(seq 1 60); do
    state="$(docker inspect -f '{{.State.Health.Status}}' zoe-database 2>/dev/null || echo unknown)"
    [[ "$state" == "healthy" ]] && break
    sleep 2
    [[ "$i" == 60 ]] && warn "zoe-database not healthy after 120s (state: $state) — continuing"
  done
  [[ "${state:-}" == "healthy" ]] && ok "zoe-database healthy"
else
  warn "--skip-docker: not touching the Docker layer"
fi

# ── 4. Database migrations ──────────────────────────────────────────────────
step "Database migrations"
if [[ "$SKIP_MIGRATIONS" == "1" ]]; then
  warn "--skip-migrations: not running DB migrations"
elif [[ "$SKIP_DOCKER" == "1" ]]; then
  warn "--skip-docker set: skipping migrations (Postgres was not started by this run)."
  warn "  Run scripts/deploy/migrate.sh yourself once your database is reachable."
elif [[ ! -x "${ROOT_DIR}/scripts/deploy/migrate.sh" ]]; then
  warn "scripts/deploy/migrate.sh missing — skipping migrations"
elif ( cd "$ROOT_DIR" && bash scripts/deploy/migrate.sh ); then
  ok "alembic + auth DDL applied"
else
  die "Database migrations failed — is Postgres up and POSTGRES_URL correct? (see migrate.sh output above). Re-run once it's reachable, or pass --skip-migrations."
fi

# ── 5. Models ───────────────────────────────────────────────────────────────
if [[ "$SKIP_MODELS" == "0" ]]; then
  step "Local brain (Gemma 4 E4B-QAT + MTP)"
  args=(--models-dir "$MODELS_DIR")
  [[ "$WITH_ROUTER" == "1" ]] && args+=(--with-router)
  bash "${ROOT_DIR}/scripts/setup/download_gguf_models.sh" "${args[@]}"
else
  warn "--skip-models: not downloading the GGUF brain"
fi

# ── 6. Host-native systemd spine ────────────────────────────────────────────
if [[ "$SKIP_SYSTEMD" == "0" ]]; then
  step "Host-native services (systemd --user)"
  mkdir -p "${HOME}/.config/systemd/user"
  cp "${ROOT_DIR}"/scripts/setup/systemd/*.service "${HOME}/.config/systemd/user/"
  cp "${ROOT_DIR}"/scripts/setup/systemd/*.timer "${HOME}/.config/systemd/user/" 2>/dev/null || true
  systemctl --user daemon-reload
  ok "unit templates installed"

  enable_units=("${SYSTEMD_SPINE[@]}")
  if [[ ! -x "$LLAMA_BIN" ]]; then
    warn "llama-server binary not found at $LLAMA_BIN."
    warn "Build llama.cpp for your platform and edit"
    warn "  ~/.config/systemd/user/llama-server.service (--model / --model-draft / binary path),"
    warn "then: systemctl --user enable --now llama-server"
    enable_units=("${enable_units[@]/llama-server}")
  fi
  # shellcheck disable=SC2206
  enable_units=(${enable_units[@]})  # drop the empty slot if llama-server removed
  # Safety: an opt-in unit must never be auto-armed by the installer. If a future
  # edit slips one into SYSTEMD_SPINE, fail loudly rather than silently going live.
  # `systemctl enable` also accepts unit-FILE PATHS, so canonicalize by BASENAME
  # then strip '.service' on BOTH sides — bare, '.service', and path spellings
  # (./flue-executor.service, /tmp/flue-zoe-brain-2x.service, dir/flue-executor) all
  # reduce to the same key and are caught. The spine should never carry paths at
  # all, so a '/' in an entry is itself a defect.
  for u in "${enable_units[@]}"; do
    [[ "$u" == */* ]] && die "SYSTEMD_SPINE entry '$u' is a path, not a unit name (spine must be bare names)"
    u_key="${u##*/}"; u_key="${u_key%.service}"
    for opt in "${OPTIONAL_UNITS[@]}"; do
      opt_key="${opt##*/}"; opt_key="${opt_key%.service}"
      [[ "$u_key" == "$opt_key" ]] \
        && die "refusing to auto-enable opt-in unit '$opt' (default-OFF safety gate)"
    done
  done
  if [[ ${#enable_units[@]} -gt 0 ]]; then
    systemctl --user enable --now "${enable_units[@]}"
    ok "enabled: ${enable_units[*]}"
  fi

  # Surface the opt-in units: installed as inert templates, never enabled here.
  # The installer deliberately does NOT touch the shared Multica dispatch pause
  # sentinel (~/.zoe/multica_dispatch_paused). That file is also read every cycle
  # by the live zoe-data poll loop and multica_board_runner, so recreating it at
  # install time would HALT all live engineering dispatch on a reinstall of an
  # established host. Pausing/arming dispatch is an explicit operator go-live
  # action, never a provisioning side effect (runbook: scripts/setup/systemd/README.md).
  ok "installed (NOT enabled) opt-in units: ${OPTIONAL_UNITS[*]}"
  printf '%b\n' "  ${C_DIM}flue-executor.service = Multica queue consumer (executor migration Phase 2).${C_NC}"
  printf '%b\n' "  ${C_DIM}Ships inert: not enabled, ZOE_EXECUTOR_DISPATCH defaults dry, single lane.${C_NC}"
  printf '%b\n' "  ${C_DIM}Go-live is an operator runbook step (register identity, create .env, arm the${C_NC}"
  printf '%b\n' "  ${C_DIM}dispatch pause yourself, enable, verify dry wiring): scripts/setup/systemd/README.md${C_NC}"

  # Let user services keep running after logout on a headless box.
  loginctl enable-linger "$USER" 2>/dev/null || true
else
  warn "--skip-systemd: not installing host-native units"
fi

# ── 7. Health gate ──────────────────────────────────────────────────────────
step "Health check"
health() { # <name> <url>
  if curl -fsS --max-time 5 "$2" >/dev/null 2>&1; then ok "$1 healthy ($2)"; else warn "$1 not responding ($2)"; fi
}
sleep 2
health "zoe-data" "http://localhost:8000/health"
health "zoe-auth" "http://localhost:8002/health"
[[ -x "$LLAMA_BIN" ]] && health "llama-server" "http://localhost:11434/health"

step "Done"
ok "Zoe host install complete."
printf '%b\n' "  UI:        https://localhost"
printf '%b\n' "  API docs:  http://localhost:8000/docs"
printf '%b\n' "  ${C_DIM}Boot order + troubleshooting: docs/guides/OPERATOR_RUNBOOK.md${C_NC}"
printf '%b\n' "  ${C_DIM}Touch panel: scripts/setup/install-pi.sh --host <IP> --user <USER> --server-url https://<THIS_IP>${C_NC}"
