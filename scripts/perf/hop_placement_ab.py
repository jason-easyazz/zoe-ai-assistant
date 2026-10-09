#!/usr/bin/env python3
"""hop_placement_ab.py - where on the Flue wire should the personalisation hop's facts ride?

THE QUESTION. Day-sim 2026-10-09 (main 4bb903d2): S9b FAIL with ``hop_in_packet: true`` ("Yes, I'd take a jacket, it's
around 19 degrees and wet in Geraldton" - the weather tool's answer, nothing about the 6 am dog walk) and P-family
P2.b 8/20 (bar >= 18/20) with the hop, against 20/20 for the oracle arm that appends the gold decision to the USER
message. The content is in the prompt; the 4B does not act on it from where it sits. ``ZOE_PERSONALISATION_HOP_PLACEMENT``
(``personalisation_hop.placement()``) has three values:

  block     the delimited ``[MEMORY CONTEXT ...] ## Shape the answer by ...`` block AFTER the user's words (the original)
  suffix    ONE parenthetical line after the words: ``(you know this about me: <fact>; <fact>)``
  preamble  the same delimited block BEFORE the user's words (last of the blocks, right before them)

WHAT THIS DRIVES. The REAL composition in ``zoe_flue_client._run_flue_brain_streaming_turn`` (the very code the chat and
voice lanes run) against the REAL Flue sidecar and the live brain, in THIS process - the live service's flags and .env are
never changed (a placement is chosen per run through this process's environment; the live zoe-data process cannot be
switched per request, and a second zoe-data does not fit the box's RAM headroom). The only stubs are the DB-bound
neighbours of the hop (identity / persona / recall / continuity / offer / verify blocks, all "" for a fresh demo user) and
``personalisation_hop.build``, which returns what ``select`` picks from the FACT ROWS below - the same facts the day-sim and
the P family store through the API (``d1-shift``, ``d1-dog``, the diet). So ``is_advice_request`` + ``select`` + ``section``
/ ``suffix_line`` + the placement code are the production ones; ``hop_in_packet`` is proven separately by the day-sim.

SCORED (the harness's own scorers, never a new yardstick):
  S9a / S9b   ``samantha_day_sim.score_personal``: a personal needle in the reply AND the brain-as-judge says the advice is tailored
  P2.b        ``samantha_person.score_diet``'s rule: a seafood word and no asserted meat (deterministic)
Placements are interleaved in a shuffled order so drift in the brain hits every arm alike.

SAFETY. ``demo_bar_<hex>`` ids only (a fresh one per run); this script makes no memory write itself (the sidecar's gated tools
run exactly as in a live turn); the shared harness lock must be HELD by the caller (``flock /tmp/zoe-voice-harness.lock``) and the
brain window (``/tmp/zoe-brain-window.lock``, a 12B sweep) must be free, else exit 3. ``ZOE_PERF=1`` or a skip notice. The
bearer token is read from the service .env (one key at a time) and never printed.

Usage:
    python3 scripts/perf/hop_placement_ab.py --selftest      # OFFLINE: the scorers and the three compositions, with negative controls
    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock python3 scripts/perf/hop_placement_ab.py [--n 6] [--placements block,suffix,preamble]
Exit: 0 ran | 2 refused / error / selftest failed | 3 lock held / brain window busy.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ZOE_DATA = REPO / "services" / "zoe-data"
LIVE_SERVICE_DIR = Path("/home/zoe/assistant/services/zoe-data")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ZOE_DATA))
import samantha_bar as sb  # noqa: E402
import samantha_day_sim as ds  # noqa: E402

uma = ds.uma
RESULTS = sb.CACHE / "hop_placement_ab_last.json"
HARNESS_LOCK = "/tmp/zoe-voice-harness.lock"
BRAIN_WINDOW_LOCK = "/tmp/zoe-brain-window.lock"
LIVE_ENV_KEYS = ("ZOE_BRAIN_TOKEN", "ZOE_FLUE_BRAIN_URL", "ZOE_FLUE_WIRE", "ZOE_FLUE_STREAM_ENABLED",
                 "ZOE_FLUE_BRAIN_TIMEOUT_S")

#: the stored forms of the seeds (``samantha_day_sim.SAY`` / ``samantha_person.World.say_diet``). "third" is how a digest writes a
#: row; "first" is the owner's own sentence - the live row can be either.
FACTS = {
    "third": {"S9a": "User works night shifts in the hospital pharmacy and sleeps during the day.",
              "S9b": "User walks their kelpie Juniper along the river every morning at 6am, before going to bed.",
              "P2.b": "User is pescatarian: fish is fine but they don't eat any meat."},
    "first": {"S9a": ds.SAY["d1-shift"], "S9b": ds.SAY["d1-dog"],
              "P2.b": ds.SAY["d1-diet"]},
}
#: the asks of each cell. P2.b cycles through ALL FIVE of the P family's diet phrasings (samantha_person.DIET_ASKS) - the 8/20 of
#: 2026-10-09 was exactly the two phrasings (4 asks each) the hop's request-shape regex recognised.
DIET_PHRASINGS = ("What should I cook tonight?", "Any dinner ideas?", "What's a good dinner for tonight?",
                  "I can't decide what to make for tea.", "Suggest something for dinner.")
ASKS = {"S9a": [ds.ASK_SLEEP], "S9b": [ds.ASK_COLD], "P2.b": list(DIET_PHRASINGS)}
RUBRIC = {"S9a": "sleep", "S9b": "cold"}


def ask_text(ask_id: str, i: int) -> str:
    texts = ASKS[ask_id]
    return texts[i % len(texts)]


def score(ask_id: str, reply: str, judge=None) -> tuple[str, dict]:
    """PASS / FAIL for one reply with the harness's own scorer. ``judge(key, said, reply) -> (verdict, why)`` or None."""
    if ask_id == "P2.b":
        meat, used = uma.asserted(reply, ds.MEAT), uma.word_hits(reply, ds.SEAFOOD)
        return ("PASS" if used and not meat else "FAIL"), {"meat": meat, "diet_used": used}
    return ds.score_personal(ask_id, reply, (lambda: judge(RUBRIC[ask_id], ASKS[ask_id][0], reply)) if judge else None)


# ── the composition under test ───────────────────────────────────────────────────────────────

def install_stubs(zc, ph, fact_for_ask) -> None:
    """Neutralise the DB-bound neighbours of the hop and make ``build`` read the FACT ROWS. In-process only."""
    async def empty(*_a, **_k):
        return ""

    async def none(*_a, **_k):
        return None

    for name in ("_identity_context_block", "_persona_context_block", "_pending_offer_block", "_continuity_context_block",
                 "_recall_context_block"):
        setattr(zc, name, empty)
    zc._verify_plan = none
    zc.is_continuity_turn = lambda *_a, **_k: False

    async def build(user_id, message, *, svc=None):
        fact = fact_for_ask(message)
        rows = [SimpleNamespace(id="mem00000000", text=fact, metadata={})] if fact else []
        return ph.select(message, rows)

    ph.build = build


async def one_turn(zc, user: str, session: str, message: str) -> tuple[str, str]:
    """The reply text of one real turn through the sidecar, and the exact message the brain was sent."""
    sent: dict[str, str] = {}
    real = zc._request_payload

    def spy(outbound: str) -> bytes:
        sent["m"] = outbound
        return real(outbound)

    zc._request_payload = spy
    try:
        parts = []
        async for d in zc._run_flue_brain_streaming_turn(message, session, user):
            if isinstance(d, str) and not d.startswith("__"):
                parts.append(d)
        return "".join(parts).strip(), sent.get("m", "")
    finally:
        zc._request_payload = real


# ── offline selftest (negative controls: the scorers go red on a generic reply) ───────────────

def selftest() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")
        ok &= bool(cond)

    judge_yes = lambda *_a: ("PASS", "tailored")  # noqa: E731
    check("S9b the generic jacket reply of 2026-10-09 is FAIL",
          score("S9b", "Yes, I'd take a jacket, it's around 19 degrees and wet in Geraldton.", judge_yes)[0] == "FAIL")
    check("S9b a reply using the walk is PASS",
          score("S9b", "It will be freezing at 6 am on your walk with Juniper, so wear a warm coat.", judge_yes)[0] == "PASS")
    check("S9b a needle the judge rejects is FAIL", score("S9b", "Wear a coat for the dog walk.", lambda *_a: ("FAIL", "x"))[0] == "FAIL")
    check("S9a generic hygiene is FAIL", score("S9a", "Keep your room dark and avoid caffeine before bed.", judge_yes)[0] == "FAIL")
    check("S9a a night-shift reply is PASS", score("S9a", "On night shifts, use blackout curtains for your daytime sleep.", judge_yes)[0] == "PASS")
    check("P2.b a meat dish is FAIL", score("P2.b", "How about a roast chicken with potatoes?")[0] == "FAIL")
    check("P2.b a generic reply without fish is FAIL", score("P2.b", "How about a vegetable stir-fry?")[0] == "FAIL")
    check("P2.b fish is PASS", score("P2.b", "How about grilled salmon with lemon and greens?")[0] == "PASS")
    import personalisation_hop as _ph
    check("every P2.b phrasing is a food advice request (the old shape regex saw 2 of 5)",
          all(_ph.advice_topics(q) == ("food",) for q in DIET_PHRASINGS))

    import personalisation_hop as ph
    import zoe_flue_client as zc

    install_stubs(zc, ph, lambda msg: FACTS["third"]["S9b"] if "wear" in msg else "")
    sends: dict[str, str] = {}

    async def run_placement(p: str) -> None:
        os.environ["ZOE_PERSONALISATION_HOP_PLACEMENT"] = p
        os.environ["ZOE_FLUE_WIRE"] = "1"
        os.environ["ZOE_FLUE_STREAM_ENABLED"] = "0"

        class _R:
            def raise_for_status(self): return None
            def json(self): return {"result": {"text": "ok"}}

        class _C:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *e): return False
            async def post(self, url, content=None, headers=None):
                sends[p] = json.loads(content)["message"]
                return _R()

        import httpx
        real = httpx.AsyncClient
        httpx.AsyncClient = _C
        try:
            [c async for c in zc._run_flue_brain_streaming_turn(ASKS["S9b"][0], "selftest", "demo_bar_00000000")]
        finally:
            httpx.AsyncClient = real

    for p in ph.PLACEMENTS:
        asyncio.run(run_placement(p))
    ask = ASKS["S9b"][0]
    check("block: the delimited block after the words", ask in sends["block"] and sends["block"].index(ask) < sends["block"].index(ph.HEADING))
    check("suffix: one line after the words, no block", sends["suffix"].splitlines()[-1].startswith(ph.SUFFIX_OPEN)
          and ph.HEADING not in sends["suffix"] and "Juniper" in sends["suffix"])
    check("preamble: the block before the words", sends["preamble"].index(ph.HEADING) < sends["preamble"].index(ask)
          and sends["preamble"].endswith(ask))
    return 0 if ok else 2


# ── live ─────────────────────────────────────────────────────────────────────────────────────

def _held_by_someone(path: str, shared: bool = False) -> bool:
    """True when another open file description holds ``path`` (exclusively, or at all when ``shared``)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def live(args) -> int:
    if os.environ.get("ZOE_PERF") != "1":
        print("hop_placement_ab: ZOE_PERF=1 not set - skip notice (nothing run)")
        return 0
    if not _held_by_someone(HARNESS_LOCK):
        print(f"refused: run under `flock {HARNESS_LOCK}` (the shared harness lock is not held)")
        return 3
    if _held_by_someone(BRAIN_WINDOW_LOCK, shared=True):
        print("refused: the brain window lock is held (a 12B sweep / training window) - wait, never kill")
        return 3
    for key in LIVE_ENV_KEYS:                     # one key at a time; values are never printed
        v = sb.env_file_value(LIVE_SERVICE_DIR, key)
        if v and key not in os.environ:
            os.environ[key] = v
    if not os.environ.get("ZOE_BRAIN_TOKEN"):
        print("refused: no ZOE_BRAIN_TOKEN in the service .env")
        return 2
    placements = [p for p in args.placements.split(",") if p]
    import personalisation_hop as ph
    import zoe_flue_client as zc

    unknown = [p for p in placements if p not in ph.PLACEMENTS]
    if unknown:
        print(f"unknown placement(s): {unknown}")
        return 2
    style = FACTS[args.facts]
    install_stubs(zc, ph, lambda msg: next((style[a] for a, qs in ASKS.items() if msg in qs), ""))
    user = sb.new_demo_user()
    sb.assert_demo_user(user)
    judge = ds.DayLive("", "", "", False).judge_rubric
    asks = [a for a in args.asks.split(",") if a]
    plan = [(p, a, i) for i in range(args.n) for a in asks for p in placements]
    random.Random(args.seed).shuffle(plan)
    out: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n": args.n, "facts": args.facts,
                 "placements": placements, "asks": asks, "user": user, "samples": []}
    for p, a, i in plan:
        os.environ["ZOE_PERSONALISATION_HOP_PLACEMENT"] = p
        sid = f"hopab-{p}-{a.replace('.', '')}-{i}-{int(time.time())}"
        try:
            reply, wire = asyncio.run(one_turn(zc, user, sid, ask_text(a, i)))
        except Exception as exc:  # noqa: BLE001
            out["samples"].append({"placement": p, "ask": a, "i": i, "verdict": "ERROR", "why": type(exc).__name__})
            print(f"  {p:9s} {a:5s} #{i} ERROR {type(exc).__name__}")
            continue
        if not reply or sb.is_brain_fallback(reply):
            v, ev = "ERROR", {"why": "empty or fallback reply"}
        else:
            v, ev = score(a, reply, judge)
        out["samples"].append({"placement": p, "ask": a, "i": i, "verdict": v, "reply": reply[:300],
                               "text": ask_text(a, i), "hop_on_wire": (ph.HEADING in wire) or (ph.SUFFIX_OPEN in wire),
                               **{k: ev[k] for k in ev if k in ("personal", "meat", "diet_used")}})
        print(f"  {p:9s} {a:5s} #{i} {v:5s} {reply[:90]!r}")
    table: dict = {}
    for s in out["samples"]:
        c = table.setdefault(s["placement"], {}).setdefault(s["ask"], {"PASS": 0, "FAIL": 0, "ERROR": 0, "n": 0, "hop": 0})
        c[s["verdict"] if s["verdict"] in c else "ERROR"] += 1
        c["n"] += 1
        c["hop"] += 1 if s.get("hop_on_wire") else 0
    out["table"] = table
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    RESULTS.write_text(json.dumps(out, indent=1))
    print("\nplacement   " + "  ".join(f"{a:>9s}" for a in asks) + "     total")
    for p in placements:
        cells = [table.get(p, {}).get(a, {"PASS": 0, "n": 0}) for a in asks]
        print(f"{p:10s}  " + "  ".join(f"{c['PASS']:>4d}/{c['n']:<4d}" for c in cells)
              + f"   {sum(c['PASS'] for c in cells)}/{sum(c['n'] for c in cells)}"
              + "   hop on the wire: " + " ".join(f"{a}={c.get('hop', 0)}/{c['n']}" for a, c in zip(asks, cells)))
    print(f"results: {RESULTS}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--n", type=int, default=6, help="repeats per (placement, ask)")
    ap.add_argument("--placements", default="block,suffix,preamble")
    ap.add_argument("--asks", default="S9a,S9b,P2.b")
    ap.add_argument("--facts", choices=sorted(FACTS), default="third", help="the stored form of the fact rows")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    return selftest() if args.selftest else live(args)


if __name__ == "__main__":
    sys.exit(main())
