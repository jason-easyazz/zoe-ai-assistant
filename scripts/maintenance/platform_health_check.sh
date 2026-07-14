#!/usr/bin/env bash
# Production checks used by the Multica Platform Health autopilot.

set -uo pipefail

failed=0

fail() {
    printf 'FAIL: %s\n' "$*"
    failed=1
}

pass() {
    printf 'PASS: %s\n' "$*"
}

warn() {
    printf 'WARN: %s\n' "$*"
}

check_tts_not_degraded() {
    # The Kokoro sidecar answers /health with device + a `degraded` flag it sets when
    # it fell back from CUDA to CPU. CPU synthesis is slower than real time, so replies
    # play back choppy — a real quality regression that root /health (pool liveness)
    # can't see. Surface it here (WARN, non-fatal): choppy-but-working voice is not a
    # platform outage and must not fail the gate the deploy/autopilot path keys on, but
    # it must be visible so a silent CUDA fallback doesn't hide.
    local url="${1:-http://127.0.0.1:10201/health}"
    local body
    body="$(curl --silent --show-error --fail --max-time 5 "$url" 2>&1)" || {
        warn "Kokoro TTS sidecar unreachable at $url (voice may be on a fallback engine): $body"
        return
    }
    if printf '%s' "$body" | python3 -c \
        'import json,sys; v=json.load(sys.stdin); raise SystemExit(1 if v.get("degraded") else 0)'; then
        pass "Kokoro TTS on accelerated path (not degraded)"
    else
        warn "Kokoro TTS is DEGRADED (CPU fallback → choppy replies): $body — free ~2.3GB and restart kokoro-tts.service"
    fi
}

check_json_health() {
    local label="$1"
    local url="$2"
    local body
    body="$(curl --silent --show-error --fail --max-time 5 "$url" 2>&1)" || {
        fail "$label is unreachable: $body"
        return
    }
    if printf '%s' "$body" | python3 -c \
        'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if value.get("status") == "ok" or value.get("ok") is True else 1)'; then
        pass "$label reports healthy"
    else
        fail "$label returned an unhealthy payload: $body"
    fi
}

if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon is unreachable"
else
    container_count=0
    while IFS=$'\t' read -r name status health; do
        ((container_count++)) || true
        if [[ "$status" != "running" ]]; then
            fail "Docker container $name is $status"
        elif [[ -n "$health" && "$health" != "healthy" ]]; then
            fail "Docker container $name health is $health"
        fi
    done < <(
        docker ps --format '{{.Names}}' |
            while read -r name; do
                docker inspect --format '{{.Name}}{{"\t"}}{{.State.Status}}{{"\t"}}{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$name" |
                    sed 's#^/##'
            done
    )
    if [[ "$container_count" -eq 0 ]]; then
        fail "Docker returned zero running containers"
    elif [[ "$failed" -eq 0 ]]; then
        pass "required Docker containers are running"
    fi
fi

if docker exec zoe-database pg_isready -U zoe -d zoe >/dev/null 2>&1; then
    pass "PostgreSQL accepts connections"
else
    fail "PostgreSQL is unavailable"
fi

check_json_health "zoe-data" "http://127.0.0.1:8000/health"
check_json_health "Hermes" "http://127.0.0.1:8642/health"
check_json_health "OpenClaw" "http://127.0.0.1:18789/health"

check_tts_not_degraded

exit "$failed"
