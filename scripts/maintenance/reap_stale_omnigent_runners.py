#!/usr/bin/env python3
"""Reap leaked Omnigent per-session runners — the safety net behind cross_review.sh.

Why this exists (MEASURED 2026-08-04, during a six-lane merge drive): every
cross-review dispatch leaves one `python -m omnigent.runner._entry` process
resident forever. Nineteen of them had accumulated, the box was down to 0-245 MB
available, and Omnigent's own host daemon then refused to come online under load
(~20:08-21:00), killing two review dispatches mid-flight. A
`docker restart zoe-omnigent` cleared them (20 -> 1, ~956 MB recovered) but is
only safe with no review in flight.

WHY NOTHING COLLECTED THEM — the two properties that defeat the obvious guards:

  * NOT AN ORPHAN. `omnigent host` spawns the runner and stays its parent, so
    the `ppid == 1` rule that `reap_stale_serena.py` leans on never fires. The
    runner even carries a parent-death watchdog, which only makes it more
    determined to survive.
  * NOT ATTRIBUTABLE BY CMDLINE. Its argv is literally
    `python -m omnigent.runner._entry` — the session id travels in its
    ENVIRONMENT. cross_review.sh's `stop_worker` scans /proc cmdlines for the
    session id, so it reaps the `omnigent run` kick and the harness subprocess
    (`omnigent.runtime.harnesses._runner --conversation-id <sid>`) and walks
    straight past the process holding the memory.

The primary fix is synchronous and lives in cross_review.sh: it now POSTs
Omnigent's own `stop_session` event, which makes the server tell the host to
stop the runner it launched. THIS tool is the safety net for the cases that
teardown cannot cover — a wrapper SIGKILLed by its caller's 2500s subprocess
timeout (the EXIT trap never runs), a wedged host daemon that cannot service the
stop, or any other launcher (omnigent_issue_executor, the Flue heavy lane) that
never took the cross-review path at all.

KILL POLICY — deliberately conservative, because the asymmetry is severe. A
missed reap costs ~50 MB until the next hourly run; a WRONG reap kills a live
agent turn mid-flight and loses work that has already been paid for. So a runner
is kept unless EVERY one of these says it is dead:

  1. GRACE      — younger than --grace-min (default 20) is never touched. A
                  runner is inserted at Popen time, before its tunnel connects
                  and before it spawns a harness, so a fresh one looks idle.
  2. ATTRIBUTED — the session id must be recoverable from the process
                  environment. An unattributable runner is KEPT, always: we
                  cannot ask about a session we cannot name.
  3. NO HARNESS — no live `omnigent.runtime.harnesses._runner
                  --conversation-id <sid>` process for that session. A harness
                  is hard local proof the runner is serving a conversation.
  4. TERMINAL   — the server says the session is terminal (or gone). `running`
                  and `waiting` are NON-terminal (a `waiting` session is
                  awaiting external work, not finished), and an UNREACHABLE or
                  unparseable server means UNKNOWN, which keeps the runner.
                  Fail safe: no answer is never treated as permission.

Reaping prefers Omnigent's own `stop_session` verb over a signal — the server
pops the host's runner entry before terminating it, so the exit is recorded as
intentional rather than reported to the session as a crash. A signal is the
fallback: SIGTERM, then SIGKILL after --term-wait for anything that ignores it.

A wrong reap is also RECOVERABLE by Omnigent's own design, which is what makes
this tool safe to automate at all: stopping a session is non-sticky, so the
session and its transcript survive and the next message relaunches it on its
still-online host. The cost of the worst case is a relaunch, not lost history.

KNOWN LIMIT: the host caps the session slug it puts in the log name at 32
characters. Omnigent 0.7.0 session ids are exactly 32 hex, so today the slug is
the whole id; if a future server issued LONGER ids the recovered id would be a
prefix, every lookup would 404, and rule 4 would read that as "gone". Rules 1-3
still stand in that case and the blast radius is the relaunch above, but revisit
this if session ids ever grow.

Dry-run by default; pass --execute to actually stop/kill. Pair with the shipped
user timer (scripts/setup/systemd/zoe-omnigent-runner-reaper.{service,timer}).

The runners live inside the `zoe-omnigent` container but are visible in the
host's /proc and owned by the host `zoe` uid, so this runs on the HOST with no
docker exec and no container modification.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# The runner's argv is exactly this and carries nothing else — see the module
# docstring. Matching the module path (not "omnigent") keeps the server and host
# daemons, which are long-lived infrastructure, out of scope entirely.
RUNNER_MARKER = "omnigent.runner._entry"

# The harness subprocess the runner spawns per conversation. THIS one does carry
# `--conversation-id <sid>` in argv, which is why cross_review.sh's cmdline scan
# reaches it and why it is usable here as a liveness proof.
HARNESS_MARKER = "omnigent.runtime.harnesses._runner"

# The runner's environment names its log file, and the host builds that name as
# `runner-<session-slug>-<timestamp>.log` where the slug is the session id with
# non-word characters stripped and capped at 32 chars
# (omnigent/host/connect.py, _handle_launch). It is the ONLY place the session
# id appears outside the process's own memory.
LOG_ENV_VAR = "OMNIGENT_PROCESS_LOG_FILE"

# The 16-char floor mirrors cross_review.sh's session-id validation: a
# degenerate short id must not become a wildcard that matches other sessions.
_SESSION_FROM_LOG = re.compile(r"runner-([0-9A-Za-z_-]{16,32})-\d{8}-\d{6}-\d+\.log$")

# Session states that mean "still working" and therefore "keep the runner".
# `waiting` is awaiting external work, NOT finished — treating it as terminal is
# the same mistake that used to end cross-reviews early (Greptile P1 on #1578).
NONTERMINAL_STATUSES = ("running", "waiting")

# The ONLY statuses that authorise a reap. This is an ALLOWLIST, not the
# complement of NONTERMINAL_STATUSES, and the difference is the whole point: an
# unrecognised status is an answer we cannot interpret, and "no answer is never
# permission" has to cover "an answer we do not understand" too, or the rule is
# only a spelling check.
#
# Read off the installed omnigent 0.7.0 rather than assumed — the session
# vocabulary is `launching` / `running` / `waiting` / `idle` / `failed`
# (`omnigent/server/schemas.py::SessionStatusEvent`;
# `server/routes/_sessions/orchestration.py:645` narrows the persisted set to the
# last four). So this was not a hypothetical future-proofing: **`launching`
# already exists**, and a deny-list read it as terminal — as permission to kill a
# session in the act of starting up. Its docstring says it never rides as an
# external `session.status` *today*, which is exactly the kind of load-bearing
# "today" a reaper must not depend on.
#
# The asymmetry picks the default: an unrecognised status costs ~50 MB until an
# operator reads the KEEP lines, while a wrong reap kills a live agent turn. So
# anything unfamiliar KEEPS, and adding a terminal state here stays a deliberate
# edit rather than a silent consequence of the server's vocabulary growing.
TERMINAL_STATUSES = ("idle", "failed")

# Status sentinels this module uses on top of whatever the server reports.
STATUS_GONE = "__gone__"
STATUS_UNKNOWN = "__unknown__"

DEFAULT_SERVER = os.environ.get("OMNIGENT_SERVER", "http://127.0.0.1:6767")


@dataclass
class RunnerProc:
    pid: int
    ppid: int
    uid: int
    age_s: float
    rss_kb: int
    cmdline: str
    session_id: str | None
    # Raw `starttime` (field 22 of /proc/<pid>/stat, in clock ticks since boot).
    # Carried so a reap can prove the pid it is about to signal is still the
    # SAME process it scanned — see `same_runner()`. `scan()` always populates
    # it; the -1.0 default means "not captured", which makes the start-time leg
    # of the identity check unavailable but never weakens the others.
    start_ticks: float = -1.0


def _load_poller():
    """Import cross_review_poll from this directory (shared HTTP classification).

    One implementation of "classify an Omnigent HTTP response" for the whole
    lane: the poller already checks status, emptiness and content-type before
    parsing, and re-implementing that here is how the two copies drift.
    """
    path = Path(__file__).resolve().parent / "cross_review_poll.py"
    spec = importlib.util.spec_from_file_location("cross_review_poll", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("cross_review_poll", mod)
    spec.loader.exec_module(mod)
    return mod


def _boot_time_s(proc_root: str) -> float:
    with open(os.path.join(proc_root, "stat")) as fh:
        for line in fh:
            if line.startswith("btime "):
                return float(line.split()[1])
    raise RuntimeError("btime not found in /proc/stat")


def session_id_from_environ(blob: bytes) -> str | None:
    """Recover the session id from a runner's raw /proc/<pid>/environ.

    Returns None for anything it cannot attribute with confidence — a runner
    launched by the CLI path (which names its log plainly `runner-...`, with no
    session slug), a truncated read, an unexpected filename shape. Callers MUST
    treat None as "keep": an unattributable runner is one we cannot ask the
    server about, and guessing is how a live session gets killed.
    """
    for item in blob.split(b"\0"):
        if not item.startswith(LOG_ENV_VAR.encode() + b"="):
            continue
        value = item.split(b"=", 1)[1].decode("utf-8", "replace")
        match = _SESSION_FROM_LOG.search(os.path.basename(value))
        return match.group(1) if match else None
    return None


def _read_proc(proc_root: str, pid: int, name: str, binary: bool = False):
    mode = "rb" if binary else "r"
    with open(os.path.join(proc_root, str(pid), name), mode) as fh:
        return fh.read()


def scan(proc_root: str = "/proc", now: float | None = None) -> list[RunnerProc]:
    """Enumerate live Omnigent per-session runner processes.

    `proc_root` is a parameter so the tests can drive synthetic process trees;
    production always passes the real /proc.
    """
    now = now if now is not None else time.time()
    boot = _boot_time_s(proc_root)
    ticks = os.sysconf("SC_CLK_TCK")
    found: list[RunnerProc] = []
    for entry in sorted(os.listdir(proc_root)):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            cmdline = (
                _read_proc(proc_root, pid, "cmdline", binary=True)
                .replace(b"\0", b" ")
                .decode(errors="replace")
                .strip()
            )
            if RUNNER_MARKER not in cmdline:
                continue
            stat = _read_proc(proc_root, pid, "stat")
            # Fields after the parenthesised comm — split on the LAST ')' so an
            # exotic process name cannot shift the offsets.
            after = stat.rsplit(")", 1)[1].split()
            ppid = int(after[1])
            start_ticks = float(after[19])
            age_s = now - (boot + start_ticks / ticks)
            rss_kb = 0
            uid = -1
            for line in _read_proc(proc_root, pid, "status").splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                elif line.startswith("Uid:"):
                    uid = int(line.split()[1])
            try:
                session_id = session_id_from_environ(
                    _read_proc(proc_root, pid, "environ", binary=True)
                )
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                # Unreadable environ -> unattributable -> kept by classify().
                session_id = None
        except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError):
            continue  # raced a process exit or an unreadable entry — skip
        found.append(RunnerProc(pid, ppid, uid, age_s, rss_kb, cmdline, session_id,
                                start_ticks))
    return found


def live_conversations(proc_root: str = "/proc") -> set[str]:
    """Session ids that currently have a live harness subprocess.

    Local, cheap and unspoofable-by-accident hard evidence that a runner is
    serving a conversation right now: the harness carries
    `--conversation-id <sid>` in its argv.
    """
    live: set[str] = set()
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            cmdline = (
                _read_proc(proc_root, int(entry), "cmdline", binary=True)
                .replace(b"\0", b" ")
                .decode(errors="replace")
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if HARNESS_MARKER not in cmdline:
            continue
        parts = cmdline.split()
        for i, token in enumerate(parts[:-1]):
            if token == "--conversation-id":
                live.add(parts[i + 1])
                break
    return live


def classify(p: RunnerProc, *, grace_min: int, busy_sessions: set[str],
             status_of) -> str | None:
    """Return a reap reason, or None to KEEP the runner.

    `status_of` is a callable taking a session id and returning the server's
    status string, `STATUS_GONE`, or `STATUS_UNKNOWN`. It is injected so the
    policy is testable with no network and so an unreachable server can never
    be mistaken for a terminal verdict.
    """
    if p.age_s < grace_min * 60:
        return None  # rule 1: a runner younger than the grace window is off limits
    if not p.session_id:
        return None  # rule 2: never reap what we cannot attribute
    if p.session_id in busy_sessions:
        return None  # rule 3: a live harness means a live conversation
    status = status_of(p.session_id)
    if status == STATUS_UNKNOWN:
        return None  # rule 4: no answer is not permission
    if status == STATUS_GONE:
        return f"session {p.session_id} no longer exists"
    if status in TERMINAL_STATUSES:
        return f"session {p.session_id} is terminal ({status})"
    # rule 4, in its allowlist form: `running`/`waiting` are the states we know
    # mean "still working", and anything ELSE we do not recognise is kept too.
    # An unfamiliar answer is not a terminal one — see TERMINAL_STATUSES.
    return None


def make_status_of(poller, server: str, http_timeout: float):
    """Build a `status_of` callable backed by the shared HTTP classification."""

    def status_of(sid: str) -> str:
        kind, doc, _detail = poller.fetch_session(server, sid, http_timeout)
        if kind == poller.GONE:
            return STATUS_GONE
        if kind != poller.OK:
            return STATUS_UNKNOWN
        raw = doc.get("status")
        # A 200 with no usable status is a schema surprise, not a verdict.
        return raw if isinstance(raw, str) and raw else STATUS_UNKNOWN

    return status_of


def _still_runner(pid: int, proc_root: str = "/proc") -> bool:
    """True if the pid is SOME Omnigent runner. Never sufficient to signal on.

    Kept as the cheap first test, but note what it cannot do: every runner's
    argv is byte-identical (`python -m omnigent.runner._entry` — the session id
    is in the ENVIRONMENT, which is the whole premise of this tool), so this
    answers "is a runner here", never "is MY runner here". Use `same_runner()`
    before anything that mutates.
    """
    try:
        blob = _read_proc(proc_root, pid, "cmdline", binary=True)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    return RUNNER_MARKER in blob.replace(b"\0", b" ").decode(errors="replace")


def same_runner(p: RunnerProc, proc_root: str = "/proc") -> bool:
    """True only if this pid is still the EXACT process `scan()` recorded.

    The scan->reap window is not hypothetical: the graceful `stop_session`
    attempt alone can burn `--stop-budget-s` (20s by default) before a signal is
    considered. `_still_runner` covers a pid recycled by an unrelated process,
    but NOT the case that matters here — a pid recycled by ANOTHER SESSION'S
    runner, which is cmdline-identical and therefore indistinguishable to it.
    Signalling that is exactly the forbidden "stop another session's runner",
    and it would land on a LIVE one.

    Three legs, all cheap, and each independently sufficient to refuse:

    1. still a runner at all (`_still_runner`),
    2. its environment still names the SAME session id — the only per-process
       identity that exists, so this is the leg that separates two runners,
    3. its `starttime` is unchanged — which additionally catches a pid recycled
       by a *relaunch of the same session*, where leg 2 legitimately matches but
       the process is a different, live one.

    An unattributable process is refused rather than signalled: this mirrors
    rule 2 of the KILL POLICY, so a runner whose environ became unreadable
    between scan and reap is kept instead of guessed at.
    """
    if not _still_runner(p.pid, proc_root):
        return False
    if not p.session_id:
        return False  # rule 2 again: never signal what we cannot name
    try:
        now_sid = session_id_from_environ(
            _read_proc(proc_root, p.pid, "environ", binary=True)
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    if now_sid != p.session_id:
        return False
    if p.start_ticks >= 0:
        try:
            stat = _read_proc(proc_root, p.pid, "stat")
            if float(stat.rsplit(")", 1)[1].split()[19]) != p.start_ticks:
                return False
        except (FileNotFoundError, ProcessLookupError, PermissionError,
                IndexError, ValueError):
            return False
    return True


def _wait_gone(p: RunnerProc, deadline_s: float, proc_root: str = "/proc") -> bool:
    """Wait for THIS runner to disappear.

    Identity-bound for the same reason `same_runner` is: if the pid is recycled
    by another runner mid-wait, our target IS gone and reporting "survived"
    would escalate a signal onto the newcomer.
    """
    end = time.time() + deadline_s
    while time.time() < end:
        if not same_runner(p, proc_root):
            return True
        time.sleep(0.2)
    return not same_runner(p, proc_root)


def reap(p: RunnerProc, *, poller, server: str, term_wait: float,
         stop_budget_s: float, http_timeout: float, proc_root: str = "/proc",
         status_of=None, busy_sessions_fn=None) -> str:
    """Stop one runner. Graceful `stop_session` first, signals as the fallback.

    `classify()` produced its verdict from a SNAPSHOT taken at scan time, and
    `main()` then walks the process list making one server lookup per session —
    so by the time this runs, that verdict can be seconds old and simply wrong.
    Two things can have changed under it: the pid may no longer be this process
    (`same_runner`), and the session may have come back to life. Both are
    re-read here, against fresh readings, before ANY mutation — including the
    graceful stop, because stopping a live session is still stopping a live
    session.

    `status_of` / `busy_sessions_fn` are injected so the revalidation is
    testable without a network, and so `main()` can pass its UNCACHED lookup:
    re-asking through the per-run cache would just replay the stale answer this
    check exists to catch.
    """
    if not same_runner(p, proc_root):
        return "SKIPPED (PID reused or exited — no longer the scanned runner)"

    # Re-validate the liveness verdict itself, not just the process identity.
    busy = (busy_sessions_fn or live_conversations)(proc_root)
    if p.session_id in busy:
        return "SKIPPED (a harness appeared for this session since the scan)"
    if status_of is not None:
        status = status_of(p.session_id)
        if status != STATUS_GONE and status not in TERMINAL_STATUSES:
            return f"SKIPPED (session is {status} now, not terminal as scanned)"

    if p.session_id:
        # The graceful path: the server pops the host's runner entry BEFORE
        # terminating it, so the exit is recorded as intentional instead of
        # reported to the session as a crash. Its own stdout is swallowed so
        # the operator sees one KEEP/REAP line per process and nothing else;
        # its stderr ALARM (if any) still surfaces.
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = poller.stop_session(server, p.session_id, stop_budget_s, 2.0, http_timeout)
        if rc == 0 and _wait_gone(p, term_wait, proc_root):
            return "stopped via stop_session"

    # The graceful attempt above can have burned `stop_budget_s`, so the pid is
    # re-proved to be this runner before the first signal — otherwise the whole
    # identity check would be defeated by simply taking long enough.
    if not same_runner(p, proc_root):
        return "SKIPPED (PID reused or exited during the graceful stop)"
    try:
        os.kill(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already gone"
    except PermissionError:
        return "SKIPPED (owned by another user)"
    if _wait_gone(p, term_wait, proc_root):
        return "terminated"
    try:
        os.kill(p.pid, signal.SIGKILL)
        return "killed (ignored SIGTERM)"
    except ProcessLookupError:
        return "terminated"
    except PermissionError:
        return "SKIPPED (owned by another user)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default=DEFAULT_SERVER,
                    help=f"Omnigent server to ask about session state (default {DEFAULT_SERVER})")
    ap.add_argument("--grace-min", type=int, default=20,
                    help="never touch runners younger than this (default 20 min)")
    ap.add_argument("--term-wait", type=float, default=10.0,
                    help="seconds to wait for an exit before escalating")
    ap.add_argument("--stop-budget-s", type=float, default=20.0,
                    help="budget for the graceful stop_session attempt")
    ap.add_argument("--http-timeout-s", type=float, default=10.0,
                    help="per-request timeout when asking about a session")
    ap.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    ap.add_argument("--execute", action="store_true",
                    help="actually stop/kill; without this flag it is a dry run")
    args = ap.parse_args(argv)

    poller = _load_poller()
    procs = scan(args.proc_root)
    busy = live_conversations(args.proc_root)
    status_of = make_status_of(poller, args.server, args.http_timeout_s)
    # One lookup per session id, not per process: several runners can share an
    # id across relaunches, and the server answer is the same for all of them.
    status_cache: dict[str, str] = {}

    def cached_status(sid: str) -> str:
        if sid not in status_cache:
            status_cache[sid] = status_of(sid)
        return status_cache[sid]

    my_uid = os.getuid()
    reaped = kept = skipped = 0
    for p in sorted(procs, key=lambda x: -x.rss_kb):
        reason = classify(p, grace_min=args.grace_min, busy_sessions=busy,
                          status_of=cached_status)
        ident = (f"pid={p.pid} age={p.age_s / 3600:.1f}h rss={p.rss_kb // 1024}MB "
                 f"ppid={p.ppid} session={p.session_id or '?'}")
        if reason is None:
            kept += 1
            print(f"KEEP  {ident}")
            continue
        if p.uid != my_uid:
            skipped += 1
            print(f"SKIP  {ident} — {reason}, but owned by uid {p.uid}")
            continue
        if not args.execute:
            reaped += 1
            print(f"WOULD-REAP  {ident} — {reason} (dry run; pass --execute)")
            continue
        # `status_of`, NOT `cached_status`: the revalidation exists to catch a
        # session that changed since the scan, and the cache would hand back the
        # very answer being re-checked.
        outcome = reap(p, poller=poller, server=args.server, term_wait=args.term_wait,
                       stop_budget_s=args.stop_budget_s, http_timeout=args.http_timeout_s,
                       proc_root=args.proc_root, status_of=status_of,
                       busy_sessions_fn=live_conversations)
        print(f"REAP  {ident} — {reason} -> {outcome}")
        if "SKIPPED" in outcome:
            skipped += 1
        else:
            reaped += 1

    mode = "reaped" if args.execute else "would reap"
    # reaped + kept + skipped == len(procs) always, so an operator can verify
    # the run at a glance.
    print(f"\n{len(procs)} omnigent runner(s): {mode} {reaped}, kept {kept}, "
          f"skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
