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

  empty body / non-JSON / HTML error page / 5xx / connection refused /
  truncated read (http.client.HTTPException)
      -> TRANSIENT. Bounded consecutive retries with exponential backoff,
         declaring poll-lost after 121s of SLEEPS at the wrapper's defaults
         (plus the requests' own duration, which is ~0 for these fast-failing
         shapes and up to http_timeout each for a stalling endpoint) while
         still riding out a ~60s server restart (all pinned by tests; see
         poll() for the arithmetic). On exhaustion: one terminal ALARM line
         naming the session, exit 4.
  404 `not_found` (session gone / never registered)
      -> GONE. Its own short bounded confirmation budget, then exit 4 (poll) or
         exit 3 (registration guard). Never silently absorbed as a blip.
  valid JSON with no `status` key
      -> TRANSIENT (a schema surprise is not a terminal verdict).
  report payload whose `items` is not a list (schema drift)
      -> classified and alarmed, exit 4. Never iterated into a TypeError.
  a transient fault while fetching the COMPLETED report
      -> the same bounded-retry treatment as any other response. The report GET
         used to be a one-shot `curl -sf` in the wrapper, so a blip in the
         second between "poll saw idle" and "read the transcript" threw away a
         review that had already been paid for. `report --server/--session-id`
         classifies and retries it; `--payload` still reads a file.
  a session teardown (`stop`) that never lands
      -> exit 8, one alarm line naming the reaper. The `stop` mode is the
         RUNNER-LEAK fix (2026-08-04): it POSTs Omnigent's own `stop_session`
         lifecycle event, which is the only teardown that reaches the
         host-launched `omnigent.runner._entry` process -- see stop_session().
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
import http.client
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
EXIT_STOP_FAILED = 8  # session teardown never landed -> a runner is leaking

# Non-terminal Omnigent session states. `waiting` means "awaiting external work"
# -- it both proves the run started and must keep the loop polling (Greptile P1
# on #1578); treating it as terminal ends reviews early.
NONTERMINAL = ("running", "waiting")

# Omnigent's own lifecycle verb for "end this session but keep the transcript"
# (`omnigent.server.routes._sessions.common._STOP_SESSION_TYPE`, verified in the
# installed 0.7.0 package). Posting it is what makes the server tell the HOST to
# stop the dedicated runner it launched -- see stop_session() below.
_STOP_SESSION_TYPE = "stop_session"

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


def _http_post(url: str, payload: dict, timeout: float):
    """Return (http_status, content_type, body_bytes) for a JSON POST.

    The second HTTP seam, and the only one that MUTATES. Same contract as
    `_http_get`: an error status is data, not an exception, so the caller
    classifies it; only genuine transport faults raise.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, (resp.headers.get("Content-Type") or ""), resp.read()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:  # pragma: no cover - defensive
            pass
        return exc.code, (exc.headers.get("Content-Type") if exc.headers else "") or "", body


def _classify_stop(status: int) -> tuple[str, str]:
    """Classify a stop_session POST response status. Returns (kind, detail)."""
    if 200 <= status < 300:
        return OK, f"HTTP {status}"
    if status in (404, 410):
        # Nothing left to stop. The session (and with it the host's runner
        # bookkeeping) is already gone, which is the state we were asking for.
        return GONE, f"HTTP {status} not_found"
    # 5xx is the interesting one and it is NOT terminal: the route raises when
    # the runner cannot be reached, and an Omnigent mid-restart 502s. Retry.
    # Other 4xx (403 not-owner, 400 unknown event type) are caller bugs that no
    # amount of retrying fixes, but they are rare enough that spending the
    # bounded budget on them is cheaper than a second classification rule that
    # could mis-file a transient as fatal.
    return TRANSIENT, f"HTTP {status}"


def stop_session(server, sid, budget_s, interval_s, http_timeout,
                 sleep=time.sleep, now=time.monotonic) -> int:
    """Ask Omnigent to terminate a session, and with it its dedicated runner.

    THIS is the teardown that reaps the leak. cross_review.sh's `stop_worker`
    signals container processes whose /proc cmdline CONTAINS the session id --
    which covers the `omnigent run` CLI kick and the harness subprocess
    (`omnigent.runtime.harnesses._runner --conversation-id <sid>`), and covers
    NOTHING ELSE. The host-launched runner is spawned by `omnigent host` as
    literally `python -m omnigent.runner._entry`: the session id travels in its
    ENVIRONMENT, never in its argv, so no cmdline scan can ever see it. Its
    parent is the live host daemon, so it is not an orphan either. Measured
    2026-08-04: 19 of them resident, one per dispatch, box down to ~0 MB free.

    `POST /v1/sessions/<id>/events {"type":"stop_session"}` is Omnigent's own
    lifecycle verb for this. The server reads host_id/runner_id from the
    OWNER-GATED session row and sends the host a `host.stop_runner` frame, so
    the teardown is attributed by construction -- it can only ever reach the
    runner bound to THIS session id, and no /proc heuristic is involved.
    Stopping is also non-destructive: stop is non-sticky in Omnigent, so a
    later message simply relaunches the session on its still-online host.

    Bounded and retried like every other response in this lane. Returns an exit
    code; the caller decides how loud that is (cross_review.sh treats a failed
    stop as a WARNING, never as a lost review -- the review is already paid for
    and the sweeper is the safety net).
    """
    url = f"{server.rstrip('/')}/v1/sessions/{sid}/events"
    start = now()
    attempt = 0
    detail = "no attempt made"
    while True:
        try:
            status, _ctype, _body = _http_post(url, {"type": _STOP_SESSION_TYPE}, http_timeout)
        except http.client.HTTPException as exc:
            kind, detail = TRANSIENT, f"HTTP protocol error: {type(exc).__name__}: {exc}"
        except urllib.error.URLError as exc:
            kind, detail = TRANSIENT, f"connection error: {exc.reason}"
        except OSError as exc:
            kind, detail = TRANSIENT, f"connection error: {exc}"
        else:
            kind, detail = _classify_stop(status)

        if kind == OK:
            print("stopped")
            return EXIT_OK
        if kind == GONE:
            print("already-gone")
            return EXIT_OK

        elapsed = now() - start
        remaining = budget_s - elapsed
        if remaining <= 0:
            _alarm(
                f"ALARM: session {sid} could not be stopped after {attempt + 1} attempts "
                f"over {budget_s:.0f}s against {server} (last: {detail}) — its "
                "host-launched runner is still resident and LEAKING memory; reap it with "
                "scripts/maintenance/reap_stale_omnigent_runners.py --execute"
            )
            return EXIT_STOP_FAILED

        _nap(sleep, min(_backoff(attempt, interval_s), remaining))
        attempt += 1


def fetch_session(server: str, sid: str, timeout: float):
    """Fetch one session document. Returns (kind, doc_or_None, detail).

    Checks, in order: transport, HTTP status, emptiness, content-type, parse.
    Every check that fails classifies rather than raising -- the caller decides
    whether the failure is worth retrying.
    """
    url = f"{server.rstrip('/')}/v1/sessions/{sid}"
    try:
        status, ctype, body = _http_get(url, timeout)
    except http.client.HTTPException as exc:
        # `http.client.IncompleteRead` (a peer that closed mid-body, i.e. shorter
        # than its own Content-Length) and its siblings BadStatusLine /
        # LineTooLong derive from Exception, NOT from URLError or OSError, so
        # they slip past both handlers below and terminate the poller with a
        # TRACEBACK instead of the one terminal alarm line this module promises
        # (Codex, #1618). A truncated read is a transport fault like any other.
        return TRANSIENT, None, f"HTTP protocol error: {type(exc).__name__}: {exc}"
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
    # `or []` alone is not a type guard: a schema-drifted scalar (`{"items": 1}`)
    # is truthy and non-iterable, so the loop below would raise TypeError out of
    # the poll loop as a traceback (Codex, #1618). Here the honest answer to
    # "does this doc prove a run happened" is simply no.
    items = doc.get("items")
    if not isinstance(items, list):
        return False
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "message":
            continue
        data = it.get("data") if isinstance(it.get("data"), dict) else {}
        if (data.get("role") or it.get("role")) == "assistant":
            return True
    return False


def _backoff(attempt: int, cap: float) -> float:
    """Exponential backoff, TOTAL over its inputs.

    `cap` is clamped non-negative and the exponent is bounded: an unclamped
    `2.0**attempt` raises OverflowError once `attempt` passes ~1024, which a
    non-positive cap makes reachable (every wait becomes 0, so the attempt
    counter runs away). Both are defence in depth behind `_positive()` below,
    which rejects such a value at the CLI boundary before it ever gets here.
    """
    return min(2.0 ** min(attempt, 60), max(0.0, cap))


def _nap(sleep, seconds: float) -> None:
    """Sleep for `seconds`, never for a negative one.

    Every retry loop in this module derives its wait from CLI-supplied numbers,
    and `time.sleep(-1)` raises `ValueError` — which would escape as a traceback
    and a raw exit 1, colliding with the wrapper's public alarm codes and
    bypassing the single-terminal-line contract (Greptile P2, #1625). A
    nonsensical interval is not a reason to lose a review, so it degrades to
    "retry immediately" and the WALL BUDGET remains the real bound: every caller
    checks its own elapsed time before the next fetch, so a zero wait cannot
    become an unbounded loop, only a fast one.

    Clamped HERE rather than at each call site so no future retry loop can
    reintroduce it, and clamped rather than validated at argparse so the
    directly-callable functions (which the offline tests drive) are safe too.
    """
    sleep(max(0.0, seconds))


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
        _nap(sleep, min(_backoff(attempt, interval_s), max(0.0, budget_s - elapsed)))
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

    Worst-case SLEEP schedule before declaring poll-lost, at the defaults the
    wrapper uses (interval 30, max_transient 8, cap = interval):

        30 (first interval, before the first poll)
      + 1 + 2 + 4 + 8 + 16 + 30 + 30   (backoffs before the 8th failed fetch)
      = 121s

    121s is the SLEEP total, and it is the whole wall clock only for faults that
    answer IMMEDIATELY -- connection refused, empty body, 5xx, 404, unparseable
    body. A STALLING endpoint (accepts the connection, then never answers)
    instead burns up to `http_timeout` per attempt, so its wall is

        121 + max_transient * http_timeout = 121 + 8*60 = 601s

    at the defaults (Codex, #1618). This is documented rather than capped on
    purpose: shrinking each request's timeout to the residual detection budget
    would hand a slow-but-HEALTHY Omnigent a sub-second timeout near the end of a
    streak, converting server load into a fabricated poll-lost -- a FALSE ALARM
    in the one lane whose entire thesis is that a lost review must be real and
    loud. The run stays bounded regardless: `timeout_s` is re-checked at the top
    of every iteration and each iteration adds at most one sleep (itself capped
    by the remaining budget) plus one fetch, so poll() always returns within
    `timeout_s + http_timeout`. cross_review.sh's budget arithmetic uses exactly
    that bound. Both numbers are pinned by tests.

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

        _nap(sleep, min(next_sleep, remaining))
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


def report_from_doc(d, sid: str) -> int:
    """Print the tail of the assistant conversation. Returns an exit code.

    Reports can span messages, so print the tail of the conversation rather than
    only the last message (polly non-blocking on #1578).
    """
    # Classify the container shape BEFORE iterating it. `d.get("items") or []`
    # guarded only the missing/empty case; a schema-drifted scalar such as
    # `{"items": 1}` is truthy and non-iterable, so the loop raised TypeError and
    # the wrapper printed a Python traceback instead of this module's single
    # session-scoped "re-dispatch required" alarm (Codex, #1618). Every sibling
    # field (doc / data / content) was already guarded; `items` was the gap.
    items = d.get("items", [])
    if items is None:
        items = []
    if not isinstance(items, list):
        _alarm(
            f"ALARM: the session payload for {sid} carried a non-list 'items' "
            f"({type(items).__name__}) — payload shape changed, no report retrieved; "
            "re-dispatch required"
        )
        return EXIT_POLL_LOST

    texts = []
    for it in items:
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


def extract_report(path: str, sid: str) -> int:
    """Print the tail of the assistant conversation from a FILE.

    Kept alongside `fetch_report` for callers that already hold the payload
    (and for the offline tests, which drive every unusable-payload shape
    through it without an HTTP seam).
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

    return report_from_doc(d, sid)


def fetch_report(server, sid, budget_s, interval_s, max_gone, http_timeout,
                 sleep=time.sleep, now=time.monotonic) -> int:
    """Fetch a COMPLETED session and print its report. Returns an exit code.

    The last one-shot HTTP call in the lane, and the last place a blip could
    still throw a finished review away. `curl -sf … -o "$TMPJ"` gave the report
    GET a single attempt with no classification: a 502 from the proxy, a
    half-written body, or an Omnigent restarting in the second between "poll saw
    idle" and "fetch the transcript" discarded a review that had ALREADY BEEN
    PAID FOR (~20 minutes of worker) and forced a full re-dispatch. Every other
    response in this module is classified and bounded-retried; this one was not
    (Codex, #1618 — resolved without a fix at the time).

    So the same contract applies here: `fetch_session` classifies, TRANSIENT is
    retried with exponential backoff until `budget_s` of WALL is spent, and GONE
    is confirmed `max_gone` times before it is believed. Anything terminal
    prints exactly one alarm line naming the session and "re-dispatch required".

    The budget is wall, not attempts, and it is checked BEFORE each new fetch, so
    the call always returns within `budget_s + http_timeout` — one sleep (itself
    capped by the residual budget) plus one request may land past the budget.
    cross_review.sh's BUDGET block counts exactly that bound.

    At the wrapper's defaults (budget 90s, cap 30s) the fetches land at
    t = 0, 1, 3, 7, 15, 31, 61, 90 — 8 attempts, and the t=61 one recovers a
    ~60s Omnigent restart with room to spare, the same tolerance poll() carries.
    All pinned by tests.
    """
    start = now()
    attempt = 0
    gone_run = 0
    while True:
        kind, doc, detail = fetch_session(server, sid, http_timeout)
        if kind == OK:
            return report_from_doc(doc, sid)

        if kind == GONE:
            gone_run += 1
            if gone_run >= max_gone:
                _alarm(
                    f"ALARM: session {sid} disappeared before its report could be read "
                    f"({detail}, confirmed {gone_run}x) — the review COMPLETED but its "
                    "transcript is gone; re-dispatch required"
                )
                return EXIT_POLL_LOST
        else:
            gone_run = 0

        elapsed = now() - start
        remaining = budget_s - elapsed
        if remaining <= 0:
            _alarm(
                f"ALARM: the completed report for session {sid} was unreadable after "
                f"{attempt + 1} attempts over {budget_s:.0f}s against {server} "
                f"(last: {detail}) — the review finished but its report was lost; "
                "re-dispatch required"
            )
            return EXIT_POLL_LOST

        _nap(sleep, min(_backoff(attempt, interval_s), remaining))
        attempt += 1


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

    # Two modes, exactly one of which must be chosen: FETCH (--server, with
    # classification + bounded retries) or FILE (--payload, for a payload the
    # caller already holds). The file mode stays because it is the only way to
    # drive an unusable-payload shape without an HTTP seam.
    rep = sub.add_parser("report", help="print the assistant report for a completed session")
    rep.add_argument("--session-id", required=True)
    rep.add_argument("--payload", help="read the session JSON from this file instead of fetching")
    rep.add_argument("--server", help="fetch GET /v1/sessions/<id> from this Omnigent server")
    rep.add_argument("--budget-s", type=float, default=90.0)
    rep.add_argument("--interval-s", type=float, default=30.0)
    rep.add_argument("--max-gone", type=int, default=3)
    rep.add_argument("--http-timeout-s", type=float, default=60.0)

    sid = sub.add_parser("session-id", help="extract the id from a session-create response body")
    sid.add_argument("--payload", required=True)

    stop = sub.add_parser(
        "stop", help="terminate a session AND its host-launched runner (the leak fix)"
    )
    stop.add_argument("--server", required=True)
    stop.add_argument("--session-id", required=True)
    # Short by design: this runs AFTER the report is in hand, inside the
    # wrapper's already-tight margin against the caller's 2500s timeout. A stop
    # that needs more than this is a wedged Omnigent, and the sweeper -- not a
    # longer wait -- is the answer to that.
    stop.add_argument("--budget-s", type=float, default=20.0)
    stop.add_argument("--interval-s", type=float, default=5.0)
    stop.add_argument("--http-timeout-s", type=float, default=10.0)
    return p


def _positive(args, *names) -> str | None:
    """Return an alarm message for the first non-positive duration, else None.

    argparse types these as floats but happily accepts `-30`. A negative wait
    reaches `time.sleep` and raises ValueError -- a TRACEBACK plus a raw exit 1,
    which collides with the wrapper's public alarm code and breaks this module's
    one-terminal-line contract; a zero one turns a bounded retry loop into a
    busy spin (Greptile P2, #1625). Both are usage errors, so they are refused
    HERE with the module's own EXIT_USAGE rather than absorbed silently.
    """
    for name in names:
        value = getattr(args, name, None)
        if value is not None and value <= 0:
            flag = "--" + name.replace("_", "-").removesuffix("-s") + "-s"
            return (
                f"ALARM: {flag} must be a positive number of seconds, got {value:g} — "
                "no report retrieved; fix the invocation and re-dispatch"
            )
    return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bad = _positive(
        args, "budget_s", "interval_s", "timeout_s", "running_grace_s", "http_timeout_s"
    )
    if bad:
        _alarm(bad)
        return EXIT_USAGE
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
        # Mode selection is checked here, not by argparse's mutually-exclusive
        # group, so a misuse exits with this module's documented usage code and
        # one alarm line rather than argparse's own exit 2 (which collides with
        # the wrapper's public alarm code).
        if bool(args.payload) == bool(args.server):
            _alarm(
                "ALARM: report needs EXACTLY ONE of --server (fetch, retried) or "
                "--payload (read a file); no report retrieved"
            )
            return EXIT_USAGE
        if args.payload:
            return extract_report(args.payload, args.session_id)
        return fetch_report(
            args.server,
            args.session_id,
            args.budget_s,
            args.interval_s,
            args.max_gone,
            args.http_timeout_s,
        )
    if args.cmd == "session-id":
        return extract_session_id(args.payload)
    if args.cmd == "stop":
        return stop_session(
            args.server,
            args.session_id,
            args.budget_s,
            args.interval_s,
            args.http_timeout_s,
        )
    return EXIT_USAGE  # pragma: no cover - argparse rejects unknown subcommands


if __name__ == "__main__":
    sys.exit(main())
