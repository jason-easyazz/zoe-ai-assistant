#!/usr/bin/env python3
"""bar_reliability.py - repeat-run reliability (pass^k) and collateral-damage scoring for the Samantha bar.

Samantha Initiative lane B E2 (docs/research/samantha-initiative-2026-11-03.md, item 12): one bar run is one draw
from a stochastic system (S4 and S8 flip on the same commit, docs/knowledge/samantha-bar.md "Flake rate"), so a
single green run is not evidence that a change is safe. This module runs (or reads) the SAME bar k times per arm and
answers three questions, with arithmetic only - it never talks to the brain itself:

1. **pass^k / pass@k** per scenario and overall (tau-bench's estimators, unbiased for n >= k runs:
   pass^k = C(c,k)/C(n,k) "all k draws pass", pass@k = 1 - C(n-c,k)/C(n,k) "at least one passes"), beside today's
   single-run score, with every scenario labelled PASS / FAIL / SKIP / FLAKY / ERROR across its runs.
2. **Collateral damage**, candidate vs control: a scenario that passed on control and does not on the candidate is a
   REGRESSION, named, whatever the overall score did. Two kinds block: ``reliability`` (control passed every run, the
   candidate did not) and ``lost`` (control passed at least once, the candidate never). A smaller drop on a scenario
   control was already flaky on is ``weakened`` (reported, not blocking: with k=3 that is inside the noise).
3. **Admissibility** - the precondition for a GEPA-style improver night: a candidate is admissible only when the two
   arms are comparable, both are complete and instrument-clean (no ERROR), its overall pass^k is NOT worse than
   control's, and it has ZERO regressions. Winning overall while breaking one scenario is not admissible.

Inputs are the bar's own results files (``samantha_bar.py --results``; the day-sim's ``asks`` shape is read too), a
directory of them, a saved aggregate, or a bar baseline (``{"scenarios": {id: verdict}}``, read as one run: k=1 is
exactly today's single-run compare).

CLI:
    bar_reliability.py score RUN.json|DIR [...] [--k 3] [--label control] [--out agg.json]
                       [--compare-baseline] [--record-baseline] [--baseline F]
    bar_reliability.py compare --control A --candidate B [--k 3] [--out cmp.json] [--admissible]
    bar_reliability.py run --out-dir D --arm control=CHECKOUT [--arm candidate=CHECKOUT] [--k 3]
                       [--arm-env ARM:ZOE_FLAG=value,ZOE_FLAG2=value ...]
                       [--harness bar|day-sim] [--step-min 35] [--dry-run] [-- extra harness args]

Exit codes: ``score`` 0 ok / 1 regression vs the reliability baseline / 2 incomplete, errored or refused;
``compare --admissible`` and ``run`` with two arms: 0 admissible / 1 measured and NOT admissible / 2 not measurable
(incomplete arm, an ERROR cell, arms not comparable, a refused gate).

``--arm-env`` is a per-arm flag override for an A/B whose candidate only turns a FLAG on (the scratch stack otherwise copies
the live flags into every arm): it is layered ON TOP of the copied flags inside THAT arm's scratch stack only
(``harness_scratch.validate_arm_env``: ``ZOE_*`` names only; never ``ZOE_HARNESS*``, a secret, a location or an isolation pin;
names only in logs), recorded in each run's JSON (``scratch.arm_env``) and in the aggregate (``arm_env``), and the two arms
may then differ ONLY in the declared override + checkout: a run that recorded a different override than the one declared makes the
arms not comparable (exit 2).

``run`` is a NIGHT job (scripts/AGENTS.md): it refuses in harness quiet hours, asks the measured night pause
(``harness_quiet.night_pause_verdict``, ``night_busy.sh voice`` via ``ZOE_NIGHT_BUSY``, the fixed 01:50-04:55 window as
the fallback) before EVERY step, waits out a running voice lander, queues for the shared harness lock no longer than
``harness_quiet.lock_wait_s`` allows and kills a step (TERM, then KILL) at ``harness_quiet.deadline_s``. Each step is
the unchanged harness with its own gates (``ZOE_PERF=1``, scratch stack, teardown proof). Arms run interleaved, the
order alternating each round, so drift over the night lands on both arms. A finished run file is kept, so a run cut
short by the window resumes the next night. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = "zoe.bar_reliability/1"
COMPARE_SCHEMA = "zoe.bar_reliability.compare/1"
DEFAULT_K = 3
VALID_STATUSES = ("ok", "partial", "regression")   # a run that produced a verdict per selected scenario
LOCK = os.environ.get("ZOE_HARNESS_LOCK", "/tmp/zoe-voice-harness.lock")
CACHE = Path.home() / ".cache" / "zoe"
DEFAULT_BASELINE = CACHE / "samantha_bar_reliability_baseline.json"
REPO = Path(__file__).resolve().parents[2]
HARNESS_SCRIPTS = {"bar": "scripts/perf/samantha_bar.py", "day-sim": "scripts/perf/samantha_day_sim.py"}
LANDER_PATTERN = r"(^|[ /])land_voice_pr\.sh( |$)"   # via bash or exec'd directly
EXIT_OK, EXIT_RED, EXIT_UNMEASURED = 0, 1, 2


# ─────────────────────────────────────────────────────────────────────────────
# Estimators (pure)
# ─────────────────────────────────────────────────────────────────────────────

def pass_hat_k(n: int, c: int, k: int) -> float | None:
    """P(all k draws pass) from n runs with c passes: C(c,k)/C(n,k). None when n < k (not estimable)."""
    if k < 1 or n < k or not 0 <= c <= n:
        return None
    return math.comb(c, k) / math.comb(n, k)


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """P(at least one of k draws passes): 1 - C(n-c,k)/C(n,k). None when n < k."""
    if k < 1 or n < k or not 0 <= c <= n:
        return None
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


# ─────────────────────────────────────────────────────────────────────────────
# Reading runs
# ─────────────────────────────────────────────────────────────────────────────

def run_kind(payload: dict) -> str:
    return "day-sim" if isinstance(payload.get("asks"), list) else "bar"


def run_verdicts(payload: dict) -> dict[str, str]:
    """{scenario id: verdict} from a bar results payload (``scenarios`` list), a day-sim one (``asks``) or a bar
    baseline (``scenarios`` mapping)."""
    scen = payload.get("asks") if isinstance(payload.get("asks"), list) else payload.get("scenarios")
    if isinstance(scen, dict):
        return {str(k): str(v) for k, v in scen.items()}
    return {str(r["id"]): str(r["verdict"]) for r in (scen or []) if isinstance(r, dict) and "id" in r}


def rubric_digest(payload: dict) -> str | None:
    return payload.get("judge_prompt_sha256") or payload.get("criteria_sha256")


def code_identity(payload: dict) -> str | None:
    """Which code a run measured: the served tree (+ the tracked diff when dirty). None when unrecorded."""
    ev = payload.get("evidence") or {}
    rev = payload.get("revision") or {}
    tree = ev.get("tree") or rev.get("tree")
    if not tree:
        return None
    diff = ev.get("diff_sha256") or rev.get("diff_sha256")
    return f"{tree}+{diff}" if diff else tree


def run_coverage(payload: dict) -> str:
    """``full`` (the whole scenario set was selected), ``partial`` (``--only`` / ``--axis``) or ``unknown`` (a payload
    that does not say - fail closed: never counted as full). A bar baseline is full: only a full run records one."""
    if "status" not in payload:
        return "full"
    if payload.get("partial") or payload.get("status") == "partial":
        return "partial"
    if payload.get("partial") is False or payload.get("status") == "ok":
        return "full"
    return "unknown"     # e.g. a ``regression`` status from a writer that does not record ``partial``


def run_backdate(payload: dict) -> str:
    """``yes`` (the day-1 -> day-2 backdate ran), ``no`` (a ``--no-backdate`` DIAGNOSTIC run: the multi-day scenarios
    never saw a day boundary) or ``unknown`` (a bar payload that does not record it - fail closed). Baselines (already
    proven when recorded) and day-sim payloads have no such flag and count as ``yes``."""
    if "status" not in payload or run_kind(payload) == "day-sim":
        return "yes"
    flag = payload.get("backdate")
    return "yes" if flag is True else "no" if flag is False else "unknown"


def run_arm_env(payload: dict) -> dict:
    """The per-arm flag overrides a run's scratch stack recorded ({} = none, or a writer that predates them)."""
    env = (payload.get("scratch") or {}).get("arm_env")
    if env is None:
        return {}
    if isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        return dict(env)        # AS RECORDED: never coerced (a recorded 1 is not the declared "1")
    return {"<malformed arm_env>": type(env).__name__}   # matches no declaration: the arm is not comparable


def _hs():
    mod = sys.modules.get("harness_scratch")
    if mod is None:   # by FILE PATH, like harness_quiet (stdlib only; the one validator of what an arm may override)
        spec = importlib.util.spec_from_file_location("harness_scratch", Path(__file__).resolve().parent / "harness_scratch.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["harness_scratch"] = mod
        spec.loader.exec_module(mod)
    return mod


def parse_arm_env(specs: Iterable[str], arms: Iterable[str]) -> dict[str, dict[str, str]]:
    """``ARM:NAME=value,NAME=value`` (repeatable) -> {arm: {NAME: value}}, validated by ``harness_scratch.validate_arm_env``.
    ValueError (names only, never a value) on an unknown arm, a malformed pair, a duplicate, or a refused name/value."""
    hs = _hs()
    known = list(arms)
    out: dict[str, dict[str, str]] = {}
    for spec in specs:
        arm, sep, rest = spec.partition(":")
        if not sep or arm not in known:
            raise ValueError(f"--arm-env must be ARM:NAME=value,... with ARM one of {known}")
        pairs: dict[str, str] = out.setdefault(arm, {})
        for item in filter(None, rest.split(",")):
            name, eq, value = item.partition("=")
            if not eq or not name:
                raise ValueError(f"--arm-env {arm}: {name or item[:20]!r} is not NAME=value")
            if name in pairs:
                raise ValueError(f"--arm-env {arm}: {name} given twice")
            pairs[name] = value
        try:
            out[arm] = hs.validate_arm_env(pairs)
        except hs.Refused as exc:
            raise ValueError(str(exc)) from None
    return out


def show_value(value: str) -> str:
    """A flag value for the dry-run plan: only a short lowercase word or a plain number (on / shadow / 1 / 0.5); anything else (a
    mixed-case, underscored, hex or long token-shaped string) is shown as its length, never its content."""
    return value if re.fullmatch(r"[a-z]{1,12}|[0-9]{1,6}(\.[0-9]{1,4})?", value) else f"<{len(value)} chars>"


def show_arm_env(env: dict[str, str]) -> str:
    """Names only for a log line (a value is shown nowhere but the run JSON)."""
    return ",".join(sorted(env)) or "none"


def run_problem(payload: dict) -> str | None:
    """Why a results payload cannot count as a run (None = it counts)."""
    if "status" not in payload:   # a baseline: a recorded verdict map, already proven when it was recorded
        return None if run_verdicts(payload) else "no scenario verdicts"
    if payload.get("status") not in VALID_STATUSES:
        return f"status {payload.get('status')!r}" + (f" ({payload.get('reason')})" if payload.get("reason") else "")
    if not (payload.get("teardown") or {}).get("proven"):
        return "teardown not proven"
    if not run_verdicts(payload):
        return "no scenario verdicts"
    return None


def load_json(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a JSON object")
    return data


def expand_inputs(paths: Iterable[Path]) -> list[Path]:
    """Files as given; a directory contributes its ``run-*.json`` files in order."""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        out.extend(sorted(p.glob("run-*.json")) if p.is_dir() else [p])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Aggregate one arm
# ─────────────────────────────────────────────────────────────────────────────

def stable_verdict(verdicts: list[str]) -> str:
    """One label for a scenario across runs: PASS (every run) / FLAKY (some) / ERROR (any instrument error and no
    pass) / the uniform non-pass verdict (FAIL or SKIP) / FAIL (a mix of non-passes). k=1: the run's own verdict."""
    if not verdicts:
        return "ERROR"
    passes = verdicts.count("PASS")
    if passes == len(verdicts):
        return "PASS"
    if passes:
        return "FLAKY"
    if "ERROR" in verdicts:
        return "ERROR"
    return verdicts[0] if len(set(verdicts)) == 1 else "FAIL"


def aggregate(payloads: list[dict], *, k: int = DEFAULT_K, label: str = "run",
              sources: list[str] | None = None, rejected: list[dict] | None = None) -> dict[str, Any]:
    """Aggregate n results payloads of ONE arm. Invalid runs (refused, errored, teardown unproven) are listed and
    not counted. Status: ``ok`` (n >= k valid, consistent, FULL-coverage runs) / ``incomplete`` (fewer than k) /
    ``partial`` (k runs of a ``--only`` / ``--axis`` selection, or of unknown coverage: never a baseline, never
    admissible - they did not exercise the rest of the bar) / ``error`` (the runs disagree on scenario set, coverage,
    samples, rubric or code - they are not repeats of one thing). ``rejected`` adds runs the caller already refused
    (the night runner: a cached run measured for a different arm identity)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    sources = sources or [f"#{i + 1}" for i in range(len(payloads))]
    valid: list[tuple[str, dict]] = []
    rejected = list(rejected or [])
    for src, p in zip(sources, payloads):
        why = run_problem(p)
        if why:
            rejected.append({"source": src, "reason": why})
        else:
            valid.append((src, p))
    problems: list[str] = []

    def _one(name: str, values: list) -> Any:
        distinct = sorted({json.dumps(v, sort_keys=True) for v in values})
        if len(distinct) > 1:
            problems.append(f"runs disagree on {name}: {', '.join(distinct)}")
        return values[0] if values else None

    maps = [run_verdicts(p) for _, p in valid]
    ids = _one("scenario set", [sorted(m) for m in maps]) or []
    samples = _one("samples", [p.get("samples", 1) for _, p in valid])
    rubric = _one("judge/criteria digest", [rubric_digest(p) for _, p in valid])
    kind = _one("harness", [run_kind(p) for _, p in valid])
    arm_env = _one("arm env override", [run_arm_env(p) for _, p in valid]) or {}
    coverage = _one("coverage (full vs --only/--axis)", [run_coverage(p) for _, p in valid])
    backdate = _one("backdate (--no-backdate is diagnostic)", [run_backdate(p) for _, p in valid])
    codes = [code_identity(p) for _, p in valid]
    code = _one("code (tree + diff)", [c for c in codes if c is not None]) if any(codes) else None
    bound = [bool((p.get("evidence") or {}).get("bound")) for _, p in valid if "status" in p]

    n = len(valid)
    scenarios: dict[str, dict] = {}
    for sid in ids:
        vs = [m.get(sid, "ERROR") for m in maps]
        c = vs.count("PASS")
        scenarios[sid] = {"runs": n, "passes": c, "verdicts": vs, "verdict": stable_verdict(vs),
                          "flaky": 0 < c < n, "errors": vs.count("ERROR"),
                          "pass_hat_k": pass_hat_k(n, c, k), "pass_at_k": pass_at_k(n, c, k),
                          "pass_rate": round(c / n, 4) if n else None}
    first = maps[0] if maps else {}
    status = "error" if (problems or not valid) else (
        "incomplete" if n < k else "ok" if coverage == "full" else "partial")
    if not valid:
        problems.append("no valid run")
    return {
        "schema": SCHEMA, "label": label, "harness": kind, "k": k, "n_runs": n, "status": status,
        "coverage": coverage, "backdate": backdate, "problems": problems, "rejected_runs": rejected, "sources": [s for s, _ in valid],
        "samples": samples, "rubric_sha256": rubric, "code": code, "arm_env": arm_env,
        "all_runs_bound": bool(bound) and all(bound) and len(bound) == n,
        "summary": {
            "scenarios": len(ids),
            "pass_hat_k": _mean(s["pass_hat_k"] for s in scenarios.values()),
            "pass_at_k": _mean(s["pass_at_k"] for s in scenarios.values()),
            "pass_rate": _mean(s["pass_rate"] for s in scenarios.values()),     # = pass^1
            "single_run_passed": sum(1 for v in first.values() if v == "PASS"),  # today's score: run 1 alone
            "all_k_pass": sorted(s for s, r in scenarios.items() if r["verdict"] == "PASS"),
            "flaky": sorted(s for s, r in scenarios.items() if r["flaky"]),
            "errored": sorted(s for s, r in scenarios.items() if r["errors"]),
        },
        "single_run_verdicts": dict(first),
        "verdicts": {s: r["verdict"] for s, r in scenarios.items()},
        "scenarios": scenarios,
    }


def as_aggregate(path: Path, *, k: int, label: str) -> dict[str, Any]:
    """A saved aggregate as-is; a directory or a single results/baseline file aggregated with ``k``."""
    p = Path(path)
    if p.is_file():
        data = load_json(p)
        if data.get("schema") == SCHEMA:
            return data
        return aggregate([data], k=k, label=label, sources=[str(p)])
    files = expand_inputs([p]) if p.is_dir() else []      # an arm that never ran: no valid run (status error)
    return aggregate([load_json(f) for f in files], k=k, label=label, sources=[str(f) for f in files])


# ─────────────────────────────────────────────────────────────────────────────
# Candidate vs control
# ─────────────────────────────────────────────────────────────────────────────

def compare(control: dict[str, Any], candidate: dict[str, Any],
            declared: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Collateral damage and the admissibility verdict (module docstring). Pure. ``declared`` = the per-arm overrides the run
    was asked to apply ({arm label: {NAME: value}}): an arm whose runs recorded anything else is not comparable."""
    unmeasured: list[str] = []
    if declared is not None:
        for arm in (control, candidate):
            want, got = declared.get(arm.get("label"), {}), arm.get("arm_env") or {}
            if want != got:
                unmeasured.append(f"{arm.get('label')}: recorded arm env override {show_arm_env(got)} is not the declared "
                                  f"{show_arm_env(want)} - the arms may differ only in the declared override + checkout")
    for arm in (control, candidate):
        if arm.get("coverage") != "full":
            unmeasured.append(f"{arm.get('label')}: coverage {arm.get('coverage')!r} - only a full bar is a complete arm")
        if arm.get("backdate") != "yes":   # samantha_bar refuses --no-backdate in both baseline modes; so do we
            unmeasured.append(f"{arm.get('label')}: backdate {arm.get('backdate')!r} - a --no-backdate (diagnostic) or "
                              "backdate-unrecorded run skipped the day boundary, so it is not a complete arm")
        if arm.get("status") != "ok":
            unmeasured.append(f"{arm.get('label')}: status {arm.get('status')} "
                              f"({arm.get('n_runs')} valid run(s), k={arm.get('k')}; {'; '.join(arm.get('problems') or []) or 'no problem named'})")
        if (arm.get("summary") or {}).get("errored"):
            unmeasured.append(f"{arm.get('label')}: instrument ERROR in {', '.join(arm['summary']['errored'])} - "
                              "an errored cell is the instrument, not a result")
    cs, ds = control.get("scenarios") or {}, candidate.get("scenarios") or {}
    if set(cs) != set(ds):
        unmeasured.append(f"scenario sets differ (control only: {sorted(set(cs) - set(ds))}, "
                          f"candidate only: {sorted(set(ds) - set(cs))})")
    for key, what in (("k", "k"), ("samples", "samples per judged ask"), ("rubric_sha256", "judge/criteria rubric"),
                      ("harness", "harness")):
        a, b = control.get(key), candidate.get(key)
        if a != b:   # unknown on one arm vs known on the other is NOT "the same" (fail closed)
            unmeasured.append(f"{what} differs: control {a!r} vs candidate {b!r}")

    regressions, weakened, improvements = [], [], []
    for sid in sorted(set(cs) & set(ds)):
        c, d = cs[sid], ds[sid]
        cc, cn, dc, dn = c["passes"], c["runs"], d["passes"], d["runs"]
        row = {"id": sid, "control": f"{cc}/{cn}", "candidate": f"{dc}/{dn}",
               "control_verdict": c["verdict"], "candidate_verdict": d["verdict"]}
        if cn and cc == cn and dc < dn:
            regressions.append({**row, "kind": "reliability"})          # always passed on control; not on candidate
        elif cc > 0 and dc == 0:
            regressions.append({**row, "kind": "lost"})                 # passed sometimes on control; never now
        elif cn and dn and dc / dn < cc / cn:
            weakened.append(row)
        elif cn and dn and dc / dn > cc / cn:
            improvements.append(row)

    cph = (control.get("summary") or {}).get("pass_hat_k")
    dph = (candidate.get("summary") or {}).get("pass_hat_k")
    delta = None if cph is None or dph is None else round(dph - cph, 4)
    net = None if delta is None else ("better" if delta > 0 else "worse" if delta < 0 else "equal")
    reasons: list[str] = list(unmeasured)
    if net == "worse":
        reasons.append(f"pass^k worse: {dph} < control {cph}")
    if regressions:
        reasons.append(f"collateral damage: {len(regressions)} scenario(s) that passed on control regressed "
                       f"({', '.join(r['id'] for r in regressions)})")
    measurable = not unmeasured
    admissible = measurable and net in ("better", "equal") and not regressions
    return {
        "schema": COMPARE_SCHEMA,
        "control": {"label": control.get("label"), "code": control.get("code"), "n_runs": control.get("n_runs"),
                    "pass_hat_k": cph, "pass_at_k": (control.get("summary") or {}).get("pass_at_k")},
        "candidate": {"label": candidate.get("label"), "code": candidate.get("code"), "n_runs": candidate.get("n_runs"),
                      "pass_hat_k": dph, "pass_at_k": (candidate.get("summary") or {}).get("pass_at_k")},
        "k": candidate.get("k"), "pass_hat_k_delta": delta, "net": net,
        "regressions": regressions, "weakened": weakened, "improvements": improvements,
        "net_no_collateral": bool(measurable and net == "better" and not regressions),
        "measurable": measurable, "admissible": admissible, "reasons": reasons,
        "same_code": bool(control.get("code")) and control.get("code") == candidate.get("code"),
        "arm_env": {"control": control.get("arm_env") or {}, "candidate": candidate.get("arm_env") or {}},
        "arm_env_differs": (control.get("arm_env") or {}) != (candidate.get("arm_env") or {}),
    }


def admissible(control: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, list[str]]:
    """The GEPA improver precondition: (admissible, reasons it is not). pass^k not worse AND zero collateral
    regressions, on comparable, complete, instrument-clean arms."""
    cmp = compare(control, candidate)
    return cmp["admissible"], cmp["reasons"]


def verdict_exit(cmp: dict[str, Any]) -> int:
    return EXIT_UNMEASURED if not cmp["measurable"] else (EXIT_OK if cmp["admissible"] else EXIT_RED)


def summary_text(agg: dict[str, Any], cmp: dict[str, Any] | None = None) -> str:
    s = agg["summary"]
    lines = [f"{agg['label']}: {agg['harness']} x{agg['n_runs']} (k={agg['k']}, status {agg['status']}) "
             f"pass^k={s['pass_hat_k']} pass@k={s['pass_at_k']} pass^1={s['pass_rate']} "
             f"single-run {s['single_run_passed']}/{s['scenarios']}"]
    for sid, r in agg["scenarios"].items():
        lines.append(f"  {sid:<4} {r['verdict']:<6} {r['passes']}/{r['runs']}  {' '.join(r['verdicts'])}")
    if s["flaky"]:
        lines.append(f"  FLAKY: {', '.join(s['flaky'])}")
    for p in agg["problems"]:
        lines.append(f"  problem: {p}")
    for r in agg["rejected_runs"]:
        lines.append(f"  rejected {r['source']}: {r['reason']}")
    if cmp is not None:
        lines.append(f"candidate vs control: net {cmp['net']} (pass^k {cmp['control']['pass_hat_k']} -> "
                     f"{cmp['candidate']['pass_hat_k']}), regressions {len(cmp['regressions'])}, "
                     f"weakened {len(cmp['weakened'])}, improvements {len(cmp['improvements'])}")
        for r in cmp["regressions"]:
            lines.append(f"  REGRESSION {r['id']} ({r['kind']}): control {r['control']} -> candidate {r['candidate']}")
        for r in cmp["weakened"]:
            lines.append(f"  weakened   {r['id']}: control {r['control']} -> candidate {r['candidate']}")
        lines.append(f"net with no collateral damage: {'YES' if cmp['net_no_collateral'] else 'no'}; "
                     f"admissible: {'YES' if cmp['admissible'] else 'NO'}")
        for why in cmp["reasons"]:
            lines.append(f"  - {why}")
    return "\n".join(lines)


def baseline_record_problem(agg: dict[str, Any]) -> str | None:
    """A reliability baseline is complete, instrument-clean, full (not partial) and commit-bound (T-4's rule)."""
    if agg.get("coverage") != "full":
        return f"coverage {agg.get('coverage')!r}: a partial (--only/--axis) or unknown-coverage run never records the bar"
    if agg.get("backdate") != "yes":
        return (f"backdate {agg.get('backdate')!r}: a --no-backdate (diagnostic) or backdate-unrecorded run never records "
                "the bar (samantha_bar refuses --no-backdate with --record-baseline)")
    if agg["status"] != "ok":
        return f"status {agg['status']}"
    if agg["summary"]["errored"]:
        return f"ERROR cells: {', '.join(agg['summary']['errored'])}"
    if agg.get("arm_env"):
        return f"arm env override ({show_arm_env(agg['arm_env'])}): a flag-overridden arm is a candidate, never the bar"
    if not agg.get("all_runs_bound"):
        return "not every run is commit-bound (evidence.bound) - run from a clean checkout of the deployed commit"
    return None


def write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# ─────────────────────────────────────────────────────────────────────────────
# The night runner
# ─────────────────────────────────────────────────────────────────────────────

def _harness_quiet():
    mod = sys.modules.get("harness_quiet")
    if mod is None:   # by FILE PATH, like samantha_bar.harness_quiet_refusal (scripts/maintenance shadows service modules)
        spec = importlib.util.spec_from_file_location("harness_quiet", REPO / "scripts" / "maintenance" / "harness_quiet.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["harness_quiet"] = mod
        spec.loader.exec_module(mod)
    return mod


def run_schedule(arms: list[str], k: int) -> list[tuple[int, str]]:
    """(round, arm) in run order: interleaved, the order alternating each round (ABBA...), so slow drift over the
    night (thermal, a growing cache, the hour) lands on both arms."""
    out: list[tuple[int, str]] = []
    for i in range(1, k + 1):
        out.extend((i, a) for a in (arms if i % 2 else list(reversed(arms))))
    return out


def run_file(out_dir: Path, arm: str, i: int) -> Path:
    return Path(out_dir) / arm / f"run-{i}.json"


def identity_file(out_dir: Path, arm: str, i: int) -> Path:
    """The arm identity a cached run was measured for (not ``run-*.json``: never read as a run)."""
    return Path(out_dir) / arm / f"identity-{i}.json"


def _git(root: Path, *args: str, runner: Callable[..., Any] = subprocess.run) -> str | None:
    try:
        r = runner(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def arm_identity(checkout: str, harness: str, extra: list[str], arm_env: dict[str, str] | None = None, *,
                 runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """WHAT an arm measures: the checkout's commit + tree (+ the tracked diff when dirty) and the exact harness and
    args and per-arm flag overrides. A cached run is reused only for an identical identity. When git cannot vouch for the checkout the identity
    carries a per-invocation nonce, so no earlier run can ever match it (fail closed: re-run, never reuse)."""
    root = Path(checkout).resolve()
    tree = _git(root, "rev-parse", "HEAD^{tree}", runner=runner)
    commit = _git(root, "rev-parse", "HEAD", runner=runner)
    tracked = _git(root, "status", "--porcelain", "--untracked-files=no", runner=runner)
    diff_sha = None
    if tracked:
        diff = _git(root, "diff", "HEAD", "--binary", runner=runner)
        diff_sha = None if diff is None else hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()[:16]
    verified = bool(tree and commit) and tracked is not None and (not tracked or diff_sha is not None)
    ident: dict[str, Any] = {"checkout": str(root), "commit": commit, "tree": tree, "diff_sha256": diff_sha,
                             "harness": harness, "script": HARNESS_SCRIPTS[harness], "args": list(extra),
                             "arm_env": dict(arm_env or {}), "verified": verified}
    if not verified:
        ident["nonce"] = os.urandom(8).hex()
    return ident


def identity_problem(out_dir: Path, arm: str, i: int, identity: dict[str, Any]) -> str | None:
    """Why the cached run slot (arm, i) does not belong to ``identity`` (None = it does)."""
    try:
        recorded = load_json(identity_file(out_dir, arm, i))
    except (OSError, ValueError):
        return "no arm identity recorded for this run"
    if recorded != identity:
        changed = sorted(k for k in set(recorded) | set(identity) if recorded.get(k) != identity.get(k))
        return f"measured for a different arm identity ({', '.join(changed)} changed)"
    return None


def done(path: Path, identity_why: str | None = None) -> bool:
    """A finished run that may be reused: valid verdicts, proven teardown AND measured for the requested arm
    (``identity_why`` = ``identity_problem`` for the slot)."""
    if identity_why is not None:
        return False
    try:
        return run_problem(load_json(path)) is None
    except (OSError, ValueError):
        return False


def lander_running(runner: Callable[..., Any] = subprocess.run) -> bool:
    try:
        return runner(["pgrep", "-f", LANDER_PATTERN], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True   # unknown = assume a lander: wait / refuse, never collide


def step_gate(name: str, step_min: int, *, hq=None, runner: Callable[..., Any] = subprocess.run) -> str | None:
    """Why the next step may NOT start (None = it may). Quiet hours + panel, the measured night pause, a lander."""
    hq = hq or _harness_quiet()
    v = hq.check(name, force=False, landing=False)   # an inherited ZOE_HARNESS_FORCE / _LANDING never opens the hours here
    if not v.allowed:
        return v.reason
    v = hq.night_pause_verdict(name, budget_min=step_min, kind="voice")
    if not v.allowed:
        return v.reason
    if lander_running(runner):
        return "a voice landing (land_voice_pr.sh) is running - it owns the brain slot"
    return None


BYPASS_ENV = ("ZOE_HARNESS_FORCE", "ZOE_HARNESS_LANDING")


def child_env(base: dict, arm_env: dict[str, str] | None = None) -> dict:
    """The env each harness step runs with: ZOE_PERF=1, and NEVER the quiet-hours bypasses (the lander-only exemption and
    the owner's force) - inherited from an operator shell they would let the child bar skip its own gate. The per-arm flag
    overrides ride in ``harness_scratch.ARM_ENV_VAR`` for THAT step only; an inherited one is dropped (an operator shell must
    not leak one arm's flags into another)."""
    var = _hs().ARM_ENV_VAR
    env = {**{k: v for k, v in base.items() if k not in BYPASS_ENV and k != var}, "ZOE_PERF": "1"}
    if arm_env:
        env[var] = json.dumps(arm_env, sort_keys=True)
    return env


def _lock(wait_s: int, sleep: Callable[[float], None] = time.sleep) -> int | None:
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o666)
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - t0 >= wait_s:
                os.close(fd)
                return None
            sleep(10)


def _run_step(cmd: list[str], deadline_s: int, env: dict, log) -> int:
    """Run one harness step, TERM at the deadline, KILL 60 s later (the bar needs >= 30 s to tear down)."""
    proc = subprocess.Popen(cmd, env=env)
    try:
        return proc.wait(timeout=deadline_s)
    except subprocess.TimeoutExpired:
        log(f"step deadline ({deadline_s}s) reached - SIGTERM, teardown has 60 s")
        proc.send_signal(signal.SIGTERM)
        try:
            return proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()


def run_arms(args, log=print, *, gate=step_gate, hq=None) -> int:
    arms = dict(a.split("=", 1) for a in args.arm)
    arm_envs = parse_arm_env(getattr(args, "arm_env", None) or [], arms)
    script = HARNESS_SCRIPTS[args.harness]
    plan = run_schedule(list(arms), args.k)
    out_dir = Path(args.out_dir)
    extra = [a for a in args.extra if a != "--"]
    if not args.dry_run and os.environ.get("ZOE_PERF") != "1":
        log("bar_reliability run: skipped - live runs require ZOE_PERF=1 (see --dry-run)")
        return EXIT_OK
    idents = {a: arm_identity(c, args.harness, extra, arm_envs.get(a)) for a, c in arms.items()}
    todo = [(i, arm) for i, arm in plan
            if not done(run_file(out_dir, arm, i), identity_problem(out_dir, arm, i, idents[arm]))]
    stale = [(i, arm) for i, arm in todo if run_file(out_dir, arm, i).exists()]

    def cmd_for(i: int, arm: str) -> list[str]:
        f = run_file(out_dir, arm, i)
        c = [sys.executable, str(Path(arms[arm]) / script), "--results", str(f),
             "--trend", str(f.parent / "trend.jsonl"), *extra]
        return c

    if args.dry_run:
        log(f"plan: {len(plan)} step(s), {len(todo)} still to run, ~{args.step_min} min each, harness {args.harness}")
        for i, arm in plan:
            mark = "redo" if (i, arm) in stale else "todo" if (i, arm) in todo else "done"
            ov = f"  env[{show_arm_env(arm_envs[arm])}]" if arm_envs.get(arm) else ""
            log(f"  [{mark}] round {i} {arm:<10} {' '.join(cmd_for(i, arm))}{ov}")
        for arm, ov in arm_envs.items():
            log(f"  arm {arm} overrides (on top of the copied live flags, this arm only): "
                + ", ".join(f"{k}={show_value(v)}" for k, v in ov.items()))
        return EXIT_OK
    for arm, checkout in arms.items():
        if not (Path(checkout) / script).is_file():
            log(f"REFUSED: arm {arm}: {Path(checkout) / script} does not exist")
            return EXIT_UNMEASURED
    for i, arm in stale:
        log(f"round {i} {arm}: cached run refused "
            f"({identity_problem(out_dir, arm, i, idents[arm]) or 'not a valid run'}) - it will be re-run")
    hq = hq or _harness_quiet()
    for arm, ov in arm_envs.items():
        log(f"arm {arm}: flag override(s) {show_arm_env(ov)}")
    for i, arm in todo:
        why = gate("bar_reliability", args.step_min, hq=hq)
        if why:
            log(f"stopping before round {i} {arm}: {why} (finished runs are kept; re-run the same command to resume)")
            break
        fd = _lock(hq.lock_wait_s(args.step_min, cap_s=1800))
        if fd is None:
            log(f"stopping before round {i} {arm}: the harness lock {LOCK} stayed held")
            break
        try:
            why = gate("bar_reliability", args.step_min, hq=hq)      # re-checked INSIDE the lock, after any wait
            deadline = hq.deadline_s()
            if why or deadline <= 0:
                log(f"stopping before round {i} {arm}: {why or 'no time left before the brain window'}")
                break
            run_file(out_dir, arm, i).parent.mkdir(parents=True, exist_ok=True)
            # a stale run (another identity, or invalid) must not survive a step that writes nothing
            run_file(out_dir, arm, i).unlink(missing_ok=True)
            identity_file(out_dir, arm, i).unlink(missing_ok=True)
            log(f"== {dt.datetime.now().isoformat(timespec='seconds')} round {i} {arm}")
            rc = _run_step(cmd_for(i, arm), deadline, child_env(os.environ, arm_envs.get(arm)), log)
            moved = _arm_moved(arms[arm], args.harness, extra, idents[arm], arm_envs.get(arm))
            if moved:      # the checkout changed under the step: what it measured is no arm's evidence
                run_file(out_dir, arm, i).unlink(missing_ok=True)
                log(f"stopping: arm {arm} checkout changed during round {i} ({moved}) - that run is discarded")
                break
            write_json(identity_file(out_dir, arm, i), idents[arm])   # whatever it wrote was measured for THIS arm
            log(f"   round {i} {arm} exit {rc}")
        finally:
            os.close(fd)
        if rc not in (0, 1):   # 0 ran / 1 ran with a regression vs ITS baseline: both are runs. 2/3 = refused
            log(f"stopping: round {i} {arm} was refused or errored (exit {rc}) - see {run_file(out_dir, arm, i)}")
            break
    return finish(out_dir, list(arms), args.k, log, identities=idents)


def _arm_moved(checkout: str, harness: str, extra: list[str], identity: dict[str, Any],
               arm_env: dict[str, str] | None = None) -> str | None:
    """Which identity fields changed since the night began (None = none; the unverified nonce is not a field)."""
    now = arm_identity(checkout, harness, extra, arm_env)
    changed = sorted(k for k in set(now) | set(identity) if k != "nonce" and now.get(k) != identity.get(k))
    return ", ".join(changed) or None


def arm_aggregate(out_dir: Path, arm: str, k: int, identity: dict[str, Any]) -> dict[str, Any]:
    """Aggregate exactly the planned slots run-1..k of ``arm``: a run measured for another arm identity (a changed
    checkout, other harness args) is REJECTED, and leftovers beyond k are not read."""
    payloads, sources, rejected = [], [], []
    for i in range(1, k + 1):
        f = run_file(out_dir, arm, i)
        if not f.exists():
            continue
        why = identity_problem(out_dir, arm, i, identity)
        if why:
            rejected.append({"source": str(f), "reason": why})
            continue
        try:
            payloads.append(load_json(f))
        except (OSError, ValueError) as exc:
            rejected.append({"source": str(f), "reason": f"unreadable: {exc}"})
            continue
        sources.append(str(f))
    agg = aggregate(payloads, k=k, label=arm, sources=sources, rejected=rejected)
    agg["arm_identity"] = identity
    return agg


def finish(out_dir: Path, arms: list[str], k: int, log=print, *, identities: dict[str, dict[str, Any]]) -> int:
    aggs = {a: arm_aggregate(Path(out_dir), a, k, identities[a]) for a in arms}
    declared = {a: dict(identities[a].get("arm_env") or {}) for a in arms}
    cmp = compare(aggs[arms[0]], aggs[arms[1]], declared=declared) if len(arms) == 2 else None
    write_json(Path(out_dir) / "reliability.json", {"arms": aggs, "compare": cmp, "declared_arm_env": declared})
    for a in arms:
        log(summary_text(aggs[a], cmp if (cmp and a == arms[-1]) else None))
    log(f"result: {Path(out_dir) / 'reliability.json'}")
    if cmp is not None:
        return verdict_exit(cmp)
    return EXIT_OK if aggs[arms[0]]["status"] == "ok" and not aggs[arms[0]]["summary"]["errored"] else EXIT_UNMEASURED


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_score(args, log) -> int:
    files = expand_inputs(args.inputs)
    agg = aggregate([load_json(f) for f in files], k=args.k, label=args.label, sources=[str(f) for f in files])
    cmp = None
    if args.compare_baseline:
        if not Path(args.baseline).is_file():
            log(f"REFUSED: no reliability baseline at {args.baseline} - record one with --record-baseline")
            return EXIT_UNMEASURED
        cmp = compare(as_aggregate(Path(args.baseline), k=args.k, label="baseline"), agg)
    if args.out:
        write_json(args.out, {**agg, "compare": cmp} if cmp else agg)
    log(summary_text(agg, cmp))
    if args.record_baseline:
        why = baseline_record_problem(agg)
        if why:
            log(f"baseline NOT recorded: {why}")
            return EXIT_UNMEASURED
        write_json(args.baseline, agg)
        log(f"reliability baseline recorded: {args.baseline}")
    if cmp is not None:
        return EXIT_UNMEASURED if not cmp["measurable"] else (EXIT_RED if cmp["regressions"] else EXIT_OK)
    return EXIT_OK if agg["status"] == "ok" and not agg["summary"]["errored"] else EXIT_UNMEASURED


def _cmd_compare(args, log) -> int:
    cmp = compare(as_aggregate(args.control, k=args.k, label="control"),
                  as_aggregate(args.candidate, k=args.k, label="candidate"))
    if args.out:
        write_json(args.out, cmp)
    log(json.dumps(cmp, indent=2, sort_keys=True) if args.json else
        f"net {cmp['net']}, regressions {[r['id'] for r in cmp['regressions']]}, "
        f"net_no_collateral={cmp['net_no_collateral']}, admissible={cmp['admissible']}"
        + "".join(f"\n  - {r}" for r in cmp["reasons"]))
    if args.admissible:
        return verdict_exit(cmp)
    return EXIT_UNMEASURED if not cmp["measurable"] else EXIT_OK


def main(argv: list[str] | None = None, log=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="aggregate one arm's runs; optionally compare to / record the reliability baseline")
    s.add_argument("inputs", nargs="+", type=Path, help="results files and/or directories of run-*.json")
    s.add_argument("--k", type=int, default=DEFAULT_K)
    s.add_argument("--label", default="run")
    s.add_argument("--out", type=Path, default=None)
    s.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--compare-baseline", action="store_true",
                   help="exit 1 when a scenario that passed on the reliability baseline regressed")
    g.add_argument("--record-baseline", action="store_true",
                   help="save this aggregate as the baseline (complete, no ERROR, every run commit-bound)")
    c = sub.add_parser("compare", help="candidate vs control: collateral damage + admissibility")
    c.add_argument("--control", type=Path, required=True)
    c.add_argument("--candidate", type=Path, required=True)
    c.add_argument("--k", type=int, default=DEFAULT_K)
    c.add_argument("--out", type=Path, default=None)
    c.add_argument("--json", action="store_true", help="print the full comparison JSON")
    c.add_argument("--admissible", action="store_true",
                   help="the GEPA improver gate: exit 0 admissible / 1 not / 2 not measurable")
    r = sub.add_parser("run", help="run the harness k times per arm, interleaved (a NIGHT job)")
    r.add_argument("--out-dir", type=Path, required=True)
    r.add_argument("--arm", action="append", required=True, metavar="NAME=CHECKOUT",
                   help="an arm and the checkout whose code it serves (1 or 2; the first is the control)")
    r.add_argument("--arm-env", action="append", default=[], metavar="ARM:NAME=value,...",
                   help="flag overrides for ONE arm's scratch stack, on top of the copied live flags (ZOE_* names only; repeatable)")
    r.add_argument("--k", type=int, default=DEFAULT_K)
    r.add_argument("--harness", choices=sorted(HARNESS_SCRIPTS), default="bar")
    r.add_argument("--step-min", type=int, default=35, help="budget per harness run, minutes (gates the start)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("extra", nargs=argparse.REMAINDER, help="-- extra args for every harness run (e.g. --samples 3)")
    args = ap.parse_args(argv)
    if getattr(args, "k", 1) < 1:
        ap.error("--k must be >= 1")
    if args.cmd == "score":
        return _cmd_score(args, log)
    if args.cmd == "compare":
        return _cmd_compare(args, log)
    if not 1 <= len(args.arm) <= 2 or any("=" not in a for a in args.arm):
        ap.error("--arm NAME=CHECKOUT, once (one arm) or twice (control first, then candidate)")
    names = [a.split("=", 1)[0] for a in args.arm]
    if len(set(names)) != len(names) or any(not n or "/" in n for n in names):
        ap.error("arm names must be distinct, non-empty and contain no '/'")
    try:
        parse_arm_env(args.arm_env, names)
    except ValueError as exc:
        ap.error(str(exc))
    return run_arms(args, log)


if __name__ == "__main__":
    raise SystemExit(main())
