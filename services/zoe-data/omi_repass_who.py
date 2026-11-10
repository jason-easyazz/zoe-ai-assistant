"""WHO SAID IT: the re-pass's speaker step (replaces ECAPA). Measured in the people-tagging bake-off (``~/.zoe/lab/omi-people-tagging``) on hand-checked turns: the panel's own
WeSpeaker ResNet34 (int8, the pinned artefact in ``scripts/setup/zoe_speaker_resnet34.py``) with per-channel mean removal reached 97-100 % turn accuracy; ECAPA 89-95 %; the shipped
labels 60 %. This module is the pure decision logic (testable without audio or a model) and the registry reads.

THE DESIGN
  * UNIT: VAD turns from the ASR chunks. A turn of at most ``LONG_TURN_S`` is ONE embedding; a longer one gets 1.5 s windows (hop 0.75) that VOTE (the worker cuts them; ``omi_repass.build_lines``
    votes per word). Never 20 s slabs. Embedder = the panel R34 int8; a CAM++ seam is the worker's ``DIAR`` registry (not built).
  * PER-CHANNEL MEAN REMOVAL: pendant turns (and the pendant prints) minus the mean of the pendant session's turn embeddings; panel prints minus the mean of the panel prints in the registry.
    A short session (< ``SESSION_MIN_S`` of speech) uses the stored per-device mean (``omi_channel_means``, stored with model_id and channel).
  * ONE PERSON REGISTRY shared with named guests: ``learned_voiceprints`` (a panel print and a pendant print per person, same model). A person's print is the panel print alone until the pendant print
    has ``FUSE_S`` seconds of CONFIRMED speech, then the fusion of both (the pendant print's own leave-one-out outlier rejection, cos < 0.3, is ``omi_voices.rebuild``).
  * ZOE FIRST: a turn that overlaps a Zoe reply in the panel log (exact) is Zoe; the Zoe print is the fallback. Removed before deciding between people.
  * WEARER: the prior is the device owner's print, mean-removed (the CONFIRMED pendant print, else the owner's panel profile - which can only ever LEAN: "you?"). Loudness is a tie-breaker only.
  * ABSTAIN: label only when the margin over the runner-up is >= ``MARGIN`` (``MARGIN_SHORT`` for turns under ``SHORT_S``); >= ``LEAN`` is "X?" (leaning); otherwise "?". A winner must also clear
    ``MIN_SCORE`` (a wearer turn is not "the closest registered person").
Keys: ``zoe`` | ``you`` | ``you?`` | ``p:<person>`` | ``p:<person>?`` | ``?`` | ``unverified`` (no print of anyone to decide with).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import numpy as np

from typed_env import env_float

LONG_TURN_S = 8.0
SHORT_S = 1.2
SESSION_MIN_S = 600.0
FUSE_S = 6.0
LOUD_TIEBREAK = 0.03
CHANNEL_PENDANT, CHANNEL_PANEL = "pendant", "panel"


def margin() -> float:
    return env_float("ZOE_OMI_REPASS_MARGIN", 0.10)


def margin_short() -> float:
    return env_float("ZOE_OMI_REPASS_MARGIN_SHORT", 0.20)


def lean() -> float:
    return env_float("ZOE_OMI_REPASS_LEAN", 0.05)


def min_score() -> float:
    return env_float("ZOE_OMI_REPASS_MIN_SCORE", 0.25)


def zoe_min() -> float:
    return env_float("ZOE_OMI_REPASS_ZOE_MIN", 0.55)


def unit(v: Any) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def session_mean(embs: Iterable[Any]) -> Optional[np.ndarray]:
    """The mean of unit embeddings (the channel/session offset), or None for nothing."""
    es = [unit(e) for e in embs]
    return np.mean(es, axis=0) if es else None


def remove_mean(e: Any, mu: Optional[Any]) -> np.ndarray:
    v = unit(e)
    return unit(v - np.asarray(mu, dtype=np.float64)) if mu is not None else v


def fused_print(panel_mr: Optional[np.ndarray], pendant_mr: Optional[np.ndarray], pendant_s: float) -> Optional[np.ndarray]:
    """A person's print: the panel print alone until the pendant print has FUSE_S of confirmed speech, then both (equal weight); a pendant-only person uses the pendant print."""
    if panel_mr is None:
        return pendant_mr
    if pendant_mr is None or pendant_s < FUSE_S:
        return panel_mr
    return unit(panel_mr + pendant_mr)


class Refs:
    """What a turn is matched against, already in the right channel: ``you`` (the wearer, mean-removed; ``you_panel_only`` = from the owner's panel profile, which can only lean),
    ``zoe`` (Zoe's TTS print, not mean-removed), ``people`` [{person, v (fused, mean-removed)}], ``mu`` (the pendant session/device mean applied to turns), ``loud_cut`` (the session's loud
    percentile, the wearer's tie-breaker)."""

    def __init__(self, you: Optional[Any] = None, zoe: Optional[Any] = None, people: Optional[list] = None, *, mu: Optional[Any] = None, you_panel_only: bool = False,
                 loud_cut: Optional[float] = None):
        self.you = None if you is None else unit(you)
        self.zoe = None if zoe is None else unit(zoe)
        self.people = [{"person": p["person"], "v": unit(p["v"]), "rej": int(p.get("rej", 0))} for p in (people or [])]
        self.mu = None if mu is None else np.asarray(mu, dtype=np.float64)
        self.you_panel_only = bool(you_panel_only)
        self.loud_cut = loud_cut

    def has_prints(self) -> bool:
        return self.you is not None or bool(self.people)


def rank(v: np.ndarray, refs: Refs, loud: bool = False) -> list[tuple]:
    """[(id, score)] best first; id = 'you' or a person id."""
    out = [(p["person"], float(p["v"] @ v)) for p in refs.people]
    if refs.you is not None:
        out.append(("you", float(refs.you @ v) + (LOUD_TIEBREAK if loud else 0.0)))
    return sorted(out, key=lambda t: -t[1])


def label_window(e: Any, refs: Refs, dur_s: float = 2.0, rms: Optional[float] = None, zoe_hit: bool = False) -> dict:
    """One unit's verdict. ``key`` as in the module docstring; ``conf`` = sure | lean | none; ``ranked`` = the candidates by score; ``margin`` over the runner-up."""
    out: dict = {"key": "unverified", "conf": "none", "ranked": [], "margin": None}
    if zoe_hit:
        return {**out, "key": "zoe", "conf": "sure"}
    raw = unit(e)
    if refs.zoe is not None and float(refs.zoe @ raw) >= zoe_min():
        return {**out, "key": "zoe", "conf": "sure"}
    if not refs.has_prints():
        return out
    v = remove_mean(e, refs.mu)
    ranked = rank(v, refs, loud=bool(refs.loud_cut is not None and rms is not None and rms >= refs.loud_cut))
    top_id, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    m = top - second
    need = margin_short() if dur_s < SHORT_S else margin()
    out.update(ranked=ranked, margin=round(m, 4))
    if top < min_score():
        return {**out, "key": "?", "conf": "none"}
    conf = "sure" if m >= need else "lean" if m >= lean() else "none"
    if top_id == "you" and refs.you_panel_only and conf == "sure":
        conf = "lean"                                            # the owner's PANEL print never makes a pendant turn "me" outright
    if conf == "none":
        return {**out, "key": "?", "conf": "none"}
    base = "you" if top_id == "you" else f"p:{top_id}"
    return {**out, "key": base + ("?" if conf == "lean" else ""), "conf": conf}


def key_parts(key: str) -> tuple:
    """(kind, person_id | None, leaning): kind = zoe | you | person | unknown | unverified."""
    lean_ = key.endswith("?") and key != "?"
    k = key[:-1] if lean_ else key
    if k.startswith("p:"):
        return "person", k[2:], lean_
    return {"zoe": "zoe", "you": "you", "?": "unknown", "unverified": "unverified"}.get(k, "unknown"), None, lean_


def candidates(e: Any, refs: Refs, n: int = 4) -> list[list]:
    """The 'who said this?' candidates for a unit, best first: [[id, score]] (id 'you' or a person id)."""
    if e is None or not refs.has_prints():
        return []
    return [[i, round(s, 4)] for i, s in rank(remove_mean(e, refs.mu), refs)[:n]]


# ── registry + channel means (database) ─────────────────────────────────────────────────────────────────────────────────────────────────

async def _all(db, sql: str, params: Iterable = ()) -> list:
    cur = await db.execute(sql, tuple(params))
    return list(await cur.fetchall())


def _vec(b: Any) -> np.ndarray:
    return np.frombuffer(bytes(b), dtype=np.float32).astype(np.float64)


def _blob(v: Any) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def load_mean(db, user_id: str, model_id: str, channel: str, scope: str) -> Optional[np.ndarray]:
    try:
        rows = await _all(db, "SELECT mean FROM omi_channel_means WHERE user_id = ? AND model_id = ? AND channel = ? AND scope = ?", (user_id, model_id, channel, scope))
    except Exception as exc:  # noqa: BLE001 - pre-0075
        if type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower():
            return None
        raise
    return _vec(rows[0][0]) if rows else None


async def store_mean(db, user_id: str, model_id: str, channel: str, scope: str, mean: Any, n_units: int, speech_s: float, now: Optional[datetime] = None) -> None:
    """The stored per-device mean, with model_id and channel (a short session falls back to it). Upsert."""
    stamp = _iso(now or datetime.now(timezone.utc))
    await db.execute("DELETE FROM omi_channel_means WHERE user_id = ? AND model_id = ? AND channel = ? AND scope = ?", (user_id, model_id, channel, scope))
    await db.execute("INSERT INTO omi_channel_means (user_id, model_id, channel, scope, mean, n_units, speech_s, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (user_id, model_id, channel, scope, _blob(mean), int(n_units), float(speech_s), stamp))


async def load_people(db, user_id: str, model_id: str, mu: Optional[Any], persist: bool = False) -> tuple:
    """(people for ``Refs``, the panel channel mean used). One registry: ``learned_voiceprints`` rows of this model for the member's own people, panel + pendant per person (named guests
    live in the panel channel). A person who may be a child is a label-only person and never matched (the rule everywhere else). Confirmed pendant seconds = the person's owner-tagged lines."""
    from person_age import person_may_be_child
    rows = await _all(db, "SELECT v.people_id, v.channel, v.embedding, v.rejections, p.relationship, p.birthday FROM learned_voiceprints v JOIN people p ON p.id = v.people_id "
                          "WHERE v.owner_user_id = ? AND v.model_id = ? AND v.learning_enabled = 1 AND p.user_id = ? AND (p.deleted = 0 OR p.deleted IS NULL)", (user_id, model_id, user_id))
    by: dict = {}
    for pid, ch, emb, rej, rel, bday in rows:
        if person_may_be_child({"relationship": rel, "birthday": bday}):
            continue
        d = by.setdefault(pid, {"rej": 0})
        d[ch] = unit(_vec(emb))
        d["rej"] = max(d["rej"], int(rej or 0))
    panels = [d[CHANNEL_PANEL] for d in by.values() if CHANNEL_PANEL in d]
    # The mean of the panel PRINTS stands in for the mean of the panel clips (those are not kept). With one or two prints a mean would zero the only reference (and be noise), so it needs
    # >= 3; below that the STORED registry panel mean (model_id + channel panel, scope "registry") is used, so a household that once had three keeps its offset.
    if len(panels) >= 3:
        panel_mu = session_mean(panels)
        if persist:
            try:
                await store_mean(db, user_id, model_id, CHANNEL_PANEL, "registry", panel_mu, len(panels), 0.0)
                await db.commit()
            except Exception as exc:  # noqa: BLE001 - pre-0075: nothing to store into
                if type(exc).__name__ != "UndefinedTableError" and "no such table" not in str(exc).lower():
                    raise
    else:
        panel_mu = await load_mean(db, user_id, model_id, CHANNEL_PANEL, "registry")
    secs = {}
    if by:
        for pid, s in await _all(db, "SELECT tagged_person_id, SUM(end_ms - start_ms) FROM omi_segments WHERE user_id = ? AND tagged_person_id IS NOT NULL AND tag_source IS NOT NULL "
                                     "GROUP BY tagged_person_id", (user_id,)):
            secs[pid] = float(s or 0) / 1000.0
    people = []
    for pid, d in by.items():
        pend = remove_mean(d[CHANNEL_PENDANT], mu) if CHANNEL_PENDANT in d else None
        pan = remove_mean(d[CHANNEL_PANEL], panel_mu) if CHANNEL_PANEL in d else None
        v = fused_print(pan, pend, secs.get(pid, 0.0))
        if v is not None:
            people.append({"person": pid, "v": v, "rej": d["rej"]})
    return people, panel_mu
