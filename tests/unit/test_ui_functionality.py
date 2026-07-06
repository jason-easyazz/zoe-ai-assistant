"""Live UI/API smoke checks.

These checks require Zoe services on localhost. When a service is not running,
pytest reports a skip instead of silently passing.
"""

from __future__ import annotations

import pytest

requests = pytest.importorskip("requests")

ZOE_ROOT = "http://localhost:8000"
ZOE_API = "http://localhost:8000/api"
PEOPLE_SERVICE = ZOE_API
COLLECTIONS_SERVICE = ZOE_API


def _get_or_skip(url: str) -> requests.Response:
    try:
        return requests.get(url, timeout=5)
    except requests.RequestException as exc:
        pytest.skip(f"Live service unavailable for {url}: {exc}")


def test_zoe_core_health_endpoint():
    response = _get_or_skip(f"{ZOE_ROOT}/health")

    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("lists", f"{ZOE_API}/lists"),
        ("calendar", f"{ZOE_API}/calendar/events?start_date=2026-06-01&end_date=2026-06-30"),
        ("reminders", f"{ZOE_API}/reminders"),
    ],
)
def test_core_api_endpoints_return_expected_status(name, url):
    response = _get_or_skip(url)

    assert response.status_code in {200, 404}, f"{name} returned {response.status_code}: {response.text[:200]}"
    if response.status_code == 200:
        assert response.text.strip()


def test_people_service_lists_people_payload():
    response = _get_or_skip(f"{PEOPLE_SERVICE}/people")
    if response.status_code in {401, 403}:
        # /api/people is auth-gated (get_current_user + guest_policy); an
        # unauthenticated smoke probe is correctly rejected on a live host.
        pytest.skip("people endpoint requires an authenticated session on this live service")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    assert "people" in payload or "count" in payload


def test_collections_service_lists_collections_payload():
    response = _get_or_skip(f"{COLLECTIONS_SERVICE}/collections")
    if response.status_code == 404:
        pytest.skip("Canonical collections route is not registered on this live service")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    assert "collections" in payload or "count" in payload
