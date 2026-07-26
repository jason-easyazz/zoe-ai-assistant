#!/usr/bin/env python3
"""Router self-training MINER + LABELER (lane A of the self-training loop).

Turns REAL family traffic into labelled router training examples that teach the
router its OWN measured mistakes — not more templates.

    shadow log (what the router did)  ──┐
                                        ├─► hard cases ─► local Gemma oracle ─► candidate_<stamp>.jsonl
    chat_messages (what was said)     ──┘                (grammar-constrained)

WHY A JOIN. The shadow log (`services/zoe-data/data/router_head_shadow.jsonl`)
stores only a 12-hex utterance HASH (`utt`) — deliberate privacy design. Training
needs the words. Two ways to get them, both implemented:

  FORWARD    `ZOE_ROUTER_SHADOW_TEXT=1` makes the router also write `utt_text`
             into the shadow-log file (opt-in, default OFF, never leaves the box).
             Records carrying `utt_text` are used directly.
  BOOTSTRAP  For rounds mined from history written BEFORE that flag existed, the
             raw text is recovered by re-hashing every `chat_messages.content`
             with the SAME function the router uses (sha256(text)[:12]) and
             joining on the hash. Verified: 33/60 distinct shadow utterances
             recovered on the first real run.

MINING REASONS (the model's measured mistakes) — see README.md:
  disagreement   two-stage picked a different domain than the live route
  abstention     two-stage gated/abstained but the live route WAS a tool
  chat-negative  the live route was chat (reinforcement + false-positive fixes)

THE ORACLE is the live local Gemma brain (:11434), asked with a JSON-schema
(grammar) constrained response so it must emit a valid call or an explicit
no-tool. Nothing is sent off-box. Calls are serial and /slots-gated so the miner
never fights a live voice turn.

HARD SAFETY RAIL: every candidate is checked against the FROZEN eval corpus
(labs/needle-benchmark/corpus.jsonl). One match ABORTS the run and writes
nothing — training on the promotion gate's own corpus would silently destroy the
whole safety property of the loop.

Run:
    python3 labs/router-selftrain/mine_candidates.py            # mine + label
    python3 labs/router-selftrain/mine_candidates.py --dry-run  # mine, don't label
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "services", "zoe-data"))

# The router's own static policy — never fork it, import it.
from router_two_stage import DOMAIN_TOOLS, TOOL_ARGS, TOOL_DOMAIN  # noqa: E402

# The router owns the shadow-log rotation scheme; import its segment contract
# rather than re-deriving the `.1`/`.2` naming here (a fork would silently stop
# matching the writer the day the scheme changes).
from semantic_router import shadow_log_segments  # noqa: E402

# ── Paths ───────────────────────────────────────────────────────────────────
# Same env var the router writes through, so a miner run in a worktree can point
# at the LIVE checkout's shadow log (the log is runtime data, never committed).
SHADOW_LOG = os.environ.get(
    "ZOE_ROUTER_HEAD_LOG",
    os.path.join(REPO, "services", "zoe-data", "data", "router_head_shadow.jsonl"),
)
HOLDOUT_CORPUS = os.path.join(REPO, "labs", "needle-benchmark", "corpus.jsonl")
TOOLS_JSON = os.path.join(REPO, "labs", "functiongemma-finetune", "zoe_tools.json")
TRAIN_DIR = os.path.join(REPO, "labs", "functiongemma-finetune", "data")
OUT_DIR = os.path.join(REPO, "data", "router_selftrain")

# ── Caps (one bad week must not be able to swamp the corpus) ────────────────
MAX_CANDIDATES = 400
MAX_CHAT_NEGATIVES = 100

GEMMA_URL = os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1")
SLOTS_URL = GEMMA_URL.rstrip("/").removesuffix("/v1") + "/slots"
BRAIN_TIMEOUT_S = 60.0
BRAIN_PAUSE_S = 0.35          # gentle serial pacing between oracle calls
SLOT_WAIT_S = 2.0             # how long to back off when the brain is busy
SLOT_MAX_WAITS = 30

REASONS = ("disagreement", "abstention", "chat-negative")


class HoldoutViolation(RuntimeError):
    """A candidate collided with the frozen eval corpus. The run must abort."""


@dataclass
class Candidate:
    text: str
    reason: str
    domain: str                       # observed live route ('chat' for negatives)
    tool: Optional[str] = None        # filled by the oracle
    args: dict = field(default_factory=dict)

    def as_train_row(self) -> dict:
        """The EXACT shape train_lora.py consumes (see data/train.jsonl)."""
        return {
            "text": self.text,
            "tool": self.tool,
            "args": self.args,
            "source": f"selftrain-{self.reason}",
        }


# ── Hashing + normalisation ─────────────────────────────────────────────────
def utt_hash(text: str) -> str:
    """MUST stay byte-identical to semantic_router's `utt` field."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Loose key for dedup + the held-out guard: case/punctuation/space blind."""
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


# ── The held-out guard (the loop's integrity property) ──────────────────────
def load_holdout(path: str = HOLDOUT_CORPUS) -> set[str]:
    out: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.add(normalize(json.loads(line).get("text", "")))
    out.discard("")
    return out


def assert_held_out(candidates: Iterable[Candidate], holdout: set[str]) -> None:
    """ABORT LOUDLY on any overlap with the frozen eval corpus.

    This is not a filter — a collision means the mining window is contaminated
    (or someone replayed the corpus through the live router), and silently
    dropping the rows would hide that. The promotion gate is only meaningful if
    the model has never seen its corpus.
    """
    hits = sorted({c.text for c in candidates if normalize(c.text) in holdout})
    if hits:
        raise HoldoutViolation(
            f"HELD-OUT GUARD TRIPPED: {len(hits)} candidate(s) collide with the "
            f"frozen eval corpus ({HOLDOUT_CORPUS}). Wrote NOTHING. "
            f"Training on the promotion gate's own corpus would destroy the "
            f"safety property of the whole loop. Offending: {hits[:5]}"
        )


# ── Sources ─────────────────────────────────────────────────────────────────
def load_shadow(path: str = SHADOW_LOG, since_ts: float = 0.0) -> list[dict]:
    """Load shadow records across ALL rotated segments, oldest first.

    The router rotates the shadow log once it hits its size cap, moving history
    into `<log>.1`, `<log>.2`, … Reading only `<log>` would silently mine just
    the newest slice and quietly shrink the training window — so walk every
    segment. `shadow_log_segments` is the router's own contract for this, so the
    reader can't drift from the writer's rotation scheme.
    """
    recs: list[dict] = []
    for segment in shadow_log_segments(path):
        with open(segment, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if float(rec.get("ts") or 0.0) >= since_ts:
                    recs.append(rec)
    return recs


async def _fetch_chat_texts() -> dict[str, str]:
    """hash → raw text for every user turn in chat_messages (the BOOTSTRAP side
    of the join: chat_messages holds the verbatim text of live voice/chat turns)."""
    import asyncpg

    url = os.environ.get("POSTGRES_URL", "")
    if not url:
        # zoe-data's env file is runtime config (never committed), so a miner run
        # from a worktree reads the live checkout's copy.
        candidates = [
            os.path.join(REPO, "services", "zoe-data", ".env"),
            "/home/zoe/assistant/services/zoe-data/.env",
        ]
        for env in candidates:
            if not os.path.exists(env):
                continue
            with open(env, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("POSTGRES_URL="):
                        url = line.split("=", 1)[1].strip()
                        break
            if url:
                break
    if not url:
        raise RuntimeError(
            "POSTGRES_URL not set, and no zoe-data/.env carrying it was found. "
            "Export POSTGRES_URL or run from a checkout with the runtime env file."
        )

    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT content FROM chat_messages WHERE role = 'user' "
            "AND content IS NOT NULL AND content <> ''"
        )
    finally:
        await conn.close()
    return {utt_hash(r["content"]): r["content"] for r in rows}


def hash_to_text(shadow: list[dict], chat_texts: dict[str, str]) -> dict[str, str]:
    """Resolve every shadow record's hash to raw text.

    FORWARD records (written under ZOE_ROUTER_SHADOW_TEXT=1) carry `utt_text` and
    need no join; everything else falls back to the chat_messages hash join.
    """
    out = dict(chat_texts)
    for rec in shadow:
        text = rec.get("utt_text")
        if text:
            out[rec.get("utt") or utt_hash(text)] = text
    return out


# ── Mining ──────────────────────────────────────────────────────────────────
def _is_tool_domain(domain: Optional[str]) -> bool:
    """A domain we can actually build a training label for.

    Must be a real (non-chat) domain that unlocks at least one concrete tool —
    a domain with an empty tool list can never satisfy the oracle-agreement check
    in `label()`, so mining it would silently drop every example instead of
    producing training data.
    """
    return bool(domain) and domain != "chat" and bool(DOMAIN_TOOLS.get(domain))


def baseline_route(rec: dict) -> Optional[str]:
    """The INDEPENDENT route the two-stage decision must be judged against.

    In `active` mode the two-stage decision *is* the route, so the record's
    `actual_routed` is just an echo of `two_stage_domain` — comparing them is a
    tautology, and a wrongly-abstaining router would look like a chat turn. The
    ground truth there is `similarity_routed`, the baseline the two-stage
    pre-empted. In `shadow2` the two-stage doesn't route, so `actual_routed` IS
    the independent baseline.

    Returns None for legacy `active` records written before `similarity_routed`
    existed: their baseline is unrecoverable, so they must not be mined for tool
    reasons (see `mine`).
    """
    if rec.get("mode") == "active":
        return rec.get("similarity_routed")
    return rec.get("actual_routed")


def mine(shadow: list[dict], texts: dict[str, str],
         max_chat_negatives: int = MAX_CHAT_NEGATIVES,
         max_total: int = MAX_CANDIDATES) -> list[Candidate]:
    """Pick the hard cases out of the shadow log.

    Only two-stage records (mode shadow2/active, i.e. carrying a two-stage
    decision) can be mined — a head-shadow-only record has no router decision to
    disagree with. Tool reasons are judged against `baseline_route`, never against
    the two-stage's own output.
    """
    tool_cands: list[Candidate] = []
    chat_fp: list[Candidate] = []      # live-chat but two-stage fired a tool
    chat_agree: list[Candidate] = []   # both said chat (reinforcement)
    seen: set[str] = set()

    for rec in shadow:
        if rec.get("mode") not in ("shadow2", "active"):
            continue
        text = texts.get(rec.get("utt") or "")
        if not text:
            continue                    # hash never resolved — nothing to train on
        key = normalize(text)
        if not key or key in seen:
            continue

        actual = rec.get("actual_routed")
        base = baseline_route(rec)          # None on legacy active records
        ts_domain = rec.get("two_stage_domain")
        abstained = bool(rec.get("gated") or rec.get("failed")
                         or rec.get("two_stage_tool") is None)

        if _is_tool_domain(base):
            # The baseline router landed on a real tool domain — that is the
            # ground truth the two-stage should have reproduced.
            if abstained:
                reason = "abstention"       # should have caught this, didn't
            elif ts_domain != base:
                reason = "disagreement"     # caught it, chose the wrong domain
            else:
                continue                    # agreed and fired — nothing to learn
            seen.add(key)
            tool_cands.append(Candidate(text=text, reason=reason, domain=base))

        elif actual == "chat":
            seen.add(key)
            cand = Candidate(text=text, reason="chat-negative", domain="chat")
            # A tool-firing two-stage on a chat turn is a measured FALSE POSITIVE
            # (a real mistake); both-said-chat is plain reinforcement. Keep the
            # mistakes first when the cap bites.
            (chat_agree if (abstained or ts_domain == "chat") else chat_fp).append(cand)

    chat_cands = (chat_fp + chat_agree)[:max_chat_negatives]
    # Tool mistakes are the point of the exercise — never let negatives push them out.
    return (tool_cands + chat_cands)[:max_total]


# ── Dedup ───────────────────────────────────────────────────────────────────
def load_existing_texts(train_dir: str = TRAIN_DIR) -> set[str]:
    """Normalised text of every example already in the training sets."""
    out: set[str] = set()
    if not os.path.isdir(train_dir):
        return out
    for name in sorted(os.listdir(train_dir)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(train_dir, name), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.add(normalize(json.loads(line).get("text", "")))
                except ValueError:
                    continue
    out.discard("")
    return out


def dedup(candidates: list[Candidate], existing: set[str]) -> list[Candidate]:
    """Drop anything already in the training sets, and any repeat within the round."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = normalize(c.text)
        if not key or key in existing or key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ── The oracle (local brain, grammar-constrained) ───────────────────────────
def _tool_specs() -> dict[str, dict]:
    with open(TOOLS_JSON, encoding="utf-8") as fh:
        return {t["name"]: t for t in json.load(fh)}


def label_schema(tool_names: list[str]) -> dict:
    """A JSON schema llama-server converts to a grammar: the model can ONLY emit
    a legal tool name (or the explicit no-tool sentinel) plus an args object."""
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": sorted(tool_names) + ["none"]},
            "args": {"type": "object"},
        },
        "required": ["tool", "args"],
        "additionalProperties": False,
    }


def _prompt(text: str, specs: dict[str, dict], names: list[str]) -> str:
    """Deliberately NO hint about what the live router did.

    The oracle is a genuinely independent second opinion; `label()` then keeps a
    row only where the oracle and the live route AGREE. Telling the oracle the
    live domain would just get it to rubber-stamp the live router's own misroutes
    — the first dry-run turned up a real one (a smart-home utterance that the live
    similarity router had sent to the `timers` domain) — and we would train on them.
    """
    lines = [
        f"- {n}: {specs[n]['description']}  args: "
        f"{json.dumps(list((specs[n].get('parameters') or {}).keys()))}"
        for n in names if n in specs
    ]
    return (
        "You label training data for a voice assistant's tool router.\n"
        "Given the user's utterance, emit the single correct tool call, filling "
        "the arguments from the utterance only (never invent values).\n"
        "Emit tool 'none' with empty args if no tool call is warranted — "
        "chit-chat, questions about yourself, and follow-ups all take 'none'.\n\n"
        "TOOLS:\n" + "\n".join(lines) +
        f"\n\nUSER UTTERANCE: {text}\n"
    )


def _wait_for_slot(http) -> None:
    """Never fight a live voice turn: back off while the brain is processing."""
    for _ in range(SLOT_MAX_WAITS):
        try:
            slots = http.get(SLOTS_URL, timeout=5.0).json()
        except Exception:
            return  # /slots unavailable — proceed, the call itself will queue
        if not any(s.get("is_processing") for s in slots):
            return
        time.sleep(SLOT_WAIT_S)


def make_brain_labeler() -> Callable[[str, list[str]], Optional[dict]]:
    """Returns (text, tool_names) -> {"tool":..., "args":...} | None."""
    import httpx

    specs = _tool_specs()
    http = httpx.Client(timeout=BRAIN_TIMEOUT_S)

    def call(text: str, names: list[str]) -> Optional[dict]:
        _wait_for_slot(http)
        body = {
            "model": "gemma",
            "messages": [{"role": "user",
                          "content": _prompt(text, specs, names)}],
            "temperature": 0.0,
            "max_tokens": 256,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "router_label",
                                "schema": label_schema(names)},
            },
        }
        try:
            resp = http.post(f"{GEMMA_URL}/chat/completions", json=body)
            resp.raise_for_status()
            out = json.loads(resp.json()["choices"][0]["message"]["content"])
        except Exception as exc:  # a label we can't get cleanly is skipped, never guessed
            print(f"  ! oracle failed ({type(exc).__name__}) — skipping: {text[:50]}",
                  file=sys.stderr)
            return None
        finally:
            time.sleep(BRAIN_PAUSE_S)
        return out if isinstance(out, dict) else None

    return call


def label(candidates: list[Candidate],
          labeler: Callable[[str, list[str]], Optional[dict]]) -> list[Candidate]:
    """Ask the oracle for the gold call, then keep ONLY two-source agreement.

    The oracle always sees the FULL tool menu and is told nothing about the live
    route, so its answer is independent. A row survives only when the oracle and
    the live system agree:

      tool reasons   oracle names a tool whose domain == the live route's domain
                     → gold = that call. (Oracle says 'none', or names an
                     off-domain tool → the live route was itself suspect; DROP.)
      chat-negative  oracle ALSO declines to call a tool → gold = no tool.
                     (Oracle wants a tool → not a clean negative; DROP.)

    Anything the oracle cannot answer cleanly is dropped. A wrong label is far
    worse than a missing one: this corpus trains an autonomous loop.
    """
    menu = sorted(TOOL_DOMAIN)
    out: list[Candidate] = []
    for c in candidates:
        got = labeler(c.text, menu)
        if not isinstance(got, dict):
            continue
        tool = got.get("tool")
        args = got.get("args")
        if not isinstance(args, dict):
            args = {}

        if tool in (None, "none", ""):
            if c.reason != "chat-negative":
                continue          # oracle won't complete the call → live route suspect
            c.tool, c.args = None, {}
            out.append(c)
            continue

        if c.reason == "chat-negative":
            continue              # oracle wants a tool → not a clean negative
        if TOOL_DOMAIN.get(tool) != c.domain:
            continue              # oracle disagrees with the live domain → drop
        legal = TOOL_ARGS.get(tool, set())
        c.tool = tool
        c.args = {k: v for k, v in args.items() if k in legal}
        out.append(c)
    return out


# ── Output ──────────────────────────────────────────────────────────────────
def write_outputs(candidates: list[Candidate], meta: dict,
                  out_dir: str = OUT_DIR, stamp: Optional[str] = None) -> tuple[str, str]:
    stamp = stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(out_dir, exist_ok=True)
    jsonl = os.path.join(out_dir, f"candidate_{stamp}.jsonl")
    metaf = os.path.join(out_dir, f"candidate_{stamp}.meta.json")
    with open(jsonl, "w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(json.dumps(c.as_train_row(), sort_keys=True) + "\n")
    with open(metaf, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return jsonl, metaf


# ── Entry point ─────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    import asyncio

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", type=float, default=0.0,
                    help="only mine shadow records with ts >= this unix time")
    ap.add_argument("--max", type=int, default=MAX_CANDIDATES)
    ap.add_argument("--max-chat-negatives", type=int, default=MAX_CHAT_NEGATIVES)
    ap.add_argument("--dry-run", action="store_true",
                    help="mine + guard + dedup, but do not call the brain or write")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args(argv)

    shadow = load_shadow(since_ts=args.since)
    print(f"shadow records in window: {len(shadow)}")

    chat_texts = asyncio.run(_fetch_chat_texts())
    texts = hash_to_text(shadow, chat_texts)
    hashes = {r.get("utt") for r in shadow if r.get("utt")}
    resolved = len(hashes & set(texts))
    print(f"hash join: {resolved}/{len(hashes)} distinct utterances resolved "
          f"({len(chat_texts)} distinct chat_messages texts)")

    cands = mine(shadow, texts, args.max_chat_negatives, args.max)
    print(f"mined: {len(cands)}")

    holdout = load_holdout()
    assert_held_out(cands, holdout)      # raises → nothing written, by design
    print(f"held-out guard: PASS ({len(holdout)} frozen corpus entries, 0 collisions)")

    cands = dedup(cands, load_existing_texts())
    by_reason_mined = {r: sum(1 for c in cands if c.reason == r) for r in REASONS}
    print(f"after dedup: {len(cands)}  {by_reason_mined}")

    if args.dry_run:
        for c in cands[:15]:
            print(f"  [{c.reason}/{c.domain}] {c.text[:70]}")
        return 0

    labelled = label(cands, make_brain_labeler())
    assert_held_out(labelled, holdout)   # belt and braces before anything is written

    meta = {
        "stamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "shadow_log": SHADOW_LOG,
        "window": {
            "since_ts": args.since,
            "records": len(shadow),
            "first_ts": min((r.get("ts", 0) for r in shadow), default=None),
            "last_ts": max((r.get("ts", 0) for r in shadow), default=None),
        },
        "hash_join": {"resolved": resolved, "distinct_shadow_utts": len(hashes),
                      "distinct_chat_texts": len(chat_texts)},
        "held_out_guard": {"result": "pass", "corpus": HOLDOUT_CORPUS,
                           "entries": len(holdout), "collisions": 0},
        "counts": {
            "mined": by_reason_mined,
            "labelled": {r: sum(1 for c in labelled if c.reason == r) for r in REASONS},
            "total": len(labelled),
        },
        "caps": {"max_candidates": args.max,
                 "max_chat_negatives": args.max_chat_negatives},
        "oracle": {"url": GEMMA_URL, "constrained": "json_schema"},
    }
    jsonl, metaf = write_outputs(labelled, meta, args.out_dir)
    print(f"labelled: {len(labelled)}  {meta['counts']['labelled']}")
    print(f"wrote {jsonl}\n      {metaf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
