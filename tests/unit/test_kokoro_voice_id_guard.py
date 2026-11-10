"""The Kokoro sidecar accepts only bare voice ids in a request's ``voice``.

KPipeline.load_voice treats a value ending in ``.pt`` as a filesystem path and
``torch.load()``s it (and splits on ``,`` to blend several), so an unchecked
``voice`` is a torch.load of a caller-chosen file — the weights_only-unpickler RCE
class (GHSA-63cw-57p8-fm3p) on the held Jetson torch wheel. The guard must reject
every such value BEFORE synthesis is reached, on both endpoints.

Pure logic: no model load (torch/kokoro are lazy-imported inside synthesis).
"""
import asyncio
import importlib.util
import pathlib
import sys

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"


@pytest.fixture(scope="module")
def kok():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_voice_guard", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_voice_guard"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("voice", ["af_sky", "bf_emma", "am_michael", "zf_xiaoxiao"])
def test_voice_ids_pass(kok, voice):
    assert kok._checked_voice(voice) == voice


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_voice_falls_back_to_default(kok, raw):
    assert kok._checked_voice(raw) == kok._VOICE


@pytest.mark.parametrize("voice", [
    "/tmp/evil.pt",
    "evil.pt",
    "../../voices/af_sky",
    "af_sky,/tmp/evil.pt",
    "af_sky,af_bella",
    "voices/af_sky",
    "AF_SKY",
    "a",
    "x" * 65,
])
def test_paths_blends_and_junk_are_rejected(kok, voice):
    with pytest.raises(kok.HTTPException) as exc:
        kok._checked_voice(voice)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("endpoint", ["synthesize", "synthesize_stream"])
def test_endpoints_reject_before_synthesis(kok, monkeypatch, endpoint):
    """Negative control on the wiring: a path voice must never reach _run_synthesis."""
    reached = []

    async def _never(text, voice, speed=1.0):  # pragma: no cover - must not run
        reached.append(voice)
        return b""

    monkeypatch.setattr(kok, "_pipeline", object())
    monkeypatch.setattr(kok, "_run_synthesis", _never)
    req = kok.SynthRequest(text="hello", voice="/tmp/evil.pt")
    with pytest.raises(kok.HTTPException) as exc:
        asyncio.run(getattr(kok, endpoint)(req))
    assert exc.value.status_code == 400
    assert reached == []
