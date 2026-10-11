#!/bin/bash
# Brain-stop window for the MTP draft-vocab-trim A/B.  Run as:
#   flock /tmp/zoe-voice-harness.lock bash scripts/maintenance/mtp_vocab_trim_window.sh OUTDIR
# Needs (env, all absolute): PATCHED_BIN  patched llama-server;  PATCHED_LIBS  dir with the patched
# libllama*.so + the STOCK libggml*.so symlinked in;  D2T_16K / D2T_32K / D2T_PREFIX  sliced draft GGUFs
# (scripts/maintenance/mtp_draft_vocab_trim.py build).  See docs/knowledge/mtp-draft-vocab-trim-2026-10-10.md.
# Pre-flight REFUSES to stop the brain unless the box can hold a second E4B once the brain's memory is
# released (measured 2026-10-10: stopping it returns ~3.9 GB; two earlier windows died with CUDA OOM at load
# because other tenants held the rest).  Max ~18 min; the brain is restarted on ANY exit.
set -u
if [ "${ZOE_PERF:-}" != "1" ]; then
  echo "refusing: this window stops the live llama-server; set ZOE_PERF=1 to opt in (and hold the harness lock)" >&2
  exit 2
fi
OUT=${1:?outdir}
: "${PATCHED_BIN:?}" "${PATCHED_LIBS:?}" "${D2T_16K:?}" "${D2T_32K:?}" "${D2T_PREFIX:?}"
STOCK=$HOME/llama.cpp-b11194/build-jetson/bin
FULL=$HOME/models/gemma4-e4b-qat/mtp-gemma-4-E4B-it.gguf
HERE=$(cd "$(dirname "$0")/../perf" && pwd)
mkdir -p "$OUT"
avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
if [ "$avail_kb" -lt 3300000 ]; then
  echo "ABORT (brain NOT stopped): MemAvailable ${avail_kb} kB < 3.3 GB; retry when other benches/Kokoro loads are done" | tee -a "$OUT/window.log"
  exit 3
fi
restore() {
  echo "RESTORE start $(date -Is)" | tee -a "$OUT/window.log"
  pkill -f "llama-server .*--port 11435" 2>/dev/null
  sleep 2
  # A cold start can fail with NvMap error 12 / cudaMalloc OOM while the page cache is full (three times on
  # 2026-10-10): drop the cache (passwordless sudo if present, else skipped) and retry up to 4 starts.
  for attempt in 1 2 3 4; do
    sudo -n sh -c 'sync; echo 1 > /proc/sys/vm/drop_caches' 2>/dev/null || true
    systemctl --user reset-failed llama-server 2>/dev/null
    systemctl --user start llama-server
    for i in $(seq 1 60); do
      if curl -sf -m 3 http://127.0.0.1:11434/health >/dev/null; then echo "BRAIN_HEALTHY $(date -Is) start ${attempt} after ${i} polls" | tee -a "$OUT/window.log"; break 2; fi
      sleep 2
    done
    echo "BRAIN_START_RETRY ${attempt} $(date -Is)" | tee -a "$OUT/window.log"
  done
  systemctl --user stop zoe-mtp-trim-brain-restart.timer 2>/dev/null
  echo "zoe-data /health: $(curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8000/health)" | tee -a "$OUT/window.log"
}
trap restore EXIT INT TERM HUP
# Dead-man switch: restart the brain even if this shell is SIGKILLed (the bench is capped at 1000 s).
systemd-run --user --quiet --unit=zoe-mtp-trim-brain-restart --on-active=1300s systemctl --user start llama-server || true
echo "MemAvailable before: $avail_kb kB" | tee -a "$OUT/window.log"
echo "BRAIN_STOP $(date -Is)" | tee -a "$OUT/window.log"
systemctl --user stop llama-server
sleep 4
echo "MemAvailable after stop: $(awk '/MemAvailable/ {print $2}' /proc/meminfo) kB" | tee -a "$OUT/window.log"
timeout 1000 python3 "$HERE/mtp_vocab_trim_ab.py" --slim --out "$OUT/bench.json" \
  --arm "stockA1=$STOCK/llama-server|$STOCK|$FULL" \
  --arm "d2t16k=$PATCHED_BIN|$PATCHED_LIBS|$D2T_16K" \
  --arm "patchedFull=$PATCHED_BIN|$PATCHED_LIBS|$FULL" \
  --arm "d2t32k=$PATCHED_BIN|$PATCHED_LIBS|$D2T_32K" \
  --arm "stockA2=$STOCK/llama-server|$STOCK|$FULL" \
  --arm "NCprefix16k=$PATCHED_BIN|$PATCHED_LIBS|$D2T_PREFIX" \
  2>&1 | tee -a "$OUT/bench.log"
echo "BENCH_END $(date -Is)" | tee -a "$OUT/window.log"
