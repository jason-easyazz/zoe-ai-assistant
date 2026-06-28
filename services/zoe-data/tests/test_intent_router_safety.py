from __future__ import annotations

import pathlib

import pytest

from intent_router import Intent, detect_intent, execute_intent


def test_detect_intent_miss_logging_mkdir_failure_does_not_abort(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("read-only home")

    monkeypatch.setattr(pathlib.Path, "mkdir", boom)

    assert detect_intent("unmatched safety regression phrase") is None


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
