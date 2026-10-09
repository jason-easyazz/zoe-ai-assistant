#!/usr/bin/env python3
"""recall_gate_replay.py - OFFLINE replay of the recall relevance gate over the Samantha-bar / day-sim / person-bench asks.

The question it answers (docs/knowledge/recall-gate.md): for each scenario's ask, what would ``ZOE_RECALL_GATE=enforce`` have ADDED to
and DROPPED from the packet the floor builds today, and does the scenario's needle survive? It is the evidence for the enforce
decision; it flips nothing.

HERMETIC. No brain, no network, no live service, no live database, no household data:
  * a throwaway MemPalace directory (``MEMPALACE_DATA_DIR`` pinned to a temp dir BEFORE any zoe-data import - the conftest rule), real
    ONNX embeddings, the real ``MemoryService.ingest / load_for_prompt / search / load_durable_for_hop``;
  * the REAL ``routers.memories.memory_for_prompt`` and ``personalisation_hop.build`` (the production floor), once with
    ``ZOE_RECALL_GATE=shadow`` (the floor's packet + the gate's RECALL_GATE line) and once with ``enforce`` (the gate's packet), each
    on a fresh gate state and the same turn sequence;
  * restraint's mute table (a Postgres table) stubbed empty; synthetic ``demo_bar_<8 hex>`` users only (asserted before any write).
The ROWS are fixtures: what the writers store for each scenario's seed turns (wording modelled on rows quoted in the bar docs and PR
bodies). They are not the live store, and the extractor is not run - so this is a replay of the SELECTION, not of the whole chain.
The asks, needles and anti-needles are imported from ``samantha_bar`` / ``samantha_day_sim`` / ``samantha_person`` so they cannot drift.

Usage:
    python3 scripts/perf/recall_gate_replay.py                  # table to stdout, JSON to ~/.cache/zoe/recall_gate_replay_last.json
    python3 scripts/perf/recall_gate_replay.py --markdown out.md --only S1,S2,D-S9b
Exit: 0 ran | 2 error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SVC = ROOT / "services" / "zoe-data"

_TMP = tempfile.mkdtemp(prefix="zoe-gate-replay-")
os.environ["MEMPALACE_DATA_DIR"] = os.path.join(_TMP, "mempalace")
os.environ["ZOE_VOICE_STT_LOG"] = os.path.join(_TMP, "voice_stt.jsonl")
os.environ["ZOE_MEMORY_REJECT_LEDGER"] = os.path.join(_TMP, "reject-ledger.json")
os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
os.environ["ZOE_RECALL_EVIDENCE"] = "1"          # the live floor serves dated bullets (beat-the-bar section 0)
os.environ["ZOE_RESTRAINT"] = "enforce"          # the enforce world: sensitive rows wait for a pull, in the floor and so in the gate
for _k in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED", "ZOE_PERSON_SUGGEST_ENABLED"):
    os.environ.pop(_k, None)
sys.path[:0] = [str(SVC), str(HERE)]

DEMO = re.compile(r"^demo_bar_[0-9a-f]{8}$")
A, B = "owner", "other"            # roles; each case gets its own demo_bar_<8 hex> ids (hashed from the case id) so no case sees another's rows


def demo_user(case_id: str, role: str) -> str:
    import hashlib

    return "demo_bar_" + hashlib.sha1(f"{case_id}/{role}".encode()).hexdigest()[:8]

LINE = re.compile(r"RECALL_GATE user=(?P<user>\S+) served=(?P<served>\d+) would_add=(?P<add>\S+) would_drop=(?P<drop>\S+) "
                  r"budget=(?P<budget>\d+) surface=(?P<surface>\w+) turn=(?P<turn>\d+) mode=(?P<mode>\w+) cap=\d+ "
                  r"held=(?P<held>\d+) cooled=(?P<cooled>\d+) delayed=(?P<delayed>\d+) skipped=(?P<skipped>\S+)")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("RECALL_GATE"):
            self.lines.append(msg)


def build_cases():
    import samantha_bar as sb
    import samantha_day_sim as ds
    import samantha_person as sp

    # rows as the writers store them (owner-stated, source chat_regex unless noted). (text, status)
    r = lambda t, status="approved", source="chat_regex": (t, status, source)          # noqa: E731
    sister = r("User's sister Marisol is flying in from Lisbon on Thursday.")
    dad_rich = r("User's dad Teodor is a retired lighthouse keeper who builds model ships in his shed.")
    dad_short = r("User's dad is Teodor.")
    filler = [r(s) for s in (
        "User had porridge with banana for breakfast.", "User plans to repaint the hallway a pale green.",
        "User's neighbour's cat keeps sitting on User's car bonnet.", "User finally finished the crossword from Sunday.",
        "User is trying to drink more water during the day.", "User watched a documentary about octopuses yesterday.",
        "User's running shoes are starting to wear out.", "User booked a haircut for next Wednesday.",
        "User is thinking about learning to play the ukulele.", "User keeps forgetting to water the fern.")]
    week = {
        "mum_old": r("User's mum Ingrid lives in Ballarat and is recovering from a hip replacement.", "superseded"),
        "mum": r("User's mum Ingrid lives in Bendigo and is recovering from a hip replacement."),
        "migraine": r("User no longer gets migraines since switching to new glasses."),
        "project": r("User leads the Kestrel billing migration, which has to go live on the 14th of November."),
        "diet": r("User is pescatarian: fish is fine but they do not eat any meat."),
        "dog": r("User walks their kelpie Juniper along the river every morning at 6am."),
        "shift": r("User works night shifts in the hospital pharmacy and sleeps during the day."),
        "race": r("User dropped the Rottnest half-marathon and is doing the City to Surf 12k in August."),
        "dentist": r("User has the dentist on Friday for a cracked molar and is nervous about it."),
    }
    hobbies = [r(f"User keeps a log of hobby item {i}: {w}.") for i, w in enumerate(
        ["sourdough starters", "bird counts at the estuary", "a jigsaw of the Alps", "tide tables", "model railway signals",
         "a seed library", "old postcards", "knitting patterns", "moth traps", "a rain gauge", "chess puzzles", "fern spores"])]
    inferred = r("User may enjoy sailing on weekends.", source="digest")     # an inferred row (rank 0): the authority wall's control
    tasks = [t for t, _g in sp.TASKS[:4]]
    return [
        dict(id="S1", title="same-day recall", surface="packet", user=A, rows=[sister], turns=[sb.ASK_SISTER],
             needles=["marisol", "lisbon"], anti=[]),
        dict(id="S2", title="changed fact: the newer wins", surface="packet", user=A,
             rows=[r("User lives in Dunedin.", "superseded"), r("User lives in Hobart now.")], turns=[sb.ASK_HOME],
             needles=["hobart"], anti=["dunedin"]),
        dict(id="S3", title="decline when nothing was said", surface="packet", user=A, rows=[sister], turns=[sb.ASK_UNSAID],
             needles=[], anti=["dentist"]),
        dict(id="S4", title="emotional thread (continuity block)", surface="continuity", user=A,
             rows=[r("User is anxious about a job interview at the aquarium on Friday.")], turns=[sb.ASK_WORRY],
             needles=["aquarium"], anti=[]),
        dict(id="S6", title="user isolation (B asks, A's rows exist)", surface="packet", user=B, rows=[sister, dad_rich],
             rows_user=A, turns=[sb.ASK_B], needles=[], anti=list(sb.A_NEEDLES)),
        dict(id="S7", title="keep the richer fact", surface="packet", user=A, rows=[dad_rich, dad_short], turns=[sb.ASK_DAD],
             needles=["teodor", "lighthouse", "model ship"], anti=[]),
        dict(id="S8", title="recall after filler", surface="packet", user=A, rows=[sister, dad_rich] + filler,
             turns=[sb.ASK_LONG_SISTER, sb.ASK_LONG_DAD], needles=[["marisol", "lisbon"], ["teodor", "lighthouse"]], anti=[]),
        dict(id="D-mum", title="day-sim: how is my mum (correction)", surface="packet", user=A,
             rows=list(week.values()), turns=[ds.ASK_MUM], needles=["bendigo"], anti=["ballarat"]),
        dict(id="D-quote", title="day-sim: the Kestrel project", surface="packet", user=A, rows=list(week.values()),
             turns=[ds.ASK_QUOTE], needles=["kestrel"], anti=[]),
        dict(id="D-race", title="day-sim: still doing the half-marathon", surface="packet", user=A, rows=list(week.values()),
             turns=[ds.ASK_RACE], needles=["city to surf"], anti=[]),
        dict(id="D-time", title="day-sim: the dentist on Friday", surface="packet", user=A, rows=list(week.values()),
             turns=[ds.ASK_TIME], needles=["dentist"], anti=[]),
        dict(id="D-S9a", title="day-sim S9a hop: sleep tips, night shift", surface="hop", user=A, rows=list(week.values()),
             turns=[ds.ASK_SLEEP], needles=["night shift"], anti=[]),
        dict(id="D-S9b", title="day-sim S9b hop: what to wear, 6am walker", surface="hop", user=A, rows=list(week.values()),
             turns=[ds.ASK_COLD], needles=["6am"], anti=[]),
        dict(id="P2.b", title="person P2.b hop: diet asks", surface="hop", user=A, rows=[week["diet"], week["dentist"], week["project"]],
             turns=[q for q, _g in sp.DIET_ASKS], needles=["pescatarian"], anti=[]),
        dict(id="P2.a", title="person P2.a task turns (no leak of the dentist worry)", surface="packet", user=A,
             rows=[week["dentist"], week["diet"], week["project"]], turns=tasks, needles=[], anti=["dentist", "molar"]),
        dict(id="X2", title="crowded world: the answer row is outside the floor's ranked 12", surface="packet", user=A,
             rows=list(week.values()) + [r(f"User keeps a log of hobby item {i}: {w}.") for i, w in enumerate(
                 ["sourdough", "estuary birds", "alpine jigsaws", "tide tables", "railway signals", "seed library", "postcards",
                  "knitting", "moth traps", "rain gauge", "chess puzzles", "fern spores", "kite designs", "map folding", "lichen",
                  "dry stone walls", "bell ringing", "pressed flowers"])],
             turns=["How is Juniper getting on lately?", "Is the Kestrel go-live still on?"], needles=[["juniper"], ["kestrel"]], anti=[]),
        dict(id="H1", title="hop: a routine in words the hop's list does not know", surface="hop", user=A,
             rows=[r("User goes sculling on Lake Pedder with Wilhelmina at 5am, whatever the weather."), week["diet"]],
             turns=["What should I wear tomorrow for the sculling with Wilhelmina? It's meant to be really cold."],
             needles=["sculling"], anti=[]),
        dict(id="X1", title="crowded world: 12 hobby rows + the week", surface="packet", user=A,
             rows=hobbies + list(week.values()) + [inferred], turns=[ds.ASK_MUM, sb.ASK_DAD, "Do you remember what I said about sailing?"],
             needles=[["bendigo"], [], ["sailing"]], anti=[]),
    ]


async def build_store(rows, user):
    from memory_service import MemoryService

    assert DEMO.match(user), user
    svc = MemoryService(data_dir=os.environ["MEMPALACE_DATA_DIR"])
    for text, status, source in rows:
        await svc.ingest(text, user_id=user, source=source, status=status)
    got = svc._collection().get(include=["documents"])             # hermetic temp store: labels for the table
    return svc, dict(zip(got["ids"], got["documents"]))


def has(packet: str, needles) -> bool:
    p = (packet or "").lower()
    return all(n in p for n in (needles or []))


def leaks(packet: str, anti) -> list[str]:
    p = (packet or "").lower()
    return [a for a in (anti or []) if a in p]


async def play(case, svc, enforce: bool, cap: _Capture):
    import personalisation_hop as hop
    import recall_gate as rg
    import routers.memories as memories

    os.environ["ZOE_RECALL_GATE"] = "enforce" if enforce else "shadow"
    rg._reset_state()
    memories._svc = lambda: svc
    out = []
    for n, msg in enumerate(case["turns"]):
        cap.lines.clear()
        rg.note_turn(case["user"], f"replay-{case['id']}", msg)
        if case["surface"] == "hop":
            h = await hop.build(case["user"], msg, svc=svc)
            text, count = h.section(), len(h.facts)
        else:
            kw = {"mode": "continuity"} if case["surface"] == "continuity" else {}
            res = await memories.memory_for_prompt(user_id=case["user"], message=msg, limit=12, _=None, **kw)
            text, count = res.get("packet") or "", res.get("count", 0)
        await rg.drain()
        m = LINE.search(cap.lines[-1]) if cap.lines else None
        out.append({"ask": msg, "text": text, "count": count, "line": m.groupdict() if m else None})
    return out


def label(ids: dict[str, str], short: str) -> str:
    import recall_gate as rg

    for rid, text in ids.items():
        if rg.short_id(rid) == short:
            return " ".join(text.split()[1:6])
    return short


async def replay(only):
    import logging as _l

    import exact_words
    import night_store
    import restraint

    exact_words.set_backend(exact_words.MemoryBackend())             # Postgres tables in production; in-process here (the conftest rule)
    night_store.set_backend(night_store.MemoryBackend())

    async def no_mutes(_uid):
        return []

    restraint.list_mutes = no_mutes
    cap = _Capture()
    _l.getLogger("recall_gate").addHandler(cap)
    _l.getLogger("recall_gate").setLevel(_l.INFO)
    results = []
    for case in build_cases():
        if only and case["id"] not in only:
            continue
        rows_user = demo_user(case["id"], case.get("rows_user", case["user"]))
        case["user"] = demo_user(case["id"], case["user"])
        svc, ids = await build_store(case["rows"], rows_user)
        shadow = await play(case, svc, False, cap)
        enforce = await play(case, svc, True, cap)
        nd = case["needles"]
        for i, (s, e) in enumerate(zip(shadow, enforce)):
            need = nd[i] if nd and isinstance(nd[0], list) else nd
            need = need if isinstance(need, list) else nd
            ln = s["line"] or {}
            add = [] if not ln or ln["add"] == "-" else ln["add"].split(",")
            drop = [] if not ln or ln["drop"] == "-" else ln["drop"].split(",")
            f_ok, e_ok = has(s["text"], need) and not leaks(s["text"], case["anti"]), has(e["text"], need) and not leaks(e["text"], case["anti"])
            results.append({
                "scenario": case["id"], "title": case["title"], "surface": case["surface"], "ask": s["ask"],
                "floor_served": s["count"], "gate_served": (int(ln["served"]) if ln else None),
                "would_add": [label(ids, x) for x in add], "would_drop": [label(ids, x) for x in drop],
                "skipped": (ln.get("skipped") if ln and ln.get("skipped") != "-" else ""), "gate_line": bool(ln),
                "needle": need, "anti": case["anti"], "floor_ok": f_ok, "enforce_ok": e_ok,
                "enforce_count": e["count"], "leak_floor": leaks(s["text"], case["anti"]), "leak_enforce": leaks(e["text"], case["anti"]),
                "verdict": ("n/a (gate abstains)" if (ln.get("skipped") not in (None, "-") or case["surface"] == "continuity")
                            else "match" if f_ok == e_ok else "ENFORCE BETTER" if e_ok else "ENFORCE WOULD REGRESS"),
            })
    return results


def table(results) -> str:
    rows = ["| scenario | ask | surface | floor rows | gate rows | would add | would drop | needle floor / enforce | verdict |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        add = "; ".join(r["would_add"]) or "-"
        drop = ("; ".join(r["would_drop"]) if len(r["would_drop"]) <= 2
                else f"{len(r['would_drop'])}: " + "; ".join(r["would_drop"][:2]) + "; ...") if r["would_drop"] else "-"
        need = ("-" if not r["needle"] and not r["anti"] else
                f"{'yes' if r['floor_ok'] else 'NO'} / {'yes' if r['enforce_ok'] else 'NO'}")
        rows.append(f"| {r['scenario']} | {r['ask'][:44]} | {r['surface']} | {r['floor_served']} | {r['gate_served'] if r['gate_served'] is not None else '-'}"
                    f" | {add} | {drop} | {need} | {r['verdict']} |")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="", help="comma list of scenario ids")
    ap.add_argument("--json", default=str(Path.home() / ".cache" / "zoe" / "recall_gate_replay_last.json"))
    ap.add_argument("--markdown", default="")
    ap.add_argument("--filler", type=int, default=None, help="override Config.filler_max (the floor's generic filler rows the gate may keep)")
    ap.add_argument("--abstain", type=int, default=None, choices=(0, 1), help="override Config.abstain (1: an empty selection is an empty packet)")
    args = ap.parse_args(argv)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if args.filler is not None or args.abstain is not None:
        import dataclasses

        import recall_gate

        kw = {k: v for k, v in (("filler_max", args.filler), ("abstain", None if args.abstain is None else bool(args.abstain))) if v is not None}
        recall_gate.PACKET = dataclasses.replace(recall_gate.PACKET, **kw)
        recall_gate.HOP = dataclasses.replace(recall_gate.HOP, **{k: v for k, v in kw.items() if k == "abstain"})
    try:
        results = asyncio.run(replay(only))
    except Exception as exc:  # noqa: BLE001
        print(f"replay failed: {exc!r}", file=sys.stderr)
        return 2
    md = table(results)
    print(md)
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(results, indent=1, ensure_ascii=False))
    if args.markdown:
        Path(args.markdown).write_text(md + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
