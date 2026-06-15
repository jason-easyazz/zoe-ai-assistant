#!/usr/bin/env bash
set -euo pipefail

LLAMA_REPO="${LLAMA_REPO:-/home/zoe/llama.cpp}"
TRIAL_SOURCE="${TRIAL_SOURCE:-/home/zoe/llama.cpp-gemma4-trial}"
TRIAL_BUILD="${TRIAL_BUILD:-$TRIAL_SOURCE/build-jetson}"
CURRENT_BUILD="${CURRENT_BUILD:-$LLAMA_REPO/build-jetson}"
MODEL_DIR="${MODEL_DIR:-/home/zoe/models/gemma4-12b}"
E2B_MODEL="${E2B_MODEL:-/home/zoe/models/gemma4-e2b/gemma-4-E2B-it-Q4_K_M.gguf}"
TRIAL_MODEL="${TRIAL_MODEL:-$MODEL_DIR/gemma-4-12B-it-Q4_K_M.gguf}"
TRIAL_MMPROJ="${TRIAL_MMPROJ:-$MODEL_DIR/mmproj-gemma-4-12B-it-Q8_0.gguf}"
TRIAL_MODEL_FILENAME="${TRIAL_MODEL_FILENAME:-$(basename "$TRIAL_MODEL")}"
TRIAL_MMPROJ_FILENAME="${TRIAL_MMPROJ_FILENAME:-$(basename "$TRIAL_MMPROJ")}"
UNIT_PATH="${UNIT_PATH:-$HOME/.config/systemd/user/llama-server.service}"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/zoe-gemma4-trial}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/backups}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:11434/health}"
HF_REPO="${HF_REPO:-ggml-org/gemma-4-12B-it-GGUF}"
HF_REVISION="${HF_REVISION:-44ee90c4b61e888ac5b318a54ec7a94df61e9cd7}"
MODEL_SHA256="${MODEL_SHA256-1278394b693672ac2799eadc9a83fd98259a6a88a40acfb1dcaa6c6fc895a606}"
MMPROJ_SHA256="${MMPROJ_SHA256-b486d28398a398db4fa14cc4b032252ad3a8d7f950b9fabd93f5c8b4d4dde52b}"
TRIAL_GPU_LAYERS="${TRIAL_GPU_LAYERS:-all}"
TRIAL_FIT_TARGET_MIB="${TRIAL_FIT_TARGET_MIB:-1024}"
TRIAL_SPEC_TYPE="${TRIAL_SPEC_TYPE:-none}"
TRIAL_SPEC_NGRAM_N="${TRIAL_SPEC_NGRAM_N:-12}"
TRIAL_SPEC_NGRAM_M="${TRIAL_SPEC_NGRAM_M:-48}"
TRIAL_SPEC_NGRAM_HITS="${TRIAL_SPEC_NGRAM_HITS:-1}"
TRIAL_CACHE_REUSE="${TRIAL_CACHE_REUSE:-0}"
TRIAL_CACHE_RAM_MIB="${TRIAL_CACHE_RAM_MIB:-0}"
TRIAL_CTX_CHECKPOINTS="${TRIAL_CTX_CHECKPOINTS:-0}"
TRIAL_CHECKPOINT_MIN_STEP="${TRIAL_CHECKPOINT_MIN_STEP:-256}"

mkdir -p "$STATE_DIR"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

wait_for_health() {
    local attempts="${1:-60}"
    local delay="${2:-2}"
    local i
    for ((i = 1; i <= attempts; i++)); do
        if curl -fsS "$HEALTH_URL" >/dev/null; then
            return 0
        fi
        sleep "$delay"
    done
    return 1
}

latest_snapshot() {
    local pointer="$STATE_DIR/latest-snapshot"
    [[ -L "$pointer" ]] || die "no trial snapshot exists"
    readlink -f "$pointer"
}

snapshot() {
    local stamp destination
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    destination="$BACKUP_ROOT/gemma4-trial-$stamp"
    mkdir -p "$destination"

    cp -a "$UNIT_PATH" "$destination/llama-server.service"
    cp -a "$CURRENT_BUILD" "$destination/build-jetson"
    sha256sum \
        "$CURRENT_BUILD/bin/llama-server" \
        "$E2B_MODEL" >"$destination/SHA256SUMS"
    {
        printf 'created_utc=%s\n' "$stamp"
        printf 'llama_commit=%s\n' "$(git -C "$LLAMA_REPO" rev-parse HEAD)"
        printf 'llama_describe=%s\n' "$(git -C "$LLAMA_REPO" describe --always --dirty)"
        printf 'unit_path=%s\n' "$UNIT_PATH"
        printf 'e2b_model=%s\n' "$E2B_MODEL"
    } >"$destination/manifest.env"

    ln -sfn "$destination" "$STATE_DIR/latest-snapshot"
    printf '%s\n' "$destination"
}

build_trial() {
    local revision="${1:-origin/master}"
    git -C "$LLAMA_REPO" fetch origin master
    if [[ -e "$TRIAL_SOURCE/.git" ]]; then
        git -C "$TRIAL_SOURCE" fetch origin master
        git -C "$TRIAL_SOURCE" checkout --detach "$revision"
    else
        rm -rf "$TRIAL_SOURCE"
        git -C "$LLAMA_REPO" worktree add --detach "$TRIAL_SOURCE" "$revision"
    fi

    cmake -S "$TRIAL_SOURCE" -B "$TRIAL_BUILD" \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.6/bin/nvcc \
        -DCMAKE_CUDA_ARCHITECTURES=87 \
        -DGGML_NATIVE=ON \
        -DBUILD_SHARED_LIBS=ON
    cmake --build "$TRIAL_BUILD" --config Release -j "${BUILD_JOBS:-4}" \
        --target llama-server llama-cli llama-mtmd-cli
    "$TRIAL_BUILD/bin/llama-server" --version
}

download_model() {
    mkdir -p "$MODEL_DIR"
    python3 - "$HF_REPO" "$HF_REVISION" "$MODEL_DIR" "$TRIAL_MODEL_FILENAME" "$TRIAL_MMPROJ_FILENAME" <<'PY'
import sys
from huggingface_hub import hf_hub_download

repo, revision, destination, model_filename, mmproj_filename = sys.argv[1:]
for filename in (model_filename, mmproj_filename):
    print(hf_hub_download(
        repo_id=repo,
        filename=filename,
        revision=revision,
        local_dir=destination,
    ))
PY
    if [[ -n "$MODEL_SHA256" ]]; then
        printf '%s  %s\n' "$MODEL_SHA256" "$TRIAL_MODEL" | sha256sum --check -
    else
        sha256sum "$TRIAL_MODEL"
    fi
    if [[ -n "$MMPROJ_SHA256" ]]; then
        printf '%s  %s\n' "$MMPROJ_SHA256" "$TRIAL_MMPROJ" | sha256sum --check -
    else
        sha256sum "$TRIAL_MMPROJ"
    fi
}

write_trial_unit() {
    [[ -x "$TRIAL_BUILD/bin/llama-server" ]] || die "trial llama-server has not been built"
    [[ -f "$TRIAL_MODEL" ]] || die "trial model has not been downloaded"

    cat >"$UNIT_PATH" <<EOF
[Unit]
Description=llama.cpp Server for Zoe (Gemma 4 12B trial)
After=network.target

[Service]
Type=simple
LimitMEMLOCK=infinity
Environment=LD_LIBRARY_PATH=$TRIAL_BUILD/bin
ExecStart=$TRIAL_BUILD/bin/llama-server \\
  --model $TRIAL_MODEL \\
  --mmproj $TRIAL_MMPROJ \\
  --host 0.0.0.0 \\
  --port 11434 \\
  --ctx-size 4096 \\
  --n-gpu-layers $TRIAL_GPU_LAYERS \\
  --fit on \\
  --fit-target $TRIAL_FIT_TARGET_MIB \\
  --fit-ctx 4096 \\
  --threads 4 \\
  --parallel 1 \\
  --batch-size 512 \\
  --ubatch-size 256 \\
  --cont-batching \\
  --cache-type-k q8_0 \\
  --cache-type-v q8_0 \\
  --spec-type $TRIAL_SPEC_TYPE \\
  --spec-ngram-simple-size-n $TRIAL_SPEC_NGRAM_N \\
  --spec-ngram-simple-size-m $TRIAL_SPEC_NGRAM_M \\
  --spec-ngram-simple-min-hits $TRIAL_SPEC_NGRAM_HITS \\
  --cache-reuse $TRIAL_CACHE_REUSE \\
  --cache-ram $TRIAL_CACHE_RAM_MIB \\
  --ctx-checkpoints $TRIAL_CTX_CHECKPOINTS \\
  --checkpoint-min-step $TRIAL_CHECKPOINT_MIN_STEP \\
  --flash-attn on \\
  --temp 0.6 \\
  --top-k 40 \\
  --top-p 0.90 \\
  --repeat-penalty 1.05 \\
  --jinja \\
  --chat-template-kwargs '{"enable_thinking":false}' \\
  --mlock \\
  --metrics
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

switch_trial() {
    [[ -L "$STATE_DIR/latest-snapshot" ]] || snapshot >/dev/null
    write_trial_unit
    systemctl --user daemon-reload
    systemctl --user restart llama-server
    if ! wait_for_health; then
        printf 'trial failed its health check; restoring E2B\n' >&2
        rollback
        return 1
    fi
    curl -fsS "$HEALTH_URL"
    printf '\n'
}

validate_new_binary_with_e2b() {
    local snapshot_path
    snapshot_path="$(latest_snapshot)"
    [[ -x "$TRIAL_BUILD/bin/llama-server" ]] || die "trial llama-server has not been built"

    sed \
        -e "s|$CURRENT_BUILD/bin/llama-server|$TRIAL_BUILD/bin/llama-server|" \
        -e "s|Environment=LD_LIBRARY_PATH=$CURRENT_BUILD/bin|Environment=LD_LIBRARY_PATH=$TRIAL_BUILD/bin|" \
        "$snapshot_path/llama-server.service" >"$UNIT_PATH"
    systemctl --user daemon-reload
    systemctl --user restart llama-server
    if ! wait_for_health; then
        printf 'new binary failed with E2B; restoring known-good runtime\n' >&2
        rollback
        return 1
    fi
    curl -fsS "$HEALTH_URL"
    printf '\n'
}

rollback() {
    local snapshot_path
    snapshot_path="$(latest_snapshot)"
    cp -a "$snapshot_path/llama-server.service" "$UNIT_PATH"
    systemctl --user daemon-reload
    systemctl --user restart llama-server
    wait_for_health || die "rollback completed but E2B health check failed"
    curl -fsS "$HEALTH_URL"
    printf '\n'
}

status() {
    systemctl --user --no-pager --full status llama-server || true
    printf '\nhealth: '
    curl -fsS "$HEALTH_URL" || true
    printf '\nvoice: '
    curl -fsS http://127.0.0.1:8000/api/voice/livekit-health || true
    printf '\n'
}

observe() {
    local hours="${1:-24}"
    local interval="${OBSERVE_INTERVAL_SECONDS:-60}"
    local deadline baseline_swap_kb failures=0 pressure=0 restarts
    local log_dir="$HOME/benchmarks"
    local log_path="$log_dir/gemma4-observation-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
    mkdir -p "$log_dir"

    deadline=$(( $(date +%s) + hours * 3600 ))
    baseline_swap_kb="$(
        awk '/SwapTotal:/ {total=$2} /SwapFree:/ {free=$2} END {print total-free}' /proc/meminfo
    )"

    while (( $(date +%s) < deadline )); do
        local now mem_available_kb swap_used_kb swap_growth_kb health_ok
        now="$(date -u +%FT%TZ)"
        mem_available_kb="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
        swap_used_kb="$(
            awk '/SwapTotal:/ {total=$2} /SwapFree:/ {free=$2} END {print total-free}' /proc/meminfo
        )"
        swap_growth_kb=$((swap_used_kb - baseline_swap_kb))
        restarts="$(systemctl --user show llama-server -p NRestarts --value)"
        if curl -fsS "$HEALTH_URL" >/dev/null; then
            health_ok=true
            failures=0
        else
            health_ok=false
            failures=$((failures + 1))
        fi

        if ((mem_available_kb < 1048576 && swap_growth_kb > 524288)); then
            pressure=$((pressure + 1))
        else
            pressure=0
        fi

        printf \
            '{"time":"%s","health":%s,"mem_available_kb":%d,"swap_used_kb":%d,"swap_growth_kb":%d,"restarts":%d,"health_failures":%d,"pressure_samples":%d}\n' \
            "$now" "$health_ok" "$mem_available_kb" "$swap_used_kb" \
            "$swap_growth_kb" "$restarts" "$failures" "$pressure" >>"$log_path"

        if ((failures >= 3 || restarts > 0 || pressure >= 5)); then
            printf 'observation gate failed at %s; rolling back\n' "$now" >>"$log_path"
            rollback >>"$log_path" 2>&1
            return 1
        fi
        sleep "$interval"
    done

    printf '{"time":"%s","result":"passed"}\n' "$(date -u +%FT%TZ)" >>"$log_path"
    printf '%s\n' "$log_path"
}

start_observation() {
    local hours="${1:-24}"
    systemctl --user reset-failed llama-server
    systemd-run --user \
        --unit=zoe-gemma4-observation \
        --description="Zoe Gemma 4 12B observation and rollback guard" \
        "$0" observe "$hours"
}

usage() {
    cat <<'EOF'
Usage: gemma4_trial.sh COMMAND

Commands:
  snapshot             Preserve the current unit, build, hashes, and revision.
  build [REVISION]     Build current llama.cpp separately for Jetson CUDA.
  download             Download and verify the pinned 12B model and mmproj.
  validate-e2b         Run E2B using the new binary, with automatic rollback.
  switch               Switch to 12B, with automatic rollback on failed health.
  rollback             Restore the exact snapshotted E2B unit.
  observe [HOURS]      Monitor 12B and roll back automatically on failure.
  start-observation    Launch the 24-hour observation as a user service.
  status               Show llama-server and repaired voice health.
EOF
}

case "${1:-}" in
    snapshot) snapshot ;;
    build) build_trial "${2:-origin/master}" ;;
    download) download_model ;;
    validate-e2b) validate_new_binary_with_e2b ;;
    switch) switch_trial ;;
    rollback) rollback ;;
    observe) observe "${2:-24}" ;;
    start-observation) start_observation "${2:-24}" ;;
    status) status ;;
    *) usage; exit 2 ;;
esac
