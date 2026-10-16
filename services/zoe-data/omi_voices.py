"""Speaker turns in a member's pendant recordings, the voices Zoe hears in them, and the voices she learns from the member's tags.

Design ``docs/architecture/memories-space.md`` sections 2.4 / 2.6; owner decision 2026-10-14 ("Zoe needs to learn" - NO spoken-
consent gate). Tables: migration 0054 (+ ``learned_voiceprints`` from 0053). Writers: ``omi_recordings._transcribe_one`` (segments,
via ``store_segments`` + ``learn``) and ``routers/omi_voices.py`` (the member's own tag API). Flags:

  ``ZOE_OMI_SPEAKER_TAG`` (omi_diarize.mode)  off = no segments are written at all; shadow / on = turns are stored with labels.
  ``ZOE_OMI_LEARN_VOICES`` (default OFF)      off = no bystander embedding is stored, no voice is clustered, a tag is a LABEL only.

THE GUARDRAILS (each pinned by ``tests/test_omi_voices.py``):
  1. In the TAGGING member's layer, on the box: every query filters on that member; another member's voices are invisible to
     them (two members who tag the same person hold two separate voiceprints). Never exported; the backup skips the data.
  2. Suggestions / attribution in that member's own recordings ONLY. Nothing here is read by auth, sign-in, the voice session,
     presence, ``/api/voice/identify`` or the profile sync - a static allow-list test pins which modules may name the tables.
  3. Only the member's tag names a person; the day review reads only confirmed tags (``review_segments``). Suggestions
     (``suggested_person_id``, never a tag) are the follow-up PR; the column and every erase of it are here.
  4. Unconfirmed voiceprints expire (``sweep``), bystander embeddings and unknown-voice clusters expire after 14 days; "forget
     <name>" (``purge_people`` via ``contacts_store.forget_voiceprints``), "forget this voice", "stop learning", deleting a
     recording (``drop_recording``: contributions removed and the mean REBUILT) and erasing the member (``forget_user``) erase.
  5. Children: a member who is not positively an adult (``manner_block.eligible``) never has a bystander voice stored, and a
     tagged person who is (or may be) a child is a label only - no voiceprint (D16).
The wearer's own ``you`` segments are never stored; an ``unsure`` one only when it scored well below the wearer's bar, so the
wearer's voice cannot leak into somebody else's voiceprint.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import numpy as np

from typed_env import env_bool, env_float, env_int

logger = logging.getLogger(__name__)

EMBED_TTL_DAYS = 14
PROVISIONAL_DAYS = 30
IDLE_DAYS = 365
CLUSTER_MIN = 0.45            # an unknown voice joins a cluster at this cosine to its centroid (calibrated, knowledge doc)
LOW_MARGIN = 0.10             # an ``unsure`` segment is learnable only this far below the wearer's bar
MIN_LEARN_MS = 2000
LOO_TAU = 0.30                # leave-one-out outlier rule of the panel's 5-clip enrolment (#2024)
CHILD_WORDS = frozenset({"child", "kid", "son", "daughter", "baby", "toddler", "grandson", "granddaughter", "grandchild",
                         "stepson", "stepdaughter", "boy", "girl", "infant", "teen", "teenager", "minor"})
NOT_PERSON_KINDS = ("zoe", "tv", "radio", "media")
_SEG_COLS = ("id", "recording_id", "seq", "start_ms", "end_ms", "text", "speaker", "score", "windows", "owner_label", "not_person",
             "tagged_person_id", "tag_source", "suggested_person_id", "suggested_score", "voice_cluster_id")


def learn_enabled() -> bool:
    return env_bool("ZOE_OMI_LEARN_VOICES", default=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days(value: int) -> int:
    return max(1, value)


def _blob(v: Any) -> bytes:
    return np.asarray(v, dtype="<f4").reshape(-1).tobytes()


def _vec(b: Any) -> np.ndarray:
    return np.frombuffer(bytes(b), dtype="<f4").astype(np.float32)


def _unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64).reshape(-1)
    return (a / max(float(np.linalg.norm(a)), 1e-12)).astype(np.float32)


def _qs(n: int) -> str:
    return ",".join("?" * n)


def _missing(exc: BaseException) -> bool:
    return type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower()


async def _all(db, sql: str, params: Iterable = ()) -> list:
    cur = await db.execute(sql, tuple(params))
    return list(await cur.fetchall())


async def _run(db, sql: str, params: Iterable = ()) -> int:
    cur = await db.execute(sql, tuple(params))
    return int(getattr(cur, "rowcount", 0) or 0)


class VoiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- who may learn --------------------------------------------------------------------------------------------------------
async def member_learning(db, user_id: str) -> bool:
    """The member's own "stop learning voices" switch (default on)."""
    rows = await _all(db, "SELECT learning_enabled FROM omi_voice_settings WHERE user_id = ?", (user_id,))
    return bool(rows[0][0]) if rows else True


async def may_learn(db, user_id: str) -> bool:
    """Flag on, the member's switch on, and the member POSITIVELY an adult (fail closed: a child's layer holds no bystander
    voiceprint, and an unknown role or a failed lookup means no)."""
    if not learn_enabled() or not await member_learning(db, user_id):
        return False
    import manner_block
    return await manner_block.eligible(user_id, db=db)


def person_may_be_child(person: dict, today: Optional[date] = None) -> bool:
    words = set(re.findall(r"[a-z]+", str(person.get("relationship") or "").lower()))
    if words & CHILD_WORDS:
        return True
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(person.get("birthday") or ""))
    if m:
        t = today or date.today()
        y, mo, d = (int(g) for g in m.groups())
        if y > 1900 and (t.year - y - ((t.month, t.day) < (mo, d))) < 18:
            return True
    return False


async def wearer_profile(db, user_id: str, model_id: str) -> Optional[np.ndarray]:
    """The member's OWN consented panel profile in THIS model (the stricter fallback until a pendant enrolment exists), or None."""
    import speaker_models
    rows = await _all(db, "SELECT embedding_blob FROM speaker_profiles WHERE user_id = ? AND consent_at IS NOT NULL AND "
                          + speaker_models.MODEL_SQL + " = ?", (user_id, model_id))
    if not rows or not rows[0][0]:
        return None
    v = _vec(rows[0][0])
    return _unit(v) if v.size == 256 and np.all(np.isfinite(v)) else None


# --- segments --------------------------------------------------------------------------------------------------------------
async def store_segments(db, user_id: str, rid: str, segs: list, *, model_id: Optional[str], channel: Optional[str]) -> list[str]:
    """Replace the recording's segments (a re-run after a crash rewrites them, never duplicates). ``segs``: dicts with
    start_ms, end_ms, text, speaker, score, windows. Returns the new ids in order."""
    await _run(db, "DELETE FROM omi_segment_embeddings WHERE segment_id IN (SELECT id FROM omi_segments WHERE recording_id = ?)", (rid,))
    await _run(db, "DELETE FROM omi_segments WHERE recording_id = ?", (rid,))
    now, ids = _iso(_now()), []
    for i, s in enumerate(segs):
        sid = str(uuid.uuid4())
        ids.append(sid)
        await _run(db, "INSERT INTO omi_segments (id, recording_id, user_id, seq, start_ms, end_ms, text, speaker, score, windows, "
                       "model_id, profile_channel, not_person, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (sid, rid, user_id, i, int(s["start_ms"]), int(s["end_ms"]), s.get("text") or "", s["speaker"], s.get("score"),
                    int(s.get("windows") or 0), model_id, channel, s.get("not_person"), now))
    return ids


async def zoe_spoken_spans(db, user_id: str, rid: str) -> list[tuple[int, int]]:
    """THE SEAM for "the pendant heard Zoe" (gap audit P1-5): spans (ms into recording ``rid``) when a panel was speaking a reply
    to this member, so those lines are marked ``not_person='zoe'``. Returns [] today: no store logs TTS playback with wall-clock
    times yet, and only a ``device`` / ``corrected`` recording clock may be aligned to one. Fill this, not the callers."""
    return []


def mark_overlaps(segs: list, spans: list, kind: str = "zoe", min_share: float = 0.5) -> int:
    """Mark each segment at least ``min_share`` inside the given spans as not a person. Pure; returns how many."""
    n = 0
    for s in segs:
        dur = max(1, int(s["end_ms"]) - int(s["start_ms"]))
        cover = sum(max(0, min(int(s["end_ms"]), b) - max(int(s["start_ms"]), a)) for a, b in spans)
        if cover / dur >= min_share and not s.get("not_person"):
            s["not_person"] = kind
            n += 1
    return n


def learnable(seg: dict, you_min: float) -> bool:
    if seg.get("not_person") or seg.get("embedding") is None or int(seg["end_ms"]) - int(seg["start_ms"]) < MIN_LEARN_MS:
        return False
    if seg["speaker"] == "other":
        return True
    return seg["speaker"] == "unsure" and seg.get("score") is not None and float(seg["score"]) <= you_min - LOW_MARGIN


async def learn(db, user_id: str, ids: list[str], segs: list, *, model_id: str, you_min: float) -> dict:
    """After a recording's segments are stored: keep each learnable segment's embedding (TTL), file it under an unknown voice of
    THIS member. (Suggesting a learned voice for it is the follow-up PR.) Nothing at all while learning is off for the member."""
    out = {"stored": 0, "clustered": 0, "suggested": 0}
    if not await may_learn(db, user_id):
        return out
    now = _now()
    exp = _iso(now + timedelta(days=_days(env_int("ZOE_OMI_SEGMENT_EMBED_TTL_DAYS", EMBED_TTL_DAYS))))
    clusters = [dict(id=r[0], c=_vec(r[1]), n=int(r[2]), status=r[3], kind=r[4]) for r in await _all(
        db, "SELECT id, centroid, n_segments, status, not_person_kind FROM omi_voice_clusters WHERE user_id = ? AND model_id = ?",
        (user_id, model_id))]
    cmin = env_float("ZOE_OMI_VOICE_CLUSTER_MIN", CLUSTER_MIN)
    for sid, seg in zip(ids, segs):
        if not learnable(seg, you_min):
            continue
        e = _unit(seg["embedding"])
        best = max(clusters, key=lambda c: float(c["c"] @ e), default=None)
        if best is not None and float(best["c"] @ e) >= cmin:
            if best["status"] == "not_a_person":     # the member said "that's the TV / Zoe": mark the line, hold nothing of it
                await _run(db, "UPDATE omi_segments SET not_person = ? WHERE id = ? AND user_id = ?", (best["kind"], sid, user_id))
                await _run(db, "UPDATE omi_voice_clusters SET last_heard_at = ?, expires_at = ? WHERE id = ?", (_iso(now), exp, best["id"]))
                continue
            best["c"] = _unit(best["c"] * best["n"] + e)
            best["n"] += 1
            await _run(db, "UPDATE omi_voice_clusters SET centroid = ?, n_segments = ?, last_heard_at = ?, expires_at = ? WHERE id = ?",
                       (_blob(best["c"]), best["n"], _iso(now), exp, best["id"]))
        else:
            rows = await _all(db, "SELECT COALESCE(MAX(seq), 0) FROM omi_voice_clusters WHERE user_id = ?", (user_id,))
            best = dict(id=str(uuid.uuid4()), c=e, n=1, status="open")
            clusters.append(best)
            await _run(db, "INSERT INTO omi_voice_clusters (id, user_id, model_id, seq, centroid, n_segments, status, first_heard_at, "
                           "last_heard_at, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 1, 'open', ?, ?, ?, ?)",
                       (best["id"], user_id, model_id, int(rows[0][0] or 0) + 1, _blob(e), _iso(now), _iso(now), _iso(now), exp))
        await _run(db, "INSERT INTO omi_segment_embeddings (segment_id, user_id, model_id, embedding, expires_at) VALUES (?, ?, ?, ?, ?)",
                   (sid, user_id, model_id, _blob(e), exp))
        await _run(db, "UPDATE omi_segments SET voice_cluster_id = ? WHERE id = ? AND user_id = ?", (best["id"], sid, user_id))
        out["stored"] += 1
        out["clustered"] += 1
    await db.commit()
    return out


# --- the member's API ----------------------------------------------------------------------------------------------------
def _label(row: dict) -> str:
    if row.get("not_person"):
        return "not_a_person"
    if row.get("tagged_person_id"):
        return "person"
    spk = row.get("owner_label") or row["speaker"]
    return {"you": "you", "other": "someone_else", "unsure": "not_sure", "mixed": "not_sure"}.get(spk, "unverified")


def public_segment(row: Any) -> dict:
    d = {k: row[k] for k in _SEG_COLS}
    d["label"] = _label(d)
    d["source"] = "confirmed" if (d["owner_label"] or d["tag_source"]) else "model"
    return d


async def segments_for(db, user_id: str, rid: str) -> list[dict]:
    rows = await _all(db, f"SELECT {', '.join(_SEG_COLS)} FROM omi_segments WHERE user_id = ? AND recording_id = ? ORDER BY seq",
                      (user_id, rid))
    return [public_segment(r) for r in rows]


async def review_segments(db, user_id: str, recording_ids: list[str]) -> list[dict]:
    """THE seam for the day review: the member's segments of DONE recordings with their label and CONFIRMED person only. A
    suggestion is withheld here on purpose (guardrail 3): the review may say "Sam (you tagged)", never "Sam (suggested)". Lines
    marked not a person (Zoe's replies, TV, radio, media) are left out: they are never a candidate."""
    if not recording_ids:
        return []
    rows = await _all(db, f"SELECT s.{', s.'.join(_SEG_COLS)} FROM omi_segments s JOIN omi_recordings r ON r.id = s.recording_id "
                          f"WHERE s.user_id = ? AND r.user_id = ? AND r.status = 'done' AND s.not_person IS NULL AND s.recording_id IN ({_qs(len(recording_ids))}) "
                          "ORDER BY r.started_at, s.seq", (user_id, user_id, *recording_ids))
    out = []
    for r in rows:
        d = public_segment(r)
        d.pop("suggested_person_id")
        d.pop("suggested_score")
        d.pop("voice_cluster_id")
        out.append(d)
    return out


async def set_owner_label(db, user_id: str, segment_id: str, value: Optional[str]) -> bool:
    if value not in (None, "you", "other"):
        raise VoiceError("owner_label is you, other or null")
    n = await _run(db, "UPDATE omi_segments SET owner_label = ?, owner_labelled_at = ? WHERE id = ? AND user_id = ?",
                   (value, _iso(_now()) if value else None, segment_id, user_id))
    await db.commit()
    return bool(n)


async def list_unknown(db, user_id: str, *, samples: int = 3) -> list[dict]:
    """Unknown voices in the member's recordings: "Voice 3 - heard in 14 recordings over 3 days", with sample segment ids."""
    out = []
    for cid, seq, first, last in await _all(db, "SELECT id, seq, first_heard_at, last_heard_at FROM omi_voice_clusters "
                                                "WHERE user_id = ? AND status = 'open' ORDER BY last_heard_at DESC", (user_id,)):
        segs = await _all(db, "SELECT s.id, s.recording_id, s.start_ms, s.end_ms, r.started_at FROM omi_segments s JOIN omi_recordings r "
                              "ON r.id = s.recording_id WHERE s.user_id = ? AND s.voice_cluster_id = ? AND s.tagged_person_id IS NULL "
                              "AND r.status = 'done' ORDER BY s.end_ms - s.start_ms DESC", (user_id, cid))
        if not segs:
            continue
        recs = {s[1] for s in segs}
        days = {str(s[4])[:10] for s in segs if s[4]}
        out.append({"id": cid, "name": f"Voice {seq}", "segments": len(segs), "recordings": len(recs), "days": len(days),
                    "first_heard_at": first, "last_heard_at": last,
                    "samples": [{"segment_id": s[0], "recording_id": s[1], "start_ms": s[2], "end_ms": s[3]} for s in segs[:samples]]})
    return out


async def _resolve_person(db, user_id: str, person_id: Optional[str], name: Optional[str], ref: str) -> dict:
    import contacts_store as cs
    if person_id:
        p = await cs.own_person(db, user_id, person_id)
        if not p:
            raise VoiceError("person not found", 404)
        rel = await _all(db, "SELECT relationship FROM people WHERE id = ?", (person_id,))
        return {**p, "relationship": rel[0][0] if rel else None, "created": False}
    clean = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not clean:
        raise VoiceError("give person_id or a name")
    now = cs.now_iso()
    pid = await cs._create_person(db, user_id, clean, now=now)      # visibility 'personal': layer 2, never the household's
    await cs.add_field(db, user_id, pid, kind="name", value=clean, provenance="entered_by_member", provenance_ref=ref)
    return {"id": pid, "name": clean, "birthday": None, "relationship": None, "created": True}


async def tag(db, user_id: str, *, person_id: Optional[str] = None, name: Optional[str] = None, cluster_id: Optional[str] = None,
              segment_id: Optional[str] = None, source: str = "owner") -> dict:
    """The member says who a voice is. Every named segment gets the CONFIRMED tag; the person is the member's own (picked, or
    created ``visibility='personal'`` with a provenance-stamped name field). With learning allowed the segments' stored
    embeddings join that person's voiceprint; otherwise (flag off, member switched off, a child on either side) it is a label only.
    The stored bystander embeddings of the tagged segments are deleted either way (used)."""
    if bool(cluster_id) == bool(segment_id):
        raise VoiceError("tag a cluster or a segment")
    where, key = ("voice_cluster_id = ?", cluster_id) if cluster_id else ("id = ?", segment_id)
    segs = await _all(db, f"SELECT id, recording_id FROM omi_segments WHERE user_id = ? AND {where}", (user_id, key))
    if not segs:
        raise VoiceError("not found", 404)
    person = await _resolve_person(db, user_id, person_id, name, f"omi_tag:{key}")
    seg_ids = [s[0] for s in segs]
    now = _iso(_now())
    await _run(db, f"UPDATE omi_segments SET tagged_person_id = ?, tag_source = ?, tagged_at = ?, suggested_person_id = NULL, "
                   f"suggested_score = NULL, voice_cluster_id = NULL WHERE user_id = ? AND id IN ({_qs(len(seg_ids))})",
               (person["id"], source, now, user_id, *seg_ids))
    embs = await _all(db, f"SELECT segment_id, model_id, embedding FROM omi_segment_embeddings WHERE user_id = ? "
                          f"AND segment_id IN ({_qs(len(seg_ids))})", (user_id, *seg_ids))
    learning = "off"
    if person_may_be_child(person):
        learning = "label_only_child"
    elif await may_learn(db, user_id) and embs:
        rec_of = {s[0]: s[1] for s in segs}
        for model_id in sorted({e[1] for e in embs}):
            vp = await _voiceprint(db, user_id, person["id"], model_id, create=True)
            if not vp["learning_enabled"]:
                learning = "stopped"
                continue
            for sid, mid, blob in embs:
                if mid == model_id:
                    await _run(db, "INSERT INTO learned_voiceprint_segments (voiceprint_id, segment_id, recording_id, embedding, added_at) "
                                   "VALUES (?, ?, ?, ?, ?) ON CONFLICT (voiceprint_id, segment_id) DO NOTHING",
                               (vp["id"], sid, rec_of[sid], bytes(blob), now))
            await rebuild(db, vp["id"])
            learning = "learning"
    await _run(db, f"DELETE FROM omi_segment_embeddings WHERE user_id = ? AND segment_id IN ({_qs(len(seg_ids))})", (user_id, *seg_ids))
    if cluster_id:
        await _run(db, "DELETE FROM omi_voice_clusters WHERE id = ? AND user_id = ?", (cluster_id, user_id))
    await db.commit()
    logger.info("OMI_VOICE_TAGGED segments=%d learning=%s created_person=%s", len(seg_ids), learning, person["created"])
    return {"person_id": person["id"], "person_created": person["created"], "segments": len(seg_ids), "learning": learning}


async def mark_not_a_person(db, user_id: str, *, kind: str, cluster_id: Optional[str] = None, segment_id: Optional[str] = None) -> int:
    """"That's not a person" - Zoe's own reply, the TV, the radio, other media (Omi's ``not_a_person`` answer): the lines are marked,
    excluded from people suggestions and the day review, and their held embeddings go. On a CLUSTER the voice is never offered
    again and a later line matching it is marked too. ``kind=None`` on a segment clears the mark. Returns the lines changed."""
    if kind is not None and kind not in NOT_PERSON_KINDS:
        raise VoiceError("kind is one of " + ", ".join(NOT_PERSON_KINDS))
    if bool(cluster_id) == bool(segment_id):
        raise VoiceError("mark a cluster or a segment")
    if cluster_id:
        if kind is None or not await _run(db, "UPDATE omi_voice_clusters SET status = 'not_a_person', not_person_kind = ? WHERE id = ? "
                                              "AND user_id = ?", (kind, cluster_id, user_id)):
            raise VoiceError("not found", 404)
        where, key = "voice_cluster_id = ?", cluster_id
    else:
        where, key = "id = ?", segment_id
    await _run(db, f"DELETE FROM omi_segment_embeddings WHERE user_id = ? AND segment_id IN (SELECT id FROM omi_segments WHERE "
                   f"user_id = ? AND {where})", (user_id, user_id, key))
    n = await _run(db, f"UPDATE omi_segments SET not_person = ?, suggested_person_id = NULL, suggested_score = NULL WHERE user_id = ? "
                       f"AND {where}", (kind, user_id, key))
    await db.commit()
    if not n and segment_id:
        raise VoiceError("not found", 404)
    return n


# --- voiceprints ---------------------------------------------------------------------------------------------------------
async def _voiceprint(db, user_id: str, person_id: str, model_id: str, *, create: bool = False) -> Optional[dict]:
    q = "SELECT id, learning_enabled FROM learned_voiceprints WHERE owner_user_id = ? AND people_id = ? AND model_id = ?"
    rows = await _all(db, q, (user_id, person_id, model_id))
    if not rows and create:
        now = _now()
        await _run(db, "INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, n_segments, "
                       "n_recordings, status, learning_enabled, created_at, updated_at, expires_at) "
                       "VALUES (?, ?, ?, ?, 'pendant', ?, 0, 0, 'provisional', 1, ?, ?, ?)",
                   (str(uuid.uuid4()), user_id, person_id, model_id, _blob(np.zeros(256)), _iso(now), _iso(now),
                    _iso(now + timedelta(days=_days(env_int("ZOE_OMI_VOICEPRINT_PROVISIONAL_DAYS", PROVISIONAL_DAYS))))))
        rows = await _all(db, q, (user_id, person_id, model_id))
    return {"id": rows[0][0], "learning_enabled": bool(rows[0][1])} if rows else None


def loo_mean(E: np.ndarray, tau: float = LOO_TAU) -> tuple[Optional[np.ndarray], list[int]]:
    """Mean of unit vectors after dropping, one at a time and lowest first, a vector whose leave-one-out cosine to the rest is
    under ``tau`` (never below 2 kept). Returns (unit mean | None, dropped indices). Pure."""
    if len(E) == 0:
        return None, []
    keep, dropped = list(range(len(E))), []
    while len(keep) > 2:
        loo = [float(E[i] @ _unit(E[[j for j in keep if j != i]].mean(axis=0))) for i in keep]
        k = int(np.argmin(loo))
        if loo[k] >= tau:
            break
        dropped.append(keep.pop(k))
    return _unit(E[keep].mean(axis=0)), sorted(dropped)


async def rebuild(db, vp_id: str) -> bool:
    """Recompute a voiceprint from its contributions (un-learning). None left = the voiceprint goes. Returns True if it remains."""
    rows = await _all(db, "SELECT embedding, recording_id FROM learned_voiceprint_segments WHERE voiceprint_id = ?", (vp_id,))
    if not rows:
        await _run(db, "DELETE FROM learned_voiceprints WHERE id = ?", (vp_id,))
        return False
    vec, _dropped = loo_mean(np.stack([_unit(_vec(r[0])) for r in rows]))
    await _run(db, "UPDATE learned_voiceprints SET embedding = ?, n_segments = ?, n_recordings = ?, updated_at = ? WHERE id = ?",
               (_blob(vec), len(rows), len({r[1] for r in rows}), _iso(_now()), vp_id))
    return True


async def list_learned(db, user_id: str) -> list[dict]:
    rows = await _all(db, "SELECT v.id, v.people_id, p.name, v.status, v.n_segments, v.n_recordings, v.learning_enabled, "
                          "v.last_confirmed_at, v.expires_at, v.created_at FROM learned_voiceprints v JOIN people p ON p.id = v.people_id "
                          "WHERE v.owner_user_id = ? AND p.user_id = ? AND (p.deleted = 0 OR p.deleted IS NULL) ORDER BY p.name",
                      (user_id, user_id))
    keys = ("id", "person_id", "name", "status", "segments", "recordings", "learning_enabled", "last_confirmed_at", "expires_at",
            "created_at")
    return [{**dict(zip(keys, r)), "learning_enabled": bool(r[6])} for r in rows]


async def set_voice_learning(db, user_id: str, vp_id: str, enabled: bool) -> bool:
    n = await _run(db, "UPDATE learned_voiceprints SET learning_enabled = ?, updated_at = ? WHERE id = ? AND owner_user_id = ?",
                   (1 if enabled else 0, _iso(_now()), vp_id, user_id))
    await db.commit()
    return bool(n)


async def forget_voice(db, user_id: str, vp_id: str) -> bool:
    """"Forget this voice": the voiceprint and every contribution go, and so do the suggestions it made; the person and the
    member's tags stay (that is "forget <name>")."""
    rows = await _all(db, "SELECT people_id FROM learned_voiceprints WHERE id = ? AND owner_user_id = ?", (vp_id, user_id))
    if not rows:
        return False
    await _run(db, "DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ?", (vp_id,))
    await _run(db, "DELETE FROM learned_voiceprints WHERE id = ? AND owner_user_id = ?", (vp_id, user_id))
    await _run(db, "UPDATE omi_segments SET suggested_person_id = NULL, suggested_score = NULL WHERE user_id = ? AND suggested_person_id = ?",
               (user_id, rows[0][0]))
    await db.commit()
    return True


async def set_member_learning(db, user_id: str, enabled: bool) -> dict:
    """The member's "stop learning voices" switch. Off also lets go of every unknown voice held for them (bystander embeddings and
    open clusters) and clears pending suggestions; learned voices stay listed (each can be forgotten) but are frozen."""
    now = _iso(_now())
    if await _run(db, "UPDATE omi_voice_settings SET learning_enabled = ?, updated_at = ? WHERE user_id = ?",
                  (1 if enabled else 0, now, user_id)) == 0:
        await _run(db, "INSERT INTO omi_voice_settings (user_id, learning_enabled, updated_at) VALUES (?, ?, ?)",
                   (user_id, 1 if enabled else 0, now))
    if not enabled:
        await _run(db, "DELETE FROM omi_segment_embeddings WHERE user_id = ?", (user_id,))
        await _run(db, "UPDATE omi_segments SET suggested_person_id = NULL, suggested_score = NULL WHERE user_id = ?", (user_id,))
        await _run(db, "UPDATE omi_segments SET voice_cluster_id = NULL WHERE user_id = ? AND voice_cluster_id IN "
                       "(SELECT id FROM omi_voice_clusters WHERE user_id = ? AND status = 'open')", (user_id, user_id))
        await _run(db, "DELETE FROM omi_voice_clusters WHERE user_id = ? AND status = 'open'", (user_id,))
    await db.commit()
    return {"learning_enabled": enabled}


# --- erasure + expiry ------------------------------------------------------------------------------------------------------
async def purge_people(db, user_id: str, person_ids: Iterable[str]) -> int:
    """"Forget <name>" (and a soft-deleted person): every learned trace of those people in the member's layer - voiceprint,
    contributions, tags, suggestions. People are soft-deleted, so no FK cascade does this. RAISES (the cascade records it)."""
    ids = sorted(set(person_ids))
    if not ids:
        return 0
    ph = _qs(len(ids))
    n = 0
    for (vp,) in await _all(db, f"SELECT id FROM learned_voiceprints WHERE owner_user_id = ? AND people_id IN ({ph})", (user_id, *ids)):
        n += await _run(db, "DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ?", (vp,))
    n += await _run(db, f"DELETE FROM learned_voiceprints WHERE owner_user_id = ? AND people_id IN ({ph})", (user_id, *ids))
    n += await _run(db, f"UPDATE omi_segments SET tagged_person_id = NULL, tag_source = NULL, tagged_at = NULL "
                        f"WHERE user_id = ? AND tagged_person_id IN ({ph})", (user_id, *ids))
    n += await _run(db, f"UPDATE omi_segments SET suggested_person_id = NULL, suggested_score = NULL "
                        f"WHERE user_id = ? AND suggested_person_id IN ({ph})", (user_id, *ids))
    await db.commit()
    return n


async def drop_recording(db, rid: str) -> None:
    """A recording is deleted / tombstoned: its segments, held embeddings and every voiceprint contribution from it go, and the
    voiceprints it fed are REBUILT from what remains. A missing table (pre-0054) is nothing to drop."""
    try:
        vps = [r[0] for r in await _all(db, "SELECT DISTINCT voiceprint_id FROM learned_voiceprint_segments WHERE recording_id = ?", (rid,))]
        await _run(db, "DELETE FROM learned_voiceprint_segments WHERE recording_id = ?", (rid,))
        for vp in vps:
            await rebuild(db, vp)
        await _run(db, "DELETE FROM omi_segment_embeddings WHERE segment_id IN (SELECT id FROM omi_segments WHERE recording_id = ?)", (rid,))
        await _run(db, "DELETE FROM omi_segments WHERE recording_id = ?", (rid,))
    except Exception as exc:  # noqa: BLE001
        if not _missing(exc):
            raise


async def redact(db, user_id: str, rx: "re.Pattern[str]", mark: str) -> int:
    n = 0
    for sid, text in await _all(db, "SELECT id, text FROM omi_segments WHERE user_id = ?", (user_id,)):
        if text and rx.search(text):
            n += await _run(db, "UPDATE omi_segments SET text = ? WHERE id = ?", (rx.sub(mark, text), sid))
    return n


async def forget_user(db, user_id: str) -> int:
    """The member is erased: every row of theirs in every voice table."""
    n = 0
    try:
        for (vp,) in await _all(db, "SELECT id FROM learned_voiceprints WHERE owner_user_id = ?", (user_id,)):
            n += await _run(db, "DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ?", (vp,))
        n += await _run(db, "DELETE FROM learned_voiceprints WHERE owner_user_id = ?", (user_id,))
        for table in ("omi_segment_embeddings", "omi_segments", "omi_voice_clusters", "omi_voice_settings"):
            n += await _run(db, f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    except Exception as exc:  # noqa: BLE001
        if not _missing(exc):
            raise
    return n


async def sweep(db, *, now: Optional[datetime] = None) -> dict:
    """Runs on every transcription-loop pass WHETHER OR NOT learning is on: expired bystander embeddings and unknown voices go,
    unconfirmed (provisional) voiceprints past 30 days and confirmed ones idle past 365 go, and so does everything learned about a
    soft-deleted person (the backstop for the People panel's delete)."""
    stamp = _iso(now or _now())
    out = {"embeddings": 0, "clusters": 0, "voiceprints": 0, "people": 0}
    out["embeddings"] = await _run(db, "DELETE FROM omi_segment_embeddings WHERE expires_at < ?", (stamp,))
    gone = [r[0] for r in await _all(db, "SELECT id FROM omi_voice_clusters WHERE expires_at < ?", (stamp,))]
    for cid in gone:
        await _run(db, "UPDATE omi_segments SET voice_cluster_id = NULL WHERE voice_cluster_id = ?", (cid,))
        out["clusters"] += await _run(db, "DELETE FROM omi_voice_clusters WHERE id = ?", (cid,))
    for (vp,) in await _all(db, "SELECT id FROM learned_voiceprints WHERE expires_at < ?", (stamp,)):
        await _run(db, "DELETE FROM learned_voiceprint_segments WHERE voiceprint_id = ?", (vp,))
        out["voiceprints"] += await _run(db, "DELETE FROM learned_voiceprints WHERE id = ?", (vp,))
    for uid, pid in await _all(db, "SELECT DISTINCT s.user_id, p.id FROM people p JOIN omi_segments s ON (s.tagged_person_id = p.id OR "
                                   "s.suggested_person_id = p.id) WHERE p.deleted = 1 UNION SELECT v.owner_user_id, p.id FROM people p "
                                   "JOIN learned_voiceprints v ON v.people_id = p.id WHERE p.deleted = 1"):
        out["people"] += await purge_people(db, uid, [pid])
    await db.commit()
    if any(out.values()):
        logger.info("OMI_VOICES_SWEPT %s", " ".join(f"{k}={v}" for k, v in out.items()))
    return out
