"""``digest_pack`` - the nightly digest's pack step (lane 2: pure logic, no memory_digest import, no model, no database).

Pins the budget arithmetic the chunked passes share with the night mind (PR #1930: 2,400 turn-tokens at the 8,192 slot; the 650 tok/s prefill and
20 s slack timeout formula), the bounded-calls rule, the overflow accounting, and the dense-day fixture's shape - including the property the whole fix
rests on: a stand-in model that reads exactly what it is shown finds NONE of the late plants through the old 3,000-character cut and ALL of them through
the packed chunks. (The end-to-end proof through ``run_memory_digest`` is ``services/zoe-data/tests/test_digest_chunked_passes.py``.)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import digest_pack as dp

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "perf"))
import dense_day as dd  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (dp.ENV, dp.MAX_CHUNKS_ENV, dp.MAX_FACTS_ENV, dp.CHUNK_TOKENS_ENV, dp.DECODE_ENV, dp.SLOT_ENV):
        monkeypatch.delenv(name, raising=False)


# ── the flag and the config reads ───────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [(None, True), ("", True), ("1", True), ("on", True), ("0", False), ("false", False), ("No", False), (" OFF ", False)])
def test_flag_is_on_by_default_and_off_only_when_said(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv(dp.ENV, raw)
    assert dp.enabled() is want


def test_decode_rate_is_a_config_read_defaulting_to_the_measured_60(monkeypatch):
    assert dp.decode_tok_s() == 60.0
    monkeypatch.setenv(dp.DECODE_ENV, "5.4")
    assert dp.decode_tok_s() == 5.4
    monkeypatch.setenv(dp.DECODE_ENV, "nonsense")
    assert dp.decode_tok_s() == 60.0


def test_chunk_budget_is_the_night_minds_2400_at_8k_and_never_exceeds_what_the_slot_has_left(monkeypatch):
    assert dp.chunk_budget(600, 512) == 2400
    assert dp.chunk_budget(600, 512, ctx=16384) == 4800 and dp.chunk_budget(600, 512, ctx=32768) == 9600
    # a small slot: the room left after instructions + reply + margin wins over the wish
    assert dp.chunk_budget(600, 512, ctx=1800) == 1800 - 600 - 512 - dp.MARGIN_TOKENS
    assert dp.chunk_budget(5000, 5000, ctx=8192) == dp.MIN_BUDGET
    monkeypatch.setenv(dp.CHUNK_TOKENS_ENV, "900")
    assert dp.chunk_budget(600, 512) == 900


def test_timeout_is_prefill_plus_output_at_the_decode_rate_plus_slack(monkeypatch):
    assert dp.timeout_for(3000, 512, rate=60.0) == pytest.approx(3000 / 650 + 512 / 60 + 20, abs=0.1)
    assert dp.timeout_for(3000, 512, rate=8.0) == pytest.approx(3000 / 650 + 512 / 8 + 20, abs=0.1)     # the old 8 tok/s sizing: ~89 s, not 45
    assert dp.timeout_for(3000, 1024, rate=60.0) > dp.timeout_for(3000, 256, rate=60.0)                 # scales with the OUTPUT size
    monkeypatch.setenv(dp.DECODE_ENV, "8")
    assert dp.timeout_for(3000, 512) == dp.timeout_for(3000, 512, rate=8.0)


def test_call_timeout_is_never_shorter_than_the_fixed_one_the_pass_always_had():
    assert dp.call_timeout(45.0, 3000, 512) == 45.0                      # at 60 tok/s the output needs ~33 s: the old 45 s stays
    assert dp.call_timeout(45.0, 3000, 4000) > 45.0                      # a long output grows it


def test_when_night_mind_is_present_the_shared_arithmetic_is_identical():
    """Drift guard: PR #1930's ``night_mind`` carries the original of these helpers; once it is on this tree the two must agree."""
    try:
        nm = importlib.import_module("night_mind")
    except Exception:  # noqa: BLE001 - not merged yet / its own deps absent
        pytest.skip("night_mind not on this tree")
    for text in ("", "a", "x" * 1000, "hello world " * 77):
        assert dp.est_tokens(text) == nm.est_tokens(text)
    assert dp.PREFILL_TOK_S == nm.PREFILL_TOK_S and dp.BASE_CHUNK_TOKENS == nm.BASE_CHUNK_TOKENS
    for ctx in (8192, 16384, 32768):
        assert dp.default_chunk_tokens(ctx) == nm.default_chunk_tokens(ctx)
    assert dp.timeout_for(2800, 300, rate=8.0) == nm.timeout_for(2800, 300, 8.0)


# ── pack ────────────────────────────────────────────────────────────────────

LINES = [f"I told you about thing number {i}, remember my sister Tamsin is arriving on the {i % 28 + 1}th." for i in range(300)]


def test_every_chunk_fits_the_budget_and_no_turn_is_lost_or_reordered():
    budget = 1200
    packed = dp.pack_lines(LINES, budget, cap=20)
    assert packed.skipped_cap == 0 and packed.turns_total == 300
    assert all(dp.est_tokens(c.text) <= budget for c in packed.chunks)
    flat = [t for c in packed.chunks for t in c.turns]
    assert flat == list(range(300))
    assert "\n".join(c.text for c in packed.chunks).split("\n") == LINES


def test_pack_is_deterministic():
    assert dp.pack_lines(LINES, 1200, cap=20) == dp.pack_lines(LINES, 1200, cap=20)


def test_a_short_transcript_is_one_chunk_holding_the_text_byte_for_byte():
    text = "I walk my dog.\n\nSet a timer.\nHi"
    packed = dp.pack_text(text, 2400)
    assert [c.text for c in packed.chunks] == [text] and packed.turns_total == 3


def test_over_the_cap_the_highest_signal_turns_survive_in_time_order_and_the_rest_are_counted():
    lines = ["turn the lounge lights off"] * 300
    lines[10] = "I am so worried about my mum Ingrid and her hip operation on Friday."
    lines[290] = "My daughter Rowan starts at Fernhill school on the 3rd of February."
    packed = dp.pack_lines(lines, 150, cap=2)
    kept = [t for c in packed.chunks for t in c.turns]
    assert len(packed.chunks) <= 2 and 10 in kept and 290 in kept        # signal wins over position
    assert kept == sorted(kept)
    assert packed.skipped_cap == 300 - len(kept) > 0
    assert dp.pack_lines(lines, 150, cap=2).skipped_cap == packed.skipped_cap


def test_boundary_slack_sheds_the_lowest_signal_turn_not_the_newest():
    """Three 61-token turns do not fit two 100-token chunks (each needs its own); the cut must fall on the least useful turn, never on the newest."""
    pad = lambda lead: lead + " " + "x" * (int(61 * 3.3) - len(lead) - 3)          # noqa: E731 - ~61 estimated tokens
    lines = [pad("I am so worried about my mum Ingrid"), pad("turn the lights off"), pad("My daughter Rowan starts school on the 3rd")]
    packed = dp.pack_lines(lines, 100, cap=2)
    kept = {t for c in packed.chunks for t in c.turns}
    assert len(packed.chunks) <= 2 and kept == {0, 2} and packed.skipped_cap == 1


def test_one_overlong_turn_is_split_not_dropped_and_not_truncated():
    long = "I keep thinking about " + " ".join(f"word{i}" for i in range(2000))
    packed = dp.pack_lines(["short one", long, "short two"], 300, cap=50)
    assert all(dp.est_tokens(c.text) <= 300 for c in packed.chunks)
    assert "word1999" in "\n".join(c.text for c in packed.chunks)
    assert packed.turns_total == 3 and {t for c in packed.chunks for t in c.turns} == {0, 1, 2}


def test_the_default_cap_bounds_calls_per_pass_and_is_configurable(monkeypatch):
    assert dp.max_chunks() == 5 and len(dp.pack_lines(LINES * 20, 400).chunks) == 5
    monkeypatch.setenv(dp.MAX_CHUNKS_ENV, "2")
    assert len(dp.pack_lines(LINES * 20, 400).chunks) == 2


# ── coverage ────────────────────────────────────────────────────────────────

def test_coverage_line_format_and_counts():
    run = dp.RunCoverage()
    f, e = run.for_pass("facts"), run.for_pass("emotional")
    f.turns_total = e.turns_total = 240
    chunk = dp.Chunk("x", tuple(range(120)))
    f.chunks, f.calls = 2, 2
    f.mark_read(chunk)
    f.mark_read(dp.Chunk("y", tuple(range(100, 240))))
    e.chunks, e.calls, e.failed = 2, 2, 1
    line = run.line("nightly", "demo-user")
    assert line.startswith("DIGEST_COVERAGE job=nightly user=demo-user turns_total=240 turns_read=240 chunks=4 calls=4 failed=1 ")
    assert "facts_calls=2" in line and "emotional_turns_read=0" in line


def test_the_run_record_is_scoped_to_its_context():
    assert dp.current() is None
    run, token = dp.begin_run()
    assert dp.current() is run
    dp.end_run(token)
    assert dp.current() is None


# ── the dense day (the fixture, and the property the fix rests on) ──────────

def test_dense_day_is_deterministic_dense_and_has_late_plants():
    a, b = dd.dense_day(), dd.dense_day()
    assert a.turns == b.turns and len(a.turns) >= 200
    assert dd.dense_day("other").turns != a.turns
    assert [p.index for p in a.late_plants()] and all(p.index >= 0.85 * len(a.turns) for p in a.late_plants())
    assert all(a.turns[p.index] == p.text for p in a.plants)


def test_legacy_cut_reads_none_of_the_late_plants_and_the_packed_chunks_read_all():
    day = dd.dense_day()
    cut = day.text[:3000]
    legacy_found = {p.key for p in day.plants if p.text in cut}
    assert legacy_found <= {"diet"} and not any(p.key in legacy_found for p in day.late_plants())
    # the stand-in model reads exactly its prompt: through the old cut it reports nothing late
    legacy_reply = dd.oracle_reply("You are a precise fact extractor.", cut, day)
    assert all(p.fact not in legacy_reply for p in day.late_plants() if p.fact)

    packed = dp.pack_text(day.text, dp.chunk_budget(600, 512))
    assert len(packed.chunks) >= 2 and packed.skipped_cap == 0
    read = "\n".join(c.text for c in packed.chunks)
    assert all(p.text in read for p in day.plants)
    chunked_reply = "".join(dd.oracle_reply("You are a precise fact extractor.", c.text, day) for c in packed.chunks)
    assert all(p.fact in chunked_reply for p in day.plants if p.fact)


def test_the_oracle_answers_only_for_the_pass_it_is_asked():
    day = dd.dense_day()
    prompt = "\n".join(day.turns)
    assert "Kestrel" in dd.oracle_reply("You are a precise fact extractor.", prompt, day)
    assert "dentist" in dd.oracle_reply("You are an empathetic listener.", prompt, day)
    assert "Halloran" in dd.oracle_reply("You extract open loops from conversations.", prompt, day)
    assert dd.oracle_reply("something else", prompt, day) == "[]"


def test_invalid_numeric_settings_fall_back_with_one_warning_via_typed_env(monkeypatch, caplog):
    import logging
    import typed_env
    typed_env._warned.clear()
    monkeypatch.setenv(dp.MAX_CHUNKS_ENV, "lots")
    monkeypatch.setenv(dp.DECODE_ENV, "fast")
    with caplog.at_level(logging.WARNING, logger="typed_env"):
        assert dp.max_chunks() == 5 and dp.decode_tok_s() == 60.0
    assert sum("typed_env" in r.getMessage() for r in caplog.records) == 2
    monkeypatch.setenv(dp.MAX_CHUNKS_ENV, "99")
    assert dp.max_chunks() == 20                                          # the module's own bound still applies
