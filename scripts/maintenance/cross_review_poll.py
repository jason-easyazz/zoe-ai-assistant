#!/usr/bin/env python3
"""Poller + report extractor for cross_review.sh.

Split out of the shell wrapper on 2026-08-03 after a live incident on PR #1614:
the inline `json.load(sys.stdin)` one-liners parsed curl bodies with no check on
HTTP status, emptiness, or content-type. Under `set -euo pipefail` those never
hard-crashed the wrapper -- they degraded to the sentinel `poll-fail`, which the
loop treated as NONTERMINAL and NON-ALARMING. A session that had vanished
(404 `not_found`, which `curl -sf` renders as an empty body) therefore spun
silently for the full 2400s budget before emitting a generic timeout ALARM that
named neither the real fault nor the remedy. The review was lost and nobody was
told. Doctrine: a review lane fails LOUDLY.

What this module adds per failure mode:

  empty body / non-JSON / HTML error page / 5xx / connection refused
      -> TRANSIENT. Bounded consecutive retries with exponential backoff,
         declaring poll-lost after at most 121s at the wrapper's defaults while
         still riding out a ~60s server restart (both pinned by tests; see
         poll() for the arithmetic). On exhaustion: one terminal ALARM line
         naming the session, exit 4.
  404 `not_found` (session gone / never registered)
      -> GONE. Its own short bounded confirmation budget, then exit 4 (poll) or
         exit 3 (registration guard). Never silently absorbed as a blip.
  valid JSON with no `status` key
      -> TRANSIENT (a schema surprise is not a terminal verdict).
  hard overall timeout while still running/waiting
      -> exit 5.
  never reaches running/waiting within the grace window
      -> exit 6.

Every non-zero exit prints EXACTLY ONE terminal line on stderr containing the
session id and the literal string "re-dispatch required", so the alarm class is
greppable and unambiguous.

The wrapper collapses all of these to its long-standing public `exit 2` (alarm);
the distinct codes here are diagnostic granularity for humans and tests, not a
change to cross_review.sh's contract.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Exit codes. 0/1 keep their conventional meaning; the rest are diagnostic and
# are all mapped to the wrapper's `exit 2` alarm.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NEVER_REGISTERED = 3  # dispatch race: the session never became readable
EXIT_POLL_LOST = 4  # transport/parse retries exhausted, or session vanished
EXIT_TIMEOUT = 5  # hard overall budget exceeded while still non-terminal
EXIT_NEVER_RUNNING = 6  # kick died silently: never reached running/waiting
EXIT_DISPATCH_FAILED = 7  # the session-create response carried no usable id

# Non-terminal Omnigent session states. `waiting` means "awaiting external work"
# -- it both proves the run started and must keep the loop polling (Greptile P1
# on #1578); treating it as terminal ends reviews early.
NONTERMINAL = ("running", "waiting")

# Fetch outcome kinds.
OK = "ok"
TRANSIENT = "transient"
GONE = "gone"


def _http_get(url: str, timeout: float):
    """Return (http_status, content_type, body_bytes).

    The single seam every test monkeypatches. Raises nothing for HTTP error
    statuses -- an error response still has a status, a content type and a body,
    and all three are evidence. Only genuine transport faults raise.
    """
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (resp.headers.get("Content-Type") or ""), resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        body = b""
        try:
            body = exc.read()
        except Exception:  # pragma: no cover - defensive
            pass
        return exc.code, (exc.headers.get("Content-Type") if exc.headers else "") or "", body


def fetch_session(server: str, sid: str, timeout: float):
    """Fetch one session document. Returns (kind, doc_or_None, detail).

    Checks, in order: transport, HTTP status, emptiness, content-type, parse.
    Every check that fails classifies rather than raising -- the caller decides
    whether the failure is worth retrying.
    """
    url = f"{server.rstrip('/')}/v1/sessions/{sid}"
    try:
        status, ctype, body = _http_get(url, timeout)
    except urllib.error.URLError as exc:
        return TRANSIENT, None, f"connection error: {exc.reason}"
    except OSError as exc:
        return TRANSIENT, None, f"connection error: {exc}"

    if status == 404:
        return GONE, None, "HTTP 404 not_found"
    if status == 410:
        return GONE, None, "HTTP 410 gone"
    if status >= 500:
        return TRANSIENT, None, f"HTTP {status}"
    if status >= 400:
        return TRANSIENT, None, f"HTTP {status}"

    if not body or not body.strip():
        # The exact 2026-08-03 shape: a 200 with nothing in it. `json.load` on
        # this is what raised "Expecting value: line 1 column 1 (char 0)".
        return TRANSIENT, None, "empty response body"

    ctype_l = (ctype or "").lower()
    if "html" in ctype_l or body.lstrip()[:1] in (b"<",):
        head = body.strip()[:80].decode("utf-8", "replace")
        return TRANSIENT, None, f"non-JSON body (content-type: {ctype or 'none'}): {head!r}"

    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        head = body.strip()[:80].decode("utf-8", "replace")
        return TRANSIENT, None, f"JSON parse failed ({exc}): {head!r}"

    if not isinstance(doc, dict):
        return TRANSIENT, None, f"JSON payload is {type(doc).__name__}, expected object"

    # An error envelope served with 200 (seen from proxies) is still not_found.
    err = doc.get("error")
    if isinstance(err, dict) and err.get("code") == "not_found":
        return GONE, None, "not_found error envelope"

    return OK, doc, ""


def has_assistant_message(doc) -> bool:
    """True if the session carries at least one assistant message item.

    The run may start AND finish between two polls; an assistant reply is proof
    of a live run, not a dead kick (Codex P2, #1578).
    """
    if not isinstance(doc, dict):
        return False
    for it in doc.get("items") or []:
        if not isinstance(it, dict) or it.get("type") != "message":
            continue
        data = it.get("data") if isinstance(it.get("data"), dict) else {}
        if (data.get("role") or it.get("role")) == "assistant":
            return True
    return False


def _backoff(attempt: int, cap: float) -> float:
    return min(2.0**attempt, cap)


def _alarm(msg: str) -> None:
    """Print exactly one terminal alarm line."""
    print(msg, file=sys.stderr)


def await_registration(server, sid, budget_s, interval_s, http_timeout, sleep=time.sleep,
                       now=time.monotonic):
    """Bounded short wait for the session to become readable after creation.

    The dispatch-race guard. Evidence the race is real: the 2026-08-03 run's
    session was `not_found` afterwards, so "POST returned an id" is not proof
    that a subsequent GET will resolve it. Returns an exit code.
    """
    start = now()
    attempt = 0
    last = "no attempt made"
    while True:
        kind, _doc, detail = fetch_session(server, sid, http_timeout)
        if kind == OK:
            return EXIT_OK
        last = detail
        elapsed = now() - start
        if elapsed >= budget_s:
            _alarm(
                f"ALARM: session {sid} never registered on {server} within "
                f"{budget_s:.0f}s ({attempt + 1} attempts, last: {last}) — the "
                "dispatch was lost before the review started; re-dispatch required"
            )
            return EXIT_NEVER_REGISTERED
        sleep(min(_backoff(attempt, interval_s), max(0.0, budget_s - elapsed)))
        attempt += 1


def poll(server, sid, timeout_s, interval_s, running_grace_s, max_transient, max_gone,
         http_timeout, sleep=time.sleep, now=time.monotonic):
    """Long poll to a terminal session status.

    On success prints the terminal status on stdout and returns EXIT_OK. Every
    failure path prints one terminal alarm line and returns a distinct code.

    EXACTLY ONE sleep per iteration -- `next_sleep` carries the interval on the
    healthy path and the backoff on a retry. Sleeping the full interval AND the
    backoff (the shape this started as) made a mid-poll dead endpoint take
    (30+1)+(30+2)+(30+4)+(30+8)+(30+16)+30 = 211s to declare poll-lost at
    production defaults, not the 61s the backoff schedule implies (codex
    cross-review, #1618).

    Worst-case time to declare poll-lost, at the defaults the wrapper uses
    (interval 30, max_transient 8, cap = interval):

        30 (first interval, before the first poll)
      + 1 + 2 + 4 + 8 + 16 + 30 + 30   (backoffs before the 8th failed fetch)
      = 121s

    `max_transient` is 8 rather than 6 precisely so that OUTAGE TOLERANCE
    survives collapsing the double sleep: once the padding interval is gone,
    the retry budget is spent purely in backoff, so a ~60s server restart would
    exhaust a 6-retry budget at 31s. Failed fetches land at t=0,1,3,7,15,31,61,91
    from the first failure, so a 60s outage accrues 6 transients and recovers.
    Detection speed and restart tolerance trade directly against each other
    here; both numbers are pinned by tests.
    """
    start = now()
    saw_running = False
    transient_run = 0
    gone_run = 0
    status = "?"
    next_sleep = interval_s

    while True:
        elapsed = now() - start
        remaining = timeout_s - elapsed
        if remaining <= 0:
            _alarm(
                f"ALARM: review still '{status}' after {timeout_s:.0f}s — session {sid} "
                "exceeded its poll budget; the worker is being stopped and "
                "re-dispatch required"
            )
            return EXIT_TIMEOUT

        sleep(min(next_sleep, remaining))
        next_sleep = interval_s  # healthy default; a retry branch overrides it

        kind, doc, detail = fetch_session(server, sid, http_timeout)

        if kind == GONE:
            gone_run += 1
            if gone_run >= max_gone:
                _alarm(
                    f"ALARM: session {sid} disappeared mid-poll ({detail}, confirmed "
                    f"{gone_run}x) — the review was lost and produced no report; "
                    "re-dispatch required"
                )
                return EXIT_POLL_LOST
            next_sleep = _backoff(gone_run - 1, interval_s)
            continue

        if kind == TRANSIENT:
            transient_run += 1
            if transient_run >= max_transient:
                _alarm(
                    f"ALARM: session {sid} unreadable — {transient_run} consecutive "
                    f"failed polls against {server} (last: {detail}); the poller is "
                    "giving up with no report and re-dispatch required"
                )
                return EXIT_POLL_LOST
            next_sleep = _backoff(transient_run - 1, interval_s)
            continue

        gone_run = 0

        raw = doc.get("status")
        if not isinstance(raw, str) or not raw:
            # Valid JSON, no usable status. A schema surprise is not a verdict:
            # retry it rather than breaking the loop on a bogus terminal value.
            transient_run += 1
            if transient_run >= max_transient:
                _alarm(
                    f"ALARM: session {sid} returned {transient_run} consecutive "
                    "responses with no 'status' field — payload shape changed, no "
                    "report retrieved; re-dispatch required"
                )
                return EXIT_POLL_LOST
            next_sleep = _backoff(transient_run - 1, interval_s)
            continue

        # Only a well-formed reply clears the retry budget. Resetting on any
        # 200 would make the no-status branch below unable to ever exhaust it.
        transient_run = 0
        status = raw
        if status in NONTERMINAL:
            saw_running = True
            continue

        if not saw_running and has_assistant_message(doc):
            # Started and finished between two polls.
            saw_running = True

        if saw_running:
            print(status)
            return EXIT_OK

        # `docker exec -d` returns before the run registers, so an early non-
        # running status is a slow START, not completion (polly #2 on #1578).
        if now() - start > running_grace_s:
            _alarm(
                f"ALARM: session {sid} never reached 'running' within "
                f"{running_grace_s:.0f}s (status: {status}) — the kick died silently; "
                "re-dispatch required"
            )
            return EXIT_NEVER_RUNNING


def extract_report(path: str, sid: str) -> int:
    """Print the tail of the assistant conversation. Returns an exit code.

    Reports can span messages, so print the tail of the conversation rather than
    only the last message (polly non-blocking on #1578).
    """
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        _alarm(
            f"ALARM: could not read the session payload for {sid} ({exc}) — no report "
            "retrieved; re-dispatch required"
        )
        return EXIT_POLL_LOST

    if not body.strip():
        _alarm(
            f"ALARM: the session payload for {sid} was EMPTY — no report retrieved; "
            "re-dispatch required"
        )
        return EXIT_POLL_LOST

    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        head = body.strip()[:80].decode("utf-8", "replace")
        _alarm(
            f"ALARM: the session payload for {sid} was not JSON ({exc}): {head!r} — no "
            "report retrieved; re-dispatch required"
        )
        return EXIT_POLL_LOST

    if not isinstance(d, dict):
        _alarm(
            f"ALARM: the session payload for {sid} was {type(d).__name__}, expected an "
            "object — no report retrieved; re-dispatch required"
        )
        return EXIT_POLL_LOST

    texts = []
    for it in d.get("items") or []:
        if not isinstance(it, dict) or it.get("type") != "message":
            continue
        data = it.get("data") if isinstance(it.get("data"), dict) else {}
        # The inline kick prompt is a message item too (Codex P2, #1578).
        if (data.get("role") or it.get("role") or "") != "assistant":
            continue
        c = data.get("content") or it.get("content") or ""
        if isinstance(c, list):
            c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        c = str(c).strip()
        if c:  # tool_use-only assistant items reduce to "" (Codex, #1578)
            texts.append(c)

    if not texts:
        _alarm(
            f"ALARM: session {sid} ended idle with zero ASSISTANT messages — the kick "
            "died silently (check container auth: claude OAuth expires 2026-08-22); "
            "re-dispatch required"
        )
        return EXIT_POLL_LOST

    print("\n\n---\n\n".join(texts[-3:]))
    return EXIT_OK


def extract_session_id(path: str) -> int:
    """Print the session id from a POST /v1/sessions response body.

    The prime suspect for the 2026-08-03 incident: a create whose response body
    was empty or non-JSON. The old `curl … | python3 -c "…json.load(sys.stdin)['id']"`
    reported that as a generic "session create failed", but the POST may well
    have SUCCEEDED server-side -- so the session existed, unreferenced, and was
    `not_found` by the time anyone looked. Say so explicitly instead.
    """
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        _alarm(
            f"ALARM: could not read the session-create response ({exc}) — no session id "
            "captured; re-dispatch required"
        )
        return EXIT_DISPATCH_FAILED

    orphan = (
        "the POST may have SUCCEEDED server-side, leaving an orphaned session no "
        "one holds the id for"
    )
    if not body.strip():
        _alarm(
            f"ALARM: the session-create response body was EMPTY — {orphan}; "
            "re-dispatch required"
        )
        return EXIT_DISPATCH_FAILED
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        head = body.strip()[:80].decode("utf-8", "replace")
        _alarm(
            f"ALARM: the session-create response was not JSON ({exc}): {head!r} — "
            f"{orphan}; re-dispatch required"
        )
        return EXIT_DISPATCH_FAILED

    sid = doc.get("id") if isinstance(doc, dict) else None
    if not isinstance(sid, str) or not sid:
        _alarm(
            f"ALARM: the session-create response carried no 'id' field ({body[:80]!r}) "
            f"— {orphan}; re-dispatch required"
        )
        return EXIT_DISPATCH_FAILED

    print(sid)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("await-registration", help="bounded wait for a new session to be readable")
    reg.add_argument("--server", required=True)
    reg.add_argument("--session-id", required=True)
    reg.add_argument("--budget-s", type=float, default=60.0)
    reg.add_argument("--interval-s", type=float, default=5.0)
    reg.add_argument("--http-timeout-s", type=float, default=30.0)

    pol = sub.add_parser("poll", help="poll to a terminal status; prints the status on stdout")
    pol.add_argument("--server", required=True)
    pol.add_argument("--session-id", required=True)
    pol.add_argument("--timeout-s", type=float, required=True)
    pol.add_argument("--interval-s", type=float, default=30.0)
    pol.add_argument("--running-grace-s", type=float, default=300.0)
    # 8, not 6: with the double sleep gone the retry budget is spent purely in
    # backoff, and 6 would exhaust 31s into a server restart. See poll().
    pol.add_argument("--max-transient", type=int, default=8)
    pol.add_argument("--max-gone", type=int, default=3)
    pol.add_argument("--http-timeout-s", type=float, default=60.0)

    rep = sub.add_parser("report", help="extract the assistant report from a session JSON file")
    rep.add_argument("--session-id", required=True)
    rep.add_argument("--payload", required=True)

    sid = sub.add_parser("session-id", help="extract the id from a session-create response body")
    sid.add_argument("--payload", required=True)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "await-registration":
        return await_registration(
            args.server, args.session_id, args.budget_s, args.interval_s, args.http_timeout_s
        )
    if args.cmd == "poll":
        return poll(
            args.server,
            args.session_id,
            args.timeout_s,
            args.interval_s,
            args.running_grace_s,
            args.max_transient,
            args.max_gone,
            args.http_timeout_s,
        )
    if args.cmd == "report":
        return extract_report(args.payload, args.session_id)
    if args.cmd == "session-id":
        return extract_session_id(args.payload)
    return EXIT_USAGE  # pragma: no cover - argparse rejects unknown subcommands


if __name__ == "__main__":
    sys.exit(main())
