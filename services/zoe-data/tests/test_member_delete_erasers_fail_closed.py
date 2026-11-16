"""Member delete: every FILE eraser fails closed (owner decision 2026-11-15, "deleting a member should delete everything").

For each eraser ``MemoryService.delete_user`` reaches that keeps member content in files, a failure in its removal step must RAISE
(so the delete fails and is retried, never reported done with the content left), and the retry must finish with nothing of the member
left. A file the eraser cannot read is a failure, not "empty"; a corrupt line or summary that can name the member goes with them.
Synthetic member ids only; every store lives in tmp_path.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

pytestmark = pytest.mark.ci_safe

A, B = "ann", "bob"
needs_permissions = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores file permissions")


def _seed_self_review(d):
    import self_review as m

    (d / f"2026-01-01{m.RECORDS_SUFFIX}").write_text(
        json.dumps({"user_id": A, "turn_id": "t1", "text": "a's words"}) + "\n"
        + json.dumps({"user_id": B, "turn_id": "t2", "text": "b's words"}) + "\n"
        + '{"user_id": "ann", "turn_id": "t3", "te\n')                       # a torn line naming A
    (d / f"2026-01-01{m.SUMMARY_SUFFIX}").write_text(json.dumps({"events": [{"member": A, "turn_id": "t1"}, {"member": B}]}))
    (d / f"2026-01-02{m.SUMMARY_SUFFIX}").write_text('{"events": [{"member": "ann"')   # a corrupt summary naming A
    return d / f"2026-01-01{m.RECORDS_SUFFIX}"


def _seed_night_extract(d):
    import night_extract as m

    (d / f"2026-01-01{m.FACTS_SUFFIX}").write_text(
        json.dumps({"user_id": A, "fact": "a's fact"}) + "\n" + json.dumps({"user_id": B, "fact": "b's fact"}) + "\n"
        + '{"user_id": "ann", "fa\n')
    (d / f"2026-01-01{m.SUMMARY_SUFFIX}").write_text(json.dumps({"members": {A: 1, B: 1}}))
    (d / f"2026-01-02{m.SUMMARY_SUFFIX}").write_text('{"members": {"ann"')
    return d / f"2026-01-01{m.FACTS_SUFFIX}"


@needs_permissions
@pytest.mark.parametrize("mod_name, seed", [("self_review", _seed_self_review), ("night_extract", _seed_night_extract)])
def test_an_unreadable_file_fails_the_erase_and_the_retry_leaves_nothing_of_the_member(tmp_path, monkeypatch, mod_name, seed):
    import importlib

    mod = importlib.import_module(mod_name)
    monkeypatch.setattr(mod, "state_dir", lambda: tmp_path)
    records = seed(tmp_path)
    os.chmod(records, 0o000)                                     # the removal step cannot read what it must rewrite
    try:
        with pytest.raises(OSError):
            asyncio.run(mod.delete_user(A))
    finally:
        os.chmod(records, 0o600)
    assert A in records.read_text(), "nothing was rewritten on a guess"
    asyncio.run(mod.delete_user(A))                              # the retry
    left = "".join(p.read_text() for p in tmp_path.iterdir() if p.is_file() and not p.name.startswith("."))
    assert '"ann"' not in left and "a's" not in left, left
    assert B in left, "the other member's records stay"


def test_the_warm_speaker_memo_that_cannot_be_loaded_fails_the_erase_strict_and_stays_lenient_otherwise(monkeypatch):
    import warm_speaker

    def broken():
        raise OSError("memo unreadable")
    monkeypatch.setattr(warm_speaker, "_ensure_loaded", broken)
    assert warm_speaker.forget_user(A) == 0, "a non-erase caller keeps the old never-raises contract"
    with pytest.raises(OSError):
        warm_speaker.forget_user(A, strict=True)


def test_delete_user_asks_the_warm_speaker_memo_to_erase_strictly(monkeypatch):
    """The member delete passes ``strict=True``: a memo that cannot be loaded fails the delete (it used to return 0, read as done)."""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "memory_service.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and any(
        isinstance(a, ast.Attribute) and a.attr == "forget_user" and getattr(a.value, "id", "") == "warm_speaker" for a in n.args)]
    assert calls and all(any(k.arg == "strict" and getattr(k.value, "value", None) is True for k in c.keywords) for c in calls)
