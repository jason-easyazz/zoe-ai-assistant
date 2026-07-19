"""Panel face-ID module (`scripts/setup/zoe_face_id.py`) — the CPU-pure parts.

ONNX inference and the camera need hardware/model files and are live-verified
on the panel; what CAN be pinned in CI is the math and policy around them:

1. ``umeyama_similarity`` — recovers a known similarity transform exactly
   (rotation+scale+translation) and maps the ArcFace source points onto the
   canonical destination; this is the alignment correctness the embedding
   depends on.
2. ``match_embedding`` — best-cosine over multiple poses per user, raw score
   (no local thresholding — the server decides), dimension-mismatch and junk
   rows skipped, empty cache → None.
3. ``_pick_best_face`` — largest area × score wins; faces under the minimum
   pixel size are rejected.
4. ``identify_face`` — hard-gated by FACE_ID_ENABLED and never raises.

No cv2, no onnxruntime, no network, no camera.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_face_id.py"

spec = importlib.util.spec_from_file_location("zoe_face_id_under_test", _MODULE_PATH)
face = importlib.util.module_from_spec(spec)
spec.loader.exec_module(face)


@pytest.fixture(autouse=True)
def clean_cache():
    with face._cache_lock:
        face._cache.update({"fetched_at": 0.0, "profiles": [], "syncing": False})
    yield


def _profile(user_id, vec):
    emb = np.asarray(vec, dtype=np.float32)
    emb = emb / np.linalg.norm(emb)
    return {"user_id": user_id, "embedding_base64": base64.b64encode(emb.tobytes()).decode()}


# ── 1. alignment transform ─────────────────────────────────────────────────

def test_umeyama_recovers_known_similarity():
    rng = np.random.default_rng(42)
    src = rng.uniform(0, 100, size=(5, 2))
    theta, scale, t = 0.3, 1.7, np.array([12.0, -4.0])
    R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    dst = scale * (src @ R.T) + t

    M = face.umeyama_similarity(src, dst)
    mapped = src @ M[:, :2].T + M[:, 2]
    assert np.allclose(mapped, dst, atol=1e-3)


def test_umeyama_maps_arcface_points_identically():
    # src == dst → identity transform (within float noise).
    M = face.umeyama_similarity(face._ARCFACE_DST, face._ARCFACE_DST)
    assert np.allclose(M[:, :2], np.eye(2), atol=1e-5)
    assert np.allclose(M[:, 2], 0.0, atol=1e-3)


# ── 2. matching ────────────────────────────────────────────────────────────

def test_match_best_pose_per_user_wins():
    with face._cache_lock:
        face._cache["profiles"] = [
            _profile("jason", [1.0, 0.0, 0.0]),   # frontal pose
            _profile("jason", [0.7, 0.7, 0.0]),   # angled pose
            _profile("kiddo", [0.0, 0.0, 1.0]),
        ]
    q = np.asarray([0.72, 0.69, 0.0], dtype=np.float32)
    user, score = face.match_embedding(q / np.linalg.norm(q))
    assert user == "jason"
    assert score > 0.99  # matched the angled pose, not the frontal one


def test_match_returns_raw_low_score_and_skips_junk():
    with face._cache_lock:
        face._cache["profiles"] = [
            {"user_id": "junk", "embedding_base64": "!!"},
            _profile("wrongdim", [1.0, 0.0]),          # dim mismatch vs query
            _profile("jason", [1.0, 0.0, 0.0]),
        ]
    user, score = face.match_embedding(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))
    assert user == "jason" and score < 0.1  # raw score, no local threshold


def test_match_empty_cache_is_none():
    assert face.match_embedding(np.ones(3, dtype=np.float32)) is None


# ── 3. best-face pick ──────────────────────────────────────────────────────

def test_pick_best_face_area_times_score_with_min_px():
    dets = np.array([
        [0, 0, 40, 40, 0.99],       # under MIN_FACE_PX (60) → rejected
        [0, 0, 200, 200, 0.60],     # area 40000 * 0.6 = 24000
        [0, 0, 150, 150, 0.95],     # area 22500 * 0.95 = 21375
    ], dtype=np.float32)
    kps = np.zeros((3, 5, 2), dtype=np.float32)
    picked = face._pick_best_face(dets, kps)
    assert picked is not None
    det, _ = picked
    assert det[2] == 200  # the 200px face wins on area×score

    only_small = np.array([[0, 0, 30, 30, 0.99]], dtype=np.float32)
    assert face._pick_best_face(only_small, np.zeros((1, 5, 2))) is None


# ── 4. gating + never-raise ────────────────────────────────────────────────

def test_identify_face_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(face, "FACE_ID_ENABLED", False)
    assert face.identify_face() is None


def test_identify_face_never_raises_without_models(monkeypatch, tmp_path):
    monkeypatch.setattr(face, "FACE_ID_ENABLED", True)
    monkeypatch.setattr(face, "DET_MODEL", str(tmp_path / "missing-det.onnx"))
    monkeypatch.setattr(face, "REC_MODEL", str(tmp_path / "missing-rec.onnx"))
    assert face.identify_face() is None
