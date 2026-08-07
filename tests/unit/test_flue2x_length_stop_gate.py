"""The Flue-2.x flip's length-stop gate must actually SEE a truncated reply.

`labs/flue-zoe-brain-2x/parity/count_length_stops.py` is the post-flip assertion the
2026-08-06 attempt did not have. That run scored `OK=18 CANT_DO=1 EMPTY=1` and was
reported as a near-pass while the lane was truncating replies mid-sentence on turns the
harness scored OK — pi-ai 0.83's output clamp had cut the budget to single digits. Verdict
counts are structurally blind to that; only the store's `stopReason` is not.

A gate nobody has seen go red is not a gate, so both directions are pinned here against a
store built to the real Flue schema: a clean store passes, and a store carrying the exact
truncation shape recorded on 2026-08-06 (`assistant_message_completed`, `stopReason:
"length"`, `usage.output: 8`) fails with exit code 1. The spilled-batch path — a >1MB
batch stored as chunk rows behind a `{"$flueChunkCount": N}` sentinel — is covered too,
because a length stop that hid inside a spilled batch would be invisible to a naive
`LIKE '%length%'` scan.

The subtler case, and the one that actually happens: an EMPTY store must FAIL. The sidecar
creates its database at boot, so a replay that never reached the 2.x lane (`ZOE_FLUE_WIRE`
unset, zoe-data still pointed at :3578, the restart forgotten) leaves a valid database with
zero replies in it. Counting zero length-stops there and reporting PASS would green-light
the flip on no evidence at all — the exact shape of the failure the gate exists to catch.
`--since` gets the same treatment, since a mis-typed timestamp silently examines nothing.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "labs/flue-zoe-brain-2x/parity/count_length_stops.py"

# Abridged from @flue/runtime@2.0.1's conversation-stream-store DDL — only the two
# tables the gate reads. Column names and types match the real store.
_SCHEMA = """
CREATE TABLE flue_conversation_stream_batches (
  path TEXT NOT NULL, seq INTEGER NOT NULL, producer_id TEXT NOT NULL,
  producer_epoch INTEGER NOT NULL, producer_sequence INTEGER NOT NULL,
  data TEXT NOT NULL, submission_id TEXT, attempt_id TEXT,
  PRIMARY KEY (path, seq)
);
CREATE TABLE flue_conversation_stream_batch_chunks (
  path TEXT NOT NULL, seq INTEGER NOT NULL, chunk_index INTEGER NOT NULL,
  chunk_count INTEGER NOT NULL, data TEXT NOT NULL,
  PRIMARY KEY (path, seq, chunk_index)
);
"""

_STREAM = "agents/zoe/replay-test"


def _completed(
    stop_reason: str, output: int, timestamp: str = "2026-08-07T02:26:40.743Z"
) -> dict:
    """One `assistant_message_completed` record, in the store's real shape."""
    return {
        "v": 1,
        "id": f"record_{stop_reason}_{output}",
        "type": "assistant_message_completed",
        "timestamp": timestamp,
        "submissionId": "sub_01KZD19G7FYQDYJ2HTS329TGPB",
        "stopReason": stop_reason,
        "usage": {"input": 3901, "output": output, "cacheRead": 0, "cacheWrite": 0},
    }


def _build_store(path: Path, records_per_batch: list[list[dict]], spill: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        for seq, records in enumerate(records_per_batch):
            blob = json.dumps(records)
            if spill:
                # The real spill encoding: sentinel in the batch row, payload in chunks.
                parts = [blob[:5], blob[5:]]
                conn.execute(
                    "INSERT INTO flue_conversation_stream_batches "
                    "(path, seq, producer_id, producer_epoch, producer_sequence, data) "
                    "VALUES (?, ?, 'p', 0, ?, ?)",
                    (_STREAM, seq, seq, json.dumps({"$flueChunkCount": len(parts)})),
                )
                for index, part in enumerate(parts):
                    conn.execute(
                        "INSERT INTO flue_conversation_stream_batch_chunks "
                        "(path, seq, chunk_index, chunk_count, data) VALUES (?, ?, ?, ?, ?)",
                        (_STREAM, seq, index, len(parts), part),
                    )
            else:
                conn.execute(
                    "INSERT INTO flue_conversation_stream_batches "
                    "(path, seq, producer_id, producer_epoch, producer_sequence, data) "
                    "VALUES (?, ?, 'p', 0, ?, ?)",
                    (_STREAM, seq, seq, blob),
                )
        conn.commit()
    finally:
        conn.close()


def _run(db: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), "--db", str(db), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_gate_script_exists() -> None:
    assert GATE.is_file(), f"the flip runbook's length-stop gate is missing: {GATE}"


def test_healthy_store_passes(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    _build_store(db, [[_completed("stop", 42)], [_completed("stop", 17)]])
    result = _run(db)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: 0 length-stopped" in result.stdout
    assert "2 examined" in result.stdout, "a pass must say how much it checked"


def test_empty_store_fails_rather_than_passing_vacuously(tmp_path: Path) -> None:
    """THE case that actually happens: the replay never reached the 2.x lane.

    The sidecar creates its DB at boot, so "no length stops" in an empty store is
    not evidence of anything. A PASS here would green-light a flip on nothing.
    """
    db = tmp_path / "empty.db"
    _build_store(db, [])
    result = _run(db)
    assert result.returncode == 1, "an empty store must not pass"
    assert "FAIL: 0 assistant replies" in result.stdout
    assert "PASS" not in result.stdout


def test_store_with_no_assistant_replies_fails(tmp_path: Path) -> None:
    """Rows present, but none of them an assistant reply — still no evidence."""
    db = tmp_path / "no-replies.db"
    _build_store(db, [[{"v": 1, "type": "user_message", "timestamp": "2026-08-07T02:00:00Z"}]])
    result = _run(db)
    assert result.returncode == 1
    assert "FAIL: 0 assistant replies" in result.stdout


def test_since_scopes_to_this_run(tmp_path: Path) -> None:
    """Stale hits from an earlier attempt must not fail a later, clean run.

    Without this the operator's only way to get a green gate is to delete the
    store — which destroys the very evidence the gate reads.
    """
    db = tmp_path / "mixed.db"
    _build_store(
        db,
        [
            [_completed("length", 8, timestamp="2026-08-06T01:00:00.000Z")],
            [_completed("stop", 42, timestamp="2026-08-07T09:00:00.000Z")],
        ],
    )
    assert _run(db).returncode == 1, "unscoped, the old hit still fails"

    scoped = _run(db, "--since", "2026-08-07T00:00:00.000Z")
    assert scoped.returncode == 0, scoped.stdout + scoped.stderr
    assert "1 examined" in scoped.stdout


def test_since_that_matches_nothing_fails(tmp_path: Path) -> None:
    """A mis-typed or future --since examines nothing; that is not a pass."""
    db = tmp_path / "future.db"
    _build_store(db, [[_completed("stop", 42, timestamp="2026-08-07T09:00:00.000Z")]])
    result = _run(db, "--since", "2027-01-01T00:00:00.000Z")
    assert result.returncode == 1
    assert "FAIL: 0 assistant replies" in result.stdout


def test_truncated_reply_fails_the_gate(tmp_path: Path) -> None:
    """The 2026-08-06 shape: a length stop at 8 output tokens, beside healthy turns."""
    db = tmp_path / "truncated.db"
    _build_store(db, [[_completed("stop", 42)], [_completed("length", 8)]])
    result = _run(db)
    assert result.returncode == 1, "a truncated reply must fail the gate"
    assert "FAIL: 1 length-stopped" in result.stdout
    assert "output=8" in result.stdout, "the recorded output budget must be reported"


def test_length_stop_inside_a_spilled_batch_is_found(tmp_path: Path) -> None:
    db = tmp_path / "spilled.db"
    _build_store(db, [[_completed("length", 1)]], spill=True)
    result = _run(db)
    assert result.returncode == 1, "a spilled batch must not hide a length stop"
    assert "FAIL: 1 length-stopped" in result.stdout


def test_missing_store_fails_rather_than_reporting_clean(tmp_path: Path) -> None:
    """A gate that reports PASS when there is nothing to read is worse than none."""
    result = _run(tmp_path / "absent.db")
    assert result.returncode != 0
    assert "PASS" not in result.stdout


def test_out_of_order_chunks_fail_instead_of_reassembling_wrongly(tmp_path: Path) -> None:
    """Upstream validates chunk_index/chunk_count per row; so must this.

    A laxer reassembly would silently concatenate chunks in the wrong order,
    producing corrupt JSON or — worse — dropping a length-stop record.
    """
    db = tmp_path / "badchunks.db"
    _build_store(db, [[_completed("length", 1)]], spill=True)
    conn = sqlite3.connect(db)
    try:
        # Renumber the chunk indices to 1,2 while the sentinel still says 2.
        # Read-delete-reinsert, because an in-place bump collides with the primary
        # key of the row it is about to become.
        rows = conn.execute(
            "SELECT path, seq, chunk_index, chunk_count, data "
            "FROM flue_conversation_stream_batch_chunks ORDER BY chunk_index"
        ).fetchall()
        conn.execute("DELETE FROM flue_conversation_stream_batch_chunks")
        conn.executemany(
            "INSERT INTO flue_conversation_stream_batch_chunks "
            "(path, seq, chunk_index, chunk_count, data) VALUES (?, ?, ?, ?, ?)",
            [(p, s, i + 1, c, d) for (p, s, i, c, d) in rows],
        )
        conn.commit()
    finally:
        conn.close()
    result = _run(db)
    assert result.returncode != 0
    assert "PASS" not in result.stdout
    assert "not contiguous" in result.stdout or "not contiguous" in result.stderr


def test_json_mode_is_machine_readable(tmp_path: Path) -> None:
    db = tmp_path / "json.db"
    _build_store(db, [[_completed("length", 3)]])
    proc = _run(db, "--json")
    assert proc.returncode == 1, "JSON mode must keep the same exit contract"
    result = json.loads(proc.stdout)
    assert result["count"] == 1
    assert result["examined"] == 1
    assert result["ok"] is False
    assert result["hits"][0]["output"] == 3
    assert result["hits"][0]["stream"] == _STREAM


def test_json_mode_reports_a_clean_run(tmp_path: Path) -> None:
    db = tmp_path / "json-clean.db"
    _build_store(db, [[_completed("stop", 42)]])
    proc = _run(db, "--json")
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert result["count"] == 0
    assert result["examined"] == 1
    assert result["ok"] is True
