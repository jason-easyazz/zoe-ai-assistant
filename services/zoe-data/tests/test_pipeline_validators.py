"""Tests for harness-run repo validators."""

import pytest
from unittest.mock import patch

from pipeline_validators import run_repo_validators, validator_evidence_item

pytestmark = pytest.mark.ci_safe


def test_run_repo_validators_success():
    with patch("pipeline_validators._run_one", return_value=(0, "ok")):
        result = run_repo_validators(repo_root="/tmp/repo")
    assert result.passed is True
    assert result.exit_code == 0
    assert len(result.content_hash) == 64


def test_run_repo_validators_failure():
    with patch("pipeline_validators._run_one", side_effect=[(0, "ok"), (1, "missing file")]):
        result = run_repo_validators(repo_root="/tmp/repo")
    assert result.passed is False
    assert result.exit_code == 1


def test_validator_evidence_item_tags_phase():
    with patch("pipeline_validators._run_one", return_value=(0, "ok")):
        result = run_repo_validators(repo_root="/tmp/repo")
    item = validator_evidence_item(result, phase="implement")
    assert item.kind == "validator"
    assert item.metadata["phase"] == "implement"
    assert item.metadata["source"] == "harness"
