"""Unit test for the Kokoro sidecar's onnx/CPU-default footgun WARNING.

Pure-logic only — stubs the lazy ``kokoro_onnx`` import so ``_load_pipeline()``
runs the onnx branch with no model load and no CUDA (same safe-import pattern as
test_kokoro_cache.py; the module's heavy deps are lazy and the top-level imports
are slim-dep-green, so importing here is safe on the GitHub runner). Marked
``ci_safe``.

Locks in the footgun guard: the loud warning fires when onnx is reached by the
SILENT default (``ZOE_KOKORO_BACKEND`` unset → ``_BACKEND_IS_DEFAULT``), and stays
quiet on an explicit backend choice (deliberate ≠ footgun).
"""
import importlib.util
import pathlib
import sys
import types

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "setup" / "kokoro_sidecar.py"
)
_MARK = "ONNX/CPU backend by DEFAULT"


@pytest.fixture
def kok(monkeypatch):
    """Import kokoro_sidecar with a stubbed onnx backend, forced onto the onnx branch."""
    fake = types.ModuleType("kokoro_onnx")

    class _FakeKokoro:  # accepts (model, voices); loads nothing
        def __init__(self, *a, **k):
            pass

    fake.Kokoro = _FakeKokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake)

    spec = importlib.util.spec_from_file_location("kokoro_sidecar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar"] = module
    spec.loader.exec_module(module)
    module._BACKEND = "onnx"  # force the onnx branch regardless of the CI env
    return module


def _capture_warnings(kok, monkeypatch):
    calls: list[str] = []
    # Replace only .warning so the branch's info() logging is unaffected; robust to
    # whatever logging config the module sets (no reliance on propagation/caplog).
    monkeypatch.setattr(kok.logger, "warning", lambda msg, *a, **k: calls.append(str(msg)))
    return calls


def test_warns_when_backend_is_default(kok, monkeypatch):
    calls = _capture_warnings(kok, monkeypatch)
    kok._BACKEND_IS_DEFAULT = True
    kok._load_pipeline()
    assert any(_MARK in c for c in calls), "footgun warning must fire on the silent onnx default"


def test_silent_when_backend_explicit(kok, monkeypatch):
    calls = _capture_warnings(kok, monkeypatch)
    kok._BACKEND_IS_DEFAULT = False
    kok._load_pipeline()
    assert not any(_MARK in c for c in calls), "an explicit backend choice must not warn"
