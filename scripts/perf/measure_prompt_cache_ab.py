#!/usr/bin/env python3
"""Two- and three-turn prompt-cache A/B for ``ZOE_BRAIN_VOLATILE_AFTER_USER`` (#2485, B3): flag off vs on.

The question: does moving the per-turn context blocks AFTER the user's words keep more of the previous turn's prompt cached
on the NEXT turn? The sidecar elides every block from all but the newest user message (``ZOE_BRAIN_ELIDE_STALE_BLOCKS``), so
the stored copy of turn 1 changes at the block's position and llama.cpp re-prefills from there. No other instrument drove two
consecutive turns of one session through the real turn code with the flag off and then on; this does.

What is REAL and what is not
  real       the Flue seam's own prompt building: ``zoe_flue_client._run_flue_brain_streaming_turn`` run through the synthetic
             rig the tests use (``services/zoe-data/tests/flue_turn_rig.py``: every block source is a stub returning text this
             script generates; ``_request_payload`` is captured and ``_wire_version`` raises, so NOTHING is POSTed, no sidecar,
             no database, no model). The flag is set in THIS process's environment only and restored after each arm.
  port       the sidecar's stale-block elision (``stripContextBlocks``), the same Python port the test suite pins to the TypeScript.
  synthetic  the system prompt + tool block (filler of the live sizes: ~2.7k + ~0.7k tokens), the chat-template wrapper, every
             block body, the user's words, the model's replies (offline). Nothing here is household text; this repo is public.

Dry envelope: the turn is run with ``replay_isolation=True`` (the seam skips the distress / medical / restraint writes, the
register apply, the transcript / notification tails and the claim-correction take: ``zoe_flue_client.py`` ``dry=`` /
``replay_isolation`` branches), plus the rig stubs every block source. The replay line the envelope adds is removed again so
the message is the bytes a panel turn sends. Live stores are pinned to a throw-away directory before the service imports
(the ``tests/conftest.py`` list), so nothing is written even by an import-time default.

Modes
  --offline    (default) no model at all. Builds both arms' conversations, tokenizes, and computes per turn the cacheable share:
               ``cache_n`` = the longest common token prefix between what llama holds after turn k (turn k's prompt + its reply)
               and turn k+1's prompt; ``uncached`` = turn k+1's prompt tokens - ``cache_n`` (the ``first_prompt_n`` the
               ``FLUE_PROMPT_CACHE`` log row reports). Tokenizer: ``--tokenizer approx`` (default, a documented sub-word
               approximation; calibrate it with ``--calibrate-url``) or ``--tokenizer server`` (POST /tokenize on a llama-server:
               tokenization only, no slot, no generation).
  --live-slot  sends the rendered prompts to a llama-server /completion and reads ``timings.prompt_n / cache_n`` and the time
               to first token. The live brain has ONE slot (``--parallel 1``): every request overwrites the slot a household
               conversation may be using, so against :11434 it is refused unless ``--i-own-the-slot`` AND this process holds
               ``/tmp/zoe-voice-harness.lock`` AND no landing / probe runs, the slot is idle and the household has been
               quiet. Against a scratch server (any other port) the slot guard does not apply.

  python3 scripts/perf/measure_prompt_cache_ab.py --offline --pairs 30 --json /tmp/ab.json
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ZOE = REPO / "services" / "zoe-data"

HARNESS_LOCK = "/tmp/zoe-voice-harness.lock"
LIVE_URL = "http://127.0.0.1:11434"
LIVE_PORT = 11434
FLAG = "ZOE_BRAIN_VOLATILE_AFTER_USER"
# Measured live (docs/knowledge/voice-feel-switch-on.md row 5): ~1.478 ms per uncached prompt token.
MS_PER_TOKEN = 1.478
OVER_TOKENS = 800            # the "slow turn" line: FLUE_PROMPT_CACHE first_prompt_n above this (38 of 149 panel turns live)
IMPROVED_TOKENS = 300        # voice-feel-switch-on.md: improved = second-turn uncached median down by >= 300 tokens
SYSTEM_TOKENS, TOOL_TOKENS = 2721, 723   # live sizes (measure_brain_prefix_cache.Session)
APPROX_CHARS_PER_TOKEN = 3.3  # sub-word chunk length of the approximate tokenizer (calibrate with --calibrate-url)

VOCAB = ("alpha river stone quiet window copper garden planet orange silver bridge candle forest harbor island jungle kettle "
         "lantern meadow needle ribbon saddle timber velvet willow yellow zephyr anchor basket cedar dune ember fjord").split()


# ── tokenizers ────────────────────────────────────────────────────────────────────────────────────────────────────────
class ApproxTokenizer:
    """A deterministic sub-word approximation: words / numbers / punctuation / whitespace runs, with long words cut into
    ``chunk``-character pieces. Prefix-consistent (identical text prefixes give identical token prefixes up to the last whole
    piece), which is all the longest-common-prefix needs; the absolute count is an estimate, calibrated against a server."""

    name = "approx"
    _RE = re.compile(r"\s?[A-Za-z]+|\s?\d+|\s*[^\sA-Za-z\d]|\s+")

    def __init__(self, chunk: int = 4) -> None:
        self.chunk = max(1, int(chunk))

    def encode(self, text: str) -> list:
        out: list = []
        for piece in self._RE.findall(text):
            if len(piece) <= self.chunk + 1:
                out.append(piece)
            else:
                out.extend(piece[i:i + self.chunk] for i in range(0, len(piece), self.chunk))
        return out


class ServerTokenizer:
    """POST /tokenize on a llama-server. Tokenization only: no slot, no generation. Results are cached by text hash."""

    name = "server"

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._cache: dict[str, list] = {}

    def encode(self, text: str) -> list:
        key = hashlib.sha1(text.encode()).hexdigest()
        if key not in self._cache:
            req = urllib.request.Request(self.base + "/tokenize", json.dumps({"content": text}).encode(),
                                         {"Content-Type": "application/json"})
            self._cache[key] = json.loads(urllib.request.urlopen(req, timeout=30).read())["tokens"]
        return self._cache[key]


def lcp(a: list, b: list) -> int:
    """Length of the longest common prefix of two token lists."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# ── prompt rendering (what llama.cpp sees) ────────────────────────────────────────────────────────────────────────────
TURN_OPEN, TURN_CLOSE = "<start_of_turn>", "<end_of_turn>\n"


def render_prompt(system: str, history: "list[tuple[str, str]]", last_user: str) -> str:
    """System + tool block, then each past (user, model) pair with the user text as STORED (stale blocks already elided), then
    the newest user message, then the open model turn. A Gemma-style template; the exact wrapper does not move the divergence."""
    out = system
    for u, m in history:
        out += f"{TURN_OPEN}user\n{u}{TURN_CLOSE}{TURN_OPEN}model\n{m}{TURN_CLOSE}"
    return out + f"{TURN_OPEN}user\n{last_user}{TURN_CLOSE}{TURN_OPEN}model\n"


def cached_after(prompt: str, reply: str) -> str:
    """What the llama slot holds once the turn is done: the prompt as sent plus the generated reply and its close."""
    return prompt + reply + TURN_CLOSE


def turn_stats(tok, cached_text: str, prompt_text: str) -> dict:
    """The cacheable share of ``prompt_text`` given what the slot held: ``cache_n`` (common token prefix), ``uncached`` (what
    must be prefilled = FLUE_PROMPT_CACHE ``first_prompt_n``), ``total``. At least the final token is always re-evaluated."""
    p = tok.encode(prompt_text)
    c = tok.encode(cached_text)
    n = min(lcp(c, p), max(0, len(p) - 1))
    return {"total": len(p), "cache_n": n, "uncached": len(p) - n}


# ── scripted conversations (synthetic) ────────────────────────────────────────────────────────────────────────────────
@dataclass
class TurnSpec:
    words: str
    blocks: dict
    opts: dict = field(default_factory=dict)


def _words(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(VOCAB) for _ in range(max(1, n)))


def _sentence(rng: random.Random, lo: int, hi: int) -> str:
    return (_words(rng, rng.randint(lo, hi)).capitalize() + "?")


def _mem(body: str, label: str = "what you know about this user; do not mention this block") -> str:
    return f"[MEMORY CONTEXT — {label}]\n{body}\n[END MEMORY CONTEXT]"


def _recall(rng: random.Random, tokens: int) -> str:
    n = max(1, int(tokens / 1.8))            # ~1.8 tokens per vocab word + the bullet punctuation
    bullets, left = [], n
    while left > 0:
        k = min(left, rng.randint(6, 16))
        bullets.append(f"- {_words(rng, k)}")
        left -= k
    return _mem("\n".join(bullets))


def _identity(rng: random.Random) -> str:
    return f"[Today — household context]\nYou are talking to {rng.choice(VOCAB).capitalize()}, a member of this household in Testville.\n[END Today]"


def _env(rng: random.Random, card: str = "") -> dict:
    return {"persona": "Household persona: warm, brief.", "manner": "Speak plainly.", "register": "casual",
            "now_card": card or "Today: synthetic card."}


def shape_plain(rng):                       # control: identity block only, plain chat
    return [TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)}) for _ in range(3)]


def shape_recall_then_plain(rng):           # a recall turn, then plain turns
    r = rng.randint(150, 650)
    return [TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), "recall": _recall(rng, r), **_env(rng)}),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)}),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)})]


def shape_recall_every_turn(rng):           # a recall packet on every turn
    return [TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), "recall": _recall(rng, rng.randint(150, 650)),
                                             **_env(rng)}) for _ in range(3)]


def shape_write_then_recall(rng):           # a turn that triggers a pending-offer block, then a recall of it, then plain
    offer = f"[PENDING CONTACT OFFER — do not mention this block]\nAsk once: add {rng.choice(VOCAB)}?\n[END PENDING CONTACT OFFER]"
    return [TurnSpec(_sentence(rng, 6, 20), {"identity": _identity(rng), "offer": offer, **_env(rng)}),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), "recall": _recall(rng, rng.randint(150, 500)),
                                             **_env(rng)}),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)})]


def shape_now_card_change(rng):             # the now-card text changes between turns, no other block but identity
    return [TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng, f"Today: card {k} {_words(rng, 6)}")})
            for k in range(3)]


def shape_session_start(rng):               # the first turn of a session carries bridge + notes + fast-path, later turns do not
    first = {"identity": _identity(rng), "bridge": _mem(_words(rng, rng.randint(40, 120)), "where you and this user left off"),
             "notes": _mem(_words(rng, rng.randint(30, 90)), "notes from earlier conversations with this person"),
             "fastpath": _mem(_words(rng, rng.randint(10, 40)), "what was just said in this same conversation"), **_env(rng)}
    return [TurnSpec(_sentence(rng, 4, 24), first),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)}),
            TurnSpec(_sentence(rng, 4, 24), {"identity": _identity(rng), **_env(rng)})]


SHAPES = {"plain_control": shape_plain, "recall_then_plain": shape_recall_then_plain,
          "recall_every_turn": shape_recall_every_turn, "write_then_recall": shape_write_then_recall,
          "now_card_change": shape_now_card_change, "session_start_bridge": shape_session_start}


def synthetic_reply(rng: random.Random) -> str:
    return _words(rng, rng.randint(15, 60)).capitalize() + "."


def system_prefix(seed: int = 7) -> str:
    """Synthetic system prompt + tool block of the live sizes (identical in both arms: it only fixes the absolute prompt size)."""
    rng = random.Random(seed)
    n = int((SYSTEM_TOKENS + TOOL_TOKENS) * APPROX_CHARS_PER_TOKEN / 6.5)
    return "<bos>" + _words(rng, n) + "\n"


# ── arm schedule, stats, verdict (pure) ───────────────────────────────────────────────────────────────────────────────
def arm_order(pair: int) -> "tuple[str, str]":
    """ABAB: pair 0 runs off then on, pair 1 on then off, ... so a slow drift in load lands on both arms alike."""
    return ("off", "on") if pair % 2 == 0 else ("on", "off")


def pct(xs: "list[float]", p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))] if xs else 0.0


def med(xs: "list[float]") -> float:
    return float(statistics.median(xs)) if xs else 0.0


def summarise_shape(rows: "list[dict]", turn: int) -> dict:
    """``rows`` = one record per (pair, arm) carrying ``turns[k]['uncached']``. Medians / p90 of turn ``turn`` (index 1 = the
    second turn), the share over OVER_TOKENS, and the paired median saving."""
    by: dict = {"off": {}, "on": {}}
    for r in rows:
        if turn < len(r["turns"]):
            by[r["arm"]][r["pair"]] = r["turns"][turn]
    pairs = sorted(set(by["off"]) & set(by["on"]))
    off = [by["off"][p]["uncached"] for p in pairs]
    on = [by["on"][p]["uncached"] for p in pairs]
    diff = [a - b for a, b in zip(off, on)]
    share = lambda xs: round(sum(1 for x in xs if x > OVER_TOKENS) / len(xs), 3) if xs else 0.0   # noqa: E731
    return {"pairs": len(pairs), "off_median": med(off), "off_p90": pct(off, 90), "on_median": med(on), "on_p90": pct(on, 90),
            "saved_median_tokens": med(diff), "saved_p90_tokens": pct(diff, 90), "saved_median_s": round(med(diff) * MS_PER_TOKEN / 1000, 3),
            "over_off": share(off), "over_on": share(on), "on_better_pairs": sum(1 for d in diff if d > 0),
            "on_worse_pairs": sum(1 for d in diff if d < 0)}


def reply_shape_changed(a: "dict | None", b: "dict | None", tol: float = 0.30) -> bool:
    """True when the two arms' replies differ in SHAPE: one is empty, or the lengths differ by more than ``tol`` of the longer.
    (A hash difference alone is expected whenever the prompt bytes differ; the said-vs-did call belongs to the replay gate.)"""
    if a is None or b is None:
        return False
    la, lb = a["len"], b["len"]
    if (la == 0) != (lb == 0):
        return True
    return bool(max(la, lb) and abs(la - lb) / max(la, lb) > tol)


def verdict(per_shape: "dict[str, dict]", live: bool, shape_changes: int = 0) -> str:
    """One line against the thresholds in voice-feel-switch-on.md. Improved = a second-turn uncached median down >= 300 tokens.
    Regressed (live only) = a reply whose shape changed. Offline there is no reply, so a regression is not assessed."""
    best_name, best = max(per_shape.items(), key=lambda kv: kv[1]["saved_median_tokens"])
    worst_name, worst = min(per_shape.items(), key=lambda kv: kv[1]["saved_median_tokens"])
    improved = [n for n, s in per_shape.items() if s["saved_median_tokens"] >= IMPROVED_TOKENS]
    got = (f"IMPROVED in {', '.join(improved)}" if improved else
           f"NOT improved: best shape {best_name} saves {best['saved_median_tokens']:.0f} tokens at the median "
           f"(threshold {IMPROVED_TOKENS})")
    if worst["saved_median_tokens"] < 0:
        got += f"; WORSE in {worst_name} ({worst['saved_median_tokens']:.0f})"
    reg = (f"; reply-shape changes: {shape_changes} (a changed shape is not a said-vs-did verdict: run the replay gate)" if live
           else "; regression NOT assessed (offline: no model, so no reply to compare)")
    return got + reg


# ── guards for the live-slot mode (pure decision + IO collectors) ─────────────────────────────────────────────────────
def live_slot_refusals(*, is_live_brain: bool, own_slot: bool, perf_env: bool, lock_held: bool, probe_running: bool,
                       slot_busy: "bool | None", household_idle_s: "float | None", min_idle_s: float = 600.0) -> "list[str]":
    """Reasons the live-slot mode must refuse (empty = go). A scratch server (not the live brain) only needs the lock."""
    why: list[str] = []
    if not lock_held:
        why.append(f"this process does not hold {HARNESS_LOCK}")
    if not is_live_brain:
        return why
    if not own_slot:
        why.append("--i-own-the-slot not given (the live brain has ONE slot: this run overwrites the household's cached conversation)")
    if not perf_env:
        why.append("ZOE_PERF=1 not set")
    if probe_running:
        why.append("a landing / voice probe is running")
    if slot_busy is None:
        why.append("could not read /slots (will not send blind)")
    elif slot_busy:
        why.append("the slot is processing a request")
    if household_idle_s is None:
        why.append("could not read the time of the last brain turn (will not send blind)")
    elif household_idle_s < min_idle_s:
        why.append(f"a brain turn ran {household_idle_s:.0f}s ago (< {min_idle_s:.0f}s quiet needed)")
    return why


def collect_guard_inputs(base: str) -> dict:
    probe = subprocess.run(["pgrep", "-f", r"[v]oice_regression_probe\.py|[m]easure_voice\.py|[m]easure_tts\.py|[m]easure_cold_first_turn\.py|"
                                          r"^(/bin/)?[b]ash .*/(land_queue|land_voice_pr|docs_merge_chain)\.sh"],
                           capture_output=True, text=True).stdout.strip()
    slot_busy = None
    try:
        with urllib.request.urlopen(base + "/slots", timeout=3) as r:
            slot_busy = any(s.get("is_processing") for s in json.loads(r.read()))
    except Exception:  # noqa: BLE001
        pass
    idle = None
    log = Path(os.environ.get("ZOE_LOG_DIR", str(Path.home() / ".zoe-logs"))) / "zoe-data.app.log"
    try:   # the mtime of the app log only (never its content): the last time anything was logged by the service
        idle = time.time() - log.stat().st_mtime
    except OSError:
        pass
    return {"probe_running": bool(probe), "slot_busy": slot_busy, "household_idle_s": idle}


def take_harness_lock():
    fh = open(HARNESS_LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


# ── the lab process: real prompt building through the synthetic rig ───────────────────────────────────────────────────
class _Patcher:
    """The two pytest.MonkeyPatch calls the rig makes (setattr / delenv), without needing pytest; ``undo`` restores all."""

    def __init__(self) -> None:
        self._saved: list = []

    def setattr(self, obj, name, value) -> None:
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def delenv(self, name, raising=True) -> None:
        self._saved.append((os.environ, name, os.environ.get(name)))
        os.environ.pop(name, None)

    def undo(self) -> None:
        for obj, name, old in reversed(self._saved):
            if obj is os.environ:
                os.environ.pop(name, None) if old is None else os.environ.__setitem__(name, old)
            else:
                setattr(obj, name, old)
        self._saved.clear()


def pin_live_stores(tmp: str) -> None:
    """The tests/conftest.py throw-away pins for stores read at import, BEFORE any zoe-data module is imported."""
    pins = {"MEMPALACE_DATA_DIR": "mempalace", "ZOE_VOICE_STT_LOG": "voice_stt.jsonl", "ZOE_LOG_DIR": "logs",
            "ZOE_WARM_SPEAKER_STATE": "warm-speaker.json", "ZOE_TIMER_STORE": "timers.json",
            "ZOE_ROUTER_HEAD_LOG": "router_head_shadow.jsonl", "ZOE_INTENT_MISS_LOG": "intent-misses.jsonl",
            "ZOE_MEMORY_REJECT_LEDGER": "memory-reject-ledger.json", "ZOE_PIPELINE_STORE_PATH": "pipeline.jsonl"}
    for var, leaf in pins.items():
        os.environ[var] = os.path.join(tmp, leaf)
    os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
    os.environ["ZOE_BRAIN_SLOT_GUARD"] = "off"
    os.environ["ZOE_REPLY_POLISH"] = "off"


_ENV_HEAD_RE = re.compile(r"^ zoe-[a-z][a-z-]*:[^\n]*\n")


def strip_envelope_head(msg: str, replay_prefix: str = "") -> str:
    """The text the model reads: the replay line (added by the dry envelope) and the sidecar-parsed envelope lines removed."""
    if replay_prefix and msg.startswith(replay_prefix):
        msg = msg[msg.index("\n") + 1:]
    while True:
        m = _ENV_HEAD_RE.match(msg)
        if not m:
            return msg
        msg = msg[m.end():]


class Lab:
    """Imports the service's turn code once, then builds wire messages for any arm."""

    def __init__(self, tests_dir: Path = ZOE / "tests", pin: bool = True) -> None:
        if pin:    # a pytest run has already pinned these through tests/conftest.py
            pin_live_stores(tempfile.mkdtemp(prefix="zoe-ab-lab-"))
        for p in (str(ZOE), str(tests_dir)):
            if p not in sys.path:
                sys.path.insert(0, p)
        import flue_turn_rig as rig
        import zoe_flue_client as zfc

        self.rig, self.zfc = rig, zfc
        self.replay_prefix = getattr(zfc, "_REPLAY_ENVELOPE_PREFIX", "") + "1\n"
        # strip_context_blocks: the Python port of the sidecar's elision lives with the tests that pin it to the .ts source.
        try:
            from test_brain_cache_stability import strip_context_blocks
        except Exception:  # noqa: BLE001 - run without pytest on the path: keep a clear failure rather than a silent no-op
            raise SystemExit("cannot import the stripContextBlocks port from services/zoe-data/tests/test_brain_cache_stability.py")
        self.strip_blocks = strip_context_blocks

    async def wires(self, turns: "list[TurnSpec]", flag_on: bool, session: str) -> "list[str]":
        """The model-visible user message of each turn, built by the real seam with the flag set in this process only."""
        old = os.environ.get(FLAG)
        os.environ[FLAG] = "1" if flag_on else "0"
        out: list[str] = []
        patcher = _Patcher()
        try:
            for t in turns:
                cap = self.rig.install(patcher, dict(t.blocks), hop_placement=t.opts.get("hop_placement", "block"))
                msg = await self.rig.outbound(cap, t.words, session_id=session, uid="ab-lab-member", replay_isolation=True)
                out.append(strip_envelope_head(msg, self.replay_prefix))
        finally:
            patcher.undo()
            if old is None:
                os.environ.pop(FLAG, None)
            else:
                os.environ[FLAG] = old
        return out


def conversation_prompts(system: str, wires: "list[str]", replies: "list[str]", strip_blocks) -> "list[tuple[str, str]]":
    """For each turn: (prompt as sent, what the slot holds after it). History carries older user messages block-elided."""
    out = []
    for k, w in enumerate(wires):
        history = [(strip_blocks(wires[j]), replies[j]) for j in range(k)]
        p = render_prompt(system, history, w)
        out.append((p, cached_after(p, replies[k])))
    return out


# ── live-slot mode ────────────────────────────────────────────────────────────────────────────────────────────────────
def complete(base: str, prompt: str, n_predict: int, slot: int = 0) -> dict:
    body = {"prompt": prompt, "n_predict": n_predict, "cache_prompt": True, "temperature": 0.0, "stream": True, "id_slot": slot}
    req = urllib.request.Request(base + "/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0, ttft, text, last = time.monotonic(), None, "", {}
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
    return {"prompt_n": int(t.get("prompt_n", 0)), "cache_n": int(t.get("cache_n", 0)),
            "ttft_ms": ttft if ttft is not None else (time.monotonic() - t0) * 1000.0,
            "total_ms": (time.monotonic() - t0) * 1000.0, "text": text}


async def run_conversation(lab: Lab, spec: "list[TurnSpec]", flag_on: bool, session: str, system: str, rng_seed: int,
                           *, tok, live_base: str = "", n_predict: int = 48) -> dict:
    """One conversation under one arm. Offline: scripted replies, token stats. Live: real replies and timings."""
    wires = await lab.wires(spec, flag_on, session)
    rrng = random.Random(rng_seed)
    scripted = [synthetic_reply(rrng) for _ in wires]
    turns: list[dict] = []
    if not live_base:
        pr = conversation_prompts(system, wires, scripted, lab.strip_blocks)
        for k in range(len(wires)):
            if k == 0:
                turns.append({"total": len(tok.encode(pr[0][0])), "cache_n": 0, "uncached": len(tok.encode(pr[0][0]))})
            else:
                turns.append(turn_stats(tok, pr[k - 1][1], pr[k][0]))
            turns[-1]["reply"] = {"len": len(scripted[k]), "sha": hashlib.sha1(scripted[k].encode()).hexdigest()[:8]}
        return {"turns": turns}
    replies: list[str] = []
    for k, w in enumerate(wires):
        history = [(lab.strip_blocks(wires[j]), replies[j]) for j in range(k)]
        res = await asyncio.to_thread(complete, live_base, render_prompt(system, history, w), n_predict)
        replies.append(res["text"].strip() or "ok")
        turns.append({"total": res["prompt_n"] + res["cache_n"], "cache_n": res["cache_n"], "uncached": res["prompt_n"],
                      "ttft_ms": round(res["ttft_ms"]), "total_ms": round(res["total_ms"]),
                      "reply": {"len": len(replies[-1]), "sha": hashlib.sha1(replies[-1].encode()).hexdigest()[:8]}})
    return {"turns": turns}


async def run_all(lab: Lab, args, tok, live_base: str = "") -> dict:
    system = system_prefix()
    out: dict = {}
    for name, make in SHAPES.items():
        rows = []
        for pair in range(args.pairs):
            spec = make(random.Random(f"{name}:{pair}"))
            for arm in arm_order(pair):
                r = await run_conversation(lab, spec, arm == "on", f"voice-panel-ab-{name}-{pair}-{arm}", system,
                                           rng_seed=pair * 7 + 1, tok=tok, live_base=live_base)
                rows.append({"pair": pair, "arm": arm, **r})
        out[name] = rows
    return out


def report(results: dict, live: bool) -> "tuple[str, dict]":
    per_shape2: dict = {}
    lines = []
    hdr = (f"{'shape':<22} {'turn':>4} {'pairs':>5} | {'off med':>8} {'off p90':>8} | {'on med':>8} {'on p90':>8} | "
           f"{'saved med':>9} {'saved s':>8} | {'>800 off':>8} {'>800 on':>8} | {'on better/worse':>15}")
    lines += [hdr, "-" * len(hdr)]
    changes = 0
    for name, rows in results.items():
        s2 = summarise_shape(rows, 1)
        per_shape2[name] = s2
        for turn in (1, 2):
            s = s2 if turn == 1 else summarise_shape(rows, 2)
            lines.append(f"{name:<22} {turn + 1:>4} {s['pairs']:>5} | {s['off_median']:>8.0f} {s['off_p90']:>8.0f} | {s['on_median']:>8.0f} "
                         f"{s['on_p90']:>8.0f} | {s['saved_median_tokens']:>9.0f} {s['saved_median_s']:>8.3f} | {s['over_off']:>8.0%} "
                         f"{s['over_on']:>8.0%} | {s['on_better_pairs']:>7}/{s['on_worse_pairs']:<7}")
        if live:
            by = {"off": {}, "on": {}}
            for r in rows:
                by[r["arm"]][r["pair"]] = r["turns"]
            for p in by["off"]:
                for k in range(min(len(by["off"][p]), len(by["on"].get(p, [])))):
                    changes += reply_shape_changed(by["off"][p][k]["reply"], by["on"][p][k]["reply"])
    reply_col = "reply shape changed: " + (f"{changes} turn(s) across arms" if live else "n/a offline (replies are scripted, identical in both arms by construction)")
    return "\n".join(lines + [reply_col, "VERDICT: " + verdict(per_shape2, live, changes)]), per_shape2


def calibrate(url: str, tok: ApproxTokenizer, system: str) -> dict:
    """Approximate vs server token counts on a few generated texts: the ratio to scale the approximation by."""
    srv = ServerTokenizer(url)
    rng = random.Random(3)
    samples = [system[: 4000], _recall(rng, 560), _words(rng, 60), _sentence(rng, 10, 20), _identity(rng)]
    pairs = [(len(tok.encode(s)), len(srv.encode(s))) for s in samples]
    return {"approx": [a for a, _ in pairs], "server": [b for _, b in pairs], "ratio_server_over_approx": round(sum(b for _, b in pairs) / sum(a for a, _ in pairs), 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="no model: token-level cacheable share (default)")
    mode.add_argument("--live-slot", action="store_true", help="send to a llama-server (guarded; see the module doc)")
    ap.add_argument("--pairs", type=int, default=30, help="conversations per shape per arm (default 30)")
    ap.add_argument("--tokenizer", choices=("approx", "server"), default="approx")
    ap.add_argument("--tokenize-url", default=LIVE_URL, help="server for --tokenizer server / --calibrate-url (tokenize only)")
    ap.add_argument("--calibrate-url", default="", help="print approx-vs-server token counts for a few texts, then continue")
    ap.add_argument("--url", default=LIVE_URL, help="llama-server for --live-slot (a scratch server avoids the slot guard)")
    ap.add_argument("--i-own-the-slot", action="store_true", help="live brain only: I accept the household's cached prompt is overwritten")
    ap.add_argument("--min-idle-s", type=float, default=600.0, help="live brain only: household quiet time required")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    tok = ServerTokenizer(a.tokenize_url) if a.tokenizer == "server" else ApproxTokenizer()
    live_base = ""
    lock = None
    if a.live_slot:
        live_base = a.url.rstrip("/")
        lock = take_harness_lock()
        port = int(live_base.rsplit(":", 1)[-1]) if live_base.rsplit(":", 1)[-1].isdigit() else 0
        g = collect_guard_inputs(live_base) if port == LIVE_PORT else {"probe_running": False, "slot_busy": False, "household_idle_s": 1e9}
        why = live_slot_refusals(is_live_brain=port == LIVE_PORT, own_slot=a.i_own_the_slot, perf_env=os.environ.get("ZOE_PERF") == "1",
                                 lock_held=lock is not None, probe_running=g["probe_running"], slot_busy=g["slot_busy"],
                                 household_idle_s=g["household_idle_s"], min_idle_s=a.min_idle_s)
        if why:
            print("refusing --live-slot:\n  - " + "\n  - ".join(why), file=sys.stderr)
            return 2
        tok = ApproxTokenizer()     # live mode reads the server's own prompt_n / cache_n; the tokenizer is not used
    if a.calibrate_url:
        print("calibration:", json.dumps(calibrate(a.calibrate_url, ApproxTokenizer(), system_prefix())))
    lab = Lab()
    results = asyncio.run(run_all(lab, a, tok, live_base))
    text, summary = report(results, bool(live_base))
    print(f"prompt-cache A/B ({'live-slot ' + live_base if live_base else 'offline, tokenizer=' + tok.name}), {a.pairs} pairs per shape, "
          f"{MS_PER_TOKEN} ms/token, flag {FLAG} set in this process only\n")
    print(text)
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": results}, indent=1))
    if lock:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
