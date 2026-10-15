#!/usr/bin/env bash
# ============================================================
# fetch_speaker_models.sh - the WeSpeaker ResNet34 INT8 model for the panel's voice daemon
# (FLAG-DARK: the daemon loads it only when ZOE_SPEAKER_MODEL=dual|resnet34).
#
# Operator doc: docs/knowledge/speaker-resnet34-switch.md. Spec: docs/research/speaker-resnet34-optimisation-2026-10.md.
#
# Unlike the face models (fetch_face_models.sh downloads them straight onto the Pi), the INT8 QDQ file is NOT published anywhere:
# it is DERIVED from the upstream FP32 model. So there are three modes:
#
#   build   (on the Jetson) fetch the upstream FP32 file (sha256-pinned), the public LibriSpeech dev-clean calibration set
#           (sha256-pinned), quantise with the harness the record used, check the result against the pinned digest, and compute the
#           cohort centring vector. Output: OUT/wespeaker_resnet34_i8qdq_u8s8.onnx (+ a refreshed artifacts JSON for review).
#           Heavy (a few GB of scratch, minutes of CPU): holds the voice-harness lock, runs niced, refuses below 2 GB MemAvailable.
#   push    (on the Jetson) copy the verified model to the Pi's ~/.zoe-voice/models/ over ssh, then run `verify` there.
#   verify  (on the Pi, or anywhere the daemon files are) sha256-check the model and run the daemon's own START-UP SELF-TEST
#           (reference-embedding cosine + latency). Exit 0 = the daemon would accept it. This is also the pre-flip check.
#
# Nothing here touches the live daemon, the database or a unit. Model files are never committed to the repo.
# Env: SPEAKER_RESNET34_MODEL_DIR (Pi model dir, default ~/.zoe-voice/models) PI_HOST PI_USER PI_DAEMON_DIR PI_VENV
# ============================================================
set -euo pipefail

MODEL_NAME="wespeaker_resnet34_i8qdq_u8s8.onnx"
# Pins - MIRRORED in scripts/setup/zoe_speaker_resnet34.py (a test fails on drift).
MODEL_SHA256="d1f65aa0e09ba00793df05ecdb21df7c5fc1336e219e029d399b2f5aa71eae32"
FP32_SHA256="5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94"
DEVCLEAN_SHA256="76f87d090650617fca0cac8f88b9416e0ebf80350acb97b343a85fa903728ab3"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${SPEAKER_RESNET34_MODEL_DIR:-$HOME/.zoe-voice/models}"
PI_HOST="${PI_HOST:-192.168.1.61}"
PI_USER="${PI_USER:-pi}"
PI_DAEMON_DIR="${PI_DAEMON_DIR:-/home/pi/.zoe-voice}"
PI_VENV="${PI_VENV:-/home/pi/.zoe-voice/venv}"
MODE="${1:-verify}"

verify_local() {
  local dir="$1" f
  f="$dir/$MODEL_NAME"
  [ -f "$f" ] || { echo "MISSING: $f (build on the Jetson, then: $0 push)" >&2; return 3; }
  echo "$MODEL_SHA256  $f" | sha256sum -c --quiet - || { echo "REFUSED: $f does not match the pinned sha256" >&2; return 3; }
  echo "==> sha256 ok: $f"
}

case "$MODE" in
  verify)
    verify_local "$MODEL_DIR"
    PY="${PI_VENV}/bin/python3"; [ -x "$PY" ] || PY="python3"
    # the daemon's own loader: pin + artifacts + self-test (cosine to the reference, latency); exit 3 = it would REFUSE
    ( cd "$SRC_DIR" && SPEAKER_RESNET34_MODEL_DIR="$MODEL_DIR" "$PY" -c 'import sys, json, zoe_speaker_resnet34 as z
try:
    e = z.Resnet34Embedder.load()
    print(json.dumps(dict(ok=True, model_id=z.MODEL_ID, report=z.selftest(e))))
except z.Unavailable as exc:
    print(json.dumps(dict(ok=False, refused=str(exc)))); sys.exit(3)' )
    ;;
  build)
    OUT="${2:-$HOME/.cache/zoe-speaker-resnet34}"
    free_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
    [ "$free_mb" -ge 2048 ] || { echo "refusing: MemAvailable ${free_mb} MB < 2048 MB (the live voice brain needs it)" >&2; exit 4; }
    mkdir -p "$OUT"
    echo "==> building the INT8 model + centring vector into $OUT (scratch: ~/.cache/zoe-speaker-bakeoff)"
    CUDA_VISIBLE_DEVICES="" flock -x -w 3600 /tmp/zoe-voice-harness.lock nice -n 10 \
      python3 "$SRC_DIR/../perf/speaker_resnet34_artifacts.py" build --out "$OUT"
    echo "==> next: review $OUT/zoe_speaker_resnet34_artifacts.json against the committed one, then: $0 push $OUT"
    ;;
  push)
    SRC="${2:?usage: $0 push <dir holding $MODEL_NAME>}"
    verify_local "$SRC"
    REMOTE_DIR="${PI_DAEMON_DIR}/models"
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}" "mkdir -p '${REMOTE_DIR}' && chmod 700 '${REMOTE_DIR}'"
    rsync -a --chmod=F644 "$SRC/$MODEL_NAME" "${PI_USER}@${PI_HOST}:${REMOTE_DIR}/$MODEL_NAME"
    # the Pi runs the same verify (pin + the daemon's self-test) against the file it now holds
    ssh -o BatchMode=yes "${PI_USER}@${PI_HOST}" "cd '${PI_DAEMON_DIR}' && SPEAKER_RESNET34_MODEL_DIR='${REMOTE_DIR}' bash fetch_speaker_models.sh verify"
    ;;
  *)
    echo "usage: $0 [verify | build [outdir] | push <dir>]" >&2
    exit 2
    ;;
esac
