#!/usr/bin/env python3
"""The Zoe OS "synthetic day": a seeded, repeatable script of requests driven through the live chat API by DEMO users so each shadowed
verb reaches its judged-action count in hours, not weeks (docs/knowledge/zoe-os-synthetic-day.md, docs/knowledge/zoe-os-integration.md).

    python3 scripts/maintenance/os_synthetic_day.py run --dry-run                      # plan summary, sends nothing
    python3 scripts/maintenance/os_synthetic_day.py prepare --state S                  # mint demo ids, print the roster command
    python3 scripts/maintenance/os_synthetic_day.py run --state S --base-url URL --max-count 20 ...

The plan is built from TEMPLATES and an invented vocabulary only. Request text lives in memory for the send and is NEVER written: the
results file holds ids, verbs, classes, codes, durations. Credentials come from the environment (``ZOE_INTERNAL_TOKEN``) or a file the
operator names (``--token-file``) and are never printed. Stdlib only. One request at a time (the brain has one slot).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROLES = ("adult_a", "adult_b", "child", "guest")
CLASSES = ("kernel", "not_expressible", "refuse", "none")
VERBS = ("list_add", "list_remove", "reminder_create", "alarm_create", "none")
LIST_TYPES = ("shopping", "personal", "work", "bucket", "tasks")
DEMO_ID = re.compile(r"^demo_osd_[0-9a-f]{8}$")      # a shape the forget-synthetic route accepts (user_filters.FORGET_SYNTHETIC_RE)

# ---- the closed vocabulary: nothing a template emits may fall outside these tuples + the template words -------------------------------
ADJ = ("brindle", "quillon", "tarnip", "wimbly", "ostrel", "fennick", "drabble", "cobbin", "yarrel", "sundry", "pellow", "marrick",
       "tumble", "gorrin", "lissom", "hazzle", "dunnet", "waverly", "kessel", "jorrow", "nimble", "praxel", "bodkin", "ravel")
NOUN = ("crackers", "marmalade", "lanterns", "buttons", "noodles", "pickles", "candles", "ribbons", "biscuits", "twine", "paprika",
        "teacups", "sponges", "walnuts", "magnets", "cushions", "mittens", "pencils", "saucers", "kettles", "pebbles", "tonic", "oats", "jam")
LISTS = ("moss garden", "tin cabin", "ember trip", "copper shed", "velvet picnic", "plum errands")
NEVER_MADE = ("zephyr chores", "quartz outing", "marble hamper", "willow tasks", "amber errands")
CRITTER = ("glimmerfox", "dustwing", "puddlefin", "thistlemoth", "burrowlark", "cinderwren")
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TEMPLATES = {
    "add_plain": ("add {a} to my shopping list", "put {a} on the shopping list", "we need {a} so add it to the shopping list",
                  "add {a} to my to do list"),
    "add_multi": ("add {a}, {b} and {c} to the shopping list", "put {a} and {b} on the shopping list"),
    "add_named": ("put {a} on the {ln} list", "add {a} to the {ln} list"),
    "add_nolist": ("add {a} to my {nl} list", "put {a} on the {nl} list"),
    "add_dupword": ("add {a} and {a} to the shopping list",),
    "add_present": ("add {a} to my shopping list",),
    "add_ask": ("add it to the list", "put that on the list"),
    "lrm_plain": ("take {a} off the shopping list", "remove {a} from the shopping list", "cross {a} off my shopping list"),
    "lrm_named": ("take {a} off the {ln} list", "remove {a} from the {ln} list"),
    "lrm_multi": ("take {a} and {b} off the shopping list",),
    "lrm_absent": ("take {a} off the shopping list", "remove {a} from my shopping list"),
    "lrm_ask": ("take it off the list",),
    "rem_in_min": ("remind me in {n} minutes to check the {a}", "in {n} minutes remind me about the {a}"),
    "rem_in_hr": ("remind me in {h} hours to check the {a}", "remind me in {h} hours about the {a}"),
    "rem_tomorrow": ("remind me tomorrow at {t} to check the {a}", "tomorrow at {t} remind me about the {a}"),
    "rem_two_days": ("remind me in two days at {t} to check the {a}",),
    "rem_date": ("remind me on {month} {d} to check the {a}",),
    "rem_recur": ("remind me every {day} to check the {a}",),
    "rem_far": ("remind me in {w} days to check the {a}",),
    "rem_past": ("remind me yesterday at {t} to check the {a}",),
    "rem_ask": ("remind me", "set a reminder"),
    "alm_time": ("set an alarm for {t}", "wake me at {t}", "alarm for {t} please"),
    "alm_two": ("set alarms for {t} and {t2}",),
    "alm_morning": ("set an alarm for the morning",),
    "alm_repeat": ("set an alarm for {t} every {day}",),
    "alm_ask": ("set an alarm",),
    "talk": ("good morning", "thank you", "tell me something about the {c}", "what is {n} plus {h}", "how do you spell {a}", "goodbye"),
    "forget": ("forget that",),
}
NUMBER_TOKENS = frozenset({"am", "pm"})
ALLOWED = frozenset(w for t in TEMPLATES.values() for s in t for w in re.findall(r"[a-z']+", re.sub(r"\{\w+\}", " ", s))) \
    | frozenset(w for group in (ADJ, NOUN, LISTS, NEVER_MADE, CRITTER, MONTHS, DAYS) for g in group for w in g.split()) | NUMBER_TOKENS \
    | {"two"}
TOKEN = re.compile(r"[a-z']+|\d+")

# kind -> (verb, class, acts). acts = the shadow acts a request should produce [inferred: the list helper adds per item].
KINDS = {"add_plain": ("list_add", "kernel", 1), "add_multi": ("list_add", "kernel", 3), "add_named": ("list_add", "kernel", 1),
         "add_nolist": ("list_add", "kernel", 1), "add_dupword": ("list_add", "kernel", 1), "add_ask": ("list_add", "refuse", 0), "add_present": ("list_add", "kernel", 1),
         "lrm_plain": ("list_remove", "kernel", 1), "lrm_named": ("list_remove", "kernel", 1), "lrm_multi": ("list_remove", "kernel", 2),
         "lrm_absent": ("list_remove", "none", 0), "lrm_ask": ("list_remove", "refuse", 0),
         "rem_in_min": ("reminder_create", "kernel", 1), "rem_in_hr": ("reminder_create", "kernel", 1),
         "rem_tomorrow": ("reminder_create", "kernel", 1), "rem_two_days": ("reminder_create", "kernel", 1),
         "rem_date": ("reminder_create", "not_expressible", 1), "rem_recur": ("reminder_create", "not_expressible", 1),
         "rem_far": ("reminder_create", "not_expressible", 1), "rem_past": ("reminder_create", "refuse", 0),
         "rem_ask": ("reminder_create", "refuse", 0), "alm_time": ("alarm_create", "kernel", 1), "alm_two": ("alarm_create", "kernel", 2),
         "alm_morning": ("alarm_create", "not_expressible", 1), "alm_repeat": ("alarm_create", "not_expressible", 1),
         "alm_ask": ("alarm_create", "refuse", 0), "talk": ("none", "none", 0), "forget": ("none", "none", 0)}
# request counts per kind. Each verb's "kernel" kinds sum to >= 250 REQUESTS (a pair's setup add counts for list_add), so a verb reaches
# its 200 judged even if a few percent are not acted on. Guest requests are extra (all refuse).
QUOTA = {"add_plain": 100, "add_multi": 36, "add_named": 30, "add_nolist": 20, "add_dupword": 20, "add_present": 30, "add_ask": 8, "lrm_plain": 200, "lrm_named": 30,
         "lrm_multi": 20, "lrm_absent": 40, "lrm_ask": 6, "rem_in_min": 100, "rem_in_hr": 60, "rem_tomorrow": 70, "rem_two_days": 30,
         "rem_date": 20, "rem_recur": 20, "rem_far": 20, "rem_past": 15, "rem_ask": 10, "alm_time": 200, "alm_two": 40, "alm_morning": 20,
         "alm_repeat": 25, "alm_ask": 10}
GUEST_KINDS = ("add_plain", "lrm_plain", "rem_in_min", "alm_time")
GUEST_EACH = 8
ROLE_WEIGHTS = (("adult_a", 4), ("adult_b", 3), ("child", 2))


def _fill(rng: random.Random, kind: str, used: list, n_items: int = 3) -> tuple[str, dict]:
    tpl = rng.choice(TEMPLATES[kind])
    items = []
    while len(items) < n_items:
        it = f"{rng.choice(ADJ)} {rng.choice(NOUN)}"
        if it not in items and it not in used[-40:]:
            items.append(it)
    hh, mm = rng.randint(1, 12), rng.choice((0, 5, 10, 15, 20, 30, 40, 45, 50))
    fmt = lambda h, m: f"{h}:{m:02d} {rng.choice(('am', 'pm'))}"      # noqa: E731
    d = {"a": items[0], "b": items[1], "c": items[2], "n": rng.randint(2, 55), "h": rng.randint(2, 9), "d": rng.randint(2, 28),
         "w": rng.randint(9, 40), "ln": rng.choice(LISTS), "nl": rng.choice(NEVER_MADE), "month": rng.choice(MONTHS),
         "day": rng.choice(DAYS), "t": fmt(hh, mm), "t2": fmt(rng.randint(1, 12), rng.choice((0, 15, 30, 45))), "c": rng.choice(CRITTER)}
    used.extend(items)
    return tpl.format(**d), d


def build_plan(seed: int) -> list[dict]:
    """The deterministic plan for a seed: a list of request dicts (``i``, ``id``, ``kind``, ``verb``, ``cls``, ``role``, ``acts``,
    ``text``). Same seed, same plan. ``text`` is in memory only."""
    rng, used, units = random.Random(seed), [], []

    def req(kind, role, text):
        verb, cls, acts = KINDS[kind]
        return {"kind": kind, "verb": verb, "cls": "refuse" if role == "guest" and verb != "none" else cls, "role": role,
                "acts": 0 if role == "guest" else acts, "text": text}

    def role():
        return rng.choices([r for r, _ in ROLE_WEIGHTS], [w for _, w in ROLE_WEIGHTS])[0]

    setups = {"lrm_plain": "add_plain", "lrm_named": "add_named", "lrm_multi": "add_multi"}
    for kind, n in QUOTA.items():
        for _ in range(n):
            r = role()
            txt, d = _fill(rng, kind, used)
            if kind in setups:                       # a remove of a PRESENT item is preceded by the add that put it there
                units.append([req(setups[kind], r, TEMPLATES[setups[kind]][0].format(**d)), req(kind, r, txt)])
            elif kind == "add_present":              # the second add finds the item already on the list
                units.append([req(kind, r, txt), req(kind, r, txt)])
            else:
                units.append([req(kind, r, txt)])
    for kind in GUEST_KINDS:
        units += [[req(kind, "guest", _fill(rng, kind, used)[0])] for _ in range(GUEST_EACH)]
    rng.shuffle(units)
    plan, talk = [], [req("talk", r, _fill(rng, "talk", used)[0]) for r in ("adult_a", "adult_b", "child", "guest") * 100]
    for k, unit in enumerate(units):
        plan += unit
        if k % 3 == 2:
            plan.append(dict(talk[(k // 3) % len(talk)]))     # a copy: ids are assigned in place below
    for i, r in enumerate(plan):
        r["i"], r["id"] = i, f"r{i + 1:04d}"
    return plan


def plan_summary(plan: list[dict]) -> dict:
    c = Counter((r["verb"], r["cls"], r["role"]) for r in plan)
    acts = Counter()
    for r in plan:
        acts[(r["verb"], r["cls"])] += r["acts"]
    return {"requests": len(plan), "table": {f"{v}|{k}|{ro}": n for (v, k, ro), n in sorted(c.items())},
            "by_verb": dict(Counter(r["verb"] for r in plan)), "by_class": dict(Counter(r["cls"] for r in plan)),
            "by_role": dict(Counter(r["role"] for r in plan)),
            "acts": {f"{v}|{k}": n for (v, k), n in sorted(acts.items()) if n}}


def planned_acts(plan: list[dict], verb: str, cls: str) -> int:
    return sum(r["acts"] for r in plan if r["verb"] == verb and r["cls"] == cls)


# ---- guards -------------------------------------------------------------------------------------------------------------------------
def night_clear(cmd: str) -> bool:
    """``night_busy.sh test`` exits 0 = pause, 1 = may run. Anything else (script missing, crash) is NOT clear: fail closed."""
    try:
        return subprocess.run(cmd.split(), capture_output=True, timeout=30).returncode == 1
    except (OSError, subprocess.SubprocessError):
        return False


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def harness_lock_free(path: str = "/tmp/zoe-voice-harness.lock") -> bool:
    """True when nobody holds the voice harness lock (tried non-blocking and released at once; an unreadable lock is NOT free)."""
    import fcntl

    try:
        with open(path, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            fcntl.flock(fh, fcntl.LOCK_UN)
            return True
    except OSError:
        return False


def household_talking(log_path: str, minutes: float, loggers: tuple, now: float, tail_bytes: int = 262144) -> bool:
    """A real turn in the last ``minutes``: any line in the log tail whose LOGGER NAME is one of ``loggers`` and whose timestamp is that
    recent. Reads only the first three tokens of a line (timestamp, level, logger name): never a message body."""
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        head = line.split(None, 3)
        if len(head) < 3 or head[2].rstrip(":") not in loggers:
            continue
        try:
            ts = datetime.strptime(head[0], "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            continue
        if now - ts < minutes * 60:
            return True
        if ts < now - minutes * 60 - 3600:
            break
    return False


# ---- the HTTP side ------------------------------------------------------------------------------------------------------------------
class Api:
    def __init__(self, base: str, token: str, timeout: float = 240.0) -> None:
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout

    def call(self, method: str, path: str, user: str | None, body: dict | None = None) -> tuple[int, object]:
        headers = {"Content-Type": "application/json"}
        if user:                                    # a guest sends NO identity at all: unauthenticated loopback = guest scope
            headers.update({"X-Internal-Token": self.token, "X-Zoe-User-Id": user, "X-Zoe-Traffic-Class": "harness"})
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            return e.code, {}
        except TimeoutError:
            return -1, {}
        except (urllib.error.URLError, OSError, ValueError):
            return 0, {}

    def chat(self, user: str | None, session: str, text: str) -> tuple[str, int]:
        """(outcome code, ms). The reply is inspected only for emptiness and dropped."""
        t0 = time.monotonic()
        code, body = self.call("POST", "/api/chat/?stream=false", user, {"message": text, "session_id": session, "stream": False})
        ms = int((time.monotonic() - t0) * 1000)
        if code == 200:
            return ("ok" if isinstance(body, dict) and body.get("response") else "empty"), ms
        return {0: "net_error", -1: "timeout"}.get(code, f"http_{code}"), ms

    def owned(self, user: str) -> dict[str, list]:
        """What the demo user still owns, by kind: ``{"lists": [(type, id)], "alarms": [id], "reminders": [id]}``."""
        out: dict[str, list] = {"lists": [], "alarms": [], "reminders": []}
        for lt in LIST_TYPES:
            code, body = self.call("GET", f"/api/lists/{lt}", user)
            rows = body.get("lists", []) if code == 200 and isinstance(body, dict) else []
            out["lists"] += [(lt, x["id"]) for x in rows if isinstance(x, dict) and x.get("user_id") == user and "id" in x]
        code, body = self.call("GET", "/api/alarms", user)
        out["alarms"] = [a["id"] for a in (body.get("alarms", []) if code == 200 and isinstance(body, dict) else [])
                         if isinstance(a, dict) and "id" in a and user in (a.get("owner"), a.get("user_id"))]
        code, body = self.call("GET", "/api/reminders/", user)
        out["reminders"] = [x["id"] for x in (body.get("reminders", []) if code == 200 and isinstance(body, dict) else [])
                            if isinstance(x, dict) and "id" in x and x.get("user_id", user) == user]
        return out

    def erase(self, user: str) -> dict:
        """The assistant's own API only: delete what the demo user made, then the member forget (``forget-synthetic`` ->
        ``MemoryService.delete_user`` -> the kernel's whole-member forget), then verify: nothing owned, a second forget removes 0.
        Counts only."""
        own = self.owned(user)
        for lt, i in own["lists"]:
            self.call("DELETE", f"/api/lists/{lt}/{i}", user)
        for i in own["alarms"]:
            self.call("DELETE", f"/api/alarms/{i}", user)
        for i in own["reminders"]:
            self.call("DELETE", f"/api/reminders/{i}", user)
        code, body = self.call("POST", f"/api/memories/users/{user}/forget-synthetic", user, {})
        left = {k: len(v) for k, v in self.owned(user).items()}
        code2, body2 = self.call("POST", f"/api/memories/users/{user}/forget-synthetic", user, {})
        return {"forget_http": code, "removed": int((body or {}).get("removed", 0)) if isinstance(body, dict) else 0, "left": left,
                "second_http": code2, "second_removed": int((body2 or {}).get("removed", -1)) if isinstance(body2, dict) else -1}


# ---- state, results, the run --------------------------------------------------------------------------------------------------------
def read_token(path: str | None) -> str:
    """``ZOE_INTERNAL_TOKEN`` from the environment, else from the file the operator names (a bare token, or ``KEY=value`` lines)."""
    tok = os.environ.get("ZOE_INTERNAL_TOKEN", "")
    if tok or not path:
        return tok
    for raw in Path(path).read_text("utf-8").splitlines():
        raw = raw.strip().replace("export ", "", 1)
        if raw.startswith("ZOE_INTERNAL_TOKEN="):
            return raw.split("=", 1)[1].strip().strip("\"'")
        if raw and "=" not in raw and not raw.startswith("#"):
            return raw
    return ""


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, st: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, path)


def prepare(path: Path, seed: int) -> dict:
    st = load_state(path)
    if not st or st.get("erased"):
        st = {"run_id": secrets.token_hex(3), "seed": seed if not st else st["seed"], "next": st.get("next", 0) if st else 0,
              "users": {r: "demo_osd_" + secrets.token_hex(4) for r in ROLES if r != "guest"}, "erased": False,
              "marker_sent": False}
        save_state(path, st)
    return st


def roster_ids(path: str) -> set:
    try:
        return {str(p) for p in json.loads(Path(path).read_text("utf-8"))["people"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


class Out:
    def __init__(self, path: Path) -> None:
        self.path = path

    def add(self, rec: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")


def run(a: argparse.Namespace, clock=time.time, sleep=time.sleep, guards=None) -> int:
    plan = build_plan(a.seed)
    if a.dry_run:
        s = plan_summary(plan)
        print(json.dumps(s, indent=1) if a.json else _render_summary(s))
        print(f"\ndry run: would send up to {a.max_count or len(plan)} of {len(plan)} requests, one at a time, one every {a.pace}s, "
              f"batches of {a.batch} behind the guards, hard stop {a.hard_stop}s after start; then 'forget that' + erase 3 demo users.")
        return 0
    state_path, out = Path(a.state), Out(Path(a.out))
    st = prepare(state_path, a.seed)
    plan = build_plan(st["seed"])                                   # a resumed run keeps ITS seed, whatever --seed says now
    if st["erased"] or any(not DEMO_ID.match(u) for u in st["users"].values()):
        print("state has no live demo users: run `prepare` again", file=sys.stderr)
        return 2
    if not a.skip_roster_check and not set(st["users"].values()) <= roster_ids(a.roster_file):
        print("the demo ids are not all in the kernel roster; shadow would skip them. Run `prepare` and write the roster "
              "(see the operator page), or pass --skip-roster-check.", file=sys.stderr)
        return 2
    api = Api(a.base_url, read_token(a.token_file))
    if guards is None:
        guards = lambda: guard_reason(a, clock())                   # noqa: E731
    if not Path(a.out).exists():
        out.add({"type": "meta", "seed": st["seed"], "run_id": st["run_id"], "started": clock(), "requests": len(plan)})
    start, sent, stop, rc = clock(), 0, "done", 0
    try:
        i = st["next"]
        while i < len(plan):
            if a.max_count and sent >= a.max_count:
                stop = "max_count"
                break
            while (why := guards()):
                out.add({"type": "pause", "t": clock(), "why": why})
                if clock() - start >= a.hard_stop:
                    stop = "hard_stop"
                    break
                sleep(a.poll)
            if stop == "hard_stop":
                break
            for r in plan[i:i + a.batch]:
                if clock() - start >= a.hard_stop:
                    stop = "hard_stop"
                    break
                if a.max_count and sent >= a.max_count:
                    stop = "max_count"
                    break
                t0 = clock()
                user = st["users"].get(r["role"])
                code, ms = api.chat(user, f"osd-{st['run_id']}-{r['role']}", r["text"])
                out.add({"type": "req", "id": r["id"], "kind": r["kind"], "verb": r["verb"], "cls": r["cls"], "role": r["role"],
                         "acts": r["acts"], "outcome": code, "ms": ms, "t": t0})
                sent += 1
                i = r["i"] + 1
                st["next"] = i
                save_state(state_path, st)
                sleep(max(0.0, a.pace - (clock() - t0)))
            if stop != "done":
                break
    except BaseException as exc:                                    # noqa: BLE001 - cleanup below must still run
        stop, rc = "interrupted:" + type(exc).__name__, 1
    finally:
        if a.cleanup == "always":
            rc = max(rc, cleanup(api, st, state_path, out, clock, sleep, a.pace))
    print(f"sent={sent} next={st['next']}/{len(plan)} stop={stop} cleanup={a.cleanup} rc={rc}")
    return rc


def guard_reason(a: argparse.Namespace, now: float) -> str | None:
    if not night_clear(a.night_cmd):
        return "night_busy"
    if mem_available_mb() < a.mem_floor_mb:
        return "memory"
    if not harness_lock_free():
        return "voice_lock"
    if a.service_log and household_talking(a.service_log, a.talk_minutes, tuple(a.talk_loggers.split(",")), now):
        return "household_talking"
    return None


def cleanup(api: Api, st: dict, state_path: Path, out: Out, clock=time.time, sleep=time.sleep, pace: float = 0.0) -> int:
    """Always runs. A deliberate 'forget that' on one demo item, then every demo user erased and verified. Non-zero + what is left."""
    rc = 0
    adult = st["users"]["adult_a"]
    sess = f"osd-{st['run_id']}-fin"
    try:
        for kind, text in (() if st.get("marker_sent") else (("fin_add", "add brindle crackers to my shopping list"),)) + (("fin_forget", "forget that"),):
            code, ms = api.chat(adult, sess, text)
            out.add({"type": "req", "id": kind, "kind": kind, "verb": "none", "cls": "none", "role": "adult_a", "acts": 0, "outcome": code,
                     "ms": ms, "t": clock()})
            if kind == "fin_add":
                st["marker_sent"] = True
                sleep(pace)
    except BaseException:                                           # noqa: BLE001
        rc = 1
    for role, uid in st["users"].items():
        try:
            res = api.erase(uid)
        except BaseException as exc:                                # noqa: BLE001
            res = {"forget_http": 0, "removed": 0, "left": {"error": 1}, "second_http": 0, "second_removed": -1}
            print(f"cleanup error for {uid}: {type(exc).__name__}", file=sys.stderr)
        out.add({"type": "cleanup", "role": role, "t": clock(), **res})
        bad = res["forget_http"] != 200 or res["second_removed"] != 0 or any(res["left"].values())
        if bad:
            rc = 1
            print(f"cleanup INCOMPLETE {uid}: forget_http={res['forget_http']} left={res['left']} second_removed={res['second_removed']}",
                  file=sys.stderr)
    st["erased"] = rc == 0
    save_state(state_path, st)
    return rc


def _render_summary(s: dict) -> str:
    rows = [f"requests: {s['requests']}", "by verb: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_verb"].items())),
            "by class: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_class"].items())),
            "by role: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_role"].items())),
            "expected shadow acts (verb|class): " + ", ".join(f"{k}={v}" for k, v in s["acts"].items()), "", "verb|class|role = requests"]
    return "\n".join(rows + [f"  {k} = {v}" for k, v in s["table"].items()])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("cmd", choices=("run", "prepare", "cleanup"))
    p.add_argument("--seed", type=int, default=20261115)
    p.add_argument("--state", default="os-synthetic-day.state.json", help="checkpoint (0600): run id, demo ids, next request index")
    p.add_argument("--out", default="os-synthetic-day.results.jsonl")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--token-file", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--pace", type=float, default=5.0, help="seconds between request starts")
    p.add_argument("--hard-stop", type=float, default=6 * 3600, help="seconds after start at which the run stops")
    p.add_argument("--max-count", type=int, default=0)
    p.add_argument("--batch", type=int, default=5, help="requests between guard checks")
    p.add_argument("--poll", type=float, default=60.0)
    p.add_argument("--cleanup", choices=("always", "never"), default="always")
    p.add_argument("--night-cmd", default="bash " + str(Path(__file__).resolve().parents[2] / "scripts/landing/night_busy.sh") + " test")
    p.add_argument("--mem-floor-mb", type=int, default=1200)
    p.add_argument("--service-log", default=os.path.expanduser("~/.zoe-logs/zoe-data.app.log"))
    p.add_argument("--talk-minutes", type=float, default=10.0)
    p.add_argument("--talk-loggers", default="routers.voice_tts", help="comma list of logger names that mean a real household turn")
    p.add_argument("--roster-file", default=os.path.expanduser("~/.zoe/os-shadow/people.json"))
    p.add_argument("--skip-roster-check", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    if a.cmd == "prepare":
        st = prepare(Path(a.state), a.seed)
        print("demo ids (synthetic): " + " ".join(st["users"].values()))
        print("roster: python3 services/zoe-data/os_shadow.py --write-roster <the existing roster ids> " + " ".join(st["users"].values()))
        return 0
    if a.cmd == "cleanup":
        st = load_state(Path(a.state))
        if not st:
            print("no state", file=sys.stderr)
            return 2
        return cleanup(Api(a.base_url, read_token(a.token_file)), st, Path(a.state), Out(Path(a.out)))
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
