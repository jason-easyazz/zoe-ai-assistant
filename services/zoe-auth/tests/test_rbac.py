"""Current-contract RBAC behavior tests."""

from core.rbac import AccessContext, PermissionResult, RBACManager


def test_permission_format_validation_accepts_supported_patterns():
    manager = RBACManager()
    invalid = manager._validate_permissions(
        ["calendar.read", "admin.system.monitor", "calendar.*", "*"]
    )
    assert invalid == []


def test_permission_format_validation_rejects_invalid_patterns():
    manager = RBACManager()
    invalid = manager._validate_permissions(
        ["invalid-permission", ".starts.with.dot", "has spaces"]
    )
    assert len(invalid) == 3


def test_wildcard_match_detects_grant():
    manager = RBACManager()
    granted = manager._check_wildcard_permissions("calendar.delete", {"calendar.*"})
    assert "calendar.*" in granted


def test_resource_owner_gets_update_access():
    manager = RBACManager()
    context = AccessContext(
        user_id="u-1",
        session_type="standard",
        device_info={"type": "web"},
        resource_owner="u-1",
    )
    result = manager._check_resource_permission(
        user_id="u-1",
        permission="calendar.update",
        resource="calendar.event.123",
        user_permissions=set(),
        context=context,
    )
    assert result.result == PermissionResult.GRANTED
    assert result.reason == "resource_owner"


def test_touch_panel_context_denies_admin_permissions():
    manager = RBACManager()
    context = AccessContext(
        user_id="u-1",
        session_type="standard",
        device_info={"type": "touch_panel"},
    )
    result = manager._check_conditional_permissions(
        user_id="u-1",
        permission="admin.system_config",
        resource=None,
        user_permissions=set(),
        context=context,
    )
    assert result.result == PermissionResult.DENIED
    assert result.reason == "admin_not_allowed_on_touch_panel"

