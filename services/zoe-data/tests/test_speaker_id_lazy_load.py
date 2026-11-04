"""zoe-data must never carry the retired speaker-encoder stack in its footprint.

Resemblyzer pulled torch (~360 MB RSS measured on the Jetson, 2026-09-27) and was retired in 2026-10: the server embeds no
audio (the panel embeds with ResNet34 ONNX). So a fresh interpreter importing ``main`` (and the voice router / speaker-ID
module it wires) must NOT have ``resemblyzer``, ``torch`` or ``transformers`` in ``sys.modules``, and the speaker-ID module
exposes no encoder (a regression that re-adds one fails here).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep: a subprocess import + numpy

_ZOE_DATA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ZOE_DATA))

import voice_speaker_id  # noqa: E402

_HEAVY = ("resemblyzer", "torch", "transformers")


def test_importing_main_does_not_load_speaker_id_stack():
    code = (
        "import json, sys\n"
        "import main, routers.voice_tts, voice_speaker_id\n"
        f"print(json.dumps(sorted(m for m in {_HEAVY!r} if m in sys.modules)))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_ZOE_DATA)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ZOE_DATA),
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"import main pulled heavy speaker-ID deps: {loaded}"




def test_speaker_id_module_has_no_encoder_left():
    for gone in ("_get_voice_encoder", "_compute_resemblyzer_embedding", "_embed_in_clean_subprocess", "_ENCODER"):
        assert not hasattr(voice_speaker_id, gone), gone
    assert voice_speaker_id._cosine_similarity(np_bytes([1, 0]), np_bytes([1, 0])) == 1.0


def np_bytes(v):
    import numpy as np

    return np.asarray(v, dtype=np.float32).tobytes()
