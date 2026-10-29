#!/usr/bin/env python3
"""Propose a STRATIFIED pinned clip set for the voice gate (V-8, deep dive 2, 2026-10-19).

The gate pins 20 clips, and by route 14 of them go to the brain, with no list, music
or reminder clip among them: a broken list add could not move the gate. This tool
labels the corpus by what each clip DOES (the replay's content-free route labels),
then picks a balanced set across categories (lists, timers, reminders, calendar,
music, smart home, weather/time, notes/journal, people/memory, chat), so a
regression in any one domain shows up.

It PROPOSES; it never re-pins. The proposal is a local file (default
~/.zoe/voice-gate/proposed_pins.json, mode 600) of clip ids (hash of name + size)
and categories, and only counts are printed. No audio, name or transcript ever
leaves the box or enters the repo. Adopt it deliberately:

    flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py \\
        --update-baseline --pin-from ~/.zoe/voice-gate/proposed_pins.json
    # then a normal run (the re-pin run judges nothing per clip), and optionally
    python3 scripts/maintenance/voice_clip_checks.py seed   # owner labels to review

Two ways to label the corpus:
  --from-json REPLAY.json   classify an existing replay dump (replay_samples.py --json)
  (default)                 run the replay itself: DRY, no brain (labels come from STT,
                            the router and the fast tiers only), under the harness lock
                            the harness quiet hours and the 01:50-04:55 brain window (a start
                            inside it, or one whose clip-count budget would still be alive at
                            01:50, is refused; --budget-min overrides the estimate). ~1 s of STT
                            per clip: the whole corpus is a late-evening job (--last N narrows it).

Stdlib only (the replay runs in the service interpreter as a subprocess).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_OUT = Path.home() / ".zoe" / "voice-gate" / "proposed_pins.json"
DEFAULT_SAMPLE_DIR = os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"
# Category -> the domain / intent label fragments that put a clip in it (first match
# wins, in this order; anything else that reached the brain is "chat").
STRATA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lists", ("list", "shopping")),
    ("timers", ("timer",)),
    ("reminders", ("remind",)),
    ("calendar", ("calendar", "event", "schedule")),
    ("music", ("music", "media", "play")),
    ("smart_home", ("smart_home", "home", "light", "device")),
    ("weather_time", ("weather", "time", "clock")),
    ("notes_journal", ("note", "journal")),
    ("people_memory", ("people", "person", "memory", "remember", "recall")),
)
CHAT = "chat"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SEC_PER_CLIP = 1.5   # ~1 s of in-process STT per clip (the docstring) plus the router and the fast tiers, rounded up
OVERHEAD_MIN = 5     # the service interpreter, its models, the router


def corpus_clip_count(sample_dir: str, last: int | None = None) -> int:
    """How many clips the dry replay will label: the top-level ``*.wav`` files (the replay's own glob), or ``--last N``."""
    try:
        n = sum(1 for e in os.scandir(sample_dir) if e.is_file() and e.name.lower().endswith(".wav"))
    except OSError:
        n = 0
    return min(n, last) if last else n


def estimate_budget_min(n_clips: int) -> int:
    """Minutes a dry replay over ``n_clips`` clips is expected to take, for the brain-window start guard."""
    return int(-(-max(0, n_clips) * SEC_PER_CLIP // 60)) + OVERHEAD_MIN


def category(labels: dict[str, Any] | None) -> str:
    """The stratum a clip belongs to, from its route labels (fast-path domain first,
    then the router's domain and tool, then the Tier-0 intent). Pure."""
    lab = labels or {}
    for field in ("domain", "intent", "router_domain", "router_tool", "tier0"):
        val = str(lab.get(field) or "").lower()
        if not val or val == CHAT or val.startswith("h:"):
            continue
        for name, frags in STRATA:
            if any(f in val for f in frags):
                return name
    return CHAT


def propose(rows: list[dict[str, Any]], clip_id_of, total: int, seed: str = "v8") -> dict[str, Any]:
    """Pick ``total`` clips spread over the strata. Only OK / DEFERRED rows with route
    labels are candidates (EMPTY, ERROR and refusals say nothing about routing). Each
    stratum gets an equal share, a short stratum gives its unused share to the others,
    and the pick inside a stratum is a deterministic hash order (spread across capture
    days, reproducible). Pure."""
    by_cat: dict[str, list[str]] = {}
    for r in rows:
        if r.get("verdict") not in ("OK", "DEFERRED") or not isinstance(r.get("route"), dict):
            continue
        cid = clip_id_of(r)
        if not cid:
            continue
        by_cat.setdefault(category(r["route"]), []).append(cid)
    for cat, ids in by_cat.items():
        ids.sort(key=lambda c: hashlib.sha256(f"{seed}\x00{c}".encode()).hexdigest())
    chosen: dict[str, list[str]] = {c: [] for c in by_cat}
    remaining = total
    open_cats = [c for c in by_cat if by_cat[c]]
    while remaining > 0 and open_cats:
        share = max(1, remaining // len(open_cats))
        progressed = False
        for cat in list(open_cats):
            take = by_cat[cat][len(chosen[cat]):len(chosen[cat]) + min(share, remaining)]
            chosen[cat].extend(take)
            remaining -= len(take)
            progressed = progressed or bool(take)
            if len(chosen[cat]) >= len(by_cat[cat]):
                open_cats.remove(cat)
            if remaining <= 0:
                break
        if not progressed:
            break
    ids = [c for cat in sorted(chosen) for c in chosen[cat]]
    return {"clip_ids": ids,
            "categories": {c: cat for cat in chosen for c in chosen[cat]},
            "counts": {cat: len(v) for cat, v in sorted(chosen.items())},
            "corpus_counts": {cat: len(v) for cat, v in sorted(by_cat.items())},
            "missing_strata": [name for name, _ in STRATA if not by_cat.get(name)]}


def _run_replay(args, out_json: str) -> int:
    lib = REPO / "scripts" / "lib"
    sys.path.insert(0, str(lib))
    from service_dir import resolve_service_dir  # noqa: E402
    from service_python import resolve_service_python  # noqa: E402

    service_dir = str(resolve_service_dir(args.service_dir))
    python, _ = resolve_service_python(None)
    cmd = [python, "tests/replay_samples.py", "--json", out_json, "--stt", args.stt,
           "--user", args.user]
    if args.last:
        cmd += ["--last", str(args.last)]
    env = {**os.environ, "ZOE_VOICE_SAMPLE_DIR": args.sample_dir, "ZOE_TRAFFIC_CLASS": "probe"}
    env.pop("ZOE_REPLAY_FAULT", None)
    print(f"labelling the corpus: dry replay, no brain, {args.last or 'all'} clip(s) via {service_dir}", flush=True)
    return subprocess.run(cmd, cwd=service_dir, env=env, stdout=subprocess.DEVNULL).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-json", type=Path, default=None, help="classify an existing replay dump")
    ap.add_argument("--total", type=int, default=60, help="clips to propose (default 60)")
    ap.add_argument("--last", type=int, default=None, help="label only the newest N clips (default: all)")
    ap.add_argument("--sample-dir", default=DEFAULT_SAMPLE_DIR)
    ap.add_argument("--service-dir", default=None)
    ap.add_argument("--stt", choices=["inprocess", "remote"], default="inprocess")
    ap.add_argument("--user", default=os.environ.get("ZOE_VOICE_PROBE_USER", "jason"))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", default="v8")
    ap.add_argument("--budget-min", type=int, default=0,
                    help="expected replay length for the brain-window start check (default: estimated from the clip count)")
    args = ap.parse_args(argv)
    vrp_ids = _load("voice_regression_probe")

    def clip_id_of(r: dict[str, Any]) -> str | None:
        return vrp_ids._row_clip_id(r, args.sample_dir)

    tmp = None
    if args.from_json:
        src = args.from_json
    else:
        hq = _load("harness_quiet")
        # the 01:50-04:55 brain window (scripts/AGENTS.md "Brain-window quiet hours"), BEFORE the lock is taken: a start inside
        # it, or one whose budget would still be alive at 01:50, would make the 12B night window / nightly voice gate refuse.
        # Not bypassable (the shared helper ignores ZOE_HARNESS_FORCE); --from-json never reaches this branch.
        budget = args.budget_min or estimate_budget_min(corpus_clip_count(args.sample_dir, args.last))
        window = hq.brain_window_verdict("voice_corpus_strata", budget_min=budget)
        print(window.reason)
        if not window.allowed:
            return 3
        verdict = hq.check("voice_corpus_strata")
        print(verdict.reason)
        if not verdict.allowed:
            return 3
        lock = vrp_ids._acquire_harness_lock()  # noqa: F841 - held for the process lifetime
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        rc = _run_replay(args, tmp)
        if rc != 0:
            print(f"replay exited {rc}", file=sys.stderr)
            os.unlink(tmp)
            return 2
        src = Path(tmp)
    try:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    finally:
        if tmp:
            os.unlink(tmp)
    rows = data.get("rows") or []
    if rows and not any(isinstance(r.get("route"), dict) for r in rows):
        print("the replay dump carries no route labels (replay_samples.py predates T-3)", file=sys.stderr)
        return 2
    prop = propose(rows, clip_id_of, args.total, args.seed)
    prop.update(created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seed=args.seed,
                labelled=len(rows), clip_id_scheme=vrp_ids.CLIP_ID_SCHEME)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(prop, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(args.out, 0o600)
    print(f"labelled {len(rows)} clip(s); corpus by category: {json.dumps(prop['corpus_counts'])}")
    print(f"proposed {len(prop['clip_ids'])} clip(s): {json.dumps(prop['counts'])}")
    if prop["missing_strata"]:
        print(f"no corpus clip found for: {', '.join(prop['missing_strata'])} (record some, or label by hand)")
    print(f"wrote {args.out} (local, mode 600). Adopt: voice_regression_probe.py --update-baseline --pin-from {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
