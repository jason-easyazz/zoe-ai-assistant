"""Smart Turn must not pull torch/transformers into zoe-data.

``voice_turn`` used ``transformers.WhisperFeatureExtractor`` for its log-mel,
and in transformers 5.x that import drags in torch unconditionally: +~360 MB
resident for the life of zoe-data from the first LiveKit turn. It now uses a
pure-numpy log-mel (parity pinned in ``test_voice_turn_logmel_parity.py``).

Two layers:
* a static check over the module source (no numpy needed — runs in the slim
  CI lane), catching function-local imports too, which is where it hid;
* a fresh-interpreter run of the REAL scoring path — detector construction and
  ``end_of_turn_prob`` with a stub onnxruntime session — asserting torch and
  transformers never reach ``sys.modules`` and that the model is still fed
  ``input_features`` float32 [1, 80, 800].

Negative control: restoring the WhisperFeatureExtractor import turns both red.
"""
import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_SERVICE_DIR = Path(__file__).resolve().parents[1]
_MODULE = _SERVICE_DIR / "voice_turn.py"
_BANNED = ("torch", "transformers", "torchaudio", "librosa")


def test_voice_turn_source_imports_no_torch_or_transformers():
    tree = ast.parse(_MODULE.read_text(), filename=str(_MODULE))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if name.split(".")[0] in _BANNED:
                offenders.append(f"line {node.lineno}: {name}")
    assert not offenders, f"voice_turn imports a heavy ML stack: {offenders}"


_PROBE = textwrap.dedent(
    """
    import sys, types
    import numpy as np

    fed = {}

    class _Session:
        def __init__(self, *a, **k):
            pass

        def run(self, _outputs, feeds):
            fed.update(feeds)
            return [np.array([[0.75]], dtype=np.float32)]

    class _Opts:
        pass

    ort = types.ModuleType("onnxruntime")
    ort.InferenceSession = _Session
    ort.SessionOptions = _Opts
    sys.modules["onnxruntime"] = ort

    import voice_turn

    det = voice_turn.SmartTurnDetector("/unused/model.onnx")
    rng = np.random.default_rng(0)
    p_long = det.end_of_turn_prob((rng.standard_normal(10 * 16000) * 2000).astype(np.int16))
    p_short = det.end_of_turn_prob(np.zeros(1600, dtype=np.int16))
    feats = fed["input_features"]
    assert list(fed) == ["input_features"], list(fed)
    assert feats.dtype == np.float32 and feats.shape == (1, 80, 800), (feats.dtype, feats.shape)
    assert p_long == 0.75 and p_short == 0.75, (p_long, p_short)
    heavy = sorted(m for m in ("torch", "transformers") if m in sys.modules)
    print("HEAVY=" + ",".join(heavy))
    """
)


def test_scoring_path_never_loads_torch_or_transformers():
    pytest.importorskip("numpy")  # the slim CI runner has no numpy; host runs exercise this
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONPATH"] = str(_SERVICE_DIR)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(_SERVICE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "HEAVY=", proc.stdout
