"""Offline tests for scripts/maintenance/cross_review_poll.py.

Regression cover for the 2026-08-03 cross-review incident on PR #1614: the
wrapper's inline `json.load(...)` parsed curl bodies with no check on HTTP
status, emptiness or content-type, so an empty/non-JSON body either raised
JSONDecodeError or degraded to a sentinel the poll loop treated as neither
terminal nor alarming -- the review was lost in silence.

No network, no live Omnigent: every test drives canned responses through the
module's single HTTP seam (`_http_get`) and a fake clock, so the whole suite is
instant and deterministic. `test_negative_control_*` is the load-bearing one --
it runs the OLD parsing logic against the empty-body fixture and asserts it
raises, then asserts the new code degrades to a retry instead.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "maintenance" / "cross_review_poll.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("cross_review_poll", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crp = _load_module()

SERVER = "http://127.0.0.1:6767"
SID = "057995d1517418e6839f51d340785dd6"

# ---------------------------------------------------------------- fixtures ---
# Canned bodies. The 404 shape was captured from the LIVE Omnigent on
# 2026-08-03 (read-only GET /v1/sessions/nonexistent-id), not hand-written.
NOT_FOUND_BODY = b'{"error":{"code":"not_found","message":"Not found."}}'
HTML_ERROR_PAGE = b"<html><head><title>502 Bad Gateway</title></head><body>502</body></html>"
EMPTY_BODY = b""


def _resp(status=200, ctype="application/json", body=b"{}"):
    return (status, ctype, body)


def _running(n=1):
    return _resp(body=json.dumps({"id": SID, "status": "running"}).encode())


def _idle_with_report(text="BLOCKING: none. CLEAN."):
    doc = {
        "id": SID,
        "status": "idle",
        "items": [
            {"type": "message", "data": {"role": "user", "content": "kick"}},
            {"type": "message", "data": {"role": "assistant", "content": text}},
        ],
    }
    return _resp(body=json.dumps(doc).encode())


class FakeHTTP:
    """Replays a scripted sequence of `_http_get` outcomes.

    Each entry is either a (status, ctype, body) tuple or an exception instance
    to raise (connection refused). The last entry repeats once exhausted, so a
    test can express "broken forever" with a single element.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, url, timeout):
        self.calls += 1
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


def _patch_http(monkeypatch, script):
    fake = FakeHTTP(script)
    monkeypatch.setattr(crp, "_http_get", fake)
    return fake


# ------------------------------------------------------- fetch_session unit ---


@pytest.mark.parametrize(
    "response, expected_kind, detail_contains",
    [
        (_resp(body=EMPTY_BODY), crp.TRANSIENT, "empty response body"),
        (_resp(ctype="text/html", body=HTML_ERROR_PAGE), crp.TRANSIENT, "non-JSON body"),
        (_resp(status=500, ctype="text/plain", body=b"boom"), crp.TRANSIENT, "HTTP 500"),
        (_resp(status=502, body=b""), crp.TRANSIENT, "HTTP 502"),
        (_resp(status=404, body=NOT_FOUND_BODY), crp.GONE, "404"),
        (_resp(body=b"{ truncated"), crp.TRANSIENT, "JSON parse failed"),
        (_resp(body=b"[1,2,3]"), crp.TRANSIENT, "expected object"),
        (_resp(body=NOT_FOUND_BODY), crp.GONE, "not_found error envelope"),
        (_resp(body=b'{"id":"x","status":"idle"}'), crp.OK, ""),
    ],
)
def test_fetch_session_classifies_every_body_shape(
    monkeypatch, response, expected_kind, detail_contains
):
    _patch_http(monkeypatch, [response])
    kind, doc, detail = crp.fetch_session(SERVER, SID, 5)
    assert kind == expected_kind, detail
    assert detail_contains in detail
    if expected_kind == crp.OK:
        assert doc["status"] == "idle"


def test_fetch_session_connection_refused_is_transient(monkeypatch):
    import urllib.error

    _patch_http(monkeypatch, [urllib.error.URLError(ConnectionRefusedError(111, "refused"))])
    kind, _doc, detail = crp.fetch_session(SERVER, SID, 5)
    assert kind == crp.TRANSIENT
    assert "connection error" in detail


# ----------------------------------------------------------- negative control ---


def _old_parse_status(body: bytes) -> str:
    """The EXACT pre-fix inline one-liner from cross_review.sh line 141.

        python3 -c "import json,sys;print(json.load(sys.stdin).get('status','?'))"
    """
    return json.load(io.StringIO(body.decode())).get("status", "?")


def test_negative_control_empty_body_crashed_old_parser_new_one_retries(monkeypatch, clock):
    """The incident, both halves: old logic raises, new logic degrades to retry.

    Without this control the hardening is unfalsifiable -- a test that only
    exercises the new code cannot show the old code was broken.
    """
    # 1. Break the fix: the old parser dies on the empty body with the exact
    #    error from the incident report.
    with pytest.raises(json.JSONDecodeError) as exc:
        _old_parse_status(EMPTY_BODY)
    assert "Expecting value: line 1 column 1 (char 0)" in str(exc.value)

    # The HTML error page kills it too -- same failure class, different body.
    with pytest.raises(json.JSONDecodeError):
        _old_parse_status(HTML_ERROR_PAGE)

    # 2. The new classifier absorbs both as retryable, raising nothing.
    for body, ctype in ((EMPTY_BODY, "application/json"), (HTML_ERROR_PAGE, "text/html")):
        _patch_http(monkeypatch, [_resp(ctype=ctype, body=body)])
        kind, _doc, _detail = crp.fetch_session(SERVER, SID, 5)
        assert kind == crp.TRANSIENT

    # 3. And a transient blip that RECOVERS still yields the report, i.e. the
    #    retry is real and not just a slower failure.
    _patch_http(
        monkeypatch,
        [_resp(body=EMPTY_BODY), _resp(ctype="text/html", body=HTML_ERROR_PAGE), _idle_with_report()],
    )
    rc = crp.poll(
        SERVER, SID, timeout_s=600, interval_s=30, running_grace_s=300,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_OK


# ------------------------------------------------------------- poll behaviour ---


def test_poll_success_sequence_prints_terminal_status(monkeypatch, capsys, clock):
    _patch_http(monkeypatch, [_running(), _running(), _idle_with_report()])
    rc = crp.poll(
        SERVER, SID, timeout_s=600, interval_s=30, running_grace_s=300,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == "idle"


def test_poll_waiting_is_nonterminal(monkeypatch, capsys, clock):
    waiting = _resp(body=json.dumps({"id": SID, "status": "waiting"}).encode())
    _patch_http(monkeypatch, [waiting, waiting, _idle_with_report()])
    rc = crp.poll(
        SERVER, SID, timeout_s=600, interval_s=30, running_grace_s=300,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == "idle"


@pytest.mark.parametrize(
    "broken",
    [
        _resp(body=EMPTY_BODY),
        _resp(ctype="text/html", body=HTML_ERROR_PAGE),
        _resp(status=500, ctype="text/plain", body=b"boom"),
        _resp(body=b'{"id":"x"}'),  # valid JSON, incomplete: no status field
    ],
    ids=["empty", "html", "500", "incomplete-json"],
)
def test_poll_exhausts_bounded_retries_and_alarms_loudly(monkeypatch, capsys, clock, broken):
    fake = _patch_http(monkeypatch, [broken])
    rc = crp.poll(
        SERVER, SID, timeout_s=100000, interval_s=30, running_grace_s=300,
        max_transient=4, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_POLL_LOST
    assert fake.calls == 4, "retries must be BOUNDED, not unlimited"
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1, f"exactly one terminal line, got: {err}"
    assert SID in err[0]
    assert "re-dispatch required" in err[0]
    # Exact, not "some number under 300": one sleep per iteration, so the first
    # interval plus the backoffs before the 4th failed fetch. A loose bound here
    # is what let the 211s double-sleep regression through review.
    assert clock.now() == 30 + (1 + 2 + 4)


def _poll_defaults():
    """The argparse defaults the wrapper actually runs with (it overrides only
    --timeout-s), read off the parser so the test cannot drift from the CLI."""
    ns = crp.build_parser().parse_args(
        ["poll", "--server", SERVER, "--session-id", SID, "--timeout-s", "2400"]
    )
    return ns


def test_poll_lost_bound_at_production_defaults_is_30_plus_1_2_4_8_16_30_30(monkeypatch, capsys, clock):
    """Pins the worst case at the defaults the wrapper really uses: 121s.

    The regression this exists for: sleeping the full interval AND the backoff
    every iteration made this (30+1)+(30+2)+(30+4)+(30+8)+(30+16)+30 = 211s,
    which the old loosely-bounded, non-default-config test could not see.
    """
    d = _poll_defaults()
    assert d.interval_s == 30 and d.max_transient == 8, "defaults moved — retune the bound"

    fake = _patch_http(monkeypatch, [_resp(body=EMPTY_BODY)])
    rc = crp.poll(
        SERVER, SID, timeout_s=d.timeout_s, interval_s=d.interval_s,
        running_grace_s=d.running_grace_s, max_transient=d.max_transient,
        max_gone=d.max_gone, http_timeout=d.http_timeout_s,
        sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_POLL_LOST
    assert fake.calls == 8
    expected = 30 + (1 + 2 + 4 + 8 + 16 + 30 + 30)
    assert expected == 121
    assert clock.now() == expected, f"poll-lost must land at exactly {expected}s"
    # And nowhere near the old silent-spin budget.
    assert clock.now() < d.timeout_s / 10


def test_one_sleep_per_iteration_on_the_transient_path(monkeypatch, clock):
    """The mechanism, asserted directly: no iteration sleeps twice.

    Bounding only the total would still pass if a future edit rebalanced the
    two sleeps, so assert the sleep SCHEDULE is exactly interval-then-backoffs.
    """
    _patch_http(monkeypatch, [_resp(body=EMPTY_BODY)])
    crp.poll(
        SERVER, SID, timeout_s=100000, interval_s=30, running_grace_s=300,
        max_transient=5, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    # 5 fetches => the interval before the 1st, then a backoff before each of
    # the other 4. The 5th failure exhausts and returns without sleeping again.
    assert clock.slept == [30, 1, 2, 4, 8], "one sleep per iteration: interval, then backoffs"


def test_a_60s_server_restart_is_ridden_out_at_production_defaults(monkeypatch, capsys, clock):
    """Tolerance must survive collapsing the double sleep.

    Removing the padding interval spends the retry budget purely in backoff, so
    a 6-retry budget would have exhausted 31s into a restart. With max_transient
    8 the failed fetches land at t=0,1,3,7,15,31,61,91 from the first failure:
    a 60s outage accrues 6 and then recovers.
    """
    d = _poll_defaults()

    class Restart:
        """Fails every fetch for 60s of clock time, then serves a real session."""

        def __init__(self, clk):
            self.clk = clk
            self.first_failure = None
            self.failures = 0

        def __call__(self, url, timeout):
            if self.first_failure is None:
                self.first_failure = self.clk.now()
            if self.clk.now() - self.first_failure < 60:
                self.failures += 1
                raise ConnectionRefusedError(111, "connection refused")
            return _idle_with_report()

    restart = Restart(clock)
    monkeypatch.setattr(crp, "_http_get", restart)
    rc = crp.poll(
        SERVER, SID, timeout_s=d.timeout_s, interval_s=d.interval_s,
        running_grace_s=d.running_grace_s, max_transient=d.max_transient,
        max_gone=d.max_gone, http_timeout=d.http_timeout_s,
        sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_OK, "a 60s restart must NOT be declared poll-lost"
    assert capsys.readouterr().out.strip() == "idle"
    assert restart.failures == 6, "6 transients accrue in a 60s outage"
    assert restart.failures < d.max_transient, "with margin left before exhaustion"


def test_poll_vanished_session_is_not_absorbed_as_a_blip(monkeypatch, capsys, clock):
    """The incident's tail: `curl -sf` turned this 404 into an empty body, which
    the old loop scored as `poll-fail` -- nonterminal AND non-alarming."""
    _patch_http(monkeypatch, [_running(), _resp(status=404, body=NOT_FOUND_BODY)])
    rc = crp.poll(
        SERVER, SID, timeout_s=100000, interval_s=30, running_grace_s=300,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_POLL_LOST
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert SID in err[0] and "re-dispatch required" in err[0]
    assert "disappeared" in err[0]


def test_poll_hard_timeout(monkeypatch, capsys, clock):
    _patch_http(monkeypatch, [_running()])
    rc = crp.poll(
        SERVER, SID, timeout_s=120, interval_s=30, running_grace_s=300,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_TIMEOUT
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert SID in err[0] and "re-dispatch required" in err[0]
    assert clock.now() <= 121, "the hard timeout must actually bound the loop"


def test_poll_never_running_is_a_silent_kick_death(monkeypatch, capsys, clock):
    _patch_http(monkeypatch, [_resp(body=json.dumps({"id": SID, "status": "idle"}).encode())])
    rc = crp.poll(
        SERVER, SID, timeout_s=100000, interval_s=30, running_grace_s=120,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_NEVER_RUNNING
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert SID in err[0] and "re-dispatch required" in err[0]


def test_poll_finished_between_two_polls_is_completion_not_death(monkeypatch, capsys, clock):
    """An assistant reply proves the run happened even if `running` was missed."""
    _patch_http(monkeypatch, [_idle_with_report()])
    rc = crp.poll(
        SERVER, SID, timeout_s=100000, interval_s=30, running_grace_s=120,
        max_transient=6, max_gone=3, http_timeout=60, sleep=clock.sleep, now=clock.now,
    )
    assert rc == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == "idle"


# ------------------------------------------------ registration (dispatch race) ---


def test_await_registration_success_is_immediate(monkeypatch, clock):
    fake = _patch_http(monkeypatch, [_running()])
    rc = crp.await_registration(SERVER, SID, 60, 5, 30, sleep=clock.sleep, now=clock.now)
    assert rc == crp.EXIT_OK
    assert fake.calls == 1
    assert clock.slept == [], "a healthy session must not cost a sleep"


def test_await_registration_tolerates_a_slow_start(monkeypatch, clock):
    _patch_http(
        monkeypatch,
        [_resp(status=404, body=NOT_FOUND_BODY), _resp(body=EMPTY_BODY), _running()],
    )
    rc = crp.await_registration(SERVER, SID, 60, 5, 30, sleep=clock.sleep, now=clock.now)
    assert rc == crp.EXIT_OK


def test_await_registration_never_registers_fails_loudly(monkeypatch, capsys, clock):
    """The dispatch race the incident proved real: POST returned an id, but the
    session was `not_found` afterwards. Bail BEFORE spending a worker."""
    fake = _patch_http(monkeypatch, [_resp(status=404, body=NOT_FOUND_BODY)])
    rc = crp.await_registration(SERVER, SID, 30, 5, 30, sleep=clock.sleep, now=clock.now)
    assert rc == crp.EXIT_NEVER_REGISTERED
    assert rc != crp.EXIT_POLL_LOST, "dispatch-race and poll-lost must be distinguishable"
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert SID in err[0] and "re-dispatch required" in err[0]
    assert "never registered" in err[0]
    assert clock.now() <= 30, "the registration wait must be SHORT and bounded"
    assert fake.calls > 1, "it must retry at least once before giving up"


# --------------------------------------------------------- session-id + report ---


def test_extract_session_id_happy_path(tmp_path, capsys):
    p = tmp_path / "create.json"
    p.write_bytes(json.dumps({"id": SID}).encode())
    assert crp.extract_session_id(str(p)) == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == SID


@pytest.mark.parametrize(
    "body, marker",
    [
        (EMPTY_BODY, "EMPTY"),
        (HTML_ERROR_PAGE, "not JSON"),
        (b'{"detail":"nope"}', "no 'id' field"),
    ],
    ids=["empty", "html", "no-id"],
)
def test_extract_session_id_flags_an_orphaned_dispatch(tmp_path, capsys, body, marker):
    p = tmp_path / "create.json"
    p.write_bytes(body)
    rc = crp.extract_session_id(str(p))
    assert rc == crp.EXIT_DISPATCH_FAILED
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert marker in err[0]
    assert "re-dispatch required" in err[0]


def test_extract_report_prints_the_conversation_tail(tmp_path, capsys):
    doc = {
        "items": [
            {"type": "message", "data": {"role": "user", "content": "kick"}},
            {"type": "message", "data": {"role": "assistant", "content": "part one"}},
            {"type": "message", "data": {"role": "assistant", "content": "part two"}},
        ]
    }
    p = tmp_path / "s.json"
    p.write_bytes(json.dumps(doc).encode())
    assert crp.extract_report(str(p), SID) == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == "part one\n\n---\n\npart two"


@pytest.mark.parametrize(
    "body",
    [EMPTY_BODY, HTML_ERROR_PAGE, b"[]", json.dumps({"items": []}).encode()],
    ids=["empty", "html", "not-an-object", "zero-assistant-messages"],
)
def test_extract_report_never_reports_an_unusable_payload_as_clean(tmp_path, capsys, body):
    p = tmp_path / "s.json"
    p.write_bytes(body)
    rc = crp.extract_report(str(p), SID)
    assert rc != crp.EXIT_OK
    out = capsys.readouterr()
    assert out.out.strip() == "", "a broken payload must never print a 'report'"
    err = out.err.strip().splitlines()
    assert len(err) == 1
    assert SID in err[0] and "re-dispatch required" in err[0]


def test_exit_codes_match_the_documented_mapping_exactly():
    """Pin the NUMBERS, not just their uniqueness.

    Asserting only distinctness let a code/contract naming mismatch through
    review (codex, #1618). scripts/AGENTS.md and the cross_review.sh header
    both publish this table; if it changes, all three change together.
    """
    assert crp.EXIT_OK == 0
    assert crp.EXIT_USAGE == 1
    assert crp.EXIT_NEVER_REGISTERED == 3
    assert crp.EXIT_POLL_LOST == 4
    assert crp.EXIT_TIMEOUT == 5
    assert crp.EXIT_NEVER_RUNNING == 6
    assert crp.EXIT_DISPATCH_FAILED == 7

    codes = [
        crp.EXIT_NEVER_REGISTERED, crp.EXIT_POLL_LOST, crp.EXIT_TIMEOUT,
        crp.EXIT_NEVER_RUNNING, crp.EXIT_DISPATCH_FAILED,
    ]
    assert len(set(codes)) == len(codes)
    assert all(c not in (crp.EXIT_OK, crp.EXIT_USAGE) for c in codes)

    # The published table must agree with the code, verbatim.
    for doc in (
        (_MODULE_PATH.parent / "cross_review.sh").read_text(),
        (_MODULE_PATH.parents[1] / "AGENTS.md").read_text(),
    ):
        for code, label in (
            (crp.EXIT_NEVER_REGISTERED, "never-registered"),
            (crp.EXIT_POLL_LOST, "poll-lost"),
            (crp.EXIT_TIMEOUT, "timeout"),
            (crp.EXIT_NEVER_RUNNING, "never-running"),
            (crp.EXIT_DISPATCH_FAILED, "dispatch-failed"),
        ):
            assert f"{code} {label}" in doc, f"undocumented/renamed: {code} {label}"


def test_wrapper_validates_the_registration_budget_env_var():
    """Bad input must hit this script's alarm, not an argparse traceback."""
    sh = (_MODULE_PATH.parent / "cross_review.sh").read_text()
    assert "CROSS_REVIEW_REGISTER_TIMEOUT_S must be a positive integer" in sh
    assert 'REGISTER_TIMEOUT_S" -gt 0' in sh


def test_cli_wires_every_subcommand(tmp_path, capsys):
    p = tmp_path / "create.json"
    p.write_bytes(json.dumps({"id": SID}).encode())
    assert crp.main(["session-id", "--payload", str(p)]) == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == SID

    doc = {"items": [{"type": "message", "data": {"role": "assistant", "content": "hi"}}]}
    q = tmp_path / "s.json"
    q.write_bytes(json.dumps(doc).encode())
    assert crp.main(["report", "--session-id", SID, "--payload", str(q)]) == crp.EXIT_OK
    assert capsys.readouterr().out.strip() == "hi"


def test_wrapper_no_longer_parses_any_body_inline():
    """Structural guard: the incident class was an inline `json.load` in bash."""
    raw = (_MODULE_PATH.parent / "cross_review.sh").read_text()
    # Comments explain the incident and legitimately name both; check the CODE.
    code = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    assert "json.load" not in code, "response parsing belongs in cross_review_poll.py"
    assert "poll-fail" not in code, "the silent nonterminal sentinel must stay gone"
    assert "cross_review_poll.py" in code, "the wrapper must actually call the poller"
