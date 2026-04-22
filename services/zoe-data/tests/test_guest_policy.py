from __future__ import annotations

import pytest
from fastapi import HTTPException

from guest_policy import (
    FEATURE_ACTIONS,
    PAGE_IDS,
    PUBLIC_HOUSEHOLD_INTENTS,
    USER_SCOPED_INTENTS,
    default_capability_matrix,
    is_guest_user,
    require_feature_access,
    require_authenticated_for_mutation,
)


def test_household_intents_include_shared_calendar_read() -> None:
    assert "calendar_show" in PUBLIC_HOUSEHOLD_INTENTS


def test_user_scoped_intents_exclude_shared_calendar_read() -> None:
    assert "calendar_show" not in USER_SCOPED_INTENTS


def test_is_guest_user_by_role() -> None:
    assert is_guest_user({"role": "guest", "user_id": "someone"}) is True


def test_is_guest_user_by_user_id() -> None:
    assert is_guest_user({"role": "member", "user_id": "guest"}) is True


def test_non_guest_user_detected() -> None:
    assert is_guest_user({"role": "member", "user_id": "jason"}) is False


def test_guest_blocked_for_mutation() -> None:
    with pytest.raises(HTTPException) as exc:
        require_authenticated_for_mutation(
            {"role": "guest", "user_id": "guest"},
            resource="calendar",
            action="create",
        )
    assert exc.value.status_code == 403


def test_authenticated_allowed_for_mutation() -> None:
    require_authenticated_for_mutation(
        {"role": "member", "user_id": "jason"},
        resource="calendar",
        action="create",
    )


def test_default_matrix_has_fixed_shape() -> None:
    matrix = default_capability_matrix()
    assert set(matrix.keys()) == {"admin", "user", "guest"}
    assert set(matrix["guest"]["pages"].keys()) == set(PAGE_IDS)
    for feature, actions in FEATURE_ACTIONS.items():
        assert feature in matrix["user"]["features"]
        for action in actions:
            assert action in matrix["user"]["features"][feature]


@pytest.mark.asyncio
async def test_guest_matrix_blocks_mutation() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_feature_access(
            db=None,
            user={"role": "guest", "user_id": "guest"},
            feature="calendar",
            action="create",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_guest_matrix_allows_household_read() -> None:
    await require_feature_access(
        db=None,
        user={"role": "guest", "user_id": "guest"},
        feature="calendar",
        action="read",
    )
