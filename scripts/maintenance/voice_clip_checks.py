#!/usr/bin/env python3
"""Per-clip outcome assertions for the voice replay gate (T-3, deep dive 2, 2026-10-19).

Before this, the gate's "OK" meant "not empty, not an error, not a refusal phrase"
(``replay_samples._classify``). A clip that confidently did the WRONG thing (a list
add routed to chat, a timer request answered as small talk) scored OK, and the
per-clip ``route_hash`` the baseline already stored was read only for clips that
had already failed. This module turns "did not refuse" into "did what it did
before, or what the owner labelled it to do".

WHAT A PINNED CLIP MUST DO NOW, when it has an expectation:
  * STT heard something (a clip that was OK and is now EMPTY is a regression, not a
    silent recording: it was heard before);
  * the verdict is OK (no CANT_DO / ERROR / DEFERRED);
  * it took the SAME ROUTE: the lane (fast | brain), and for a fast-path clip the
    tier:domain:intent; with a signature-bearing baseline also the router's domain
    + chosen tool and the Tier-0 intent (``route_sig``). A legacy baseline only has
    ``route`` (the hash of tier:domain:intent, or of "brain"), and that is compared;
  * every field an owner label names matches (the expectations file).

WHERE EXPECTATIONS COME FROM (never the repo: the corpus is household audio):
  1. ``--expectations`` (default ~/.zoe/voice-gate/clip_expectations.json, env
     ZOE_VOICE_CLIP_EXPECTATIONS): owner labels keyed by clip id, e.g.
     {"version": 1, "clips": {"<clip id>": {"lane": "fast", "domain": "lists",
      "intent": "add_item", "note": "..."}}}. A label OVERRIDES the baseline for
     that clip and is checked against the actual labels field by field. A field the
     label OMITS (``seed`` writes only lane/tier/domain/intent) is still held to the
     baseline's recorded label for it (router_domain / router_tool / tier0), when the
     baseline was OK and carries labels; to change one of those on purpose, name it.
  2. the baseline's per-clip profile (``summary.clip_profile``): every clip that was
     OK in the baseline is expected to stay OK on the same route.
  A clip that was not OK in the baseline and has no owner label is "unchecked".

WHAT IT CANNOT SEE (stated so a green gate is not over-read): the brain's own tool
choice (sampled at temperature 0.7; a brain-lane clip is checked for its lane and
router decision, not for which tool the brain then called), the reply's wording
beyond the refusal patterns, and anything after the brain (TTS, the panel).

Stdlib only. Clip ids, hashes and code labels only - never a clip name, transcript
or reply. ``reasons`` (which reach the PUBLIC voice-gate log via the artifact's
``reason``) carry clip ids and the kind of mismatch only; the labels themselves
stay in ``clip_results`` in the local artifact.

CLI:
    voice_clip_checks.py show [--artifact PATH]      # per-clip table of the last run (local)
    voice_clip_checks.py seed [--artifact PATH] [--expectations PATH] [--force]
        # write an expectations file from the last PASSING run's observed labels,
        # for the owner to review and correct (label_source "observed")
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

DEFAULT_EXPECTATIONS = Path.home() / ".zoe" / "voice-gate" / "clip_expectations.json"


def default_expectations() -> Path:
    """ZOE_VOICE_CLIP_EXPECTATIONS (read per call, so a test can point it at a temp dir), else the default."""
    return Path(os.environ.get("ZOE_VOICE_CLIP_EXPECTATIONS") or DEFAULT_EXPECTATIONS)


DEFAULT_ARTIFACT = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
LABEL_FIELDS = ("lane", "tier", "domain", "intent", "router_domain", "router_tool", "tier0")
OK = "OK"


# ── loading ──────────────────────────────────────────────────────────────────
def load_expectations(path: Path | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    """(clip id -> owner label, problem). An absent file is no labels and no problem;
    a present but malformed one is a PROBLEM (the gate fails: an owner's labels that
    cannot be read must not silently stop being checked)."""
    if path is None or not Path(path).exists():
        return {}, None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"expectations file {path} unreadable: {exc}"
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, dict):
        return {}, f"expectations file {path} has no 'clips' object"
    out: dict[str, dict[str, Any]] = {}
    for cid, lab in clips.items():
        if not isinstance(cid, str) or not isinstance(lab, dict):
            return {}, f"expectations file {path}: entry {cid!r} is not an object"
        unknown = [k for k in lab if k not in LABEL_FIELDS + ("note", "label_source", "category")]
        if unknown:
            return {}, f"expectations file {path}: clip {cid} has unknown field(s) {unknown}"
        if not any(k in lab for k in LABEL_FIELDS):
            return {}, f"expectations file {path}: clip {cid} labels nothing ({', '.join(LABEL_FIELDS)})"
        out[cid] = lab
    return out, None


def baseline_profile(baseline: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    summ = baseline.get("summary") if isinstance(baseline, dict) else None
    prof = summ.get("clip_profile") if isinstance(summ, dict) else None
    return prof if isinstance(prof, dict) else {}


# ── the check ────────────────────────────────────────────────────────────────
def _labels_of(row: dict[str, Any]) -> dict[str, Any] | None:
    lab = row.get("route")
    return lab if isinstance(lab, dict) else None


def check_clip(cid: str, row: dict[str, Any] | None, expect_base: dict[str, Any] | None,
               owner: dict[str, Any] | None) -> dict[str, Any]:
    """One pinned clip against its expectation. Pure. ``row`` is the measure_voice row
    (verdict, route_hash, route_sig, route labels, heard_hash) or None when the clip
    was not replayed; ``expect_base`` its baseline profile entry; ``owner`` its label."""
    res: dict[str, Any] = {"clip_id": cid}
    base_ok = isinstance(expect_base, dict) and expect_base.get("verdict") == OK
    if owner is None and not base_ok:
        res.update(result="unchecked",
                   why=("no baseline profile entry and no owner label" if not isinstance(expect_base, dict)
                        else f"baseline verdict {expect_base.get('verdict')} (not OK): nothing to hold it to"))
        if row is not None:
            res["verdict"] = row.get("verdict")
        return res
    res["expected_from"] = "owner" if owner is not None else "baseline"
    if row is None:
        res.update(result="fail", kind="missing", why="the clip was not replayed")
        return res
    verdict = row.get("verdict")
    actual = _labels_of(row)
    res.update(verdict=verdict, route_actual=row.get("route_hash"), sig_actual=row.get("route_sig"),
               labels=actual)
    if isinstance(expect_base, dict):
        res["heard_changed"] = (expect_base.get("heard") is not None
                                and row.get("heard_hash") != expect_base.get("heard"))
    problems: list[tuple[str, str]] = []
    if verdict == "EMPTY":
        problems.append(("stt-empty", "STT heard nothing on a clip that was heard before"))
    elif verdict != OK:
        problems.append(("verdict", f"verdict {verdict} (expected OK)"))
    if owner is not None:
        res["expected_labels"] = {k: owner[k] for k in LABEL_FIELDS if k in owner}
        if actual is None:
            problems.append(("labels-missing", "the replay recorded no route labels to check the owner label against"))
        else:
            bad = [k for k in LABEL_FIELDS if k in owner and owner[k] != actual.get(k)]
            if bad:
                problems.append(("label", "owner label mismatch on " + ", ".join(bad)))
            # A field the owner label OMITS is not thereby unchecked: it falls back to the
            # baseline's recorded label when the baseline was OK and carries labels (`seed`
            # writes only lane/tier/domain/intent, and a hand-written label may name fewer).
            b_lab = expect_base.get("labels") if base_ok and isinstance(expect_base.get("labels"), dict) else None
            if b_lab is not None:
                fell_back = [k for k in LABEL_FIELDS if k not in owner and k in b_lab]
                drift = [k for k in fell_back if b_lab.get(k) != actual.get(k)]
                if drift:
                    problems.append(("label", "baseline mismatch (field the owner label omits) on " + ", ".join(drift)))
    elif verdict != "EMPTY":
        # Baseline-derived: the full signature when both sides carry one, else the
        # legacy route hash (tier:domain:intent, or "brain").
        b_sig, a_sig = expect_base.get("sig"), row.get("route_sig")
        if b_sig and a_sig:
            res["compared"] = "route_sig"
            res["sig_expected"] = b_sig
            if b_sig != a_sig:
                problems.append(("route", "route signature changed"))
                b_lab = expect_base.get("labels") if isinstance(expect_base.get("labels"), dict) else None
                if b_lab is not None:
                    res["expected_labels"] = b_lab
                    if actual is not None:
                        res["changed_fields"] = [k for k in LABEL_FIELDS if b_lab.get(k) != actual.get(k)]
        elif b_sig:
            # A signature baseline is only ever held to the signature. The replay lacking
            # one (an instrumentation regression) must NOT fall back to the coarser route
            # hash, which can pass with router_domain/router_tool/tier0 never observed.
            res["compared"] = "route_sig"
            res["sig_expected"] = b_sig
            problems.append(("route-sig-missing", "the baseline carries a route signature but the replay "
                                                  "recorded none (route labels not observed)"))
        else:
            res["compared"] = "route_hash"
            res["route_expected"] = expect_base.get("route")
            if expect_base.get("route") is None:
                problems.append(("route-missing", "the baseline profile has no route for this clip"))
            elif expect_base.get("route") != row.get("route_hash"):
                problems.append(("route", "route (tier:domain:intent / lane) changed"))
    if problems:
        res.update(result="fail", kind=problems[0][0], why="; ".join(p[1] for p in problems))
    else:
        res["result"] = "pass"
    return res


def evaluate(rows: list[dict[str, Any]], baseline: dict[str, Any] | None,
             owner_labels: dict[str, dict[str, Any]], pinned_ids: list[str] | None,
             clip_id_of: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
    """Every pinned clip (or, with no pin, every replayed clip) against its expectation.

    Returns {results, summary: {checked, passed, failed, unchecked, compared}, regressions}.
    ``regressions`` are public-safe strings (clip ids + mismatch kind only)."""
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        cid = clip_id_of(r)
        if cid:
            by_id[cid] = r
    prof = baseline_profile(baseline)
    ids = list(pinned_ids) if pinned_ids else list(by_id)
    for cid in owner_labels:            # an owner-labelled clip outside the pin is still checked if replayed
        if cid not in ids and cid in by_id:
            ids.append(cid)
    results = [check_clip(cid, by_id.get(cid), prof.get(cid), owner_labels.get(cid)) for cid in ids]
    checked = [r for r in results if r["result"] != "unchecked"]
    failed = [r for r in checked if r["result"] == "fail"]
    # how routes were compared: route_sig | route_hash (baseline profile), owner (labels)
    compared = sorted({r.get("compared") or ("owner" if r.get("expected_from") == "owner" else None)
                       for r in checked} - {None})
    regressions = [f"FUNCTION: clip {r['clip_id']} {r['kind']}" for r in failed]
    return {"results": results,
            "summary": {"checked": len(checked), "passed": len(checked) - len(failed), "failed": len(failed),
                        "unchecked": len(results) - len(checked), "owner_labelled": sum(
                            1 for r in checked if r.get("expected_from") == "owner"),
                        "compared": compared},
            "regressions": regressions}


def profile_entry(row: dict[str, Any]) -> dict[str, Any]:
    """The per-clip baseline profile entry for a measure_voice row (content-free)."""
    out = dict(verdict=row["verdict"], via_brain=row.get("via_brain") is True,
               route=row.get("route_hash"), heard=row.get("heard_hash"))
    if row.get("route_sig"):
        out["sig"] = row["route_sig"]
    if isinstance(row.get("route"), dict):
        out["labels"] = {k: row["route"].get(k) for k in LABEL_FIELDS}
    return out


# ── self-test verdicts ───────────────────────────────────────────────────────
def fault_caught(fault: str, clip_eval: dict[str, Any], expected_ok: int) -> tuple[bool, str]:
    """Did the per-clip checks go red on an injected fault? ``expected_ok`` is the
    number of clips the baseline/owner expects to be OK.

    blank_stt: EVERY expected clip must fail as stt-empty (a blank STT that slipped
               through on any clip means the EMPTY exclusion still hides it).
    misroute:  at least one expected clip must fail on its route/labels (the router
               moved EVERY clip to another domain; a signature-bearing baseline must
               catch all of them, a legacy route-hash baseline at least the fast-path ones)."""
    res = [r for r in clip_eval.get("results", []) if r.get("result") != "unchecked"]
    if expected_ok <= 0 or not res:
        return False, "no clip had an expectation - the self-test has nothing to judge against"
    if fault == "blank_stt":
        empty = [r for r in res if r.get("result") == "fail" and r.get("kind") == "stt-empty"]
        return (len(empty) == len(res),
                f"{len(empty)}/{len(res)} expected clips failed as stt-empty")
    if fault == "misroute":
        moved = [r for r in res if r.get("result") == "fail" and r.get("kind") in ("route", "label", "verdict")]
        return bool(moved), f"{len(moved)}/{len(res)} expected clips failed on route/label/verdict"
    return False, f"unknown fault {fault!r}"


# ── CLI ──────────────────────────────────────────────────────────────────────
def _read_artifact(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read artifact {path}: {exc}")


def _cmd_show(args) -> int:
    art = _read_artifact(args.artifact)
    print(f"artifact {args.artifact}: status={art.get('status')} self_test={art.get('self_test', False)}")
    print(f"clip checks: {json.dumps(art.get('clip_summary'))}")
    for r in art.get("clip_results") or []:
        lab = r.get("labels") or {}
        print(f"  {r['clip_id']}  {r.get('result'):<9} {str(r.get('verdict')):<8} "
              f"lane={lab.get('lane')} {lab.get('tier')}:{lab.get('domain')}:{lab.get('intent')} "
              f"router={lab.get('router_domain')}/{lab.get('router_tool')} tier0={lab.get('tier0')}"
              f"{'  <- ' + r['why'] if r.get('result') == 'fail' else ''}")
    return 0


def _cmd_seed(args) -> int:
    art = _read_artifact(args.artifact)
    if art.get("status") != "pass" or art.get("self_test"):
        print("refusing to seed from a run that is not a real PASS", file=sys.stderr)
        return 2
    path = Path(args.expectations or default_expectations())
    if path.exists() and not args.force:
        print(f"{path} exists; pass --force to overwrite (owner labels would be lost)", file=sys.stderr)
        return 2
    clips = {}
    for r in art.get("clip_results") or []:
        lab = r.get("labels")
        if r.get("verdict") == OK and isinstance(lab, dict):
            clips[r["clip_id"]] = {**{k: lab.get(k) for k in ("lane", "tier", "domain", "intent")},
                                   "label_source": "observed"}
    if not clips:
        print("the artifact carries no per-clip labels (replay predates T-3?) - nothing to seed", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "clips": clips}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"wrote {len(clips)} observed labels to {path} - review them: a wrong label here is a wrong bar")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    s = sub.add_parser("seed")
    s.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    s.add_argument("--expectations", type=Path, default=None)
    s.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    return _cmd_show(args) if args.cmd == "show" else _cmd_seed(args)


if __name__ == "__main__":
    sys.exit(main())
