import pytest

from zoe_capability_profile import (
    DEFAULT_CAPABILITY_PROFILES,
    CapabilityProfile,
    capability_profile_index,
    profiles_requiring_approval,
    validate_capability_profiles,
)

pytestmark = pytest.mark.ci_safe


def test_default_capability_profiles_validate_and_cover_core_surfaces():
    serialized = validate_capability_profiles()
    ids = {profile["capability_id"] for profile in serialized}

    assert {
        "chat_router",
        "mempalace_memory",
        "hindsight_reflective_memory",
        "graphiti_relational_memory",
        "graphify_system_map",
        "multica_governance",
        "hermes_escalation",
        "openclaw_fallback",
        "pi_external_runtime",
        "home_assistant_control",
    } <= ids


def test_trusted_profiles_have_evidence_tests_and_rollback():
    for profile in DEFAULT_CAPABILITY_PROFILES:
        if profile.trust_level in {"trusted", "privileged"}:
            assert profile.evidence_refs
            assert profile.tests
            assert profile.rollback


def test_privileged_profiles_require_approval():
    privileged = [profile for profile in DEFAULT_CAPABILITY_PROFILES if profile.trust_level == "privileged"]

    assert privileged
    assert all(profile.approval_required for profile in privileged)
    assert {"multica_governance", "hermes_escalation", "home_assistant_control"} <= {
        profile.capability_id for profile in privileged
    }


def test_capability_profile_index_rejects_duplicate_ids():
    duplicate = CapabilityProfile(
        capability_id="chat_router",
        name="Duplicate chat router",
        owner_surface="chat",
        task_types=("conversation",),
        trust_level="experimental",
    )

    with pytest.raises(ValueError, match="duplicate capability_id"):
        capability_profile_index([DEFAULT_CAPABILITY_PROFILES[0], duplicate])


def test_capability_profile_rejects_unknown_owner_surface():
    profile = CapabilityProfile(
        capability_id="bad_owner",
        name="Bad owner",
        owner_surface="unknown",
        task_types=("test",),
        trust_level="experimental",
    )

    with pytest.raises(ValueError, match="unknown owner_surface"):
        profile.validate()


def test_trusted_profile_requires_evidence():
    profile = CapabilityProfile(
        capability_id="unsupported_trusted",
        name="Unsupported trusted capability",
        owner_surface="chat",
        task_types=("test",),
        trust_level="trusted",
        tests=("test",),
        rollback="revert",
    )

    with pytest.raises(ValueError, match="trusted or privileged capabilities require evidence_refs"):
        profile.validate()


def test_metadata_is_read_only():
    profile = CapabilityProfile(
        capability_id="metadata_test",
        name="Metadata test",
        owner_surface="chat",
        task_types=("test",),
        trust_level="experimental",
        metadata={"alpha": "beta"},
    )

    with pytest.raises(TypeError):
        profile.metadata["gamma"] = "delta"
    assert profile.to_dict()["metadata"] == {"alpha": "beta"}


def test_profiles_requiring_approval_includes_install_memory_and_graph_surfaces():
    ids = {profile.capability_id for profile in profiles_requiring_approval()}

    assert "pi_external_runtime" in ids
    assert "graphiti_relational_memory" in ids
    assert "mempalace_memory" in ids
