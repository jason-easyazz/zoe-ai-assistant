#!/usr/bin/env bash
# =============================================================================
# soak-test.sh — Overnight soak test for Zoe Pi 5
# Sends 1 request/min for 8 hours. Reports latency drift, memory, disk.
# =============================================================================
set -uo pipefail

ZOE_URL="${ZOE_URL:-http://127.0.0.1:8000}"
ZOE_TOKEN="${ZOE_TOKEN:-}"
ZOE_USER="${ZOE_USER:-}"
ZOE_PASS="${ZOE_PASS:-}"
DURATION_HOURS="${SOAK_HOURS:-8}"
INTERVAL_S="${SOAK_INTERVAL:-60}"
LOG_FILE="${SOAK_LOG:-$HOME/soak-test-$(date +%Y%m%d-%H%M).log}"

# Auto-fetch auth token if not provided
if [[ -z "$ZOE_TOKEN" ]]; then
    if [[ -z "$ZOE_USER" || -z "$ZOE_PASS" ]]; then
        echo "ERROR: Set ZOE_TOKEN, or set ZOE_USER + ZOE_PASS to auto-login."
        echo "  Example: ZOE_USER=Jason ZOE_PASS=your_password bash soak-test.sh"
        exit 1
    fi
    echo "Fetching auth token for $ZOE_USER..."
    LOGIN_RESP=$(curl -sf -X POST "$ZOE_URL/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$ZOE_USER\",\"password\":\"$ZOE_PASS\"}" 2>/dev/null)
    ZOE_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
    if [[ -z "$ZOE_TOKEN" ]]; then
        echo "ERROR: Login failed. Check ZOE_USER/ZOE_PASS and that $ZOE_URL is reachable."
        echo "Response: $LOGIN_RESP"
        exit 1
    fi
    echo "Token obtained."
fi

DURATION_S=$((DURATION_HOURS * 3600))
TOTAL_REQUESTS=$((DURATION_S / INTERVAL_S))

echo "Soak test: $TOTAL_REQUESTS requests over ${DURATION_HOURS}h, logging to $LOG_FILE"
echo "Start: $(date)" | tee -a "$LOG_FILE"
echo "Press Ctrl+C to stop early." | tee -a "$LOG_FILE"

PASS=0; FAIL=0; START_EPOCH=$(date +%s)

# Test messages (rotate through)
MESSAGES=(
    "hi"
    "what time is it"
    "add milk to shopping list"
    "what's the weather like today"
    "tell me a quick fun fact"
)

i=0
while true; do
    ELAPSED_S=$(( $(date +%s) - START_EPOCH ))
    [[ $ELAPSED_S -ge $DURATION_S ]] && break

    MSG="${MESSAGES[$((i % ${#MESSAGES[@]}))]}"
    i=$((i+1))

    REQUEST_START=$(date +%s%3N)
    RESP=$(curl -sf -X POST "$ZOE_URL/api/chat/" \
        -H "Authorization: Bearer $ZOE_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"$MSG\",\"session_id\":\"soak-test\"}" \
        --max-time 30 2>/dev/null || echo "")
    REQUEST_END=$(date +%s%3N)
    LATENCY=$((REQUEST_END - REQUEST_START))

    FREE_MEM=$(free -m | awk '/^Mem:/{print $7}')
    DISK_AVAIL=$(df -m "$HOME" | awk 'NR==2{print $4}')

    if [[ -n "$RESP" ]]; then
        PASS=$((PASS+1))
        STATUS="OK"
    else
        FAIL=$((FAIL+1))
        STATUS="FAIL"
    fi

    TS=$(date '+%Y-%m-%d %H:%M:%S')
    LOG_LINE="$TS  status=$STATUS  latency=${LATENCY}ms  free_mem=${FREE_MEM}MB  disk=${DISK_AVAIL}MB  msg=\"${MSG}\""
    echo "$LOG_LINE" | tee -a "$LOG_FILE"

    # Alert if latency is degrading badly
    if [[ $LATENCY -gt 20000 ]]; then
        echo "  [WARN] Latency spike: ${LATENCY}ms" | tee -a "$LOG_FILE"
    fi
    # Alert if free memory is critically low
    if [[ $FREE_MEM -lt 200 ]]; then
        echo "  [WARN] Low memory: ${FREE_MEM}MB" | tee -a "$LOG_FILE"
    fi

    sleep "$INTERVAL_S"
done

echo "" | tee -a "$LOG_FILE"
echo "Soak test complete: $PASS OK, $FAIL failed" | tee -a "$LOG_FILE"
echo "End: $(date)" | tee -a "$LOG_FILE"

if [[ $FAIL -gt 0 ]]; then
    echo "FAILURES detected. Check log: $LOG_FILE"
    exit 1
fi
