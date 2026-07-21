#!/usr/bin/env bash
# zoe_ground_truth.sh — print LIVE runtime reality, fast and read-only.
#
# WHY THIS EXISTS. codebase-memory / Serena / grep answer "what EXISTS" (a
# function is defined, here's who calls it). They cannot answer "what RUNS" —
# whether a code path executes, which flag is set in the running process,
# whether a scheduled job actually registered, whether a "paused" service got
# bounced for a test since the docs were written. Every wrong conclusion in the
# 2026-07-20 session lived in that gap: a paused service silently restarted, a
# board assumed to live "inside Hermes" that is its own product, a flag read
# from its default instead of the running env, a job that never registered.
#
# A static document CANNOT track that — only reading the live process can. So
# read this BEFORE trusting any doc's claim about what is live. It is the
# "what runs" companion to the "what exists" tools.
#
# Strictly READ-ONLY: no restarts, no writes, no deletes. Each check is
# independent and non-fatal — a failure prints a marker and moves on, so one
# broken probe never blanks the rest. Safe to run anytime, including on the
# memory-tight box (no heavy operations).
set -uo pipefail

C_OK=$'\033[92m'; C_BAD=$'\033[91m'; C_DIM=$'\033[90m'; C_HDR=$'\033[96m'; C_0=$'\033[0m'
[ -t 1 ] || { C_OK=; C_BAD=; C_DIM=; C_HDR=; C_0=; }
hdr() { printf '\n%s── %s ──%s\n' "$C_HDR" "$1" "$C_0"; }
ok()  { printf '  %s✓%s %s\n' "$C_OK" "$C_0" "$1"; }
bad() { printf '  %s✗ %s%s\n' "$C_BAD" "$1" "$C_0"; }
dim() { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_0"; }

PSQL() { docker exec zoe-database psql -U zoe -d "${1:-zoe}" -tAc "$2" 2>/dev/null; }

printf '%sZOE GROUND TRUTH%s  %s  (read-only)\n' "$C_HDR" "$C_0" "$(uptime -p 2>/dev/null || true)"

# ── Host-native services + real health (is-active LIES; poll /health) ────────
hdr "SERVICES (health-checked, not is-active)"
declare -A HEALTH=( [zoe-data]=8000 [llama-server]=11434 [kokoro-tts]=10201
                    [functiongemma-router]=11436 [flue-zoe-brain]=3578 )
for unit in zoe-data llama-server kokoro-tts functiongemma-router flue-zoe-brain \
            flue-zoe-telegram hermes-agent openclaw-gateway serena-mcp github-runner; do
  active=$(systemctl --user is-active "$unit.service" 2>/dev/null)
  port=${HEALTH[$unit]:-}
  if [ -n "$port" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 4 "http://127.0.0.1:$port/health" 2>/dev/null)
    [ "$code" = "200" ] && ok "$unit  active + /health 200 (:$port)" \
                        || bad "$unit  is-active=$active but /health=$code (:$port)"
  else
    [ "$active" = "active" ] && ok "$unit  active" || dim "$unit  $active"
  fi
done

# ── Which brain actually answers — from the RUNNING process env ──────────────
hdr "BRAIN LANE (from the running process env, not code defaults)"
zpid=$(ss -tlnp 2>/dev/null | awk '/:8000 /{print $NF}' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
if [ -n "${zpid:-}" ] && [ -r "/proc/$zpid/environ" ]; then
  env_of() { tr '\0' '\n' < "/proc/$zpid/environ" 2>/dev/null | grep "^$1=" | cut -d= -f2-; }
  be=$(env_of ZOE_BRAIN_BACKEND); dim "zoe-data pid=$zpid"
  ok "ZOE_BRAIN_BACKEND=${be:-<unset→'core' default>}"
  for f in ZOE_ROUTER_HEAD ZOE_EXPERT_MODE ZOE_FLUE_STREAM_ENABLED ZOE_MULTICA \
           ZOE_INTENT_DISPATCH_REQUIRE_TOKEN ZOE_SEAM_RECALL_INJECT ZOE_ROUTER_SELFTRAIN; do
    v=$(env_of "$f"); dim "$f=${v:-<unset>}"
  done
else
  bad "cannot read zoe-data process env (pid on :8000 not found or /proc unreadable)"
fi

# ── Scheduled jobs that ACTUALLY registered (the music_discovery class) ──────
hdr "SCHEDULED JOBS (registered in apscheduler_jobs — not just coded)"
jobs=$(PSQL zoe "SELECT id || '  next=' || to_timestamp(next_run_time)::timestamp(0) FROM apscheduler_jobs ORDER BY id;")
if [ -n "$jobs" ]; then printf '%s\n' "$jobs" | while IFS= read -r j; do ok "$j"; done
else dim "(no rows — jobstore empty or unreadable)"; fi
dim "reminder: a coded add_job that raised (e.g. unpicklable closure) is ABSENT here, silently"

# ── Multica: its OWN product on Zoe, not a Hermes component ──────────────────
hdr "MULTICA (own containers + own DB — verify before reasoning about it)"
docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -i multica | while read -r l; do ok "$l"; done
mcount=$(PSQL multica "SELECT count(*) FROM issue;")
[ -n "$mcount" ] && ok "multica DB reachable — issue rows: $mcount" || dim "multica DB not reachable"
if [ -e "$HOME/.zoe/multica_dispatch_paused" ]; then
  bad "dispatch PAUSED (kill switch present: ~/.zoe/multica_dispatch_paused)"
else ok "dispatch armed (no kill switch)"; fi

# ── Hermes: was paused; something may have restarted it for a test ───────────
hdr "HERMES (doc may say paused — check the live process)"
ha=$(systemctl --user is-active hermes-agent.service 2>/dev/null)
if [ "$ha" = "active" ]; then
  ls=$(ss -tln 2>/dev/null | grep -c ':8642 ')
  bad "hermes-agent ACTIVE (listening=$ls on :8642) — if a doc says 'paused', the doc is stale"
else dim "hermes-agent $ha"; fi

# ── Load-bearing tables: row count + freshness (empty+documented-live=suspect)
hdr "KEY TABLES (row count / freshness)"
freshness() { # table  timestamp-col
  local n mx
  n=$(PSQL zoe "SELECT count(*) FROM $1;")
  mx=$(PSQL zoe "SELECT max($2)::timestamp(0) FROM $1;" 2>/dev/null)
  [ -n "$n" ] && dim "$1: $n rows${mx:+, newest $mx}" || dim "$1: (unreadable)"
}
freshness chat_messages created_at
freshness people created_at
freshness memory_consolidation_state updated_at

# ── Observability + deploy state (the amplifiers that hid bugs) ──────────────
hdr "OBSERVABILITY + DEPLOY"
applog="$HOME/.zoe-logs/zoe-data.app.log"
if [ -s "$applog" ]; then
  recs=$(tail -500 "$applog" 2>/dev/null | grep -cE ' (INFO|WARNING|ERROR) ')
  tb=$(tail -500 "$applog" 2>/dev/null | grep -cE 'Traceback|CRITICAL')
  ok "app log live ($recs app records in last 500 lines, $tb tracebacks)"
else bad "app log EMPTY/ABSENT — app-level logging may be broken again (pre-#1468 blackout signature)"; fi
avail=$(free -m 2>/dev/null | awk '/Mem:/{print $7}')
[ -n "$avail" ] && dim "available memory: ${avail}Mi  (voice replay gate needs ≥2000)"
if [ -d "$HOME/assistant/.git" ]; then
  br=$(git -C "$HOME/assistant" rev-parse --abbrev-ref HEAD 2>/dev/null)
  behind=$(git -C "$HOME/assistant" rev-list --count HEAD..origin/main 2>/dev/null)
  [ "${behind:-0}" = "0" ] && ok "live checkout: $br, 0 behind origin/main" \
                           || bad "live checkout: $br, $behind behind origin/main — merged fixes NOT yet deployed"
fi
gate="$HOME/.cache/zoe/voice_regression_last.json"
[ -f "$gate" ] && dim "voice gate last: $(python3 -c "import json;d=json.load(open('$gate'));print(d.get('status'),'-',d.get('reason'))" 2>/dev/null || echo unreadable)"

printf '\n%sread this BEFORE trusting a doc about what is live. what exists ≠ what runs.%s\n' "$C_DIM" "$C_0"
