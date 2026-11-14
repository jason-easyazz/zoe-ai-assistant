"""Tests for the felt scorecard. Synthetic lines and fakes only; no live access.

Hand-run (not CI-wired; deliberately no ci_safe marker):
    flock -x -w 3000 /tmp/zoe-test-lane.lock python3 -m pytest -q -p no:cacheprovider labs/felt-scorecard/test_scorecard.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorecard as sc  # noqa: E402

MEMBER = "member-a"
MSESS = "voice-panel-m1"
MEMBERS = sc.Members(users={MEMBER}, sessions={MSESS})
TZ8 = dt.timezone(dt.timedelta(hours=8))
SINCE = dt.datetime(2026, 11, 5, tzinfo=TZ8)
UNTIL = dt.datetime(2026, 11, 12, tzinfo=TZ8)


def L(ts: str, body: str) -> str:
    return f"2026-11-{ts}+0800 INFO     mod: {body}"


def parsed(raw: list[str]):
    out = []
    for r in raw:
        t = sc.parse_ts(r)
        assert t is not None
        out.append((t, r))
    return out


def test_parse_ts_with_offset_and_window():
    t = sc.parse_ts(L("06T10:00:00", "X"))
    assert t == dt.datetime(2026, 11, 6, 2, 0, tzinfo=dt.timezone.utc)
    assert sc.parse_ts("garbage") is None


def test_harness_split_follows_lane1_rule():
    assert sc.is_harness("FLUE_TOOLS session=x traffic=harness", {"session": "x"})
    # probe and CI turns can run as a real member with no replay session prefix: the class tag is the only signal (Codex on #2424)
    assert sc.is_harness("PROVENANCE_REPLY user=realmember served=1 traffic=probe", {"user": "realmember"})
    assert sc.is_harness("PROVENANCE_REPLY user=realmember served=1 traffic=ci", {"user": "realmember"})
    assert not sc.is_harness("PROVENANCE_REPLY user=realmember served=1 traffic=household", {"user": "realmember"})
    assert not sc.is_harness("PROVENANCE_REPLY user=realmember served=1 nottraffic=probe", {"user": "realmember"})
    assert sc.is_harness("x", {"user": "demo_bar_abc123"})
    assert sc.is_harness("x", {"session": "qa-sweep-1"})
    assert sc.is_harness("x", {"session": "replay-ab12"})
    assert not sc.is_harness("x", {"session": "voice-panel-1", "user": MEMBER})


# --- finding 3: gzip rotations are read; a window the logs do not cover is unmeasured -----------------

def test_gz_rotation_is_read(tmp_path):
    plain = tmp_path / "zoe-data.app.log"
    plain.write_text(L("10T10:00:00", f"FLUE_TOOLS session={MSESS} tools=[] recall_memory=called:no") + "\n")
    with gzip.open(tmp_path / "zoe-data.app.log.1.gz", "wt") as fh:
        fh.write(L("04T09:00:00", "something else") + "\n")
        fh.write(L("06T10:00:00", f"FLUE_TOOLS session={MSESS} tools=[x] recall_memory=called:yes") + "\n")
    logs = sc.load_log_lines(sorted(str(p) for p in tmp_path.iterdir()))
    assert len(logs.lines) == 2
    assert logs.earliest == sc.parse_ts(L("04T09:00:00", ""))
    assert sc.m_tools(logs.lines, SINCE, UNTIL, MEMBERS)["data"]["member"] == {"n": 2, "tool": 1, "recall": 1}


def _logset(lines, first, last=None):
    rot = sc.Rotation("zoe-data.app.log", first=first, last=last or UNTIL, lines=lines)
    return sc.LogSet(lines, [rot])


def test_uncovered_window_makes_every_log_metric_unmeasured():
    lines = parsed([L("08T10:00:00", f"SEAM_CONTINUITY user={MEMBER} matched=False")])
    late = sc.parse_ts(L("07T00:00:00", ""))  # logs start after SINCE
    out = sc.log_section(_logset(lines, late), SINCE, UNTIL, MEMBERS)
    assert {k for k, *_ in sc.LOG_METRICS} == set(out)
    assert all(m["status"] == "unmeasured" and m["value"] == "unmeasured" for m in out.values())
    assert "retained app logs start" in out["continuity"]["reason"]
    covered = sc.log_section(_logset(lines, sc.parse_ts(L("01T00:00:00", ""))), SINCE, UNTIL, MEMBERS)
    assert covered["continuity"]["status"] == "measured"


def test_no_member_join_makes_member_metrics_unmeasured():
    out = sc.log_section(_logset([], sc.parse_ts(L("01T00:00:00", ""))), SINCE, UNTIL, None, "db down")
    assert all(m["status"] == "unmeasured" for m in out.values())


def _write_minutes(path, day, start_h, hours, extra=()):
    """A plain rotation with one filler line every 5 minutes, plus `extra` lines."""
    rows = []
    for k in range(hours * 12):
        h, m = start_h + (k * 5) // 60, (k * 5) % 60
        rows.append(L(f"{day}T{h:02d}:{m:02d}:00", "filler"))
    rows += list(extra)
    rows.sort()
    path.write_text("\n".join(rows) + "\n")


def test_damaged_middle_rotation_makes_window_unmeasured(tmp_path):
    _write_minutes(tmp_path / "zoe-data.app.log.2", "01", 0, 2)
    good = tmp_path / "zoe-data.app.log.1.gz"
    with gzip.open(good, "wt") as fh:
        for k in range(200):
            fh.write(L(f"06T{k // 12:02d}:{(k % 12) * 5:02d}:00", f"FLUE_TOOLS session={MSESS} tools=[] n={k} pad={'x' * 200}") + "\n")
    raw = good.read_bytes()
    good.write_bytes(raw[: len(raw) // 2])  # truncated gzip: readable start, EOFError later
    _write_minutes(tmp_path / "zoe-data.app.log", "06", 17, 2)
    logs = sc.load_log_lines(sorted(str(p) for p in tmp_path.iterdir()))
    assert any(r.error for r in logs.rotations)
    since = sc.parse_ts(L("01T00:30:00", ""))
    reason = sc.coverage_reason(logs, since, UNTIL)
    assert reason and "could not be read in full" in reason
    out = sc.log_section(logs, since, UNTIL, MEMBERS)
    assert all(m["status"] == "unmeasured" for m in out.values())


def test_missing_middle_rotation_gap_makes_window_unmeasured(tmp_path):
    _write_minutes(tmp_path / "zoe-data.app.log.1", "01", 0, 2)
    _write_minutes(tmp_path / "zoe-data.app.log", "06", 0, 2)  # five days missing between them
    logs = sc.load_log_lines(sorted(str(p) for p in tmp_path.iterdir()))
    reason = sc.coverage_reason(logs, sc.parse_ts(L("01T00:30:00", "")), UNTIL)
    assert reason and "a rotation is missing" in reason
    # a window entirely after the gap is still measurable
    assert sc.coverage_reason(logs, sc.parse_ts(L("06T00:30:00", "")), sc.parse_ts(L("06T01:00:00", ""))) is None


def test_contiguous_rotations_are_covered(tmp_path):
    _write_minutes(tmp_path / "zoe-data.app.log.1", "05", 0, 12)
    _write_minutes(tmp_path / "zoe-data.app.log", "05", 12, 11)
    logs = sc.load_log_lines(sorted(str(p) for p in tmp_path.iterdir()))
    assert sc.coverage_reason(logs, sc.parse_ts(L("05T01:00:00", "")), sc.parse_ts(L("05T22:00:00", ""))) is None


def test_dedup_only_across_rotation_overlap(tmp_path):
    ev = L("06T10:00:00", f"FLUE_TOOLS session={MSESS} tools=[] recall_memory=called:no")
    edge = L("06T12:00:00", f"FLUE_TOOLS session={MSESS} tools=[x] recall_memory=called:no")
    (tmp_path / "zoe-data.app.log.1").write_text("\n".join([ev, ev, edge]) + "\n")  # genuine repeat in one file
    (tmp_path / "zoe-data.app.log").write_text("\n".join([edge, L("06T13:00:00", "filler")]) + "\n")  # edge re-logged after rotation
    logs = sc.load_log_lines(sorted(str(p) for p in tmp_path.iterdir()))
    d = sc.m_tools(logs.lines, SINCE, UNTIL, MEMBERS)["data"]["member"]
    assert d == {"n": 3, "tool": 1, "recall": 0}


# --- finding 4: offered raises need full coverage ------------------------------------------------------

def test_count_raises_counts_only_raised_and_refuses_partial_window():
    lines = parsed([
        L("06T10:00:00", f"DAY_REVIEW_RAISE user={MEMBER} raised=0 reason=off"),
        L("06T10:00:01", f"DAY_REVIEW_RAISE user={MEMBER} raised=1 unanswered=1"),
    ])
    assert sc.count_raises(_logset(lines, sc.parse_ts(L("01T00:00:00", ""))), SINCE, UNTIL) == (1, None)
    n, reason = sc.count_raises(_logset(lines, sc.parse_ts(L("06T00:00:00", ""))), SINCE, UNTIL)
    assert n is None and "retained app logs start" in reason


# --- finding 6: tools and brain timing count member sessions only, others reported separately ----------

def test_tools_member_other_and_harness_are_separate():
    lines = parsed([
        L("06T10:00:00", f"FLUE_TOOLS session={MSESS} tools=[] recall_memory=called:no ms=1"),
        L("06T10:01:00", f"FLUE_TOOLS session={MSESS} tools=[set_timer] recall_memory=called:no ms=1"),
        L("06T10:01:30", "FLUE_TOOLS session=voice-panel-guest tools=[x] recall_memory=called:yes ms=1"),
        L("06T10:02:00", "FLUE_TOOLS session=bar-a-1 tools=[recall_memory] recall_memory=called:yes ms=1 traffic=harness"),
        L("06T10:03:00", "FLUE_TOOLS session=qa-1 tools=[recall_memory] recall_memory=called:yes ms=1"),
        L("01T10:00:00", f"FLUE_TOOLS session={MSESS} tools=[x] recall_memory=called:yes ms=1"),  # outside window
    ])
    d = sc.m_tools(lines, SINCE, UNTIL, MEMBERS)["data"]
    assert d["member"] == {"n": 2, "tool": 1, "recall": 0}
    assert d["other"] == {"n": 1, "tool": 1, "recall": 1}
    assert d["harness"] == {"n": 2, "tool": 2, "recall": 2}


def test_voice_timing_member_sessions_only():
    def vt(i, sess, ms):
        return L(f"06T10:0{i}:00", f"VOICE TIMING turn={i} session={sess} path=stream pre_brain_ms=200 "
                                   f"memory_packet_ms=0 (history=0 memory=0 domain=0) brain_ttft_ms={ms} llm_first_token_ms=1")
    lines = parsed([vt(0, MSESS, 1000), vt(1, MSESS, 2000), vt(2, MSESS, 3000), vt(3, "voice-panel-guest", 9000)])
    m = sc.m_brain_first_text(lines, SINCE, UNTIL, MEMBERS)
    assert m["n"] == 3 and m["data"]["brain_ttft_p50_ms"] == 2000.0
    assert m["data"]["pre_plus_ttft_p50_ms"] == 2200.0 and m["data"]["other"] == 1
    no_pre = parsed([L("06T11:00:00", f"VOICE TIMING turn=9 session={MSESS} path=stream brain_ttft_ms=1500")])
    m2 = sc.m_brain_first_text(no_pre, SINCE, UNTIL, MEMBERS)  # must not crash; E+F has no samples
    assert m2["status"] == "measured" and m2["data"]["pre_plus_ttft_p50_ms"] is None
    assert "pre_brain+brain_ttft unmeasured" in m2["value"] and m2["data"]["brain_ttft_p50_ms"] == 1500.0
    only_guest = sc.m_brain_first_text(parsed([vt(3, "voice-panel-guest", 9000)]), SINCE, UNTIL, MEMBERS)
    assert only_guest["status"] == "unmeasured" and only_guest["value"] == "unmeasured"
    # brain_ttft_ms=-1 means "no token arrived" (timed out / interrupted): never a latency sample (Codex on #2424)
    mixed = sc.m_brain_first_text(parsed([vt(0, MSESS, 1000), vt(1, MSESS, -1), vt(2, MSESS, 3000)]), SINCE, UNTIL, MEMBERS)
    assert mixed["n"] == 2 and mixed["data"]["brain_ttft_p50_ms"] == 2000.0 and mixed["data"]["no_token"] == 1
    assert mixed["data"]["pre_plus_ttft_p50_ms"] == 2200.0
    only_dead = sc.m_brain_first_text(parsed([vt(0, MSESS, -1), vt(1, MSESS, -1)]), SINCE, UNTIL, MEMBERS)
    assert only_dead["status"] == "unmeasured" and "no-token turns: 2" in str(only_dead)


def test_household_kept_uses_the_household_predicate():
    """`household_kept` must be a subset of `household`: the same real-user and harness-session exclusions (Codex on #2424).
    Mutation: drop either exclusion from the kept filter -> a harness follow-through is attributed to the household."""
    import inspect
    import re
    src = re.sub(r"\s+", " ", inspect.getsource(sc))
    house = ("session_id NOT LIKE 'replay-%' AND {real_user_pred('user_id')} "
             "AND lower(coalesce(session_id, '')) !~ '^(qa|demo|bench|probe|test|bar-)')")
    assert src.count("count(*) FILTER (WHERE " + house) == 1
    assert src.count("count(*) FILTER (WHERE status = 'kept' AND " + house) == 1


def test_candidate_values_skip_harness():
    lines = parsed([
        L("06T10:00:00", f"SEAM_CONTINUITY user={MEMBER} matched=False"),
        L("06T10:00:01", "SEAM_CONTINUITY user=demo_x_1 matched=False"),
        L("06T10:00:02", f"FLUE_TOOLS session={MSESS} tools=[]"),
        L("06T10:00:03", "FLUE_TOOLS session=bar-a-1 tools=[] traffic=harness"),
    ])
    assert sc.candidate_values(lines, SINCE, UNTIL) == ([MEMBER], [MSESS])


def test_provenance_member_brain_only():
    lines = parsed([
        L("06T10:00:00", f"PROVENANCE_REPLY user={MEMBER} seq=1 kind=brain served=3 used=1"),
        L("06T10:00:01", f"PROVENANCE_REPLY user={MEMBER} seq=2 kind=brain served=0 used=0"),
        L("06T10:00:02", f"PROVENANCE_REPLY user={MEMBER} seq=3 kind=fast served=5 used=5"),
        L("06T10:00:03", "PROVENANCE_REPLY user=guest seq=1 kind=brain served=1 used=1"),
        L("06T10:00:04", f"PROVENANCE_REPLY user={MEMBER} seq=4 kind=brain served=1 used=1 traffic=harness"),
    ])
    assert sc.m_provenance(lines, SINCE, UNTIL, MEMBERS)["data"] == {"n": 2, "served": 1, "used": 1}


def test_recall_every_turn_eligible_found_claimed():
    base = f"RECALL_EVERY_TURN user={MEMBER} mode=shadow ids=- tokens=0 pool=54 ms=0.4"
    lines = parsed([
        L("08T10:00:00", f"{base} rows=0 claimed=- injected=0 skipped=warming"),
        L("08T10:01:00", f"{base} rows=1 claimed=recall_floor injected=0 skipped=-"),
        L("08T10:02:00", f"{base} rows=0 claimed=continuity injected=0 skipped=-"),
        L("08T10:03:00", f"{base} rows=0 claimed=- injected=0 skipped=-"),
        L("08T10:04:00", "RECALL_EVERY_TURN user=guest mode=shadow rows=0 claimed=- injected=0 skipped=guest"),
    ])
    d = sc.m_recall_every_turn(lines, SINCE, UNTIL, MEMBERS)["data"]
    assert (d["lines"], d["eligible"], d["found"], d["claimed"], d["injected"]) == (4, 3, 1, 2, 0)
    assert d["skipped"] == {"warming": 1}


def test_continuity_matched():
    lines = parsed([
        L("06T10:00:00", f"SEAM_CONTINUITY user={MEMBER} matched=False bullets=0 chars=0"),
        L("06T10:00:01", f"SEAM_CONTINUITY user={MEMBER} matched=True bullets=2 chars=90"),
        L("06T10:00:02", "SEAM_CONTINUITY user=demo_x_1 matched=True bullets=2 chars=90"),
    ])
    assert sc.m_continuity(lines, SINCE, UNTIL, MEMBERS)["data"] == {"n": 2, "matched": 1}


def test_night_outcomes_best_run_per_night():
    text = [
        "[02:56:18] NIGHT_WINDOW_SUMMARY run=20261104-025052 outcome='ok' exit=0",
        "[03:12:01] NIGHT_WINDOW_SUMMARY run=20261106-025153 outcome='refused: no lever set fits' exit=4",
        "[02:58:34] NIGHT_WINDOW_SUMMARY run=20261108-025132 outcome='ok' exit=5",
        "[01:00:00] NIGHT_WINDOW_SUMMARY run=20261108-010000 outcome='aborted: x' exit=3",
    ]
    assert sc.night_outcomes(text) == {"20261104": True, "20261106": False, "20261108": True}


# --- finding 7: the Pi-side extractor is an allowlist ---------------------------------------------------

PANEL_RAW = [
    "2026-11-10 05:28:18,382 [voice-daemon] INFO Recorded follow-up: duration=1.92s stop=silence endpoint=vad tail=deep close=deep=640ms",
    "2026-11-10 05:28:24,628 [voice-daemon] INFO turn_stream TTFA=1.04s len=17 utt=abcdef",
    "2026-11-10 05:29:00,000 [voice-daemon] INFO Recorded command: duration=2.0s heard='SECRETWORDS here' stop=silence tail=quiet",
    "2026-11-10 05:29:05,000 [voice-daemon] INFO turn_stream TTFA=2.00s transcript='PRIVATE text' note=\"HIDDEN\"",
    "2026-11-10 05:30:00,000 [voice-daemon] INFO turn_stream TTFA=9.00s len=10",  # no Recorded before it
    "2026-11-10 05:40:00,000 [voice-daemon] INFO Recorded command: duration=2.0s stop=silence endpoint=vad tail=no_speech",
    "2026-11-10 05:40:03,000 [voice-daemon] INFO turn_stream TTFA=1.00s len=10",  # Recorded without a tail rule
    "2026-11-10 05:41:00,000 [voice-daemon] INFO speaker said=LEAKME turn_stream_misc=1 Recorded nothing",
    "2026-11-10 05:42:00,000 [voice-daemon] INFO wake heard text=ALSOLEAK",
]


@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")
def test_panel_allowlist_emits_only_timestamp_tag_number(tmp_path):
    log = tmp_path / "voice.log"
    log.write_text("\n".join(PANEL_RAW) + "\n")
    out = subprocess.run(sc.panel_command(str(log)), shell=True, capture_output=True, text=True, check=True).stdout
    for bad in ("SECRET", "PRIVATE", "HIDDEN", "LEAKME", "ALSOLEAK", "abcdef", "voice-daemon", "transcript"):
        assert bad not in out
    rows = out.splitlines()
    assert rows and all(sc._PANEL_LINE_RE.match(r) for r in rows)
    since = dt.datetime(2026, 11, 1, tzinfo=sc.LOCAL_TZ)
    until = dt.datetime(2026, 11, 30, tzinfo=sc.LOCAL_TZ)
    assert sc.panel_pairs(rows, since, until) == [1680.0, 2800.0]


def test_panel_pairs_ignores_anything_not_in_the_reduced_shape():
    since = dt.datetime(2026, 11, 1, tzinfo=sc.LOCAL_TZ)
    until = dt.datetime(2026, 11, 30, tzinfo=sc.LOCAL_TZ)
    rows = ["2026-11-10 05:28:18 C 640", "2026-11-10 05:28:20 T 1.0 extra", "2026-11-10 05:28:24 T 1.04"]
    assert sc.panel_pairs(rows, since, until) == [1680.0]


def test_pct_interpolates():
    assert sc.pct([], 50) is None
    assert sc.pct([5.0], 90) == 5.0
    assert sc.pct([1, 2, 3, 4], 50) == 2.5
    assert sc.pct(list(range(1, 11)), 90) == pytest.approx(9.1)


# --- finding 5 / NB1: durable rows are one GROUP BY; sources are allowlisted ---------------------------

def _fake_palace(path):
    con = sqlite3.connect(os.path.join(path, "chroma.sqlite3"))
    con.executescript("""
        CREATE TABLE collections (id TEXT, name TEXT);
        CREATE TABLE segments (id TEXT, collection TEXT, scope TEXT);
        CREATE TABLE embeddings (id INTEGER, segment_id TEXT);
        CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL);
        INSERT INTO collections VALUES ('c1', 'mempalace_drawers'), ('c2', 'mempalace_audit');
        INSERT INTO segments VALUES ('s1', 'c1', 'METADATA'), ('s2', 'c2', 'METADATA');
    """)
    t_in = dt.datetime(2026, 11, 6, tzinfo=TZ8).timestamp()
    t_out = dt.datetime(2026, 10, 1, tzinfo=TZ8).timestamp()
    rows = [(1, "s1", MEMBER, t_in, None, "digest", "approved"),
            (2, "s1", MEMBER, t_in, None, "weird private label", "pending"),
            (3, "s1", MEMBER, None, "2026-11-07T01:00:00.000000Z", "voice", "approved"),
            (4, "s1", MEMBER, t_out, None, "digest", "approved"),
            (5, "s1", "someone-else", t_in, None, "digest", "approved"),
            (6, "s2", MEMBER, t_in, None, "digest", "approved")]
    for eid, seg, uid, ts, at, src, st in rows:
        con.execute("INSERT INTO embeddings VALUES (?, ?)", (eid, seg))
        con.execute("INSERT INTO embedding_metadata VALUES (?, 'user_id', ?, NULL, NULL)", (eid, uid))
        con.execute("INSERT INTO embedding_metadata VALUES (?, 'chroma:document', 'DOC TEXT', NULL, NULL)", (eid,))
        if ts is not None:
            con.execute("INSERT INTO embedding_metadata VALUES (?, 'added_ts', NULL, NULL, ?)", (eid, ts))
        if at is not None:
            con.execute("INSERT INTO embedding_metadata VALUES (?, 'added_at', ?, NULL, NULL)", (eid, at))
        con.execute("INSERT INTO embedding_metadata VALUES (?, 'source', ?, NULL, NULL)", (eid, src))
        con.execute("INSERT INTO embedding_metadata VALUES (?, 'status', ?, NULL, NULL)", (eid, st))
    con.commit()
    con.close()


def test_durable_groups_and_source_allowlist(tmp_path):
    _fake_palace(str(tmp_path))
    groups = sc.durable_groups(str(tmp_path), SINCE, UNTIL)
    assert all("DOC TEXT" not in str(g) for g in groups)
    d = sc.summarise_durable(groups, {MEMBER})
    assert d["rows"] == 3 and d["approved"] == 2
    assert d["by_source"] == {"digest": 1, "voice": 1, "other": 1}


def test_durable_groups_opens_read_only(tmp_path, monkeypatch):
    _fake_palace(str(tmp_path))
    real_connect = sqlite3.connect
    seen = []

    def spy(target, *a, **k):
        seen.append((target, k.get("uri")))
        return real_connect(target, *a, **k)

    monkeypatch.setattr(sc.sqlite3, "connect", spy)
    sc.durable_groups(str(tmp_path), SINCE, UNTIL)
    assert seen and all(t.endswith("?mode=ro") and uri for t, uri in seen)


# --- NB3 / NB4: read-only session + transaction, and every query goes through _agg ----------------------

class _FakeTx:
    def __init__(self, conn, readonly):
        self.conn, self.readonly = conn, readonly

    async def __aenter__(self):
        self.conn.tx_readonly.append(self.readonly)
        self.conn.in_tx = True

    async def __aexit__(self, *exc):
        self.conn.in_tx = False


class _FakeConn:
    def __init__(self):
        self.in_tx = False
        self.tx_readonly: list[bool] = []
        self.fetched: list[str] = []
        self.outside_tx = 0

    def transaction(self, *, readonly=False, **_):
        return _FakeTx(self, readonly)

    async def fetch(self, sql, *args):
        if not self.in_tx:
            self.outside_tx += 1
        self.fetched.append(sql.strip())
        if "WITH ORDINALITY" in sql:
            return [(1,)]
        return [tuple([0] * 8)]

    async def close(self):
        pass


def test_db_collect_read_only_and_all_via_agg(monkeypatch):
    conn = _FakeConn()
    connect_kwargs = {}

    async def fake_connect(dsn, **kw):
        connect_kwargs.update(kw)
        return conn

    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(connect=fake_connect))
    via_agg: list[str] = []
    real_agg = sc._agg

    async def spy(c, sql, *a):
        via_agg.append(sql.strip())
        return await real_agg(c, sql, *a)

    monkeypatch.setattr(sc, "_agg", spy)
    res = asyncio.run(sc.db_collect("postgres://x", SINCE, UNTIL, [MEMBER, "nobody"], [MSESS]))
    assert connect_kwargs["server_settings"]["default_transaction_read_only"] == "on"
    assert conn.tx_readonly == [True]
    assert conn.outside_tx == 0
    assert conn.fetched and conn.fetched == via_agg  # nothing reached the connection without _agg
    assert res["member_users"] == {MEMBER} and res["member_sessions"] == {MSESS}


def test_agg_guard():
    class FakeConn:
        def __init__(self, rows):
            self.rows = rows

        async def fetch(self, sql, *a):
            return self.rows

    assert asyncio.run(sc._agg(FakeConn([(1, 2.5)]), "SELECT count(*), 2.5")) == [(1, 2.5)]
    with pytest.raises(ValueError):
        asyncio.run(sc._agg(FakeConn([("text",)]), "SELECT name FROM users"))
    with pytest.raises(ValueError):
        asyncio.run(sc._agg(FakeConn([]), "DELETE FROM users"))
    with pytest.raises(ValueError):
        asyncio.run(sc._agg(FakeConn([]), "SELECT 1; DELETE FROM users"))
    pred = sc.real_user_pred("s.user_id")
    assert "guest-%" in pred and "!~*" in pred


# --- third pass ------------------------------------------------------------------------------------------

def test_stale_log_end_makes_window_unmeasured(tmp_path):
    _write_minutes(tmp_path / "zoe-data.app.log", "05", 0, 12)  # lines stop at 11:55
    logs = sc.load_log_lines([str(tmp_path / "zoe-data.app.log")])
    since = sc.parse_ts(L("05T01:00:00", ""))
    assert sc.coverage_reason(logs, since, sc.parse_ts(L("05T12:00:00", ""))) is None
    reason = sc.coverage_reason(logs, since, sc.parse_ts(L("05T18:00:00", "")))
    assert reason and "stale or stopped log" in reason
    assert sc.count_raises(logs, since, sc.parse_ts(L("05T18:00:00", "")))[0] is None
    out = sc.log_section(logs, since, sc.parse_ts(L("05T18:00:00", "")), MEMBERS)
    assert all(m["status"] == "unmeasured" for m in out.values())


def test_unknown_skip_reason_collapses_to_other():
    base = f"RECALL_EVERY_TURN user={MEMBER} mode=shadow rows=0 claimed=- injected=0"
    lines = parsed([L("08T10:00:00", f"{base} skipped=guest"), L("08T10:00:01", f"{base} skipped=somebodys_words")])
    m = sc.m_recall_every_turn(lines, SINCE, UNTIL, MEMBERS)
    assert m["data"]["skipped"] == {"guest": 1, "other": 1}
    assert "somebodys_words" not in m["method"]


def test_malformed_marker_is_unmeasured_not_a_crash():
    bad = parsed([L("06T10:00:00", f"PROVENANCE_REPLY user={MEMBER} seq=1 kind=brain served=abc used=0")])
    m = sc.m_provenance(bad, SINCE, UNTIL, MEMBERS)
    assert m["status"] == "unmeasured" and "malformed marker" in m["reason"]
    bad2 = parsed([L("08T10:00:00", f"RECALL_EVERY_TURN user={MEMBER} rows=x claimed=- injected=0 skipped=-")])
    assert sc.m_recall_every_turn(bad2, SINCE, UNTIL, MEMBERS)["status"] == "unmeasured"
    bad3 = parsed([L("06T10:00:00", f"VOICE TIMING session={MSESS} pre_brain_ms=1 brain_ttft_ms=nan_ms")])
    assert sc.m_brain_first_text(bad3, SINCE, UNTIL, MEMBERS)["status"] == "unmeasured"


def test_panel_metric_requires_log_to_reach_window_end():
    since = dt.datetime(2026, 11, 10, 0, 0, tzinfo=sc.LOCAL_TZ)
    rows = ["2026-11-09 23:00:00 E", "2026-11-10 05:28:18 C 640", "2026-11-10 05:28:24 T 1.04"]
    rows += [f"2026-11-10 {h:02d}:00:00 E" for h in range(6, 12)]
    ok = sc.panel_metric(rows, since, dt.datetime(2026, 11, 10, 12, 0, tzinfo=sc.LOCAL_TZ))
    assert ok["status"] == "measured" and ok["n"] == 1
    stale = sc.panel_metric(rows, since, dt.datetime(2026, 11, 10, 20, 0, tzinfo=sc.LOCAL_TZ))
    assert stale["status"] == "unmeasured" and "stale or stopped log" in stale["reason"]


@pytest.mark.parametrize("argv", [["--days", "0"], ["--long-days", "-1"], ["--nights", "0"],
                                  ["--since", "2026-11-05T00:00", "--until", "2026-11-05T00:00"]])
def test_empty_or_negative_window_exits_2_before_any_read(argv, monkeypatch):
    monkeypatch.setattr(sc, "build", lambda a: pytest.fail("build must not run"))
    with pytest.raises(SystemExit) as exc:
        sc.main(argv)
    assert exc.value.code == 2
    assert sc.rate(5, 0) == "unmeasured (empty window)"


def test_pendant_writers_are_not_panel_supply():
    """A pendant fact approved into the same collection (omi_review / omi_day_review) is the pendant channel's row: it must not
    raise the panel/chat total or hide under `other` (Codex on #2424)."""
    groups = [(MEMBER, "digest", "approved", 2), (MEMBER, "omi_review", "approved", 5), (MEMBER, "omi_day_review", "approved", 1),
              (MEMBER, "something_new", "pending", 1)]
    d = sc.summarise_durable(groups, {MEMBER})
    assert d["rows"] == 3 and d["approved"] == 2 and d["pendant_rows_excluded"] == 6
    assert d["by_source"] == {"digest": 2, "other": 1}



# --- corrections applied (VALUE_CORRECTION) -------------------------------------------------------------

def test_corrections_member_only_rows_summed():
    lines = parsed([
        L("06T10:00:00", f"VALUE_CORRECTION_APPLIED user={MEMBER} rows=2"),
        L("06T10:01:00", f"VALUE_CORRECTION_APPLIED user={MEMBER} rows=3"),
        L("06T10:02:00", "VALUE_CORRECTION_APPLIED user=someone-else rows=7"),           # not a member
        L("06T10:03:00", f"VALUE_CORRECTION_APPLIED user={MEMBER} rows=9 traffic=harness"),  # harness
        L("06T10:04:00", "VALUE_CORRECTION_APPLIED user=demo_bar_abc123 rows=4"),          # synthetic user
        L("06T10:05:00", f"VALUE_CORRECTION_STALE lane=idle_consolidation user={MEMBER} session={MSESS}"),
        L("06T10:06:00", f"VALUE_CORRECTION_KIND_GUESS lane=idle_consolidation user={MEMBER} session={MSESS}"),
        L("06T10:07:00", f"VALUE_CORRECTION_KIND_GUESS lane=idle_consolidation user={MEMBER} session=qa-x"),  # harness session
        L("01T10:00:00", f"VALUE_CORRECTION_APPLIED user={MEMBER} rows=50"),               # outside window
    ])
    m = sc.m_corrections(lines, SINCE, UNTIL, MEMBERS)
    assert m["status"] == "measured" and m["n"] == 2
    assert m["data"] == {"applied": 2, "rows": 5, "stale": 1, "kind_guess": 1}


def test_corrections_malformed_rows_is_unmeasured_and_zero_lines_is_n0():
    bad = parsed([L("06T10:00:00", f"VALUE_CORRECTION_APPLIED user={MEMBER} rows=lots")])
    m = sc.m_corrections(bad, SINCE, UNTIL, MEMBERS)
    assert m["status"] == "unmeasured" and "malformed marker" in m["reason"]
    empty = sc.m_corrections([], SINCE, UNTIL, MEMBERS)
    assert empty["status"] == "measured" and empty["n"] == 0 and empty["data"]["applied"] == 0


def test_correction_marker_does_not_change_existing_candidates_or_metrics():
    """VALUE_CORRECTION joined MARKERS/USER_MARKERS: a log without it yields the same candidates and metrics as before,
    and a correction line only adds its own user= candidate (never a session)."""
    base = [
        L("06T10:00:00", f"SEAM_CONTINUITY user={MEMBER} matched=True"),
        L("06T10:00:02", f"FLUE_TOOLS session={MSESS} tools=[]"),
        L("06T10:00:03", f"PROVENANCE_REPLY user={MEMBER} seq=1 kind=brain served=1 used=0"),
    ]
    before = sc.candidate_values(parsed(base), SINCE, UNTIL)
    assert before == ([MEMBER], [MSESS])
    extra = base + [L("06T10:00:04", f"VALUE_CORRECTION_STALE lane=idle_consolidation user=member-b session=voice-panel-b"),
                    L("06T10:00:05", "VALUE_CORRECTION_APPLIED user=demo_x_1 rows=1")]
    assert sc.candidate_values(parsed(extra), SINCE, UNTIL) == ([MEMBER, "member-b"], [MSESS])
    for fn in (sc.m_continuity, sc.m_tools, sc.m_provenance):
        assert fn(parsed(base), SINCE, UNTIL, MEMBERS)["data"] == fn(parsed(extra), SINCE, UNTIL, MEMBERS)["data"]


# --- first sound under 1 s / 0.6 s ----------------------------------------------------------------------

def test_panel_first_sound_bands_are_strictly_under():
    since = dt.datetime(2026, 11, 10, 0, 0, tzinfo=sc.LOCAL_TZ)
    until = dt.datetime(2026, 11, 10, 12, 0, tzinfo=sc.LOCAL_TZ)
    rows = ["2026-11-09 23:00:00 E"]
    # close + TTFA per turn: 599, 600 (not under 0.6), 999, 1000 (not under 1), 1500
    for i, (close, ttfa) in enumerate([(400, "0.199"), (400, "0.2"), (640, "0.359"), (640, "0.36"), (800, "0.7")]):
        rows += [f"2026-11-10 0{i + 1}:00:00 C {close}", f"2026-11-10 0{i + 1}:00:03 T {ttfa}"]
    rows += [f"2026-11-10 {h:02d}:00:00 E" for h in range(6, 12)]
    m = sc.panel_metric(rows, since, until)
    assert m["status"] == "measured" and m["n"] == 5
    assert (m["data"]["under_1000_ms"], m["data"]["under_600_ms"]) == (3, 1)
    assert "under 1 s 3 of 5 (60.0%)" in m["value"] and "under 0.6 s 1 of 5 (20.0%)" in m["value"]


# --- ALERTS ----------------------------------------------------------------------------------------------

RED, GREEN = sc.CRASH_ALERT, sc.CRASH_RECOVERY
LONG_SINCE = dt.datetime(2026, 10, 20, tzinfo=TZ8)


def _hourly(start: dt.datetime, end: dt.datetime, fmt: str, body: str) -> list[tuple[dt.datetime, str]]:
    out, t = [], start
    while t <= end:
        out.append((t, fmt.format(t=t) + body))
        t += dt.timedelta(hours=1)
    return out


def _crash_log(path, extra: list[tuple[dt.datetime, str]], start=dt.datetime(2026, 10, 15, tzinfo=TZ8)):
    rows = _hourly(start, UNTIL + dt.timedelta(hours=1), "{t:%Y-%m-%dT%H:%M:%S%z} ", "crash-watch: healthy, restarts=0 (+0 since last check)")
    rows += extra
    rows.sort(key=lambda r: r[0])
    path.write_text("\n".join(r for _, r in rows) + "\n", encoding="utf-8")


def test_crash_loops_counts_alerts_recoveries_by_allowlisted_unit(tmp_path):
    log = tmp_path / "crash-loop-watch.log"
    at = lambda d, h, m: dt.datetime(2026, 11, d, h, m, tzinfo=TZ8)  # noqa: E731
    _crash_log(log, [
        (at(6, 10, 30), f"{RED} Zoe is crash-looping. | zoe-data.service restarted 6x since the last check (total 9), ActiveState=activating, /health not responding. | Zoe cannot be talked to right now."),
        (at(6, 11, 30), f"{GREEN} Zoe recovered — zoe-data.service is healthy again (total restarts 9)."),
        (at(7, 10, 30), f"{RED} kokoro-tts.service is crash-looping: restarted 5x in the last 15 min (total 5), ActiveState=activating. | Check: journalctl --user -u kokoro-tts.service"),
        (at(7, 12, 30), f"{RED} private-thing.service is crash-looping: restarted 5x in the last 15 min (total 5), ActiveState=failed."),
        (at(7, 12, 31), "crash-watch: telegram send failed: HTTP Error 401"),
        (dt.datetime(2026, 10, 1, tzinfo=TZ8), f"{RED} zoe-data.service is crash-looping"),  # outside the window
    ])
    m = sc.alert_crash_loops(str(log), LONG_SINCE, UNTIL)
    assert m["status"] == "measured", m
    assert (m["data"]["alerts"], m["data"]["recoveries"], m["data"]["undelivered"]) == (3, 1, 1)
    assert m["data"]["by_unit"] == {"zoe-data.service": 1, "kokoro-tts.service": 1, "other": 1}
    import json as _json
    assert "private-thing" not in _json.dumps(m)


def test_crash_loops_missing_unreadable_and_uncovered(tmp_path):
    m = sc.alert_crash_loops(str(tmp_path / "nope.log"), LONG_SINCE, UNTIL)
    assert m["status"] == "unmeasured" and "FileNotFoundError" in m["reason"]
    log = tmp_path / "crash-loop-watch.log"
    _crash_log(log, [], start=dt.datetime(2026, 11, 1, tzinfo=TZ8))           # starts after the window
    assert "after the window start" in sc.alert_crash_loops(str(log), LONG_SINCE, UNTIL)["reason"]
    rows = [r for r in log.read_text().splitlines() if not r.startswith("2026-11-08")]   # watcher silent a day
    log.write_text("\n".join(rows) + "\n")
    m = sc.alert_crash_loops(str(log), dt.datetime(2026, 11, 2, tzinfo=TZ8), UNTIL)
    assert m["status"] == "unmeasured" and "silent" in m["reason"]


def test_crash_loops_alert_straddling_window_edge_is_not_counted(tmp_path):
    log = tmp_path / "crash-loop-watch.log"
    _crash_log(log, [(LONG_SINCE + dt.timedelta(minutes=1), f"{RED} zoe-data.service is crash-looping")])
    since = LONG_SINCE + dt.timedelta(minutes=30)  # the alert lies between the stamps at 00:00 and 01:00; since=00:30
    m = sc.alert_crash_loops(str(log), since, UNTIL)
    assert m["data"]["alerts"] == 0 and m["data"]["edge"] == 1


def _report(d, day, mode="night", outcome="ok", **kw):
    import json as _json
    (d / f"{day}.json").write_text(_json.dumps({"mode": mode, "outcome": outcome, "started": f"{day}T02:50:00", **kw}))


def test_nights_buckets_alarms_and_missing(tmp_path):
    since, until = dt.datetime(2026, 11, 1, 12, tzinfo=sc.LOCAL_TZ), dt.datetime(2026, 11, 8, 12, tzinfo=sc.LOCAL_TZ)
    _report(tmp_path, "2026-10-30")                                        # before the window: proves coverage
    _report(tmp_path, "2026-11-02", outcome="ok", decode_alert="12B slow")
    _report(tmp_path, "2026-11-03", outcome="degraded: digest@12B")
    _report(tmp_path, "2026-11-04", outcome="refused: busy", restore_status="NOT HEALTHY: x")
    _report(tmp_path, "2026-11-05", outcome="aborted: no load", night_lost="did not load")
    _report(tmp_path, "2026-11-06")
    _report(tmp_path, "2026-11-06-rehearse", mode="rehearse", outcome="failed: x")   # not a real night
    # 2026-11-07 and 2026-11-08 have no report
    (tmp_path / "2026-10-31.json").write_text("{not json")                 # corrupt, but outside the window
    (tmp_path / "2026-11-07-rehearse.json").write_text("{not json")        # corrupt, but not a real-night report
    m = sc.alert_nights(str(tmp_path), since, until)
    d = m["data"]
    assert m["status"] == "measured" and m["n"] == 7 and m["window"] == "nights 2026-11-02..2026-11-08"
    assert (d["ok"], d["degraded"], d["not_ok"], d["no_report"]) == (2, 1, 2, 2)
    assert (d["alarm"], d["decode_slow"], d["restore_failed"], d["night_lost"]) == (3, 1, 1, 1)
    assert d["unreadable"] == 1 and d["alarm_file_now"] is False
    assert "x" not in m["value"] and "did not load" not in str(m)


@pytest.mark.parametrize("name,body", [("2026-11-04.json", "{not json"), ("2026-11-04-1530.json", "[1, 2]"),
                                       ("2026-11-04.json", '{"mode": "night", "started": "2026-11-04T02:50:00"}'),
                                       ("2026-11-04.json", '{"mode": "night", "outcome": "ok", "started": "garbage"}')])
def test_nights_unreadable_report_in_window_is_unmeasured_not_missing(tmp_path, name, body):
    since, until = dt.datetime(2026, 11, 1, 12, tzinfo=sc.LOCAL_TZ), dt.datetime(2026, 11, 8, 12, tzinfo=sc.LOCAL_TZ)
    _report(tmp_path, "2026-10-30")
    _report(tmp_path, "2026-11-03")
    (tmp_path / name).write_text(body)
    m = sc.alert_nights(str(tmp_path), since, until)
    assert m["status"] == "unmeasured" and "1 night report(s) in the window exist but cannot be read" in m["reason"]


def test_nights_missing_dir_and_narrowed_window(tmp_path):
    assert sc.alert_nights(str(tmp_path / "nope"), SINCE, UNTIL)["status"] == "unmeasured"
    assert sc.alert_nights(str(tmp_path), SINCE, UNTIL)["status"] == "unmeasured"   # empty dir
    _report(tmp_path, "2026-11-08")
    m = sc.alert_nights(str(tmp_path), SINCE, UNTIL)
    assert m["status"] == "measured" and "narrowed" in m["window"] and m["window"].startswith("nights 2026-11-08..")


BACKUP_LOG = """ok: zoe: zoe-1-1.dump.gz restored, 10 tables, 100 rows, Alembic 1
restore drill: all checks passed
zoe-backup-verify: done
refused: MemAvailable 900 MB < floor 1100 MB
ALERT: weekly restore drill refused: MemAvailable 900 MB < floor 1100 MB
zoe-backup-verify: on-box restore drill REFUSED to run (low memory / stuck backup / stale; alerted)
zoe-backup-verify: 1 problem(s) detected
FINDING: host archive: something private was MISSING
ALERT: weekly restore drill FAILED (1): host archive: something private was MISSING
ALERT: a brand new secret kind of alert
alert not delivered: RuntimeError
zoe-backup-verify: 1 problem(s) detected
"""


def test_backups_whole_log_counts_and_kinds(tmp_path):
    log, state = tmp_path / "backup-verify.log", tmp_path / "state.json"
    log.write_text(BACKUP_LOG)
    state.write_text('{"time": "2026-11-08T05:31:52+08:00", "ok": false, "findings": ["private finding"]}')
    m = sc.alert_backups(str(log), str(state))
    assert m["status"] == "measured" and m["window"].startswith("whole log")
    d = m["data"]
    assert (d["alerts"], d["runs"], d["problem_runs"], d["undelivered"], d["last_drill_ok"]) == (3, 3, 2, 1, False)
    assert d["by_kind"] == {"drill_refused": 1, "drill_failed": 1, "other": 1}
    assert "private" not in str(m) and "secret" not in str(m)
    assert "2026-11-08 05:31" in m["method"] and "passed: no" in m["method"]


@pytest.mark.parametrize("state_body", [None, "{not json", '{"ok": true}', '{"time": "2026-11-08T05:31:52+08:00", "ok": "yes"}',
                                        '{"time": "not a time", "ok": true}', "[1]"])
def test_backups_unreadable_or_malformed_state_is_unmeasured(tmp_path, state_body):
    log, state = tmp_path / "backup-verify.log", tmp_path / "state.json"
    log.write_text(BACKUP_LOG)
    if state_body is not None:
        state.write_text(state_body)
    m = sc.alert_backups(str(log), str(state))
    assert m["status"] == "unmeasured" and "drill state file" in m["reason"]
    assert "(readable: 3 ALERT lines over 3 runs)" in m["reason"] and "private" not in str(m)


def test_backups_missing_log_and_state(tmp_path):
    assert sc.alert_backups(str(tmp_path / "nope.log"), str(tmp_path / "s.json"))["status"] == "unmeasured"
    log = tmp_path / "backup-verify.log"
    log.write_text("ALERT: weekly restore drill FAILED (1): x\n")  # a run cut before its last line
    state = tmp_path / "state.json"
    state.write_text('{"time": "2026-11-08T05:31:52+08:00", "ok": true}')
    m = sc.alert_backups(str(log), str(state))
    assert m["data"]["runs"] == 0 and m["data"]["alerts"] == 1 and "unterminated last run: yes" in m["method"]
    assert sc.alert_backups(str(log), str(tmp_path / "missing.json"))["status"] == "unmeasured"


def _health_log(path, extra, start=dt.datetime(2026, 10, 15, tzinfo=TZ8), step=dt.timedelta(minutes=5)):
    rows, t = [], start
    while t <= UNTIL + dt.timedelta(minutes=10):
        rows.append((t, f"[{t.astimezone(sc.LOCAL_TZ):%Y-%m-%dT%H:%M:%S}] ok   postgres: pg_isready rc=0"))
        t += step
    rows += [(t, f"[{t.astimezone(sc.LOCAL_TZ):%Y-%m-%dT%H:%M:%S}] {body}") for t, body in extra]
    rows.sort(key=lambda r: r[0])
    path.write_text("\n".join(r for _, r in rows) + "\n")


def test_health_alerts_by_check_with_allowlist(tmp_path):
    log = tmp_path / "health.log"
    at = dt.datetime(2026, 11, 6, 10, 0, tzinfo=TZ8)
    _health_log(log, [
        (at, "ALERT DOWN: night-window timer (zoe-night-window.timer is inactive / disabled)"),
        (at + dt.timedelta(minutes=5), "ALERT DOWN: someone's private check (x)"),
        (at + dt.timedelta(minutes=10), "ALERT recovered: night-window timer (armed)"),
        (dt.datetime(2026, 10, 16, tzinfo=TZ8), "ALERT DOWN: postgres (pg_isready rc=1)"),  # before the window
    ])
    m = sc.alert_health(str(log), LONG_SINCE, UNTIL)
    assert m["status"] == "measured", m
    assert (m["data"]["down"], m["data"]["recovered"]) == (2, 1)
    assert m["data"]["by_check"] == {"night-window timer": 1, "other": 1}
    assert "private" not in str(m) and "night-window timer 1" in m["value"]


def test_health_missing_and_uncovered(tmp_path):
    m = sc.alert_health(str(tmp_path / "health.log"), LONG_SINCE, UNTIL)
    assert m["status"] == "unmeasured" and "cannot read health.log" in m["reason"]
    log = tmp_path / "health.log"
    _health_log(log, [], step=dt.timedelta(hours=1))      # a writer that runs hourly leaves 1 h holes
    assert "silent" in sc.alert_health(str(log), LONG_SINCE, UNTIL)["reason"]
    _health_log(log, [])
    log.write_text(log.read_text() + "[2026-11-31T25:00:00] ALERT DOWN: postgres (x)\n")   # an ALERT that cannot be dated
    m = sc.alert_health(str(log), LONG_SINCE, UNTIL)
    assert m["status"] == "unmeasured" and "invalid timestamp" in m["reason"]


def test_distress_and_kv_miss_from_app_log():
    lines = parsed([
        L("06T10:00:00", "DISTRESS_NO_CONTACT a hand-off fired and nobody was told: ZOE_DISTRESS_CONTACT_USER is not set"),
        L("06T10:01:00", "DISTRESS_NO_CONTACT a hand-off fired and nobody was told traffic=harness"),
        L("06T10:02:00", "PANEL_KV_MISS session=voice-panel-a kind=first first_prompt_n=900 first_cache_n=0 since_prev_s=-"),
        L("06T11:02:00", "PANEL_KV_MISS session=voice-panel-a kind=warm first_prompt_n=900 first_cache_n=0 since_prev_s=30"),
        L("07T10:02:00", "PANEL_KV_MISS session=voice-panel-a kind=first first_prompt_n=900 first_cache_n=0 since_prev_s=-"),
        L("07T10:03:00", "PANEL_KV_MISS session=voice-panel-a kind=weird_private first_prompt_n=900 first_cache_n=0"),
        L("07T10:04:00", "kv_miss_alert: alert not delivered (attempt 1/3): http_401"),
        L("07T10:05:00", "kv_miss_alert: alert not delivered (attempt 2/3): http_401 traffic=harness"),
        L("07T10:06:00", "kv_miss_alert: alert not delivered (attempt 3/3): http_401 traffic=ci"),
        L("01T10:00:00", "PANEL_KV_MISS session=voice-panel-a kind=first first_prompt_n=900 first_cache_n=0"),  # outside
    ])
    logs = _logset(lines, sc.parse_ts(L("01T00:00:00", "")))
    d = sc.alert_distress_no_contact(logs, SINCE, UNTIL)
    assert d["status"] == "measured" and d["data"] == {"lines": 1, "harness": 1}
    k = sc.perf_panel_kv_miss(logs, SINCE, UNTIL)
    assert k["label"] == "[Performance]"
    assert k["data"] == {"first": 2, "warm": 1, "other": 1, "days_first": 2, "undelivered": 1}
    assert "weird_private" not in str(k) and "voice-panel-a" not in str(k)
    # retention starting after the window start narrows the window (stated), never a silent partial count
    late = _logset([x for x in lines if x[0] >= sc.parse_ts(L("06T10:01:30", ""))], sc.parse_ts(L("06T10:01:30", "")))
    dl, kl = sc.alert_distress_no_contact(late, SINCE, UNTIL), sc.perf_panel_kv_miss(late, SINCE, UNTIL)
    assert dl["status"] == kl["status"] == "measured" and "narrowed" in dl["window"] and "narrowed" in kl["window"]
    assert dl["data"]["lines"] == 0 and kl["data"]["first"] == 2
    # any other coverage failure (a damaged rotation) stays unmeasured
    rot = sc.Rotation("zoe-data.app.log.1.gz", first=sc.parse_ts(L("01T00:00:00", "")), last=UNTIL, error="EOFError")
    broken = sc.LogSet(lines, [rot])
    assert sc.alert_distress_no_contact(broken, SINCE, UNTIL)["status"] == "unmeasured"
    assert sc.perf_panel_kv_miss(broken, SINCE, UNTIL)["status"] == "unmeasured"


def test_alert_markers_are_not_member_candidates():
    lines = parsed([L("06T10:00:00", "PANEL_KV_MISS session=voice-panel-z kind=first"),
                    L("06T10:00:01", "DISTRESS_NO_CONTACT user=member-z")])
    assert sc.candidate_values(lines, SINCE, UNTIL) == ([], [])


def test_alert_section_never_crashes_and_renders_sections(tmp_path, monkeypatch):
    args = types.SimpleNamespace(log_dir=str(tmp_path), crash_log=None, night_reports=str(tmp_path / "nr"),
                                 backup_log=None, backup_state=str(tmp_path / "s.json"), health_log=None)
    monkeypatch.setattr(sc, "alert_backups", lambda *a: 1 / 0)
    rows = sc.alert_section(args, sc.LogSet([], []), LONG_SINCE, UNTIL)
    assert [r["key"] for r in rows] == ["alerts_crash_loops", "alerts_night", "alerts_backups", "alerts_health",
                                        "alerts_distress_no_contact", "perf_panel_kv_miss"]
    assert all(r["status"] == "unmeasured" for r in rows)
    assert "reader failed (ZeroDivisionError)" in rows[2]["reason"]
    assert rows[-1]["section"] == sc.PERFORMANCE and rows[0]["section"] == sc.ALERTS
    text = sc.render({"generated_at": "x", "runtime_s": 0, "metrics": rows,
                      "window": {"since": "a", "until": "b", "long_since": "c"}})
    assert f"== {sc.ALERTS} ==" in text and f"== {sc.PERFORMANCE} ==" in text
    assert text.index(sc.ALERTS) < text.index(sc.PERFORMANCE)


# --- the daemon's FIRST_SOUND line (measured first sound) -------------------------------------------------

_FS_OK = ("2026-11-10 05:30:00,123 [voice-daemon] INFO FIRST_SOUND close=deep=640ms post_ms=120 reflex=none skip=- "
          "reflex_at_ms=-1 reflex_dur_ms=-1 reflex_cut=0 reflex_wait_ms=0 server_first_ms=870 server_kind=content "
          "gap_ms=-1 still_talking=1 talk_chunks=4 chunks=3 underrun_max_ms=40 underrun_sum_ms=55 "
          "obs_ms=1500 win=wake quiet_ms=640 fall=clean muted_ms=160")


@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")
def test_first_sound_allowlist_emits_integers_only_and_text_emits_nothing(tmp_path):
    log = tmp_path / "voice.log"
    texty = [  # each carries one extra token or a text-bearing field: must NOT become an F row, and must not leak
        _FS_OK + " transcript=LEAKME",
        _FS_OK.replace("server_kind=content", "server_kind=content LEAKME"),
        _FS_OK.replace("skip=-", "skip=LEAK ME"),
        _FS_OK.replace("post_ms=120", "post_ms=LEAKME"),
        _FS_OK.replace("talk_chunks=4", "talk_chunks=4 name=LEAKME"),
        _FS_OK.replace("still_talking=1", "still_talking=maybe"),
        _FS_OK.replace("win=wake", "win=wake LEAKME"),
        _FS_OK.replace("fall=clean", "fall=LEAK ME"),
        _FS_OK + " muted_ms=5",
    ]
    log.write_text("\n".join([_FS_OK] + texty) + "\n")
    out = subprocess.run(sc.panel_command(str(log)), shell=True, capture_output=True, text=True, check=True).stdout
    rows = out.splitlines()
    assert "LEAK" not in out and "voice-daemon" not in out and "content" not in out
    assert all(sc._PANEL_LINE_RE.match(r) for r in rows)
    assert rows[0] == "2026-11-10 05:30:00 F -1 870 1 4 40 55 1500 160"
    assert all(r.endswith(" E") for r in rows[1:])  # timestamp only, nothing else
    since = dt.datetime(2026, 11, 1, tzinfo=sc.LOCAL_TZ)
    until = dt.datetime(2026, 11, 30, tzinfo=sc.LOCAL_TZ)
    assert sc.panel_first_sound_rows(rows, since, until) == [(-1, 870, 1, 4, 40, 55, 1500, 160)]


def test_first_sound_metrics():
    since = dt.datetime(2026, 11, 10, 0, 0, tzinfo=sc.LOCAL_TZ)
    until = dt.datetime(2026, 11, 10, 12, 0, tzinfo=sc.LOCAL_TZ)
    # (reflex_at, server_first, still, talk, um, us): firsts 500, 600(not <0.6), 999, 1000(not <1), 1500; one no-sound turn
    spec = [(-1, 500, 0, 0, 0, 0), (-1, 600, 1, 3, 60, 100), (-1, 999, 0, -1, 0, 0), (-1, 1000, 0, 2, 0, 0),
            (-1, 1500, 0, 0, 20, 20), (-1, -1, 0, 0, 0, 0)]
    obs = [1500, 1500, 0, 900, 1500, 1500]   # per-turn observed window; muted ms: -1 unknown, 0 none, 80 some
    muted = [0, 80, -1, 0, 0, 160]
    rows = ["2026-11-09 23:00:00 E"]
    for i, (a, b, c, d, e, f) in enumerate(spec):
        rows.append(f"2026-11-10 0{i + 1}:00:00 F {a} {b} {c} {d} {e} {f} {obs[i]} {muted[i]}")
    rows += ["2026-11-10 07:00:00 F 5 100 0 0 0 0 1500 0"]  # a reflex at 5 ms: first sound = min(reflex, server)
    m = {x["key"]: x for x in sc.panel_first_sound_metrics(rows, since, until)}
    fs = m["first_sound_from_close"]
    assert fs["n"] == 6 and (fs["data"]["under_1000_ms"], fs["data"]["under_600_ms"]) == (4, 2)
    assert fs["data"]["p50_ms"] == 799.5  # 5, 500, 600, 999, 1000, 1500
    talk = m["panel_post_close_talk"]
    assert talk["n"] == 6 and talk["data"]["still_talking"] == 1 and talk["data"]["talk_chunks"] == 5  # talk -1 turn excluded
    assert talk["data"]["full_window_turns"] == 5 and "full 1.5 s window seen on 5 of 6" in talk["value"]  # the 900 ms turn is not full
    un = m["panel_playback_underrun"]
    assert un["data"]["max_ms"] == 60 and un["data"]["sum_ms"] == 120 and un["data"]["starved_turns"] == 2 and un["n"] == 7
    assert un["data"]["muted_turns"] == 2 and "mic muted on 2 of 6 turns" in un["value"]  # the -1 turn is unknown, not clean
    assert m["panel_server_first"]["n"] == 6 and m["panel_server_first"]["data"]["p50_ms"] == 799.5
    empty = sc.panel_first_sound_metrics(["2026-11-10 05:00:00 E"], since, until)
    assert len(empty) == 4 and all(x["status"] == "unmeasured" for x in empty)
