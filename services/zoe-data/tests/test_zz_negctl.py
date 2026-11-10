"""NEGATIVE CONTROL for the sharded validate gate (throwaway branch, never merged)."""
import pytest

pytestmark = pytest.mark.ci_safe


def test_negative_control_must_turn_validate_red():
    assert False, "deliberate failure: the validate aggregator must go red"
