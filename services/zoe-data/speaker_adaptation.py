"""Guarded speaker-profile adaptation: learn a household member's everyday voice, slowly, and only when sure.

Today a voiceprint changes ONLY through explicit enrolment; everyday turns are scored in shadow and thrown away.
That is safe and it ages: a voice drifts with colds, mics, rooms and months. This module lets the stored profile
follow the voice - under guards strict enough that an impostor cannot walk the profile toward themselves.

Policy lives HERE, on the server (the panel is a sensor: it claims, the server decides). The panel daemon already
holds the turn's embedding and sends a qualifying one - never audio - to ``POST /api/voice/adapt``; the normal
profile sync carries the adapted profile back (``overlay_adapted``).

Design
------
* **Anchor + adapted, side by side.** ``speaker_profiles.embedding_blob`` is the enrolled ANCHOR and is never
  touched here (only an explicit enrolment moves it). The adapted profile is a separate column. A re-enrolment bumps
  ``sample_count`` which invalidates the adapted profile (``adapted_from_count``), so the enrol route needs no
  knowledge of adaptation.
* **Every guard must pass** (first failure wins, and is the logged ``reason``):
    1. ``low_score``       - cosine to the profile being served < ``ZOE_SPEAKER_ADAPT_MIN_SCORE`` (0.85). The server
                             recomputes the score from the embedding; the panel's claimed score can only LOWER it.
    2. ``off_anchor``      - cosine to the enrolled anchor < ``..._ANCHOR_FLOOR`` (0.80): a turn must still be
                             recognisably the enrolled voice, not merely like the (possibly drifted) adapted one.
    3. ``short_clip``      - less than ``..._MIN_SPEECH_S`` (2.0) seconds of speech (embedding quality collapses on
                             short clips; the length is the daemon's claim - it holds the audio, the server never does).
    4. ``no_corroboration``- none of: the panel is bound to this user, a login/PIN session for this user is fresh
                             (``..._PIN_WINDOW_S``), a face match for this user. Verified server-side where the server
                             can (bindings, panel session); the face match is a panel claim.
    5. ``close_to_other``  - margin to the best OTHER enrolled profile < ``..._MIN_MARGIN`` (0.10). Vacuous in a
                             household with one enrolled voice (there is nobody to confuse it with), so there the
                             score, anchor floor, corroboration and drift bound carry the load.
    6. ``rate_interval`` / ``rate_daily`` - at most one update per ``..._MIN_INTERVAL_S`` (600) and
                             ``..._MAX_PER_DAY`` (5) per UTC day.
    7. ``drift_bound``     - the updated profile may never be further from the anchor than ``..._MIN_ANCHOR_COS``
                             (0.92 cosine). The update is REFUSED, not clamped: a refused update cannot be steered.
* **Small steps:** ``new = normalize((1 - alpha) * current + alpha * embedding)``, alpha 0.05.
* **Kill switch** ``ZOE_SPEAKER_ADAPT=off|shadow|on`` (default off; anything unparseable is off). ``off`` touches
  nothing - no DB read, no write, sync/identify serve exactly what they served before. ``shadow`` runs the whole
  pipeline and logs what it WOULD write, writing nothing.
* **Forget:** "forget my voice" deletes the ``speaker_profiles`` row (``DELETE /api/voice/profiles/{id}``); the anchor
  and the adapted profile share the row, so both go. ``reset_adaptation`` drops only the adapted profile.
* **Audit:** one ``zoe.speaker_adapt`` INFO line per applied (or shadow would-apply) update: user id, score, margin,
  delta norm, drift cosine, count. No audio, no embedding values.

Not in scope: the daemon decides nothing here; flag-dark; no model is loaded (numpy only).
"""
from __future__ import annotations

import base64
import dataclasses
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import numpy as np

import speaker_models
from typed_env import env_float, env_int, env_str

logger = logging.getLogger(__name__)
audit = logging.getLogger("zoe.speaker_adapt")

MODES = ("off", "shadow", "on")


# ── configuration (read per call so an .env flip applies without a restart) ──

def adapt_mode() -> str:
    """``ZOE_SPEAKER_ADAPT``: ``off`` (default) | ``shadow`` | ``on``. Unknown values are ``off``."""
    raw = env_str("ZOE_SPEAKER_ADAPT", "off").lower()
    return raw if raw in MODES else "off"


def _finite(v: float, default: float) -> float:
    return v if math.isfinite(v) else default


@dataclass(frozen=True)
class AdaptConfig:
    min_score: float = 0.85
    anchor_floor: float = 0.80
    min_margin: float = 0.10
    min_speech_s: float = 2.0
    alpha: float = 0.05
    min_anchor_cos: float = 0.92
    max_per_day: int = 5
    min_interval_s: float = 600.0
    pin_window_s: float = 900.0
    face_min: float = 0.60

    @classmethod
    def from_env(cls, model_id: Optional[str] = None) -> "AdaptConfig":
        """The guards from env; ``model_id`` swaps in that model's own score / anchor constants (``speaker_models.
        adapt_guard_overrides``). Resemblyzer's 0.85 / 0.80 never fire on ResNet34's cosine scale (record, section 5)."""
        d = cls()
        alpha = _finite(env_float("ZOE_SPEAKER_ADAPT_ALPHA", 0.05), d.alpha)
        if not 0.0 < alpha <= 0.5:  # a big step defeats "small steps"; refuse to be configured into one
            alpha = d.alpha
        base = cls(
            min_score=_finite(env_float("ZOE_SPEAKER_ADAPT_MIN_SCORE", 0.85), d.min_score),
            anchor_floor=_finite(env_float("ZOE_SPEAKER_ADAPT_ANCHOR_FLOOR", 0.80), d.anchor_floor),
            min_margin=_finite(env_float("ZOE_SPEAKER_ADAPT_MIN_MARGIN", 0.10), d.min_margin),
            min_speech_s=_finite(env_float("ZOE_SPEAKER_ADAPT_MIN_SPEECH_S", 2.0), d.min_speech_s),
            alpha=alpha,
            min_anchor_cos=_finite(env_float("ZOE_SPEAKER_ADAPT_MIN_ANCHOR_COS", 0.92), d.min_anchor_cos),
            max_per_day=max(0, env_int("ZOE_SPEAKER_ADAPT_MAX_PER_DAY", 5)),
            min_interval_s=max(0.0, _finite(env_float("ZOE_SPEAKER_ADAPT_MIN_INTERVAL_S", 600.0), d.min_interval_s)),
            pin_window_s=max(0.0, _finite(env_float("ZOE_SPEAKER_ADAPT_PIN_WINDOW_S", 900.0), d.pin_window_s)),
            face_min=_finite(env_float("ZOE_SPEAKER_ADAPT_FACE_MIN", 0.60), d.face_min),
        )
        over = speaker_models.adapt_guard_overrides(model_id) if model_id else {}
        return dataclasses.replace(base, **over) if over else base


# ── pure math ──────────────────────────────────────────────────────────────

def _vec(blob: Any) -> Optional[np.ndarray]:
    """float32 vector from stored bytes, or None when it is not a usable embedding."""
    if blob is None:
        return None
    try:
        v = np.frombuffer(bytes(blob), dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if v.size == 0 or not np.all(np.isfinite(v)):
        return None
    return v


def _unit(v: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if v is None:
        return None
    n = float(np.linalg.norm(v))
    if not math.isfinite(n) or n <= 0:
        return None
    return (v / n).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class AdaptState:
    """The rate-limit bookkeeping stored on the profile row."""

    adapt_count: int = 0
    adapted_at: Optional[datetime] = None
    day: Optional[str] = None
    day_count: int = 0


@dataclass
class Decision:
    allowed: bool
    reason: str
    score: Optional[float] = None          # cosine to the profile being served (min'd with the panel's claim)
    anchor_cos: Optional[float] = None
    margin: Optional[float] = None         # None = no other enrolled profile
    delta_norm: Optional[float] = None
    drift_cos: Optional[float] = None      # cosine(updated profile, anchor)
    corroboration: list = field(default_factory=list)
    new_vec: Optional[np.ndarray] = None

    def public(self) -> dict:
        def r(x):
            return None if x is None else round(float(x), 4)
        return {
            "allowed": self.allowed, "reason": self.reason, "score": r(self.score),
            "anchor_cos": r(self.anchor_cos), "margin": r(self.margin),
            "delta_norm": r(self.delta_norm), "drift_cos": r(self.drift_cos),
            "corroboration": list(self.corroboration),
        }


def evaluate(
    *,
    embedding: np.ndarray,
    anchor: np.ndarray,
    adapted: Optional[np.ndarray],
    others: Sequence[np.ndarray],
    claimed_score: Optional[float],
    speech_s: Optional[float],
    corroboration: Sequence[str],
    state: AdaptState,
    now: datetime,
    cfg: AdaptConfig,
) -> Decision:
    """Apply every guard. Pure: no I/O, no clock, no globals - ``now`` and ``cfg`` are arguments.

    ``others`` are the vectors of every OTHER enrolled member's profile (anchor and adapted). ``adapted`` is the
    CURRENTLY VALID adapted profile or None. The returned ``Decision.new_vec`` is the updated unit vector when allowed.
    """
    emb = _unit(embedding)
    anc = _unit(anchor)
    if emb is None or anc is None or emb.shape != anc.shape:
        return Decision(False, "invalid_embedding")
    # Resemblyzer returns unit-norm embeddings. A panel that sends something far from unit norm is not sending one.
    n = float(np.linalg.norm(embedding))
    if not 0.9 <= n <= 1.1:
        return Decision(False, "invalid_embedding")
    cur = _unit(adapted) if adapted is not None and adapted.shape == anc.shape else None
    served = cur if cur is not None else anc

    own = cosine(emb, served)
    score = own
    if claimed_score is not None and math.isfinite(claimed_score):
        score = min(own, float(claimed_score))   # a claim can only make the bar HIGHER
    anchor_cos = cosine(emb, anc)
    best_other = max((cosine(emb, _unit(o)) for o in others if o is not None and _unit(o) is not None
                      and o.shape == anc.shape), default=None)
    margin = None if best_other is None else own - best_other
    d = Decision(False, "ok", score=score, anchor_cos=anchor_cos, margin=margin,
                 corroboration=list(corroboration))

    if score < cfg.min_score:
        d.reason = "low_score"
        return d
    if anchor_cos < cfg.anchor_floor:
        d.reason = "off_anchor"
        return d
    if speech_s is None or not math.isfinite(speech_s) or speech_s < cfg.min_speech_s:
        d.reason = "short_clip"
        return d
    if not corroboration:
        d.reason = "no_corroboration"
        return d
    if margin is not None and margin < cfg.min_margin:
        d.reason = "close_to_other"
        return d
    if state.adapted_at is not None and (now - state.adapted_at).total_seconds() < cfg.min_interval_s:
        d.reason = "rate_interval"
        return d
    today = now.strftime("%Y-%m-%d")
    if state.day == today and state.day_count >= cfg.max_per_day:
        d.reason = "rate_daily"
        return d

    candidate = _unit((1.0 - cfg.alpha) * served + cfg.alpha * emb)
    if candidate is None:
        d.reason = "invalid_embedding"
        return d
    d.drift_cos = cosine(candidate, anc)
    d.delta_norm = float(np.linalg.norm(candidate - served))
    if d.drift_cos < cfg.min_anchor_cos:
        d.reason = "drift_bound"
        return d
    d.allowed = True
    d.new_vec = candidate
    return d


def serve_blob(anchor_blob: Any, adapted_blob: Any, adapted_from_count: Any, sample_count: Any) -> Any:
    """The blob a matcher should be given: the adapted profile iff it is valid for THIS anchor, else the anchor.

    Valid = present, a usable vector of the anchor's dimension, and started from the anchor's current
    ``sample_count`` (a re-enrolment makes it stale).
    """
    if adapted_blob is None or adapted_from_count is None:
        return anchor_blob
    if int(adapted_from_count or 0) != int(sample_count or 0):
        return anchor_blob
    a, v = _vec(anchor_blob), _vec(adapted_blob)
    if a is None or v is None or a.shape != v.shape:
        return anchor_blob
    return bytes(adapted_blob)


# ── persistence (compat-DB: `?` placeholders, runs on PostgreSQL and SQLite) ──

def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(str(raw).strip()[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def overlay_adapted(db: Any, rows: Sequence[Sequence[Any]], *, user_idx: int, blob_idx: int,
                          model_idx: Optional[int] = None) -> list:
    """Replace each row's anchor blob with its valid adapted blob (mode ``on`` only; the caller checks the mode).

    The adapted profile belongs to ONE model's row, so the lookup is keyed by (user, model): ``model_idx`` is the column
    of ``rows`` that holds the row's model id (None = every row is the legacy model). A ResNet34 anchor is never
    overlaid with a Resemblyzer adapted blob, or the reverse.

    Fails SAFE: any error (e.g. the migration has not run) serves the anchors untouched. Consent was already applied
    by the caller's query, so an unconsented profile never reaches this function.
    """
    out = [tuple(r) for r in rows]
    try:
        async with db.execute(
            "SELECT user_id, adapted_blob, adapted_from_count, sample_count, " + speaker_models.MODEL_SQL
            + " FROM speaker_profiles WHERE consent_at IS NOT NULL AND adapted_blob IS NOT NULL"
        ) as cur:
            adapted_rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("speaker adaptation overlay unavailable (serving anchors): %s", exc)
        return out
    by_key = dict(((r[0], speaker_models.normalize_model_id(r[4])), r) for r in adapted_rows)
    for i, row in enumerate(out):
        mid = speaker_models.row_model_id(row, model_idx) if model_idx is not None else speaker_models.LEGACY_MODEL_ID
        a = by_key.get((row[user_idx], mid))
        if not a:
            continue
        blob = serve_blob(row[blob_idx], a[1], a[2], a[3])
        if blob is not row[blob_idx]:
            lst = list(row)
            lst[blob_idx] = blob
            out[i] = tuple(lst)
    return out


async def serve_adapted_profiles(rows: Sequence[Sequence[Any]], *, user_idx: int, blob_idx: int,
                                 model_idx: Optional[int] = None) -> Sequence:
    """The feed hook (``/profiles/sync``, ``/identify``): in mode ``on`` swap anchors for valid adapted profiles;
    in ANY other mode return ``rows`` itself - no DB read, nothing changed, so the flag-off feed is byte-identical to
    before adaptation existed. Fails safe to the anchors."""
    if adapt_mode() != "on":
        return rows
    from db_compat import get_compat_db

    try:
        async with get_compat_db() as db:
            return await overlay_adapted(db, rows, user_idx=user_idx, blob_idx=blob_idx, model_idx=model_idx)
    except Exception as exc:
        logger.warning("speaker adaptation overlay failed (serving anchors): %s", exc)
        return rows


async def _verify_corroboration(db: Any, *, panel_id: str, user_id: str, claims: dict, now: datetime,
                                cfg: AdaptConfig) -> list:
    """Which independent signals vouch that ``user_id`` is really the person at this panel. Fails closed."""
    got: list = []
    try:
        async with db.execute(
            "SELECT user_id FROM panel_user_bindings WHERE panel_id = ? AND binding_type = 'default' LIMIT 1",
            (panel_id,),
        ) as cur:
            row = await cur.fetchone()
        if row and row[0] == user_id:
            got.append("panel_bound")
    except Exception as exc:
        logger.debug("speaker adapt: binding lookup failed: %s", exc)
    try:
        async with db.execute(
            "SELECT user_id, last_seen_at FROM ui_panel_sessions WHERE panel_id = ? "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (panel_id,),
        ) as cur:
            row = await cur.fetchone()
        seen = _parse_ts(row[1]) if row else None
        if row and row[0] == user_id and seen is not None \
                and 0 <= (now - seen).total_seconds() <= cfg.pin_window_s:
            got.append("pin_recent")
    except Exception as exc:
        logger.debug("speaker adapt: panel-session lookup failed: %s", exc)
    try:
        if str(claims.get("face_user_id") or "") == user_id and float(claims.get("face_score")) >= cfg.face_min:
            got.append("face")
    except (TypeError, ValueError):
        pass
    return got


def _audit(user_id: str, mode: str, applied: bool, d: Decision, count: int, panel_id: str) -> None:
    audit.info(
        "speaker_adapt user=%s panel=%s mode=%s %s score=%.4f margin=%s delta_norm=%.5f drift_cos=%.4f "
        "count=%d corroboration=%s",
        user_id, panel_id, mode, "applied" if applied else "would_apply",
        d.score or 0.0, "n/a" if d.margin is None else f"{d.margin:.4f}",
        d.delta_norm or 0.0, d.drift_cos or 0.0, count, ",".join(d.corroboration),
    )


async def process_adaptation(db: Any, *, panel_id: str, payload: dict, now: Optional[datetime] = None,
                             mode: Optional[str] = None, cfg: Optional[AdaptConfig] = None) -> dict:
    """Handle one panel offer. Returns ``{ok, mode, applied, reason, ...decision}``; never raises on policy."""
    mode = adapt_mode() if mode is None else mode
    if mode == "off":
        return {"ok": True, "mode": "off", "applied": False, "reason": "off"}
    payload = payload or {}
    model_id = speaker_models.normalize_model_id(payload.get("model_id"))
    cfg = cfg or AdaptConfig.from_env(model_id)     # each model carries its own score / anchor guards
    now = now or datetime.now(timezone.utc)

    def refuse(reason: str, **extra) -> dict:
        logger.debug("speaker adapt refused: %s", reason)
        return dict(ok=True, mode=mode, applied=False, reason=reason, model_id=model_id, **extra)

    user_id = str(payload.get("voice_user_id") or payload.get("user_id") or "").strip()
    if not user_id:
        return refuse("no_user")
    if not speaker_models.is_known(model_id):
        return refuse("unknown_model")
    if model_id != speaker_models.acting_model():
        return refuse("not_acting_model")           # only the model that ACTS is ever adapted (never a shadow one)
    try:
        emb_bytes = base64.b64decode(str(payload.get("embedding_base64") or ""), validate=True)
    except Exception:
        return refuse("invalid_embedding")
    embedding = _vec(emb_bytes)
    if embedding is None:
        return refuse("invalid_embedding")
    try:
        claimed = float(payload.get("voice_score"))
    except (TypeError, ValueError):
        claimed = None
    try:
        speech_s = float(payload.get("speech_s"))
    except (TypeError, ValueError):
        speech_s = None
    claims = payload.get("corroboration") if isinstance(payload.get("corroboration"), dict) else {}

    try:
        async with db.execute(
            "SELECT id, embedding_blob, sample_count, consent_at, adapted_blob, adapted_from_count, adapt_count, "
            "adapted_at, adapt_day, adapt_day_count FROM speaker_profiles WHERE user_id = ? AND "
            + speaker_models.MODEL_SQL + " = ?",
            (user_id, model_id),
        ) as cur:
            row = await cur.fetchone()
        # the OTHER members' profiles of the SAME model only: a different model's vectors are not comparable
        async with db.execute(
            "SELECT user_id, embedding_blob, sample_count, adapted_blob, adapted_from_count FROM speaker_profiles "
            "WHERE consent_at IS NOT NULL AND user_id != ? AND " + speaker_models.MODEL_SQL + " = ?",
            (user_id, model_id),
        ) as cur:
            other_rows = await cur.fetchall()
    except Exception as exc:  # e.g. migration 0048 not applied: fail closed, write nothing
        logger.warning("speaker adapt: storage unavailable (%s)", exc)
        return refuse("storage_unavailable")
    if not row:
        return refuse("no_profile")
    (pid, anchor_blob, sample_count, consent_at, adapted_blob, from_count, adapt_count, adapted_at, day,
     day_count) = row
    if consent_at is None:
        return refuse("no_consent")
    anchor = _vec(anchor_blob)
    if anchor is None:
        return refuse("invalid_embedding")

    served = serve_blob(anchor_blob, adapted_blob, from_count, sample_count)
    adapted = _vec(served) if served is not anchor_blob else None
    others: list = []
    for o in other_rows:
        for blob in (o[1], serve_blob(o[1], o[3], o[4], o[2])):
            v = _vec(blob)
            if v is not None:
                others.append(v)

    corroboration = await _verify_corroboration(db, panel_id=panel_id, user_id=user_id, claims=claims, now=now,
                                                cfg=cfg)
    state = AdaptState(adapt_count=int(adapt_count or 0), adapted_at=_parse_ts(adapted_at),
                       day=str(day) if day else None, day_count=int(day_count or 0))
    d = evaluate(embedding=embedding, anchor=anchor, adapted=adapted, others=others, claimed_score=claimed,
                 speech_s=speech_s, corroboration=corroboration, state=state, now=now, cfg=cfg)
    out = dict(ok=True, mode=mode, applied=False, model_id=model_id)
    out.update(d.public())
    if not d.allowed:
        logger.debug("speaker adapt refused user=%s reason=%s", user_id, d.reason)
        return out

    today = now.strftime("%Y-%m-%d")
    new_day_count = state.day_count + 1 if state.day == today else 1
    if mode == "shadow":
        _audit(user_id, mode, False, d, state.adapt_count + 1, panel_id)
        out["reason"] = "shadow"
        out["would_apply"] = True
        return out

    cur = await db.execute(
        "UPDATE speaker_profiles SET adapted_blob = ?, adapted_from_count = ?, "
        "adapt_count = COALESCE(adapt_count, 0) + 1, adapted_at = ?, adapt_day = ?, adapt_day_count = ? "
        "WHERE id = ? AND COALESCE(adapt_count, 0) = ?",
        (d.new_vec.astype(np.float32).tobytes(), int(sample_count or 0), now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         today, new_day_count, pid, state.adapt_count),
    )
    if getattr(cur, "rowcount", 1) == 0:   # a concurrent update won the optimistic-concurrency race
        out["reason"] = "conflict"
        return out
    await db.commit()
    _audit(user_id, mode, True, d, state.adapt_count + 1, panel_id)
    out["applied"] = True
    out["reason"] = "applied"
    return out


async def reset_adaptation(db: Any, user_id: str) -> bool:
    """Drop ONLY the adapted profile (the enrolled anchor stays). The daily-cap counters stay too, so
    reset-then-relearn is not a way around the rate limit. True when a row was changed."""
    cur = await db.execute(
        "UPDATE speaker_profiles SET adapted_blob = NULL, adapted_from_count = NULL, adapt_count = 0, "
        "adapted_at = NULL WHERE user_id = ? AND adapted_blob IS NOT NULL",
        (user_id,),
    )
    await db.commit()
    return getattr(cur, "rowcount", 0) != 0
