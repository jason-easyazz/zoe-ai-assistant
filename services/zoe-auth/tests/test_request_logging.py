"""Request-logging middleware must not leak query-string secrets.

Regression guard for the token+PII leak where the middleware logged the full
request URL (including query string) at INFO. The OIDC RP-initiated logout
endpoint takes id_token_hint as a query param, so a logout would write the
user's signed ID token (carrying email/sub/role) into the auth-service logs.
"""

import logging

from fastapi.testclient import TestClient

from main import app


# A token-shaped value; the exact contents don't matter — it must never appear
# in the logs verbatim.
_FAKE_ID_TOKEN = (
    "eyJhbGciOiJSUzI1NiJ9."
    "eyJzdWIiOiIxMjMiLCJlbWFpbCI6Imphc29uQGV4YW1wbGUuY29tIiwicm9sZSI6ImFkbWluIn0."
    "c2lnbmF0dXJlLXNlY3JldC1ub3QtZm9yLWxvZ3M"
)


def test_logout_query_token_not_logged(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="main"):
        # follow_redirects=False: the logout returns a 302 we don't need to chase.
        client.get(
            "/application/o/end-session/",
            params={"id_token_hint": _FAKE_ID_TOKEN},
            follow_redirects=False,
        )

    request_lines = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Request:")
    ]
    assert request_lines, "middleware should still emit a Request log line"

    joined = "\n".join(request_lines)
    # The secret must not be logged...
    assert _FAKE_ID_TOKEN not in joined
    assert "id_token_hint" not in joined
    # ...but method + path observability must be preserved.
    assert "GET /application/o/end-session/" in joined


def test_request_logging_survives_malformed_query():
    """Logging must never crash on odd/empty query strings."""
    client = TestClient(app)
    resp = client.get("/health", params={"": "", "x": ""})
    assert resp.status_code in (200, 503)  # health may report unhealthy without a DB
