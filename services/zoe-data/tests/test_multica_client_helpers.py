import pytest

from multica_client import MULClient


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
