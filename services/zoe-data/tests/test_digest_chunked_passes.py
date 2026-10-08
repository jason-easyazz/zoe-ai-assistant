"""The nightly passes read the WHOLE day (``digest_pack``; ``ZOE_DIGEST_CHUNKED``), not its first 3,000 characters.

Before: the fact extractor and the emotional pass cut the day's transcript to 3,000 characters, the loader read at most 200 turns, the open-loop
pass at most 50 turns inside a 3,000-character budget, and every call had a fixed 30-45 s timeout. On a busy day ~70 % of what the owner said never
reached a model (docs/research/night-mind-2026-10-09.md). Now each pass packs the day into chunks that fit the slot (prompt overhead counted), maps one
call per chunk and reduces in code with the EXISTING dedup; the observation gate and every write path are untouched.

The dense-day fixture (``scripts/perf/dense_day.py``: 240 turns, plants in the last 15 %, two of them the Samantha day-sim's own scenario 4/5 seeds)
is run through the REAL ``run_memory_digest`` / ``_extract_open_loops`` against a stand-in model that reads exactly the transcript it is shown. The
NEGATIVE CONTROL is the same fixture with ``ZOE_DIGEST_CHUNKED=0``: the late plants must NOT be found (a test that cannot go red measures nothing).
Synthetic names; no model, no database, no live service.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import types
from pathlib import Path

import pytest

import db_compat
import digest_pack
import memory_digest as md
import memory_reject_ledger as led

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "perf"))
import dense_day as dd  # noqa: E402

CTX = 8192


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    led.reset_for_tests()
    for name in ("ZOE_DIGEST_CHUNKED", "ZOE_DIGEST_MAX_CHUNKS", "ZOE_DIGEST_MAX_FACTS", "ZOE_DIGEST_CHUNK_TOKENS", "ZOE_DIGEST_DECODE_TOK_S",
                 "ZOE_DIGEST_LLM_TIMEOUT_SCALE", "ZOE_BRAIN_SLOT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    yield
    led.reset_for_tests()


@pytest.fixture(scope="module")
def day():
    return dd.dense_day()


# ── the stand-ins ───────────────────────────────────────────────────────────

class _Row:
    def __init__(self, mem_id, text, metadata=None):
        self.id, self.text, self.metadata = mem_id, text, metadata if metadata is not None else {}


class _Svc:
    def __init__(self):
        self.ingested: list[tuple[str, dict]] = []

    async def ingest(self, text, **kw):
        self.ingested.append((text, kw))
        return _Row(f"new-{len(self.ingested)}", text, {"status": "approved"})

    async def search(self, *a, **k):
        return []

    async def get(self, mem_id):
        return None

    async def review(self, mem_id, **kw):
        return None

    async def list_by_status(self, **kw):
        return []

    def texts(self, source_memory_type=None):
        return [t for t, kw in self.ingested if source_memory_type is None or kw.get("memory_type") == source_memory_type]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _MsgDb:
    """The loader's db: honours the SQL's own ``LIMIT n`` (so the legacy 200-row cap is real), ASC like the loader's ORDER BY."""

    def __init__(self, turns):
        self.turns = turns
        self.sql: list[str] = []

    async def execute(self, sql, params=()):
        self.sql.append(sql)
        n = int(re.search(r"LIMIT\s+(\d+)", sql).group(1))
        return _Cursor([(t, f"m{i:04d}") for i, t in enumerate(self.turns)][:n])


class Brain:
    """A stand-in llama-server: records every call and answers like a model that READS exactly the transcript it is shown."""

    def __init__(self, day, *, fail_on=()):
        self.day, self.calls, self.fail_on = day, [], set(fail_on)

    def install(self, monkeypatch):
        brain = self

        class _Resp:
            def __init__(self, text):
                self._t = text

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": self._t}}]}

        class _Client:
            def __init__(self, *a, timeout=None, **k):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, **k):
                system, prompt = json["messages"][0]["content"], json["messages"][1]["content"]
                brain.calls.append({"system": system, "prompt": prompt, "max_tokens": json["max_tokens"], "timeout": self.timeout})
                if len(brain.calls) in brain.fail_on:
                    raise md.httpx.ReadTimeout("slot busy")
                return _Resp(dd.oracle_reply(system, prompt, brain.day))

        monkeypatch.setattr(md.httpx, "AsyncClient", _Client)
        return self

    def of(self, kind):
        key = {"facts": "fact extractor", "emotional": "empathetic", "loops": "open loops"}[kind]
        return [c for c in self.calls if key in c["system"].lower()]


def _world(monkeypatch, day, svc, *, fail_on=()):
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)
    return Brain(day, fail_on=fail_on).install(monkeypatch)


def _run_nightly(day, db=None):
    return asyncio.run(md.run_memory_digest("demo-user", db=db or _MsgDb(day.turns)))


def _found(day, texts, kind):
    """The plants of ``kind`` whose needle appears in some stored text."""
    return {p.key for p in day.plants if kind in p.kinds and any(p.needle in t.lower() for t in texts)}


def _keys(day, kind, *, late=None):
    return {p.key for p in day.plants if kind in p.kinds and (late is None or p.late == late)}


# ── the fixture itself ──────────────────────────────────────────────────────

def test_the_fixture_is_a_dense_day_with_late_plants(day):
    assert len(day.turns) >= 200
    assert len(day.text) > 6 * 1000, "a day the old 3,000-character cut cannot hold"
    assert {p.key for p in day.late_plants()} >= {"project", "dentist", "marathon", "permit"}
    assert all(p.index >= int(len(day.turns) * 0.85) for p in day.late_plants())
    say = dd.day_sim_say()
    assert [p.text for p in day.plants if p.key == "dentist"] == [say["d3-dentist"]]     # scenario 4's seed, verbatim
    assert [p.text for p in day.plants if p.key == "project"] == [say["d1-project"]]     # scenario 5's seed, verbatim


# ── the dense day: legacy misses the late plants, chunked finds them ────────

def test_chunked_mode_stores_every_planted_fact_including_the_late_ones(monkeypatch, day):
    svc = _Svc()
    brain = _world(monkeypatch, day, svc)
    result = _run_nightly(day)
    assert "error" not in result, result
    stored = _found(day, svc.texts(), "fact")
    assert stored == _keys(day, "fact"), f"missed: {_keys(day, 'fact') - stored}"
    assert _keys(day, "fact", late=True) <= stored
    # one fact call per chunk, never one for the whole thing
    assert 1 <= len(brain.of("facts")) <= digest_pack.max_chunks()
    # every fact is still anchored to the owner's verbatim words (the gate and the anchors are unchanged)
    for text, kw in svc.ingested:
        if kw.get("memory_type") != "emotional_moment":
            assert kw.get("anchor_text") and kw["anchor_text"] in day.text


def test_negative_control_legacy_mode_misses_the_late_plants(monkeypatch, day):
    """THE CONTROL: the same day, ``ZOE_DIGEST_CHUNKED=0`` -> the old cut. If this ever finds a late plant the fixture no longer measures the defect."""
    monkeypatch.setenv("ZOE_DIGEST_CHUNKED", "0")
    svc = _Svc()
    brain = _world(monkeypatch, day, svc)
    _run_nightly(day)
    stored = _found(day, svc.texts(), "fact")
    assert _keys(day, "fact", late=True).isdisjoint(stored), f"legacy mode found {stored & _keys(day, 'fact', late=True)}"
    assert "diet" in stored                                                 # the early plant: legacy reads the first 3,000 characters
    assert len(brain.of("facts")) == 1 and len(brain.of("emotional")) == 1  # one call each, as ever
    assert all(len(c["prompt"]) < 3000 + 2500 for c in brain.calls)         # the instruction text plus at most 3,000 characters of the day


def test_emotional_pass_finds_the_late_moments_when_chunked_and_not_when_legacy(monkeypatch, day):
    svc = _Svc()
    _world(monkeypatch, day, svc)
    _run_nightly(day)
    moments = svc.texts("emotional_moment")
    assert _found(day, moments, "emotion") == _keys(day, "emotion")

    monkeypatch.setenv("ZOE_DIGEST_CHUNKED", "0")
    svc2 = _Svc()
    _world(monkeypatch, day, svc2)
    _run_nightly(day)
    assert _found(day, svc2.texts("emotional_moment"), "emotion") == set()


class _CompatCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Cur:
    def __init__(self, rows=(), rowcount=0):
        self._rows, self.rowcount = [tuple(r) if isinstance(r, tuple) else (r,) for r in rows], rowcount

    def __await__(self):
        async def _s():
            return self
        return _s().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchall(self):
        return self._rows


class _LoopsDb:
    """The open-loop pass's db: the last-two-days SELECT is newest first and honours ITS ``LIMIT n``."""

    def __init__(self, turns):
        self.turns, self.inserts, self.limits = turns, [], []

    def execute(self, sql, params=()):
        head = " ".join(sql.split())
        if head.startswith("SELECT cm.content"):
            n = int(re.search(r"LIMIT\s+(\d+)", head).group(1))
            self.limits.append(n)
            return _Cur([(t,) for t in reversed(self.turns)][:n])
        if head.startswith("SELECT loop_text") or head.startswith("SELECT id, loop_text"):
            return _Cur([])
        if head.startswith("UPDATE open_loops"):
            return _Cur(rowcount=0)
        assert head.startswith("INSERT INTO open_loops"), head[:80]
        self.inserts.append(params)
        return _Cur(rowcount=1)


def _loops(monkeypatch, day):
    db = _LoopsDb(day.turns)
    monkeypatch.setattr(db_compat, "get_compat_db", lambda: _CompatCtx(db))
    brain = Brain(day).install(monkeypatch)
    result = asyncio.run(md._extract_open_loops("demo-user"))
    return db, brain, result


def _loop_plants(day):
    return {p.key for p in day.plants if "loop" in p.kinds}


def test_open_loops_chunked_reads_the_whole_two_days(monkeypatch, day):
    db, brain, result = _loops(monkeypatch, day)
    assert result["status"] == "ok" and db.limits == [600]
    got = {p.key for p in day.plants if "loop" in p.kinds and any(p.needle in i[1].lower() for i in db.inserts)}
    assert got == _loop_plants(day), f"missed {_loop_plants(day) - got}"
    assert 1 <= len(brain.of("loops")) <= digest_pack.max_chunks()


def test_open_loops_negative_control_legacy_reads_only_the_newest_fifty(monkeypatch):
    """Legacy: newest 50 rows inside a 3,000-character budget, so a loop planted EARLY in the window is never read."""
    monkeypatch.setenv("ZOE_DIGEST_CHUNKED", "0")
    early = dd.dense_day()
    assert [p for p in early.plants if p.key == "assessment"][0].index < 0.5 * len(early.turns)
    db, brain, result = _loops(monkeypatch, early)
    assert db.limits == [50]
    seen = {p.key for p in early.plants if "loop" in p.kinds and any(p.needle in i[1].lower() for i in db.inserts)}
    assert "assessment" not in seen and len(brain.of("loops")) == 1
    monkeypatch.delenv("ZOE_DIGEST_CHUNKED")                              # the same day, chunked: the early loop is read too
    db2, _b2, _r2 = _loops(monkeypatch, early)
    assert any("assessment" in i[1].lower() for i in db2.inserts)


# ── the budget, the slot and the timeouts ───────────────────────────────────

def test_every_prompt_fits_the_slot_with_the_reply_cap(monkeypatch, day):
    brain = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    assert brain.calls
    for c in brain.calls:
        used = digest_pack.est_tokens(c["system"]) + digest_pack.est_tokens(c["prompt"]) + c["max_tokens"]
        assert used + digest_pack.MARGIN_TOKENS <= CTX, (c["system"][:30], used)


def test_a_smaller_slot_makes_smaller_chunks_that_still_fit(monkeypatch, day):
    monkeypatch.setenv("ZOE_BRAIN_SLOT_TOKENS", "4096")
    svc = _Svc()
    brain = _world(monkeypatch, day, svc)
    _run_nightly(day)
    assert len(brain.of("facts")) >= 2
    for c in brain.calls:
        assert digest_pack.est_tokens(c["system"]) + digest_pack.est_tokens(c["prompt"]) + c["max_tokens"] + digest_pack.MARGIN_TOKENS <= 4096
    assert _found(day, svc.texts(), "fact") == _keys(day, "fact")


def test_calls_per_member_night_are_bounded_however_long_the_day(monkeypatch):
    monkeypatch.setenv("ZOE_DIGEST_MAX_CHUNKS", "2")      # the knob: 600 short turns fit three chunks, so the cap is lowered to see it bind
    huge = dd.dense_day("dense-huge", n_turns=3000)
    svc = _Svc()
    brain = _world(monkeypatch, huge, svc)
    caplog_lines: list[str] = []
    monkeypatch.setattr(md._loops_log, "info", lambda fmt, *a: caplog_lines.append(fmt % a if a else fmt))
    # the loader reads at most 600 rows; the pack step then holds the day to <= max_chunks calls per pass
    _run_nightly(huge)
    cap = digest_pack.max_chunks()
    assert len(brain.of("facts")) <= cap and len(brain.of("emotional")) <= cap
    line = next(ln for ln in caplog_lines if ln.startswith("DIGEST_COVERAGE job=nightly"))
    fields = dict(kv.split("=", 1) for kv in line.split()[1:])
    assert int(fields["calls"]) == len(brain.calls) <= 2 * cap
    assert int(fields["skipped_cap"]) > 0 and int(fields["turns_read"]) < int(fields["turns_total"])   # said out loud, not silent


def test_timeouts_scale_with_output_size_at_the_configured_decode_rate(monkeypatch, day):
    brain = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    legacy = {"facts": 45.0, "emotional": 30.0}
    for kind, floor in legacy.items():
        for c in brain.of(kind):
            want = digest_pack.timeout_for(digest_pack.est_tokens(c["system"]) + digest_pack.est_tokens(c["prompt"]), c["max_tokens"])
            assert c["timeout"] == max(floor, want) and c["timeout"] >= floor
    # a slower brain (the config read, not a constant): a 512-token reply at 8 tok/s needs ~64 s of decode alone - more than the old flat 45 s
    monkeypatch.setenv("ZOE_DIGEST_DECODE_TOK_S", "8")
    brain2 = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    assert all(c["timeout"] >= 512 / 8 for c in brain2.of("facts"))
    assert all(c["timeout"] > 45.0 for c in brain2.of("facts"))
    # the 12B night window's scale still multiplies on top
    monkeypatch.setenv("ZOE_DIGEST_LLM_TIMEOUT_SCALE", "4")
    brain3 = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    assert all(c["timeout"] >= 4 * 512 / 8 for c in brain3.of("facts"))


def test_legacy_mode_keeps_the_fixed_timeouts(monkeypatch, day):
    monkeypatch.setenv("ZOE_DIGEST_CHUNKED", "0")
    brain = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    assert [c["timeout"] for c in brain.of("facts")] == [45.0] and [c["timeout"] for c in brain.of("emotional")] == [30.0]


# ── the count line ──────────────────────────────────────────────────────────

def _coverage(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(md._loops_log, "info", lambda fmt, *a: lines.append(fmt % a if a else fmt))
    return lines


def _fields(line):
    return dict(kv.split("=", 1) for kv in line.split()[1:])


def test_digest_coverage_line_says_how_much_of_the_day_was_read(monkeypatch, day):
    lines = _coverage(monkeypatch)
    brain = _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    f = _fields(next(ln for ln in lines if ln.startswith("DIGEST_COVERAGE job=nightly")))
    assert f["turns_total"] == f["turns_read"] == str(len(day.turns))
    assert f["calls"] == str(len(brain.calls)) and int(f["chunks"]) >= 2 and f["failed"] == "0" and f["chunked"] == "1"

    # negative control: legacy mode reports what it actually read - a small fraction of the same day
    monkeypatch.setenv("ZOE_DIGEST_CHUNKED", "0")
    lines.clear()
    _world(monkeypatch, day, _Svc())
    _run_nightly(day)
    g = _fields(next(ln for ln in lines if ln.startswith("DIGEST_COVERAGE job=nightly")))
    assert g["chunked"] == "0" and int(g["turns_total"]) == 200                 # the legacy 200-row cap
    assert int(g["turns_read"]) < 0.3 * len(day.turns)                          # the 3,000-character cut


def test_open_loop_run_has_its_own_coverage_line(monkeypatch, day):
    lines = _coverage(monkeypatch)
    _loops(monkeypatch, day)
    f = _fields(next(ln for ln in lines if ln.startswith("DIGEST_COVERAGE job=open_loops")))
    # turns_total here is the turns left after Zoe's own mechanics (timers, lights, music) are skipped; the rest are all read
    assert f["turns_total"] == f["turns_read"] and int(f["calls"]) == int(f["chunks"]) >= 1 and f["chunked"] == "1"


# ── the reduce, the gate, the failure modes ─────────────────────────────────

def test_the_same_fact_from_two_chunks_is_stored_once_and_a_richer_one_replaces_the_thinner():
    thin = {"type": "profile", "fact": "User is training for a half-marathon", "quote": "q1"}
    same = {"type": "profile", "fact": "User is training for a half marathon.", "quote": "q2"}
    rich = {"type": "profile", "fact": "User is training for the Rottnest half-marathon in February", "quote": "q3"}
    other = {"type": "profile", "fact": "User walks the dog Juniper every morning", "quote": "q4"}
    merged = md._merge_by_text([[thin, other], [same, rich]], "fact")
    assert [m["quote"] for m in merged] == ["q3", "q4"]
    assert md._merge_by_text([[thin, same]], "fact") == [thin, same]            # ONE chunk is returned untouched (the legacy answer)
    assert len(md._merge_by_text([[thin], [other] * 5], "fact", 3)) <= 3        # the per-night fact cap


def test_a_later_correction_is_never_merged_away_as_a_duplicate_of_the_fact_it_corrects():
    old = {"type": "profile", "fact": "User works at the hospital pharmacy", "quote": "q1"}
    fix = {"type": "profile", "fact": "User no longer works at the hospital pharmacy", "quote": "q2"}
    assert md._merge_by_text([[old], [fix]], "fact") == [old, fix]
    assert md._merge_by_text([[old], [dict(old, quote="q3")]], "fact") == [old]       # a true restatement is still dropped


def test_a_richer_answer_without_valid_evidence_cannot_displace_the_backed_earlier_one():
    thin = {"type": "profile", "fact": "User is training for a half-marathon", "quote": "I am training for a half-marathon"}
    rich = {"type": "profile", "fact": "User is training for the Rottnest half-marathon in February", "quote": "never said this"}
    day_text = "I am training for a half-marathon"
    ok = lambda it: md.fact_anchor(it, day_text) is not None          # noqa: E731
    assert md._merge_by_text([[thin], [rich]], "fact", supported=ok) == [thin]
    assert md._merge_by_text([[thin], [rich]], "fact") == [rich]       # (without the gate hook the richer one replaces, as before)


def test_the_fact_cap_bounds_a_single_chunks_answer_too():
    facts = [{"type": "profile", "fact": "User likes thing %d" % i} for i in range(5)]
    assert md._merge_by_text([facts], "fact", 2) == facts[:2]


def test_a_fabricated_quote_is_still_not_stored_in_chunked_mode(monkeypatch, day):
    """The observation gate is unchanged: a fact whose 'quote' the owner never said is held, wherever in the day the model put it."""
    svc = _Svc()
    _world(monkeypatch, day, svc)
    real = dd.oracle_reply

    def liar(system, prompt, d):
        if "fact extractor" in system.lower() and "Kestrel" in prompt:
            return json.dumps([{"type": "profile", "fact": "User is the CEO of Kestrel Holdings", "quote": "I am the CEO of Kestrel Holdings"}])
        return real(system, prompt, d)

    monkeypatch.setattr(dd, "oracle_reply", liar)
    _run_nightly(day)
    assert not any("ceo" in t.lower() for t, kw in svc.ingested if kw.get("status") == "approved" and not kw.get("hold"))


def test_one_failed_chunk_costs_that_chunk_only_inside_a_nightly_run(monkeypatch, day):
    monkeypatch.setenv("ZOE_DIGEST_CHUNK_TOKENS", "900")          # four chunks, so one failing leaves the others
    svc = _Svc()
    brain = _world(monkeypatch, day, svc, fail_on={1})            # the FIRST fact call times out
    result = _run_nightly(day)
    assert "error" not in result
    stored = _found(day, svc.texts(), "fact")
    assert stored and _keys(day, "fact", late=True) <= stored      # the second chunk (the late plants) still landed
    assert "diet" not in stored                                    # the failed chunk's plant waits for tomorrow's overlapping window
    assert len(brain.of("facts")) >= 3


def test_every_chunk_failing_is_still_an_extractor_error_row(monkeypatch, day):
    _world(monkeypatch, day, _Svc(), fail_on=set(range(1, 99)))
    result = _run_nightly(day)
    assert result["error"].startswith("extractor_failed:")


def test_outside_a_nightly_run_any_failed_chunk_raises_so_a_watermark_holds(monkeypatch, day):
    """memory_idle_consolidation calls the extractor with no run record: a partial answer must not advance its watermark."""
    monkeypatch.setenv("ZOE_DIGEST_CHUNK_TOKENS", "900")
    brain = Brain(day, fail_on={2}).install(monkeypatch)
    with pytest.raises(md.ExtractorError):
        asyncio.run(md._extract_facts_with_gemma(day.text))
    assert len(brain.of("facts")) == 2          # stopped at the first failed chunk


def test_a_short_day_sends_exactly_the_legacy_prompt_in_one_call(monkeypatch):
    short = dd.DenseDay("short", turns=["My dog Biscuit is two years old and I walk her every morning before work.", "Set a timer for five minutes."])
    short.plants = []
    brain = Brain(short).install(monkeypatch)
    text = "\n".join(short.turns)
    asyncio.run(md._extract_facts_with_gemma(text))
    assert len(brain.calls) == 1
    assert brain.calls[0]["prompt"] == md._EXTRACTION_PROMPT.format(chat_text=text)
    assert brain.calls[0]["timeout"] == 45.0
