from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_helper():
    root = Path(__file__).resolve().parents[3]
    helper_path = root / "scripts/maintenance/zoe_apply_intent_gap_contract.py"
    spec = importlib.util.spec_from_file_location("zoe_apply_intent_gap_contract", helper_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_apply_joke_contract_patches_router_and_adds_test(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        "# Open-domain Q&A / creative\n"
        "_AGENT_CHAT_RE = re.compile(\n"
        '    r"^(?:tell me about|what(?:\'s| is) the (?:capital|weather)|"\n'
        '    r"search the web|write me (?:an? )?(?:email|haiku|poem)|"\n'
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n'
        "    re.IGNORECASE,\n"
        ")\n",
        encoding="utf-8",
    )

    result = helper.apply_joke_contract(tmp_path)

    router = router_path.read_text(encoding="utf-8")
    generated_test = tmp_path / "services/zoe-data/tests/test_intent_open_domain.py"
    assert result["idempotent"] is False
    assert "tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes" in router
    assert generated_test.exists()
    assert "test_joke_requests_route_to_open_domain_agent" in generated_test.read_text(encoding="utf-8")

    second = helper.apply_joke_contract(tmp_path)

    assert second == {"contract": "joke-open-domain", "changed": [], "idempotent": True}


def test_apply_joke_contract_appends_to_existing_open_domain_test(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    test_path = tmp_path / "services/zoe-data/tests/test_intent_open_domain.py"
    router_path.parent.mkdir(parents=True)
    test_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    test_path.write_text(
        "def test_existing_open_domain_behavior():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    helper.apply_joke_contract(tmp_path)

    generated = test_path.read_text(encoding="utf-8")
    assert "def test_existing_open_domain_behavior" in generated
    assert "def test_joke_requests_route_to_open_domain_agent" in generated


def test_apply_joke_contract_fails_cleanly_when_router_missing(tmp_path):
    helper = _load_helper()

    try:
        helper.apply_joke_contract(tmp_path)
    except SystemExit as exc:
        assert "intent_router.py not found" in str(exc)
    else:
        raise AssertionError("expected SystemExit for missing intent_router.py")


def test_apply_joke_contract_is_idempotent_with_current_router_pattern(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes)",\n',
        encoding="utf-8",
    )

    result = helper.apply_joke_contract(tmp_path)

    assert result["changed"] == ["services/zoe-data/tests/test_intent_open_domain.py"]
    assert "tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes" in router_path.read_text(encoding="utf-8")
