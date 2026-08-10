"""Brain-lane runtime failover (ZOE_BRAIN_FAILOVER, default OFF).

CONFIRMED BASELINE (the negative control for everything below): with
``ZOE_BRAIN_BACKEND=flue`` and the sidecar refusing connections, dispatch has
NO runtime failover — the turn is served the canned flue sentinel and the
healthy core lane is never attempted. ``test_baseline_*`` pins that today's
behaviour is unchanged while the flag is off; every ``test_failover_*`` case
must go RED if the failover branch in ``brain_dispatch`` is removed.
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging

import pytest

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _clean_breaker():
    """The circuit breaker is module state — never leak it between tests."""
    import brain_dispatch as bd

    bd.reset_failover_state()
    yield
    bd.reset_failover_state()


# ── fakes ────────────────────────────────────────────────────────────────────


class _RefusedClient:
    """httpx.AsyncClient stand-in whose every request is connection-refused."""

    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        import httpx

        type(self).calls += 1
        raise httpx.ConnectError("[Errno 111] Connection refused")

    def stream(self, *a, **k):  # pragma: no cover - streaming lane not used here
        raise AssertionError("stream() not expected in these tests")


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=None, response=None
            )

    def json(self):
        return self._body


def _refused_httpx(monkeypatch):
    import httpx

    _RefusedClient.calls = 0
    monkeypatch.setattr(httpx, "AsyncClient", _RefusedClient)
    return _RefusedClient


def _flue_backend(monkeypatch):
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "true")
    monkeypatch.delenv("ZOE_FLUE_STREAM_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_SEAM_RECALL_INJECT", raising=False)
    monkeypatch.delenv("ZOE_SEAM_OFFER_INJECT", raising=False)


def _boom_core(monkeypatch):
    """Core lane that FAILS the test if it is dispatched to."""
    import zoe_core_client

    async def boom(msg, sid, uid="", **kw):
        raise AssertionError("core lane dispatched — no failover was expected")

    async def boom_stream(msg, sid, uid="", **kw):
        raise AssertionError("core lane dispatched — no failover was expected")
        yield ""  # pragma: no cover

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", boom)
    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", boom_stream)


# ── (1) CONFIRMED baseline: no runtime failover today ────────────────────────


@pytest.mark.asyncio
async def test_baseline_refused_flue_returns_sentinel_and_never_tries_core(monkeypatch):
    """THE FINDING (#1613): a refused :3578 fails the turn with the canned
    sentinel; the healthy core lane is never attempted."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)  # default OFF
    _boom_core(monkeypatch)
    _refused_httpx(monkeypatch)

    out = await bd.brain_oneshot("hi", "s1", "jason")
    assert out == zoe_flue_client._FALLBACK_TEXT


@pytest.mark.asyncio
async def test_baseline_refused_flue_streaming_yields_only_sentinel(monkeypatch):
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)
    _refused_httpx(monkeypatch)

    chunks = [c async for c in bd.brain_streaming("hi", "s1", "jason")]
    assert chunks == [zoe_flue_client._FALLBACK_TEXT]


@pytest.mark.asyncio
async def test_baseline_every_turn_pays_the_failed_connect(monkeypatch):
    """No circuit breaker today: turn 2 re-attempts the dead sidecar."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)
    client = _refused_httpx(monkeypatch)

    await bd.brain_oneshot("one", "s1", "jason")
    await bd.brain_oneshot("two", "s1", "jason")
    assert client.calls == 2


# ── (2) flag ON: bounded failover onto the healthy core lane ─────────────────


def _recording_core(monkeypatch, reply="core answered"):
    """Core lane stub that records how many turns it served."""
    import zoe_core_client

    calls: list[tuple[str, str, str]] = []

    async def fake_core(msg, sid, uid="", **kw):
        calls.append((msg, sid, uid))
        return reply

    async def fake_core_stream(msg, sid, uid="", **kw):
        calls.append((msg, sid, uid))
        yield reply

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", fake_core)
    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", fake_core_stream)
    return calls


@pytest.mark.asyncio
async def test_failover_on_refused_flue_is_served_by_core_exactly_once(monkeypatch):
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    core_calls = _recording_core(monkeypatch)
    client = _refused_httpx(monkeypatch)

    out = await bd.brain_oneshot("hi", "s1", "jason")

    assert out == "core answered"
    assert len(core_calls) == 1, "the retry must be bounded to exactly one"
    assert core_calls[0] == ("hi", "s1", "jason"), "the SAME turn is retried"
    assert client.calls == 1, "one failed connect, then the lane hop"


@pytest.mark.asyncio
async def test_failover_on_streaming_refused_flue_is_served_by_core(monkeypatch):
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    core_calls = _recording_core(monkeypatch)
    _refused_httpx(monkeypatch)

    chunks = [c async for c in bd.brain_streaming("hi", "s1", "jason")]

    assert chunks == ["core answered"]
    assert len(core_calls) == 1


@pytest.mark.asyncio
async def test_failover_falls_to_legacy_when_core_is_off(monkeypatch):
    """The retry lands on the next CONFIGURED lane, not a hardcoded 'core'."""
    import brain_dispatch as bd
    import zoe_agent

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_USE_CORE_BRAIN", "false")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _refused_httpx(monkeypatch)

    async def fake_legacy(msg, sid, uid="family-admin", **kw):
        return f"legacy:{msg}"

    monkeypatch.setattr(zoe_agent, "run_zoe_agent", fake_legacy)

    assert await bd.brain_oneshot("hi", "s1", "jason") == "legacy:hi"


@pytest.mark.asyncio
async def test_failover_logs_lane_attempted_and_lane_served(monkeypatch, caplog):
    """The operator-facing single greppable line (the #1613 runbook check)."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _recording_core(monkeypatch)
    _refused_httpx(monkeypatch)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        await bd.brain_oneshot("my locker code is 31999", "sess-abc", "jason")

    lines = [r.getMessage() for r in caplog.records if "BRAIN_LANE" in r.getMessage()]
    assert len(lines) == 1, f"exactly one lane line per turn, got {lines}"
    assert "lane_attempted=flue" in lines[0]
    assert "lane_served=core" in lines[0]
    assert "outcome=failover" in lines[0]
    assert "session=sess-abc" in lines[0]
    # Ids only — the user's utterance must never land in the operator log.
    assert "31999" not in lines[0] and "locker" not in lines[0]


# ── (3) circuit breaker: open, skip the dead connect, close after the TTL ────


@pytest.mark.asyncio
async def test_circuit_opens_so_the_second_turn_skips_the_dead_connect(monkeypatch):
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    core_calls = _recording_core(monkeypatch)
    client = _refused_httpx(monkeypatch)

    await bd.brain_oneshot("one", "s1", "jason")
    await bd.brain_oneshot("two", "s1", "jason")
    await bd.brain_oneshot("three", "s1", "jason")

    assert len(core_calls) == 3, "every turn is still answered"
    assert client.calls == 1, "only the FIRST turn pays the failed-connect tax"


@pytest.mark.asyncio
async def test_circuit_closes_after_ttl_and_the_next_turn_probes_flue(monkeypatch):
    """Fake clock — no sleeps. After the TTL the next turn re-probes flue."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)
    client = _refused_httpx(monkeypatch)

    now = {"t": 1000.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await bd.brain_oneshot("one", "s1", "jason")
    assert client.calls == 1

    now["t"] += 29.0  # still inside the cooldown
    await bd.brain_oneshot("two", "s1", "jason")
    assert client.calls == 1, "cooldown still open — flue must not be dialled"

    now["t"] += 2.0  # TTL lapsed
    await bd.brain_oneshot("three", "s1", "jason")
    assert client.calls == 2, "the first turn past the TTL is the probe"


@pytest.mark.asyncio
async def test_recovered_flue_closes_the_circuit(monkeypatch):
    """A successful probe closes the breaker: flue serves subsequent turns."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    core_calls = _recording_core(monkeypatch)
    client = _refused_httpx(monkeypatch)

    now = {"t": 500.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await bd.brain_oneshot("down", "s1", "jason")
    assert len(core_calls) == 1

    # Sidecar comes back; TTL lapses; the probe succeeds.
    async def healthy_flue(msg, sid, uid="", **kw):
        return f"flue:{msg}"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", healthy_flue)
    now["t"] += 31.0

    assert await bd.brain_oneshot("up", "s1", "jason") == "flue:up"
    assert await bd.brain_oneshot("again", "s1", "jason") == "flue:again"
    assert len(core_calls) == 1, "core is not used once flue is healthy again"
    assert client.calls == 1


# ── (4) THE REPLAY INVARIANT — never re-dispatch a turn that spoke ──────────


@pytest.mark.asyncio
async def test_replay_invariant_mid_stream_failure_after_first_token_never_failovers(
    monkeypatch, caplog
):
    """LOAD-BEARING: a turn that already streamed output must NEVER be replayed.

    The panel speaks each delta as it arrives, so re-dispatching a turn whose
    first token already went out makes Zoe say the reply twice — and re-runs
    whatever tools/writes the sidecar already executed. A transport failure
    AFTER the first delta ends the turn; it is surfaced in the log, never
    retried on another lane.
    """
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)  # any core dispatch fails this test

    async def dies_after_first_token(msg, sid, uid="", **kw):
        yield "Turning the "
        raise zoe_flue_client.FlueTransportError("connection reset mid-stream")

    monkeypatch.setattr(
        zoe_flue_client, "run_flue_brain_streaming", dies_after_first_token
    )

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        chunks = [c async for c in bd.brain_streaming("lights on", "s1", "jason")]

    assert chunks == ["Turning the "], "the partial reply stands; nothing is replayed"
    line = [r.getMessage() for r in caplog.records if "BRAIN_LANE" in r.getMessage()]
    assert len(line) == 1
    assert "lane_served=flue" in line[0] and "outcome=mid_stream_error" in line[0]


@pytest.mark.asyncio
async def test_non_transport_exception_after_text_propagates_and_never_failovers(
    monkeypatch,
):
    """The wrapper only ever handles FlueTransportError — it swallows nothing
    else, and it certainly does not turn an unknown mid-stream error into a
    silent second delivery of the same turn."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    async def dies_after_first_token(msg, sid, uid="", **kw):
        yield "partial"
        raise RuntimeError("model exploded")

    monkeypatch.setattr(
        zoe_flue_client, "run_flue_brain_streaming", dies_after_first_token
    )

    seen: list[str] = []
    with pytest.raises(RuntimeError, match="model exploded"):
        async for c in bd.brain_streaming("hi", "s1", "jason"):
            seen.append(c)
    assert seen == ["partial"]


# ── (5) model-level failures must NOT failover ──────────────────────────────


@pytest.mark.asyncio
async def test_http_status_error_does_not_failover(monkeypatch):
    """A 500 means the sidecar is UP and RAN the turn — retrying would
    double-run its tools/writes. Canned sentinel, no lane hop."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    class _ErrClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _FakeResponse({"error": "boom"}, status_code=500)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _ErrClient)

    assert await bd.brain_oneshot("hi", "s1", "jason") == zoe_flue_client._FALLBACK_TEXT


@pytest.mark.asyncio
async def test_read_timeout_does_not_failover(monkeypatch):
    """A slow generation is the sidecar WORKING, not an unreachable sidecar."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    class _SlowClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            import httpx

            raise httpx.ReadTimeout("generation took too long")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _SlowClient)

    assert await bd.brain_oneshot("hi", "s1", "jason") == zoe_flue_client._FALLBACK_TEXT


@pytest.mark.asyncio
async def test_empty_200_does_not_failover(monkeypatch):
    """An empty successful result is a model-level miss — the turn ran."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    class _EmptyClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _FakeResponse({"result": {"text": ""}})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _EmptyClient)

    assert await bd.brain_oneshot("hi", "s1", "jason") == zoe_flue_client._FALLBACK_TEXT


@pytest.mark.asyncio
async def test_transport_failure_does_not_open_the_circuit_when_flag_is_off(monkeypatch):
    """Flag OFF touches no breaker state — a later flip starts from closed."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)
    _refused_httpx(monkeypatch)

    await bd.brain_oneshot("hi", "s1", "jason")
    assert bd._circuit_open() is False


# ── (6) CONCURRENT turns: the breaker must not be clobbered ─────────────────
#
# Turns overlap (voice + chat + a panel timer all land on one worker), and the
# breaker's mutations run AFTER their own await, so two in-flight turns write
# it in either order. These are the direct controls for the compare-and-set:
# a turn may only apply the outcome of the state it OBSERVED. Deterministic —
# fake clock, explicit events, no sleeps.


def _gated_flue(monkeypatch, script):
    """Patch ``run_flue_brain`` with a per-message script.

    ``script[msg]`` is either a reply string, an exception to raise, or an
    ``asyncio.Event`` pair ``(entered, release, outcome)`` for a turn that must
    be parked mid-flight while another turn runs.
    """
    import zoe_flue_client

    async def fake_flue(msg, sid, uid="", **kw):
        item = script[msg]
        if isinstance(item, tuple):
            entered, release, outcome = item
            entered.set()
            await release.wait()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", fake_flue)


@pytest.mark.asyncio
async def test_stale_success_must_not_erase_a_newer_open(monkeypatch):
    """THE RACE: turn A (in flight, breaker closed) succeeds AFTER turn B's
    transport failure armed a fresh cooldown. An unconditional close would
    erase B's newer open and the next turn pays another failed connect."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 100.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    entered, release = asyncio.Event(), asyncio.Event()
    _gated_flue(
        monkeypatch,
        {
            "slow-ok": (entered, release, "flue:slow-ok"),
            "fails": zoe_flue_client.FlueTransportError("[Errno 111] refused"),
        },
    )

    slow = asyncio.create_task(bd.brain_oneshot("slow-ok", "s1", "jason"))
    await entered.wait()  # A is inside flue, having observed a CLOSED breaker
    assert bd._circuit_open() is False

    assert await bd.brain_oneshot("fails", "s2", "jason") == "core answered"
    assert bd._circuit_open() is True, "B's failure arms the cooldown"

    release.set()
    assert await slow == "flue:slow-ok", "A's own turn is unaffected"

    assert bd._circuit_open() is True, (
        "a turn that started BEFORE the open must not close it — "
        "stale success erased the newer cooldown"
    )


@pytest.mark.asyncio
async def test_two_turns_crossing_the_ttl_produce_exactly_one_probe(monkeypatch):
    """Half-open is a claim, not a free-for-all: concurrent turns crossing the
    same lapsed TTL must not both dial a sidecar that is probably still down.

    The second turn is run WHILE the probe is parked inside the flue call —
    a probe that returns before the next turn starts proves nothing about
    concurrency (the fakes in the sequential tests never suspend).
    """
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    core_calls = _recording_core(monkeypatch)

    now = {"t": 200.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    dials: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def fake_flue(msg, sid, uid="", **kw):
        dials.append(msg)
        if msg == "probe":
            entered.set()
            await release.wait()
        raise zoe_flue_client.FlueTransportError("[Errno 111] refused")

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", fake_flue)

    await bd.brain_oneshot("open-it", "s1", "jason")  # arms the cooldown
    assert dials == ["open-it"]

    now["t"] += 31.0  # TTL lapsed
    probe = asyncio.create_task(bd.brain_oneshot("probe", "s2", "jason"))
    await entered.wait()  # the probe claimed the lapse and is IN FLIGHT
    assert dials == ["open-it", "probe"]

    assert await bd.brain_oneshot("concurrent", "s3", "jason") == "core answered"
    assert dials == ["open-it", "probe"], (
        "a second turn crossing the SAME lapse must take the fallback lane, not "
        f"pile another failed connect onto the dead sidecar (dials={dials})"
    )

    release.set()
    assert await probe == "core answered"
    assert len(core_calls) == 3, "every turn is still answered"


@pytest.mark.asyncio
async def test_stale_failure_must_not_reopen_after_a_newer_probe_closed_it(monkeypatch):
    """The mirror case: an older turn's transport failure lands after a newer
    half-open probe already proved flue healthy. The fresher evidence wins —
    the stale failure must not put a healthy lane back in the penalty box."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 300.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    entered, release = asyncio.Event(), asyncio.Event()
    _gated_flue(
        monkeypatch,
        {
            "slow-fail": (
                entered,
                release,
                zoe_flue_client.FlueTransportError("[Errno 111] refused"),
            ),
            "opener": zoe_flue_client.FlueTransportError("[Errno 111] refused"),
            "probe": "flue:probe",
        },
    )

    slow = asyncio.create_task(bd.brain_oneshot("slow-fail", "s1", "jason"))
    await entered.wait()  # A observed a CLOSED breaker

    await bd.brain_oneshot("opener", "s2", "jason")  # arms the cooldown
    assert bd._circuit_open() is True

    now["t"] += 31.0
    assert await bd.brain_oneshot("probe", "s3", "jason") == "flue:probe"
    assert bd._circuit_open() is False, "a successful probe closes the breaker"

    release.set()
    assert await slow == "core answered", "A still fails over — its own turn is retried"

    assert bd._circuit_open() is False, (
        "a failure from a turn that started two states ago must not re-arm the "
        "cooldown against a sidecar a newer probe just proved healthy"
    )


@pytest.mark.asyncio
async def test_concurrent_probe_success_still_closes_for_everyone(monkeypatch):
    """Guard against over-correcting: the compare-and-set must not strand the
    breaker OPEN. A probe that observed the open state does close it."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 400.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    import zoe_flue_client

    _gated_flue(
        monkeypatch,
        {
            "down": zoe_flue_client.FlueTransportError("[Errno 111] refused"),
            "up": "flue:up",
        },
    )

    await bd.brain_oneshot("down", "s1", "jason")
    assert bd._circuit_open() is True
    now["t"] += 31.0
    assert await bd.brain_oneshot("up", "s2", "jason") == "flue:up"
    assert bd._circuit_open() is False
    assert await bd.brain_oneshot("up", "s3", "jason") == "flue:up"


# ── (7) the transport CLASSIFIER matrix — what may and may not be retried ────
#
# This predicate is the whole trigger: everything it calls transport-class gets
# a second dispatch. Pin both directions explicitly, because the failure mode
# is silent — a widening of httpx's class tree turning read timeouts retryable
# would double-run turns the sidecar is still executing (#1137).


def _httpx_status_error():
    import httpx

    return httpx.HTTPStatusError("500 boom", request=None, response=None)


def _transport_positives():
    import httpx

    return [
        ("httpx.ConnectError", httpx.ConnectError("[Errno 111] Connection refused")),
        ("httpx.ConnectTimeout", httpx.ConnectTimeout("connect timed out")),
        ("ConnectionRefusedError", ConnectionRefusedError("refused")),
        ("ConnectionResetError", ConnectionResetError("reset by peer")),
        ("OSError:ECONNREFUSED", OSError(errno.ECONNREFUSED, "Connection refused")),
        ("OSError:ECONNRESET", OSError(errno.ECONNRESET, "Connection reset by peer")),
        ("OSError:EHOSTUNREACH", OSError(errno.EHOSTUNREACH, "No route to host")),
        ("OSError:ENETUNREACH", OSError(errno.ENETUNREACH, "Network is unreachable")),
    ]


def _transport_negatives():
    import httpx

    return [
        # The sidecar ANSWERED: it is up and it ran the turn.
        ("httpx.HTTPStatusError", _httpx_status_error()),
        # Slow generation / socket-level read-write: the turn is EXECUTING.
        ("httpx.ReadTimeout", httpx.ReadTimeout("generation took too long")),
        ("httpx.WriteTimeout", httpx.WriteTimeout("write timed out")),
        ("httpx.PoolTimeout", httpx.PoolTimeout("no free connection")),
        ("httpx.ReadError", httpx.ReadError("stream broke")),
        # Body-level failures — a response existed to fail on.
        ("httpx.DecodingError", httpx.DecodingError("bad body")),
        ("json.JSONDecodeError", json.JSONDecodeError("undecodable", "{", 0)),
        ("ValueError", ValueError("not json")),
        # Unrelated OSError errnos, and anything unclassified: fail toward NOT
        # retrying. A bare TimeoutError carries no errno, so it stays out —
        # deliberately conservative, a retry is the expensive mistake.
        ("OSError:ENOENT", OSError(errno.ENOENT, "No such file or directory")),
        ("OSError:EPIPE", OSError(errno.EPIPE, "Broken pipe")),
        ("TimeoutError", TimeoutError("timed out")),
        ("RuntimeError", RuntimeError("model exploded")),
    ]


@pytest.mark.parametrize(
    "exc",
    [pytest.param(e, id=name) for name, e in _transport_positives()],
)
def test_is_transport_failure_positive_matrix(exc):
    import zoe_flue_client

    assert zoe_flue_client._is_transport_failure(exc) is True


@pytest.mark.parametrize(
    "exc",
    [pytest.param(e, id=name) for name, e in _transport_negatives()],
)
def test_is_transport_failure_negative_matrix(exc):
    import zoe_flue_client

    assert zoe_flue_client._is_transport_failure(exc) is False, (
        "this failure class means the sidecar RAN the turn — re-dispatching it "
        "double-runs the sidecar's tools/writes"
    )


# ── (8) replay control through the REAL client collector ────────────────────


class _MidStreamClient:
    """httpx stand-in: the NDJSON stream yields text, then the socket dies."""

    calls: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    class _Resp:
        # status_code is read before raise_for_status() on the streaming path
        # (the wire-2 HTTP 400 mirror-diagnosis), so a real-shaped double needs it.
        status_code = 200
        headers = {"content-type": "application/x-ndjson"}

        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_lines(self):
            import httpx

            yield json.dumps("Turning the ")
            yield json.dumps("lights on.")
            raise httpx.ConnectError("[Errno 104] Connection reset by peer")

    def stream(self, method, url, content=b"", headers=None):
        type(self).calls.append(url)
        return self._Resp()

    async def post(self, url, content=b"", headers=None):  # pragma: no cover
        type(self).calls.append(url)
        raise AssertionError("a re-POST would RE-RUN a turn that already spoke")


@pytest.mark.asyncio
async def test_one_shot_collector_never_surfaces_transport_error_after_deltas(
    monkeypatch,
):
    """The replay invariant, exercised through the REAL ``run_flue_brain``
    collector rather than a stubbed generator: once deltas have been collected,
    a transport-class death cannot become a ``FlueTransportError``, so the
    failover wrapper is never even offered the chance to re-dispatch."""
    import httpx

    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    _boom_core(monkeypatch)  # any re-dispatch fails this test
    _MidStreamClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _MidStreamClient)

    # (a) the client itself: opted IN to raising, it still must not raise.
    text = await zoe_flue_client.run_flue_brain(
        "lights on", "s1", "jason", raise_transport_errors=True
    )
    assert text == "Turning the lights on."

    # (b) end to end through dispatch: partial reply stands, no lane hop.
    _MidStreamClient.calls = []
    assert await bd.brain_oneshot("lights on", "s1", "jason") == "Turning the lights on."
    assert len(_MidStreamClient.calls) == 1, "exactly one dispatch, no re-POST"
    assert bd._circuit_open() is False, "a turn that was SERVED never opens the breaker"


# ── (9) dispatch-internal kwargs are not caller-settable ────────────────────


@pytest.mark.asyncio
async def test_caller_supplied_raise_transport_errors_kwarg_is_ignored(monkeypatch):
    """``raise_transport_errors`` is owned by the failover wrapper. A caller
    forwarding it inside ``**kwargs`` used to be a TypeError on the flag-ON
    path (multiple values) and a raising client on the flag-OFF path."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    _recording_core(monkeypatch)
    _refused_httpx(monkeypatch)

    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    assert await bd.brain_oneshot(
        "hi", "s1", "jason", **{"raise_transport_errors": True}
    ) == "core answered"
    chunks = [
        c
        async for c in bd.brain_streaming(
            "hi", "s1", "jason", **{"raise_transport_errors": True}
        )
    ]
    assert chunks == ["core answered"]

    # Flag OFF: unchanged sentinel — the exception must not escape into a route.
    bd.reset_failover_state()
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    assert await bd.brain_oneshot(
        "hi", "s1", "jason", **{"raise_transport_errors": True}
    ) == zoe_flue_client._FALLBACK_TEXT


def test_flag_default_is_off_and_cooldown_default_is_documented(monkeypatch):
    import brain_dispatch as bd

    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    assert bd.failover_enabled() is False
    for truthy in ("1", "true", "yes", "on", "ON"):
        monkeypatch.setenv("ZOE_BRAIN_FAILOVER", truthy)
        assert bd.failover_enabled() is True
    for falsy in ("0", "false", "off", "", "banana"):
        monkeypatch.setenv("ZOE_BRAIN_FAILOVER", falsy)
        assert bd.failover_enabled() is False

    monkeypatch.delenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", raising=False)
    assert bd._cooldown_s() == 45.0
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "not-a-number")
    assert bd._cooldown_s() == 45.0, "unparseable falls back to the default"


# ── (10) wire 2: the failover invariants hold on the ZOE_FLUE_WIRE=2 paths ───
#
# The failover plumbing was written against the pre-wire-2 client, whose ONLY
# routes were "streaming" and "wait=result". Main's #1637 work added two more
# (the wire-2 aggregated turn, and a wire-2 pre-admission branch inside the
# streaming path) — so every invariant below has to be re-proved there, not
# assumed to carry over. Each test is a negative control for a specific way the
# reconciliation could have been got wrong.


def _wire2(monkeypatch, *, stream_enabled: bool):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "2")
    if stream_enabled:
        monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    else:
        monkeypatch.delenv("ZOE_FLUE_STREAM_ENABLED", raising=False)


class _StreamResp:
    """httpx streaming-response double with a scripted body."""

    def __init__(
        self,
        *,
        status_code=200,
        content_type="application/x-ndjson",
        lines=(),
        body=b"",
        raise_at_end=None,
    ):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._lines = list(lines)
        self._body = body
        self._raise_at_end = raise_at_end

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                str(self.status_code), request=None, response=None
            )

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._raise_at_end is not None:
            raise self._raise_at_end


def _scripted_stream_client(monkeypatch, response=None, connect_error=None):
    """httpx.AsyncClient double: stream() replays ``response`` (or refuses)."""
    import httpx

    state = {"stream_calls": 0, "post_calls": 0}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, content=b"", headers=None):
            state["stream_calls"] += 1
            if connect_error is not None:
                raise connect_error
            return response

        async def post(self, url, content=b"", headers=None):
            # Wire 2 has no ?wait=result call — reaching this is the bug the
            # client's structural guard exists to prevent.
            state["post_calls"] += 1
            raise AssertionError("wire 2 must never send a ?wait=result POST")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return state


@pytest.mark.asyncio
async def test_wire2_streaming_transport_failure_still_failovers(monkeypatch):
    """NEGATIVE CONTROL for the merge's central ordering decision.

    Main added a wire-2 pre-admission branch that yields the canned sentinel
    because 2.x has no ``?wait=result`` to fall back to. If the failover raise is
    ordered AFTER it, every ZOE_FLUE_WIRE=2 turn silently loses failover — the
    wire the 2.x sidecar actually speaks. This test goes RED in that ordering.
    """
    import brain_dispatch as bd
    import httpx

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=True)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    core_calls = _recording_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch, connect_error=httpx.ConnectError("[Errno 111] Connection refused")
    )

    chunks = [c async for c in bd.brain_streaming("hi", "s1", "jason")]

    assert chunks == ["core answered"], "the healthy core lane serves the turn"
    assert len(core_calls) == 1, "exactly one retry"
    assert bd._circuit_open() is True, "a transport failure arms the breaker"


@pytest.mark.asyncio
async def test_wire2_aggregated_transport_failure_still_failovers(monkeypatch):
    """The wire-2 NON-streaming turn is a whole separate function
    (``_run_turn_aggregated_wire2``) that did not exist when the failover was
    written — it needs its own pre-admission raise, or the default wire-2
    deployment (streaming off) has no failover at all."""
    import brain_dispatch as bd
    import httpx

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=False)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    core_calls = _recording_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch, connect_error=httpx.ConnectError("[Errno 111] Connection refused")
    )

    assert await bd.brain_oneshot("hi", "s1", "jason") == "core answered"
    assert len(core_calls) == 1

    chunks = [c async for c in bd.brain_streaming("hi", "s2", "jason")]
    assert chunks == ["core answered"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_enabled", [False, True])
async def test_wire2_http_400_is_a_refusal_not_a_transport_failure(
    monkeypatch, stream_enabled
):
    """THE distinction the reconciliation turns on.

    A 400 from a 1.x sidecar that cannot parse the wire-2 body means the sidecar
    ANSWERED — the turn was refused, not unreachable. That is not evidence the
    turn did not run somewhere, it is a wire misconfiguration, and failing over
    would answer a config error by quietly moving lanes. Canned sentinel, no
    lane hop, breaker untouched.
    """
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=stream_enabled)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)  # any lane hop fails this test
    _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(status_code=400, body=b'{"error":"message required"}'),
    )

    chunks = [c async for c in bd.brain_streaming("hi", "s1", "jason")]

    assert chunks == [zoe_flue_client._FALLBACK_TEXT]
    assert bd._circuit_open() is False, "a refusal must not arm the breaker"


@pytest.mark.asyncio
async def test_wire2_aggregated_death_after_admission_never_failovers(monkeypatch):
    """Admission is ownership. Once the 2.x sidecar returns 2xx it is EXECUTING
    the turn (writes included), so a stream death afterwards must never be
    re-dispatched even though the exception is transport-class."""
    import brain_dispatch as bd
    import httpx

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=False)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)
    state = _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(
            lines=[json.dumps("Turning the lights on.")],
            raise_at_end=httpx.ConnectError("[Errno 104] Connection reset by peer"),
        ),
    )

    out = await bd.brain_oneshot("lights on", "s1", "jason")

    assert out == "Turning the lights on.", "the partial reply stands"
    assert state["stream_calls"] == 1, "exactly one dispatch, no re-POST"
    assert bd._circuit_open() is False, "a served turn never opens the breaker"


@pytest.mark.asyncio
async def test_wire2_aggregated_admitted_then_death_before_text_never_failovers(
    monkeypatch,
):
    """The ``admitted`` guard, isolated: 2xx received, then the socket dies
    BEFORE any text. Nothing was yielded and nothing accumulated, so the
    exception alone looks exactly like a pre-admission refusal — but the sidecar
    already owns the turn, and re-dispatching would double-run its writes.
    Classification by exception class is not enough; admission is the gate.
    """
    import brain_dispatch as bd
    import httpx
    import zoe_flue_client

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=False)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)
    state = _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(
            lines=[],
            raise_at_end=httpx.ConnectError("[Errno 104] Connection reset by peer"),
        ),
    )

    out = await bd.brain_oneshot("hi", "s1", "jason")

    assert out == zoe_flue_client._FALLBACK_TEXT
    assert state["stream_calls"] == 1, "no re-dispatch of an admitted turn"
    assert bd._circuit_open() is False


@pytest.mark.asyncio
async def test_wire2_flag_off_keeps_the_canned_sentinel(monkeypatch):
    """Flag-off byte-identity, re-proved on the wire-2 paths: with
    ZOE_BRAIN_FAILOVER unset a refused 2.x sidecar still yields the sentinel and
    never touches the core lane, exactly as before this branch."""
    import brain_dispatch as bd
    import httpx
    import zoe_flue_client

    _flue_backend(monkeypatch)
    _wire2(monkeypatch, stream_enabled=False)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch, connect_error=httpx.ConnectError("[Errno 111] Connection refused")
    )

    assert await bd.brain_oneshot("hi", "s1", "jason") == zoe_flue_client._FALLBACK_TEXT
    assert bd._circuit_open() is False


# ── (11) the lane log survives a consumer that walks away (Codex P2 a) ──────


def _lane_lines(caplog):
    return [r.getMessage() for r in caplog.records if "BRAIN_LANE" in r.getMessage()]


@pytest.mark.asyncio
async def test_lane_log_lands_when_the_consumer_disconnects_after_one_chunk(
    monkeypatch, caplog
):
    """NEGATIVE CONTROL for "emit the lane record before exposing chunks".

    A streaming caller can disconnect after the first delta — a closed tab, a
    cancelled voice turn — which throws GeneratorExit at the yield and skips
    every statement after it. With the success line living after the loop, such
    a turn produced NO BRAIN_LANE record at all, and interrupted turns are
    ordinary runtime turns: the operator's only lane-attribution instrument goes
    silent for them. Cleanup must pay the record the turn owes.
    """
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    async def long_stream(msg, sid, uid="", **kw):
        yield "first "
        yield "second "
        yield "third"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", long_stream)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        gen = bd.brain_streaming("hi", "sess-cut", "jason")
        assert await gen.__anext__() == "first "
        await gen.aclose()  # the consumer walks away mid-stream

    lines = _lane_lines(caplog)
    assert len(lines) == 1, f"exactly one lane line per turn, got {lines}"
    assert "lane_attempted=flue" in lines[0]
    assert "outcome=interrupted" in lines[0], "and it is labelled honestly"
    assert "session=sess-cut" in lines[0]


@pytest.mark.asyncio
async def test_interrupted_turn_logs_exactly_once_not_twice(monkeypatch, caplog):
    """The cleanup emitter must not DOUBLE-log a turn that already reported.
    'Exactly one line per turn' is what makes the signal countable."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    async def short_stream(msg, sid, uid="", **kw):
        yield "only"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", short_stream)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        gen = bd.brain_streaming("hi", "s1", "jason")
        assert [c async for c in gen] == ["only"]
        await gen.aclose()  # closing an exhausted generator adds nothing

    assert len(_lane_lines(caplog)) == 1


# ── (12) a failed flue turn is never logged as a success (Codex P2 b) ───────


def _oneshot_client(monkeypatch, *, response=None, exc=None):
    """httpx double for the wire-1 ?wait=result path."""
    import httpx

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, *a, **k):
            if exc is not None:
                raise exc
            return response

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_healthy_turn_still_logs_outcome_ok(monkeypatch, caplog):
    """The control for the three tests below: a genuinely good turn must keep
    reporting ok, or the new labels are just pessimism rather than truth."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)
    _oneshot_client(monkeypatch, response=_FakeResponse({"result": {"text": "hello"}}))

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        assert await bd.brain_oneshot("hi", "s1", "jason") == "hello"

    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=ok" in lines[0] and "lane_served=flue" in lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,kind",
    [
        ("http_500", "status"),
        ("read_timeout", "timeout"),
        ("empty_200", "empty"),
    ],
)
async def test_failed_flue_sentinel_turn_is_not_logged_as_ok(
    monkeypatch, caplog, label, kind
):
    """THE FINDING: an HTTP error, a read timeout and an empty 200 all end as
    the canned sentinel and a normally-terminated stream, so the lane line said
    ``outcome=ok`` — success reported for precisely the failed brain turns an
    operator greps BRAIN_LANE to diagnose. Behaviour is unchanged (still the
    sentinel, still no lane hop); only the label becomes truthful.
    """
    import brain_dispatch as bd
    import httpx
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)  # the no-retry behaviour is pinned unchanged
    if kind == "status":
        _oneshot_client(monkeypatch, response=_FakeResponse({}, status_code=500))
    elif kind == "timeout":
        _oneshot_client(monkeypatch, exc=httpx.ReadTimeout("generation too long"))
    else:
        _oneshot_client(monkeypatch, response=_FakeResponse({"result": {"text": ""}}))

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        out = await bd.brain_oneshot("hi", "s1", "jason")

    assert out == zoe_flue_client._FALLBACK_TEXT, "behaviour unchanged"
    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=ok" not in lines[0], f"{label} must not report success: {lines[0]}"
    assert "outcome=fallback" in lines[0]


@pytest.mark.asyncio
async def test_post_admission_stream_death_after_text_logs_error_not_ok(
    monkeypatch, caplog
):
    """The case a sentinel-string comparison could never catch: real text WAS
    served, then the stream died. The turn is truncated, not successful."""
    import brain_dispatch as bd
    import httpx

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    _boom_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(
            lines=[json.dumps("Turning the "), json.dumps("lights on.")],
            raise_at_end=httpx.ConnectError("[Errno 104] Connection reset by peer"),
        ),
    )

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        chunks = [c async for c in bd.brain_streaming("lights on", "s1", "jason")]

    assert chunks == ["Turning the ", "lights on."], "the partial reply stands"
    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=ok" not in lines[0]
    assert "outcome=error" in lines[0]


@pytest.mark.asyncio
async def test_stream_error_terminal_without_text_logs_fallback_not_ok(
    monkeypatch, caplog
):
    """The sidecar OWNED and reported the failure ({"error": ...}); the client
    serves the sentinel and ends the stream normally. Still not a success."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    _boom_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(lines=[json.dumps({"error": "model OOM"})]),
    )

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        chunks = [c async for c in bd.brain_streaming("hi", "s1", "jason")]

    assert chunks == [zoe_flue_client._FALLBACK_TEXT]
    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=ok" not in lines[0]
    assert "outcome=fallback" in lines[0]
    assert "model OOM" in lines[0], "the reason names what actually failed"


@pytest.mark.asyncio
async def test_outcome_label_never_changes_dispatch(monkeypatch):
    """Labels only. A client that reports nothing at all (an older module, a
    test double) must still dispatch identically — the label degrades, the turn
    does not."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)

    async def silent_client(msg, sid, uid="", **kw):
        assert "outcome_sink" in kw, "the sink is offered"
        return "answered without reporting"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", silent_client)

    assert await bd.brain_oneshot("hi", "s1", "jason") == "answered without reporting"


# ── (13) flag OFF: the DEFAULT deployment's lane line must be truthful too ───
#
# Everything above proves the label on the ZOE_BRAIN_FAILOVER=1 path. The flag
# is default OFF, so the deployment an operator actually greps was the one still
# logging `outcome=dispatched` — emitted BEFORE the request ran, and therefore
# identical for a healthy turn, an HTTP 500, a read timeout, an empty 200 and a
# truncated stream. These pin that the OFF path reads the same client verdict,
# while its DISPATCH stays byte-identical (one call, no raise_transport_errors,
# no lane hop, breaker untouched).


@pytest.mark.asyncio
async def test_flag_off_healthy_turn_logs_ok_not_dispatched(monkeypatch, caplog):
    """The control: a genuinely good default-path turn reports ok."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)
    _oneshot_client(monkeypatch, response=_FakeResponse({"result": {"text": "hello"}}))

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        assert await bd.brain_oneshot("hi", "s1", "jason") == "hello"

    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=ok" in lines[0] and "lane_served=flue" in lines[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,kind",
    [
        ("http_500", "status"),
        ("read_timeout", "timeout"),
        ("empty_200", "empty"),
    ],
)
async def test_flag_off_failed_flue_turn_is_not_logged_as_dispatched(
    monkeypatch, caplog, label, kind
):
    """THE ROUND-9 FINDING: with the flag unset these three all ended as the
    canned sentinel behind an `outcome=dispatched` line written before the
    request even ran — the default deployment's BRAIN_LANE record could not
    distinguish a healthy turn from a failed one. Behaviour is unchanged (still
    the sentinel, still no lane hop, breaker still untouched); only the label
    becomes the client's verdict.
    """
    import brain_dispatch as bd
    import httpx
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)  # the no-failover behaviour is pinned unchanged
    if kind == "status":
        _oneshot_client(monkeypatch, response=_FakeResponse({}, status_code=500))
    elif kind == "timeout":
        _oneshot_client(monkeypatch, exc=httpx.ReadTimeout("generation too long"))
    else:
        _oneshot_client(monkeypatch, response=_FakeResponse({"result": {"text": ""}}))

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        out = await bd.brain_oneshot("hi", "s1", "jason")

    assert out == zoe_flue_client._FALLBACK_TEXT, "behaviour unchanged"
    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert (
        "outcome=dispatched" not in lines[0]
    ), f"{label} still reports the pre-request label: {lines[0]}"
    assert "outcome=ok" not in lines[0]
    assert "outcome=fallback" in lines[0]
    assert bd._circuit_open() is False, "flag OFF must not touch breaker state"


@pytest.mark.asyncio
async def test_flag_off_streaming_death_after_text_logs_error(monkeypatch, caplog):
    """The streaming half, on the case a sentinel comparison could never catch:
    real text was served, then the stream died. Truncated, not successful."""
    import brain_dispatch as bd
    import httpx

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    _boom_core(monkeypatch)
    _scripted_stream_client(
        monkeypatch,
        response=_StreamResp(
            lines=[json.dumps("Turning the "), json.dumps("lights on.")],
            raise_at_end=httpx.ConnectError("[Errno 104] Connection reset by peer"),
        ),
    )

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        chunks = [c async for c in bd.brain_streaming("lights on", "s1", "jason")]

    assert chunks == ["Turning the ", "lights on."], "the partial reply stands"
    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=error" in lines[0] and "outcome=ok" not in lines[0]


@pytest.mark.asyncio
async def test_flag_off_streaming_consumer_disconnect_still_logs_once(
    monkeypatch, caplog
):
    """The every-path contract applies to the default deployment as well."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)

    async def long_stream(msg, sid, uid="", **kw):
        yield "first "
        yield "second "

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", long_stream)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        gen = bd.brain_streaming("hi", "sess-off-cut", "jason")
        assert await gen.__anext__() == "first "
        await gen.aclose()

    lines = _lane_lines(caplog)
    assert len(lines) == 1, f"exactly one lane line per turn, got {lines}"
    assert "outcome=interrupted" in lines[0]
    assert "session=sess-off-cut" in lines[0]
    assert bd._circuit_open() is False, "flag OFF never arms or clears the breaker"


@pytest.mark.asyncio
async def test_flag_off_dispatch_is_unchanged_by_the_label(monkeypatch):
    """The guard on the fix itself: the OFF path may READ a verdict, never ACT
    on one. It offers the sink, withholds `raise_transport_errors` (which is
    what would let a transport error escape into a route with nothing to catch
    it), makes exactly one client call, and never hops lanes."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)  # any lane hop fails this test

    seen: list[dict] = []

    async def recording_client(msg, sid, uid="", **kw):
        seen.append(kw)
        return "answered"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", recording_client)

    assert await bd.brain_oneshot("hi", "s1", "jason") == "answered"
    assert len(seen) == 1, "exactly one dispatch"
    assert "outcome_sink" in seen[0], "the sink is offered"
    assert "raise_transport_errors" not in seen[0], (
        "the OFF path must never opt into raising — FlueTransportError would "
        "escape into a route that has nothing to catch it"
    )


@pytest.mark.asyncio
async def test_flag_off_streaming_dispatch_is_unchanged_by_the_label(monkeypatch):
    """The same guard on the STREAMING half. The one-shot spy above cannot see
    this path at all, and it is the one the panel speaks from."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)  # any lane hop fails this test

    seen: list[dict] = []

    def recording_stream(msg, sid, uid="", **kw):
        seen.append(kw)

        async def _gen():
            yield "a"
            yield "b"

        return _gen()

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", recording_stream)

    assert [c async for c in bd.brain_streaming("hi", "s1", "jason")] == ["a", "b"]
    assert len(seen) == 1, "exactly one dispatch"
    assert "outcome_sink" in seen[0], "the sink is offered"
    assert "raise_transport_errors" not in seen[0], (
        "the OFF path must never opt into raising — FlueTransportError would "
        "escape into a route that has nothing to catch it"
    )
    assert bd._circuit_open() is False, "flag OFF must not touch breaker state"


@pytest.mark.asyncio
async def test_flag_off_silent_client_still_dispatches(monkeypatch):
    """Labels degrade, turns do not: a client that reports no verdict (an older
    module, a test double) is dispatched to identically."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    _boom_core(monkeypatch)

    async def silent(msg, sid, uid="", **kw):
        return "answered without reporting"

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", silent)

    assert await bd.brain_oneshot("hi", "s1", "jason") == "answered without reporting"


# ── (14) a CANCELLED one-shot owes a lane line too ──────────────────────────
#
# The streaming wrapper's try/finally has covered the disconnect case since
# round 8. One-shots have the same hole and no generator to be closed: the
# LiveKit lane awaits `brain_oneshot` under `asyncio.wait_for`, and chat
# disconnect cleanup cancels its task — both exit the await without reaching any
# terminal branch, so an ordinary interrupted turn logged NOTHING.


def _parked_flue_oneshot(monkeypatch):
    """Patch ``run_flue_brain`` with a turn that never finishes."""
    import zoe_flue_client

    entered, release = asyncio.Event(), asyncio.Event()

    async def parked(msg, sid, uid="", **kw):
        entered.set()
        await release.wait()
        return "never returned"  # pragma: no cover

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", parked)
    return entered, release


@pytest.mark.asyncio
@pytest.mark.parametrize("failover", ["1", None], ids=["flag_on", "flag_off"])
async def test_cancelled_one_shot_logs_interrupted(monkeypatch, caplog, failover):
    """NEGATIVE CONTROL for the one-shot try/finally: remove it and a cancelled
    turn leaves no BRAIN_LANE record at all. Both flag states — the default-off
    path is the one actually running."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    if failover is None:
        monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    else:
        monkeypatch.setenv("ZOE_BRAIN_FAILOVER", failover)
    _boom_core(monkeypatch)
    entered, _release = _parked_flue_oneshot(monkeypatch)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        task = asyncio.create_task(bd.brain_oneshot("hi", "sess-cancel", "jason"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    lines = _lane_lines(caplog)
    assert len(lines) == 1, f"exactly one lane line per turn, got {lines}"
    assert "lane_attempted=flue" in lines[0]
    assert "outcome=interrupted" in lines[0]
    assert "reason=caller_cancelled" in lines[0]
    assert "session=sess-cancel" in lines[0]


@pytest.mark.asyncio
async def test_wait_for_timeout_on_a_one_shot_logs_interrupted(monkeypatch, caplog):
    """The LiveKit lane's real shape: `asyncio.wait_for` cancels the turn from
    the outside. Same hole, same record owed."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    _boom_core(monkeypatch)
    _parked_flue_oneshot(monkeypatch)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                bd.brain_oneshot("hi", "sess-timeout", "jason"), timeout=0.02
            )

    lines = _lane_lines(caplog)
    assert len(lines) == 1
    assert "outcome=interrupted" in lines[0] and "session=sess-timeout" in lines[0]


@pytest.mark.asyncio
async def test_cancelled_one_shot_that_already_reported_does_not_double_log(
    monkeypatch, caplog
):
    """The once-only guard, on the one-shot: a turn cancelled while its FALLBACK
    lane is running already emitted its line. 'Exactly one per turn' is what
    makes the signal countable."""
    import brain_dispatch as bd
    import zoe_core_client
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")

    entered, release = asyncio.Event(), asyncio.Event()

    async def parked_core(msg, sid, uid="", **kw):
        entered.set()
        await release.wait()
        return "never"  # pragma: no cover

    async def refused_flue(msg, sid, uid="", **kw):
        raise zoe_flue_client.FlueTransportError("[Errno 111] refused")

    monkeypatch.setattr(zoe_core_client, "run_zoe_core", parked_core)
    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", refused_flue)

    with caplog.at_level(logging.INFO, logger="brain_dispatch"):
        task = asyncio.create_task(bd.brain_oneshot("hi", "sess-hop", "jason"))
        await entered.wait()  # the failover line is already written
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    lines = _lane_lines(caplog)
    assert len(lines) == 1, f"exactly one lane line per turn, got {lines}"
    assert "outcome=failover" in lines[0], "the turn's real verdict, not the cleanup's"


# ── (15) a probe that PROVED the lane must not leave the breaker armed ──────
#
# The half-open probe re-arms the deadline as it claims the lapse, so the
# breaker is open for the whole probe turn and only the probe's own outcome
# closes it. Reaching `_close_circuit` after the loop is therefore not enough:
# the consumer can disconnect after the first delta, which throws GeneratorExit
# at the yield and skips it — leaving a demonstrably reachable sidecar in the
# penalty box for another full cooldown. The evidence is the yield itself.


async def _arm_the_breaker(bd, monkeypatch):
    """One refused one-shot turn — the breaker's only entry point."""
    import zoe_flue_client

    async def refused(msg, sid, uid="", **kw):
        raise zoe_flue_client.FlueTransportError("[Errno 111] refused")

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", refused)
    await bd.brain_oneshot("open-it", "s1", "jason")
    assert bd._circuit_open() is True


@pytest.mark.asyncio
async def test_probe_that_yielded_then_lost_its_consumer_closes_the_breaker(
    monkeypatch,
):
    """NEGATIVE CONTROL for the yielded_any close: drop it and a probe that
    PROVED flue reachable still costs every following turn a full cooldown on
    the fallback lane."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 700.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await _arm_the_breaker(bd, monkeypatch)
    now["t"] += 31.0  # TTL lapsed — the next streaming turn claims the probe

    release = asyncio.Event()

    async def one_delta_then_park(msg, sid, uid="", **kw):
        yield "first "
        await release.wait()  # pragma: no cover - the consumer leaves first
        yield "never"  # pragma: no cover

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", one_delta_then_park)

    gen = bd.brain_streaming("probe", "s2", "jason")
    assert await gen.__anext__() == "first ", "flue answered — the lane is proven"
    await gen.aclose()  # the consumer walks away mid-stream

    assert bd._circuit_open() is False, (
        "the probe yielded a flue delta, so the lane is demonstrably reachable — "
        "the half-open claim must not survive the consumer's disconnect"
    )


@pytest.mark.asyncio
async def test_probe_cancelled_before_any_delta_leaves_the_breaker_armed(monkeypatch):
    """GUARD THE GUARD: the close is licensed by EVIDENCE, not by cleanup
    running. A consumer that cancels before the first delta proved nothing about
    flue — closing there would hand the next turn a failed connect on the word
    of a turn that never saw an answer."""
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 800.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await _arm_the_breaker(bd, monkeypatch)
    now["t"] += 31.0

    entered, release = asyncio.Event(), asyncio.Event()

    async def never_yields(msg, sid, uid="", **kw):
        entered.set()
        await release.wait()  # the consumer gives up first
        yield "never"  # pragma: no cover

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", never_yields)

    gen = bd.brain_streaming("probe", "s2", "jason")
    first = asyncio.create_task(gen.__anext__())
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await gen.aclose()

    assert (
        bd._circuit_open() is True
    ), "nothing was yielded, so the probe proved nothing — the claim stands"


@pytest.mark.asyncio
async def test_a_stale_yielded_probe_must_not_erase_a_newer_open(monkeypatch):
    """The cleanup close is a compare-and-set like every other breaker mutation,
    and this is the race that proves it. Turn A observes a CLOSED breaker and
    yields a flue delta; turn B's transport failure then arms a fresh cooldown;
    only THEN does A's consumer disconnect. A's evidence is real but STALE — B
    looked at flue more recently — so A's cleanup must not hand the next turn a
    failed connect. An unconditional close in the `finally` reds here.
    """
    import brain_dispatch as bd
    import zoe_flue_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 1000.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    release = asyncio.Event()

    async def one_delta_then_park(msg, sid, uid="", **kw):
        yield "first "
        await release.wait()  # pragma: no cover - the consumer leaves first
        yield "never"  # pragma: no cover

    async def refused(msg, sid, uid="", **kw):
        raise zoe_flue_client.FlueTransportError("[Errno 111] refused")

    monkeypatch.setattr(zoe_flue_client, "run_flue_brain_streaming", one_delta_then_park)
    monkeypatch.setattr(zoe_flue_client, "run_flue_brain", refused)

    gen = bd.brain_streaming("slow-ok", "s1", "jason")
    assert await gen.__anext__() == "first "  # A observed a CLOSED breaker
    assert bd._circuit_open() is False

    assert await bd.brain_oneshot("fails", "s2", "jason") == "core answered"
    assert bd._circuit_open() is True, "B's failure arms the cooldown"

    await gen.aclose()  # A's consumer walks away, carrying stale evidence

    assert bd._circuit_open() is True, (
        "a turn that started BEFORE the open must not close it from cleanup — "
        "stale evidence erased the newer cooldown"
    )


@pytest.mark.asyncio
async def test_cancelled_one_shot_probe_leaves_the_breaker_armed(monkeypatch):
    """The same rule on the one-shot: it yields nothing incrementally, so a
    cancelled await is never evidence that flue answered."""
    import brain_dispatch as bd

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)

    now = {"t": 900.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await _arm_the_breaker(bd, monkeypatch)
    now["t"] += 31.0

    entered, _release = _parked_flue_oneshot(monkeypatch)
    task = asyncio.create_task(bd.brain_oneshot("probe", "s2", "jason"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bd._circuit_open() is True


@pytest.mark.asyncio
async def test_a_disconnected_turn_that_never_dialled_flue_touches_no_breaker_state(
    monkeypatch,
):
    """The other side of the evidence rule: a turn served by the FALLBACK lane
    because the breaker was open must not clear the breaker on its way out, no
    matter how its consumer leaves. It never asked flue anything, so a fallback
    delta must never count as proof — two independent guards hold that (it
    emitted its record before dispatching, and `served_any` is set only inside
    the flue loop)."""
    import brain_dispatch as bd
    import zoe_core_client

    _flue_backend(monkeypatch)
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER", "1")
    monkeypatch.setenv("ZOE_BRAIN_FAILOVER_COOLDOWN_S", "30")
    _recording_core(monkeypatch)  # the arming turn fails over onto this lane

    now = {"t": 1100.0}
    monkeypatch.setattr(bd, "_monotonic", lambda: now["t"])

    await _arm_the_breaker(bd, monkeypatch)

    async def core_stream(msg, sid, uid="", **kw):
        yield "core "
        yield "answered"

    monkeypatch.setattr(zoe_core_client, "run_zoe_core_streaming", core_stream)

    gen = bd.brain_streaming("during-cooldown", "s2", "jason")
    assert await gen.__anext__() == "core "
    await gen.aclose()

    assert (
        bd._circuit_open() is True
    ), "the fallback lane answering says nothing about flue's reachability"
