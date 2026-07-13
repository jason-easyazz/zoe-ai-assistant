#!/usr/bin/env python3
"""Build the training set: seeds + synthetic paraphrases via local llama-server.

LAB-ONLY. Talks to the live Gemma brain at :11434 (OpenAI-compatible) at a
gentle sequential rate (one request at a time + pause) so live voice turns are
not starved. Output: data/train.jsonl ({text, label}); dedup-normalized and
filtered against the held-out eval corpus (eval/needle_corpus.jsonl) so no
eval utterance leaks into training.

Usage: python3 build_dataset.py [--target-per-class 80] [--sleep 2.0]
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from labels import LABELS, SEEDS

HERE = Path(__file__).parent
LLAMA = "http://localhost:11434/v1/chat/completions"

CLASS_DESC = {
    "calendar": "checking or adding calendar events / appointments / schedule",
    "lists": "shopping lists, todo lists: add, remove, show items",
    "reminders": "setting or listing reminders (remind me to ...)",
    "timers": "kitchen-style countdown timers: set, check, cancel",
    "weather": "asking about weather, temperature, rain, forecast",
    "time": "asking the current time, date, or day",
    "people": "asking about or telling facts about a specific person the user knows (names, birthdays, relationships)",
    "memory": "asking Zoe to remember a personal fact, or to recall something the user said earlier",
    "notes": "creating, dictating, or searching free-text notes",
    "journal": "adding a journal or diary entry about the user's day/feelings",
    "music": "playing, pausing, skipping music; playlists; music volume",
    "smart_home": "controlling lights, switches, fans, thermostat, locks in the house",
    "chat": "pure conversation with the assistant: small talk, feelings, opinions, general-knowledge questions, jokes — NO device/tool action wanted",
}


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


def llama(prompt: str, max_tokens: int = 700) -> str:
    body = json.dumps({
        "model": "gemma",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(LLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def gen_paraphrases(label: str, seeds: list[str], n: int, style_hint: str) -> list[str]:
    seed_block = "\n".join(f"- {s}" for s in seeds[:10])
    prompt = (
        f"You generate training utterances for a voice-assistant intent classifier.\n"
        f"Intent: {label} — {CLASS_DESC[label]}\n"
        f"Existing examples:\n{seed_block}\n\n"
        f"Write {n} NEW spoken utterances a real user would say for this intent, "
        f"{style_hint}. Vary vocabulary heavily; avoid reusing the example wording. "
        f"Casual Australian English is welcome. One per line, no numbering, no quotes."
    )
    out = llama(prompt)
    lines = [re.sub(r"^[\-\*\d\.\)\s]+", "", l).strip().strip('"') for l in out.splitlines()]
    return [l for l in lines if 2 <= len(l.split()) <= 24]


STYLES = [
    "short and direct",
    "sloppy, indirect, or trailing-off paraphrases — the kind keyword routing misses",
    "polite or roundabout phrasings (could you maybe..., I was wondering if...)",
    "phrasings with fillers and speech-recognition style lowercase, no punctuation",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-per-class", type=int, default=80)
    ap.add_argument("--sleep", type=float, default=2.0)
    args = ap.parse_args()

    # held-out eval texts: NEVER in training
    eval_norms = {norm(json.loads(l)["text"])
                  for l in (HERE / "eval/needle_corpus.jsonl").read_text().splitlines() if l.strip()}

    out_path = HERE / "data/train.jsonl"
    data: dict[str, dict[str, str]] = {}  # norm -> row
    if out_path.exists():  # resumable
        for l in out_path.read_text().splitlines():
            r = json.loads(l)
            data[norm(r["text"])] = r

    def add(text: str, label: str, src: str) -> bool:
        k = norm(text)
        if not k or k in eval_norms or k in data:
            return False
        data[k] = {"text": text, "label": label, "source": src}
        return True

    for lab in LABELS:
        for s in SEEDS[lab]:
            add(s, lab, "seed")

    for lab in LABELS:
        count = lambda: sum(1 for r in data.values() if r["label"] == lab)
        si = 0
        tries = 0
        while count() < args.target_per_class and tries < 12:
            tries += 1
            style = STYLES[si % len(STYLES)]
            si += 1
            try:
                for t in gen_paraphrases(lab, SEEDS[lab], 20, style):
                    add(t, lab, "synthetic")
            except Exception as e:
                print(f"[{lab}] gen error: {e}; backing off")
                time.sleep(10)
            time.sleep(args.sleep)  # gentle on the live brain
            print(f"[{lab}] {count()}/{args.target_per_class}")
        # checkpoint after each class
        with out_path.open("w") as f:
            for r in data.values():
                f.write(json.dumps(r) + "\n")

    from collections import Counter
    print(Counter(r["label"] for r in data.values()))
    print(f"total {len(data)} -> {out_path}")


if __name__ == "__main__":
    main()
