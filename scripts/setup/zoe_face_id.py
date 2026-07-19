"""On-panel face identification for the Zoe touch panel (Pi + USB webcam).

All vision compute stays HERE on the panel — the Jetson stores profiles and
applies policy only. Pipeline per wake-word capture:

    USB camera (cv2.VideoCapture) → grab a few frames over ~1s
    → SCRFD face detection (det_500m.onnx, 5-point landmarks)
    → pick the best face (largest area × detector score)
    → ArcFace 5-point alignment (Umeyama similarity transform, no skimage)
    → MobileFaceNet embedding (w600k_mbf.onnx, 512-dim, buffalo_sc pack)
    → cosine match against the synced face-profile cache
    → (user_id, raw score) CLAIM — the server applies its own threshold.

Frames are process-local and discarded after embedding; only embeddings ever
leave the Pi. Models live in ~/.zoe-voice/models/ (see fetch_face_models.sh,
SHA256-pinned). The decode/alignment logic follows insightface's
model_zoo/scrfd.py + utils/face_align.py (buffalo_sc's own stack), trimmed to
the single-image CPU case.

The module is import-safe without cv2/onnxruntime/model files: everything
heavy is lazy, and every public function degrades to None instead of raising
— an absent camera or model must never break a voice turn.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("zoe-face-id")

# ── Config (env names match the daemon's conventions) ────────────────────────
FACE_ID_ENABLED = os.environ.get("FACE_ID_ENABLED", "false").lower() in ("1", "true", "yes")
ZOE_URL = os.environ.get("ZOE_URL", "https://zoe.local")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").lower() not in ("0", "false", "no")
CAMERA_INDEX = int(os.environ.get("FACE_CAMERA_INDEX", "0"))
CAPTURE_FRAMES = int(os.environ.get("FACE_CAPTURE_FRAMES", "4"))
CAPTURE_SPAN_S = float(os.environ.get("FACE_CAPTURE_SPAN_S", "0.8"))
DET_THRESHOLD = float(os.environ.get("FACE_DET_THRESHOLD", "0.5"))
MIN_FACE_PX = int(os.environ.get("FACE_MIN_PX", "60"))  # min bbox side, rejects tiny/far faces

MODEL_DIR = os.path.expanduser(os.environ.get("FACE_MODEL_DIR", "~/.zoe-voice/models"))
DET_MODEL = os.path.join(MODEL_DIR, "det_500m.onnx")
REC_MODEL = os.path.join(MODEL_DIR, "w600k_mbf.onnx")
REC_MODEL_NAME = "buffalo_sc/w600k_mbf"
EMBED_DIM = 512

_CACHE_PATH = os.path.expanduser("~/.zoe-voice/face_profiles.json")
_SYNC_TTL_S = float(os.environ.get("FACE_ID_SYNC_TTL_S", "3600"))

# ArcFace canonical 5-point destination for a 112×112 crop
# (insightface utils/face_align.py `arcface_dst`).
_ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32)


# ── Lazy ONNX sessions ───────────────────────────────────────────────────────
_sessions: dict = {}
_sessions_lock = threading.Lock()


def _get_session(path: str):
    with _sessions_lock:
        if path in _sessions:
            return _sessions[path]
        try:
            import onnxruntime as ort
            sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            _sessions[path] = sess
            log.info("face-id model loaded: %s", os.path.basename(path))
            return sess
        except Exception as exc:
            log.debug("face-id model unavailable (%s): %s", path, exc)
            _sessions[path] = None
            return None


def models_available() -> bool:
    return os.path.isfile(DET_MODEL) and os.path.isfile(REC_MODEL)


# ── SCRFD detection (det_500m: strides 8/16/32, 2 anchors, kps) ─────────────

def _distance2bbox(points, distance):
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points, distance):
    preds = []
    for i in range(0, distance.shape[1], 2):
        px = points[:, i % 2] + distance[:, i]
        py = points[:, i % 2 + 1] + distance[:, i + 1]
        preds.append(px)
        preds.append(py)
    return np.stack(preds, axis=-1)


def _nms(dets, thresh=0.4):
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


def detect_faces(img_bgr, det_size: int = 640):
    """SCRFD detect. Returns (dets Nx5 [x1,y1,x2,y2,score], kps Nx5x2) in
    original-image coordinates, or (empty, empty) when the model is missing."""
    import cv2

    sess = _get_session(DET_MODEL)
    if sess is None:
        return np.empty((0, 5), np.float32), np.empty((0, 5, 2), np.float32)

    # Letterbox to det_size × det_size preserving aspect.
    im_ratio = img_bgr.shape[0] / img_bgr.shape[1]
    if im_ratio > 1:
        new_h, new_w = det_size, int(det_size / im_ratio)
    else:
        new_w, new_h = det_size, int(det_size * im_ratio)
    det_scale = new_h / img_bgr.shape[0]
    det_img = np.zeros((det_size, det_size, 3), dtype=np.uint8)
    det_img[:new_h, :new_w, :] = cv2.resize(img_bgr, (new_w, new_h))

    blob = cv2.dnn.blobFromImage(
        det_img, 1.0 / 128.0, (det_size, det_size),
        (127.5, 127.5, 127.5), swapRB=True)
    input_name = sess.get_inputs()[0].name
    outs = sess.run(None, {input_name: blob})

    # det_500m: 9 outputs → fmc=3 (strides 8/16/32), 2 anchors, kps present.
    fmc, strides, num_anchors = 3, (8, 16, 32), 2
    scores_l, bboxes_l, kps_l = [], [], []
    for idx, stride in enumerate(strides):
        scores = outs[idx]
        bbox_preds = outs[idx + fmc] * stride
        kps_preds = outs[idx + fmc * 2] * stride
        if scores.ndim == 3:  # batched model variant
            scores, bbox_preds, kps_preds = scores[0], bbox_preds[0], kps_preds[0]
        h, w = det_size // stride, det_size // stride
        centers = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
        centers = (centers * stride).reshape((-1, 2))
        if num_anchors > 1:
            centers = np.stack([centers] * num_anchors, axis=1).reshape((-1, 2))
        pos = np.where(scores.ravel() >= DET_THRESHOLD)[0]
        if pos.size == 0:
            continue
        bboxes = _distance2bbox(centers, bbox_preds)
        kps = _distance2kps(centers, kps_preds).reshape((-1, 5, 2))
        scores_l.append(scores.ravel()[pos])
        bboxes_l.append(bboxes[pos])
        kps_l.append(kps[pos])

    if not scores_l:
        return np.empty((0, 5), np.float32), np.empty((0, 5, 2), np.float32)

    scores = np.hstack(scores_l)
    bboxes = np.vstack(bboxes_l) / det_scale
    kps = np.vstack(kps_l) / det_scale
    dets = np.hstack((bboxes, scores[:, None])).astype(np.float32)
    order = dets[:, 4].argsort()[::-1]
    dets, kps = dets[order], kps[order]
    keep = _nms(dets)
    return dets[keep], kps[keep]


# ── Alignment (Umeyama similarity transform — no skimage dep) ────────────────

def umeyama_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """2x3 similarity transform mapping src→dst (least-squares, Umeyama 1991).

    Equivalent to skimage SimilarityTransform.estimate + .params[0:2] as used
    by insightface's estimate_norm.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n, d = src.shape
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_c = src - src_mean
    dst_c = dst - dst_mean
    cov = dst_c.T @ src_c / n
    U, S, Vt = np.linalg.svd(cov)
    sign = np.ones(d)
    if np.linalg.det(cov) < 0:
        sign[-1] = -1
    R = U @ np.diag(sign) @ Vt
    var_src = (src_c ** 2).sum() / n
    scale = (S * sign).sum() / var_src if var_src > 0 else 1.0
    t = dst_mean - scale * (R @ src_mean)
    M = np.zeros((2, 3), dtype=np.float32)
    M[:, :2] = (scale * R).astype(np.float32)
    M[:, 2] = t.astype(np.float32)
    return M


def align_face(img_bgr, kps: np.ndarray, size: int = 112):
    """ArcFace-standard 112×112 aligned crop from 5 landmarks."""
    import cv2

    M = umeyama_similarity(kps.astype(np.float32), _ARCFACE_DST)
    return cv2.warpAffine(img_bgr, M, (size, size), borderValue=0.0)


# ── Embedding ────────────────────────────────────────────────────────────────

def embed_face(aligned_bgr) -> np.ndarray | None:
    """512-dim L2-normalised embedding from a 112×112 aligned BGR crop."""
    import cv2

    sess = _get_session(REC_MODEL)
    if sess is None:
        return None
    blob = cv2.dnn.blobFromImage(
        aligned_bgr, 1.0 / 127.5, (112, 112),
        (127.5, 127.5, 127.5), swapRB=True)
    input_name = sess.get_inputs()[0].name
    emb = sess.run(None, {input_name: blob})[0].ravel().astype(np.float32)
    norm = float(np.linalg.norm(emb))
    if norm <= 0:
        return None
    return emb / norm


# ── Profile cache (mirrors the voice daemon's speaker cache) ─────────────────
_cache: dict = {"fetched_at": 0.0, "profiles": [], "syncing": False}
_cache_lock = threading.Lock()


def load_cache_from_disk() -> None:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("profiles"), list):
            with _cache_lock:
                _cache["profiles"] = data["profiles"]
            log.info("face profiles loaded from disk: %d", len(data["profiles"]))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.debug("face profile cache load failed: %s", exc)


def sync_profiles(force: bool = False) -> None:
    """Refresh the cache from GET /api/face/profiles/sync (TTL, single-flight)."""
    import requests

    with _cache_lock:
        fresh = (time.time() - _cache["fetched_at"]) < _SYNC_TTL_S
        if (fresh and not force) or _cache["syncing"]:
            return
        _cache["syncing"] = True
    try:
        r = requests.get(
            f"{ZOE_URL}/api/face/profiles/sync",
            headers={"X-Device-Token": DEVICE_TOKEN},
            timeout=10, verify=VERIFY_SSL,
        )
        r.raise_for_status()
        profiles = r.json().get("profiles") or []
        with _cache_lock:
            _cache["fetched_at"] = time.time()
            _cache["profiles"] = profiles
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH), mode=0o700, exist_ok=True)
            fd = os.open(_CACHE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"profiles": profiles}, f)
        except Exception as exc:
            log.debug("face profile cache persist failed: %s", exc)
        log.info("face profiles synced: %d", len(profiles))
    except Exception as exc:
        with _cache_lock:
            cached = len(_cache["profiles"])
        log.debug("face profile sync failed (keeping cached %d): %s", cached, exc)
    finally:
        with _cache_lock:
            _cache["syncing"] = False


def match_embedding(emb: np.ndarray) -> tuple[str, float] | None:
    """Best cosine match over the cached profiles (multiple poses per user —
    a user's best pose wins). Returns (user_id, raw score); the ACCEPTANCE
    decision is the server's."""
    with _cache_lock:
        profiles = list(_cache["profiles"])
    if not profiles:
        return None
    best_user, best_score = None, -1.0
    for p in profiles:
        try:
            ref = np.frombuffer(base64.b64decode(p["embedding_base64"]), dtype=np.float32)
        except Exception:
            continue
        if ref.shape != emb.shape:
            continue
        denom = float(np.linalg.norm(emb) * np.linalg.norm(ref))
        if denom <= 0:
            continue
        score = float(np.dot(emb, ref) / denom)
        if score > best_score:
            best_user, best_score = p.get("user_id"), score
    if not best_user:
        return None
    return best_user, best_score


# ── Camera capture + end-to-end identify ─────────────────────────────────────
_camera_lock = threading.Lock()


def _pick_best_face(dets: np.ndarray, kps: np.ndarray):
    """Largest area × detector score, rejecting faces below MIN_FACE_PX."""
    best_i, best_v = -1, 0.0
    for i in range(dets.shape[0]):
        x1, y1, x2, y2, score = dets[i]
        w, h = x2 - x1, y2 - y1
        if min(w, h) < MIN_FACE_PX:
            continue
        v = float(w * h * score)
        if v > best_v:
            best_i, best_v = i, v
    if best_i < 0:
        return None
    return dets[best_i], kps[best_i]


def capture_frames(n: int = None, span_s: float = None) -> list:
    """Grab n frames over span_s from the USB camera; [] on any failure.
    Single-owner: skipped (returns []) if another capture is in flight."""
    n = n or CAPTURE_FRAMES
    span_s = span_s if span_s is not None else CAPTURE_SPAN_S
    if not _camera_lock.acquire(blocking=False):
        log.debug("camera busy; skipping capture")
        return []
    frames = []
    try:
        import cv2
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            log.debug("camera %d not available", CAMERA_INDEX)
            return []
        try:
            delay = span_s / max(1, n - 1) if n > 1 else 0.0
            for i in range(n):
                ok, frame = cap.read()
                if ok and frame is not None:
                    frames.append(frame)
                if i < n - 1 and delay:
                    time.sleep(delay)
        finally:
            cap.release()
    except Exception as exc:
        log.debug("camera capture failed: %s", exc)
    finally:
        _camera_lock.release()
    return frames


def identify_face() -> tuple[str, float] | None:
    """Full pipeline: capture → detect → best face across frames → align →
    embed → match. Returns a (user_id, raw score) claim or None. Never raises."""
    if not FACE_ID_ENABLED:
        return None
    try:
        if not models_available():
            return None
        sync_profiles()
        frames = capture_frames()
        if not frames:
            return None
        best = None  # (value, frame, det, kps)
        for frame in frames:
            dets, kps = detect_faces(frame)
            picked = _pick_best_face(dets, kps)
            if picked is None:
                continue
            det, kp = picked
            x1, y1, x2, y2, score = det
            v = float((x2 - x1) * (y2 - y1) * score)
            if best is None or v > best[0]:
                best = (v, frame, det, kp)
        if best is None:
            return None
        _, frame, det, kp = best
        aligned = align_face(frame, kp)
        emb = embed_face(aligned)
        if emb is None:
            return None
        return match_embedding(emb)
    except Exception as exc:
        log.debug("face identify failed: %s", exc)
        return None


def warmup() -> None:
    """Background warmup: disk cache, fresh sync, model load. Never raises."""
    try:
        load_cache_from_disk()
        sync_profiles(force=True)
        if models_available():
            _get_session(DET_MODEL)
            _get_session(REC_MODEL)
    except Exception as exc:
        log.debug("face-id warmup failed: %s", exc)
