"""Speaker-model registry: WHICH embedding space a voiceprint lives in, and the per-model numbers that follow from it.

Why this exists. Resemblyzer and WeSpeaker ResNet34 are BOTH 256-d, so a shape check cannot tell their vectors apart: a
Resemblyzer profile scored against a ResNet34 embedding (or the reverse) runs without error and returns a meaningless
number. Every ``speaker_profiles`` row therefore carries a ``model_id`` (migration 0051) and **profiles of different
model_ids are never compared** - not in the sync feed, not in ``/identify``, not in the panel's matcher, not in adaptation.

A ``model_id`` names everything that changes the embedding space:

  ``resemblyzer``                       the incumbent (GE2E d-vector); also what a NULL / empty column means (every row
                                        enrolled before 0051).
  ``wespeaker_resnet34+i8qdq-u8s8+c1``  WeSpeaker ResNet34, static INT8 QDQ (U8 activations, S8 per-channel weights),
                                        centred on public-cohort mean vector ``c1``. Change the model file, the
                                        quantisation or the centring vector and the id MUST change - an INT8 profile and
                                        an FP32 query do not mix (cosine ~0.97 between the two kinds for one clip).

The flag ``ZOE_SPEAKER_MODEL`` = ``resemblyzer`` (DEFAULT) | ``dual`` | ``resnet34`` (read per call; anything else is
``resemblyzer``). It is mirrored by the panel daemon (``scripts/setup/zoe_voice_daemon.py``):

  resemblyzer  today's behaviour, byte for byte. ResNet34 profiles are never served to a panel.
  dual         the panel embeds with BOTH and logs both decisions, but ACTS only on Resemblyzer. Enrolment builds BOTH
               profiles from the same clips. The sync feed carries both (to a client that asks for them).
  resnet34     ResNet34 acts. Resemblyzer is no longer matched, claimed or synced.

The numbers (record: ``docs/research/speaker-resnet34-optimisation-2026-10.md``) are MIRRORED in
``scripts/setup/zoe_speaker_resnet34.py`` (the Pi cannot import this file) and drift-pinned by
``services/zoe-data/tests/test_speaker_models.py``. Pure: numpy only, no model, no DB, no network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from typed_env import env_float, env_str

LEGACY_MODEL_ID = "resemblyzer"
RESNET34_MODEL_ID = "wespeaker_resnet34+i8qdq-u8s8+c1"

MODES = ("resemblyzer", "dual", "resnet34")

#: SQL that reads a row's model id, treating NULL / '' (every pre-0051 row) as the legacy model.
MODEL_SQL = "COALESCE(NULLIF(model_id, ''), 'resemblyzer')"

#: record section 4.3 - cosine of the CENTRED embedding to the CENTRED centroid, impostors = public far-field speakers.
#: Starting values for the shadow week, to be re-derived on labelled household turns.
SHORT_MAX_S = 2.0
RESNET34_THRESHOLDS = dict(far1=dict(short=0.233, long=0.265), far01=dict(short=0.309, long=0.351))   # FAR 1 % / 0.1 %
#: record section 5 - the #2002 adaptation guards re-tuned for this scale (score gate at the global FAR-0.1 % threshold,
#: plus the ANCHOR-cosine gate that stops a lookalike). The Resemblyzer values (0.85 / 0.80) never fire on this model.
RESNET34_ADAPT_MIN_SCORE = 0.342
RESNET34_ADAPT_ANCHOR_FLOOR = 0.45
#: enrolment: clips per profile, the leave-one-out cosine under which a clip is dropped as "not the same speaker", and
#: how many clips must survive for a profile to be stored at all (the record keeps >= 2; we ask for more).
RESNET34_ENROLL_CLIPS = 5
RESNET34_ENROLL_TAU = 0.30
RESNET34_ENROLL_MIN_KEEP = 3


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    dim: int
    label: str


MODELS = dict([
    (LEGACY_MODEL_ID, ModelSpec(LEGACY_MODEL_ID, 256, "Resemblyzer (GE2E d-vector)")),
    (RESNET34_MODEL_ID, ModelSpec(RESNET34_MODEL_ID, 256, "WeSpeaker ResNet34, INT8 QDQ, cohort-centred (c1)")),
])


# -- mode -----------------------------------------------------------------------------------------------------------

def speaker_mode() -> str:
    """``ZOE_SPEAKER_MODEL``: ``resemblyzer`` (default) | ``dual`` | ``resnet34``. Unknown values are ``resemblyzer``."""
    raw = env_str("ZOE_SPEAKER_MODEL", "resemblyzer").lower()
    return raw if raw in MODES else "resemblyzer"


def acting_model(mode: Optional[str] = None) -> str:
    """The model whose claims the server honours and whose embeddings drive decisions."""
    return RESNET34_MODEL_ID if (mode or speaker_mode()) == "resnet34" else LEGACY_MODEL_ID


def enrolled_models(mode: Optional[str] = None) -> tuple:
    """The models a (re-)enrolment builds a profile for. ``dual`` enrols BOTH from the same clips."""
    m = mode or speaker_mode()
    if m == "resnet34":
        return (RESNET34_MODEL_ID,)
    if m == "dual":
        return (LEGACY_MODEL_ID, RESNET34_MODEL_ID)
    return (LEGACY_MODEL_ID,)


def visible_models(mode: Optional[str] = None) -> frozenset:
    """Models whose profiles the sync feed may hand to a panel in this mode."""
    m = mode or speaker_mode()
    if m == "resnet34":
        return frozenset([RESNET34_MODEL_ID])
    if m == "dual":
        return frozenset([LEGACY_MODEL_ID, RESNET34_MODEL_ID])
    return frozenset([LEGACY_MODEL_ID])


def clips_needed(mode: Optional[str] = None) -> int:
    """Enrolment clips the guided flow asks for: 3 for the legacy model alone (unchanged), 5 whenever ResNet34 is enrolled."""
    return RESNET34_ENROLL_CLIPS if RESNET34_MODEL_ID in enrolled_models(mode) else 3


# -- identity of a model --------------------------------------------------------------------------------------------

def normalize_model_id(raw: Any) -> str:
    """A stored / claimed model id: NULL, empty and non-strings are the legacy model; anything else is stripped as-is
    (``is_known`` decides whether it is usable)."""
    s = raw.strip() if isinstance(raw, str) else ""
    return s or LEGACY_MODEL_ID


def model_id_field(mapping: Any, key: str) -> Optional[str]:
    """A model id named by a REQUEST: an ABSENT field is the legacy model (every client older than ``ZOE_SPEAKER_MODEL``);
    a present field must be a non-empty string (stripped) - a blank, null or non-string value is MALFORMED and returns None,
    which callers refuse (400 / claim refused) instead of quietly treating it as Resemblyzer. (Stored rows use
    ``normalize_model_id``: there NULL / '' genuinely means a pre-0051 Resemblyzer row.)"""
    if not isinstance(mapping, dict) or key not in mapping:
        return LEGACY_MODEL_ID
    raw = mapping[key]
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def model_label(model_id: str, mode: Optional[str] = None) -> str:
    """How a member's profile of ``model_id`` is described to them: ``current`` (the model that recognises them now),
    ``new model, testing`` (enrolled for the shadow comparison in ``dual``), or ``not in use``."""
    m = mode or speaker_mode()
    if model_id == acting_model(m):
        return "current"
    if model_id in enrolled_models(m):
        return "new model, testing"
    return "not in use"


def is_known(model_id: str) -> bool:
    return model_id in MODELS


def row_model_id(row: Any, idx: int) -> str:
    """Model id of a fetched row whose ``idx``-th column is the model (a row without that column - an older query shape - is legacy)."""
    try:
        return normalize_model_id(row[idx])
    except (IndexError, TypeError, KeyError):
        return LEGACY_MODEL_ID


def same_space(a: Any, b: Any) -> bool:
    """True only when two model ids name the same embedding space. The one function every comparison asks first."""
    return normalize_model_id(a) == normalize_model_id(b)


# -- embeddings -----------------------------------------------------------------------------------------------------

def validate_embedding(raw: bytes, model_id: str) -> Optional[np.ndarray]:
    """float32 unit-norm vector of the model's dimension, or None. A panel that sends something else is not sending one."""
    spec = MODELS.get(model_id)
    if spec is None:
        return None
    try:
        v = np.frombuffer(bytes(raw), dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if v.size != spec.dim or not np.all(np.isfinite(v)):
        return None
    n = float(np.linalg.norm(v))
    if not 0.9 <= n <= 1.1:
        return None
    return v.copy()


# -- thresholds -----------------------------------------------------------------------------------------------------

def length_group(speech_s: Any) -> str:
    """``short`` (2 s or less) | ``long``. An unknown / non-finite length is ``long``: that group has the HIGHER (stricter)
    threshold, so a missing length can only make the gate harder to pass."""
    try:
        s = float(speech_s)
    except (TypeError, ValueError):
        return "long"
    if not math.isfinite(s) or s <= 0:
        return "long"
    return "short" if s <= SHORT_MAX_S else "long"


def resnet34_tier() -> str:
    """``ZOE_SPEAKER_RESNET34_TIER``: ``far1`` (default, FAR 1 %) | ``far01`` (FAR 0.1 %, stricter)."""
    raw = env_str("ZOE_SPEAKER_RESNET34_TIER", "far1").lower()
    return raw if raw in RESNET34_THRESHOLDS else "far1"


def resnet34_threshold(group: str, tier: Optional[str] = None) -> float:
    """Accept threshold for a length group. ``ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT`` / ``_LONG`` override the table
    (the shadow week's re-derived values); a non-finite override is ignored."""
    g = "short" if group == "short" else "long"
    table = RESNET34_THRESHOLDS[tier if tier in RESNET34_THRESHOLDS else resnet34_tier()]
    if g == "short":
        override = env_float("ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT", float("nan"))
    else:
        override = env_float("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", float("nan"))
    return override if math.isfinite(override) else table[g]


def claim_threshold(model_id: str, speech_s: Any, legacy_threshold: float) -> Optional[float]:
    """The acceptance bar for a panel's claim made in ``model_id``'s space, or None when the model is unknown (reject)."""
    if model_id == LEGACY_MODEL_ID:
        return legacy_threshold
    if model_id == RESNET34_MODEL_ID:
        return resnet34_threshold(length_group(speech_s))
    return None


def thresholds_public(model_id: str) -> dict:
    """What the sync feed tells a panel about a model's bar (informational: the SERVER decides every claim)."""
    if model_id == RESNET34_MODEL_ID:
        return dict(short_max_s=SHORT_MAX_S, short=resnet34_threshold("short"), long=resnet34_threshold("long"),
                    tier=resnet34_tier())
    return {}


# -- adaptation guards per model ------------------------------------------------------------------------------------

def adapt_guard_overrides(model_id: str) -> dict:
    """The (min_score, anchor_floor) the #2002 adaptation guards use for ``model_id``; ``{}`` = the AdaptConfig defaults
    (Resemblyzer scale). Per-model env: ``ZOE_SPEAKER_ADAPT_RESNET34_MIN_SCORE`` / ``..._ANCHOR_FLOOR``."""
    if model_id == RESNET34_MODEL_ID:
        d_score, d_anchor = RESNET34_ADAPT_MIN_SCORE, RESNET34_ADAPT_ANCHOR_FLOOR
        s = env_float("ZOE_SPEAKER_ADAPT_RESNET34_MIN_SCORE", d_score)
        a = env_float("ZOE_SPEAKER_ADAPT_RESNET34_ANCHOR_FLOOR", d_anchor)
        return dict(min_score=s if math.isfinite(s) else d_score, anchor_floor=a if math.isfinite(a) else d_anchor)
    return {}
