"""Tests for main.py Multica poll-loop helpers."""

import pytest


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def record_progress(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"id": args[0], **kwargs}


@pytest.mark.asyncio
async def test_record_completed_multica_chain_records_explicit_retro_completion_metadata():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(
        client,
        "issue-1",
        {
            "status": "done",
            "pipeline": {"phase": "retro"},
            "pr_url": "https://github.com/o/r/pull/1",
        },
    )

    assert client.calls == [
        (
            ("issue-1",),
            {
                "phase": "retro",
                "evidence": "Kanban chain done after retro",
                "pr_url": "https://github.com/o/r/pull/1",
                "clear_blocker": True,
                "status": "done",
            },
        )
    ]


@pytest.mark.asyncio
async def test_record_completed_multica_chain_preserves_legacy_closeout_completion():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(client, "issue-2", {"status": "done"})

    assert client.calls[0][1]["pr_url"] is None
    assert client.calls[0][1]["phase"] == "closeout"
    assert client.calls[0][1]["evidence"] == "Kanban chain done"
    assert client.calls[0][1]["status"] == "done"


@pytest.mark.asyncio
async def test_record_completed_multica_chain_records_non_retro_pipeline_phase():
    from main import _record_completed_multica_chain

    client = RecordingClient()

    await _record_completed_multica_chain(
        client,
        "issue-3",
        {"status": "done", "pipeline": {"phase": "closeout"}},
    )

    assert client.calls[0][1]["phase"] == "closeout"
    assert client.calls[0][1]["evidence"] == "Kanban chain done"
    assert client.calls[0][1]["status"] == "done"
