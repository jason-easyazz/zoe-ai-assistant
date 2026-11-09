"""The owner's BACKFILL of a named guest from saved panel clips (``scripts/maintenance/named_guest_backfill.py``; runbook
``docs/knowledge/named-guests.md`` "Backfill from saved clips").

For a guest who already talked to the panel before named guests existed: their turns are in the replay corpus
(``ZOE_VOICE_SAVE_AUDIO``) with no verdict sidecar, so the "who was that?" naming flow cannot reach them. The owner picks the clips;
this builds the guest's panel print from them and stores it through the SAME write as the panel's own enrolment
(``named_guest_enrol.store_print``): the member's own person, ``channel='panel'``, guest scope, the same refusals. No second registry.

Each clip is embedded with the panel's own pinned ResNet34 (``scripts/setup/zoe_speaker_resnet34.py``, loaded by path as Omi does:
same front end, centring and self-test; the whole clip, as the daemon embeds a live turn), so the print lives in the space the server
scores live claims in. Per clip the verdict is one of:

  ``kept``        used for the print
  ``unreadable``  not a 16-bit PCM WAV / no embedding
  ``too_short``   under ``MIN_CLIP_S`` (the enrolment run's own floor for a usable capture)
  ``member``      within the FAR-1 % bar (for its length) of a consented MEMBER profile: the owner's own voice never becomes the guest
  ``outlier``     dropped by the enrolment recipe's leave-one-out rule (``build_profile``: a clip that does not sound like the rest)

Fewer than ``min_kept`` kept clips stores nothing. ``dry_run`` scores and reports and writes nothing. Each kept clip also reports
its leave-one-out score against the print built from the OTHER kept clips beside the live claim bar for its length: a direct
preview of whether a live turn like it would clear the gate. Paths and names never reach the log (counts and scores only).
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

import speaker_models

logger = logging.getLogger(__name__)

MIN_CLIP_S = 1.2           # = zoe_voice_enroll_mode.MIN_TOTAL_S: shorter is a clipped or empty capture
DEFAULT_MIN_KEPT = 3       # = zoe_speaker_resnet34.ENROLL_MIN_KEEP: the enrolment recipe's own minimum


@dataclass
class Clip:
    path: str
    verdict: str = "kept"
    seconds: Optional[float] = None
    member_score: Optional[float] = None     # best cosine to any consented member profile
    member_bar: Optional[float] = None       # the FAR-1 % bar for this clip's length
    loo_score: Optional[float] = None        # kept clips: cosine to the print built from the OTHER kept clips
    live_bar: Optional[float] = None         # kept clips: the live wake-turn claim bar for this clip's length
    vec: Any = field(default=None, repr=False)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("vec", None)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64).reshape(-1)
    return (a / max(float(np.linalg.norm(a)), 1e-12)).astype(np.float32)


def _module():
    import omi_diarize

    return omi_diarize._embedder_module()


def embed(paths: Sequence[str], embedder) -> list[Clip]:
    """One ``Clip`` per path, embedded whole with the panel's model (``embedder.embed_wav``). Never raises for a bad clip."""
    out = []
    for p in paths:
        c = Clip(path=str(p))
        try:
            vec, seconds = embedder.embed_wav(Path(p).read_bytes())
            c.seconds = float(seconds)
            c.vec = None if vec is None else _unit(vec)
        except Exception:  # noqa: BLE001 - not a 16-bit PCM WAV, missing, unreadable
            c.verdict = "unreadable"
        if c.verdict == "kept" and c.vec is None:
            c.verdict = "unreadable"
        out.append(c)
    return out


def assess(clips: list[Clip], members: Sequence[np.ndarray], *, min_kept: int = DEFAULT_MIN_KEPT,
           build_profile: Optional[Callable] = None) -> tuple[list[Clip], Optional[np.ndarray]]:
    """Judge each clip, then build the print from the survivors with the enrolment recipe. Pure apart from loading the recipe."""
    build_profile = build_profile or _module().build_profile
    far1 = speaker_models.RESNET34_THRESHOLDS["far1"]
    mids = [_unit(m) for m in members if np.asarray(m).size == 256]
    for c in clips:
        if c.verdict != "kept":
            continue
        group = speaker_models.length_group(c.seconds)
        c.member_bar = far1["short" if group == "short" else "long"]
        c.member_score = max((float(m @ c.vec) for m in mids), default=None)
        if c.seconds is not None and c.seconds < MIN_CLIP_S:
            c.verdict = "too_short"
        elif c.member_score is not None and c.member_score >= c.member_bar:
            c.verdict = "member"
    live = [c for c in clips if c.verdict == "kept"]
    if not live:
        return clips, None
    built = build_profile([c.vec for c in live])
    for i in getattr(built, "dropped", []) or []:
        live[i].verdict = "outlier"
    kept = [c for c in live if c.verdict == "kept"]
    for i, c in enumerate(kept):
        rest = [k.vec for j, k in enumerate(kept) if j != i]
        c.loo_score = float(_unit(np.mean(np.stack(rest), axis=0)) @ c.vec) if rest else None
        c.live_bar = speaker_models.claim_threshold(speaker_models.acting_model(), c.seconds)
    if len(kept) < max(1, int(min_kept)) or not getattr(built, "ok", False):
        return clips, None
    return clips, _unit(built.vector)


async def member_profiles(model_id: str) -> list[np.ndarray]:
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        async with db.execute("SELECT embedding_blob FROM speaker_profiles WHERE consent_at IS NOT NULL AND "
                              + speaker_models.MODEL_SQL + " = ?", (model_id,)) as cur:
            rows = await cur.fetchall()
    return [np.frombuffer(bytes(r[0]), dtype="<f4").astype(np.float32) for r in rows if r[0]]


async def run(owner: str, name: str, paths: Sequence[str], *, embedder, dry_run: bool = True,
              min_kept: int = DEFAULT_MIN_KEPT) -> dict:
    """The whole backfill. Returns the report (``stored`` true only when a print was written). Raises ``GuestError`` for a refusal
    of the write itself (flag off, not an adult member, a member's name or voice, a ledger outage)."""
    import enrollment_state as es
    import named_guest_enrol as nge
    from named_guests import mode

    model_id = speaker_models.acting_model()
    clean = nge._clean_name(name)
    if not clean:
        raise nge.GuestError("that is not a plain name", 400)
    clips, vec = assess(embed(paths, embedder), await member_profiles(model_id), min_kept=min_kept)
    counts: dict = {}
    for c in clips:
        counts[c.verdict] = counts.get(c.verdict, 0) + 1
    report = dict(name=clean, model_id=model_id, dry_run=dry_run, min_kept=min_kept, counts=counts,
                  clips=[c.public() for c in clips], enough=vec is not None, stored=False)
    logger.info("NAMED_GUEST_BACKFILL dry_run=%s clips=%d %s enough=%s", dry_run, len(clips),
                " ".join(f"{k}={v}" for k, v in sorted(counts.items())), vec is not None)
    if dry_run or vec is None:
        return report
    if mode() == "off":
        raise nge.GuestError("named guests are off (ZOE_NAMED_GUESTS): nothing was stored", 404)
    if await es.audience_of(owner) != "adult":
        raise nge.GuestError("the owner must be an adult member: nothing was stored", 403)
    import memory_forgotten as mf

    try:
        forgotten = await mf.matches(owner, clean, strict=True)    # the owner naming a forgotten name here is an explicit re-teach
    except Exception as exc:  # noqa: BLE001
        raise nge.GuestError("the forget ledger is unavailable; nothing was stored", 503) from exc
    out = await nge.store_print(owner, clean, vec, model_id, sample_count=counts.get("kept", 0), forgotten_at_issue=forgotten,
                                ref="named_guest_backfill")
    report.update(stored=True, guest_id=out["guest_id"])
    return report
