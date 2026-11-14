#!/usr/bin/env python3
"""Alert the operator when zoe-data (or another auto-restarting Zoe unit, OTHER_UNITS) is crash-looping.

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
import contextlib
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

STATE_PATH = os.path.expanduser("~/.zoe/crash_loop_watch.json")
TELEGRAM_ENV = os.path.expanduser("~/assistant/labs/flue-zoe-telegram-2x/.env")
UNIT = "zoe-data.service"
HEALTH_URL = "http://localhost:8000/health"
# 13s per cycle observed in the incident, so 5 restarts ~= a minute of looping.
# Deliberately above normal: a single restart (deploy, manual) must never alert.
DEFAULT_THRESHOLD = 5
HEARTBEAT_S = 3600  # steady-state "healthy" line at most hourly (see check())
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

    `TELEGRAM_ALLOWED_USERS` is RETIRED — `labs/flue-zoe-telegram-2x/.env.example`
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


def _telegram(text: str, report=None) -> bool:
    """Best-effort send. Returns True if delivered. Failure lines go to ``report`` (a callable taking one line) when given,
    else to stderr; a caller that wants the reason passes ``report`` rather than redirecting the process-wide stderr."""
    return _telegram_result(text, report) == "delivered"


# Transport outcome classes (a CLOSED vocabulary: only these ever reach a log or the outbox).
#   delivered  - Telegram took it (any recipient).
#   uncertain  - the request went out and the reply never came (read timeout): it may have been delivered, so it is
#                never re-sent (at most once; the repo rule telegram_notify.py states).
#   auth       - Telegram refused the bot's credential (HTTP 401 / 403 / 404 - an unknown token is a 404): ALERTING IS
#                BROKEN until the token is fixed. 2026-10-28..11-03: nine kv-miss alerts died this way, unretried.
#   config     - no env file / token / recipient, or HTTP 400 (bad chat id): also broken, not transient.
#   server     - HTTP 429 / 5xx or a non-200 reply: transient.
#   network    - the request never got an answer from Telegram (DNS, connect, connect timeout): transient.
_PRIORITY = ("uncertain", "auth", "config", "server", "network")


def _http_class(code: int) -> str:
    if code in (401, 403, 404):
        return "auth"
    if code == 429 or code >= 500:
        return "server"
    return "config"


def _telegram_result(text: str, report=None) -> str:
    """One send to every recipient; the outcome class above (delivered = at least one recipient got it). Never raises,
    never reports the token."""
    return _telegram_all(text, report)[0]


def _telegram_all(text: str, report=None) -> "tuple[str, int, int]":
    """Send to EVERY recipient (never stop at the first success: the others would never hear). Returns
    (outcome class, recipients delivered, recipients failed). delivered = at least one succeeded; a failed recipient
    beside a delivered one is a PARTIAL delivery, reported by count (the alert is not re-sent: the ones who have it
    would get it twice). No recipient id ever leaves this function."""
    if report is None:
        def report(line: str) -> None:
            print(line, file=sys.stderr)
    try:
        with open(TELEGRAM_ENV) as fh:
            env = dict(re.findall(r"^(\w+)=(.*)$", fh.read(), re.M))
    except Exception as exc:
        report(f"crash-watch: cannot read telegram env ({exc}); alert NOT delivered")
        return "config", 0, 0
    # The BOT FILE's token wins; an environment token is only a fallback for a file that has none. The bot file is the one
    # token that is provably live (the bot itself runs on it). The environment is whatever the calling process happened to
    # load: from 2026-10-27 zoe-data's unit loaded a retired agent's env file whose revoked token overrode this file's valid
    # one, and every in-process ops alert failed with HTTP 401 for a week.
    # Recipients still honour a NON-EMPTY environment override, so an operator can point alerts elsewhere. An EMPTY variable
    # is never an override: zoe-data's .env carries blank keys and loads them into os.environ.
    file_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    env_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    env = {**env, **{k: v for k, v in os.environ.items() if k == "ZOE_ALERT_TELEGRAM_CHAT_IDS" and v.strip()}}
    token = file_token or env_token
    users = _recipients(env)
    if not token or not users:
        report("crash-watch: NO ALERT RECIPIENT CONFIGURED — a crash loop would go "
               "unreported. Set ZOE_ALERT_TELEGRAM_CHAT_IDS (comma-separated chat "
               f"ids) in {TELEGRAM_ENV} or the environment.")
        return "config", 0, 0
    fails: list[str] = []
    ok = 0
    if len(users) > MAX_RECIPIENTS:
        report(f"crash-watch: {len(users)} alert recipients configured; only the first {MAX_RECIPIENTS} are sent to")
        users = users[:MAX_RECIPIENTS]
    for chat_id in users:
        try:
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data)
            with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
                if resp.status == 200:
                    ok += 1
                else:
                    fails.append("server")
        except urllib.error.HTTPError as exc:      # before URLError: it is a subclass
            report(f"crash-watch: telegram send failed: HTTP Error {exc.code}")
            fails.append(_http_class(exc.code))
        except urllib.error.URLError as exc:
            report(f"crash-watch: telegram send failed: {exc}")
            # urlopen WRAPS a socket timeout in URLError. A timeout may have happened after the request went out, so it
            # may have been delivered: "uncertain" (never re-sent), not "network" (retried, which could send it twice).
            # Anything else (DNS, refused, unreachable) never reached Telegram.
            fails.append("uncertain" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "network")
        except (TimeoutError, socket.timeout) as exc:   # sent, reply lost: may have been delivered
            report(f"crash-watch: telegram send failed: {exc}")
            fails.append("uncertain")
        except Exception as exc:
            report(f"crash-watch: telegram send failed: {exc}")
            fails.append("network")
    if ok:
        return "delivered", ok, len(fails)
    return (min(fails, key=_PRIORITY.index) if fails else "network"), 0, len(fails)


# ── the shared owner-alert delivery helper ────────────────────────────────────────────────────────────────────────
# EVERY owner alert goes through deliver() (scripts/AGENTS.md). Why: 2026-10-28..11-03 nine alerts were raised, failed
# with HTTP 401 and were simply lost - nothing retried them and nothing said alerting itself was broken.
#
# Shape of every send (deliver() and drain() alike), so the lock is NEVER held across the network:
#   (a) a short locked write: the alert is upserted into the durable OUTBOX and the due entries are CLAIMED (a lease,
#       CLAIM_LEASE_S), the file and its directory fsynced;
#   (b) the lock is released and the claimed entries are sent (bounded retries with backoff for a transient failure,
#       an explicit 15 s timeout per request);
#   (c) a second short locked write RE-READS the outbox and records each outcome on the entry it claimed (a concurrent
#       sender's changes are kept). Delivered (or uncertain) = removed; otherwise it stays, backed off.
# A process killed during (b) leaves its claimed entries on disk; when the lease expires the next drain() (the crash-loop
# watcher's 2-minute tick) sends them. An alert is retried until delivered or GIVE_UP_S old (then a loud gave_up marker;
# never a silent drop). A raise whose code is pending SUPERSEDES that entry's text (one entry per code).
#
# The outbox is ONE small JSON file, not a table: every sender (zoe-data's thread, three systemd timers) can reach it
# with no database, while zoe-data or Postgres are the very things that may be down. It holds per alert only a fixed
# code, timestamps, counts and the message text; a caller must pass text with NO household content (ops facts only).
# It also keeps running totals (the durable raised-vs-delivered record), the open ``broken`` record while delivery
# fails on a credential or a missing setting, and ``spells`` (the last SPELLS_KEPT broken spells, healed ones included)
# that zoe-data turns into the owner's panel notification - the second channel, which does not use the Telegram
# credential (notifications_store.sync_alerting_broken).
#
# Marker, one line per outcome, counts only:
#   ALERT_DELIVERY event=<raised|delivered|uncertain|queued|gave_up|broken|healed|failed> code=<code> tries=<n> reason=<class> pending=<n>
OUTBOX_PATH = os.path.expanduser("~/.zoe/alert-outbox.json")
OUTBOX_MAX = 50                  # distinct pending codes; the oldest beyond this gives up (loudly)
MAX_TEXT = 1000
GIVE_UP_S = 7 * 86400
RETRY_BASE_S = 120               # first later retry after 2 min, doubling, capped at an hour
RETRY_MAX_S = 3600
INLINE_BACKOFF_S = (2.0, 6.0)    # the new alert's own retries: 3 attempts in ~8 s, transient failures only
DRAIN_LIMIT = 10
SEND_TIMEOUT_S = 15              # per request
MAX_RECIPIENTS = 5               # a hard cap, so the longest possible send is bounded and the lease below can cover it
INLINE_ATTEMPTS = 3
# The lease must outlive the LONGEST possible send of everything one sender claims, or a second sender would take an
# entry over mid-send and deliver it twice. Worst case for one sender: the freshly raised entry gets INLINE_ATTEMPTS
# rounds (each MAX_RECIPIENTS x SEND_TIMEOUT_S) plus the inline backoff, and each of the other DRAIN_LIMIT - 1 claimed
# entries gets one round; plus a margin. A claim older than this was a dead sender (a live one releases its claims in
# step (c) straight away, so the lease only delays a retry after a process was killed mid-send).
_ROUND_S = MAX_RECIPIENTS * SEND_TIMEOUT_S
CLAIM_LEASE_S = int(_ROUND_S * INLINE_ATTEMPTS + sum(INLINE_BACKOFF_S) + (DRAIN_LIMIT - 1) * _ROUND_S + 120)
SPELLS_KEPT = 20
BROKEN = ("auth", "config")
TRANSIENT = ("network", "server")
#: Alert codes are a CLOSED list (they reach logs and the outbox): the fixed ones below, plus the per-unit forms built by
#: _unit_code for the units in OTHER_UNITS. Anything else - a typo, or text that is not a code at all - becomes "other".
FIXED_CODES = frozenset({"crash_loop", "recovered", "unit_crash_loop", "unit_recovered", "health", "backup",
                         "kv_miss_first", "other"})


def _safe_code(code) -> str:
    if not isinstance(code, str):
        return "other"
    if code in FIXED_CODES:
        return code
    for prefix in ("unit_crash_loop.", "unit_recovered."):
        if code.startswith(prefix) and (code[len(prefix):] + ".service") in OTHER_UNITS:
            return code
    return "other"


def _stderr_log(line: str) -> None:
    print(line, file=sys.stderr)


def _ops_transport(text: str):
    """The default transport: the ops Telegram path, no report lines (the class is the reason). Returns the outcome
    class, or (class, delivered, failed) so a PARTIAL delivery is counted."""
    return _telegram_all(text, report=lambda _line: None)


def _live_outbox_under_test() -> bool:
    """A test that did not point OUTBOX_PATH at a temp file must never touch the live outbox or the network."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and \
        os.path.realpath(OUTBOX_PATH) == os.path.realpath(os.path.expanduser("~/.zoe/alert-outbox.json"))


def _load_outbox() -> dict:
    try:
        with open(OUTBOX_PATH) as fh:
            box = json.load(fh)
        if not isinstance(box, dict):
            box = {}
    except FileNotFoundError:
        box = {}
    except Exception:
        box = {"corrupt": True}
    box.setdefault("entries", [])
    box.setdefault("totals", {})
    return box


def _write_outbox(box: dict) -> None:
    """Atomic, durable write: temp file 0600 (fchmod, whatever the umask), fsync, rename, fsync the directory."""
    box.pop("corrupt", None)
    d = os.path.dirname(OUTBOX_PATH)
    tmp = OUTBOX_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps(box).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, OUTBOX_PATH)
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextlib.contextmanager
def _outbox_locked():
    """A SHORT read-modify-write under an exclusive lock (several processes send). Never send inside it. Raises OSError
    when the outbox cannot be stored."""
    os.makedirs(os.path.dirname(OUTBOX_PATH), exist_ok=True)
    with open(OUTBOX_PATH + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        box = _load_outbox()
        yield box
        _write_outbox(box)


def _marker(log, event: str, code: str, tries: int = 0, reason: str = "-", pending: int = 0) -> None:
    try:
        log(f"ALERT_DELIVERY event={event} code={code} tries={int(tries)} reason={reason} pending={int(pending)}")
    except Exception:
        pass


def _bump(box: dict, key: str) -> None:
    box["totals"][key] = int(box["totals"].get(key) or 0) + 1


def _backoff(tries: int) -> float:
    return float(min(RETRY_BASE_S * (2 ** max(0, tries - 1)), RETRY_MAX_S))


#: recipients that did NOT get the alert on the last _attempt while at least one did (a partial delivery); 0 otherwise.
_last_partial = threading.local()


def _attempt(transport, text: str, attempts: int, sleep) -> tuple[str, int]:
    """Up to ``attempts`` sends; only a TRANSIENT class is retried. (outcome class, attempts used). An Exception from
    the transport is a network failure; a BaseException (the process being killed) propagates: the claim stays on disk.
    A transport may return the class alone or (class, delivered, failed)."""
    res = "network"
    _last_partial.failed = 0
    for i in range(attempts):
        try:
            got = transport(text)
            if isinstance(got, tuple):
                res = str(got[0])
                _last_partial.failed = int(got[2]) if res == "delivered" and len(got) > 2 else 0
            else:
                res = str(got)
        except Exception:
            res = "network"
        if res not in TRANSIENT:
            return res, i + 1
        if i < attempts - 1:
            sleep(INLINE_BACKOFF_S[min(i, len(INLINE_BACKOFF_S) - 1)])
    return res, attempts


def _claimed(ent: dict, now: float) -> bool:
    c = ent.get("claim") or {}
    return bool(c) and float(c.get("until") or 0) > now


def _family(code: str) -> str:
    """Alerts that answer each other share a family and are sent in the order they were raised: the main loop alert and
    its recovery; each watched unit's loop alert and its recovery. Any other code is its own family."""
    code = str(code or "")
    if code in ("crash_loop", "recovered"):
        return "main"
    for p in ("unit_crash_loop", "unit_recovered"):
        if code == p or code.startswith(p + "."):
            return "unit" + code[len(p):]
    return code


def _claim_due(box: dict, now: float, inline: str = "") -> list[dict]:
    """(a) Claim unclaimed entries, OLDEST FIRST (so a "recovered" never overtakes the loop alert it answers), at most
    DRAIN_LIMIT: on a fresh raise (``inline`` = its code) every pending entry, since the path is being tried anyway; on a
    drain only the DUE ones (their backoff). Returns snapshots {code, text, rev, claim, inline} to send outside the lock."""
    due, held = [], set()
    for e in sorted(box["entries"], key=lambda e: float(e.get("first_ts") or 0)):
        fam = _family(e.get("code"))
        ready = (inline or float(e.get("next_ts") or 0) <= now) and not _claimed(e, now)
        if ready and fam not in held and len(due) < DRAIN_LIMIT:
            due.append(e)
        else:
            held.add(fam)       # an EARLIER alert of this family is still waiting (backoff, another sender's lease, the
            #                     limit): nothing later in the family may go first - a recovery never overtakes its outage
    out = []
    for e in due:
        cid = uuid.uuid4().hex
        e["claim"] = {"id": cid, "until": now + CLAIM_LEASE_S}
        out.append({"code": e["code"], "text": e.get("text") or "", "rev": int(e.get("rev") or 0), "claim": cid,
                    "inline": e["code"] == inline})
    return out


def _send_claimed(claims: list[dict], transport, sleep) -> list[tuple[dict, str, int]]:
    """(b) Send outside the lock. Stops at the first broken/transient failure: the same credential and network would
    fail the rest too (they are released unsent in (c))."""
    done = []
    for c in claims:
        res, used = _attempt(transport, c["text"], INLINE_ATTEMPTS if c["inline"] else 1, sleep)
        c["partial"] = int(getattr(_last_partial, "failed", 0) or 0)
        done.append((c, res, used))
        if res not in ("delivered", "uncertain"):
            break
    return done


def _record(box: dict, claims: list[dict], done: list[tuple[dict, str, int]], now: float, log) -> dict:
    """(c) Record outcomes on the RE-READ outbox. An entry whose text was superseded while it was being sent (its
    ``rev`` moved) is kept and due again if its OLD text was delivered: the new text has not been sent yet."""
    out: dict = {}
    entries = box["entries"]
    by_code = {e.get("code"): e for e in entries}
    sent_ids = {c["claim"] for c, _r, _u in done}
    for c in claims:                                  # claimed but not attempted: released, no attempt counted
        e = by_code.get(c["code"])
        if c["claim"] not in sent_ids and e and (e.get("claim") or {}).get("id") == c["claim"]:
            e.pop("claim", None)
    for c, res, used in done:
        code = c["code"]
        e = by_code.get(code)
        if e is None or (e.get("claim") or {}).get("id") != c["claim"]:
            continue                                  # another sender took over after our lease expired: theirs counts
        e.pop("claim", None)
        e["tries"] = int(e.get("tries") or 0) + used
        superseded = int(e.get("rev") or 0) != c["rev"]
        if res in ("delivered", "uncertain"):
            _bump(box, res)
            if superseded:
                e["next_ts"] = now
            else:
                entries.remove(e)
            _marker(log, res, code, e["tries"], "-", len(entries))
            if res == "delivered" and c.get("partial"):       # some recipients did not get it: counted, never silent
                _bump(box, "partial")
                box["totals"]["partial_recipients"] = int(box["totals"].get("partial_recipients") or 0) + int(c["partial"])
                _marker(log, "partial", code, e["tries"], f"failed_recipients_{int(c['partial'])}", len(entries))
            if res == "delivered" and box.get("broken"):
                br = box.pop("broken")
                for sp in box.get("spells") or []:
                    if sp.get("since") == br.get("since"):
                        sp["healed"] = now
                _marker(log, "healed", code, e["tries"], "-", len(entries))
            out[code] = (res, "-")
            continue
        if res in BROKEN:
            br = box.get("broken")
            if not br:
                br = {"since": now, "count": 0}
                spells = box.setdefault("spells", [])
                spells.append({"since": now, "reason": res, "healed": None})
                del spells[:-SPELLS_KEPT]
            box["broken"] = {**br, "last": now, "reason": res, "count": int(br.get("count") or 0) + 1}
            _bump(box, "broken")
            _marker(log, "broken", code, e["tries"], res, len(entries))
        if now - float(e.get("first_ts") or now) >= GIVE_UP_S:
            entries.remove(e)
            _bump(box, "gave_up")
            _marker(log, "gave_up", code, e["tries"], res, len(entries))
            out[code] = ("gave_up", res)
        else:
            e["next_ts"] = now + _backoff(e["tries"])
            _bump(box, "queued")
            _marker(log, "queued", code, e["tries"], res, len(entries))
            out[code] = ("queued", res)
    return out


def deliver(code: str, text: str, *, transport=None, now: "float | None" = None, sleep=time.sleep,
            log=None) -> tuple[str, str]:
    """Raise one owner alert. Returns (outcome, reason): outcome is delivered | uncertain | queued (the outbox holds it
    and will retry; also when another sender is sending this code right now) | gave_up | failed (it could not even be
    stored, and the direct send failed too). ``reason`` is a transport class from the closed vocabulary above, or "-".
    Never raises an Exception (a BaseException - the process being killed - propagates; the entry is already durable)."""
    log = log or _stderr_log
    transport = transport or _ops_transport
    code = _safe_code(code)
    text = str(text or "")[:MAX_TEXT]
    t = time.time() if now is None else float(now)
    _marker(log, "raised", code)
    if _live_outbox_under_test():
        return "failed", "test_guard"
    try:                                                  # (a) durable before any send
        with _outbox_locked() as box:
            if box.get("corrupt"):
                _marker(log, "corrupt", "outbox")
            _bump(box, "raised")
            entries = box["entries"]
            ent = next((e for e in entries if e.get("code") == code), None)
            if ent:
                ent.update(text=text, raised=int(ent.get("raised") or 0) + 1, last_raised_ts=t, next_ts=t,
                           rev=int(ent.get("rev") or 0) + 1)
            else:
                entries.append({"code": code, "text": text, "first_ts": t, "last_raised_ts": t,
                                "raised": 1, "tries": 0, "next_ts": t, "rev": 0})
            while len(entries) > OUTBOX_MAX:
                old = min(entries, key=lambda e: float(e.get("first_ts") or 0))
                entries.remove(old)
                _bump(box, "gave_up")
                _marker(log, "gave_up", old.get("code", "other"), old.get("tries", 0), "outbox_full", len(entries))
            claims = _claim_due(box, t, inline=code)
    except Exception:
        # The outbox cannot be stored. NEVER send unstored (the contract: durable before any send - an unrecorded send
        # can neither be retried nor counted). Loud failure; the caller keeps its own last resort (the crash watcher's
        # ``pending`` state re-raises on its next tick).
        _marker(log, "failed", code, 0, "outbox_write")
        return "failed", "outbox_write"
    done = _send_claimed(claims, transport, sleep)        # (b) no lock held
    try:                                                  # (c)
        with _outbox_locked() as box:
            res = _record(box, claims, done, t, log)
    except Exception:
        _marker(log, "failed", code, 0, "outbox_write")   # the claim lease expires and a drain re-sends it
        return "failed", "outbox_write"
    return res.get(code, ("queued", "inflight"))


def drain(*, transport=None, now: "float | None" = None, sleep=time.sleep, log=None) -> dict:
    """Retry whatever is due in the outbox (the crash-loop watcher calls this every tick), same (a)/(b)/(c) shape.
    {code: (outcome, reason)}."""
    log = log or _stderr_log
    transport = transport or _ops_transport
    if _live_outbox_under_test() or not os.path.exists(OUTBOX_PATH):
        return {}
    t = time.time() if now is None else float(now)
    try:
        with _outbox_locked() as box:
            claims = _claim_due(box, t)
        if not claims:
            return {}
        done = _send_claimed(claims, transport, sleep)
        with _outbox_locked() as box:
            return _record(box, claims, done, t, log)
    except Exception as exc:
        print(f"crash-watch: outbox drain failed: {type(exc).__name__}", file=sys.stderr)
        return {}


def outbox_status() -> dict:
    """Read-only counts: pending, totals, the broken record and the broken spells (no message text)."""
    if _live_outbox_under_test():
        return {"pending": 0, "pending_codes": [], "totals": {}, "broken": None, "spells": []}
    box = _load_outbox()
    return {"pending": len(box["entries"]), "pending_codes": sorted(e.get("code", "?") for e in box["entries"]),
            "totals": box["totals"], "broken": box.get("broken"), "spells": box.get("spells") or []}


def alerting_broken() -> "dict | None":
    """The open broken record ({since, last, reason, count}) while owner alerts fail on a credential or a missing
    setting; None once one alert is delivered again."""
    return outbox_status().get("broken")


def alerting_spells() -> list:
    """The recent broken spells [{since, reason, healed}] (healed = None while open): what zoe-data's tick turns into
    one owner notification per spell, kept (marked resolved) after the spell heals."""
    return list(outbox_status().get("spells") or [])


CODE_CRASH_LOOP = "crash_loop"
CODE_RECOVERED = "recovered"


def _unit_code(prefix: str, unit: str) -> str:
    """A fixed alert code per watched unit (``unit_crash_loop.<unit>`` / ``unit_recovered.<unit>``), so one unit's alert
    never supersedes another's; a unit outside OTHER_UNITS gets the bare prefix (never a free string)."""
    if unit in OTHER_UNITS:
        return f"{prefix}.{unit[:-len('.service')] if unit.endswith('.service') else unit}"
    return prefix


def _raise_alert(code: str, msg: str) -> bool:
    """True when the outbox has it (delivered, uncertain, held for retry, or given up loudly); False only when it could
    not even be stored and the direct send failed."""
    outcome, _reason = deliver(code, msg)
    return outcome != "failed"


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
        if not dry_run:
            # ONE retry path: the outbox. Handed to it = done here (cooldown starts); the outbox re-sends until delivered.
            handed = _raise_alert(CODE_CRASH_LOOP if looping else CODE_RECOVERED, msg)
            if looping:
                state["alerted"] = handed
                if handed:
                    state["last_alert_ts"] = now
            elif handed:
                state["alerted"] = False
                state.pop("last_alert_ts", None)
    else:
        status = "healthy" if healthy else f"unhealthy (active={active})"
        # A steady "healthy, +0" line every 2 minutes was 41,700 undated lines
        # (2.3 MB) in ~/.zoe-logs/crash-loop-watch.log (2026-10-04 log review).
        # Say something only when the picture CHANGES (status flip, restarts
        # moved) or as an hourly heartbeat proving the watcher is alive; every
        # line now carries a timestamp so the log can be read after the fact.
        steady = (healthy and delta == 0 and not dry_run
                  and state.get("last_status") == "healthy"
                  and now - state.get("last_log_ts", 0) < HEARTBEAT_S)
        if not steady:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
            print(f"{stamp} crash-watch: {status}, restarts={restarts} (+{delta} since last check)")
            if not dry_run:
                state["last_log_ts"] = now

    if not dry_run:
        state["restarts"] = restarts
        state["last_status"] = "healthy" if healthy else "unhealthy"
        state["checked_at"] = now
        _save_state(state)
    return 2 if looping else 0


# The other Restart= units whose silent loop is an outage (gap audit 2026-11-13: flue-zoe-telegram crash-looped 6,418
# times over ~9 h on 2026-11-07 with no alert, because only zoe-data was watched). They have no /health of their own
# here, so the signal is the restart counter alone; the threshold already ignores a single deploy or probe restart
# (a probe's stop/start is not an automatic restart and never moves NRestarts). The alert reaches Telegram's HTTP API
# directly with the bot's token, so it is delivered even while the bot PROCESS is the one looping.
OTHER_UNITS = ("flue-zoe-telegram.service", "flue-zoe-brain-2x.service", "llama-server.service",
               "kokoro-tts.service", "functiongemma-router.service", "github-runner.service")


UNIT_WINDOW_S = 900   # restarts are counted over this window, not one tick: llama-server's ~130 s fail-and-restart cycle
                      # is one restart per 2-min tick and would never reach the threshold within a single tick


def check_units(units, threshold: int, cooldown: int, dry_run: bool, window_s: float = UNIT_WINDOW_S) -> int:
    """Restart-counter loop check for ``units``; per-unit state under ``state["units"]``. A unit this host does not
    have (no readable NRestarts) is skipped silently. A loop is ``threshold`` automatic restarts within ``window_s``.
    Every alert goes through deliver(): an alert Telegram did not take is held in the OUTBOX and re-sent by every tick's
    drain() until delivered, even after the restart rate drops. ``pending`` here is only for an alert the outbox could
    not even store (or one left by the pre-outbox version), so there is ONE retry path. Returns 2 if any unit is
    looping, else 0."""
    state = _load_state()
    per = state.setdefault("units", {})
    now = time.time()
    any_loop = False
    for unit in units:
        raw = _systemd_prop(unit, "NRestarts")
        if not raw.isdigit():
            continue
        restarts = int(raw)
        s = per.setdefault(unit, {})
        prev = s.get("restarts")
        if not isinstance(prev, int) or restarts < prev:          # first sight, or a counter reset: not a loop
            s["hist"] = []
        delta = restarts - prev if isinstance(prev, int) and restarts >= prev else 0
        hist = [h for h in s.get("hist", []) if now - h[0] <= window_s] + ([[now, delta]] if delta else [])
        s["hist"] = hist
        recent = sum(h[1] for h in hist)
        active = _systemd_prop(unit, "ActiveState")
        settled = delta == 0 and active == "active"               # running, and no restart since the last tick
        looping = recent >= threshold and not settled
        any_loop = any_loop or looping
        msg = None
        if s.get("pending"):
            msg = s["pending"]                                    # one the outbox could not store (or a pre-outbox state)
        elif looping and now - s.get("last_alert_ts", 0) >= cooldown:
            msg = (f"🔴 {unit} is crash-looping: restarted {recent}x in the last {int(window_s // 60)} min "
                   f"(total {restarts}), ActiveState={active}.\nCheck: journalctl --user -u {unit}")
        elif s.get("alerted") and settled:
            msg = f"🟢 {unit} recovered (total restarts {restarts})."
        if msg:
            print(msg.replace("\n", " | "))
            if not dry_run:
                loop_msg = msg.startswith("🔴")
                handed = _raise_alert(_unit_code("unit_crash_loop" if loop_msg else "unit_recovered", unit), msg)
                if loop_msg:
                    if handed:                                    # the outbox owns the retry from here
                        s["alerted"] = True
                        s["last_alert_ts"] = now
                        s.pop("pending", None)
                    else:                                         # not even stored: the ONLY case this state retries
                        s["pending"] = msg
                elif handed:
                    s["alerted"] = False
                    s.pop("last_alert_ts", None)
        s["restarts"] = restarts
    if not dry_run:
        _save_state(state)
    return 2 if any_loop else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="restarts since last check that constitute a loop")
    ap.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_S)
    ap.add_argument("--dry-run", action="store_true",
                    help="evaluate and print, send nothing, persist nothing")
    ap.add_argument("--outbox-status", action="store_true",
                    help="print the owner-alert outbox counts (no message text) and exit; sends nothing")
    ap.add_argument("--drain-outbox", action="store_true",
                    help="retry what is due in the owner-alert outbox now, print the outcome counts, and exit")
    args = ap.parse_args(argv)
    if args.outbox_status:
        print(json.dumps(outbox_status(), sort_keys=True))
        return 0
    if args.drain_outbox:
        print(json.dumps({c: o for c, (o, _r) in drain().items()}, sort_keys=True))
        print(json.dumps(outbox_status(), sort_keys=True))
        return 0
    rc = 0
    try:
        rc = check(args.threshold, args.cooldown, args.dry_run)
    except Exception as exc:
        print(f"crash-watch FAILED: {exc}", file=sys.stderr)
        rc = 1
    try:                    # independent of zoe-data's result: one unit's failure never hides another's loop
        rc = max(rc, check_units(OTHER_UNITS, args.threshold, args.cooldown, args.dry_run))
    except Exception as exc:
        print(f"crash-watch FAILED (other units): {exc}", file=sys.stderr)
        rc = max(rc, 1)
    if not args.dry_run:    # every tick retries whatever owner alert (from any sender) is due in the outbox
        drain()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
