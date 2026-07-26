import pytest

from multica_client import MULClient
from multica_ticket_contract import describe_ticket, parse_ticket_block, update_ticket_progress

pytestmark = pytest.mark.ci_safe


class GuardedClient(MULClient):
    def is_configured(self):
        return True

    async def get_issue(self, issue_id):
        return {}

    async def list_issues(self, *args, **kwargs):
        return []

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
        def is_configured(self):
            return True

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
async def test_record_progress_writes_completion_reason():
    class Client(MULClient):
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {
                "id": issue_id,
                "description": describe_ticket("Done issue"),
            }

        async def update_issue(self, issue_id, **kwargs):
            return {"id": issue_id, **kwargs}

    updated = await Client().record_progress(
        "issue-1",
        phase="done",
        status="done",
        completion_reason="merged after Greptile 5/5",
    )

    metadata = parse_ticket_block(updated["description"])
    assert metadata["phase"] == "done"
    assert metadata["completion_reason"] == "merged after Greptile 5/5"


def test_update_ticket_progress_can_clear_dispatch_approval():
    from multica_ticket_contract import parse_ticket_block, update_ticket_progress, write_ticket_block

    description = write_ticket_block(
        "Human prose",
        {
            "schema": 1,
            "dispatch_approved": True,
            "blocked_reason": None,
        },
    )

    updated = update_ticket_progress(
        description,
        blocker="IMPLEMENT_BUDGET: code-enforced tool budget exceeded",
        dispatch_approved=False,
    )

    metadata = parse_ticket_block(updated)
    assert metadata["dispatch_approved"] is False
    assert metadata["blocked_reason"] == "IMPLEMENT_BUDGET: code-enforced tool budget exceeded"


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
        description = update_ticket_progress(description, evidence="old evidence")
        old_updated_at = parse_ticket_block(description)["updated_at"]
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
    assert metadata["last_evidence"] == "old evidence"
    assert metadata["updated_at"] != old_updated_at


@pytest.mark.asyncio
async def test_resolve_issue_returns_backend_id_match_without_listing():
    class Client(MULClient):
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {"id": issue_id, "identifier": "ZOE-1"}

        async def list_issues(self, *args, **kwargs):
            raise AssertionError("direct backend id match should not list issues")

    assert await Client().resolve_issue("issue-1") == {"id": "issue-1", "identifier": "ZOE-1"}


@pytest.mark.asyncio
async def test_resolve_issue_falls_back_to_visible_identifier():
    class Client(MULClient):
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {}

        async def list_issues(self, status=None, *, limit=None):
            assert limit == 1000
            return [{"id": "issue-1", "identifier": "ZOE-5465"}]

    assert await Client().resolve_issue("zoe-5465") == {"id": "issue-1", "identifier": "ZOE-5465"}


@pytest.mark.asyncio
async def test_resolve_issue_returns_empty_when_reference_not_found():
    class Client(MULClient):
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {}

        async def list_issues(self, status=None, *, limit=None):
            return [{"id": "issue-1", "identifier": "ZOE-1"}]

    assert await Client().resolve_issue("ZOE-9999") == {}


@pytest.mark.asyncio
async def test_record_progress_updates_resolved_backend_issue_id():
    calls = []

    class Client(MULClient):
        def is_configured(self):
            return True

        async def get_issue(self, issue_id):
            return {}

        async def list_issues(self, status=None, *, limit=None):
            return [{"id": "issue-1", "identifier": "ZOE-5465", "description": describe_ticket("Issue") }]

        async def update_issue(self, issue_id, **kwargs):
            calls.append((issue_id, kwargs))
            return {"id": issue_id, **kwargs}

    updated = await Client().record_progress("ZOE-5465", phase="done")

    assert updated["id"] == "issue-1"
    assert calls[0][0] == "issue-1"
    assert parse_ticket_block(calls[0][1]["description"])["phase"] == "done"
