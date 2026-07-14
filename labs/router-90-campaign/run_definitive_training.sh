#!/bin/bash
# DEFINITIVE functok training run for the router-90 campaign.
# 2 more epochs on the EXPANDED dataset (original 2,950 + sibling set 275),
# warm-starting from the e1 merged checkpoint (runs/functok/merged) — i.e.
# 3 total epochs on the original data + 2 on the sibling-discrimination set.
# Full-speed regime (2026-07-14, Jason: box is NOT production): no idle-pause
# supervisor; keeps only oom_score_adj=1000 tagging as cheap insurance.
# Stop llama-server first for RAM/cores; restart it after
# (systemctl --user stop/start llama-server.service).
#
# Usage:  nohup bash labs/router-90-campaign/run_definitive_training.sh \
#             >> /home/zoe/zoe-definitive-trainer.log 2>&1 &
set -eu
FT="$(cd "$(dirname "$0")/../functiongemma-finetune" && pwd)"
MERGED="${MERGED:-$FT/runs/functok/merged}"   # e1 checkpoint (or e3's when done)
OUT="${OUT:-$FT/runs/functok-definitive}"
echo "=== definitive run start $(date)  base=$MERGED out=$OUT"

cat "$FT/data/train.jsonl" "$FT/data/train_sibling.jsonl" \
    > "$FT/data/train_expanded.jsonl"
wc -l "$FT/data/train_expanded.jsonl"

setsid env TRAIN_THREADS=8 python3 "$FT/train_lora.py" --variant functok --cpu \
    --epochs 2 --batch 8 --accum 2 \
    --data "$FT/data/train_expanded.jsonl" \
    --model-dir "$MERGED" --out "$OUT" &
TPID=$!
sleep 5
for p in $(pgrep -g "$(ps -o pgid= -p $TPID | tr -d ' ')"); do
    echo 1000 > /proc/$p/oom_score_adj 2>/dev/null || true
done
echo "training pid=$TPID (oom_score_adj=1000)"
wait "$TPID"; RC=$?
echo "=== definitive training exited rc=$RC $(date)"
echo "next: cd $FT && LLAMA_CPP=/home/zoe/llama.cpp ./export_gguf.sh $OUT/merged \
functiongemma-270m-zoe-functok-def-Q8_0.gguf  # then copy to /home/zoe/models/lab/"
echo "then: GGUF_FUNCTOK=/home/zoe/models/lab/functiongemma-270m-zoe-functok-def-Q8_0.gguf \
python3 labs/router-90-campaign/run_grammar_eval.py --config functok-gb"
