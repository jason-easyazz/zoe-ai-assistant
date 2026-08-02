#!/usr/bin/env python3
"""Alert the operator when zoe-data is crash-looping.

WHY THIS EXISTS (2026-07-31): zoe-data restarted 37+ times over ~8 minutes with
a corrupt vector index and nothing told anyone. The outage was discovered by a
human trying to talk to Zoe and finding her mute.

WHY POLLING AND NOT `OnFailure=`: zoe-data runs `Restart=always`, so a crash
loop never enters systemd's `failed` state — it just cycles forever, and
`OnFailure=` never fires. The alternative (adding StartLimitBurst so the loop
eventually trips to `failed`) would make systemd GIVE UP restarting Zoe, which
turns a self-healing transient into a permanent outage. So this polls instead:
zero behaviour change to the live unit.

WHY TELEGRAM: the alert must survive the thing it reports on. zoe-data's own
voice/announce path needs zoe-data to be UP, so it cannot report its own death.
flue-zoe-telegram is a separate process holding its own token.

Read-only with respect to Zoe: it inspects systemd + /health and sends a
message. It never restarts, edits, or repairs anything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

STATE_PATH = os.path.expanduser("~/.zoe/crash_loop_watch.json")
TELEGRAM_ENV = os.path.expanduser("~/assistant/labs/flue-zoe-telegram/.env")
UNIT = "zoe-data.service"
HEALTH_URL = "http://localhost:8000/health"
# 13s per cycle observed in the incident, so 5 restarts ~= a minute of looping.
# Deliberately above normal: a single restart (deploy, manual) must never alert.
DEFAULT_THRESHOLD = 5
DEFAULT_COOLDOWN_S = 1800  # don't re-alert more than twice an hour


def _systemd_prop(unit: str, prop: str) -> str:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "-p", prop, "--value"],
                           capture_output=True, text=True, timeout=15, env=env)
        return r.stdout.strip()
    except Exception:
        return ""


def _health_ok(url: str, timeout: float = 6.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_PATH)


def _recipients(env: dict) -> list[str]:
    """Chat ids to alert, newest setting first.

    `TELEGRAM_ALLOWED_USERS` is RETIRED — `labs/flue-zoe-telegram/.env.example`
    says so explicitly, and a host provisioned from that example has no such
    key. Reading only it (as this did) meant every freshly-provisioned host
    would find zero recipients and print "alert NOT delivered" forever while a
    crash loop ran unannounced (review: Codex). That is precisely the silent-
    failure class this watcher exists to end, so the dedicated setting comes
    first and the retired key is kept only as a fallback for hosts still
    carrying it.
    """
    for key in ("ZOE_ALERT_TELEGRAM_CHAT_IDS", "TELEGRAM_ALLOWED_USERS"):
        ids = [u.strip() for u in env.get(key, "").split(",") if u.strip()]
        if ids:
            return ids
    return []


def _telegram(text: str) -> bool:
    """Best-effort send. Returns True if delivered."""
    try:
        with open(TELEGRAM_ENV) as fh:
            env = dict(re.findall(r"^(\w+)=(.*)$", fh.read(), re.M))
    except Exception as exc:
        print(f"crash-watch: cannot read telegram env ({exc}); alert NOT delivered",
              file=sys.stderr)
        return False
    # Environment wins over the file, so an operator can point alerts somewhere
    # else without editing the bot's own env.
    env = {**env, **{k: v for k, v in os.environ.items()
                     if k in ("ZOE_ALERT_TELEGRAM_CHAT_IDS", "TELEGRAM_BOT_TOKEN")}}
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    users = _recipients(env)
    if not token or not users:
        print("crash-watch: NO ALERT RECIPIENT CONFIGURED — a crash loop would go "
              "unreported. Set ZOE_ALERT_TELEGRAM_CHAT_IDS (comma-separated chat "
              f"ids) in {TELEGRAM_ENV} or the environment.", file=sys.stderr)
        return False
    ok = False
    for chat_id in users:
        try:
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=15) as resp:
                ok = ok or resp.status == 200
        except Exception as exc:
            print(f"crash-watch: telegram send failed: {exc}", file=sys.stderr)
    return ok


def check(threshold: int, cooldown: int, dry_run: bool) -> int:
    restarts_raw = _systemd_prop(UNIT, "NRestarts")
    if not restarts_raw.isdigit():
        print(f"crash-watch: cannot read NRestarts for {UNIT} (got {restarts_raw!r})",
              file=sys.stderr)
        return 1
    restarts = int(restarts_raw)
    active = _systemd_prop(UNIT, "ActiveState")
    healthy = _health_ok(HEALTH_URL)
    now = time.time()

    state = _load_state()
    prev = state.get("restarts")
    delta = restarts - prev if isinstance(prev, int) and restarts >= prev else 0
    # A restart COUNTER RESET (unit stopped/reloaded) must not read as a crash loop.
    if isinstance(prev, int) and restarts < prev:
        delta = 0

    looping = delta >= threshold and not healthy
    recovered = state.get("alerted") and healthy

    msg = None
    if looping:
        last = state.get("last_alert_ts", 0)
        if now - last >= cooldown:
            msg = (f"🔴 Zoe is crash-looping.\n"
                   f"{UNIT} restarted {delta}x since the last check "
                   f"(total {restarts}), ActiveState={active}, /health not responding.\n"
                   f"Zoe cannot be talked to right now.\n"
                   f"Check: journalctl --user -u zoe-data / ~/.zoe-logs/zoe-data.stderr.log")
    elif recovered:
        msg = f"🟢 Zoe recovered — {UNIT} is healthy again (total restarts {restarts})."

    if msg:
        print(msg.replace("\n", " | "))
        delivered = True if dry_run else _telegram(msg)
        if not dry_run:
            state["alerted"] = bool(looping and delivered)
            if looping and delivered:
                state["last_alert_ts"] = now
            if recovered:
                state["alerted"] = False
                state.pop("last_alert_ts", None)
    else:
        status = "healthy" if healthy else f"unhealthy (active={active})"
        print(f"crash-watch: {status}, restarts={restarts} (+{delta} since last check)")

    if not dry_run:
        state["restarts"] = restarts
        state["checked_at"] = now
        _save_state(state)
    return 2 if looping else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="restarts since last check that constitute a loop")
    ap.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_S)
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate and print, send nothing, persist nothing")
    args = ap.parse_args(argv)
    try:
        return check(args.threshold, args.cooldown, args.dry_run)
    except Exception as exc:
        print(f"crash-watch FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
