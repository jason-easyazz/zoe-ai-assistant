from __future__ import annotations

import pytest
import importlib.util
from pathlib import Path

pytestmark = pytest.mark.ci_safe


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


def test_apply_say_exactly_contract_patches_router_and_adds_test(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes)",\n',
        encoding="utf-8",
    )

    result = helper.apply_say_exactly_contract(tmp_path)

    router = router_path.read_text(encoding="utf-8")
    generated_test = tmp_path / "services/zoe-data/tests/test_intent_open_domain.py"
    assert result["idempotent"] is False
    assert "say exactly[: ]+(?:.+)" in router
    assert generated_test.exists()
    assert "test_say_exactly_routes_to_open_domain_agent" in generated_test.read_text(encoding="utf-8")

    second = helper.apply_say_exactly_contract(tmp_path)

    assert second == {"contract": "say-exactly-open-domain", "changed": [], "idempotent": True}


def test_apply_say_exactly_contract_can_patch_base_router_pattern(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )

    result = helper.apply_say_exactly_contract(tmp_path)

    assert result["changed"] == [
        "services/zoe-data/intent_router.py",
        "services/zoe-data/tests/test_intent_open_domain.py",
    ]
    assert "say exactly[: ]+(?:.+)" in router_path.read_text(encoding="utf-8")


def test_apply_say_exactly_contract_is_idempotent_with_current_router_pattern(tmp_path):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    test_path = tmp_path / "services/zoe-data/tests/test_intent_open_domain.py"
    router_path.parent.mkdir(parents=True)
    test_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"tell me (?:a|another) joke|make me laugh|(?:do you |have you )?(?:got|have) any jokes|know any (?:good )?jokes|"\n'
        '    r"say exactly[: ]+.+)",\n',
        encoding="utf-8",
    )
    test_path.write_text(
        "def test_say_exactly_routes_to_open_domain_agent():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    result = helper.apply_say_exactly_contract(tmp_path)

    assert result == {"contract": "say-exactly-open-domain", "changed": [], "idempotent": True}


def test_apply_say_exactly_contract_appends_to_existing_open_domain_test(tmp_path):
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

    helper.apply_say_exactly_contract(tmp_path)

    generated = test_path.read_text(encoding="utf-8")
    assert "def test_existing_open_domain_behavior" in generated
    assert "def test_say_exactly_routes_to_open_domain_agent" in generated


def test_apply_say_exactly_contract_fails_cleanly_when_router_missing(tmp_path):
    helper = _load_helper()

    try:
        helper.apply_say_exactly_contract(tmp_path)
    except SystemExit as exc:
        assert "intent_router.py not found" in str(exc)
    else:
        raise AssertionError("expected SystemExit for missing intent_router.py")


def test_cli_guard_refuses_live_repo_root(tmp_path, monkeypatch):
    helper = _load_helper()
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    try:
        helper._guard_not_live_root(tmp_path)
    except SystemExit as exc:
        assert "Refusing to mutate live checkout" in str(exc)
        assert "task worktree" in str(exc)
    else:
        raise AssertionError("expected SystemExit for live repo root")


def test_cli_guard_allows_explicit_live_repo_override(tmp_path, monkeypatch):
    helper = _load_helper()
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    helper._guard_not_live_root(tmp_path, allow_live_root=True)


def test_cli_guard_resolves_relative_live_repo_root(tmp_path, monkeypatch):
    helper = _load_helper()
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    try:
        helper._guard_not_live_root(Path("."))
    except SystemExit as exc:
        assert "Refusing to mutate live checkout" in str(exc)
    else:
        raise AssertionError("expected SystemExit for relative live repo root")


def test_apply_say_exactly_contract_refuses_live_repo_root(tmp_path, monkeypatch):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    try:
        helper.apply_say_exactly_contract(tmp_path)
    except SystemExit as exc:
        assert "Refusing to mutate live checkout" in str(exc)
    else:
        raise AssertionError("expected SystemExit for direct live-root apply")


def test_apply_say_exactly_contract_allows_explicit_live_repo_override(tmp_path, monkeypatch):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    result = helper.apply_say_exactly_contract(tmp_path, allow_live_root=True)

    assert result["idempotent"] is False
    assert "say exactly[: ]+(?:.+)" in router_path.read_text(encoding="utf-8")


def test_apply_joke_contract_refuses_live_repo_root(tmp_path, monkeypatch):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    try:
        helper.apply_joke_contract(tmp_path)
    except SystemExit as exc:
        assert "Refusing to mutate live checkout" in str(exc)
    else:
        raise AssertionError("expected SystemExit for direct live-root joke apply")


def test_apply_joke_contract_allows_explicit_live_repo_override(tmp_path, monkeypatch):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))

    result = helper.apply_joke_contract(tmp_path, allow_live_root=True)

    assert result["idempotent"] is False
    assert "tell me (?:a|another) joke" in router_path.read_text(encoding="utf-8")


def test_main_threads_live_root_override(tmp_path, monkeypatch, capsys):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "zoe_apply_intent_gap_contract.py",
            "say_exactly",
            "--repo-root",
            str(tmp_path),
            "--allow-live-root",
        ],
    )

    assert helper.main() == 0

    assert "say-exactly-open-domain" in capsys.readouterr().out
    assert "say exactly[: ]+(?:.+)" in router_path.read_text(encoding="utf-8")


def test_main_refuses_live_repo_root_without_override(tmp_path, monkeypatch):
    helper = _load_helper()
    router_path = tmp_path / "services/zoe-data/intent_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        '    r"can you explain|set up (?:a )?new automation|what is happening in)",\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_LIVE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "zoe_apply_intent_gap_contract.py",
            "say_exactly",
            "--repo-root",
            str(tmp_path),
        ],
    )

    try:
        helper.main()
    except SystemExit as exc:
        assert "Refusing to mutate live checkout" in str(exc)
    else:
        raise AssertionError("expected SystemExit for live-root CLI without override")


def test_focused_checks_block_already_covered_clean_worktree(tmp_path, monkeypatch):
    helper = _load_helper()
    calls = []

    def fake_run_checked(cmd, *, cwd):
        calls.append(("checked", cmd, cwd))
        return object()

    def fake_run(cmd, cwd):
        calls.append(("run", cmd, cwd))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(helper, "_run_checked", fake_run_checked)
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    focused = helper.run_focused_checks(
        tmp_path,
        {"idempotent": True, "changed": []},
        kanban_task="t_123",
        hermes_bin="/tmp/hermes",
    )

    assert focused["terminal_action"] == "kanban_block:ALREADY_COVERED"
    assert any(call[1][:3] == ["python3", "-m", "py_compile"] for call in calls)
    assert any(call[1][:4] == ["python3", "-m", "pytest", "-q"] for call in calls)
    assert ("run", ["git", "diff", "--quiet"], tmp_path) in calls
    assert any(
        call[1][:4] == ["/tmp/hermes", "kanban", "block", "t_123"]
        and "BLOCKER=ALREADY_COVERED" in call[1][4]
        for call in calls
    )


def test_focused_checks_do_not_block_when_helper_changed_files(tmp_path, monkeypatch):
    helper = _load_helper()
    calls = []

    def fake_run_checked(cmd, *, cwd):
        calls.append(("checked", cmd, cwd))
        return object()

    def fake_run(cmd, cwd):
        calls.append(("run", cmd, cwd))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(helper, "_run_checked", fake_run_checked)
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    focused = helper.run_focused_checks(
        tmp_path,
        {"idempotent": False, "changed": ["services/zoe-data/intent_router.py"]},
        kanban_task="t_123",
        hermes_bin="/tmp/hermes",
    )

    assert focused["terminal_action"] is None
    assert not any(call[1][:3] == ["/tmp/hermes", "kanban", "block"] for call in calls)
