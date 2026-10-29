"""Shared fakes for the landing-tooling tests (scripts/landing/*.sh).

A fake `gh` and a fake `systemctl` go FIRST on PATH; the state they serve is a JSON file the test writes, and every
`gh` call is appended to a log so a test can assert what was (not) done - e.g. that a draft was never merged.
Not a test module (no test_ prefix); imported as `landing_fakes` (tests/unit is a package dir on sys.path).
"""
from __future__ import annotations

import calendar
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LANDING = REPO / "scripts" / "landing"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_GH_STATE"]))
argv = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as fh:
    fh.write(" ".join(argv) + "\n")

def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None

def emit(obj):
    jq = opt("--jq")
    text = json.dumps(obj)
    if jq:
        r = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.exit(r.returncode)
    print(text)

def pr_of(n):
    return state["prs"].get(str(n), {})

if argv[:2] == ["pr", "view"]:
    p = pr_of(argv[2])
    if state.get("pr_view_fails"):
        sys.exit(1)
    emit({
        "state": p.get("state", "OPEN"), "isDraft": p.get("isDraft", False),
        "headRefOid": p.get("headRefOid", "a" * 40), "mergeStateStatus": p.get("mergeStateStatus", "CLEAN"),
        "mergeable": p.get("mergeable", "MERGEABLE"),
        "autoMergeRequest": ({"enabledBy": "x"} if p.get("armed") else None),
        "files": [{"path": f} for f in p.get("files", [])],
    })
elif argv[:2] == ["pr", "list"]:
    emit([{"number": int(n), "isDraft": p.get("isDraft", False), "headRefName": "b%s" % n}
          for n, p in state["prs"].items() if p.get("state", "OPEN") == "OPEN"])
elif argv[:2] == ["pr", "checks"]:
    p = pr_of(argv[2])
    if "--required" in argv:
        for name, st in p.get("required", {}).items():
            print("%s\t%s\t1m\turl" % (name, st))
    else:
        emit(p.get("checks", []))
elif argv[:2] == ["api", "graphql"]:
    if state.get("graphql_fails"):
        sys.exit(1)
    n = [a for a in argv if a.startswith("n=")][0][2:]
    after = ([a for a in argv if a.startswith("a=")] or ["a=0"])[0][2:]
    t = state["threads"].get(n, {"total": 0, "unresolved": []})
    start = int(after); end = min(start + 100, t["total"])
    nodes = [{"id": "T%d" % i, "isResolved": i not in t["unresolved"], "path": "f.py", "line": i,
              "comments": {"nodes": [{"author": {"login": "bot"}, "body": "thread %d" % i}]}} for i in range(start, end)]
    emit({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": end < t["total"], "endCursor": str(end)}, "nodes": nodes}}}}})
# pr merge / ready / update-branch, run ..., anything else: logged, succeed silently
'''

FAKE_SYSTEMCTL = r'''#!/bin/bash
# fake `systemctl --user ...`: is-active from $FAKE_ACTIVE (space separated units), show from FAKE_NW_* env
shift   # --user
case "$1" in
  is-active) for u in $FAKE_ACTIVE; do [ "$u" = "$2" ] && { echo active; exit 0; }; done; echo inactive; exit 3;;
  show) case "$*" in
          *ExecMainExitTimestamp*) echo "${FAKE_NW_EXIT_TS:-}";;
          *ExecMainStatus*) echo "${FAKE_NW_STATUS:-}";;
          *) echo "";;
        esac;;
esac
exit 0
'''


def epoch(date: str, hhmm: str, sec: int = 0) -> int:
    """UTC epoch of `date` (YYYY-MM-DD) at HH:MM - the tests pin TZ=UTC, as the CI runners are."""
    t = time.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M")
    return calendar.timegm(t) + sec


class Rig:
    """A throwaway landing home + fake bin dir. Use run()/state() from a test."""

    def __init__(self, tmp: Path):
        self.home = tmp / "landing-home"
        (self.home / "coord").mkdir(parents=True)
        (self.home / "logs").mkdir()
        self.repo = tmp / "live-checkout"
        self.repo.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.gh_log = tmp / "gh.log"
        self.gh_log.touch()
        self.state_file = tmp / "gh-state.json"
        for name, body in (("gh", FAKE_GH), ("systemctl", FAKE_SYSTEMCTL)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.set_state({"prs": {}, "threads": {}})
        self.tmp = tmp

    def set_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state))

    def env(self, **extra: str) -> dict:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "TZ": "UTC",
            "LC_ALL": "C",
            "ZOE_LANDING_HOME": str(self.home),
            "ZOE_LANDING_REPO": str(self.repo),
            "FAKE_GH_STATE": str(self.state_file),
            "FAKE_GH_LOG": str(self.gh_log),
        }
        env.update(extra)
        return env

    def run(self, script: str, *args: str, timeout: int = 60, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(LANDING / script), *args], env=self.env(**extra), cwd=self.repo,
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def gh_calls(self) -> list[str]:
        return [ln for ln in self.gh_log.read_text().splitlines() if ln]


def tools_available() -> bool:
    return bool(shutil.which("jq") and shutil.which("bash") and shutil.which("flock"))
