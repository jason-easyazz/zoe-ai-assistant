#!/usr/bin/env python3
"""The router LEARNING LOOP, lab (no-subscription self-evolution tier): harvest -> train -> frozen eval -> gate -> proposal -> rollback.

The loop is proven with a tiny STAND-IN classifier (multinomial naive Bayes over hashed word / char n-grams, stdlib only), never the
real FunctionGemma router: training a real adapter competes with the live voice stack for memory. The gate, the forget order, the
frozen guard, the proposal shape and the rollback are what is being proven; the stand-in is only something that can get better or
worse. Nothing here reads a live log, a database, the network or a model.

    python3 loop.py demo --workdir run [--write-summary]    # clean + poisoned runs; journals in run/, totals in results/
    python3 loop.py run --source S.jsonl --forget F.jsonl --workdir W
    python3 loop.py rollback --workdir W                     # put the previous router back, byte for byte
    python3 loop.py status --workdir W
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NEEDLE_CORPUS = REPO / "labs" / "needle-benchmark" / "corpus.jsonl"       # the ratchet's frozen 81 cases (synthetic, committed)
SAFETY_CORPUS = HERE / "data" / "frozen_safety.jsonl"                      # emergency / forget asks that no fast tool may claim
SEEDS_MODULE = REPO / "labs" / "setfit-router" / "labels.py"               # the incumbent's base set (synthetic seeds)

LABELS = ("calendar", "lists", "reminders", "timers", "weather", "time", "people", "memory", "notes", "journal", "music",
          "smart_home", "chat")

# ── the gate (deterministic; a failed gate is a normal outcome) ──
MARGIN_PTS = 2.0          # overall frozen accuracy must rise by at least this many points
INTENT_TOL_PTS = 15.0     # no intent may lose more than this (n=4 -> no case may be lost; n=12 -> at most one)
SAFETY_INTENTS = ("chat", "timers", "reminders", "safety:emergency", "safety:forget")   # must keep 100% of their previous score

# ── harvest ──
MIN_CONF = 0.75
#: the most a signal can vouch for, whatever the source claims (turn_outcomes label sources + the brain-paraphrase policy path)
SIGNAL_CEILING = {
    "owner_confirmed": 1.0,       # the member confirmed the label for their own turn
    "explicit_correction": 0.95,  # "no, I meant the lights"
    "brain_paraphrase": 0.9,      # a generic paraphrase the brain wrote from the intent catalogue (no household words)
    "brain_tool": 0.9,            # the brain executed a tool of that domain after the router abstained / disagreed
    "followup_success": 0.85,     # the follow-up that worked was answered by that fast domain
    "router_belief": 0.4,         # the router's own pick: never a label on its own
}

# ── the stand-in model ──
DIM = 1 << 14
ALPHA = 0.5


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", str(text or "").lower()).split())


def text_key(text: str) -> str:
    """Word-free key of a wording (what a forget list carries instead of the words)."""
    return hashlib.sha256(f"router-learn|{normalise(text)}".encode()).hexdigest()[:16]


def features(text: str) -> Counter:
    words = normalise(text).split()
    grams = list(words) + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for w in words:
        p = f"#{w}#"
        grams += [p[i:i + 4] for i in range(max(1, len(p) - 3))]
    return Counter(zlib.crc32(g.encode()) % DIM for g in grams)


class Model:
    """Multinomial naive Bayes. ``to_bytes`` is canonical (sorted keys, fixed separators) so an artefact is byte-stable."""

    def __init__(self, prior: Optional[dict] = None, counts: Optional[dict] = None, totals: Optional[dict] = None):
        self.prior = prior or {}
        self.counts = counts or {}
        self.totals = totals or {}

    @classmethod
    def fit(cls, rows: Iterable[tuple[str, str]]) -> "Model":
        prior: Counter = Counter()
        counts: dict = defaultdict(Counter)
        for text, label in rows:
            prior[label] += 1
            counts[label].update(features(text))
        return cls(dict(prior), {k: {str(f): n for f, n in v.items()} for k, v in counts.items()},
                   {k: sum(v.values()) for k, v in counts.items()})

    def predict(self, text: str) -> str:
        n = sum(self.prior.values()) or 1
        feats = features(text)
        best, best_s = "chat", -math.inf
        for label in sorted(self.prior):
            c, tot = self.counts.get(label, {}), self.totals.get(label, 0)
            s = math.log(self.prior[label] / n) + sum(k * math.log((c.get(str(f), 0) + ALPHA) / (tot + ALPHA * DIM))
                                                      for f, k in feats.items())
            if s > best_s:
                best, best_s = label, s
        return best

    def to_bytes(self) -> bytes:
        return json.dumps({"kind": "nb-hashed-ngrams", "dim": DIM, "alpha": ALPHA, "prior": self.prior, "counts": self.counts,
                           "totals": self.totals}, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Model":
        d = json.loads(raw)
        return cls(d["prior"], d["counts"], d["totals"])


# ── the frozen set: read in place, never copied into a training path ──

def load_frozen(needle: Path = NEEDLE_CORPUS, safety: Path = SAFETY_CORPUS) -> list[dict]:
    out = []
    for line in needle.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out.append({"id": d["id"], "text": d["text"], "intent": d["domain"], "expected": d["domain"]})
    for line in safety.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out.append({"id": d["id"], "text": d["text"], "intent": d["intent"], "expected": d["expected"]})
    return out


def frozen_norms(frozen: list[dict]) -> frozenset:
    return frozenset(normalise(c["text"]) for c in frozen)


# ── 1. harvest ──

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def load_forget(path: Optional[Path]) -> dict:
    """``{"ids": set, "text_keys": set}`` from a JSONL of ``{"id": ...}`` / ``{"text_key": ...}`` rows (no words)."""
    ids, keys = set(), set()
    for row in read_jsonl(path) if path and Path(path).exists() else []:
        if row.get("id"):
            ids.add(str(row["id"]))
        if row.get("text_key"):
            keys.add(str(row["text_key"]))
    return {"ids": ids, "text_keys": keys}


def harvest(source: Callable[[], Iterable[dict]], forget: dict, frozen: frozenset) -> tuple[list[dict], Counter]:
    """Permitted, labelled examples with provenance, and a count of every drop. A FORGOTTEN row is dropped before anything else
    looks at it (only its id and its word-free key are read: no frozen check, no label check, no dedup bookkeeping), so it can never
    influence what survives."""
    dropped: Counter = Counter()
    kept: list[dict] = []
    for row in source():
        rid = str(row.get("id") or "")
        if rid in forget["ids"] or text_key(row.get("text", "")) in forget["text_keys"]:
            dropped["forgotten"] += 1
            continue
        norm = normalise(row.get("text", ""))
        if not norm:
            dropped["empty"] += 1
            continue
        if norm in frozen:
            dropped["frozen_overlap"] += 1
            continue
        if row.get("label") not in LABELS:
            dropped["bad_label"] += 1
            continue
        signal = row.get("signal")
        if signal not in SIGNAL_CEILING:
            dropped["unknown_signal"] += 1
            continue
        conf = min(float(row.get("confidence") or 0.0), SIGNAL_CEILING[signal])
        if conf < MIN_CONF:
            dropped["low_confidence"] += 1
            continue
        kept.append({"id": rid, "text": row["text"], "norm": norm, "label": row["label"],
                     "provenance": {"signal": signal, "confidence": round(conf, 3), "source_id": rid}})
    by_norm: dict = defaultdict(list)
    for ex in kept:
        by_norm[ex["norm"]].append(ex)
    out: list[dict] = []
    for norm, group in by_norm.items():
        if len({g["label"] for g in group}) > 1:
            dropped["conflict"] += len(group)
            continue
        dropped["duplicate"] += len(group) - 1
        out.append(max(group, key=lambda g: g["provenance"]["confidence"]))
    return out, dropped


def base_examples(frozen: frozenset) -> tuple[list[tuple[str, str]], int]:
    """The incumbent's training set: the setfit-router SEEDS (synthetic), frozen-guarded like everything else."""
    ns: dict = {}
    exec(compile(SEEDS_MODULE.read_text(), str(SEEDS_MODULE), "exec"), ns)
    rows, dropped = [], 0
    for label, texts in ns["SEEDS"].items():
        for t in texts:
            if normalise(t) in frozen:
                dropped += 1
            else:
                rows.append((t, label))
    return rows, dropped


# ── 2. evaluate ──

def evaluate(model: Model, frozen: list[dict]) -> dict:
    per: dict = defaultdict(lambda: [0, 0])
    ok = 0
    chat_fp = 0
    for c in frozen:
        pred = model.predict(c["text"])
        hit = pred == c["expected"]
        ok += hit
        per[c["intent"]][0] += hit
        per[c["intent"]][1] += 1
        chat_fp += c["expected"] == "chat" and pred != "chat"
    n_chat = sum(1 for c in frozen if c["expected"] == "chat")
    return {"n": len(frozen), "correct": ok, "accuracy_pct": round(100.0 * ok / len(frozen), 1),
            "chat_fp_pct": round(100.0 * chat_fp / max(1, n_chat), 1),
            "per_intent": {k: {"correct": v[0], "n": v[1], "pct": round(100.0 * v[0] / v[1], 1)} for k, v in sorted(per.items())}}


# ── 3. the promotion gate ──

def gate(before: dict, after: dict, *, margin: float = MARGIN_PTS, tol: float = INTENT_TOL_PTS,
         safety: Iterable[str] = SAFETY_INTENTS) -> dict:
    checks, reasons = {}, []
    delta = round(after["accuracy_pct"] - before["accuracy_pct"], 1)
    checks["overall_improves"] = delta >= margin
    if not checks["overall_improves"]:
        reasons.append(f"overall moved {delta:+.1f} pts, needs at least +{margin:.1f}")
    worst = []
    for intent, b in before["per_intent"].items():
        a = after["per_intent"].get(intent, {"pct": 0.0, "correct": 0, "n": b["n"]})
        if a["n"] != b["n"]:
            reasons.append(f"{intent}: case count changed ({b['n']} -> {a['n']})")
            worst.append(intent)
        elif b["pct"] - a["pct"] > tol:
            reasons.append(f"{intent}: {b['pct']:.1f}% -> {a['pct']:.1f}%, loses more than {tol:.0f} pts")
            worst.append(intent)
    checks["no_intent_regression"] = not worst
    unsafe = []
    for intent in safety:
        b = before["per_intent"].get(intent)
        a = after["per_intent"].get(intent)
        if b is None:
            continue
        if a is None or a["correct"] < b["correct"]:
            unsafe.append(intent)
            reasons.append(f"safety intent {intent}: {b['correct']}/{b['n']} -> {(a or {}).get('correct', 0)}/{b['n']}")
    checks["safety_held"] = not unsafe
    return {"promote": all(checks.values()), "checks": checks, "delta_pts": delta, "reasons": reasons,
            "constants": {"margin_pts": margin, "intent_tol_pts": tol, "safety_intents": list(safety)}}


# ── 4. the owner-visible proposal (evolution_queue.propose shape; counts only) ──

def proposal(before: dict, after: dict, verdict: dict, dropped: Counter, examples: list[dict], stamp: str) -> dict:
    by_signal = Counter(e["provenance"]["signal"] for e in examples)
    by_label = Counter(e["label"] for e in examples)
    better = [k for k, b in before["per_intent"].items() if after["per_intent"][k]["correct"] > b["correct"]]
    weak = [f"{k} {a['correct']}/{a['n']}" for k, a in after["per_intent"].items()
            if k in verdict["constants"]["safety_intents"] and a["correct"] < a["n"]]
    description = (
        f"The router practised on {len(examples)} new examples and now picks the right kind of request "
        f"{after['accuracy_pct']:.1f}% of the time on the fixed test set (was {before['accuracy_pct']:.1f}%). "
        f"It got better at: {', '.join(better) or 'nothing in particular'}. No request type got worse beyond the limit, and "
        f"timers, reminders, ordinary chat, emergencies and forget requests are at least as right as before. "
        + (f"Still not fully right, unchanged by this round: {', '.join(weak)}. " if weak else "")
        + "If you say yes, the new router replaces the current one; the current one is kept and can be put back with one command.")
    return {
        "source": "router_learning_lab", "change_class": "harness", "risk_tier": 1, "component": "router", "status": "probed",
        "title": f"Router learned from {len(examples)} examples: {before['accuracy_pct']:.1f}% -> {after['accuracy_pct']:.1f}%",
        "description": description,
        "evidence": {
            "signal": {"examples": len(examples), "by_signal": dict(by_signal), "by_label": dict(by_label), "dropped": dict(dropped)},
            "before": {"accuracy_pct": before["accuracy_pct"], "per_intent_pct": {k: v["pct"] for k, v in before["per_intent"].items()}},
            "after": {"accuracy_pct": after["accuracy_pct"], "per_intent_pct": {k: v["pct"] for k, v in after["per_intent"].items()}},
            "gate": {"checks": verdict["checks"], "delta_pts": verdict["delta_pts"], "constants": verdict["constants"]},
            "ref": stamp,
        },
    }


# ── 5. artefacts + rollback ──

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def artefacts(workdir: Path) -> tuple[Path, Path]:
    return workdir / "artefacts" / "current.json", workdir / "artefacts" / "previous.json"


def rollback(workdir: Path) -> bool:
    """Put the previous router back byte for byte. False (and nothing changed) when there is no previous one."""
    cur, prev = artefacts(workdir)
    if not prev.exists():
        return False
    _atomic_write(cur, prev.read_bytes())
    return True


# ── the whole loop ──

def run(workdir: Path, source: Callable[[], Iterable[dict]], forget: dict, *, frozen_cases: Optional[list[dict]] = None,
        stamp: Optional[str] = None) -> dict:
    workdir = Path(workdir)
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    frozen_cases = frozen_cases if frozen_cases is not None else load_frozen()
    fz = frozen_norms(frozen_cases)
    base, base_dropped = base_examples(fz)
    cur, prev = artefacts(workdir)
    if not cur.exists():                               # first run: the incumbent is the base-only model
        _atomic_write(cur, Model.fit(base).to_bytes())
    incumbent = Model.from_bytes(cur.read_bytes())
    examples, dropped = harvest(source, forget, fz)
    leaked = [e for e in examples if e["norm"] in fz]
    assert not leaked, "frozen case reached training"   # unreachable by construction; the tests plant one to prove it
    candidate = Model.fit(base + [(e["text"], e["label"]) for e in examples])
    before, after = evaluate(incumbent, frozen_cases), evaluate(candidate, frozen_cases)
    verdict = gate(before, after)
    journal = {"stamp": stamp, "harvested": len(examples), "dropped": dict(dropped), "base_rows": len(base),
               "base_dropped_frozen": base_dropped, "before": before, "after": after, "verdict": verdict, "promoted": False}
    if verdict["promote"]:
        _atomic_write(prev, cur.read_bytes())          # the previous router is kept before anything replaces it
        _atomic_write(cur, candidate.to_bytes())
        prop = proposal(before, after, verdict, dropped, examples, stamp)
        _atomic_write(workdir / "proposals" / f"{stamp}.json", json.dumps(prop, indent=2, sort_keys=True).encode())
        journal["promoted"] = True
        journal["proposal"] = f"proposals/{stamp}.json"
    _atomic_write(workdir / "runs" / f"{stamp}.json", json.dumps(journal, indent=2, sort_keys=True).encode())
    return journal


def poison(rows: list[dict]) -> list[dict]:
    """Every label moved to the NEXT label in LABELS: a deterministic derangement (no row keeps its true label)."""
    nxt = {a: LABELS[(i + 1) % len(LABELS)] for i, a in enumerate(LABELS)}
    return [{**r, "label": nxt.get(r.get("label"), r.get("label"))} for r in rows]


def table(j: dict) -> str:
    b, a = j["before"], j["after"]
    lines = ["| intent | before | after |", "|---|---|---|"]
    for k, v in b["per_intent"].items():
        w = a["per_intent"][k]
        lines.append(f"| {k} | {v['correct']}/{v['n']} ({v['pct']:.1f}%) | {w['correct']}/{w['n']} ({w['pct']:.1f}%) |")
    lines.append(f"| **overall** | {b['correct']}/{b['n']} ({b['accuracy_pct']:.1f}%) | {a['correct']}/{a['n']} ({a['accuracy_pct']:.1f}%) |")
    v = j["verdict"]
    lines.append("")
    lines.append(f"gate: {'PROMOTE' if v['promote'] else 'KEEP CURRENT'}; checks {v['checks']}; reasons {v['reasons'] or 'none'}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", required=True)
    r.add_argument("--forget")
    r.add_argument("--workdir", required=True)
    r.add_argument("--poison", action="store_true", help="derange every label first (the gate must refuse)")
    d = sub.add_parser("demo")
    d.add_argument("--workdir", required=True)
    d.add_argument("--write-summary", action="store_true", help="rewrite results/summary.json (the pinned totals)")
    for name in ("rollback", "status"):
        sub.add_parser(name).add_argument("--workdir", required=True)
    a = ap.parse_args(argv)
    wd = Path(a.workdir)
    if a.cmd == "rollback":
        ok = rollback(wd)
        print("rolled back to the previous router" if ok else "no previous router: nothing changed")
        return 0 if ok else 1
    if a.cmd == "status":
        cur, prev = artefacts(wd)
        for p in (cur, prev):
            print(p.name, hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "absent")
        return 0
    if a.cmd == "run":
        rows = read_jsonl(Path(a.source))
        rows = poison(rows) if a.poison else rows
        j = run(wd, lambda: rows, load_forget(Path(a.forget) if a.forget else None))
        print(table(j))
        return 0
    # demo: full journals stay in the workdir (<workdir>/<name>/runs/); only the compact totals go beside the lab
    s = demo_summary(wd, read_jsonl(HERE / "data" / "synthetic_source.jsonl"), load_forget(HERE / "data" / "forget.jsonl"), True)
    if a.write_summary:
        SUMMARY.parent.mkdir(exist_ok=True)
        SUMMARY.write_text("{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v, sort_keys=True)}" for k, v in s.items()) + "\n}\n")
    return 0


SUMMARY = HERE / "results" / "summary.json"


def demo_summary(wd: Path, rows: list[dict], forget: dict, verbose: bool = False) -> dict:
    """The clean and poisoned runs, each from the same base incumbent, compact: totals, verdict, failed checks, drops."""
    out = {}
    for name, data in (("clean", rows), ("poisoned", poison(rows))):
        shutil.rmtree(wd / name, ignore_errors=True)
        j = run(wd / name, lambda data=data: data, forget, stamp=f"demo-{name}")
        if verbose:
            print(f"## {name} run\n\n{table(j)}\n\ndropped: {j['dropped']}\n")
        out[name] = {"before": f"{j['before']['correct']}/{j['before']['n']}", "after": f"{j['after']['correct']}/{j['after']['n']}",
                     "promoted": j["promoted"], "failed_checks": sorted(k for k, v in j["verdict"]["checks"].items() if not v),
                     "harvested": j["harvested"], "dropped": dict(sorted(j["dropped"].items()))}
    return out


if __name__ == "__main__":
    sys.exit(main())
