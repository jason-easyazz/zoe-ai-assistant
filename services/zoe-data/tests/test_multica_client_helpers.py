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


@pytest.mark.asyncio
async def test_create_issue_metadata_preserves_existing_ticket_fields():
    created_payload = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "issue-1", **created_payload}

    class HTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            created_payload.update(json)
            return Response()

    class Client(MULClient):
        def is_configured(self):
            return True

        def _headers(self):
            return {}

    import multica_client

    original = multica_client.httpx.AsyncClient
    multica_client.httpx.AsyncClient = lambda timeout: HTTP()
    try:
        description = describe_ticket(
            "Keep my fields",
            acceptance_criteria=["dispatch once"],
            evidence_expectations=["chain exists"],
            source="initial",
        )
        client = Client()
        client._base = "http://multica"
        client._token = "token"
        client._workspace = "workspace"
        await client.create_issue("ticket", description=description, metadata={"source": "override"})
    finally:
        multica_client.httpx.AsyncClient = original

    metadata = parse_ticket_block(created_payload["description"])
    assert metadata["acceptance_criteria"] == ["dispatch once"]
    assert metadata["evidence_expectations"] == ["chain exists"]
    assert metadata["source"] == "override"
