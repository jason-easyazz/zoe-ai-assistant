"""S10x LIVE tier: the real judge over the pilot's pool, scored against the pass bars of the record (ZMB cells ``S10x.live.*``).

The store tier (``scenarios/retirement.json``) scripts the brain's choice, so it measures the walls, the prefilter, the effect and the
candidate stage. This tier is the judge: the model that must tell "I gave up the cello" (ends a note) from "I saw a cello today" (ends
nothing) and from "I almost gave up the cello but kept going" (a hard one). It is meant to run once in an operator window against the
bake-off clone brain (Gemma 4 E4B on :11500, ``bakeoff.py``), which is a measurement the owner schedules (it stops the live brain) - nothing
here starts, stops or touches any service.

Everything else is the REAL pipeline: ``memory_retire.prepare`` (the cue gate, the owner's own words, the candidates over the service's own
blended search and, on Z0e, the real MiniLM embedder) and ``memory_retire.decide`` (the wall). Only the judge's NUMBER comes from the model, through the same
prompt the voice lane's digest uses (``memory_retire.judge_prompt`` / ``parse_pick``). Run it with the judge scripted (``oracle_judge``,
``naive_judge``) and it is the instrument check: an oracle must pass every bar, the naive rule (take the retrieval's top-1) must fail them.

    python3 scripts/perf/zmb/s10x_live.py --clone-url http://127.0.0.1:11500 --out /tmp/s10x-live.json        # the real measurement
    python3 scripts/perf/zmb/s10x_live.py --self-check                                                          # oracle + naive, no model

The pass bars (docs/research/mempalace-deep-dive-2026-10-06.md section 6.3): >= 24 of 30 correct retirements; <= 2 of 40 wrong on non-changes
and 0 of 10 on the hard set; 0 of 30 other-person copies; 0 retirements from a third-party, unverified or pasted turn; ``as_of`` before the change
returns the old fact for every correct retirement; no extra model call and <= 40 ms added on a turn that carries no change cue. "Voice turn
unchanged" is the replay gate's (the per-turn digest runs after the reply). Counts and verdicts only: no household text goes in the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

if __package__ in (None, ""):                          # run as a script: make ``zmb`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "zmb"

from . import s10x_data
from .scorers import wilson

Judge = Callable[[str, list], Awaitable[Optional[int]]]

BARS = {
    "right_rows": "ZMB S10x pass bar: at least 24 of 30 changes retire the owner's right old row with the sentence attached",
    "non_changes": "at most 2 of 40 non-changes (30 mentions + 10 hard) retire any row",
    "hard_set": "0 of the 10 hard non-changes retire a row",
    "other_person_copies": "0 of 30 other-person copies retired",
    "third_party_pasted": "0 retirements from a third-party, unverified or pasted turn (and no judge call made for one)",
    "as_of": "a read as of a moment before the change returns the old fact for every correct retirement, and a read now does not",
    "latency": ("the gate adds <= 40 ms (p95) and no model call on a turn with no change cue (the record's bar). A turn that DOES carry a cue pays the "
                "candidate search, measured and reported but not gated, and the judge: the brain's two tool calls on the chat lane, one off-turn call on the voice lane"),
}
MIN_RIGHT, MAX_WRONG, MAX_LATENCY_MS = 24, 2, 40.0


# ── scripted judges (the instrument check) ──────────────────────────────────────────────────────────

def oracle_judge(gold_for: "dict[str, str]") -> Judge:
    """A perfect judge: names the candidate whose text is the gold old row for the sentence, else 0. ``gold_for``: sentence -> old row text."""
    async def judge(quote: str, rows: list) -> Optional[int]:
        want = gold_for.get(quote)
        return next((i for i, r in enumerate(rows, 1) if r.text == want), 0)
    return judge


async def naive_judge(quote: str, rows: list) -> Optional[int]:
    """No judgement: the retrieval's top-1 (what the pilot measured wrong on 34 of 40 non-changes)."""
    return 1 if rows else 0


def clone_judge(base_url: str) -> Judge:
    """The real judge: the voice lane's own prompt (``memory_retire.judge_prompt``), answered by the model at ``base_url`` (the bake-off
    clone brain). ``None`` = unreachable (the pipeline then retires nothing and the run records it as a miss, never a pass)."""
    from .lab_driver import load_service
    mr = load_service().memory_retire

    async def judge(quote: str, rows: list) -> Optional[int]:
        return await mr.ask_judge(quote, rows, base_url=base_url, timeout_s=60.0)
    return judge


class _Counting:
    """A judge that counts its own calls (a deterministic turn must make none)."""
    def __init__(self, judge: Judge):
        self.judge, self.calls = judge, 0

    async def __call__(self, quote: str, rows: list) -> Optional[int]:
        self.calls += 1
        return await self.judge(quote, rows)


# ── the run ──────────────────────────────────────────────────────────────────────────────────────

def _percentile(xs: "list[float]", q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0.0


def _fresh_pool(arm: Any, user: str) -> "tuple[dict[str, dict], set[str]]":
    from .arms.base import Turn
    arm.reset(user)
    arm.ingest([Turn(t, "owner_taught") for t in s10x_data.pool()])
    by_text = {r["text"]: r for r in arm.stats()["rows"]}
    return by_text, {by_text[t]["id"] for t in s10x_data.other_person_copies() if t in by_text}


def run(judge: Judge, *, embed: bool = True, log: "Callable[[str], None]" = print, label: str = "judge") -> "dict[str, Any]":
    """Run every S10x live measurement with ``judge``. ``embed``: the arm is Z0e (real Chroma + MiniLM, as live); ``False`` is the lab's
    bag-of-words Z0 (the machinery check only: its retrieval is a stand-in, so ``right_rows`` is not a finding there)."""
    from .arms.z0 import Z0Arm
    arm = Z0Arm(name="Z0e" if embed else "Z0", embed=embed)
    user = "demo_bar_" + hashlib.sha1(b"s10x-live").hexdigest()[:8]
    judge = _Counting(judge)
    pairs = s10x_data.PAIRS
    changes = [say for _o, say, _f in pairs]
    out: "dict[str, Any]" = {"judge": label, "arm": arm.name, "n_changes": len(pairs)}
    try:
        # warm the embedder / the model path once, off the clock
        _fresh_pool(arm, user)
        arm.quote_retire(changes[0], brain={"pick": 0})

        # A. non-changes on a fresh pool: 30 mentions + 10 hard (a retirement is a wrong one)
        by_text, copy_ids = _fresh_pool(arm, user)
        t0 = time.monotonic()
        wrong_mentions = sum(arm.quote_retire(t, brain={"judge": judge})["action"] == "retired" for t in s10x_data.NON_CHANGES)
        wrong_hard = sum(arm.quote_retire(t, brain={"judge": judge})["action"] == "retired" for t, _i in s10x_data.HARD_NON_CHANGES)
        out["non_changes"] = {"mentions": len(s10x_data.NON_CHANGES), "wrong_on_mentions": wrong_mentions, "hard": len(s10x_data.HARD_NON_CHANGES),
                              "wrong_on_hard": wrong_hard, "judge_calls": judge.calls, "seconds": round(time.monotonic() - t0, 1)}
        log(f"[{label}] non-changes: {wrong_mentions + wrong_hard}/{len(changes) + 10} wrong (hard {wrong_hard}/10)")

        # B. the 30 changes on a fresh pool
        by_text, copy_ids = _fresh_pool(arm, user)
        right = copies_retired = other_retired = 0
        retired_ids: "list[str]" = []
        for old, say, _fact in pairs:
            res = arm.quote_retire(say, brain={"judge": judge})
            if res["action"] != "retired":
                continue
            retired_ids.append(res["chosen"])
            if res["chosen"] in copy_ids:
                copies_retired += 1
            elif res["chosen"] == by_text[old]["id"]:
                now = next(r for r in arm.stats()["rows"] if r["id"] == res["chosen"])
                right += now["status"] == "superseded" and now["retire_quote"] == say
            else:
                other_retired += 1
        out["changes"] = {"right": right, "other_row_retired": other_retired, "copies_retired": copies_retired}
        log(f"[{label}] changes: {right}/{len(pairs)} right, {other_retired} other row, {copies_retired} copies")

        # C. the as_of timeline of every correct retirement
        as_of_ok = as_of_n = 0
        rows_now = {r["id"]: r for r in arm.stats()["rows"]}
        for old, say, _fact in pairs:
            rid = by_text[old]["id"]
            r = rows_now.get(rid)
            if not (r and r["status"] == "superseded" and r["retire_quote"] == say):
                continue
            as_of_n += 1
            mid = dt.datetime.fromtimestamp((float(r["valid_from"]) + float(r["invalid_at"])) / 2, dt.timezone.utc).isoformat()
            before = {h["id"] for h in arm.as_of(old, mid)}
            now_ids = {h["id"] for h in arm.recall(old, 10)}
            as_of_ok += rid in before and rid not in now_ids
        out["as_of"] = {"checked": as_of_n, "ok": as_of_ok}

        # D. a third person, an unverified voice, a pasted email: every shape of the 30 changes, on a fresh pool
        _fresh_pool(arm, user)
        calls0, retired_bad = judge.calls, 0
        for _old, say, _fact in pairs:
            for kw in ({"text": f"Quentin says: {say}"},
                       {"text": say, "speaker": "panel_unverified"},
                       {"text": f"Here is an email my cousin forwarded me:\nFrom: Sam Ito\nSubject: news\n\n{say}"}):
                lane, verified = arm.RETIRE_SPEAKERS[kw.get("speaker", "owner_typed")]
                retired_bad += arm.quote_retire(kw["text"], lane=lane, speaker_verified=verified, brain={"judge": judge})["action"] == "retired"
        out["third_party_pasted"] = {"turns": 3 * len(pairs), "retired": retired_bad, "judge_calls": judge.calls - calls0}

        # E. turns with no change cue: no model call, and what the prefilter costs (the cue gate is regex; no store is read)
        calls0, ms = judge.calls, []
        cueless = [t for t in s10x_data.HELD_OUT_MENTIONS] + ["I saw a cello today.", "I walked to the shops.", "The weather is lovely."]
        for text in cueless * 3:
            t = time.monotonic()
            arm.quote_retire(text, brain={"judge": judge})
            ms.append((time.monotonic() - t) * 1000.0)
        out["latency"] = {"turns": len(ms), "p50_ms": round(_percentile(ms, 0.5), 3), "p95_ms": round(_percentile(ms, 0.95), 3),
                          "judge_calls": judge.calls - calls0}
        # ... and what the deterministic half costs on a turn that DOES carry a cue (the candidate search; the judge itself is the model's
        # call: the brain's two tool calls on the chat lane, one call off the turn on the voice lane, and is not part of this number)
        _fresh_pool(arm, user)
        cue_ms = []
        for text in changes[:15] + [t for t in s10x_data.NON_CHANGES if arm.cue_gate(t)] + [t for t, _i in s10x_data.HARD_NON_CHANGES]:
            t = time.monotonic()
            arm.quote_retire(text, brain={"pick": 0})
            cue_ms.append((time.monotonic() - t) * 1000.0)
        out["latency"].update(cue_turns=len(cue_ms), cue_p50_ms=round(_percentile(cue_ms, 0.5), 1), cue_p95_ms=round(_percentile(cue_ms, 0.95), 1))
    finally:
        arm.close()
    out["cells"] = verdicts(out)
    return out


def verdicts(m: "dict[str, Any]") -> "dict[str, dict[str, Any]]":
    """Each ``S10x.live.*`` cell's verdict from the measurements (PASS / FAIL, the number and the bar)."""
    n_non = m["non_changes"]["mentions"] + m["non_changes"]["hard"]
    wrong = m["non_changes"]["wrong_on_mentions"] + m["non_changes"]["wrong_on_hard"]
    lo, hi = wilson(m["changes"]["right"], m["n_changes"])
    cells = {
        "right_rows": (m["changes"]["right"] >= MIN_RIGHT and m["changes"]["other_row_retired"] <= MAX_WRONG,
                       f"{m['changes']['right']}/{m['n_changes']} right (Wilson95 {lo:.2f}-{hi:.2f}); {m['changes']['other_row_retired']} other row retired"),
        "non_changes": (wrong <= MAX_WRONG, f"{wrong}/{n_non} wrong"),
        "hard_set": (m["non_changes"]["wrong_on_hard"] == 0, f"{m['non_changes']['wrong_on_hard']}/{m['non_changes']['hard']} wrong"),
        "other_person_copies": (m["changes"]["copies_retired"] == 0, f"{m['changes']['copies_retired']}/{m['n_changes']} retired"),
        "third_party_pasted": (m["third_party_pasted"]["retired"] == 0 and m["third_party_pasted"]["judge_calls"] == 0,
                               f"{m['third_party_pasted']['retired']}/{m['third_party_pasted']['turns']} retired, "
                               f"{m['third_party_pasted']['judge_calls']} judge calls"),
        "as_of": (m["as_of"]["checked"] > 0 and m["as_of"]["ok"] == m["as_of"]["checked"],
                  f"{m['as_of']['ok']}/{m['as_of']['checked']} correct retirements keep their timeline"),
        "latency": (m["latency"]["p95_ms"] <= MAX_LATENCY_MS and m["latency"]["judge_calls"] == 0,
                    f"no-cue turns: p50 {m['latency']['p50_ms']} ms, p95 {m['latency']['p95_ms']} ms, {m['latency']['judge_calls']} judge calls "
                    f"over {m['latency']['turns']}; REPORTED, not gated: cue turns pay the candidate search (judge excluded) p50 {m['latency']['cue_p50_ms']} ms, "
                    f"p95 {m['latency']['cue_p95_ms']} ms over {m['latency']['cue_turns']}"),
    }
    return {f"S10x.live.{k}": {"verdict": "PASS" if ok else "FAIL", "measured": text, "bar": BARS[k]} for k, (ok, text) in cells.items()}


def render(result: "dict[str, Any]") -> str:
    lines = [f"S10x live tier: judge={result['judge']} arm={result['arm']}"]
    for cid, c in result["cells"].items():
        lines.append(f"  {c['verdict']:4}  {cid:34} {c['measured']}   [{c['bar']}]")
    return "\n".join(lines)


def main(argv: "Optional[list[str]]" = None) -> int:
    ap = argparse.ArgumentParser(description="S10x live tier: the real judge over the pilot's pool, against the record's pass bars.")
    ap.add_argument("--clone-url", help="the bake-off clone brain's base URL (llama-server, e.g. http://127.0.0.1:11500)")
    ap.add_argument("--self-check", action="store_true", help="run the scripted judges (oracle must pass every bar, naive must fail): no model")
    ap.add_argument("--no-embed", action="store_true", help="the lab's bag-of-words Z0 instead of Z0e (machinery check only)")
    ap.add_argument("--out", help="write the result JSON here")
    args = ap.parse_args(argv)
    results = []
    if args.self_check:
        gold = {say: old for old, say, _f in s10x_data.PAIRS}
        results += [run(oracle_judge(gold), embed=not args.no_embed, label="oracle"), run(naive_judge, embed=not args.no_embed, label="naive")]
    elif args.clone_url:
        results.append(run(clone_judge(args.clone_url), embed=not args.no_embed, label=f"clone@{args.clone_url}"))
    else:
        ap.error("give --clone-url (the real measurement) or --self-check")
    for r in results:
        print(render(r))
    if args.out:
        Path(args.out).write_text(json.dumps(results if len(results) > 1 else results[0], indent=1), encoding="utf-8")
    return 0 if all(c["verdict"] == "PASS" for r in results[:1] for c in r["cells"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
