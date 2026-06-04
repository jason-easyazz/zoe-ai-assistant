import pytest

from multica_client import MULClient
from multica_ticket_contract import describe_ticket, parse_ticket_block, update_ticket_progress


class GuardedClient(MULClient):
    def is_configured(self):
        return True

    async def get_issue(self, issue_id):
        return {}

    async def update_issue(self, *args, **kwargs):
        raise AssertionError("description write must not run when get_issue fails")


@pytest.mark.asyncio
async def test_safe_patch_description_skips_when_get_issue_fails():
    assert await GuardedClient().safe_patch_description("missing", {"schema": 1}) == {}


@pytest.mark.asyncio
async def test_append_issue_note_skips_when_get_issue_fails():
    assert await GuardedClient().append_issue_note("missing", "note") == {}


@pytest.mark.asyncio
async def test_record_progress_skips_when_get_issue_fails():
    assert await GuardedClient().record_progress("missing", phase="verify") == {}


@pytest.mark.asyncio
async def test_record_progress_can_clear_blocker():
    class Client(MULClient):
        async def get_issue(self, issue_id):
            return {
                "id": issue_id,
                "description": update_ticket_progress(describe_ticket("Blocked issue"), blocker="old blocker"),
            }

        async def update_issue(self, issue_id, **kwargs):
            return {"id": issue_id, **kwargs}

    updated = await Client().record_progress("issue-1", phase="closeout", status="done", clear_blocker=True)
    metadata = parse_ticket_block(updated["description"])
    assert metadata["blocked_reason"] is None
    assert metadata["phase"] == "closeout"
