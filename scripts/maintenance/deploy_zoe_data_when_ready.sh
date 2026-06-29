#!/usr/bin/env bash
# Gatekeeper wrapper for the blessed zoe-data live deploy.
#
# This script checks whether the live tree is safe to deploy, then delegates all
# pull/restart/rollback behavior to deploy_live.sh. It never repairs the live
# checkout itself.
set -euo pipefail

LIVE="${ZOE_LIVE_TREE:-/home/zoe/assistant}"
MIN_AVAIL_MB="${ZOE_DEPLOY_MIN_AVAIL_MB:-700}"
SERVICE="${ZOE_SERVICE:-zoe-data}"
PORT="${ZOE_PORT:-8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_LIVE="${SCRIPT_DIR}/deploy_live.sh"

MODE="check"
MODE_SET=0
WATCH_INTERVAL=30
MAX_WAIT=""
YES_RESTART=0

log() {
    printf 'deploy-ready: %s\n' "$*"
}

fail_usage() {
    printf 'deploy-ready: ERROR: %s\n\n' "$*" >&2
    usage >&2
    exit 2
}

usage() {
    cat <<'EOF'
Usage: deploy_zoe_data_when_ready.sh [--check | --watch[=SECONDS] | --deploy] [options]

Modes:
  --check
      Read-only gate check. Prints PASS/FAIL for each gate plus READY/NOT-READY.
      This is the default mode and performs no working-tree/live mutation. It does
      fetch origin/main, which updates remote-tracking refs.

  --watch[=SECONDS]
  --watch SECONDS
      Re-run --check until READY. Defaults to 30 seconds between checks.
      Optional: --max-wait SECONDS. This mode never deploys.

  --deploy
      Run the same gates once. If READY, refuses unless
      --yes-restart-production is also supplied. When confirmed, execs the
      blessed scripts/maintenance/deploy_live.sh, then runs post-deploy checks.

Required deploy confirmation:
  --yes-restart-production
      Required with --deploy because restarting zoe-data is a production action.

Environment:
  ZOE_LIVE_TREE=/home/zoe/assistant
  ZOE_DEPLOY_MIN_AVAIL_MB=700
  ZOE_SERVICE=zoe-data
  ZOE_PORT=8000
EOF
}

set_mode() {
    local requested="$1"
    if [[ "$MODE_SET" -eq 1 ]]; then
        if [[ "$MODE" != "$requested" ]]; then
            fail_usage "modes are mutually exclusive"
        fi
    fi
    MODE="$requested"
    MODE_SET=1
}

parse_positive_int() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        fail_usage "$name must be a non-negative integer"
    fi
    printf '%s' "$value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            set_mode "check"
            shift
            ;;
        --watch)
            set_mode "watch"
            if [[ $# -gt 1 && "$2" =~ ^[0-9]+$ ]]; then
                WATCH_INTERVAL="$(parse_positive_int "--watch" "$2")"
                shift 2
            else
                shift
            fi
            ;;
        --watch=*)
            set_mode "watch"
            WATCH_INTERVAL="$(parse_positive_int "--watch" "${1#--watch=}")"
            shift
            ;;
        --max-wait)
            [[ $# -gt 1 ]] || fail_usage "--max-wait requires SECONDS"
            MAX_WAIT="$(parse_positive_int "--max-wait" "$2")"
            shift 2
            ;;
        --max-wait=*)
            MAX_WAIT="$(parse_positive_int "--max-wait" "${1#--max-wait=}")"
            shift
            ;;
        --deploy)
            set_mode "deploy"
            shift
            ;;
        --yes-restart-production)
            YES_RESTART=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail_usage "unknown argument: $1"
            ;;
    esac
done

if [[ "$MODE" != "deploy" && "$YES_RESTART" -eq 1 ]]; then
    fail_usage "--yes-restart-production is only valid with --deploy"
fi
if [[ "$MODE" != "watch" && -n "$MAX_WAIT" ]]; then
    fail_usage "--max-wait is only valid with --watch"
fi

gate_pass() {
    printf 'deploy-ready: PASS %-18s %s\n' "$1" "$2"
}

gate_fail() {
    printf 'deploy-ready: FAIL %-18s %s\n' "$1" "$2"
}

mem_available_mb() {
    if [[ -r /proc/meminfo ]]; then
        awk '/^MemAvailable:/ {print int($2 / 1024); found=1; exit} END {if (!found) exit 1}' /proc/meminfo 2>/dev/null && return 0
    fi
    if command -v free >/dev/null 2>&1; then
        free -m | awk '/^Mem:/ {print $7; found=1; exit} END {if (!found) exit 1}' 2>/dev/null && return 0
    fi
    printf '0'
}

check_gates() {
    local failed=0
    local branch=""
    local status=""
    local avail=0
    local head=""
    local upstream=""
    local left=0
    local right=0
    local counts=""

    log "checking live tree: ${LIVE}"
    log "service=${SERVICE} port=${PORT} min_avail_mb=${MIN_AVAIL_MB}"

    if git -C "$LIVE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        gate_pass "git-repo" "$LIVE is a git worktree"
    else
        gate_fail "git-repo" "$LIVE is not a git worktree"
        failed=1
    fi

    if [[ "$failed" -eq 0 ]]; then
        branch="$(git -C "$LIVE" branch --show-current 2>/dev/null || true)"
        if [[ "$branch" == "main" ]]; then
            gate_pass "branch" "on main"
        else
            gate_fail "branch" "on '${branch:-detached}', expected main"
            failed=1
        fi

        git -C "$LIVE" update-index -q --refresh 2>/dev/null || true
        status="$(git -C "$LIVE" status --porcelain 2>/dev/null || true)"
        if [[ -z "$status" ]]; then
            gate_pass "clean-tree" "working tree clean"
        else
            gate_fail "clean-tree" "working tree has uncommitted changes"
            printf '%s\n' "$status" | sed 's/^/deploy-ready:   /'
            failed=1
        fi

        if git -C "$LIVE" fetch origin main >/dev/null 2>&1; then
            head="$(git -C "$LIVE" rev-parse HEAD 2>/dev/null || true)"
            upstream="$(git -C "$LIVE" rev-parse refs/remotes/origin/main 2>/dev/null || true)"
            if [[ -n "$head" && -n "$upstream" ]]; then
                counts="$(git -C "$LIVE" rev-list --left-right --count HEAD...refs/remotes/origin/main 2>/dev/null || true)"
                if [[ "$counts" =~ ^[0-9]+[[:space:]]+[0-9]+$ ]]; then
                    read -r left right <<<"$counts"
                else
                    gate_fail "origin-main" "could not compare HEAD to origin/main"
                    failed=1
                    left=0
                    right=0
                fi
            fi

            if [[ "$failed" -eq 0 && -n "$head" && -n "$upstream" ]]; then
                if [[ "$left" -eq 0 && "$right" -eq 0 ]]; then
                    gate_pass "origin-main" "main is up-to-date with origin/main"
                elif [[ "$left" -eq 0 && "$right" -gt 0 ]]; then
                    gate_pass "origin-main" "main is behind origin/main by ${right} commit(s); deploy_live.sh will ff-pull"
                elif [[ "$left" -gt 0 && "$right" -eq 0 ]]; then
                    gate_fail "origin-main" "main is ahead of origin/main by ${left} commit(s)"
                    failed=1
                else
                    gate_fail "origin-main" "main has diverged from origin/main (${left} ahead, ${right} behind)"
                    failed=1
                fi
            else
                if [[ "$failed" -eq 0 ]]; then
                    gate_fail "origin-main" "could not resolve HEAD or refs/remotes/origin/main after fetch (HEAD ${head:-unknown})"
                    failed=1
                fi
            fi
        else
            gate_fail "origin-main" "git fetch origin main failed"
            failed=1
        fi
    fi

    avail="$(mem_available_mb)"
    if [[ "$avail" =~ ^[0-9]+$ && "$avail" -gt "$MIN_AVAIL_MB" ]]; then
        gate_pass "memory" "MemAvailable=${avail}MB > ${MIN_AVAIL_MB}MB"
    else
        gate_fail "memory" "MemAvailable=${avail}MB <= ${MIN_AVAIL_MB}MB"
        failed=1
    fi

    if [[ "$failed" -eq 0 ]]; then
        log "READY"
        return 0
    fi
    log "NOT-READY"
    return 1
}

main_schedules_moonshine_startup() {
    # This verify intentionally assumes the current startup warmup idiom:
    # scheduling the warm task via asyncio.create_task/ensure_future.
    grep -Eq "(create_task|ensure_future)[(][^)]*warm_moonshine"
}

main_schedules_whisper_startup() {
    grep -Eq "(create_task|ensure_future)[(][^)]*(warm_whisper|warm_faster_whisper|load_whisper|load_faster_whisper|faster_whisper|faster-whisper)"
}

preflight_target_content_verify() {
    local target_ref="refs/remotes/origin/main"
    local voice_path="services/zoe-data/routers/voice_tts.py"
    local main_path="services/zoe-data/main.py"
    local voice_content=""
    local main_content=""
    local failed=0

    log "pre-flight target content verify: ${target_ref}"
    voice_content="$(git -C "$LIVE" show "${target_ref}:${voice_path}" 2>/dev/null || true)"
    main_content="$(git -C "$LIVE" show "${target_ref}:${main_path}" 2>/dev/null || true)"

    if [[ -n "$voice_content" && "$voice_content" == *"_prewarm_stt_on_wake"* ]]; then
        gate_pass "target-voice" "${voice_path} contains _prewarm_stt_on_wake"
    else
        gate_fail "target-voice" "${target_ref}:${voice_path} missing _prewarm_stt_on_wake"
        failed=1
    fi

    if [[ -n "$main_content" ]] && main_schedules_moonshine_startup <<<"$main_content"; then
        gate_pass "target-main" "${main_path} schedules Moonshine startup warmup"
    else
        gate_fail "target-main" "${target_ref}:${main_path} does not schedule warm_moonshine startup warmup"
        failed=1
    fi

    if [[ -n "$main_content" ]] && main_schedules_whisper_startup <<<"$main_content"; then
        gate_fail "target-main-whisper" "${target_ref}:${main_path} appears to schedule a whisper startup warmup"
        failed=1
    else
        gate_pass "target-main-whisper" "no whisper startup warmup scheduling detected in target commit"
    fi

    if [[ "$failed" -eq 0 ]]; then
        log "PRE-FLIGHT TARGET CONTENT PASS"
        return 0
    fi
    log "PRE-FLIGHT TARGET CONTENT FAIL"
    return 1
}

post_deploy_info() {
    local code=""
    local live_sha=""

    live_sha="$(git -C "$LIVE" rev-parse --short HEAD 2>/dev/null || true)"
    log "post-deploy informational check: live_sha=${live_sha:-unknown}"

    code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:${PORT}/health" || true)"
    if [[ "$code" == "200" ]]; then
        gate_pass "post-health" "http://127.0.0.1:${PORT}/health returned 200"
    else
        log "WARNING: post-deploy informational health returned ${code:-no response}; deploy_live.sh reported success and owns rollback. The service may still be LIVE at ${live_sha:-unknown}; this wrapper does not roll back after deploy_live succeeds."
    fi
    return 0
}

run_watch() {
    local start now elapsed
    start="$(date +%s)"
    while true; do
        log "watch cycle $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        if check_gates; then
            return 0
        fi
        if [[ -n "$MAX_WAIT" ]]; then
            now="$(date +%s)"
            elapsed=$((now - start))
            if [[ "$elapsed" -ge "$MAX_WAIT" ]]; then
                log "watch max-wait reached (${MAX_WAIT}s)"
                return 1
            fi
        fi
        sleep "$WATCH_INTERVAL"
    done
}

case "$MODE" in
    check)
        check_gates
        ;;
    watch)
        run_watch
        ;;
    deploy)
        if ! check_gates; then
            log "deploy aborted: gates are NOT-READY"
            exit 1
        fi
        if [[ "$YES_RESTART" -ne 1 ]]; then
            log "REFUSING: --deploy would restart production ${SERVICE}; rerun with --yes-restart-production to confirm this production action."
            exit 1
        fi
        if ! preflight_target_content_verify; then
            log "deploy aborted: target commit content is NOT-READY"
            exit 1
        fi
        if [[ ! -x "$DEPLOY_LIVE" ]]; then
            log "ERROR: blessed deploy script is not executable: $DEPLOY_LIVE" >&2
            exit 1
        fi
        log "running blessed deploy: $DEPLOY_LIVE"
        "$DEPLOY_LIVE"
        post_deploy_info
        ;;
esac
