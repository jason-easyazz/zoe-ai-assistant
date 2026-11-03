#!/usr/bin/env python3
"""Live-shape prefix-cache bench for the brain's ONE llama-server slot.

Why it exists: the warm isolated harnesses (``measure_voice.py``, ``measure_first_sound.py``) run one prompt shape alone, so they
report "prefix cache healthy, median re-prefill 1 token" while the live brain re-prefilled its whole prompt on about half of
real voice turns (2026-10-12, ``docs/knowledge/brain-prefix-cache-2026-10-12.md``). This replays the SHAPES of a real session
(prompt sizes, the recall block that is elided on the next turn, the tool block that grows, the three post-turn helper calls
per turn) against a llama-server and reports, per condition, ``first_prompt_n`` / ``first_cache_n``, the miss rate and TTFT.

Conditions (same turn sequence in each):
  isolated     no helper traffic                              (what the old harnesses measured; the ceiling)
  interleaved  three helper calls right after every turn      (the live behaviour before ``brain_slot_guard``)
  guarded      helpers queued and run as a batch once the burst of turns ends (``brain_slot_guard`` quiet window)
  harness      guarded, plus ONE harness conversation turn (its own session: the same system + tool block, its own
               ~0.5k-token history) between EVERY two panel turns - the Samantha bar / day-sim running while the
               household talks (P-3, 2026-10-19). guarded vs harness = what harness quiet hours buy the panel.

Everything is synthetic text: no household words. The prompts only need the right TOKEN COUNTS and the right place where they
change; ``/tokenize`` on the target server sizes them.

Targets:
  --url http://127.0.0.1:11499   a scratch llama-server (any SWA model, ``--swa-full --cache-ram 1024 --parallel 1``): offline
                                 proof of the mechanism; TTFT is then a projection from ``--prefill-ms-per-token``.
  --live                         the live 4B on :11434. Takes ``flock -w 3600 /tmp/zoe-brain-window.lock`` itself, refuses
                                 02:30-04:30 local (the 12B night window) and the voice-harness lock, and never restarts anything.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import random
import statistics
import sys
import time
import urllib.request
from datetime import datetime

LIVE_URL = "http://127.0.0.1:11434"
BRAIN_WINDOW_LOCK = "/tmp/zoe-brain-window.lock"
# Measured on the live 4B 2026-10-12 (FLUE_PROMPT_CACHE rows): 3804 tokens in 5683 ms.
LIVE_PREFILL_MS_PER_TOKEN = 1.49
LIVE_TTFT_FLOOR_MS = 160.0

VOCAB = ("alpha river stone quiet window copper garden planet orange silver bridge candle forest harbor island jungle kettle "
         "lantern meadow needle ribbon saddle timber velvet willow yellow zephyr anchor basket cedar dune ember fjord").split()


def post(url: str, body: dict, timeout: float = 180.0) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


class Target:
    def __init__(self, base: str, prefill_ms: float):
        self.base = base.rstrip("/")
        self.prefill_ms = prefill_ms
        self._ratio: float | None = None

    def ntok(self, text: str) -> int:
        return len(post(self.base + "/tokenize", {"content": text})["tokens"])

    def words(self, seed: int, n_tokens: int) -> str:
        """About ``n_tokens`` tokens of deterministic filler (calibrated once against the server's tokenizer)."""
        if self._ratio is None:
            probe = " ".join(random.Random(0).choice(VOCAB) for _ in range(400))
            self._ratio = self.ntok(probe) / 400.0
        r = random.Random(seed)
        return " ".join(r.choice(VOCAB) for _ in range(max(1, int(n_tokens / self._ratio))))

    def complete(self, prompt: str, n_predict: int) -> tuple[int, int, float, str]:
        """(prompt_n, cache_n, ttft_ms, text). Streams, so TTFT is wall time to the first generated token."""
        body = {"prompt": prompt, "n_predict": n_predict, "cache_prompt": True, "temperature": 0.0, "stream": True}
        req = urllib.request.Request(self.base + "/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
        t0 = time.monotonic()
        ttft = None
        text = ""
        last: dict = {}
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                d = json.loads(line[5:])
                if ttft is None and d.get("content"):
                    ttft = (time.monotonic() - t0) * 1000.0
                text += d.get("content", "")
                if d.get("stop"):
                    last = d
        t = last.get("timings", {})
        return int(t.get("prompt_n", 0)), int(t.get("cache_n", 0)), ttft if ttft is not None else (time.monotonic() - t0) * 1000.0, text


class Session:
    """One conversation's prompt, built the way the live brain builds it (system, tools, history, newest turn)."""

    SYSTEM_TOKENS = 2721
    TOOL_STAGES = (491, 723, 872)      # observed tool-block sizes; the block only ever grows (session-sticky groups)
    RECALL_BLOCK_TOKENS = 560          # real tokens of a live recall turn's blocks: the logged chars/4 tail (434) undercounts ~1.27x

    def __init__(self, tg: Target, seed: int):
        self.tg, self.seed = tg, seed
        self.system = "<bos>" + tg.words(seed, self.SYSTEM_TOKENS)
        self.tools = [tg.words(seed + 10 + i, n) for i, n in enumerate(self.TOOL_STAGES)]
        self.stage = 0
        self.history: list[tuple[str, str]] = []   # (user words, reply) with blocks already elided
        self.n = 0

    def prompt(self, recall: bool) -> tuple[str, str]:
        self.n += 1
        words = self.tg.words(self.seed * 1000 + self.n, 12)
        block = (" BLOCK " + self.tg.words(self.seed * 7000 + self.n, self.RECALL_BLOCK_TOKENS) + " END ") if recall else ""
        s = self.system + "".join(self.tools[: self.stage + 1])
        for u, r in self.history:
            s += f" USER {u} MODEL {r}"
        return s + f" USER {block}{words}", words

    def settle(self, words: str, reply: str) -> None:
        self.history.append((words, reply))      # the next prompt carries the turn WITHOUT its recall block (elided)


def helper_prompts(tg: Target, utterance: str) -> list[str]:
    """The three post-turn calls seen live: fixed prefixes of ~376 / ~476 / ~296 tokens, the utterance last."""
    return [tg.words(500 + i, n) + " " + utterance for i, n in enumerate((376, 476, 296))]


def harness_session(tg: Target, panel: "Session", seed: int) -> "Session":
    """A second conversation sharing the panel's system + tool block (the same Flue sidecar soul), with its own
    pre-grown history (~0.5k tokens, a bar session mid-run; kept to its last 6 turns so it fits the 8192 slot)."""
    h = Session(tg, seed)
    h.system, h.tools = panel.system, panel.tools
    for k in range(4):
        h.history.append((tg.words(seed * 3000 + k, 12), tg.words(seed * 4000 + k, 120)))
    return h


def run_condition(tg: Target, name: str, turns: int, burst: int, seed: int) -> dict:
    sess = Session(tg, seed)
    rows, pending = [], []
    harness = harness_session(tg, sess, seed + 500) if name == "harness" else None
    for i in range(turns):
        recall = i % 3 == 0                       # one in three turns is a recall turn (block, then elided)
        if i in (6, 13):                          # the router shortlist unlocks another ability group
            sess.stage = min(sess.stage + 1, len(sess.tools) - 1)
        prompt, words = sess.prompt(recall)
        pn, cn, ttft, reply = tg.complete(prompt, 12)
        sess.settle(words, reply.strip() or "ok")
        first_of_burst = i % burst == 0
        rows.append({"turn": i, "recall": recall, "prompt_n": pn, "cache_n": cn, "ttft_ms": ttft, "first_of_burst": first_of_burst})
        calls = helper_prompts(tg, words)
        if name == "interleaved":
            for c in calls:
                tg.complete(c, 2)
        elif name in ("guarded", "harness"):
            pending += calls
            if (i + 1) % burst == 0:              # the conversation went quiet: flush the backlog, serially
                for c in pending:
                    tg.complete(c, 2)
                pending = []
        if harness is not None:                   # the harness takes the slot before the next panel turn
            harness.stage = sess.stage
            h_prompt, h_words = harness.prompt(i % 2 == 0)
            _pn, _cn, _t, h_reply = tg.complete(h_prompt, 12)
            harness.settle(h_words, h_reply.strip() or "ok")
            harness.history = harness.history[-6:]
    return {"name": name, "rows": rows}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))] if xs else 0.0


def summarise(res: dict, tg: Target) -> dict:
    # The first turn of the whole run is cold in every condition: leave it out of the rate.
    rows = [r for r in res["rows"] if r["turn"] > 0]
    miss = [r for r in rows if r["cache_n"] == 0]
    proj = [r["prompt_n"] * LIVE_PREFILL_MS_PER_TOKEN + LIVE_TTFT_FLOOR_MS for r in rows]
    return {
        "condition": res["name"], "turns": len(rows),
        "miss_rate": round(len(miss) / len(rows), 3) if rows else 0,
        # the panel alarm's measure (PANEL_KV_MISS, ZOE_PANEL_KV_MISS_TOKENS default 500)
        "reprefill_gt500_rate": round(sum(1 for r in rows if r["prompt_n"] > 500) / len(rows), 3) if rows else 0,
        "first_prompt_n_p50": pct([r["prompt_n"] for r in rows], 50), "first_prompt_n_p95": pct([r["prompt_n"] for r in rows], 95),
        "first_cache_n_p50": pct([r["cache_n"] for r in rows], 50),
        "ttft_ms_measured_p50": round(pct([r["ttft_ms"] for r in rows], 50)), "ttft_ms_measured_p95": round(pct([r["ttft_ms"] for r in rows], 95)),
        "ttft_ms_projected_live_p50": round(pct(proj, 50)), "ttft_ms_projected_live_p95": round(pct(proj, 95)),
        "mean_prompt_n": round(statistics.mean(r["prompt_n"] for r in rows)) if rows else 0,
    }


def take_brain_window_lock():
    """flock -w 3600 /tmp/zoe-brain-window.lock, in-process, and refuse the 12B night window (02:30-04:30)."""
    now = datetime.now()
    if (now.hour, now.minute) >= (2, 30) and (now.hour, now.minute) < (4, 30):
        sys.exit("refusing: 02:30-04:30 is the night window (the 12B owns the box)")
    fh = open(BRAIN_WINDOW_LOCK, "w")
    deadline = time.time() + 3600
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.time() > deadline:
                sys.exit("could not take the brain window lock within 3600 s")
            time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:11499", help="scratch llama-server (default :11499)")
    ap.add_argument("--live", action="store_true", help=f"target the live 4B at {LIVE_URL} (takes the brain window lock)")
    ap.add_argument("--turns", type=int, default=24)
    ap.add_argument("--burst", type=int, default=6, help="turns per conversation burst before it goes quiet")
    ap.add_argument("--conditions", default="isolated,interleaved,guarded")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    lock = None
    base = a.url
    if a.live:
        base = LIVE_URL
        lock = take_brain_window_lock()
    elif a.url.rstrip("/").endswith(":11434"):
        sys.exit("--url points at the live brain: use --live (it takes the brain window lock)")
    tg = Target(base, LIVE_PREFILL_MS_PER_TOKEN)
    out = []
    for k, name in enumerate(a.conditions.split(",")):
        res = run_condition(tg, name.strip(), a.turns, a.burst, seed=11 + k)   # a fresh session per condition
        out.append({**summarise(res, tg), "rows": res["rows"]})
    keys = ["condition", "turns", "miss_rate", "reprefill_gt500_rate", "first_prompt_n_p50", "first_prompt_n_p95", "first_cache_n_p50",
            "ttft_ms_projected_live_p50", "ttft_ms_projected_live_p95", "ttft_ms_measured_p50", "ttft_ms_measured_p95"]
    print("\t".join(keys))
    for o in out:
        print("\t".join(str(o[k]) for k in keys))
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    if lock:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
