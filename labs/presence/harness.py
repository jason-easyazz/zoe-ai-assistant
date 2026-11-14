"""Presence-fusion lab harness: run ``services/zoe-data/presence_fusion.py`` over synthetic household traces and print THE NUMBERS.

    python3 labs/presence/harness.py                 # all scenarios, 3 seeds x 28 days, markdown tables to stdout
    python3 labs/presence/harness.py --days 7 --seeds 1 --only today,phone
    python3 labs/presence/harness.py --seed 2 --only phone --save runs.jsonl   # one short chunk; then --report runs.jsonl

Stdlib only; no model, no network, no service. Ground truth comes from ``household.py``.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "services" / "zoe-data"))

import household as hh  # noqa: E402
import presence_fusion as pf  # noqa: E402

SAMPLE_EVERY_MIN = 5
LEFT_WINDOW = (-5 * 60.0, 90 * 60.0)
ARRIVE_WINDOW = (-5 * 60.0, 60 * 60.0)
FALSE_LEFT_TARGET = 0.03          # per adult (phone owner) day: about one false "left" a month per person

SCENARIOS = {
    "today": hh.TODAY,
    "today+door": hh.TODAY + ("door",),
    "today+ble": hh.TODAY + ("ble",),
    "today+mmwave": hh.TODAY + ("mmwave",),
    "today+camera": hh.TODAY + ("camera",),
    "phone": hh.TODAY + hh.PHONE,
    "phone+door": hh.TODAY + hh.PHONE + ("door",),
    "phone+ble": hh.TODAY + hh.PHONE + ("ble",),
    "phone+mmwave": hh.TODAY + hh.PHONE + ("mmwave",),
    "phone+camera": hh.TODAY + hh.PHONE + ("camera",),
    "phone+all": hh.TODAY + hh.PHONE + hh.HARDWARE,
}


def _fusion(**kw):
    f = pf.PresenceFusion(start=hh.DAY0, **kw)
    f.register("adult_a")
    f.register("adult_b")
    f.register("child_c", role="child", guardians=("adult_a", "adult_b"))
    return f


def _transitions(states, toward_away):
    out = []
    for m in range(1, len(states)):
        was, now = states[m - 1] == hh.AWAY, states[m] == hh.AWAY
        if toward_away and not was and now:
            out.append(hh.DAY0 + m * 60.0)
        if not toward_away and was and not now:
            out.append(hh.DAY0 + m * 60.0)
    return out


def _match(truth_times, event_times, window):
    """Greedy one-to-one match; returns (delays of matched truths, count of unmatched events)."""
    used, delays = set(), []
    for tt in truth_times:
        best = None
        for i, et in enumerate(event_times):
            if i not in used and window[0] <= et - tt <= window[1]:
                best = i
                break
        if best is not None:
            used.add(best)
            delays.append(event_times[best] - tt)
    return delays, len(event_times) - len(used)


def run(trace, families, **kw):
    f = _fusion(**kw)
    obs = trace.observations(families)
    events, i, n = [], 0, len(obs)
    total_min = trace.days * 1440
    conf = {p: {"hh": 0, "ha": 0, "hu": 0, "ah": 0, "aa": 0, "au": 0} for p in hh.MEMBERS}
    room = {p: [0, 0, 0] for p in hh.MEMBERS}          # samples home, room given, room right
    gate = [0, 0, 0, 0]                                 # alone: n, said yes | with others: n, said yes
    for m in range(total_min):
        t = hh.DAY0 + m * 60.0
        while i < n and obs[i][0] <= t:
            o = obs[i]
            events += f.observe(pf.Observation(o[0], o[2], o[3], o[4], o[5], o[6] if len(o) > 6 else 1))
            i += 1
        events += f.step(t)
        if m % SAMPLE_EVERY_MIN:
            continue
        for p in hh.MEMBERS:
            truth = trace.truth[p][m]
            st = f.belief(p, t).state
            key = ("a" if truth == hh.AWAY else "h") + {"home": "h", "away": "a", "unknown": "u"}[st]
            conf[p][key] += 1
            if truth != hh.AWAY:
                where = f.room_of(p, t)[0]
                room[p][0] += 1
                room[p][1] += where is not None
                room[p][2] += where == truth
        if trace.truth["adult_a"][m] == hh.PANEL_ROOM and not trace.asleep["adult_a"][m]:
            others = any(trace.truth[q][m] == hh.PANEL_ROOM for q in ("adult_b", "child_c", "guest_g"))
            said = f.others_present(hh.PANEL_ROOM, "adult_a", t) == "yes"
            gate[2 if others else 0] += 1
            gate[3 if others else 1] += said
    return f, events, conf, room, gate


def score(trace, events, conf, room, gate):
    persons = {}
    for p in hh.MEMBERS:
        c = conf[p]
        def at(e):
            return trace.truth[p][min(len(trace.truth[p]) - 1, int((e.t - hh.DAY0) // 60))]
        # a "left" decided after the person is back (or an "arrived" while they are out) is wrong, whatever is near it
        lefts = [e.t for e in events if e.person == p and e.kind == "left" and at(e) == hh.AWAY]
        bad_l = sum(1 for e in events if e.person == p and e.kind == "left" and at(e) != hh.AWAY)
        arrs = [e.t for e in events if e.person == p and e.kind == "arrived" and at(e) != hh.AWAY]
        bad_a = sum(1 for e in events if e.person == p and e.kind == "arrived" and at(e) == hh.AWAY)
        woke = [e.t for e in events if e.person == p and e.kind == "woke"]
        ld, lf = _match(_transitions(trace.truth[p], True), lefts, LEFT_WINDOW)
        ad, af = _match(_transitions(trace.truth[p], False), arrs, ARRIVE_WINDOW)
        lf, af = lf + bad_l, af + bad_a
        wd, wf = _match(trace.wakes[p], woke, (-30 * 60.0, 3 * 3600.0))
        persons[p] = {
            "home_p": c["hh"] / max(1, c["hh"] + c["ah"]), "home_r": c["hh"] / max(1, c["hh"] + c["ha"] + c["hu"]),
            "away_p": c["aa"] / max(1, c["aa"] + c["ha"]), "away_r": c["aa"] / max(1, c["aa"] + c["ah"] + c["au"]),
            "unknown": (c["hu"] + c["au"]) / max(1, sum(c.values())),
            "left_n": len(_transitions(trace.truth[p], True)), "left_hit": len(ld), "left_delays": ld, "left_false": lf,
            "arr_n": len(_transitions(trace.truth[p], False)), "arr_hit": len(ad), "arr_delays": ad, "arr_false": af,
            "woke_hit": len(wd), "woke_n": len(trace.wakes[p]), "woke_delays": wd, "woke_false": wf,
            "room_cov": room[p][1] / max(1, room[p][0]), "room_acc": room[p][2] / max(1, room[p][1]),
        }
    persons["_gate"] = {"false_yes_alone": gate[1] / max(1, gate[0]), "yes_with_others": gate[3] / max(1, gate[2])}
    return persons


def _agg(rows, key):
    vals = [r[key] for r in rows]
    return sum(vals) / len(vals)


def summarise(results, days):
    """results: list of per-seed score dicts -> one line of numbers per scenario."""
    tracked = [r[p] for r in results for p in hh.MEMBERS]
    adults = [r[p] for r in results for p in hh.PHONES]
    person_days = len(tracked) * days
    adult_days = len(adults) * days           # only a phone owner can produce a "left" without hardware
    left_delays = [d for r in tracked for d in r["left_delays"]]
    arr_delays = [d for r in tracked for d in r["arr_delays"]]
    wk = [d for r in tracked for d in r["woke_delays"]]
    med = lambda xs: statistics.median(xs) / 60.0 if xs else float("nan")  # noqa: E731
    return {
        "home_p": _agg(tracked, "home_p"), "home_r": _agg(tracked, "home_r"),
        "away_p": _agg(tracked, "away_p"), "away_r": _agg(tracked, "away_r"), "unknown": _agg(tracked, "unknown"),
        "left_recall": sum(r["left_hit"] for r in tracked) / max(1, sum(r["left_n"] for r in tracked)),
        "left_recall_adults": sum(r["left_hit"] for r in adults) / max(1, sum(r["left_n"] for r in adults)),
        "left_med_min": med(left_delays),
        "false_left_per_pd": sum(r["left_false"] for r in tracked) / adult_days,
        "arr_recall": sum(r["arr_hit"] for r in tracked) / max(1, sum(r["arr_n"] for r in tracked)),
        "arr_med_min": med(arr_delays),
        "false_arr_per_pd": sum(r["arr_false"] for r in tracked) / adult_days,
        "woke_recall": sum(r["woke_hit"] for r in tracked) / max(1, sum(r["woke_n"] for r in tracked)),
        "woke_med_min": med(wk), "false_woke_per_pd": sum(r["woke_false"] for r in tracked) / person_days,
        "room_cov": _agg(tracked, "room_cov"), "room_acc": _agg(tracked, "room_acc"),
        "gate_false_yes": sum(r["_gate"]["false_yes_alone"] for r in results) / len(results),
        "gate_yes_others": sum(r["_gate"]["yes_with_others"] for r in results) / len(results),
    }


COLS = [("home_p", "home P"), ("home_r", "home R"), ("away_p", "away P"), ("away_r", "away R"), ("unknown", "unknown"),
        ("left_recall", "left found"), ("left_recall_adults", "left found (phone owners)"), ("left_med_min", "left delay min"),
        ("false_left_per_pd", "false left /adult-day"), ("arr_recall", "arrived found"), ("arr_med_min", "arrived delay min"),
        ("false_arr_per_pd", "false arrived /adult-day"), ("woke_recall", "woke found"), ("woke_med_min", "woke delay min"),
        ("false_woke_per_pd", "false woke /person-day"), ("room_cov", "room known"), ("room_acc", "room right"),
        ("gate_false_yes", "gate: others? yes when alone"), ("gate_yes_others", "gate: yes when others there")]


def table(rows):
    head = "| scenario | " + " | ".join(c[1] for c in COLS) + " |"
    sep = "|---|" + "---|" * len(COLS)
    lines = [head, sep]
    for name, s in rows:
        lines.append(f"| {name} | " + " | ".join(f"{s[k]:.3f}" if k.startswith(("false", "gate")) else f"{s[k]:.2f}" for k, _ in COLS) + " |")
    return "\n".join(lines)


SWEEP = {f"phone dwell={int(d / 60)}m activity={label}": (d, ctx, own)
         for d in (300.0, 600.0, 900.0)
         for label, (ctx, own) in {"none": (0.0, 0.0), "house": (pf.LEFT_CONTEXT_S, 0.0),
                                   "house+own": (pf.LEFT_CONTEXT_S, pf.LEFT_OWN_ACTIVITY_S)}.items()}


def run_named(trace, name):
    if name in SCENARIOS:
        return run(trace, SCENARIOS[name])
    dwell, ctx, own = SWEEP[name]
    return run(trace, SCENARIOS["phone"], dwell_left_s=dwell, left_context_s=ctx, left_own_activity_s=own)


def report(rows_by_name, days, seeds):
    print(f"seeds={seeds} days={days} tracked people={len(hh.MEMBERS)} false-left target <= {FALSE_LEFT_TARGET}/adult-day\n")
    main_rows = [(n, summarise(r, days)) for n, r in rows_by_name.items() if n in SCENARIOS]
    sweep_rows = [(n, summarise(r, days)) for n, r in rows_by_name.items() if n in SWEEP]
    if main_rows:
        print(table(main_rows))
    if sweep_rows:
        print("\n" + table(sweep_rows))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0, help="run this one seed only (chunked runs)")
    ap.add_argument("--only", default="", help="comma-separated scenario / sweep names")
    ap.add_argument("--sweep", action="store_true", help="add the left dwell / activity-rule sweep on the phone scenario")
    ap.add_argument("--save", default="", help="append per-seed scores (JSON lines) here instead of printing")
    ap.add_argument("--report", default="", help="print the tables from a --save file; runs nothing")
    a = ap.parse_args(argv)
    if a.report:
        rows, seeds, days = {}, set(), a.days
        for line in Path(a.report).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            rows.setdefault(rec["name"], []).append(rec["score"])
            seeds.add(rec["seed"])
            days = rec["days"]
        order = [n for n in list(SCENARIOS) + list(SWEEP) if n in rows]
        report({n: rows[n] for n in order}, days, len(seeds))
        return 0
    seeds = [a.seed] if a.seed else list(range(1, a.seeds + 1))
    pool = list(SCENARIOS) + (list(SWEEP) if a.sweep else [])
    names = [n for n in pool + list(SWEEP) if (n in a.only.split(",") if a.only else n in pool)]
    names = list(dict.fromkeys(names))
    rows = {n: [] for n in names}
    for seed in seeds:
        tr = hh.generate(a.days, seed)
        for name in names:
            _, ev, conf, room, gate = run_named(tr, name)
            sc = score(tr, ev, conf, room, gate)
            rows[name].append(sc)
            if a.save:
                with open(a.save, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"name": name, "seed": seed, "days": a.days, "score": sc}) + "\n")
    if not a.save:
        report(rows, a.days, len(seeds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
