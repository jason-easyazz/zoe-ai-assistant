"""Edit-path regression for 3b importance (Greptile #1017).

Importance is a computed function of a row's TEXT. On edit, the review()
carry-forward loop copies un-recomputed metadata from the old row onto the new
one — so if `importance` weren't in the carry-forward skip set, editing a
high-stakes fact (importance=0.9) down to ordinary text would keep the stale
0.9. This exercises the real edit path against an isolated store.
"""
import pytest

from memory_service import MemoryService


async def _ingest(svc, text):
    return await svc.ingest(
        text, user_id="u1", source="test", memory_type="fact", status="approved",
    )


@pytest.mark.asyncio
async def test_edit_high_stakes_to_ordinary_drops_stale_importance(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path))
    ref = await _ingest(svc, "Jason is allergic to penicillin")
    assert (ref.metadata or {}).get("importance") == 0.9        # producer wrote it

    # Edit the SAME row down to ordinary text.
    new_ref = await svc.review(ref.id, decision="edit", edits="Jason went to the shops", actor="test")
    # importance must be RECOMPUTED from the new (ordinary) text — i.e. absent —
    # not carried forward from the old high-stakes row.
    assert "importance" not in (new_ref.metadata or {}), \
        f"stale importance carried forward: {new_ref.metadata!r}"


@pytest.mark.asyncio
async def test_edit_ordinary_to_high_stakes_gains_importance(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path))
    ref = await _ingest(svc, "Jason went to the shops")
    assert "importance" not in (ref.metadata or {})

    new_ref = await svc.review(ref.id, decision="edit", edits="Jason is allergic to penicillin", actor="test")
    assert (new_ref.metadata or {}).get("importance") == 0.9     # recomputed up
