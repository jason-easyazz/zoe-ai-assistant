"""OpenClaw message context prefix (zoe-auth role → agent)."""

from openclaw_ws import _zoe_context_prefix


def test_context_prefix_includes_user_role_and_name():
    p = _zoe_context_prefix("u-1", user_role="admin", username="jason")
    assert "[CONTEXT:" in p
    assert "user_id=u-1" in p
    assert "role=admin" in p
    assert "name=jason" in p


def test_context_prefix_unknown_role_when_omitted():
    p = _zoe_context_prefix("family-admin", user_role=None, username=None)
    assert "role=unknown" in p
    assert "name=" in p
