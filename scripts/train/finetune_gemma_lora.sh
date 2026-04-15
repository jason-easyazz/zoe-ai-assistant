#!/usr/bin/env bash
# ============================================================================
# Fine-tune Gemma 4 E2B as Zoe using LoRA on the Jetson (CUDA GPU)
#
# Result: a merged GGUF that can be shared to Pi and loads with no system prompt
# Required: ~6GB VRAM (Jetson AGX/Orin recommended), Python 3.10+, CUDA 12+
#
# Usage:
#   bash finetune_gemma_lora.sh [--data training.jsonl] [--steps 200] [--dry-run]
#
# After this runs:
#   1. Merged model saved to ~/models/zoe-gemma-4-e2b-lora-merged/
#   2. Converted to GGUF at ~/models/zoe-gemma-4-e2b-Q4_K_M.gguf
#   3. Copy GGUF to Pi: scp ~/models/zoe-gemma-4-e2b-Q4_K_M.gguf pi@192.168.1.60:~/models/
#   4. Update gemma-server.service on Pi to point to the new model
# ============================================================================

set -euo pipefail

TRAINING_DATA="${TRAINING_DATA:-zoe_gemma_training.jsonl}"
MAX_STEPS="${MAX_STEPS:-300}"
BASE_MODEL_ID="${BASE_MODEL_ID:-google/gemma-4-e2b-it}"
BASE_GGUF="${BASE_GGUF:-$HOME/models/google_gemma-4-E2B-it-Q4_K_M.gguf}"
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
OUTPUT_DIR="${OUTPUT_DIR:-$HOME/models/zoe-gemma-4-e2b-lora}"
MERGED_DIR="${MERGED_DIR:-$HOME/models/zoe-gemma-4-e2b-lora-merged}"
GGUF_OUT="${GGUF_OUT:-$HOME/models/zoe-gemma-4-e2b-Q4_K_M.gguf}"
DRY_RUN=false

for arg in "$@"; do
    case $arg in
        --data=*) TRAINING_DATA="${arg#*=}" ;;
        --steps=*) MAX_STEPS="${arg#*=}" ;;
        --dry-run) DRY_RUN=true ;;
    esac
done

echo "════════════════════════════════════════════════════════════════"
echo "  Zoe × Gemma 4 E2B — LoRA Fine-Tuning"
echo "  Data:    $TRAINING_DATA"
echo "  Steps:   $MAX_STEPS"
echo "  Output:  $GGUF_OUT"
echo "════════════════════════════════════════════════════════════════"

# ── Prerequisites ─────────────────────────────────────────────────────────────
command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
command -v nvidia-smi >/dev/null 2>&1 && { echo "GPU:"; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; } || echo "Warning: no GPU detected"

[ -f "$TRAINING_DATA" ] || { echo "ERROR: Training data not found: $TRAINING_DATA"; echo "Run: python3 generate_gemma_training_data.py --out $TRAINING_DATA"; exit 1; }

if $DRY_RUN; then
    echo; echo "DRY RUN — would fine-tune Gemma with $MAX_STEPS steps on $(wc -l < "$TRAINING_DATA") examples"
    exit 0
fi

# ── Python environment ────────────────────────────────────────────────────────
VENV="$HOME/.venv-train"
if [ ! -d "$VENV" ]; then
    echo; echo "Setting up training venv..."
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

echo; echo "Installing Unsloth + TRL..."
pip install -q --upgrade pip
# Unsloth is the fastest LoRA trainer for Gemma on CUDA
pip install -q "unsloth[cu121] @ git+https://github.com/unslothai/unsloth.git" 2>/dev/null \
    || pip install -q unsloth  # Fallback to PyPI if CUDA 12.1 not available

pip install -q trl datasets transformers accelerate bitsandbytes

# ── Fine-tuning script ────────────────────────────────────────────────────────
echo; echo "Starting LoRA fine-tuning..."
python3 - <<PYEOF
import json, torch
from pathlib import Path
from datasets import Dataset
from unsloth import FastLanguageModel
from trl import SFTTrainer
from transformers import TrainingArguments

BASE_MODEL  = "$BASE_MODEL_ID"
OUTPUT_DIR  = "$OUTPUT_DIR"
MERGED_DIR  = "$MERGED_DIR"
MAX_STEPS   = int("$MAX_STEPS")
DATA_PATH   = "$TRAINING_DATA"

print(f"Loading base model: {BASE_MODEL}")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE_MODEL,
    max_seq_length=1024,
    dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    load_in_4bit=True,  # QLoRA — saves ~50% VRAM
)

# LoRA configuration: target attention + FFN layers
model = FastLanguageModel.get_peft_model(
    model,
    r=16,              # LoRA rank — 16 is good balance of quality vs size
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0.05,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=42,
)
print(f"LoRA adapter parameters: {model.num_parameters(only_trainable=True):,}")

# Load training data (ShareGPT format)
print(f"Loading training data from {DATA_PATH}")
raw = [json.loads(l) for l in open(DATA_PATH)]

# Convert ShareGPT → Gemma chat template
def format_example(example):
    convs = example["conversations"]
    formatted = tokenizer.apply_chat_template(
        [{"role": ("user" if c["from"] == "human" else "assistant"), "content": c["value"]}
         for c in convs],
        tokenize=False,
        add_generation_prompt=False,
    )
    return {"text": formatted}

dataset = Dataset.from_list(raw)
dataset = dataset.map(format_example)
print(f"Training on {len(dataset)} examples")

# Training arguments tuned for Pi/Jetson deployment target
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    max_steps=MAX_STEPS,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=4,   # Effective batch = 8
    learning_rate=2e-4,
    warmup_steps=20,
    lr_scheduler_type="cosine",
    fp16=torch.cuda.is_available(),
    optim="adamw_8bit",
    logging_steps=10,
    save_steps=100,
    save_total_limit=2,
    report_to="none",
    seed=42,
)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=1024,
    args=training_args,
)

print("Starting training...")
trainer.train()

# Save LoRA adapter
print(f"Saving LoRA adapter to {OUTPUT_DIR}")
model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)

# Merge adapter into base model (needed for GGUF conversion)
print(f"Merging adapter into base model → {MERGED_DIR}")
FastLanguageModel.for_inference(model)
model.save_pretrained_merged(MERGED_DIR, tokenizer, save_method="merged_16bit")
print("Merge complete!")
PYEOF

# ── Convert merged model to GGUF ─────────────────────────────────────────────
echo; echo "Converting merged model to Q4_K_M GGUF..."

# Activate llama.cpp python env if it has conversion tools
CONVERT_SCRIPT="$LLAMA_CPP_DIR/convert_hf_to_gguf.py"
if [ ! -f "$CONVERT_SCRIPT" ]; then
    CONVERT_SCRIPT="$LLAMA_CPP_DIR/convert-hf-to-gguf.py"
fi

if [ -f "$CONVERT_SCRIPT" ]; then
    python3 "$CONVERT_SCRIPT" "$MERGED_DIR" --outtype f16 --outfile "${GGUF_OUT%.gguf}_f16.gguf"

    # Quantise to Q4_K_M
    QUANTIZE="$LLAMA_CPP_DIR/build/bin/llama-quantize"
    if [ -f "$QUANTIZE" ]; then
        "$QUANTIZE" "${GGUF_OUT%.gguf}_f16.gguf" "$GGUF_OUT" Q4_K_M
        rm -f "${GGUF_OUT%.gguf}_f16.gguf"
        echo "Quantised to Q4_K_M: $GGUF_OUT"
    else
        echo "Warning: llama-quantize not found — keeping F16 GGUF"
        mv "${GGUF_OUT%.gguf}_f16.gguf" "$GGUF_OUT"
    fi
else
    echo "Warning: convert_hf_to_gguf.py not found at $LLAMA_CPP_DIR"
    echo "Merged model is at $MERGED_DIR — convert manually."
fi

# ── Deploy to Pi ───────────────────────────────────────────────────────────────
PI_HOST="${PI_HOST:-pi@192.168.1.60}"

echo
echo "════════════════════════════════════════════════════════════════"
echo "  Fine-tuning complete!"
echo "  GGUF: $GGUF_OUT"
echo
echo "  To deploy to Pi:"
echo "    scp $GGUF_OUT ${PI_HOST}:~/models/zoe-gemma-4-e2b-Q4_K_M.gguf"
echo "    ssh $PI_HOST 'sed -i \"s|google_gemma-4-E2B-it-Q4_K_M.gguf|zoe-gemma-4-e2b-Q4_K_M.gguf|\" ~/.config/systemd/user/gemma-server.service && systemctl --user daemon-reload && systemctl --user restart gemma-server'"
echo
echo "  After deploying the fine-tuned model, shrink _PI_SOUL in pi_agent.py:"
echo '    _PI_SOUL = "You are Zoe. Tools: mempalace_search, mempalace_add, ha_control, bash."'
echo "════════════════════════════════════════════════════════════════"
