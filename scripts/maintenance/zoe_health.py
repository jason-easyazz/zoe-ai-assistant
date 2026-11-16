#!/usr/bin/env python3
"""ONE health check on the canonical spine (architecture deep dive B9), run by zoe-health.timer every 5 minutes.

It replaces the two untracked watchdogs: ``~/bin/zoe-health-check.sh`` (zoe-health.service: still probed Hermes
``:8642``, never the live brain ``:3579`` or Kokoro ``:10201``, carried a literal database password, and its only
output was a log nobody read) and ``~/bin/zoe-watchdog.sh`` (a crontab line: zoe-data + llama only, pushed through
zoe-data's own API, so it could not report zoe-data being down).

Probes, all bounded (2 s HTTP, 5 s docker/systemctl):
  * the spine's real ``/health`` (never ``is-active``, which lies while a model loads): zoe-data :8000,
    the 4B llama-server :11434, the Flue 2.x brain :3579, Kokoro :10201, the FunctionGemma router :11436;
  * Postgres (``pg_isready`` in zoe-database), containers in ``(unhealthy)`` / ``Restarting``;
  * failed user units (a failed backup or verify job shows up here), disk on /, MemAvailable;
  * the restore drill has passed in the last 10 days (``zoe_restore_drill.py --check-stale``): a failed unit clears
    when the next run is merely refused or never fires, so staleness is its own check. ONE voice: while the drill is
    running it is the voice for its own bad spells (``--check-stale`` exit 6: stale, but it recorded a run in the last 2
    days), so this check stays ok (report-only); it alerts only when the drill has stopped running (a dead timer), exit 4.
  * the forget ledger: zoe-data's ``/health`` field ``forget_ledger`` (an enum; the secret itself is never read here).
    ``unconfigured`` is red (a forget is not permanent); unreadable is ``unknown``, never red on its own.
While the night window is open (``~/.zoe/night-window/WINDOW_OPEN``) the spine is stopped ON PURPOSE: its probes
are skipped, not failed. A valid PLANNED-window marker (``maintenance_window.sh``; ``read_planned_window``) skips the
probes in ``WINDOW_TAKES`` the same way. docs/knowledge/health-alerts.md has the marker and wording contracts.

Alerting is on TRANSITIONS only: two consecutive failures alert, one recovery message follows (only for something
alerted), one message per run, plain sentences (``CHECK_WORDS`` + ``zoe_crash_loop_watch.PLAIN_WORDS``); a check that
keeps dropping out is said once (``FLAP_*``). State: ``~/.local/state/zoe-health/state.json``. The alert goes over Telegram through the shared owner-alert helper
(``zoe_crash_loop_watch.deliver``: retries + a durable outbox) - a process separate from zoe-data, so it can report
zoe-data's death. The "owner alerts" check is red while that helper records alerting as broken (Telegram refused the
credential, or no recipient), so the health log says so even though its own Telegram line cannot get through.

    python3 scripts/maintenance/zoe_health.py            # one run (the unit)
    python3 scripts/maintenance/zoe_health.py --dry-run  # probe + print, no state, no alert

Exit 0 healthy, 1 a check is failing (whether or not it alerted this run).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

HOME = Path(os.environ.get("HOME", str(Path.home())))
REPO_ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("ZOE_HEALTH_STATE", str(HOME / ".local" / "state" / "zoe-health" / "state.json")))
NIGHT_MARKER = Path(os.environ.get("NIGHT_DIR", str(HOME / ".zoe" / "night-window"))) / "WINDOW_OPEN"
# The planned-window marker (scripts/AGENTS.md; written by maintenance_window.sh): JSON {"until": <epoch>, "kind": <kind>}
# and nothing else. Overridable for tests the same way NIGHT_DIR is.
MAINT_MARKER = Path(os.environ.get("MAINTENANCE_WINDOW_DIR", str(HOME / ".zoe" / "maintenance-window"))) / "OPEN"
WINDOW_KINDS = {"test": "test", "replay": "voice replay", "deploy": "update", "other": "maintenance"}
WINDOW_MAX_S = 2 * 3600       # a marker further ahead than this is ignored: a stale marker can never silence an outage
OVERRUN_RECALL_S = 2 * 3600   # an ended window still explains (in words) a service that stayed off, for this long
# The services a planned window stops ON PURPOSE. Today only the speech service: the landing probe window stops and
# restarts kokoro-tts and nothing else (scripts/landing/landing_lib.sh landing_probe_window). The brain, zoe-data, the
# database, disk and memory are NOT expected to drop and keep alerting through any window.
WINDOW_TAKES = ("kokoro-tts",)

# The canonical spine (docs/CANONICAL.md): name -> port. Retired lanes (Hermes :8642, OpenClaw :18789, Flue 1.x
# :3578) are deliberately absent; tests/unit/test_zoe_health.py fails if one comes back.
SPINE: dict[str, int] = {
    "zoe-data": 8000,
    "llama-server": 11434,
    "flue-zoe-brain-2x": 3579,
    "kokoro-tts": 10201,
    "functiongemma-router": 11436,
}
DISK_WARN_PCT = 90
MEM_WARN_MB = 700          # the remote voice-gate floor; below it the box is in trouble
FAILS_TO_ALERT = 2
FLAP_COUNT = 3                # the 3rd alerted drop of one check within FLAP_WINDOW_S is said once, as "keeps dropping out"
FLAP_WINDOW_S = 6 * 3600      # ...and the dropouts are over (one closing line) after this long with no new drop
FLAP_LONG_S = 3600            # while dropping out, a single drop longer than this is told like an ordinary outage


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    unknown: bool = False      # no verdict this run: printed, but neither alerts nor recovers (``transitions`` skips it)
    code: str = ""             # the short technical code that ends an owner message, e.g. "no-answer" (never a path)
    skipped: bool = False      # taken down ON PURPOSE by a planned window: no verdict, and a not-yet-alerted count restarts
    overrun: Optional[dict] = None   # {"kind", "until"} of a planned window that ended while this check is still failing


def _http_code(ok: bool, why: str) -> str:
    if ok:
        return "ok"
    if why.startswith("HTTP "):
        return "error-reply"
    return "timed-out" if "timeout" in why.lower() else "no-answer"


def read_planned_window(now: float, path: Optional[Path] = None) -> tuple[Optional[dict], Optional[dict], str]:
    """(open, overrun, note). ``open`` is {kind, until} of a VALID unexpired marker; ``overrun`` is a valid-shaped marker
    whose ``until`` passed less than OVERRUN_RECALL_S ago (the tooling never closed it, or the window ran long). A marker
    that is unreadable, has any key but ``until`` + ``kind``, an unknown kind, a non-number ``until`` or an ``until``
    more than WINDOW_MAX_S ahead is IGNORED (note says why, for the log): it silences nothing."""
    p = path or MAINT_MARKER
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return None, None, ""
    except (OSError, ValueError):
        return None, None, "planned-window marker ignored: unreadable"
    if not isinstance(raw, dict) or set(raw) != {"until", "kind"} or raw["kind"] not in WINDOW_KINDS:
        return None, None, "planned-window marker ignored: malformed"
    until = raw["until"]
    if isinstance(until, bool) or not isinstance(until, (int, float)) or until != until:
        return None, None, "planned-window marker ignored: malformed"
    if until > now + WINDOW_MAX_S:
        return None, None, "planned-window marker ignored: too far ahead"
    w = {"kind": raw["kind"], "until": float(until)}
    if until > now:
        return w, None, f"planned {raw['kind']} window open: {', '.join(WINDOW_TAKES)} probe skipped"
    if now - until <= OVERRUN_RECALL_S:
        return None, w, f"planned {raw['kind']} window ended {int(now - until)} s ago (marker not closed)"
    return None, None, "planned-window marker ignored: expired"


def http_ok(port: int, path: str = "/health", timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _run(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, type(exc).__name__


NIGHT_TIMER = "zoe-night-window.timer"
DRILL = REPO_ROOT / "scripts" / "maintenance" / "zoe_restore_drill.py"


def restore_drill_check(run: Callable = _run) -> Check:
    """A drill refused week after week, or a verify timer that stopped firing, never shows as a failed unit."""
    rc, txt = run([sys.executable, str(DRILL), "--check-stale"], timeout=20.0)
    line = (txt or "").strip().splitlines()[-1:] or [f"rc={rc}"]
    # rc 6 = stale, but the drill is still running (a run recorded in the last 2 days): its own owner_alert cadence
    # (transition, weekly reminder, "passed again") is the ONE voice, so this check stays ok (report-only; the line still
    # says STALE in health.log). When the drill stops running (dead timer), rc becomes 4 and this check alerts instead.
    return Check("restore drill", rc in (0, 6), line[0], code="stale" if rc not in (0, 6) else "ok")


def night_timer_check(run: Callable = _run) -> Check:
    """The night window only happens if its timer is armed. A stopped/disabled timer used to be silent (one night the 12B simply
    never ran and nothing said so); ``failed units`` cannot see it because an inactive timer is not failed. Not skipped while the
    window is open: the timer stays active while its service runs."""
    _, active = run(["systemctl", "--user", "is-active", NIGHT_TIMER])
    _, enabled = run(["systemctl", "--user", "is-enabled", NIGHT_TIMER])
    active, enabled = (active or "").strip(), (enabled or "").strip()
    ok = active == "active" and enabled in ("enabled", "enabled-runtime")
    return Check("night-window timer", ok, f"{NIGHT_TIMER} is {active or '?'} / {enabled or '?'}" if not ok else "armed",
                 code=f"{active or '?'}/{enabled or '?'}")


# Desktop-session units (xdg portals, pipewire, gnome-*) fail on a headless login and are not Zoe's: alerting on them
# would page the owner every boot for nothing. Everything else stays watched, including units this list does not know.
FOREIGN_UNIT_PREFIXES = ("xdg-", "gnome-", "org.gnome.", "at-spi", "dbus-", "pipewire", "wireplumber", "pulseaudio", "tracker-", "evolution-")


def _foreign_unit(unit: str) -> bool:
    return unit.startswith(FOREIGN_UNIT_PREFIXES)


def fetch_forget_ledger(timeout: float = 2.0) -> Optional[str]:
    """zoe-data's published forget-ledger state (``configured`` / ``unconfigured``), or None when it cannot be read."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{SPINE['zoe-data']}/health", timeout=timeout) as r:
            state = json.loads(r.read().decode("utf-8", "replace")).get("forget_ledger")
    except Exception:  # noqa: BLE001 - down / 503 / not JSON: the zoe-data check already covers down
        return None
    return state if state in ("configured", "unconfigured") else None


def forget_ledger_check(state: Optional[str]) -> Check:
    """Red while forgets cannot be permanent ("forgotten means forever" needs the ledger secret). Names no value."""
    if state == "configured":
        return Check("forget ledger", True, "configured")
    if state == "unconfigured":
        return Check("forget ledger", False, "unconfigured: a forget is not permanent until ZOE_FORGET_LEDGER_SALT is set "
                                            "in zoe-data's env", code="unconfigured")
    return Check("forget ledger", True, "unknown (zoe-data status unreadable)", unknown=True)


def probe(http: Callable[[int], tuple[bool, str]] = http_ok, run: Callable = _run,
          night_open: Optional[bool] = None,
          forget_ledger: Optional[Callable[[], Optional[str]]] = None,
          planned: Optional[tuple] = None) -> list[Check]:
    """``planned`` is ``read_planned_window``'s (open, overrun, ...) result; None reads the marker now."""
    out: list[Check] = []
    night = NIGHT_MARKER.exists() if night_open is None else night_open
    w_open, w_over = (planned or read_planned_window(time.time()))[:2]
    for name, port in SPINE.items():
        if night:
            continue
        if w_open and name in WINDOW_TAKES:
            out.append(Check(f"{name} :{port}", True, f"skipped: planned {w_open['kind']} window", skipped=True))
            continue
        ok, why = http(port)
        out.append(Check(f"{name} :{port}", ok, why, code=_http_code(ok, why),
                         overrun=w_over if name in WINDOW_TAKES and not ok else None))
    rc, _ = run(["docker", "exec", "zoe-database", "pg_isready", "-U", "zoe", "-q"])
    out.append(Check("postgres", rc == 0, f"pg_isready rc={rc}", code="not-ready"))
    rc, txt = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
    bad = [ln.split("\t")[0] for ln in txt.splitlines() if "(unhealthy)" in ln or "\tRestarting" in ln] if rc == 0 else []
    out.append(Check("containers", rc == 0 and not bad, ", ".join(bad) if bad else f"docker rc={rc}" if rc else "ok",
                     code=", ".join(bad) if bad else "docker-unreadable"))
    rc, txt = run(["systemctl", "--user", "--failed", "--plain", "--no-legend", "--type=service,timer"])
    failed = sorted(u for u in (ln.split()[0] for ln in txt.splitlines() if ln.strip()) if not _foreign_unit(u)) if rc == 0 else []
    out.append(Check("failed units", not failed, ", ".join(failed) if failed else "none", code=", ".join(failed)))
    out.append(night_timer_check(run))
    out.append(restore_drill_check(run))
    du = shutil.disk_usage("/")
    pct = round(100 * du.used / du.total)
    out.append(Check("disk /", pct < DISK_WARN_PCT, f"{pct}% used", code=f"{pct}% full"))
    avail = 0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    out.append(Check("memory", avail >= MEM_WARN_MB, f"MemAvailable {avail} MB", code=f"{avail} MB free"))
    if forget_ledger is not None:
        out.append(forget_ledger_check(forget_ledger()))
    return out


# ── what each check means to the owner (2026-11-16: "a bit more human, not like reading a log") ─────────────────────
# The spine services' words live in zoe_crash_loop_watch.PLAIN_WORDS (shared with the crash-loop watch). ``tech`` names
# the thing in the bracket at the END of a message; ``outage`` picks "It was out for" over "That lasted".
TODO_WAIT = "Nothing for you to do yet; I'll tell you when it's back."
TODO_SELF = "Nothing for you to do yet; it normally restarts by itself, and I'll tell you when it's back."
# after a planned window the service was STOPPED by hand, so a restart-on-failure policy will not bring it back
TODO_OVERRUN = "It may have been left switched off; if it isn't back soon, someone technical needs to start it again."
TODO_TECH = "It won't sort itself out, so it needs someone technical to look at it; nothing else breaks meanwhile."
CHECK_WORDS: dict[str, dict] = {
    "postgres": {"noun": "my memory", "tech": "database", "outage": True, "todo": TODO_WAIT,
                 "headline": "I can't reach my memory.",
                 "means": "I can't remember or look anything up: lists, reminders and what I know are out of reach.",
                 "back": "I can reach my memory again."},
    "containers": {"noun": "my background helpers", "tech": "containers", "outage": True, "todo": TODO_WAIT,
                   "headline": "One of my background helpers is unwell.",
                   "means": "Most things still work, but whatever that helper does for me may be slow or missing.",
                   "back": "My background helpers are all healthy again."},
    "failed units": {"noun": "my scheduled jobs", "tech": "jobs", "outage": False,
                     "todo": "Nothing for you to do yet; I'll tell you once it's sorted.",
                     "headline": "One of my scheduled jobs did not finish.",
                     "means": "Everything still works for now, but that job needs to run properly next time.",
                     "back": "My scheduled jobs are all finishing again."},
    "night-window timer": {"noun": "my overnight routine", "tech": "zoe-night-window.timer", "outage": False,
                           "todo": TODO_TECH, "headline": "My overnight routine is switched off.",
                           "means": "Tonight I won't do my overnight thinking and tidying up.",
                           "back": "My overnight routine is switched back on."},
    "restore drill": {"noun": "my practice restore", "tech": "restore-drill", "outage": False,
                      "todo": "It needs someone technical to look at it this week; nothing breaks meanwhile.",
                      "headline": "My practice restore from backup has stopped running.",
                      "means": "The backups may well be fine, but I can't promise they would restore until one passes.",
                      "back": "My practice restore is running again."},
    "disk /": {"noun": "my storage", "tech": "disk", "outage": False,
               "todo": "It won't clear by itself; some old files need tidying up, so ask someone technical soon.",
               "headline": "I'm running out of storage.",
               "means": "If it fills up I won't be able to save anything new.",
               "back": "I have enough storage again."},
    "memory": {"noun": "my working memory", "tech": "memory", "outage": False, "todo": TODO_WAIT,
               "headline": "I'm running short of working memory.",
               "means": "I may be slow to answer, or miss a request now and then.",
               "back": "My working memory is back to normal."},
    "forget ledger": {"noun": "permanent forgetting", "tech": "forget-ledger", "outage": False,
                      "todo": "It needs a setting that someone technical can add; nothing else is affected.",
                      "headline": "Forgetting isn't permanent right now.",
                      "means": "If you ask me to forget something I'll hide it, but I can't promise it's gone for good.",
                      "back": "Forgetting is permanent again."},
    "owner alerts": {"noun": "my messages to you", "tech": "telegram", "outage": True,
                     "todo": "I'm keeping them and will send them as soon as it works; the Telegram setup may need a look.",
                     "headline": "My messages to you aren't getting through.",
                     "means": "Telegram is refusing them, so you may miss news from me.",
                     "back": "My messages are getting through to you again."},
}
BACKUP_WORDS = {"headline": "My latest backup did not finish.",
                "means": "Nothing is lost, but the newest copy of your data is older than it should be."}
_FALLBACK = {"noun": "one of my checks", "tech": "check", "outage": False, "todo": TODO_WAIT,
             "headline": "Something in my background checks is failing.", "means": "It may affect how well I work.",
             "back": "That background check is fine again."}
_SHARED: Optional[dict] = None


def _shared_words() -> dict:
    """The spine services' words, read once from the crash-loop watch (it is loaded anyway, for deliver()). A load
    failure falls back to generic words: wording must never stop an alert."""
    global _SHARED
    if _SHARED is None:
        try:
            _SHARED = dict(_ops().PLAIN_WORDS)
        except Exception:  # noqa: BLE001
            _SHARED = {}
    return _SHARED


def words(c: Check) -> dict:
    base = c.name.split(" :")[0]
    if base in SPINE:
        w = _shared_words().get(base) or {**_FALLBACK, "noun": "one of my services",
                                          "headline": "One of my services isn't answering."}
        w = {"outage": True, "todo": TODO_SELF, **w, "tech": base}
    else:
        w = dict(CHECK_WORDS.get(c.name, _FALLBACK))
        if c.name == "failed units" and "backup" in c.code:
            w.update(BACKUP_WORDS)
    w["code"] = f"{w['tech']}: {c.code or 'failing'}"
    return w


def say_duration(seconds: float) -> str:
    """Plain English, deterministic: "a minute", "14 minutes", "3 hours", "2 days"."""
    m = int(max(0.0, seconds) / 60 + 0.5)
    if m <= 1:
        return "a minute"
    if m < 120:
        return f"{m} minutes"
    h = int(m / 60 + 0.5)
    return f"{h} hours" if h < 48 else f"{int(h / 24 + 0.5)} days"


def _cap(noun: str) -> str:
    return noun[:1].upper() + noun[1:]


def _down_line(c: Check, w: dict, since: float, now: float) -> str:
    if c.overrun:
        kind = WINDOW_KINDS.get(c.overrun["kind"], "maintenance")
        when = (f"The planned {kind} window ended {say_duration(now - c.overrun['until'])} ago, "
                f"but {w['noun']} has not come back.")
    elif w["outage"]:
        when = f"It stopped about {say_duration(now - since)} ago and has not come back by itself."
    else:
        when = f"I noticed it about {say_duration(now - since)} ago."
    todo = TODO_OVERRUN if c.overrun else w["todo"]
    return f"{w['headline']} {w['means']} {when} {todo} ({w['code']}{', window overran' if c.overrun else ''})"


def _flap_line(w: dict, incidents: list, now: float) -> str:
    done = [e - b for b, e in incidents if e]
    each = f", for about {say_duration(sum(done) / len(done))} each time" if done else ""
    return (f"{_cap(w['noun'])} keeps dropping out: {len(incidents)} times in the last "
            f"{say_duration(now - incidents[0][0])}{each}. It is out again now. When it is out: {w['means']} "
            f"Rather than message you every time, I'll only tell you if a drop lasts more than "
            f"{say_duration(FLAP_LONG_S)}, and once it has stayed steady for {say_duration(FLAP_WINDOW_S)}. "
            f"({w['tech']}: keeps dropping out)")


def _compose(downs: list, ups: list, others: list, now: float) -> list[str]:
    """ONE message's paragraphs for a run: the new problems (several at once become one paragraph), then what came
    back, then the dropping-out summaries."""
    out: list[str] = []
    if len(downs) == 1:
        out.append(_down_line(*downs[0], now))
    elif downs:
        first = min(since for _c, _w, since in downs)
        plain = (TODO_WAIT, TODO_SELF)
        rows = [f"- {w['headline']} {w['means']}" + ("" if w["todo"] in plain else f" {w['todo']}")
                for _c, w, _s in downs]
        rest = " about the rest" if any(w["todo"] not in plain for _c, w, _s in downs) else ""
        out.append("\n".join(["A few things went wrong at once:"] + rows + [
            f"It started about {say_duration(now - first)} ago. Nothing for you to do yet{rest}; "
            f"I'll tell you as each one comes back. ({'; '.join(w['code'] for _c, w, _s in downs)})"]))
    if len(ups) == 1:
        w, since = ups[0]
        tail = "" if since is None else (f" It was out for {say_duration(now - since)}." if w["outage"]
                                         else f" That lasted {say_duration(now - since)}.")
        out.append(w["back"] + tail)
    elif ups:
        out.append("\n".join(w["back"] if since is None else f"{w['back'][:-1]} after {say_duration(now - since)}."
                             for w, since in ups))
    return out + others


def transitions(checks: list[Check], state: dict, now: Optional[float] = None) -> tuple[list[str], dict]:
    """New state + this run's message paragraphs (``send`` makes them ONE message). A check alerts on its
    FAILS_TO_ALERT-th consecutive failure and once on recovery after having alerted (never a recovery for something
    not alerted). Checks absent this run (night window) keep their state; ``unknown`` ones are not looked at; ``skipped``
    ones (a planned window) keep an alerted state but a not-yet-alerted count restarts from zero."""
    now = time.time() if now is None else now
    new = dict(state)
    downs: list = []
    ups: list = []
    others: list[str] = []
    for c in checks:
        if c.unknown:
            continue
        s = dict(new.get(c.name, {}))
        s.setdefault("fails", 0)
        s.setdefault("alerted", False)
        incidents = [list(i) for i in s.get("incidents", []) if not i[1] or now - i[0] <= FLAP_WINDOW_S]
        if c.skipped:
            if s["fails"] < FAILS_TO_ALERT:
                s.update(fails=0, since=None)
        elif c.ok:
            for i in incidents:
                i[1] = i[1] or now
            if s["alerted"]:
                ups.append((words(c), s.get("since")))
            if s.get("flap") and not any(now - b < FLAP_WINDOW_S for b, _e in incidents):
                w = words(c)
                others.append(f"{_cap(w['noun'])} has stayed steady for {say_duration(FLAP_WINDOW_S)} now, so I "
                              f"think the dropouts are over. It dropped out {s.get('flap_n', 0)} times in all.")
                s.update(flap=False, flap_n=0)
                incidents = []
            s.update(fails=0, alerted=False, since=None)
        else:
            if not s["fails"] or s.get("since") is None:
                s["since"] = now
            s["fails"] += 1
            if s["fails"] >= FAILS_TO_ALERT:
                if not any(b == s["since"] for b, _e in incidents):
                    incidents.append([s["since"], None])
                    if s.get("flap"):
                        s["flap_n"] = s.get("flap_n", 0) + 1
                if s.get("flap"):
                    if not s["alerted"] and now - s["since"] >= FLAP_LONG_S:
                        w = words(c)
                        others.append(f"{w['headline']} This drop has lasted {say_duration(now - s['since'])}, "
                                      f"longer than the earlier ones. {w['means']} {w['todo']} ({w['code']})")
                        s["alerted"] = True
                elif not s["alerted"]:
                    if len(incidents) >= FLAP_COUNT:
                        others.append(_flap_line(words(c), incidents, now))
                        s.update(flap=True, flap_n=len(incidents))
                    else:
                        downs.append((c, words(c), s["since"]))
                        s["alerted"] = True
        s["incidents"] = incidents
        new[c.name] = s
    return _compose(downs, ups, others, now), new


def _ops():
    spec = importlib.util.spec_from_file_location("_clw", REPO_ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def alerting_check(broken: "Callable[[], Optional[dict]] | None" = None) -> Check:
    """Red while owner alerts fail on a credential or a missing setting (the outbox's broken record)."""
    try:
        b = (broken or _ops().alerting_broken)()
    except Exception as exc:  # noqa: BLE001
        return Check("owner alerts", True, f"unreadable ({type(exc).__name__})", unknown=True)
    if not b:
        return Check("owner alerts", True, "ok")
    since = datetime.fromtimestamp(float(b.get("since") or 0)).isoformat(timespec="minutes")
    return Check("owner alerts", False, f"BROKEN since {since}: Telegram {b.get('reason', '?')} failure, alerts held in the outbox",
                 code=f"{b.get('reason', '?')} failure")


def send(lines: list[str]) -> bool:
    """True when the lines were delivered (or there was nothing to send)."""
    if not lines:
        return True
    if os.environ.get("ZOE_HEALTH_NO_ALERT"):
        return True
    try:
        outcome, _reason = _ops().deliver("health", "\n\n".join(lines))
        return outcome in ("delivered", "uncertain", "queued")   # queued: the shared outbox retries it
    except Exception as exc:  # noqa: BLE001
        print(f"alert not delivered: {type(exc).__name__}", file=sys.stderr)
        return False


def undelivered(state_before: dict, state_after: dict) -> dict:
    """The state to persist when this run's alerts were NOT delivered: a DOWN that did not reach anyone stays
    pending (alerted=False) so the next run retries it, instead of being recorded as sent and never repeated. The same
    holds for a "keeps dropping out" summary (flap): undelivered, it is said again next run."""
    out = dict(state_after)
    for name, s in state_after.items():
        before = state_before.get(name, {})
        if s.get("alerted") and not before.get("alerted"):
            out[name] = {**out[name], "alerted": False}
        if s.get("flap") and not before.get("flap"):
            out[name] = {**out[name], "flap": False}
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)
    now = time.time()
    planned = read_planned_window(now)
    checks = probe(forget_ledger=fetch_forget_ledger, planned=planned) + [alerting_check()]
    stamp = datetime.now().isoformat(timespec="seconds")
    if NIGHT_MARKER.exists():
        print(f"[{stamp}] night window open: spine probes skipped")
    if planned[2]:
        print(f"[{stamp}] {planned[2]}")
    for c in checks:
        print(f"[{stamp}] {'?   ' if c.unknown else 'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}")
    if ns.dry_run:
        return 0 if all(c.ok for c in checks) else 1
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    lines, new = transitions(checks, state, now)
    for ln in lines:
        print(f"[{stamp}] ALERT {ln}")
    if not send(lines):          # deliver FIRST; persist "alerted" only for what actually went out
        new = undelivered(state, new)
        print(f"[{stamp}] alert NOT delivered - will retry next run", file=sys.stderr)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(new))
    os.replace(tmp, STATE)
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
