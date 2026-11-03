"""The struggles report (``struggle_digest`` over ``struggle_detector``): ADMIN PERSONS only.

``GET /api/system/struggles?days=7&scope=me|guest|all&top=20``
  * ``me`` (default) - the caller's own struggles, with example words from the caller's own rows;
  * ``guest``        - guest turns (categories only: guest rows hold no words);
  * ``all``          - every member + guests, COUNTS and clusters only; example words still only from the caller's own rows.
Plus the detector's counters (``stats``: queued / dropped / errors) so an operator can see it is alive and off the path.

PROPOSALS (Phase 2, ``struggle_proposals``; ``ZOE_STRUGGLE_PROPOSALS``) - the night pass's recurring problems with a candidate fix class,
COUNTS and text-free shapes only (never a household word; the router-example text is NOT served here, the Phase 3 seam
``queued_router_examples`` resolves it in-process):
  * ``GET  /api/system/struggles/proposals?status=&kind=&limit=``    list (best first) + counts by status / kind + the mode
  * ``GET  /api/system/struggles/proposals/{id}``                    one proposal with its evidence event ids
  * ``POST /api/system/struggles/proposals/{id}/accept`` ``{reason}`` router_example -> the router self-train queue; others -> a task stub
  * ``POST /api/system/struggles/proposals/{id}/reject`` ``{reason}`` suppressed for 30 days
  Accepting APPLIES NOTHING: it records the decision and the follow-up for Phase 3 / the operator.

FAILED TURNS (failure capture, ``turn_outcomes``; ``ZOE_STRUGGLE_OUTCOMES``) - approve-before-train: the caller's OWN failed turns of the
week with the proposed correct route; only a turn the caller confirms can become a router training example:
  * ``GET  /api/system/struggles/turns?days=7``                     the caller's own failed turns (their own words) + how many wait
  * ``POST /api/system/struggles/turns/{id}/confirm`` ``{label?}``   confirm the proposed route, or give the right one (a fast domain or chat)
  * ``POST /api/system/struggles/turns/{id}/reject``                 not a routing failure / not to be learned from

A panel device token authenticates a panel, not a person, so it is refused even when bound to an admin. Registered
unconditionally (read-only); with ``ZOE_STRUGGLE_DETECT=off`` it reports what is stored and ``mode: off``.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import require_admin

router = APIRouter(prefix="/api/system/struggles", tags=["struggles"])


async def _require_human_admin(user: dict = Depends(require_admin)) -> dict:
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="Sign in as the household admin to see the struggles report.")
    return user


@router.get("")
async def struggles_report(
    days: float = Query(7.0, gt=0, le=90),
    scope: str = Query("me", pattern="^(me|guest|all)$"),
    top: int = Query(20, ge=1, le=100),
    user: dict = Depends(_require_human_admin),
):
    import struggle_detector
    import struggle_digest

    viewer = str(user.get("user_id") or "")
    if not viewer:
        raise HTTPException(status_code=403, detail="no user")
    user_ids = {"me": [viewer], "guest": ["guest"], "all": None}[scope]
    try:
        report = await struggle_digest.digest(viewer_id=viewer, user_ids=user_ids, days=days, top=top)
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip is a 503, never a traceback
        raise HTTPException(status_code=503, detail=f"struggles store unavailable ({type(exc).__name__})") from exc
    report["scope"] = scope
    report["stats"] = struggle_detector.stats()
    return report


# ── Phase 2: proposals ───────────────────────────────────────────────────────

class Decision(BaseModel):
    # a CODE, never free text: a note could carry a household utterance into a proposal and back out of the API
    reason: Literal["", "not_a_problem", "wrong_label", "already_fixed", "not_now", "other"] = ""


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=f"struggle proposals store unavailable ({type(exc).__name__})")


@router.get("/proposals")
async def list_proposals(
    status: Optional[str] = Query(None, pattern="^(proposed|accepted|rejected|applied)$"),
    kind: Optional[str] = Query(None, pattern="^(router_example|intent_gap|answer_quality|stt_term)$"),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(_require_human_admin),
):
    import struggle_proposals as sp

    try:
        proposals = await sp.list_proposals(status=status, kind=kind, limit=limit)
        counts = await sp.counts()
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip is a 503, never a traceback
        raise _unavailable(exc) from exc
    return {"mode": sp.mode(), "counts": counts, "proposals": proposals}


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, user: dict = Depends(_require_human_admin)):
    import struggle_proposals as sp

    try:
        return await sp.get_proposal(proposal_id)
    except sp.NotFound as exc:
        raise HTTPException(status_code=404, detail="no such proposal") from exc
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


async def _decide(proposal_id: str, action: str, body: Decision, user: dict) -> dict:
    import struggle_proposals as sp

    try:
        return await sp.decide(proposal_id, action, str(user.get("user_id") or ""), body.reason)
    except sp.NotFound as exc:
        raise HTTPException(status_code=404, detail="no such proposal") from exc
    except sp.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _unavailable(exc) from exc


@router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(proposal_id: str, body: Decision = Body(default_factory=Decision), user: dict = Depends(_require_human_admin)):
    return await _decide(proposal_id, "accept", body, user)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, body: Decision = Body(default_factory=Decision), user: dict = Depends(_require_human_admin)):
    return await _decide(proposal_id, "reject", body, user)


# ── failure capture: the owner's label review ────────────────────────────────

class TurnLabel(BaseModel):
    # a ROUTE name from a fixed vocabulary (checked again in turn_outcomes.decide), never free text
    label: Optional[Literal["weather", "time", "timers", "lists", "calendar", "smart_home", "music", "reminders", "chat"]] = None


@router.get("/turns")
async def failed_turns(days: float = Query(7.0, gt=0, le=90), user: dict = Depends(_require_human_admin)):
    import turn_outcomes

    try:
        return await turn_outcomes.review(str(user.get("user_id") or ""), days=days)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"failed-turn store unavailable ({type(exc).__name__})") from exc


async def _label(row_id: str, action: str, label: Optional[str], user: dict) -> dict:
    import turn_outcomes

    try:
        return await turn_outcomes.decide(row_id, str(user.get("user_id") or ""), action, label)
    except turn_outcomes.ReviewError as exc:
        raise HTTPException(status_code=404 if "no such" in str(exc) else 409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"failed-turn store unavailable ({type(exc).__name__})") from exc


@router.post("/turns/{row_id}/confirm")
async def confirm_turn(row_id: str, body: TurnLabel = Body(default_factory=TurnLabel), user: dict = Depends(_require_human_admin)):
    return await _label(row_id, "confirm", body.label, user)


@router.post("/turns/{row_id}/reject")
async def reject_turn(row_id: str, user: dict = Depends(_require_human_admin)):
    return await _label(row_id, "reject", None, user)
