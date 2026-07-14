#!/usr/bin/env bash
# router_rollout.sh — staged rollout driver for the two-stage router
# (SetFit shortlist head + FunctionGemma sidecar, ZOE_ROUTER_HEAD flag).
#
# Run ON the box by the operator (or the main session), from anywhere:
#
#   scripts/maintenance/router_rollout.sh --preflight
#   scripts/maintenance/router_rollout.sh --stage shadow2
#   scripts/maintenance/router_rollout.sh --stage active
#   scripts/maintenance/router_rollout.sh --rollback
#   scripts/maintenance/router_rollout.sh --status
#
# Every stage change: pre-flight -> set ZOE_ROUTER_HEAD in the LIVE
# services/zoe-data/.env -> restart zoe-data -> verify health -> stage-specific
# post-checks. Any failure after the flag was touched triggers an automatic
# rollback of the flag (+ restart) via the ERR/EXIT trap — the flag is never
# left half-set. Fails loudly (non-zero exit + FAIL lines) at every step.
#
# Runbook: docs/knowledge/two-stage-router-rollout.md
# Shadow2 scoring: scripts/maintenance/router_shadow2_report.py
#
# Environment overrides (defaults match the lane-1 contract in
# labs/router-90-campaign/HANDOFF.md + PR #1318; adjust if lane 1 shipped
# different names):
#   ROLLOUT_REPO          live checkout            (default /home/zoe/assistant)
#   ROLLOUT_ENV_FILE      zoe-data env file        (default $REPO/services/zoe-data/.env)
#   ROLLOUT_FLAG          flag name                (default ZOE_ROUTER_HEAD)
#   ROLLOUT_SERVICE       systemd user unit        (default zoe-data.service)
#   ROLLOUT_SIDECAR_UNIT  sidecar user unit        (default functiongemma-sidecar.service)
#   ROLLOUT_API           zoe-data base URL        (default http://127.0.0.1:8000)
#   ROLLOUT_SIDECAR_URL   sidecar base URL         (default http://127.0.0.1:11436)
#   ROLLOUT_BRAIN_URL     brain llama-server       (default http://127.0.0.1:11434)
#   ROLLOUT_EXPECTED_GGUF substring expected in sidecar /props model path
#                                                  (default functiongemma-270m-zoe-functok-r2)
#   ROLLOUT_SHADOW2_LOG   shadow2 JSONL            (default $REPO/services/zoe-data/data/router_head_shadow.jsonl)
#   ROLLOUT_SHADOW_LOG    stage-1 shadow JSONL     (default $REPO/services/zoe-data/data/router_head_shadow.jsonl)
#   ROLLOUT_CMD_UTTERANCE canonical command probe  (default "add rollout probe to my shopping list")
#   ROLLOUT_ACTIVE_MAX_MS sub-second gate for the command probe (default 1000)
#   ROLLOUT_SETTLE_S      wait after POST before checking logs (default 5)
set -euo pipefail

REPO="${ROLLOUT_REPO:-/home/zoe/assistant}"
ENV_FILE="${ROLLOUT_ENV_FILE:-$REPO/services/zoe-data/.env}"
FLAG="${ROLLOUT_FLAG:-ZOE_ROUTER_HEAD}"
SERVICE="${ROLLOUT_SERVICE:-zoe-data.service}"
SIDECAR_UNIT="${ROLLOUT_SIDECAR_UNIT:-functiongemma-sidecar.service}"
API="${ROLLOUT_API:-http://127.0.0.1:8000}"
SIDECAR_URL="${ROLLOUT_SIDECAR_URL:-http://127.0.0.1:11436}"
BRAIN_URL="${ROLLOUT_BRAIN_URL:-http://127.0.0.1:11434}"
EXPECTED_GGUF="${ROLLOUT_EXPECTED_GGUF:-functiongemma-270m-zoe-functok-r2}"
SHADOW2_LOG="${ROLLOUT_SHADOW2_LOG:-$REPO/services/zoe-data/data/router_head_shadow.jsonl}"
SHADOW_LOG="${ROLLOUT_SHADOW_LOG:-$REPO/services/zoe-data/data/router_head_shadow.jsonl}"
CMD_UTTERANCE="${ROLLOUT_CMD_UTTERANCE:-add rollout probe to my shopping list}"
ACTIVE_MAX_MS="${ROLLOUT_ACTIVE_MAX_MS:-1000}"
SETTLE_S="${ROLLOUT_SETTLE_S:-5}"
SESSION_ID="router-rollout-probe"  # stable: no orphaned chat_sessions rows per run

CHAT_UTTERANCES=(
  "good morning zoe how are you today"
  "tell me a fun fact about the ocean"
  "what do you think about space travel"
)

log()  { printf '[rollout] %s\n' "$*"; }
fail() { printf '[rollout] FAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- flag safety
ORIG_FLAG_VALUE=""
FLAG_TOUCHED=0
ROLLBACK_IN_PROGRESS=0

current_flag() {
  # last assignment wins, like dotenv loaders
  grep -E "^${FLAG}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true
}

set_flag() {
  local value="$1"
  [ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"
  if [ "$FLAG_TOUCHED" -eq 0 ]; then
    ORIG_FLAG_VALUE="$(current_flag)"
    FLAG_TOUCHED=1
  fi
  if grep -qE "^${FLAG}=" "$ENV_FILE"; then
    sed -i "s|^${FLAG}=.*|${FLAG}=${value}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$FLAG" "$value" >> "$ENV_FILE"
  fi
  [ "$(current_flag)" = "$value" ] || fail "flag write did not stick in $ENV_FILE"
  log "$FLAG=$value written to $ENV_FILE"
}

on_error() {
  local rc=$?
  [ "$ROLLBACK_IN_PROGRESS" -eq 1 ] && exit "$rc"
  if [ "$FLAG_TOUCHED" -eq 1 ]; then
    ROLLBACK_IN_PROGRESS=1
    echo "[rollout] ERROR (rc=$rc) after flag change — auto-restoring ${FLAG}=${ORIG_FLAG_VALUE:-off}" >&2
    set_flag "${ORIG_FLAG_VALUE:-off}" || true
    systemctl --user restart "$SERVICE" || true
    sleep 3
    curl -fsS --max-time 20 "$API/health" >/dev/null \
      && echo "[rollout] auto-rollback: zoe-data healthy on ${FLAG}=${ORIG_FLAG_VALUE:-off}" >&2 \
      || echo "[rollout] auto-rollback: zoe-data UNHEALTHY — operator attention required" >&2
  fi
  exit "$rc"
}
trap on_error ERR

# ------------------------------------------------------------------- helpers
http_ok() { curl -fsS --max-time "${2:-15}" "$1" >/dev/null 2>&1; }

post_chat() {
  # POST one utterance; echoes "HTTP_CODE MS BODY_HEAD"
  local msg="$1" t0 t1 code body_file ms
  body_file="$(mktemp)"
  t0=$(date +%s%3N)
  code=$(curl -sS -o "$body_file" -w '%{http_code}' --max-time 60 \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"message": sys.argv[1], "session_id": sys.argv[2]}))' "$msg" "$SESSION_ID")" \
    "$API/api/chat/?stream=false" || echo "000")
  t1=$(date +%s%3N)
  ms=$((t1 - t0))
  printf '%s %s %s\n' "$code" "$ms" "$(head -c 200 "$body_file" | tr '\n' ' ')"
  rm -f "$body_file"
}

line_count() { [ -f "$1" ] && wc -l < "$1" || echo 0; }

probe_routes() {
  # Routes for ONE probe, scoped to records written after the probe (skip the
  # pre-probe line count $2) and matched to the probe utterance $3 by the
  # record's sha256 hash ("utt", #1318 privacy convention). ONLY hash-matched
  # records are printed (one route per line): a record without the probe's
  # identity is never attributed to the probe, so unrelated live traffic can
  # never decide a probe. If the log writer doesn't record "utt", this prints
  # nothing and the caller sees route "?" — reconcile the log contract instead.
  python3 - "$1" "$2" "$3" <<'PY'
import hashlib, json, sys
path, skip, text = sys.argv[1], int(sys.argv[2]), sys.argv[3]
utt = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
matched = []
try:
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < skip or not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            route = "?"
            for key in ("final_routed", "routed", "shadow2_routed", "head_routed",
                        "actual_routed"):
                if rec.get(key):
                    route = str(rec[key])
                    break
            if str(rec.get("utt", "")).startswith(utt[:12]):
                matched.append(route)
except OSError:
    pass
for r in matched:
    print(r)
PY
}

probe_route_for() {
  # route for one probe utterance across the shadow2 + stage-1 logs
  local skip2="$1" skip1="$2" msg="$3" routes
  routes="$(probe_routes "$SHADOW2_LOG" "$skip2" "$msg")"
  [ -n "$routes" ] || routes="$(probe_routes "$SHADOW_LOG" "$skip1" "$msg")"
  [ -n "$routes" ] && echo "$routes" | tail -n1 || echo "?"
}

# ----------------------------------------------------------------- pre-flight
preflight() {
  log "pre-flight checks"

  # 1. live checkout on main + synced with origin
  local branch behind
  branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "main" ] || fail "live checkout $REPO is on '$branch', not main"
  git -C "$REPO" fetch origin main --quiet || fail "git fetch failed in $REPO"
  behind="$(git -C "$REPO" rev-list --count HEAD..origin/main)"
  [ "$behind" = "0" ] || fail "live checkout is $behind commit(s) behind origin/main — sync first"
  log "  live checkout: main, synced with origin"

  # 2. sidecar unit installed + active
  if systemctl --user cat "$SIDECAR_UNIT" >/dev/null 2>&1; then
    systemctl --user is-active --quiet "$SIDECAR_UNIT" \
      || fail "sidecar unit $SIDECAR_UNIT installed but not active"
    log "  sidecar unit: $SIDECAR_UNIT active"
  else
    fail "sidecar unit $SIDECAR_UNIT not installed (override ROLLOUT_SIDECAR_UNIT if lane 1 named it differently)"
  fi

  # 3. sidecar answering with the expected GGUF (llama-server /props)
  local props
  props="$(curl -fsS --max-time 15 "$SIDECAR_URL/props" 2>/dev/null)" \
    || fail "sidecar not answering at $SIDECAR_URL/props"
  echo "$props" | grep -q "$EXPECTED_GGUF" \
    || fail "sidecar /props does not mention expected GGUF '$EXPECTED_GGUF' (got: $(echo "$props" | head -c 200))"
  log "  sidecar: healthy, serving $EXPECTED_GGUF"

  # 4. brain healthy
  http_ok "$BRAIN_URL/health" || fail "brain llama-server not healthy at $BRAIN_URL/health"
  log "  brain: healthy ($BRAIN_URL)"

  # 5. zoe-data healthy
  http_ok "$API/health" || fail "zoe-data not healthy at $API/health"
  log "  zoe-data: healthy ($API)"

  [ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"
  log "  env file: $ENV_FILE (current $FLAG=$(current_flag || echo '<unset>'))"
  log "pre-flight OK"
}

restart_and_verify() {
  log "restarting $SERVICE"
  systemctl --user restart "$SERVICE"
  local i
  for i in $(seq 1 30); do
    if http_ok "$API/health" 5; then
      log "zoe-data healthy after restart (${i}s)"
      return 0
    fi
    sleep 1
  done
  fail "zoe-data did not come back healthy within 30s of restart"
}

# -------------------------------------------------------------------- stages
stage_shadow2() {
  preflight
  set_flag "shadow2"
  restart_and_verify

  local before after out code ms
  before="$(line_count "$SHADOW2_LOG")"
  log "shadow2 log baseline: $before line(s) at $SHADOW2_LOG"

  local u
  for u in "${CHAT_UTTERANCES[@]}"; do
    out="$(post_chat "$u")"
    code="${out%% *}"; ms="$(echo "$out" | awk '{print $2}')"
    [ "$code" = "200" ] || fail "synthetic POST failed (HTTP $code): $u"
    log "  POST ok (${ms}ms): $u"
  done

  sleep "$SETTLE_S"
  after="$(line_count "$SHADOW2_LOG")"
  if [ "$after" -gt "$before" ]; then
    log "shadow2 log grew: $before -> $after lines"
  elif journalctl --user -u "$SERVICE" --since "2 min ago" 2>/dev/null | grep -qi "shadow2"; then
    log "shadow2 lines visible in journal (JSONL at $SHADOW2_LOG not growing — check ROLLOUT_SHADOW2_LOG path)"
  else
    fail "no shadow2 log lines after 3 synthetic utterances — is the flag wired? (checked $SHADOW2_LOG + journal)"
  fi

  FLAG_TOUCHED=0  # success: flag stays
  log "STAGE shadow2 COMPLETE — score it with: python3 scripts/maintenance/router_shadow2_report.py"
}

stage_active() {
  preflight
  set_flag "active"
  restart_and_verify

  # post-deploy check 1: canonical command routes to a tool, sub-second
  local out code ms body skip2 skip1 route
  skip2="$(line_count "$SHADOW2_LOG")"; skip1="$(line_count "$SHADOW_LOG")"
  out="$(post_chat "$CMD_UTTERANCE")"
  code="${out%% *}"; ms="$(echo "$out" | awk '{print $2}')"; body="${out#* * }"
  [ "$code" = "200" ] || fail "command probe failed (HTTP $code): $CMD_UTTERANCE"
  echo "$body" | grep -q '"response"' || fail "command probe: no response field in body: $body"
  [ "$ms" -le "$ACTIVE_MAX_MS" ] \
    || fail "command probe took ${ms}ms > ${ACTIVE_MAX_MS}ms — active router is not on the fast path"
  sleep "$SETTLE_S"
  route="$(probe_route_for "$skip2" "$skip1" "$CMD_UTTERANCE")"
  if [ "$route" = "chat" ] || [ "$route" = "?" ]; then
    fail "command probe route='$route' per router log — active router did not take the tool route ('?' = no record carrying the probe's utt hash; check the log path/record contract)"
  fi
  log "  command probe: HTTP 200 in ${ms}ms, route=$route — OK"

  # post-deploy check 2: chat utterances must NOT tool-call
  local u
  for u in "${CHAT_UTTERANCES[@]}"; do
    skip2="$(line_count "$SHADOW2_LOG")"; skip1="$(line_count "$SHADOW_LOG")"
    out="$(post_chat "$u")"
    code="${out%% *}"; ms="$(echo "$out" | awk '{print $2}')"
    [ "$code" = "200" ] || fail "chat probe failed (HTTP $code): $u"
    sleep "$SETTLE_S"
    route="$(probe_route_for "$skip2" "$skip1" "$u")"
    if [ "$route" != "chat" ] && [ "$route" != "?" ]; then
      fail "chat-FP: '$u' routed to '$route' under active — roll back and investigate"
    fi
    [ "$route" = "?" ] && log "  WARNING: no log record matched this probe's utt hash — chat probe verified by response only"
    log "  chat probe ok (${ms}ms, route=$route): $u"
  done

  FLAG_TOUCHED=0  # success: flag stays
  log "ACTIVE ROUTER PROBES COMPLETE — the stage is NOT done yet: run the mandatory voice replay gate before calling active done (see runbook)"
}

rollback() {
  log "rolling back: $FLAG=off"
  set_flag "off"
  restart_and_verify
  FLAG_TOUCHED=0
  log "ROLLBACK COMPLETE — $FLAG=off, zoe-data healthy"
}

status() {
  echo "flag:        $FLAG=$(current_flag || echo '<unset>')  ($ENV_FILE)"
  echo "zoe-data:    $(http_ok "$API/health" && echo healthy || echo UNHEALTHY)  ($API)"
  echo "brain:       $(http_ok "$BRAIN_URL/health" && echo healthy || echo UNHEALTHY)  ($BRAIN_URL)"
  echo "sidecar:     $(http_ok "$SIDECAR_URL/props" && echo healthy || echo UNHEALTHY)  ($SIDECAR_URL, unit $SIDECAR_UNIT)"
  echo "shadow2 log: $(line_count "$SHADOW2_LOG") line(s)  ($SHADOW2_LOG)"
  echo "shadow log:  $(line_count "$SHADOW_LOG") line(s)  ($SHADOW_LOG)"
}

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

case "${1:-}" in
  --preflight) preflight ;;
  --stage)
    case "${2:-}" in
      shadow2) stage_shadow2 ;;
      active)  stage_active ;;
      *) echo "unknown stage '${2:-}' (shadow2|active)" >&2; usage ;;
    esac ;;
  --rollback) rollback ;;
  --status)   status ;;
  *) usage ;;
esac
