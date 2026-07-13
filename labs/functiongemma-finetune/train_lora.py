#!/usr/bin/env python3
"""LoRA fine-tune FunctionGemma-270M as Zoe's complete-call router (LAB ONLY).

Two variants from ONE canonical dataset (data/train.jsonl):

  --variant plain    prompts carry the full 21-tool declaration block (stock
                     FunctionGemma usage); target = <start_function_call>…
  --variant functok  Octopus-style: prompts carry NO tool declarations (a
                     short fixed developer line only); target OPENS with a
                     functional token <zoe_tool_k> — mapped onto Gemma's
                     reserved <unusedK> vocab rows (no resize, GGUF-safe) —
                     followed by the same call syntax for the args.
                     <zoe_no_tool> == <unused20>.

Targets are rendered through the model's own chat template (never hand-built)
so the training format matches inference exactly.

Memory safety (this box runs the live brain + Kokoro, chronically tight):
  - refuses to start when MemAvailable < 2 GB
  - a callback aborts training (saving state) if MemAvailable < 600 MB
  - bf16 + LoRA + Adafactor + gradient checkpointing; embed_tokens is fully
    trained ONLY for functok (the functional tokens live there) with
    ensure_weight_tying=True so the tied lm_head follows.

Run (repo root):
  python3 labs/functiongemma-finetune/train_lora.py --variant functok \
      [--epochs 3] [--out runs/functok]
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
# on-box copy; off-box, pass --model-dir (or the HF id unsloth/functiongemma-270m-it)
DEFAULT_MODEL_DIR = "/home/zoe/models/lab/functiongemma-270m-it-hf"
MIN_START_MB = 2048
ABORT_MB = 600

DEV_PROMPT_PLAIN = (
    "You are Zoe, a local voice assistant. Route the user's utterance to "
    "exactly one function call with the right arguments. If the utterance is "
    "ordinary conversation with no actionable command, call general_chat."
)
DEV_PROMPT_FUNCTOK = (
    "You are Zoe's router. Answer with the routing token for the user's "
    "utterance, then the function call arguments."
)

CHAT_REPLIES = [
    "Sure — happy to chat about that.", "Good question! Let me think with you.",
    "Ha, I like that.", "I'm here — go on.", "That sounds lovely.",
    "Hmm, tell me more.", "Always happy to talk.",
]

# tool order is zoe_tools.json order; index 20 == no-tool (<zoe_no_tool>)
NO_TOOL_INDEX = 20


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise RuntimeError("MemAvailable not found")


def load_tools() -> list[dict]:
    raw = json.loads((HERE / "zoe_tools.json").read_text())
    raw.append({
        "name": "general_chat",
        "description": ("Escape hatch: the utterance is ordinary conversation, "
                        "a question for the assistant, or chit-chat that needs "
                        "no tool. Call this when no other function applies."),
        "parameters": {"utterance": {"type": "string"}},
    })
    return [
        {"type": "function",
         "function": {"name": t["name"], "description": t["description"],
                      "parameters": {"type": "object",
                                     "properties": t["parameters"]}}}
        for t in raw
    ]


def functional_token(tool_index: int) -> str:
    return f"<unused{tool_index}>"


def render_example(tok, rec: dict, variant: str, tools: list[dict],
                   tool_index: dict[str, int], rng: random.Random) -> tuple[str, str]:
    """Returns (prompt_text, target_text). Prompt ends at '<start_of_turn>model\\n'."""
    dev = DEV_PROMPT_PLAIN if variant == "plain" else DEV_PROMPT_FUNCTOK
    msgs = [{"role": "system", "content": dev},
            {"role": "user", "content": rec["text"]}]
    if rec["tool"] is None:
        reply = rng.choice(CHAT_REPLIES)
        full_msgs = msgs + [{"role": "assistant", "content": reply}]
    else:
        args = {k: v for k, v in rec["args"].items() if v != ""}
        full_msgs = msgs + [{"role": "assistant", "tool_calls": [
            {"type": "function",
             "function": {"name": rec["tool"], "arguments": args}}]}]
    tpl_tools = tools if variant == "plain" else None
    full = tok.apply_chat_template(full_msgs, tools=tpl_tools, tokenize=False)
    marker = "<start_of_turn>model\n"
    cut = full.rindex(marker) + len(marker)
    prompt, target = full[:cut], full[cut:]
    # trim template tails the model should not be trained to emit
    for tail in ("<start_function_response>",):
        if target.endswith(tail):
            target = target[: -len(tail)]
    if not target.endswith("<end_of_turn>\n") and rec["tool"] is None:
        target += "<end_of_turn>\n"
    if variant == "functok":
        idx = NO_TOOL_INDEX if rec["tool"] is None else tool_index[rec["tool"]]
        if rec["tool"] is None:
            target = functional_token(idx) + "<end_of_turn>\n"  # router stops here
        else:
            target = functional_token(idx) + target
    return prompt, target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["plain", "functok"], required=True)
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--data", type=Path, default=HERE / "data" / "train.jsonl")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=None,
                    help="default 3072 for plain, 256 for functok")
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--cpu", action="store_true")
    args = ap.parse_args()
    out = args.out or HERE / "runs" / args.variant
    max_len = args.max_len or (3072 if args.variant == "plain" else 256)

    avail = mem_available_mb()
    if avail < MIN_START_MB:
        raise SystemExit(f"ABORT: MemAvailable {avail} MB < {MIN_START_MB} MB "
                         "— free memory first (live services take priority).")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainerCallback, TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    tools = load_tools()
    tool_index = {t["function"]["name"]: i for i, t in enumerate(tools)}
    assert tool_index["general_chat"] == NO_TOOL_INDEX

    rng = random.Random(7)
    records = [json.loads(l) for l in args.data.read_text().splitlines()]
    rng.shuffle(records)

    def encode(rec):
        prompt, target = render_example(tok, rec, args.variant, tools,
                                        tool_index, rng)
        p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
        t_ids = tok(target, add_special_tokens=False)["input_ids"]
        ids = (p_ids + t_ids)[:max_len]
        labels = ([-100] * len(p_ids) + t_ids)[:max_len]
        return {"input_ids": ids, "labels": labels}

    encoded = [encode(r) for r in records]
    n_val = max(8, int(len(encoded) * args.val_frac))
    ds_val = Dataset.from_list(encoded[:n_val])
    ds_train = Dataset.from_list(encoded[n_val:])
    print(f"train {len(ds_train)}  val {len(ds_val)}  max_len {max_len}")

    device_ok = torch.cuda.is_available() and not args.cpu
    if not device_ok:
        # bf16 matmuls are emulated (pathologically slow) on aarch64 CPU
        torch.set_num_threads(int(os.environ.get("TRAIN_THREADS", "6")))
    model = AutoModelForCausalLM.from_pretrained(
        args.model_dir, dtype=torch.bfloat16 if device_ok else torch.float32,
        attn_implementation="eager")  # gemma3 recommends eager for training
    model.config.use_cache = False

    lora_kwargs = dict(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM",
    )
    if args.variant == "functok":
        # the functional tokens live in reserved <unusedK> embedding rows —
        # they must be trained; tied lm_head follows via ensure_weight_tying
        lora_kwargs["modules_to_save"] = ["embed_tokens"]
        lora_kwargs["ensure_weight_tying"] = True
    model = get_peft_model(model, LoraConfig(**lora_kwargs))
    model.print_trainable_parameters()

    def collate(batch):
        maxlen = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        input_ids, labels, attn = [], [], []
        for b in batch:
            d = maxlen - len(b["input_ids"])
            input_ids.append(b["input_ids"] + [pad] * d)
            labels.append(b["labels"] + [-100] * d)
            attn.append([1] * len(b["input_ids"]) + [0] * d)
        return {"input_ids": torch.tensor(input_ids),
                "labels": torch.tensor(labels),
                "attention_mask": torch.tensor(attn)}

    class MemGuard(TrainerCallback):
        def on_step_end(self, targs, state, control, **kw):
            if state.global_step % 20 == 0 and mem_available_mb() < ABORT_MB:
                print(f"MEMGUARD: MemAvailable < {ABORT_MB} MB — aborting run")
                control.should_training_stop = True
            return control

    targs = TrainingArguments(
        output_dir=str(out), num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.accum,
        learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.03,
        logging_steps=25, eval_strategy="epoch", save_strategy="no",
        bf16=device_ok, use_cpu=not device_ok,
        gradient_checkpointing=(device_ok or args.variant == "plain"),
        # plain's ~2.3k-token sequences need checkpointing even on CPU —
        # un-checkpointed activations swap-thrashed the live box (avail 164MB)
        optim="adafactor",
        report_to=[], dataloader_num_workers=0, seed=7,
    )
    class WindowedLossTrainer(Trainer):
        """Computes lm_head logits only over the last `window` positions.

        plain's ~2.3k-token sequences make full-sequence logits explode
        (batch x seq x 262k vocab fp32 ~ 2.5 GB per example) — but every
        supervised token sits at the END of the sequence, so a small
        logits_to_keep window loses nothing. Requires right-padding with
        window >= pad_delta + target_len (asserted per batch)."""

        def __init__(self, *a, window: int = 96, **kw):
            super().__init__(*a, **kw)
            self.window = window

        def compute_loss(self, model, inputs, return_outputs=False,
                         num_items_in_batch=None):
            import torch.nn.functional as F
            labels = inputs.pop("labels")
            k = min(self.window, labels.shape[1])
            assert (labels[:, :-k] != -100).sum() == 0, (
                "supervised token outside logits window — raise --loss-window")
            out = model(**inputs, logits_to_keep=k)
            # logits[:, j] predicts the token at position L-k+j+1
            shift_logits = out.logits[:, :-1, :]
            shift_labels = labels[:, labels.shape[1] - k + 1:]
            loss = F.cross_entropy(
                shift_logits.reshape(-1, shift_logits.size(-1)).float(),
                shift_labels.reshape(-1), ignore_index=-100)
            return (loss, out) if return_outputs else loss

    trainer_cls = WindowedLossTrainer if args.variant == "plain" else Trainer
    trainer = trainer_cls(model=model, args=targs, train_dataset=ds_train,
                          eval_dataset=ds_val, data_collator=collate,
                          callbacks=[MemGuard()])
    trainer.train()

    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out / "adapter"))
    # merged export for GGUF conversion
    merged = model.merge_and_unload()
    merged.save_pretrained(str(out / "merged"))
    tok.save_pretrained(str(out / "merged"))
    print(f"saved adapter + merged -> {out}")


if __name__ == "__main__":
    main()
