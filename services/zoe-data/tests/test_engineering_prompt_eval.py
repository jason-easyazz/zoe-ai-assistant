import pytest
import importlib.util
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def test_engineering_prompt_contract_eval_passes():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/maintenance/evaluate_engineering_prompts.py"
    )
    spec = importlib.util.spec_from_file_location("evaluate_engineering_prompts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    report = module.evaluate()
    assert report["ok"] is True
    assert report["pass_rate"] == 1.0
