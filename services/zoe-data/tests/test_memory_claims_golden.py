"""The memory-correction GOLDEN SET: schema, oracle, ratchet and the baseline pin (design note section G, build step 2).

``fixtures/memory_claims_golden.json`` is the review contract for every change to how Zoe corrects stored memory: each case has the
turns said, the rows stored before and the stored outcome an honest implementation must reach. ``fixtures/memory_claims_recorded/
oracle.json`` holds the claim an honest reader would emit per case (nothing consumes it yet except the schema check here).
``fixtures/memory_claims_golden.lock`` is the ratchet: case ids are only ever added and a changed ``expect`` needs a reason.

THIS FILE DOES NOT REQUIRE TODAY'S CODE TO PASS THE CASES: ``labs/memory-claims-lab/run_baseline.py`` scores today's code as a report,
and the last test pins that score to ``baseline-today.json`` so a heuristic patch that moves it shows up as a diff. No skip / xfail
markers in this file. Write the lock: ``python3 test_memory_claims_golden.py --write-lock`` (a deleted id needs ``--allow-removal <ids> --reason "..."``, recorded in ``memory_claims_golden.removals.json``). Synthetic data only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

FIX = Path(__file__).parent / "fixtures"
GOLDEN_PATH = FIX / "memory_claims_golden.json"
ORACLE_PATH = FIX / "memory_claims_recorded" / "oracle.json"
LOCK_PATH = FIX / "memory_claims_golden.lock"
LAB = Path(__file__).resolve().parents[3] / "labs" / "memory-claims-lab"
CASES = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["cases"]
ORACLE = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))["oracle"]
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8")) if LOCK_PATH.exists() else {}

CLASSES = {"correction", "kind", "emergency", "forget", "log"}
ATTRIBUTES = {"event_date", "origin_date", "birthday", "age", "amount", "measure", "count", "time_of_day", "schedule", "location",
              "employer", "partner", "diet"}
EVENTS = {"fall", "heart", "stroke", "breathing", "bleeding", "worst_headache"}
CLAIM_TIMES = {"current", "past", "future", "hypothetical", "conditional", "question"}
#: appendix rows 1-22 of the design note: every one needs at least one case
EVIDENCE = set(range(1, 23))
EXPECT_KEYS = {"rows_after", "left_as_mined", "invalid_rule", "kind_decisions", "events", "dropped_stale", "log_has_no", "registry",
               "side_effects", "not_record_expert", "registry_gone", "registry_rows"}


def expect_sha(case) -> str:
    return hashlib.sha256(json.dumps(case["expect"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_the_set_is_big_and_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 60
    assert {c["class"] for c in CASES} == CLASSES


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_case_matches_the_schema(case):
    assert case["class"] in CLASSES and re.fullmatch(r"[A-Z]\d{2}[a-z]?", case["id"])
    assert case["member"].startswith("demo_"), "synthetic members only"
    assert case["source"] and case["note"]
    assert case["reason_if_changed"] is None or isinstance(case["reason_if_changed"], str)
    assert set(case["expect"]) <= EXPECT_KEYS and "left_as_mined" in case["expect"] and "invalid_rule" in case["expect"]
    assert case["expect"]["invalid_rule"] is None or 1 <= case["expect"]["invalid_rule"] <= 20
    assert [t["t"] for t in case["turns"]] == list(range(len(case["turns"])))
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", t["at"]) for t in case["turns"]), "fixed dates only"
    assert all(i < len(case["turns"]) for i in case["flag_turns"])
    row_ids = [r["id"] for r in case["rows_before"]]
    assert len(row_ids) == len(set(row_ids))
    assert all(r["status"] == "approved" for r in case["rows_before"])
    assert {r["id"] for r in case["expect"].get("rows_after", [])} <= set(row_ids)
    ids = [g["id"] for g in case["graph"]]
    assert len(ids) == len(set(ids))
    kinds = [m for m in case["mined"] if m["check"] == "kind"]
    events = [m for m in case["mined"] if m["check"] == "event"]
    assert all(m["check"] in ("kind", "event") for m in case["mined"])
    if "kind_decisions" in case["expect"]:
        assert len(case["expect"]["kind_decisions"]) == len(kinds)
        assert set(case["expect"]["kind_decisions"]) <= {"add", "refuse", "hold_pending"}
    if "events" in case["expect"]:
        assert len(case["expect"]["events"]) == len(events)
    if "dropped_stale" in case["expect"]:
        assert len(case["expect"]["dropped_stale"]) == len(case["late_mined"])
    if case["class"] == "log":
        assert case["expect"].get("log_has_no")
    if case["requires"]:
        assert case["requires"] in ("registry", "marker") and case["class"] == "forget"


def test_every_evidence_class_has_a_case():
    seen = {e for c in CASES for e in c["evidence"]}
    assert EVIDENCE <= seen, sorted(EVIDENCE - seen)


def test_the_note_cases_and_the_unpatched_shapes_are_present():
    ids = {c["id"] for c in CASES}
    note = {f"K{i:02d}" for i in (1, 2, 3, 5, 6, 7, 8, 9, 10)} | {f"C{i:02d}" for i in range(1, 44)} | \
        {f"E{i:02d}" for i in range(1, 16)} | {f"F{i:02d}" for i in range(1, 5)}
    assert note <= ids, sorted(note - ids)
    assert {"K04a", "K04b", "C25b", "C23b", "C21b", "C22b", "C10b", "C45", "E06b", "E06c", "E06d", "E06e"} <= ids


def test_cases_name_only_invented_people():
    text = GOLDEN_PATH.read_text(encoding="utf-8") + ORACLE_PATH.read_text(encoding="utf-8")
    assert not re.search(r"(?=[A-Za-z0-9+/_-]*\d)[A-Za-z0-9+/_-]{32,}", text), "a token-shaped string"
    assert not re.search(r"@\w+\.\w+|https?://|\b\d{3}[- ]?\d{3}[- ]?\d{4}\b", text)


def test_the_oracle_covers_every_case_and_follows_the_claim_schema():
    assert set(ORACLE) == {c["id"] for c in CASES}
    by_id = {c["id"]: c for c in CASES}
    for cid, out in ORACLE.items():
        assert set(out) == {"claims", "abstain"} and out["abstain"] in (None, "no_correction", "unsure")
        assert (out["abstain"] is None) == bool(out["claims"]), cid
        assert len(out["claims"]) <= 3 or cid == "C04"
        for c in out["claims"]:
            turns = by_id[cid]["turns"]
            assert c["type"] in ("correction", "kind", "emergency") and 0 <= c["turn"] < len(turns)
            assert 0 < len(c["quote"]) <= 160 and " ".join(c["quote"].lower().split()) in " ".join(turns[c["turn"]]["text"].lower().split()), cid
            assert c["subject"]
            if c["type"] == "correction":
                assert c["attribute"] in ATTRIBUTES and c["new"] and c["time"] in CLAIM_TIMES and "old" in c, cid
            if c["type"] == "kind":
                assert c["kind"] in ("person", "animal")
            if c["type"] == "emergency":
                assert c["event"] in EVENTS and c["time"] in CLAIM_TIMES, cid


# -- the ratchet ---------------------------------------------------------------------------------------------------------------------

def test_case_ids_are_only_ever_added():
    assert LOCK, "the lock is missing: python3 test_memory_claims_golden.py --write-lock"
    assert set(LOCK) <= {c["id"] for c in CASES}, f"cases removed: {sorted(set(LOCK) - {c['id'] for c in CASES})}"
    assert {c["id"] for c in CASES} <= set(LOCK), "a new case is not in the lock yet (--write-lock)"


def test_a_changed_expectation_carries_a_reason():
    for c in CASES:
        if LOCK.get(c["id"]) != expect_sha(c):
            assert (c["reason_if_changed"] or "").strip(), f"{c['id']}: expect changed with no reason_if_changed"


def test_no_skip_or_xfail_in_this_file():
    src = Path(__file__).read_text(encoding="utf-8")
    marks = [m for m in ("mark." + "skip", "mark." + "xfail", "pytest." + "skip", "pytest." + "xfail") if m in src]
    assert not marks


# -- the baseline pin ----------------------------------------------------------------------------------------------------------------

def test_the_baseline_runner_reproduces_the_committed_score():
    """Today's code scored by the lab runner equals ``baseline-today.json``. A change that moves it (a heuristic patch, or a redesign
    that now passes more cases) must re-record it with ``run_baseline.py --write-baseline`` so the diff is reviewed."""
    spec = importlib.util.spec_from_file_location("memory_claims_run_baseline", LAB / "run_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    got = mod.run_all()
    want = json.loads((LAB / "baseline-today.json").read_text(encoding="utf-8"))
    assert got["totals"] == want["totals"] and got["by_class"] == want["by_class"]
    assert {i: r["status"] for i, r in got["results"].items()} == {i: r["status"] for i, r in want["results"].items()}


# -- the lock writer: a removal is always explicit and visible -------------------------------------------------------------------------

REMOVALS_PATH = FIX / "memory_claims_golden.removals.json"
REMOVALS = json.loads(REMOVALS_PATH.read_text(encoding="utf-8")) if REMOVALS_PATH.exists() else []


def write_lock(cases, lock_path, removals_path, allow_removal=(), reason=""):
    """Rebuild the lock from ``cases``. Refuses (ValueError) when the existing lock holds ids that ``cases`` lacks, unless
    ``allow_removal`` names EXACTLY those ids and a non-empty ``reason`` is given; each removal is then appended to the removals record,
    so it is visible in review. A changed ``expect`` still needs ``reason_if_changed``."""
    old = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    have = {c["id"] for c in cases}
    missing, allowed = sorted(set(old) - have), sorted(set(allow_removal))
    if missing != allowed:
        if missing:
            raise ValueError(f"case ids missing from the fixture: {missing}; removing needs --allow-removal naming exactly them plus --reason"
                             if not allowed else f"--allow-removal {allowed} does not match the missing ids {missing}")
        raise ValueError(f"--allow-removal names ids that are not missing: {allowed}")
    if missing and not (reason or "").strip():
        raise ValueError(f"removing {missing} needs a non-empty --reason")
    new = {}
    for c in cases:
        sha = expect_sha(c)
        if c["id"] in old and old[c["id"]] != sha and not (c["reason_if_changed"] or "").strip():
            raise ValueError(f"{c['id']}: expect changed without a reason_if_changed")
        new[c["id"]] = sha
    if missing:
        log = json.loads(removals_path.read_text(encoding="utf-8")) if removals_path.exists() else []
        log += [{"id": i, "reason": reason.strip()} for i in missing]
        removals_path.write_text(json.dumps(log, indent=1) + "\n", encoding="utf-8")
    lock_path.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return new


def test_every_removed_id_is_on_record_with_a_reason():
    ids = {c["id"] for c in CASES}
    assert all(r["id"] not in ids and r["reason"].strip() for r in REMOVALS)
    assert len({r["id"] for r in REMOVALS}) == len(REMOVALS)


@pytest.fixture
def lockdir(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text(LOCK_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return lock, tmp_path / "removals.json"


def test_the_lock_writer_refuses_a_deleted_case_and_names_it(lockdir):
    lock, rem = lockdir
    before = lock.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="K11"):
        write_lock([c for c in CASES if c["id"] != "K11"], lock, rem)
    assert lock.read_text(encoding="utf-8") == before and not rem.exists()


def test_a_named_removal_with_a_reason_succeeds_and_is_recorded(lockdir):
    lock, rem = lockdir
    new = write_lock([c for c in CASES if c["id"] != "K11"], lock, rem, ["K11"], "superseded by K11b")
    assert "K11" not in new and "K11" not in json.loads(lock.read_text(encoding="utf-8"))
    assert json.loads(rem.read_text(encoding="utf-8")) == [{"id": "K11", "reason": "superseded by K11b"}]
    with pytest.raises(ValueError, match="reason"):
        write_lock([c for c in CASES if c["id"] != "K10"], lock, rem, ["K10"], " ")


def test_allow_removal_naming_an_id_that_is_not_missing_fails(lockdir):
    lock, rem = lockdir
    with pytest.raises(ValueError, match="not missing"):
        write_lock(CASES, lock, rem, ["K11"], "why")
    with pytest.raises(ValueError, match="does not match"):
        write_lock([c for c in CASES if c["id"] != "K11"], lock, rem, ["K11", "K10"], "why")


def test_an_ordinary_append_still_works_and_changes_nothing_else(lockdir):
    lock, rem = lockdir
    extra = dict(CASES[0], id="K99", expect=dict(CASES[0]["expect"]))
    new = write_lock([*CASES, extra], lock, rem)
    assert set(new) == set(LOCK) | {"K99"} and all(new[i] == LOCK[i] for i in LOCK) and not rem.exists()


def test_the_committed_lock_is_what_the_writer_produces():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        lock = Path(d) / "l"
        lock.write_text(LOCK_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        write_lock(CASES, lock, Path(d) / "r")
        assert lock.read_text(encoding="utf-8") == LOCK_PATH.read_text(encoding="utf-8")


if __name__ == "__main__":
    if "--write-lock" in sys.argv:
        args = sys.argv[1:]
        allow = [a for a in args[args.index("--allow-removal") + 1:] if not a.startswith("--")] if "--allow-removal" in args else []
        why = args[args.index("--reason") + 1] if "--reason" in args else ""
        try:
            print(f"wrote {len(write_lock(CASES, LOCK_PATH, REMOVALS_PATH, allow, why))} entries")
        except ValueError as exc:
            sys.exit(str(exc))
