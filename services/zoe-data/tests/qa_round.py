"""A QA round as DATA, and its runner: ``--lab`` runs it on the member-scoped lab household; ``--live`` prints the turns.

A round file (``tests/fixtures/qa_rounds/round-<id>.json``) is a list of cases with STABLE ids (``theme.subject.verb``), so round
N and round N+1 diff by id. Every name and fact in a round file is synthetic: never a household utterance.

Case fields: ``id``, ``theme``, ``title``, ``steps``; optional ``live_only`` (needs the real brain / palace: the lab reports it
``not_run``), ``known_bug`` (a one-line reason: a failing lab result is ``known_fail``, a PASSING one is ``unexpected_pass``,
which fails the CI round like ``xfail(strict=True)``), ``expect_note`` (what the live judge looks for).

Step kinds (lab): ``{"as": who, "http": [METHOD, path], "body": {...}, "query": {...}}`` (real HTTP), ``{"as", "say": text,
"card": {"id", "title", "day"}}`` (typed Skybridge, the card path), ``{"as", "voice": text}`` (a spoken turn at the panel),
``{"ring_timer": "${var}"}``, ``{"tick_alarm": "${var}"}``, ``{"detect": text}`` (the pure safety floors: ``medical`` /
``distress`` tiers). A step may ``save`` values (``{"var": "dotted.path"}``) and ``expect`` checks: ``{"status": 200}`` or
``{"path": "a.b" | "a.*.c", <op>: value}`` with op one of ``equals``, ``contains``, ``not_contains``, ``len``, ``truthy``
(bool), ``text_not_contains`` (the whole response body). ``${var}`` is substituted in every string.

``--live`` mode prints each case's ``say`` / ``voice`` turns as JSON lines for the PRIVATE live harness to send (roles map to
its demo users there); it never talks to a service itself. A live round's results come back in the same JSON shape
(``write_results``) so the markdown table diffs the same way.

  python3 services/zoe-data/tests/qa_round.py --lab  services/zoe-data/tests/fixtures/qa_rounds/round-10a.json --out DIR
  python3 services/zoe-data/tests/qa_round.py --live services/zoe-data/tests/fixtures/qa_rounds/round-10a.json

``--lab`` runs under pytest (``test_qa_round.py``) so ``tests/conftest.py`` pins every live store before a service module loads.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "known_fail", "unexpected_pass", "not_run")
_VAR = re.compile(r"\$\{(\w+)\}")


def load_round(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    ids = [c["id"] for c in data["cases"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate case ids: {dup}")
    for c in data["cases"]:
        if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", c["id"]) or not c.get("theme") or not c.get("steps"):
            raise ValueError(f"malformed case {c.get('id')!r}")
    return data


def _sub(value: Any, env: dict) -> Any:
    if isinstance(value, str):
        return _VAR.sub(lambda m: str(env[m.group(1)]), value)
    if isinstance(value, list):
        return [_sub(v, env) for v in value]
    if isinstance(value, dict):
        return {k: _sub(v, env) for k, v in value.items()}
    return value


def pick(obj: Any, path: str) -> Any:
    """``a.b.0.c``; ``*`` maps over a list (``events.*.title``)."""
    if path in ("", "."):
        return obj
    head, _, rest = path.partition(".")
    if head == "*":
        return [pick(o, rest) for o in (obj or [])]
    if isinstance(obj, list):
        return pick(obj[int(head)], rest)
    return pick((obj or {}).get(head), rest)


def check(expect: dict, status: int, body: Any, text: str) -> str | None:
    """None when the expectation holds, else why not (one line)."""
    if "status" in expect:
        return None if status == expect["status"] else f"status {status} != {expect['status']}"
    if "text_not_contains" in expect:
        return None if expect["text_not_contains"] not in text else f"response carries {expect['text_not_contains']!r}"
    got = pick(body, expect.get("path", ""))
    if "equals" in expect:
        return None if got == expect["equals"] else f"{expect['path']} = {got!r}, expected {expect['equals']!r}"
    if "contains" in expect:
        return None if expect["contains"] in (got or "") else f"{expect['path']} = {got!r}, lacks {expect['contains']!r}"
    if "not_contains" in expect:
        return None if expect["not_contains"] not in (got or "") else f"{expect['path']} = {got!r}, has {expect['not_contains']!r}"
    if "len" in expect:
        n = len(got or [])
        return None if n == expect["len"] else f"len({expect['path']}) = {n}, expected {expect['len']}"
    if "truthy" in expect:
        return None if bool(got) is expect["truthy"] else f"{expect['path']} = {got!r}, expected truthy={expect['truthy']}"
    raise ValueError(f"unknown expectation {expect!r}")


def _card(spec: dict) -> dict:
    day = spec.get("day", "2026-11-23")
    return {"intent": {"domain": "calendar"},
            "cards": [{"content": {"source": "calendar_show", "start_date": day, "events": [
                {"id": spec["id"], "title": spec["title"], "start_date": day, "start_time": spec.get("start_time", "17:00")}]}}]}


async def _lab_step(hh, step: dict) -> tuple[int, Any, str]:
    who = step.get("as", "ari")
    if "http" in step:
        method, path = step["http"]
        r = hh.http(who, method, path, step.get("body"), **(step.get("query") or {}))
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body, r.text
    if "say" in step:
        out = hh.say(who, step["say"], context=_card(step["card"]) if step.get("card") else None)
        return 200, out, json.dumps(out)
    if "voice" in step:
        out = await hh.voice(who, step["voice"])
        return 200, out, json.dumps(out, default=str)
    if "ring_timer" in step:
        await hh.ring_timer(step["ring_timer"])
        return 200, {"announcements": hh.announcements()}, json.dumps(hh.announcements())
    if "tick_alarm" in step:
        fire_at = hh.db.rows("SELECT next_fire_at FROM alarms WHERE id = ?", (step["tick_alarm"],))[0]["next_fire_at"]
        events = await hh.tick_alarms(fire_at + 1)
        out = {"events": [{"event": e, "id": a["id"]} for e, a in events]}
        return 200, out, json.dumps(out)
    if "detect" in step:
        import distress_handoff
        import medical_safety

        out = {"medical": medical_safety.detect(step["detect"]).cls, "distress": distress_handoff.detect(step["detect"]).tier}
        return 200, out, json.dumps(out)
    raise ValueError(f"unknown step {step!r}")


async def run_case_lab(case: dict, make_household) -> dict:
    """One case on a FRESH household (``make_household()``); returns ``{id, theme, status, why}``."""
    base = {"id": case["id"], "theme": case["theme"]}
    if case.get("live_only"):
        return {**base, "status": "not_run", "why": "live only (needs the brain / palace)"}
    hh = make_household()
    env: dict[str, Any] = {}
    why = None
    try:
        for n, raw in enumerate(case["steps"], 1):
            step = _sub(raw, env)
            status, body, text = await _lab_step(hh, step)
            for name, path in (step.get("save") or {}).items():
                env[name] = pick(body, path)
            for exp in step.get("expect") or []:
                why = check(exp, status, body, text)
                if why:
                    why = f"step {n}: {why}"
                    break
            if why:
                break
        if hh.brain_calls and not why:
            why = f"{len(hh.brain_calls)} turn(s) fell through to the (fake) brain"
    except Exception as exc:  # noqa: BLE001 - a crashed case is a failed case, with its reason
        why = f"error: {type(exc).__name__}: {str(exc)[:160]}"
    passed = why is None
    if case.get("known_bug"):
        status = "unexpected_pass" if passed else "known_fail"
        why = (f"known bug no longer reproduces: {case['known_bug']}" if passed else f"known bug: {case['known_bug']} ({why})")
    else:
        status = "pass" if passed else "fail"
    return {**base, "status": status, "why": why or ""}


def summarise(round_id: str, mode: str, results: list[dict]) -> dict:
    themes: dict[str, dict[str, int]] = {}
    for r in results:
        themes.setdefault(r["theme"], {s: 0 for s in STATUSES})[r["status"]] += 1
    return {"round": round_id, "mode": mode, "cases": sorted(results, key=lambda r: r["id"]),
            "themes": dict(sorted(themes.items())), "ok": not any(r["status"] in ("fail", "unexpected_pass") for r in results)}


def markdown(summary: dict) -> str:
    lines = [f"# QA round {summary['round']} ({summary['mode']})", "", "| case id | theme | status | why |", "|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['theme']} | {r['status']} | {r['why'].replace('|', '/')} |" for r in summary["cases"]]
    lines += ["", "| theme | " + " | ".join(STATUSES) + " |", "|---|" + "---|" * len(STATUSES)]
    lines += [f"| {t} | " + " | ".join(str(c[s]) for s in STATUSES) + " |" for t, c in summary["themes"].items()]
    return "\n".join(lines) + "\n"


def scorecard_line(summary: dict) -> str:
    """One line the felt scorecard reads: ``QA_ROUND round=10a mode=lab calendar=5/5 lists=2/2 ...`` (passed/run, not_run apart)."""
    parts = [f"QA_ROUND round={summary['round']} mode={summary['mode']}"]
    for theme, c in summary["themes"].items():
        run = c["pass"] + c["fail"] + c["known_fail"] + c["unexpected_pass"]
        parts.append(f"{theme}={c['pass']}/{run}" + (f"+{c['not_run']}nr" if c["not_run"] else ""))
    return " ".join(parts)


def write_results(summary: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"round-{summary['round']}-{summary['mode']}"
    (out / f"{stem}.json").write_text(json.dumps({**summary, "scorecard": scorecard_line(summary)}, indent=1) + "\n")
    (out / f"{stem}.md").write_text(markdown(summary))
    return out / f"{stem}.json"


def live_turns(data: dict) -> list[dict]:
    """The turns the private live harness sends, one dict per turn (http / lab-only steps are not part of a live round)."""
    out = []
    for case in data["cases"]:
        turns = [s for s in case["steps"] if "say" in s or "voice" in s]
        for n, s in enumerate(turns, 1):
            out.append({"case": case["id"], "theme": case["theme"], "turn": n, "as": s.get("as", "ari"),
                        "lane": "voice" if "voice" in s else "typed", "text": s.get("say") or s.get("voice"),
                        "expect_note": case.get("expect_note", "")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--lab", action="store_true", help="run on the lab household (under pytest)")
    mode.add_argument("--live", action="store_true", help="print the turns for the private live harness (JSON lines)")
    ap.add_argument("round_file")
    ap.add_argument("--out", default="", help="--lab: directory for round-<id>-lab.json / .md")
    a = ap.parse_args(argv)
    data = load_round(a.round_file)
    if a.live:
        for t in live_turns(data):
            print(json.dumps(t))
        return 0
    import pytest

    os.environ["QA_ROUND_FILE"] = str(Path(a.round_file).resolve())
    if a.out:
        os.environ["QA_ROUND_OUT"] = str(Path(a.out).resolve())
    return int(pytest.main(["-q", "-p", "no:cacheprovider", str(Path(__file__).with_name("test_qa_round.py")), "-k", "the_round"]))


if __name__ == "__main__":
    sys.exit(main())
