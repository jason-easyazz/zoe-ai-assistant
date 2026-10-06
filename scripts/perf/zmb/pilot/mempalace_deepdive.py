"""MemPalace deep-dive pilots (research record docs/research/mempalace-deep-dive-2026-10-06.md).

Cheap, offline probes of MemPalace 3.10.0's parts OTHER than "a Chroma store" (which the HM pilot already measured):
the conversation miner, the memory-type extractor, the L0-L3 wake-up stack, the temporal knowledge graph, the fact
checker, the room taxonomy, the AAAK dialect, the exporter, and a retrieval pilot for the "one-word change of state"
class (Samantha bar S10). Everything is synthetic (the household cast of ``household.py``), deterministic, and runs
in the bake-off venv in the scrubbed env:

    bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/mempalace_deepdive.py <cmd> [--out FILE]

cmd: convo | extractor | kg | s10 | layers | rooms | aaak | export | protocol | entities | stt_names | all

Never touches ``~/.mempalace``, the live Chroma store, Postgres or any service. All state is under ``--workdir``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))                       # scripts/perf
from zmb.pilot import household  # noqa: E402

REPO = HERE.parents[3]
ZOE_DATA = REPO / "services" / "zoe-data"
SCRATCH = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work")


def rss_mb() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmHWM"):
            return int(line.split()[1]) // 1024
    return 0


def wilson(k: int, n: int, z: float = 1.96) -> "list[float]":
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(max(0.0, (c - h) / d), 3), round(min(1.0, (c + h) / d), 3)]


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 3) if n else None, "wilson95": wilson(k, n)}


def _workdir(args, name: str) -> Path:
    p = Path(args.workdir) / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------------------------------------------
# E1 - the conversation miner on a household transcript
# ---------------------------------------------------------------------------------------------------------------
def _assistant_reply(t: dict) -> str:
    return "Thanks, I'll remember that." if t["kind"] != "filler" else "Okay, that's done."


def _write_transcripts(root: Path, turns: "list[dict]", with_reply: bool) -> None:
    by_ud: "dict[tuple[str, int], list[dict]]" = {}
    for t in turns:
        by_ud.setdefault((t["user"], t["day"]), []).append(t)
    for (user, day), ts in by_ud.items():
        d = root / user
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        for t in ts:
            lines.append(f"> {t['text']}")
            if with_reply:
                lines.append(_assistant_reply(t))
            lines.append("")
        (d / f"day{day}.txt").write_text("\n".join(lines), encoding="utf-8")


def cmd_convo(args) -> dict:
    from mempalace.convo_miner import MIN_CHUNK_SIZE, chunk_exchanges, mine_convos
    from mempalace.palace import get_collection
    from mempalace.searcher import search_memories

    turns, queries, meta = household.generate("hm-pilot-v1", 1000)
    out: dict = {"n_turns": len(turns), "min_chunk_size": MIN_CHUNK_SIZE, "arms": {}}
    short = [t for t in turns if len(t["text"]) < MIN_CHUNK_SIZE]
    out["user_turns_shorter_than_min_chunk"] = rate(len(short), len(turns))
    for arm, with_reply in (("exchange_with_assistant_reply", True), ("user_lines_only", False)):
        wd = _workdir(args, f"convo-{arm}")
        conv, pal = wd / "convos", wd / "palace"
        _write_transcripts(conv, turns, with_reply)
        t0 = time.time()
        for user in household.USERS:
            if (conv / user).is_dir():
                mine_convos(str(conv / user), str(pal), wing=user, agent="deepdive")
        mine_s = round(time.time() - t0, 1)
        col = get_collection(str(pal), create=False)
        got = col.get(include=["documents", "metadatas"])
        docs, metas = got["documents"], got["metadatas"]
        low = [d.lower() for d in docs]
        kept = 0
        for t in turns:
            if any(t["text"].lower() in d for d in low):
                kept += 1
        # unique-fact turns (the queries' gold): are they all present?
        gold_ids = {g for q in queries for g in q["gold"]}
        gold_turns = [t for t in turns if t["turn_id"] in gold_ids]
        gold_kept = sum(1 for t in gold_turns if any(t["text"].lower() in d for d in low))
        rooms: "dict[str, int]" = {}
        for m in metas:
            rooms[m.get("room", "?")] = rooms.get(m.get("room", "?"), 0) + 1

        def hit_vec(q, k):
            r = col.query(query_texts=[q["text"]], n_results=k, where={"wing": q["user"]}, include=["documents"])
            g = next(t for t in turns if t["turn_id"] == q["gold"][0])["text"].lower()
            return [g in d.lower() for d in r["documents"][0]]

        def hit_hyb(q, k):
            r = search_memories(q["text"], str(pal), wing=q["user"], n_results=k)
            g = next(t for t in turns if t["turn_id"] == q["gold"][0])["text"].lower()
            return [g in (h.get("text") or "").lower() for h in r.get("results", [])]

        res = {}
        for name, fn in (("vector_scoped", hit_vec), ("hybrid_scoped_search_memories", hit_hyb)):
            h1 = h5 = 0
            for q in queries:
                hs = fn(q, 5)
                h1 += bool(hs and hs[0])
                h5 += any(hs)
            res[name] = {"hit@1": rate(h1, len(queries)), "hit@5": rate(h5, len(queries))}
        closets = None
        try:
            ccol = get_collection(str(pal), create=False, collection_name="mempalace_closets")
            closets = ccol.count()
        except Exception as exc:  # noqa: BLE001
            closets = f"unavailable: {type(exc).__name__}"
        out["arms"][arm] = {
            "mine_seconds": mine_s, "drawers": len(docs), "turns_text_found_in_a_drawer": rate(kept, len(turns)),
            "gold_fact_turns_found": rate(gold_kept, len(gold_turns)), "rooms": dict(sorted(rooms.items(), key=lambda x: -x[1])),
            "closets": closets, "retrieval": res, "rss_hwm_mb": rss_mb(),
            "metadata_keys": sorted({k for m in metas for k in m}),
        }
    # chunk_exchanges on a hand-built check of the "---" and short-line behaviour (the 3.11 changelog says a bug was fixed)
    sample = "> my dentist is Dr Voss\nGot it.\n---\nNot part of the reply\n> the gate code is 4821\nNoted, thanks.\n> stop\n> pause\nOk\n"
    out["chunker_probe"] = chunk_exchanges(sample)
    return out


# ---------------------------------------------------------------------------------------------------------------
# E2 - general_extractor: what does the "memory type" pass pull out of household speech
# ---------------------------------------------------------------------------------------------------------------
def cmd_extractor(args) -> dict:
    from mempalace.general_extractor import extract_memories

    turns, _q, _m = household.generate("hm-pilot-v1", 1000)
    out: dict = {}
    for user in ("dana", "tove"):
        text = "\n\n".join(t["text"] for t in turns if t["user"] == user)
        chunks = extract_memories(text)
        types_ = {}
        for c in chunks:
            types_[c["memory_type"]] = types_.get(c["memory_type"], 0) + 1
        fact_turns = [t for t in turns if t["user"] == user and t["kind"] not in ("filler", "near_dup")]
        covered = [t["kind"] for t in fact_turns if any(t["text"][:30] in c["content"] for c in chunks)]
        out[user] = {"turns": sum(1 for t in turns if t["user"] == user), "chunks": len(chunks), "by_type": types_,
                     "fact_turns": len(fact_turns), "fact_turns_covered": covered}
    # labelled sentence set: five types, household flavour; a coarse "does it type it right" check
    labelled = [
        ("decision", "We decided to go with the Quay Street vet because they open on Saturdays."),
        ("decision", "I chose the blue sofa because the grey one was too dark."),
        ("decision", "We went with the early flight since the kids wake up at five anyway."),
        ("preference", "I always prefer tea in the afternoon, never coffee after three."),
        ("preference", "Leo hates mushrooms, please never put them in his lunch."),
        ("preference", "I like the lights dimmed low when we watch films."),
        ("milestone", "Mika finally learned to ride her bike without stabilisers today!"),
        ("milestone", "We got the new boiler working at last, it was a breakthrough."),
        ("problem", "The back gate keeps jamming, the hinge is rusted and that's the root cause."),
        ("problem", "The dishwasher broke again, the pump seal was the issue and I fixed it."),
        ("emotional", "I felt really sad at school today because nobody sat with me."),
        ("emotional", "I'm so worried about mum, I love her and I'm scared."),
        ("none", "turn on the kitchen lights"),
        ("none", "set a timer for ten minutes"),
        ("none", "what's the weather like today"),
        ("none", "My dentist is Dr Voss and the surgery is on Elm Street."),
        ("none", "The side gate code is 4821, please don't share it."),
        ("none", "I am allergic to kiwi, it makes my lips swell."),
    ]
    rows = []
    for gold, s in labelled:
        got = extract_memories(s)
        rows.append({"gold": gold, "text": s, "got": [c["memory_type"] for c in got]})
    controls = [  # the prose it was built for (long, project-style): the instrument must light up here
        "We decided to use Postgres instead of MySQL because the JSON support is better and the team already knows it. "
        "After a long debate about the trade-offs we went with the managed option, and the migration plan was agreed on Friday.",
        "The deploy kept failing because the config file was missing a trailing comma. It took two hours but the root cause "
        "was finally found and the fix was a one-line change, and then everything worked again.",
    ]
    out["positive_control_long_prose"] = [[c["memory_type"] for c in extract_memories(t)] for t in controls]
    typed = [r for r in rows if r["gold"] != "none"]
    right = sum(1 for r in typed if r["got"] and r["got"][0] == r["gold"])
    extracted_any = sum(1 for r in typed if r["got"])
    none_rows = [r for r in rows if r["gold"] == "none"]
    out["labelled"] = {"typed_correct_first": rate(right, len(typed)), "typed_extracted_any": rate(extracted_any, len(typed)),
                       "non_memory_wrongly_extracted": rate(sum(1 for r in none_rows if r["got"]), len(none_rows)),
                       "household_facts_in_none_set": [{"text": r["text"], "got": r["got"]} for r in none_rows[3:]],
                       "rows": rows}
    return out


# ---------------------------------------------------------------------------------------------------------------
# E3 - knowledge graph + fact checker on household shapes
# ---------------------------------------------------------------------------------------------------------------
def cmd_kg(args) -> dict:
    from mempalace.knowledge_graph import KnowledgeGraph

    wd = _workdir(args, "kg")
    kg = KnowledgeGraph(db_path=str(wd / "kg.sqlite3"))
    out: dict = {}
    # (a) a value that changes: two ways. Hand-rolled add vs supersede.
    kg.add_triple("Dana", "lives_in", "Dunedin", valid_from="2024-01-01")
    kg.add_triple("Dana", "lives_in", "Hobart", valid_from="2026-09-01")          # nobody closed Dunedin
    cur = [f["object"] for f in kg.query_entity("Dana") if f.get("current")]
    out["add_without_supersede_current_values"] = cur
    kg2 = KnowledgeGraph(db_path=str(wd / "kg2.sqlite3"))
    kg2.add_triple("Dana", "lives_in", "Dunedin", valid_from="2024-01-01")
    kg2.supersede("Dana", "lives_in", "Dunedin", "Hobart", at="2026-09-01")
    out["supersede_current"] = [f["object"] for f in kg2.query_entity("Dana") if f.get("current")]
    out["as_of_2025"] = [f["object"] for f in kg2.query_entity("Dana", as_of="2025-06-01")]
    out["as_of_2026_09_01"] = [f["object"] for f in kg2.query_entity("Dana", as_of="2026-09-01")]
    out["as_of_boundary_day_before"] = [f["object"] for f in kg2.query_entity("Dana", as_of="2026-08-31")]
    # (b) is there any contradiction detection at write time? same predicate, different object, single-valued
    kg3 = KnowledgeGraph(db_path=str(wd / "kg3.sqlite3"))
    kg3.add_triple("Tove", "works_at", "Northgate Library")
    kg3.add_triple("Tove", "works_at", "Quay Street Bakery")
    out["two_open_values_for_a_single_valued_predicate"] = [f["object"] for f in kg3.query_entity("Tove") if f.get("current")]
    # (c) no extraction: the triples exist only because a caller wrote them
    out["triples_without_a_caller"] = 0
    # (d) fact checker - household shapes
    from mempalace.fact_checker import check_text
    pal = wd / "palace"
    pal.mkdir()
    kgp = KnowledgeGraph(db_path=str(pal / "knowledge_graph.sqlite3"))
    kgp.add_triple("Biscuit", "pet", "Dana", valid_from="2023-01-01")
    kgp.add_triple("Mika", "daughter", "Dana", valid_from="2012-01-01")
    kgp.add_triple("Leo", "son", "Dana", valid_from="2015-01-01")
    kgp.add_triple("Kofi", "plumber", "Dana", valid_from="2024-01-01", valid_to="2025-01-01")
    reg = {"people": ["Dana", "Dina", "Mika", "Mia", "Leo", "Leon", "Tove", "Toby", "Kofi"]}
    os.environ.setdefault("MEMPALACE_KNOWN_ENTITIES", "")
    cases = [
        ("relationship_mismatch", "Biscuit is Dana's child"),
        ("relationship_mismatch", "Mika is Dana's sister"),
        ("none", "Mika is Dana's daughter"),
        ("stale_fact", "Kofi is Dana's plumber"),
        ("relationship_mismatch", "Dana has two kids, Mika and Biscuit"),   # the S21 sentence shape: no X-is-Y's-Z pattern
        ("relationship_mismatch", "Dana's child is Biscuit"),               # possessive shape
        ("none", "Biscuit is the dog"),
    ]
    rows = []
    for gold, text in cases:
        try:
            iss = check_text(text, str(pal))
        except Exception as exc:  # noqa: BLE001
            iss = [{"type": f"ERROR {type(exc).__name__}: {exc}"}]
        rows.append({"gold": gold, "text": text, "issues": [i.get("type") for i in iss]})
    out["fact_checker_rows"] = rows
    out["fact_checker_hits"] = rate(sum(1 for r in rows if (r["gold"] == "none") == (not r["issues"]) and (r["gold"] == "none" or r["gold"] in r["issues"])), len(rows))
    # (e) similar_name: needs known_entities.json under HOME/.mempalace; a household whose names sit within 2 edits
    import mempalace.miner as mm
    mm._ENTITY_REGISTRY_PATH = str(wd / "known_entities.json")
    (wd / "known_entities.json").write_text(json.dumps({"people": ["Dana", "Tove", "Mika", "Leo", "Priya", "Ravi", "Kofi"]}))
    mm._ENTITY_REGISTRY_CACHE.update({"mtime": None})
    turns, _q, _m = household.generate("hm-pilot-v1", 1000)
    named = [t for t in turns if re.search(r"\b(Dana|Tove|Mika|Leo|Priya|Ravi|Kofi)\b", t["text"])]
    flags = [i for t in named for i in check_text(t["text"], str(pal)) if i["type"] == "similar_name"]
    out["similar_name_distinct_household"] = {"turns_naming_a_member": len(named), "similar_name_flags": len(flags)}
    (wd / "known_entities.json").write_text(json.dumps({"people": ["Dana", "Dina", "Tove", "Toby", "Mika", "Mia", "Leo", "Leon", "Priya", "Ravi", "Kofi"]}))
    mm._ENTITY_REGISTRY_CACHE.update({"mtime": None})
    time.sleep(0.01)
    flags2 = [i for t in named for i in check_text(t["text"], str(pal)) if i["type"] == "similar_name"]
    out["similar_name_close_names_household"] = {"turns_naming_a_member": len(named), "similar_name_flags": len(flags2),
                                                  "example": flags2[0]["detail"] if flags2 else None}
    (wd / "known_entities.json").write_text(json.dumps({"people": ["Dana", "Tove", "Mika", "Leo"]}))
    mm._ENTITY_REGISTRY_CACHE.update({"mtime": None})
    out["typo_case_Dana_vs_Dane"] = [i["type"] for i in check_text("Dane is picking up Mika", str(pal))]
    out["typo_case_Dana_exact"] = [i["type"] for i in check_text("Dana is picking up Mika", str(pal))]
    return out


# ---------------------------------------------------------------------------------------------------------------
# E4 - the one-word change class (S10): can retrieval find the row a change retires?
# ---------------------------------------------------------------------------------------------------------------
PAIRS = [
    ("User plays the cello in a community orchestra on Tuesday evenings.", "I gave up the cello.", "User gave up the cello."),
    ("User runs five kilometres every morning before work.", "I stopped running.", "User stopped running."),
    ("User has a season ticket for the Harbour Rovers.", "I let my season ticket lapse.", "User let their Harbour Rovers season ticket lapse."),
    ("User drives a blue Corolla.", "I sold the Corolla.", "User sold the Corolla."),
    ("User works at Northgate Library three days a week.", "I left the library.", "User left Northgate Library."),
    ("User takes piano lessons on Thursdays with Ms Halvorsen.", "I quit piano lessons.", "User quit piano lessons."),
    ("User is vegetarian.", "I eat meat again.", "User eats meat again."),
    ("User smokes a pipe in the evening.", "I have quit smoking.", "User quit smoking."),
    ("User goes to the climbing gym on Saturdays.", "I cancelled my climbing gym membership.", "User cancelled their climbing gym membership."),
    ("User has a weekly pottery class at the community hall.", "I finished the pottery class.", "User finished the pottery class."),
    ("User keeps two goldfish in the lounge.", "The goldfish died.", "User's goldfish died."),
    ("User subscribes to the Daily Ledger newspaper.", "I cancelled the newspaper.", "User cancelled the Daily Ledger subscription."),
    ("User is learning Portuguese with an app every night.", "I dropped Portuguese.", "User dropped Portuguese."),
    ("User coaches the under-tens netball team.", "I stepped down as netball coach.", "User stepped down as netball coach."),
    ("User grows tomatoes in the back garden.", "I ripped out the tomatoes.", "User ripped out the tomatoes."),
    ("User takes a daily blood pressure tablet.", "The doctor took me off the tablets.", "User no longer takes blood pressure tablets."),
    ("User volunteers at the food bank on Mondays.", "I stopped volunteering at the food bank.", "User stopped volunteering at the food bank."),
    ("User drinks coffee with breakfast.", "I switched to tea.", "User switched from coffee to tea."),
    ("User has a membership at the Quay Street pool.", "I no longer go swimming.", "User no longer goes swimming."),
    ("User writes in a gratitude journal each night.", "I gave up journaling.", "User gave up journaling."),
    ("User walks the dog, Biscuit, at six every evening.", "Mika took over walking Biscuit.", "Mika walks Biscuit now."),
    ("User plays tennis on Sunday mornings.", "I hung up my racquet.", "User stopped playing tennis."),
    ("User is training for the Harbour Half Marathon in March.", "I dropped out of the half marathon.", "User dropped out of the Harbour Half Marathon."),
    ("User sings in the church choir.", "I left the choir.", "User left the choir."),
    ("User rents a flat on Elm Street.", "We bought a house.", "User bought a house."),
    ("User has a standing Friday lunch with Tove.", "Tove and I stopped doing Friday lunches.", "User stopped the Friday lunches with Tove."),
    ("User uses a standing desk at work.", "I got rid of the standing desk.", "User got rid of the standing desk."),
    ("User plays chess online every night.", "I deleted my chess account.", "User deleted their chess account."),
    ("User takes the 7:40 bus to work.", "I cycle to work now.", "User cycles to work now."),
    ("User paints watercolours at the weekend.", "I haven't painted in months, I gave it up.", "User gave up painting."),
]

#: one NON-retiring statement about the same object per pair (index-aligned with PAIRS); HARD_NEGATIVES carry a cue
#: word but end nothing ("almost gave up", "not giving up"): what a retrieval-only gate cannot separate.
NEGATIVES = [
    "User played the cello at the orchestra last night.", "User ran faster than usual this morning.",
    "User watched the Harbour Rovers on Saturday.", "User washed the Corolla at the weekend.",
    "User took a book back to Northgate Library.", "User bought new piano lesson books.",
    "User cooked a vegetarian curry for the neighbours.", "User lit the pipe of a visiting uncle.",
    "User took Mika to the climbing gym.", "User showed the pottery class bowl to Tove.",
    "User fed the goldfish extra food.", "User read the Daily Ledger crossword.",
    "User practised Portuguese with a neighbour.", "User watched the netball team win.",
    "User picked the tomatoes in the garden.", "User collected the blood pressure tablets from the chemist.",
    "User drove to the food bank on Monday.", "User drank coffee at the cafe.",
    "User went to the Quay Street pool with Leo.", "User wrote a long journal entry tonight.",
    "User walked Biscuit to the park.", "User watched tennis on television.",
    "User ran the Harbour Half Marathon course on foot.", "User sang in the church choir at Christmas.",
    "User walked past the flat on Elm Street.", "User had Friday lunch with Priya.",
    "User moved the standing desk to the window.", "User played chess online with Ravi.",
    "User took the 7:40 bus on Tuesday.", "User painted a watercolour for Tove.",
]
HARD_NEGATIVES = [
    "User almost gave up the cello but kept going.", "User is not giving up running.",
    "User nearly sold the Corolla but changed their mind.", "User stopped running to tie a shoelace.",
    "User quit the meeting early.", "User dropped Mika at the climbing gym.",
    "User finished the pottery bowl.", "User gave up a seat on the bus.",
    "User left the library early.", "User cancelled the dentist appointment.",
]

GENERIC = [
    "User likes listening to jazz in the kitchen.", "User's daughter plays the violin in the school orchestra.",
    "User owns a red bicycle.", "User prefers window seats on flights.", "User's mother lives in Perth.",
    "User is allergic to kiwi.", "User's favourite film is Local Hero.", "User has two children, Mika and Leo.",
    "User's dentist is Dr Voss.", "User's birthday is on the 12th of March.", "User likes the lounge lights dimmed.",
    "User's car insurance renews in June.", "User's plumber is Kofi Mensah.", "User takes sugar in tea.",
    "User's gate code is private.", "User wants to visit Lisbon next spring.", "User's sister Priya lives in Auckland.",
    "User likes oat milk.", "User has a standing dentist check-up every six months.", "User reads the news on the radio.",
    "User's neighbour is called Ravi.", "User wakes at six thirty.", "User doesn't like loud music late at night.",
    "User's boiler was serviced in October.", "User is saving for a new sofa.", "User likes jasmine tea.",
    "User's favourite colour is green.", "User has a library card at Northgate.", "User keeps spare keys with the neighbour.",
    "User likes quiet mornings.", "User's phone is on the family plan.", "User prefers cash at the market.",
    "User's partner is Tove.", "User's dog is called Biscuit.", "User likes folk music on Sundays.",
    "User takes the bins out on Thursday.", "User's wifi password is on the fridge.", "User likes to cook on Friday.",
    "User is learning to bake sourdough.", "User's favourite mug is the blue one.",
]


def _swap_subject(old: str, person: str) -> str:
    return old.replace("User's", f"{person}'s", 1) if old.startswith("User's") else old.replace("User", person, 1)


def _load_zoe_supersede():
    """memory_supersede.py with its two heavyweight imports stubbed from source constants (no service imported)."""
    import ast
    src = (ZOE_DATA / "memory_digest.py").read_text(encoding="utf-8")
    val = None
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_AFFECT_STOPWORDS":
            val = eval(compile(ast.Expression(n.value), "x", "eval"))
    md = types.ModuleType("memory_digest")
    md._AFFECT_STOPWORDS = val
    sys.modules["memory_digest"] = md
    uc = types.ModuleType("user_model_card")
    uc.ALLOWED_TYPES, uc.STATE_CHANGE = frozenset(), "state_change"
    sys.modules["user_model_card"] = uc
    sys.path.insert(0, str(ZOE_DATA))
    import memory_supersede  # noqa: WPS433
    return memory_supersede


def cmd_s10(args) -> dict:
    import numpy as np
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    ms = _load_zoe_supersede()
    olds = [p[0] for p in PAIRS]
    swaps = [_swap_subject(o, "User's sister Priya") for o in olds]
    pool = olds + swaps + GENERIC
    ef = ONNXMiniLM_L6_V2()
    def enc(xs):                      # batches of 8: a larger ONNX batch grows the arena (HM pilot, section 4.1)
        return np.asarray([v for i in range(0, len(xs), 8) for v in ef(xs[i:i + 8])], dtype="float32")
    P = enc(pool)
    P /= np.linalg.norm(P, axis=1, keepdims=True)
    out: dict = {"pairs": len(PAIRS), "pool": len(pool), "pool_composition": "30 old facts + 30 other-person copies + 40 generic"}

    # Zoe's deterministic rule (the documented S10 failure) on the extracted-fact form
    z_topic = [ms.same_topic(p[2], p[0]) for p in PAIRS]
    z_cue = [ms.fact_cue(p[2]) is not None for p in PAIRS]
    z_false = [ms.same_topic(p[2], s) for p, s in zip(PAIRS, swaps)]
    out["zoe_rule_same_topic_detects_old_row"] = rate(sum(z_topic), len(PAIRS))
    out["zoe_rule_cue_present_in_new_fact"] = rate(sum(z_cue), len(PAIRS))
    out["zoe_rule_false_retire_other_person_copy"] = rate(sum(z_false), len(PAIRS))
    out["zoe_rule_misses"] = [p[2] for p, ok in zip(PAIRS, z_topic) if not ok][:12]

    # embedding retrieval of the row to retire, from (a) what the user said, (b) the extracted fact
    for name, idx in (("utterance", 1), ("extracted_fact", 2)):
        Q = enc([p[idx] for p in PAIRS])
        Q /= np.linalg.norm(Q, axis=1, keepdims=True)
        sims = Q @ P.T
        r1 = r3 = r5 = confuser_above = 0
        misses: "list[str]" = []
        for i in range(len(PAIRS)):
            order = np.argsort(-sims[i])
            rank = int(np.where(order == i)[0][0]) + 1
            r1 += rank == 1
            r3 += rank <= 3
            r5 += rank <= 5
            confuser_above += bool(sims[i, len(PAIRS) + i] > sims[i, i])
            if rank > 1:
                misses.append(f"{PAIRS[i][idx]} -> rank {rank}")
        out[f"embedding_{name}"] = {"old_row_top1": rate(r1, len(PAIRS)), "old_row_top3": rate(r3, len(PAIRS)),
                                    "old_row_top5": rate(r5, len(PAIRS)),
                                    "other_person_copy_outranks_old_row": rate(int(confuser_above), len(PAIRS)),
                                    "not_top1": misses}
    # a deterministic relaxation that needs no memory system: cue + same subject + the cue-less object tokens contained in the old row
    def relaxed(new: str, old: str) -> bool:
        if not ms.same_subject(new, old) or not ms.same_attribute(new, old):
            return False
        a = ms.topic_tokens(new) - ms._subject_tokens(new)
        b = ms.topic_tokens(old) - ms._subject_tokens(old)
        cue_tokens = set()
        c = ms.fact_cue(new)
        if c:
            cue_tokens = set(re.findall(r"[a-z]+", c.pattern.pattern.lower()))
        a = {t for t in a if t not in ("gave", "given", "stopped", "quit", "left", "sold", "dropped", "cancelled", "finished",
                                         "died", "deleted", "switched", "stepped", "ripped", "longer", "bought", "cycles", "walks")}
        return bool(a & b) and ms.fact_cue(new) is not None
    r_ok = [relaxed(p[2], p[0]) for p in PAIRS]
    r_false = [relaxed(p[2], s) for p, s in zip(PAIRS, swaps)]
    r_generic = [relaxed(p[2], g) for p in PAIRS for g in GENERIC]
    out["relaxed_rule_cue_plus_shared_object_token"] = {
        "detects_old_row": rate(sum(r_ok), len(PAIRS)),
        "false_retire_other_person_copy": rate(sum(r_false), len(PAIRS)),
        "false_retire_generic_distractors": rate(sum(r_generic), len(r_generic)),
    }
    # negatives: statements about the same object that retire nothing. Retrieval alone cannot separate them.
    allneg = NEGATIVES + HARD_NEGATIVES
    hard_old = [0, 1, 3, 1, 4, 8, 9, 0, 4, 9]          # the pair each hard negative is about (index into PAIRS)
    Nn = enc(allneg)
    Nn /= np.linalg.norm(Nn, axis=1, keepdims=True)
    simn = Nn @ P.T
    tgt = list(range(len(NEGATIVES))) + hard_old
    top1_is_old = sum(1 for i, t in enumerate(tgt) if int(np.argmax(simn[i])) == t)
    cue_fp = [ms.fact_cue(x) is not None for x in allneg]
    topic_fp = [ms.same_topic(x, PAIRS[t][0]) for x, t in zip(allneg, tgt)]
    out["negatives"] = {
        "n": len(allneg), "retrieval_top1_is_the_row_it_mentions": rate(top1_is_old, len(allneg)),
        "zoe_cue_table_fires_on_a_non_retirement": rate(sum(cue_fp), len(allneg)),
        "zoe_cue_fires_on_the_10_hard_negatives": rate(sum(cue_fp[len(NEGATIVES):]), len(HARD_NEGATIVES)),
        "zoe_same_topic_true_on_a_non_retirement": rate(sum(topic_fp), len(allneg)),
    }
    # the two-stage gate a protocol would run: candidate by retrieval, decision by cue (Zoe's table)
    tp = sum(1 for z in z_cue if z)
    out["retrieval_plus_zoe_cue_gate"] = {"retires_on_true_changes": rate(tp, len(PAIRS)),
                                           "retires_on_non_changes": rate(sum(cue_fp), len(allneg))}
    # MemPalace route: the KG needs (subject, predicate, old_object, new_object). Who supplies them?
    out["mempalace_kg_supersede_requires"] = "a caller that already knows the old object; nothing in mempalace/ derives it from a sentence"
    return out


# ---------------------------------------------------------------------------------------------------------------
# E5 - L0-L3 wake-up stack on a household palace
# ---------------------------------------------------------------------------------------------------------------
def cmd_layers(args) -> dict:
    from mempalace.layers import Layer1, MemoryStack
    from mempalace.palace import get_collection

    turns, _q, _m = household.generate("hm-pilot-v1", 1000)
    wd = _workdir(args, "layers")
    pal = wd / "palace"
    col = get_collection(str(pal), create=True)
    mine = [t for t in turns if t["user"] == "dana"]
    base = time.strftime("2026-10-0%dT09:00:00")
    ids, docs, metas = [], [], []
    for i, t in enumerate(mine):
        ids.append(t["turn_id"])
        docs.append(t["text"])
        metas.append({"wing": "dana", "room": "voice", "source_file": f"dana:day{t['day']}", "added_by": "pilot",
                      "filed_at": f"2026-10-0{t['day']}T09:{i % 60:02d}:00"})
    for s in range(0, len(ids), 8):
        col.upsert(ids=ids[s:s + 8], documents=docs[s:s + 8], metadatas=metas[s:s + 8])
    ident = wd / "identity.txt"
    ident.write_text("I am Zoe, the household assistant for the Okonkwo family. People: Dana (parent), Tove (parent), Mika and Leo (children).", encoding="utf-8")
    l1 = Layer1(palace_path=str(pal), wing="dana").generate()
    stack = MemoryStack(palace_path=str(pal), identity_path=str(ident))
    wake = stack.wake_up(wing="dana")
    fact_kinds = {}
    for t in mine:
        if t["kind"] not in ("filler", "near_dup"):
            fact_kinds[t["kind"]] = t["text"][:60]
    in_wake = {k: (v[:40].lower() in wake.lower()) for k, v in fact_kinds.items()}
    filler_in_l1 = sum(1 for line in l1.splitlines() if line.strip().startswith("- ") and any(
        line.strip()[2:].lower().startswith(t["text"].lower()[:20]) for t in mine if t["kind"] == "filler"))
    return {"dana_turns": len(mine), "wake_up_chars": len(wake), "wake_up_tokens_est": len(wake) // 4,
            "l1_lines": [ln for ln in l1.splitlines()][:25], "facts_present_in_wake_up": in_wake,
            "facts_present": rate(sum(in_wake.values()), len(in_wake)), "l1_filler_lines": filler_in_l1,
            "l3_search_example": stack.search("what am I allergic to", wing="dana", n_results=3)[:600]}


# ---------------------------------------------------------------------------------------------------------------
# E6 - rooms as a routing scheme
# ---------------------------------------------------------------------------------------------------------------
HOUSEHOLD_ROOMS = {
    "health": ["allergic", "allergy", "doctor", "tablet", "dentist", "vet", "swell"],
    "family": ["kids", "children", "birthday", "sister", "mum", "Mika", "Leo"],
    "home": ["gate", "plumber", "boiler", "lights", "heating", "oven", "keys"],
    "work": ["work", "job", "office", "library", "bakery", "studio"],
    "calendar": ["concert", "appointment", "tomorrow", "Thursday", "remind"],
    "media": ["music", "film", "playlist", "song", "volume"],
}


def cmd_rooms(args) -> dict:
    """MemPalace's own room detector (``detect_convo_room``) on household turns, and how a room filter changes recall."""
    from mempalace.convo_miner import TOPIC_KEYWORDS, detect_convo_room
    from mempalace.palace import get_collection

    turns, queries, _m = household.generate("hm-pilot-v1", 1000)
    out: dict = {"mempalace_topic_rooms": sorted(TOPIC_KEYWORDS)}
    kinds = ["dentist", "vet", "concert", "gate", "allergy", "film", "work", "kids", "birthday", "plumber"]
    gold_room = {}
    for k in kinds:
        t = next(t for t in turns if t["kind"] == k)
        gold_room[k] = detect_convo_room(t["text"])
    out["mempalace_room_of_each_fact_turn"] = gold_room
    out["distinct_rooms_over_10_facts"] = len(set(gold_room.values()))
    out["filler_rooms"] = {}
    for t in [t for t in turns if t["kind"] == "filler"][:200]:
        r = detect_convo_room(t["text"])
        out["filler_rooms"][r] = out["filler_rooms"].get(r, 0) + 1
    # household taxonomy (hand-built, 6 rooms) routing by keywords; then recall with room filter
    def route(text: str) -> str:
        best, bs = "general", 0
        for room, kws in HOUSEHOLD_ROOMS.items():
            s = sum(1 for kw in kws if kw.lower() in text.lower())
            if s > bs:
                best, bs = room, s
        return best
    wd = _workdir(args, "rooms")
    col = get_collection(str(wd / "palace"), create=True)
    ids, docs, metas = [], [], []
    for t in turns:
        ids.append(t["turn_id"])
        docs.append(t["text"])
        metas.append({"wing": t["user"], "room": route(t["text"]), "source_file": f"{t['user']}:d{t['day']}", "added_by": "pilot",
                      "filed_at": f"2026-10-0{t['day']}T09:00:00"})
    for s in range(0, len(ids), 8):
        col.upsert(ids=ids[s:s + 8], documents=docs[s:s + 8], metadatas=metas[s:s + 8])
    by_id = {t["turn_id"]: t for t in turns}
    room_of = {t["turn_id"]: route(t["text"]) for t in turns}
    rooms_all = sorted(set(room_of.values()))
    counts = {"wing_only": 0, "correct_room": 0, "wrong_room_avg": 0.0, "route_by_query_matches_gold": 0}
    n = len(queries)
    wrong_hits = 0
    wrong_trials = 0
    for q in queries:
        gid = q["gold"][0]
        base = {"wing": q["user"]}
        r = col.query(query_texts=[q["text"]], n_results=5, where=base, include=[])
        counts["wing_only"] += gid in r["ids"][0]
        gr = room_of[gid]
        r = col.query(query_texts=[q["text"]], n_results=5, where={"$and": [{"wing": q["user"]}, {"room": gr}]}, include=[])
        counts["correct_room"] += gid in r["ids"][0]
        for room in rooms_all:
            if room == gr:
                continue
            wrong_trials += 1
            r = col.query(query_texts=[q["text"]], n_results=5, where={"$and": [{"wing": q["user"]}, {"room": room}]}, include=[])
            wrong_hits += gid in r["ids"][0]
        counts["route_by_query_matches_gold"] += route(q["text"]) == gr
        rr = route(q["text"])                                              # what a real router would filter on
        hard = col.query(query_texts=[q["text"]], n_results=5, where={"$and": [{"wing": q["user"]}, {"room": rr}]}, include=[])["ids"][0]
        counts["routed_hard"] = counts.get("routed_hard", 0) + (gid in hard)
        wing5 = col.query(query_texts=[q["text"]], n_results=5, where=base, include=[])["ids"][0]
        soft = list(dict.fromkeys(hard[:2] + wing5))[:5]                   # room as a prior: 2 slots, wing fills the rest
        counts["routed_soft"] = counts.get("routed_soft", 0) + (gid in soft)
    out["recall_hit@5"] = {"wing_only": rate(counts["wing_only"], n), "correct_room": rate(counts["correct_room"], n),
                           "any_wrong_room": rate(wrong_hits, wrong_trials),
                           "keyword_router_picks_the_gold_room": rate(counts["route_by_query_matches_gold"], n),
                           "routed_room_hard_filter": rate(counts["routed_hard"], n),
                           "routed_room_soft_prior_2_of_5": rate(counts["routed_soft"], n)}
    out["room_population"] = {r: sum(1 for v in room_of.values() if v == r) for r in rooms_all}
    return out


# ---------------------------------------------------------------------------------------------------------------
# E7 AAAK, E8 exporter, E9 protocol size
# ---------------------------------------------------------------------------------------------------------------
def cmd_aaak(args) -> dict:
    from mempalace.dialect import Dialect

    d = Dialect()
    samples = [
        "My dentist is Dr Voss and the surgery is on Elm Street.",
        "I am allergic to kiwi, it makes my lips swell.",
        "I have two kids, Mika and Leo, and the dog is called Biscuit.",
        "The side gate code is 4821, please don't share it.",
        "Biscuit's vet appointment is on Thursday at 3:30 with Dr Adeyemi.",
        "I felt really sad at school today because nobody sat with me.",
        "I play the cello in a community orchestra on Tuesday evenings and I gave it up last week because my wrist hurts.",
    ]
    rows = []
    for s in samples:
        c = d.compress(s)
        rows.append({"original": s, "aaak": c, **d.compression_stats(s, c)})
    keep = [(r["original"], r["aaak"]) for r in rows]
    nums = [(s, re.findall(r"\d{3,}", s)) for s, _ in keep if re.search(r"\d{3,}", s)]
    names = [(s, re.findall(r"\b[A-Z][a-z]{2,}\b", s)) for s, _ in keep]
    lost_nums = sum(1 for (s, ns), (_, a) in zip(nums, [k for k in keep if re.search(r"\d{3,}", k[0])]) for n in ns if n not in a)
    lost_names = sum(1 for (s, ns), (_, a) in zip(names, keep) for n in ns if n not in a and n[:3].upper() not in a)
    return {"rows": rows, "numbers_dropped": lost_nums, "capitalised_words_dropped_or_recoded": lost_names,
            "mean_size_ratio": round(sum(r["size_ratio"] for r in rows) / len(rows), 2)}


def cmd_export(args) -> dict:
    from mempalace.exporter import export_palace
    from mempalace.palace import get_collection

    wd = _workdir(args, "export")
    col = get_collection(str(wd / "palace"), create=True)
    col.upsert(ids=["a", "b"], documents=["My dentist is Dr Voss.", "I am allergic to kiwi."],
               metadatas=[{"wing": "dana", "room": "health", "filed_at": "2026-10-01T09:00:00"},
                          {"wing": "dana", "room": "health", "filed_at": "2026-10-02T09:00:00"}])
    res = export_palace(str(wd / "palace"), str(wd / "out"))
    files = sorted(str(p.relative_to(wd / "out")) for p in (wd / "out").rglob("*") if p.is_file())
    text = "\n".join((wd / "out" / f).read_text() for f in files)
    return {"result": res, "files": files, "sample": text[:500], "plaintext_of_every_drawer": "Dr Voss" in text}


def cmd_protocol(args) -> dict:
    import importlib.util
    src = (Path(importlib.util.find_spec("mempalace").origin).parent / "mcp_server" / "tools_read.py").read_text()
    m = re.search(r'PALACE_PROTOCOL = """(.*?)"""', src, re.S)
    proto = m.group(1)
    zoe = (REPO / "labs" / "flue-zoe-brain-2x" / "src" / "agents" / "zoe.ts").read_text()
    soul = (REPO / "labs" / "flue-zoe-brain-2x" / "src" / "soul.ts").read_text()
    blocks = re.findall(r"export const (\w*DOCTRINE) = \[(.*?)\]\.join", zoe, re.S)
    sizes = {name: len(body) for name, body in blocks}
    recall_doctrines = {k: v for k, v in sizes.items() if "RECALL" in k or "CAPTURE" in k or "SESSION" in k}
    return {"mempalace_protocol_chars": len(proto), "mempalace_protocol_tokens_est": len(proto) // 4,
            "mempalace_protocol_rules": 5,
            "zoe_doctrine_chars_by_block": sizes, "zoe_memory_related_doctrine_chars": sum(recall_doctrines.values()),
            "zoe_memory_related_doctrine_tokens_est": sum(recall_doctrines.values()) // 4,
            "zoe_soul_recall_paragraph_chars": len(re.search(r"const RECALL =\s*\n?\s*\"(.*?)\";", soul, re.S).group(1)),
            "note": "chars/4 token estimate; both are upper-bound prose, not tokenizer counts"}


def cmd_entities(args) -> dict:
    """entity_detector on (a) the household voice transcript, (b) a diary-style narrative control with the same cast."""
    from mempalace.entity_detector import detect_entities

    turns, _q, _m = household.generate("hm-pilot-v1", 1000)
    wd = _workdir(args, "entities")
    a = wd / "voice.txt"
    a.write_text("\n".join(t["text"] for t in turns), encoding="utf-8")
    narrative = []
    for i in range(12):
        narrative += [
            f"Dana said Mika had a rough day at school and Leo laughed about it. Dana asked Tove to pick up Biscuit from the vet on day {i}.",
            "Mika told Dana she wanted a bike. Tove replied that Leo should share, and Dana smiled. Biscuit barked at the gate.",
            "Tove said she would call Kofi about the boiler. Dana thought it was a good idea and Mika agreed.",
        ]
    b = wd / "diary.txt"
    b.write_text("\n\n".join(narrative), encoding="utf-8")
    out: dict = {}
    for name, f in (("voice_transcript", a), ("diary_control", b)):
        det = detect_entities([f])
        out[name] = {k: [(e["name"], round(e.get("confidence", 0), 2)) if isinstance(e, dict) else e for e in v][:12]
                     for k, v in det.items()}
    cast = {"Dana", "Tove", "Mika", "Leo", "Biscuit", "Kofi", "Marisol", "Dev"}
    for name in out:
        people = {n for n, _c in out[name].get("people", [])}
        out[name]["cast_found_as_people"] = sorted(people & cast)
        out[name]["non_cast_people"] = sorted(people - cast)
    return out


def cmd_stt_names(args) -> dict:
    """fact_checker's edit-distance (``_edit_distance``, <= 2) as a forget-alias matcher: catch STT misspellings of a forgotten name."""
    from mempalace.fact_checker import _edit_distance

    turns, _q, _m = household.generate("hm-pilot-v1", 1000)
    target = household.FORGOTTEN.lower()
    variants = ["Marisal", "Marysol", "Marizol", "Marisole", "Marissol", "Maricol", "Marisoul", "Marrisol"]
    split_variants = ["Mari sol", "Mari-sol", "Maris ol"]
    toks = lambda t: re.findall(r"[A-Za-z']+", t)
    hit = sum(1 for v in variants if _edit_distance(target, v.lower()) <= 2)
    # adjacent-token joins catch the split spellings
    joined_hit = 0
    for v in split_variants:
        parts = toks(v)
        joined_hit += _edit_distance(target, "".join(parts).lower()) <= 2
    vocab: "dict[str, int]" = {}
    for t in turns:
        for w in toks(t["text"]):
            vocab[w.lower()] = vocab.get(w.lower(), 0) + 1
    near = sorted((w for w in vocab if 0 < _edit_distance(target, w) <= 2))
    near_excluding_exact = [w for w in near if w != target]
    # same check with a looser bound to show where the false positives start
    near3 = sorted(w for w in vocab if 0 < _edit_distance(target, w) <= 3)
    # false-match exposure on a big vocabulary: how many dictionary words sit within 1 / 2 edits of a name, by name length
    exposure: "dict[str, dict]" = {}
    wp = Path("/usr/share/dict/words")
    if wp.is_file():
        words = sorted({w.strip().lower() for w in wp.read_text(errors="ignore").splitlines() if w.strip().isalpha()})
        for name in ("Leo", "Dana", "Tove", "Mika", "Ravi", "Kofi", "Priya", "Teodor", "Marisol", "Biscuit", "Barnaby",
                     "Ottoline", "Percival", "Ignatius", "Philippa", "Henrietta"):
            n = name.lower()
            c1 = c2 = 0
            for w in words:
                if abs(len(w) - len(n)) > 2 or w == n:
                    continue
                d = _edit_distance(n, w)
                c1 += d == 1
                c2 += 0 < d <= 2
            exposure[name] = {"len": len(n), "words_within_1": c1, "words_within_2": c2}
    return {"dictionary_words": len(words) if wp.is_file() else None, "dictionary_exposure_by_name": exposure,
            "variants_caught_within_2": rate(hit, len(variants)), "split_variants_caught_after_join": rate(joined_hit, len(split_variants)),
            "household_vocabulary_words": len(vocab), "other_words_within_2_of_the_forgotten_name": near_excluding_exact,
            "other_words_within_3": near3[:10],
            "privacy_note": "needs the plaintext name (or a phonetic key) at match time; the ZMB ledger stores salted hashes, which cannot be fuzzy-matched"}


CMDS = {"convo": cmd_convo, "extractor": cmd_extractor, "kg": cmd_kg, "s10": cmd_s10, "layers": cmd_layers,
        "rooms": cmd_rooms, "aaak": cmd_aaak, "export": cmd_export, "protocol": cmd_protocol, "entities": cmd_entities, "stt_names": cmd_stt_names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=[*CMDS, "all"])
    ap.add_argument("--workdir", default=str(SCRATCH / "deepdive"))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    Path(args.workdir).mkdir(parents=True, exist_ok=True)
    res = {}
    for name in (CMDS if args.cmd == "all" else [args.cmd]):
        t0 = time.time()
        try:
            res[name] = CMDS[name](args)
        except Exception as exc:  # noqa: BLE001
            import traceback
            res[name] = {"ERROR": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
        res[name]["_seconds"] = round(time.time() - t0, 1)
    res["_rss_hwm_mb"] = rss_mb()
    txt = json.dumps(res, indent=1, default=str)
    if args.out:
        Path(args.out).write_text(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
