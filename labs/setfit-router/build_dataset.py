#!/usr/bin/env python3
"""Build the training set: seeds + synthetic paraphrases via local llama-server.

LAB-ONLY. Talks to an OpenAI-compatible llama-server you name EXPLICITLY via
--brain-url (no default: pointing this at the live brain must be a deliberate
choice, made off-peak). Requests are strictly sequential and rate-capped by
--max-rps (default 0.5 req/s) so live voice turns are never starved. Output: data/train.jsonl ({text, label}); dedup-normalized and
filtered against the held-out eval corpus (eval/needle_corpus.jsonl) so no
eval utterance leaks into training.

Usage: python3 build_dataset.py --brain-url http://localhost:11434 [--target-per-class 80] [--max-rps 0.5]
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
BRAIN_URL = None  # set from --brain-url in main()
_LAST_CALL = 0.0
MAX_RPS = 0.5

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
    global _LAST_CALL
    wait = (1.0 / MAX_RPS) - (time.monotonic() - _LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL = time.monotonic()
    body = json.dumps({
        "model": "gemma",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(BRAIN_URL.rstrip("/") + "/v1/chat/completions", data=body,
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
    global BRAIN_URL, MAX_RPS
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain-url", required=True,
                    help="OpenAI-compatible llama-server base URL (e.g. http://localhost:11434). "
                         "No default on purpose: hitting the LIVE brain must be explicit; run off-peak.")
    ap.add_argument("--target-per-class", type=int, default=80)
    ap.add_argument("--max-rps", type=float, default=0.5,
                    help="max requests/second to the brain (default 0.5; keep conservative)")
    args = ap.parse_args()
    BRAIN_URL = args.brain_url
    MAX_RPS = max(0.01, args.max_rps)

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
