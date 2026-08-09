"""`strict_content_type` must stay ON for this service.

FastAPI 0.132.0 made `strict_content_type` default to TRUE: a request carrying a
non-empty body to a route with a NON-FORM body parameter is only parsed as JSON
when its `Content-Type` is `application/json` (or an `application/…+json`
suffix). Anything else — including a missing header — fails body validation.

Every zoe-auth caller in the repo was audited and all of them send the header,
so we kept the strict default rather than disabling it. These tests pin that
decision: if someone passes `strict_content_type=False` to `FastAPI(...)` in
`main.py` to "fix" a caller, the first test goes RED and forces the
conversation back to fixing the caller instead.

They also pin the two things that must NOT become collateral damage: the
`application/…+json` suffix form, and bodyless POSTs (logout, logout/all,
reset-password), which never had a Content-Type and must keep working.

One subtlety, measured rather than assumed: the flag governs ONLY the
missing-header case. A body sent under a present-but-wrong type (`text/plain`,
`application/x-www-form-urlencoded`) is never parsed as JSON on a non-form route
whether the flag is on or off — `strict_content_type=False` merely restores the
old fallback for requests that supply no header at all (fastapi/routing.py:436-439).
The wrong-type cases below are therefore pinning framework behaviour rather than
our configuration; only `missing-content-type` flips if someone disables the flag.

`422` is the specific signal for "body rejected". The accepted cases assert
`!= 422` rather than a concrete status because they proceed into handlers that
need a database this test deliberately does not provide — getting PAST body
validation is the whole property under test.
"""

import json

import pytest
from fastapi.testclient import TestClient

from main import app

BODY_ROUTE = "/api/auth/login/passcode"
VALID_BODY = json.dumps({"user_id": "nobody", "passcode": "1234"})

UNPROCESSABLE = 422


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing-content-type"),
        pytest.param({"Content-Type": "text/plain"}, id="text-plain"),
        pytest.param(
            {"Content-Type": "application/x-www-form-urlencoded"}, id="form-urlencoded"
        ),
    ],
)
def test_json_body_without_json_content_type_is_rejected(client, headers):
    """The strict default must reject a JSON body sent under the wrong type."""
    resp = client.post(BODY_ROUTE, content=VALID_BODY, headers=headers)
    assert resp.status_code == UNPROCESSABLE


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "application/vnd.api+json"],
)
def test_json_body_with_json_content_type_is_parsed(client, content_type):
    """A correctly-typed body must get PAST validation (positive control).

    Without this, the test above would still pass if the route were simply
    broken for every request.
    """
    resp = client.post(
        BODY_ROUTE, content=VALID_BODY, headers={"Content-Type": content_type}
    )
    assert resp.status_code != UNPROCESSABLE


def test_bodyless_post_is_unaffected(client):
    """Bodyless POSTs send no Content-Type and must not be caught by the check."""
    resp = client.post("/api/auth/logout", headers={"X-Session-ID": "not-a-session"})
    assert resp.status_code != UNPROCESSABLE
