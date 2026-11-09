"""Passive named guests: an unknown voice that keeps talking TO Zoe at the panel is grouped, and once Zoe is sure it is one person
she asks "I don't think we've met. What's your name?" - the answer makes them a named guest (owner 2026-11-08: "can't Zoe learn a
person's voice with enough conversation?"). Runbook ``docs/knowledge/named-guests.md``; flag ``ZOE_GUEST_VOICE_LEARN`` (off DEFAULT
| shadow | on), separate from the explicit "learn Quinn's voice" flow.

WHAT IS KEPT  the speaker EMBEDDING of an addressed panel turn (a wake-word or window turn the daemon scored) that the server matched
              to no member and no named guest - never audio ("signatures, not recordings"). ``panel_voice_samples`` (at most
              ``MAX_SAMPLES`` per voice) under a household ``panel_voice_clusters`` row (migration 0073). Biometric: the nightly backup
              carries no row data of either table.
THE GUARDS    * never a member's voice: a sample within the member bar (the model's FAR-1 % threshold for the clip length) of ANY
                consented member profile, or of any named guest, is dropped before it is kept - so no cluster can absorb a member.
              * conservative grouping: a sample joins a voice only at ``ZOE_GUEST_LEARN_JOIN`` (0.45, the cut the Omi calibration
                chose for the same model: purity first, a split voice only costs a second ask); voices are NEVER merged (one person
                can form two acoustic clusters - the known trap - and is then simply asked twice, never mixed with someone else).
              * "confident enough" = ``MIN_TURNS`` turns, over ``MIN_SESSIONS`` conversations or days, at least ``MIN_WAKE`` of them
                woken by the wake word (a television that only ever leaks into listening windows never qualifies), and a mean
                sample-to-centre cosine of at least ``ZOE_GUEST_LEARN_TIGHT``. Starting values from the repo's calibration records,
                to be re-derived from the shadow logs (``GUEST_LEARN`` lines carry counts and scores, never a vector or a word).
              * a voice never confirmed is dropped after ``ZOE_GUEST_LEARN_EXPIRE_DAYS`` (14) without a new turn.
              * a name answer from this (unverified) voice only CREATES a new named guest from its OWN voice: a name already held by any
                named guest, or a household member's name, is refused - it can never claim an existing person.
THE OWNER     lists, names ("that was Quinn this morning"), and erases unknown voices (``/api/voice/unknown-voices``); a named voice
              becomes a named guest under the member who named it (an answered ask: under the household's first admin).
"""
from __future__ import annotations

import base64
import contextvars
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

import speaker_models
from typed_env import flag

logger = logging.getLogger(__name__)

MAX_SAMPLES = 30
ASK_WINDOW_S = 600           # an answer must come within ten minutes of the ask
_NAME_ANSWER = re.compile(r"^(?:(?:hi|hello|hey|oh|um|well|yeah|yes|zoe)[\s,]+)*(?:i'?m|i am|it'?s|my name'?s|my name is|call me|this is)?\s*"
                          r"(?P<name>[a-z][a-z'\-]{1,19})(?:\s+(?:here|thanks|thank you))?[\s.!]*$", re.I)
_NOT_NAMES = frozenset({"fine", "good", "okay", "ok", "sorry", "here", "nobody", "no", "yes", "yeah", "not", "just", "a", "the",
                        "zoe", "guest", "great", "well", "back", "home", "busy", "tired"})


def mode() -> str:
    return str(flag("ZOE_GUEST_VOICE_LEARN"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64).reshape(-1)
    return (a / max(float(np.linalg.norm(a)), 1e-12)).astype(np.float32)


def _vec(b) -> np.ndarray:
    return np.frombuffer(bytes(b), dtype="<f4").astype(np.float32)


def thresholds() -> dict:
    return dict(join=float(flag("ZOE_GUEST_LEARN_JOIN")), tight=float(flag("ZOE_GUEST_LEARN_TIGHT")),
                min_turns=int(flag("ZOE_GUEST_LEARN_MIN_TURNS")), min_sessions=int(flag("ZOE_GUEST_LEARN_MIN_SESSIONS")),
                min_wake=int(flag("ZOE_GUEST_LEARN_MIN_WAKE")), expire_days=int(flag("ZOE_GUEST_LEARN_EXPIRE_DAYS")))


def ready(c: dict, t: Optional[dict] = None) -> bool:
    """Is this unknown voice one person Zoe may ask? Pure."""
    t = t or thresholds()
    return (c["n_turns"] >= t["min_turns"] and max(len(c["sessions"]), len(c["days"])) >= t["min_sessions"]
            and c["n_wake"] >= t["min_wake"] and c["tightness"] >= t["tight"])


def answer_name(text: str) -> str:
    """The name in a short answer ("I'm Quinn", "it's Quinn", "Quinn"), or "". Pure."""
    m = _NAME_ANSWER.match((text or "").strip())
    if not m:
        return ""
    name = m.group("name").lower()
    return "" if name in _NOT_NAMES else name[:1].upper() + name[1:]


def sample_from(payload: dict, caller: dict) -> Optional[tuple[np.ndarray, str]]:
    """``(unit embedding, length group)`` of an addressed panel turn the daemon attached, or None. Device on the LAN, acting model."""
    c = caller or {}
    if c.get("source") != "device" or c.get("via_relay") or c.get("via_tunnel"):
        return None
    block = (payload or {}).get("speaker")
    if not isinstance(block, dict) or str(block.get("model_id") or "") != speaker_models.acting_model():
        return None
    try:
        v = speaker_models.validate_embedding(base64.b64decode(str(block.get("embedding") or ""), validate=True),
                                              speaker_models.acting_model())
    except Exception:  # noqa: BLE001
        v = None
    if v is None:
        return None
    return _unit(v), speaker_models.length_group(block.get("speech_s"))


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _rows(db, sql, params=()):
    async with db.execute(sql, tuple(params)) as cur:
        return await cur.fetchall()


async def _known_voices(db, model_id: str) -> list[np.ndarray]:
    """Every consented member profile and every named guest print of this model: a sample near any of them is never kept."""
    out = []
    for (blob,) in await _rows(db, "SELECT embedding_blob FROM speaker_profiles WHERE consent_at IS NOT NULL AND "
                                   + speaker_models.MODEL_SQL + " = ?", (model_id,)):
        out.append(_unit(_vec(blob)))
    for (blob,) in await _rows(db, "SELECT embedding FROM learned_voiceprints WHERE channel = 'panel' AND model_id = ?", (model_id,)):
        out.append(_unit(_vec(blob)))
    return [v for v in out if v.size == 256 and np.all(np.isfinite(v))]


def _cluster(row) -> dict:
    return dict(id=row[0], centroid=_unit(_vec(row[1])), n_turns=int(row[2]), n_wake=int(row[3]),
                sessions=json.loads(row[4] or "[]"), days=json.loads(row[5] or "[]"), tightness=float(row[6] or 0.0),
                status=row[7], asked_at=row[8], last_at=row[9])


_CLUSTER_COLS = "id, centroid, n_turns, n_wake, sessions, days, tightness, status, asked_at, last_at"


async def sweep(db, now: Optional[datetime] = None) -> int:
    stamp = _iso(now or _now())
    gone = [r[0] for r in await _rows(db, "SELECT id FROM panel_voice_clusters WHERE expires_at < ?", (stamp,))]
    for cid in gone:
        await db.execute("DELETE FROM panel_voice_samples WHERE cluster_id = ?", (cid,))
        await db.execute("DELETE FROM panel_voice_clusters WHERE id = ?", (cid,))
    return len(gone)


async def observe(payload: dict, caller: dict, *, session_id: str, wake: bool, text: str = "",
                  now: Optional[datetime] = None) -> dict:
    """Keep one unknown, addressed turn's voice. Returns ``{"action": ...}``: ``off`` | ``skip`` | ``member_like`` | ``kept`` |
    ``ask`` (Zoe should ask this voice its name now) | ``named`` (+ ``guest``: this turn answered the ask). NEVER raises."""
    try:
        m = mode()
        if m == "off":
            return {"action": "off"}
        got = sample_from(payload, caller)
        if got is None:
            return {"action": "skip"}
        vec, group = got
        model_id = speaker_models.acting_model()
        t = thresholds()
        now = now or _now()
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            await sweep(db, now)
            guard = speaker_models.RESNET34_THRESHOLDS["far1"]["short" if group == "short" else "long"]
            near = max((float(k @ vec) for k in await _known_voices(db, model_id)), default=-1.0)
            if near >= guard:
                await db.commit()
                logger.info("GUEST_LEARN mode=%s action=member_like near=%.3f", m, near)
                return {"action": "member_like"}
            clusters = [_cluster(r) for r in await _rows(db, f"SELECT {_CLUSTER_COLS} FROM panel_voice_clusters WHERE model_id = ?",
                                                         (model_id,))]
            best = max(clusters, key=lambda c: float(c["centroid"] @ vec), default=None)
            score = float(best["centroid"] @ vec) if best else -1.0
            if best is not None and best["status"] == "asked" and m == "on" and best["asked_at"] and \
                    (now - datetime.strptime(best["asked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() \
                    <= ASK_WINDOW_S and score >= t["join"]:
                name = answer_name(text)
                if name:
                    guest = await _name_cluster(db, best["id"], name, owner=None)
                    await db.commit()
                    logger.info("GUEST_LEARN mode=on action=%s", "named" if guest else "name_refused")
                    return {"action": "named", "guest": guest} if guest else {"action": "name_refused"}
            if best is None or score < t["join"]:
                holder = await _household_owner(db)
                if not holder:                       # nobody to hold an unknown voice for: nothing is kept
                    await db.commit()
                    return {"action": "skip"}
                cid = str(uuid.uuid4())
                await db.execute("INSERT INTO panel_voice_clusters (id, user_id, model_id, centroid, n_turns, n_wake, sessions, days, "
                                 "tightness, status, first_at, last_at, expires_at) VALUES (?, ?, ?, ?, 0, 0, '[]', '[]', 0, 'open', ?, ?, ?)",
                                 (cid, holder, model_id, vec.astype("<f4").tobytes(), _iso(now), _iso(now), _iso(now)))
                best = dict(id=cid, centroid=vec, n_turns=0, n_wake=0, sessions=[], days=[], tightness=0.0, status="open",
                            asked_at=None, last_at=_iso(now))
            await db.execute("INSERT INTO panel_voice_samples (id, cluster_id, embedding, session_id, day, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), best["id"], vec.astype("<f4").tobytes(),
                                                           str(session_id or "")[:80], _iso(now)[:10], _iso(now)))
            samples = [_unit(_vec(r[0])) for r in await _rows(
                db, "SELECT embedding FROM panel_voice_samples WHERE cluster_id = ? ORDER BY created_at DESC", (best["id"],))]
            for (sid,) in (await _rows(db, "SELECT id FROM panel_voice_samples WHERE cluster_id = ? ORDER BY created_at DESC",
                                      (best["id"],)))[MAX_SAMPLES:]:
                await db.execute("DELETE FROM panel_voice_samples WHERE id = ?", (sid,))
            samples = samples[:MAX_SAMPLES]
            centre = _unit(np.mean(np.stack(samples), axis=0))
            tight = float(np.mean([float(s @ centre) for s in samples]))
            sessions = (best["sessions"] + [str(session_id or "")[:80]])[-10:]
            days = sorted(set(best["days"] + [_iso(now)[:10]]))[-10:]
            c = dict(best, n_turns=best["n_turns"] + 1, n_wake=best["n_wake"] + (1 if wake else 0),
                     sessions=sorted(set(sessions)), days=days, tightness=tight)
            stale_ask = c["status"] == "asked" and (not c["asked_at"] or (now - datetime.strptime(
                c["asked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() > 86400)
            ask = ready(c, t) and (c["status"] == "open" or stale_ask)      # asked at most once a day
            status = "asked" if (ask and m == "on") else c["status"]
            asked_at = _iso(now) if (ask and m == "on") else c["asked_at"]
            await db.execute(
                "UPDATE panel_voice_clusters SET centroid = ?, n_turns = ?, n_wake = ?, sessions = ?, days = ?, tightness = ?, status = ?, "
                "asked_at = ?, last_at = ?, expires_at = ? WHERE id = ?",
                (centre.astype("<f4").tobytes(), c["n_turns"], c["n_wake"], json.dumps(c["sessions"]), json.dumps(days), tight, status,
                 asked_at, _iso(now),
                 _iso(now + timedelta(days=t["expire_days"])), c["id"]))
            await db.commit()
        logger.info("GUEST_LEARN mode=%s action=%s turns=%d sessions=%d wake=%d tight=%.3f join=%.3f", m,
                    "ask" if ask else "kept", c["n_turns"], max(len(c["sessions"]), len(days)), c["n_wake"], tight, score)
        if ask and m == "on":
            return {"action": "ask", "cluster_id": c["id"]}
        return {"action": "kept", "cluster_id": c["id"]}
    except Exception as exc:  # noqa: BLE001 - learning is optional; the turn is not
        logger.warning("named_guest_learning: observe failed: %s", type(exc).__name__)
        return {"action": "skip"}


async def _household_owner(db) -> Optional[str]:
    rows = await _rows(db, "SELECT id FROM users WHERE role IN ('admin', 'family-admin') ORDER BY id LIMIT 1")
    return str(rows[0][0]) if rows else None


async def _name_cluster(db, cluster_id: str, name: str, *, owner: Optional[str]):
    by_member = owner is not None
    """Make the unknown voice a named guest: refused (None) when the name is a member's or ANY named guest's already (an unverified
    voice can only create, never claim), or when there is no owner. The voice's samples and row go once it is a guest."""
    import contacts_store as cs
    import named_guest_enrol as nge
    import named_guests as ng
    import omi_voices

    rows = await _rows(db, "SELECT centroid, model_id, n_turns, user_id FROM panel_voice_clusters WHERE id = ? AND expires_at >= ?",
                       (cluster_id, _iso(_now())))          # an expired voice is gone, never promoted
    if not rows or await nge._is_member_name(db, name):
        return None
    key = cs.fold(name)
    for (other,) in await _rows(db, "SELECT p.name FROM learned_voiceprints v JOIN people p ON p.id = v.people_id "
                                    "WHERE v.channel = 'panel' AND (p.deleted = 0 OR p.deleted IS NULL)"):
        if cs.fold(other or "") == key:
            return None
    owner = owner or str(rows[0][3] or "") or await _household_owner(db)
    if not owner:
        return None
    import memory_forgotten as mf

    if await mf.matches(owner, name, strict=True):          # forgotten for good: a returning voice cannot bring the name back
        return None
    pid = await cs._create_person(db, owner, name, now=cs.now_iso())      # always a NEW person: never linked to an existing one
    if by_member:   # a member's naming is their own word; an answered ask is the guest's own introduction (the people row only)
        await cs.add_field(db, owner, pid, kind="name", value=name, provenance="entered_by_member", provenance_ref="named_guest_learned")
    now = cs.now_iso()
    await db.execute(
        "INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, n_segments, n_recordings, status, "
        "learning_enabled, created_at, updated_at, last_confirmed_at, expires_at) VALUES (?, ?, ?, ?, 'panel', ?, ?, 0, 'confirmed', 1, "
        "?, ?, ?, ?)", (str(uuid.uuid4()), owner, pid, rows[0][1], bytes(rows[0][0]), int(rows[0][2]), now, now, now,
                        omi_voices.KEEP_UNTIL_FORGOTTEN))
    # The named voice AND every other unknown voice near it go: one person can form two clusters, and a leftover copy of their
    # embeddings must not outlive them as an "unknown" (or recreate them after a forget). Review #2343.
    centre = _unit(_vec(rows[0][0]))
    guard = speaker_models.RESNET34_THRESHOLDS["far1"]["long"]
    doomed = [cluster_id] + [r[0] for r in await _rows(db, "SELECT id, centroid FROM panel_voice_clusters WHERE model_id = ? AND id <> ?",
                                                       (rows[0][1], cluster_id)) if float(_unit(_vec(r[1])) @ centre) >= guard]
    for cid in doomed:
        await db.execute("DELETE FROM panel_voice_samples WHERE cluster_id = ?", (cid,))
        await db.execute("DELETE FROM panel_voice_clusters WHERE id = ?", (cid,))
    return ng.Guest(person_id=pid, name=name, owner_user_id=owner)


# ── the owner's view ──────────────────────────────────────────────────────────────────────────────────────────────────────

async def list_unknown() -> list[dict]:
    """Unknown voices: counts and dates only, never a vector."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        await sweep(db)
        await db.commit()
        rows = await _rows(db, "SELECT id, n_turns, n_wake, sessions, days, tightness, status, first_at, last_at FROM "
                               "panel_voice_clusters ORDER BY last_at DESC")
    return [dict(id=r[0], turns=r[1], wake_turns=r[2], conversations=len(json.loads(r[3] or "[]")), days=json.loads(r[4] or "[]"),
                 tightness=round(float(r[5] or 0), 3), status=r[6], first_heard=r[7], last_heard=r[8], ready=ready(dict(
                     n_turns=r[1], n_wake=r[2], sessions=json.loads(r[3] or "[]"), days=json.loads(r[4] or "[]"),
                     tightness=float(r[5] or 0)))) for r in rows]


async def name_unknown(cluster_id: str, name: str, owner: str) -> Optional[dict]:
    """A member names an unknown voice ("that was Quinn this morning"): a named guest in THEIR layer, or None (refused)."""
    import named_guest_enrol as nge
    from db_compat import get_compat_db

    clean = nge._clean_name(name)
    if not clean:
        return None
    async with get_compat_db() as db:
        await sweep(db)
        guest = await _name_cluster(db, cluster_id, clean, owner=owner)
        await db.commit()
    return dict(guest_id=guest.person_id, name=guest.name) if guest else None


async def erase_unknown(cluster_id: Optional[str] = None) -> int:
    """Erase one unknown voice (or all of them): its samples and its row. RAISES on a failure (fail closed)."""
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        if cluster_id is None:
            await db.execute("DELETE FROM panel_voice_samples")
            cur = await db.execute("DELETE FROM panel_voice_clusters")
        else:
            await db.execute("DELETE FROM panel_voice_samples WHERE cluster_id = ?", (cluster_id,))
            cur = await db.execute("DELETE FROM panel_voice_clusters WHERE id = ?", (cluster_id,))
        n = int(getattr(cur, "rowcount", 0) or 0)
        await db.commit()
    return n


_ASK: contextvars.ContextVar[bool] = contextvars.ContextVar("named_guest_ask", default=False)


def bind_ask(on: bool) -> None:
    _ASK.set(bool(on))


def asking() -> bool:
    return _ASK.get()


def ask_line() -> str:
    return ("You have heard this voice several times but do not know who it is. In this reply, ask them once, kindly: "
            "\"I don't think we've met. What's your name?\" They are a guest: share nothing private about the household.")
