from __future__ import annotations

import pathlib

import pytest

from intent_router import Intent, detect_and_extract_intent, detect_intent, execute_intent


def test_detect_intent_miss_logging_mkdir_failure_does_not_abort(monkeypatch):
    mkdir_calls = []

    def boom(*args, **kwargs):
        mkdir_calls.append((args, kwargs))
        raise OSError("read-only home")

    monkeypatch.setattr(pathlib.Path, "mkdir", boom)

    assert detect_intent("unmatched safety regression phrase", log_miss=True) is None
    assert mkdir_calls


def test_detect_intent_miss_logging_home_failure_does_not_abort(monkeypatch):
    home_calls = []

    def boom():
        home_calls.append(True)
        raise RuntimeError("home directory cannot be resolved")

    monkeypatch.setattr(pathlib.Path, "home", boom)

    assert detect_intent("another unmatched safety regression phrase", log_miss=True) is None
    assert home_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", ["2**10", "9**9**9"])
async def test_calculate_rejects_exponentiation_safely(expression: str):
    response = await execute_intent(Intent("calculate", {"expression": expression}))

    assert response.startswith("I can only handle numeric expressions.")
    assert "**" not in response


@pytest.mark.asyncio
async def test_calculate_allows_ordinary_arithmetic():
    response = await execute_intent(Intent("calculate", {"expression": "2+3*4"}))

    assert response == "2+3*4 = 14"


@pytest.mark.ci_safe
def test_identity_param_defaults_fail_open_to_guest():
    """Lock in the #1021/#1032 posture: identity-param defaults must fail open to
    least-privilege ``guest``, never the privileged ``family-admin`` account. A
    future edit that reverts any of these back to ``family-admin`` fails here.
    (ci_safe: pure signature/attribute inspection — no DB, no model loads.)"""
    import inspect

    from routers.chat import chat_inject_background, run_openclaw_agent
    from proactive.triggers.openclaw_trigger import OpenClawTrigger

    for fn in (execute_intent, detect_and_extract_intent, run_openclaw_agent, chat_inject_background):
        default = inspect.signature(fn).parameters["user_id"].default
        assert default == "guest", f"{fn.__name__} user_id default must be 'guest', got {default!r}"

    assert OpenClawTrigger._user_id == "guest", (
        f"OpenClawTrigger._user_id must default to 'guest', got {OpenClawTrigger._user_id!r}"
    )
