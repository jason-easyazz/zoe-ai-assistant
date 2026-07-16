"""Pins the pipeline state-store compaction tool.

The tool rewrites an operator state store IN PLACE, so the properties that make
that safe are pinned here: latest-per-ref survives byte-identically, order is
preserved (last-match-wins readers depend on it), an already-compacted file is a
no-op, dry-run never mutates, and the tail window that keeps
engineering_harness_loop's diagnostics intact is honoured.

Everything runs against tmp_path fixtures — never the live ~/.zoe store.
"""

from __future__ import annotations

import ast
import fcntl
import importlib.util
import json
import multiprocessing
import os
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "compact_pipeline_state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("compact_pipeline_state", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _record(task_ref: str, *, event: str = "transition", revision: int = 1, pad: int = 0) -> str:
    """A record shaped like the ones pipeline_store.save_state writes."""
    payload = {
        "event": event,
        "task_ref": task_ref,
        "phase": "implement",
        "status": "running",
        "state": {
            "task_ref": task_ref,
            "journal_revision": revision,
            "filler": "x" * pad,
        },
    }
    return json.dumps(payload, sort_keys=True)


def _write_store(path: Path, lines: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _read_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _latest_state_from_file(path: Path, task_ref: str) -> dict | None:
    """Mirror of pipeline_store._latest_state_from_lines (last match wins)."""
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("task_ref") != task_ref or "state" not in payload:
            continue
        latest = payload["state"]
    return latest


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    """3 task_refs x several snapshots each, interleaved, newest revision last."""
    path = tmp_path / "runs.jsonl"
    lines: list[str] = []
    for revision in range(1, 6):
        for ref in ("multica:aaa", "multica:bbb", "multica:ccc"):
            lines.append(_record(ref, revision=revision, pad=64))
    _write_store(path, lines)
    return path


def test_keeps_exactly_the_latest_record_per_ref(store: Path):
    report = mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)

    assert report["executed"] is True
    assert report["records"] == 15
    assert report["task_refs"] == 3
    records = _read_records(store)
    assert len(records) == 3
    assert {r["task_ref"] for r in records} == {"multica:aaa", "multica:bbb", "multica:ccc"}
    # the surviving record for each ref is the LATEST one, not an arbitrary one
    assert all(r["state"]["journal_revision"] == 5 for r in records)


def test_kept_records_are_byte_identical_and_ordered(store: Path):
    before = store.read_text(encoding="utf-8").splitlines()
    expected = [ln for ln in before if json.loads(ln)["state"]["journal_revision"] == 5]

    mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)

    after = store.read_text(encoding="utf-8").splitlines()
    # byte-for-byte, and in the original file order
    assert after == expected


def test_readers_see_the_same_latest_state_after_compaction(store: Path):
    before = {ref: _latest_state_from_file(store, ref) for ref in ("multica:aaa", "multica:bbb", "multica:ccc")}

    mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)

    after = {ref: _latest_state_from_file(store, ref) for ref in ("multica:aaa", "multica:bbb", "multica:ccc")}
    assert after == before


def test_is_a_no_op_on_an_already_compacted_file(store: Path):
    mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)
    digest = store.read_bytes()
    backups_before = set(store.parent.glob("*.backup-*"))

    report = mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)

    assert report["executed"] is False
    assert report["kept_records"] == report["records"]
    assert report["backup"] is None
    assert store.read_bytes() == digest
    # a no-op must not rewrite, nor take a fresh backup of an unchanged file
    assert set(store.parent.glob("*.backup-*")) == backups_before
    assert not list(store.parent.glob("*.tmp"))


def test_dry_run_reports_reclaim_without_mutating(store: Path):
    original = store.read_bytes()

    report = mod.compact(store, execute=False, keep_tail=0, log=lambda *_: None)

    assert report["executed"] is False
    assert report["kept_records"] == 3
    assert report["records"] == 15
    assert report["reclaimed_bytes"] == report["bytes_before"] - report["bytes_after"]
    assert report["reclaimed_bytes"] > 0
    assert store.read_bytes() == original
    assert report["backup"] is None
    assert not list(store.parent.glob("*.backup-*"))


def test_execute_writes_a_restorable_backup(store: Path):
    original = store.read_bytes()

    report = mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)

    backup = Path(report["backup"])
    assert backup.is_file()
    # the backup is the complete pre-compaction store, so the op is reversible
    assert backup.read_bytes() == original
    assert not list(store.parent.glob("*.tmp"))


def test_backup_dir_override(store: Path, tmp_path: Path):
    dest = tmp_path / "backups"
    report = mod.compact(store, execute=True, keep_tail=0, backup_dir=dest, log=lambda *_: None)
    assert Path(report["backup"]).parent == dest


def test_keep_tail_preserves_the_harness_diagnostic_window(store: Path):
    # keep_tail=6 keeps the last 6 records verbatim; union with latest-per-ref
    # (which are the last 3) is 6 records, so recurrence counts survive.
    report = mod.compact(store, execute=True, keep_tail=6, log=lambda *_: None)

    records = _read_records(store)
    assert report["kept_records"] == 6
    assert len(records) == 6
    revisions = [r["state"]["journal_revision"] for r in records]
    assert revisions == [4, 4, 4, 5, 5, 5]
    # latest-per-ref is still correct on top of the wider window
    assert _latest_state_from_file(store, "multica:aaa")["journal_revision"] == 5


def test_keep_tail_larger_than_the_store_keeps_everything(store: Path):
    report = mod.compact(store, execute=True, keep_tail=1000, log=lambda *_: None)
    assert report["executed"] is False
    assert len(_read_records(store)) == 15


def test_unparseable_line_is_never_silently_dropped(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    _write_store(path, [_record("multica:aaa", revision=1), "{not json", _record("multica:aaa", revision=2)])

    report = mod.compact(path, execute=True, keep_tail=10, log=lambda *_: None)

    assert report["bad_json"] == 1
    assert "{not json" in path.read_text(encoding="utf-8")


def test_final_record_without_a_trailing_newline_is_not_concatenated(tmp_path: Path):
    """A crashed append can leave the last line without its newline.

    Only the LAST line can lack one, and keep_spans sorts by offset, so a kept
    newline-less span is always last and cannot run into a following record. Pinned
    because a future reordering of the keep set would corrupt the store here.
    """
    path = tmp_path / "runs.jsonl"
    path.write_text(
        _record("multica:aaa", revision=1) + "\n" + _record("multica:bbb", revision=1),
        encoding="utf-8",
    )

    mod.compact(path, execute=True, keep_tail=0, log=lambda *_: None)

    # both are latest-for-their-ref, so both survive and must stay separable
    assert len(_read_records(path)) == 2
    assert _latest_state_from_file(path, "multica:aaa")["journal_revision"] == 1
    assert _latest_state_from_file(path, "multica:bbb")["journal_revision"] == 1


def test_superseded_final_record_without_trailing_newline(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    path.write_text(
        _record("multica:aaa", revision=1) + "\n" + _record("multica:aaa", revision=2),
        encoding="utf-8",
    )

    mod.compact(path, execute=True, keep_tail=0, log=lambda *_: None)

    records = _read_records(path)
    assert len(records) == 1
    assert records[0]["state"]["journal_revision"] == 2


def test_records_without_state_do_not_create_a_task_ref(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    # `effect_requested` style rows carry a task_ref but no state
    stateless = json.dumps({"event": "effect_requested", "task_ref": "multica:zzz"}, sort_keys=True)
    _write_store(path, [_record("multica:aaa", revision=1), stateless, _record("multica:aaa", revision=2)])

    report = mod.compact(path, execute=False, keep_tail=0, log=lambda *_: None)

    assert report["task_refs"] == 1  # only multica:aaa; the stateless row is not state
    assert report["kept_records"] == 1


def test_missing_store_is_a_clean_no_op(tmp_path: Path):
    report = mod.compact(tmp_path / "nope.jsonl", execute=True, log=lambda *_: None)
    assert report == {"ok": True, "found": False, "path": str(tmp_path / "nope.jsonl")}


def test_empty_store_is_a_clean_no_op(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    path.write_text("", encoding="utf-8")
    report = mod.compact(path, execute=True, log=lambda *_: None)
    assert report["executed"] is False
    assert path.read_bytes() == b""


def test_scan_indexes_spans_without_reading_the_file_into_memory(store: Path):
    # the tool must index by byte offset; a read()-everything implementation would
    # not produce spans. This pins the streaming contract at the API level.
    with store.open("rb") as handle:
        result = mod.scan(handle)
    assert result.records == 15
    assert result.total_bytes == store.stat().st_size
    assert len(result.all_spans) == 15
    # spans must tile the file exactly, in order
    assert result.all_spans[0][0] == 0
    assert sum(length for _, length in result.all_spans) == result.total_bytes
    offsets = [off for off, _ in result.all_spans]
    assert offsets == sorted(offsets)


def test_source_never_renames_over_the_store():
    """The flock-then-rename hazard: writers flock the INODE, so compaction must
    rewrite in place. A future os.rename/os.replace onto the store path would
    silently break mutual exclusion with pipeline_store.save_state.

    Asserted over the AST (not the text) so the design rationale in the docstring,
    which necessarily names the rejected idiom, cannot trip it.
    """
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    banned = {("os", "rename"), ("os", "replace"), ("shutil", "move"), ("shutil", "copy2")}
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in banned:
                found.append(f"{func.value.id}.{func.attr} (line {node.lineno})")
            # Path(...).rename(...) / p.replace(...) on any receiver
        if isinstance(func, ast.Attribute) and func.attr in {"rename", "replace"}:
            if not isinstance(func.value, ast.Name) or func.value.id != "str":
                found.append(f".{func.attr}() (line {node.lineno})")
    assert not found, f"rename-style call(s) in the compactor: {found}"


def test_keep_tail_default_matches_the_harness_tail_read():
    """The tail window exists to preserve engineering_harness_loop's history read;
    if that module's tail grows, this default must grow with it."""
    harness = (ROOT / "scripts" / "maintenance" / "engineering_harness_loop.py").read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_PIPELINE_TAIL\s*=\s*(\d+)", harness, re.MULTILINE)
    assert match, "DEFAULT_PIPELINE_TAIL not found in engineering_harness_loop.py"
    assert mod.DEFAULT_KEEP_TAIL == int(match.group(1))


def test_store_path_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ZOE_PIPELINE_STORE_PATH", raising=False)
    assert mod.store_path().name == "engineering_pipeline_runs.jsonl"
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "env.jsonl"))
    assert mod.store_path() == tmp_path / "env.jsonl"
    # explicit override beats the env var, matching pipeline_store's ladder shape
    assert mod.store_path(str(tmp_path / "arg.jsonl")) == tmp_path / "arg.jsonl"


def _append_while_locked(path_str: str, ready_fd: int) -> None:
    """Child: append a record the way save_state does — open, flock, append."""
    path = Path(path_str)
    with path.open("a+", encoding="utf-8") as handle:
        os.write(ready_fd, b"x")  # signal "fd is open, about to block on flock"
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0, 2)
            handle.write(_record("multica:late", revision=99) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_concurrent_writer_record_survives_compaction(store: Path):
    """A writer blocked on flock while we compact must not lose its record.

    This is the regression that a temp-file + rename implementation would fail:
    the writer holds an fd on the pre-compaction inode, so a rename would send its
    append to an orphaned inode. Rewriting in place keeps the writer's fd valid.
    """
    read_fd, write_fd = os.pipe()
    ctx = multiprocessing.get_context("fork")
    proc = ctx.Process(target=_append_while_locked, args=(str(store), write_fd))

    with store.open("rb+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            proc.start()
            os.close(write_fd)
            assert os.read(read_fd, 1) == b"x"  # child has its fd open on the inode
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # compact takes its own LOCK_EX; the child races it for the lock
    mod.compact(store, execute=True, keep_tail=0, log=lambda *_: None)
    proc.join(timeout=30)
    os.close(read_fd)
    assert proc.exitcode == 0

    # Whoever won the lock, the writer's record must be in the live store —
    # never stranded on an orphaned inode.
    assert _latest_state_from_file(store, "multica:late") is not None
