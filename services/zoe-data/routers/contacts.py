"""Contacts card API (flag-dark ``ZOE_CONTACTS_API``, default OFF; mounted via ``register(app)``, off = 404).

The contact card of one of the CALLER's own people (layer 2 of the identity design, section 5.5): multi-value fields with their
provenance, dates, relationships, links to synced source contacts; a vCard 4.0 export; a .vcf import that runs the merge model; and
the merge-review queue. Store: ``../contacts_store.py``; migration 0052; design ``docs/architecture/contacts-data-model.md``.

    GET    /api/contacts/people/{person_id}           the card (404 for anyone else's person, family-visible or not)
    GET    /api/contacts/people/{person_id}/vcard     text/vcard; charset=utf-8
    POST   /api/contacts/people/{person_id}/fields    {kind, value, parts?, types?, pref?, label?} - a member-entered field
    DELETE /api/contacts/fields/{field_id}            the member's own, non-read-only field
    POST   /api/contacts/import?provider=phone        raw .vcf body -> per-action counts + review ids
    GET    /api/contacts/reviews?status=open          the member's merge reviews
    POST   /api/contacts/reviews/{review_id}          {action: link|new|dismiss, person_id?}

Every route is a signed-in, non-guest HUMAN member's own data: a device token (a panel) is refused, and admin never widens it.
The legacy ``/api/people`` routes are unchanged (back-compat). No sync engine, no connector, no token here.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

import contacts_store
from auth import require_signed_in
from database import get_db
from guest_policy import require_feature_access

logger = logging.getLogger(__name__)

FLAG = "ZOE_CONTACTS_API"
MAX_IMPORT_BYTES = 5 * 1024 * 1024

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


def enabled() -> bool:
    return os.environ.get("ZOE_CONTACTS_API", "").strip().lower() in ("1", "true", "yes", "on")


async def _member(user: dict = Depends(require_signed_in)) -> dict:
    """A member PERSON: contact cards are private knowledge, and a device token authenticates a panel, not a person."""
    if "device_token_role" in user or str(user.get("username", "")).startswith("panel:"):
        raise HTTPException(status_code=403, detail="sign in as a household member to see your contacts")
    return user


def _fail(exc: contacts_store.ContactsError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/people/{person_id}")
async def get_card(person_id: str, user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="read")
    card = await contacts_store.card_for_person(db, user["user_id"], person_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return card


@router.get("/people/{person_id}/vcard")
async def get_vcard(person_id: str, user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="read")
    text = await contacts_store.export_vcard(db, user["user_id"], person_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return Response(content=text, media_type="text/vcard; charset=utf-8")


@router.post("/people/{person_id}/fields")
async def add_field(person_id: str, body: dict, user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="update")
    try:
        return await contacts_store.add_field(
            db, user["user_id"], person_id, kind=str(body.get("kind") or ""), value=str(body.get("value") or ""),
            parts=body.get("parts") or {}, types=body.get("types") or [], pref=body.get("pref"), label=body.get("label"))
    except contacts_store.ContactsError as exc:
        _fail(exc)


@router.delete("/fields/{field_id}")
async def remove_field(field_id: str, user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="update")
    try:
        await contacts_store.remove_field(db, user["user_id"], field_id)
    except contacts_store.ContactsError as exc:
        _fail(exc)
    return {"ok": True}


@router.post("/import")
async def import_vcf(request: Request, provider: str = Query("phone"), user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="create")
    raw = await request.body()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="vCard file too large")
    if provider not in ("phone", "local", "carddav", "google", "microsoft"):
        raise HTTPException(status_code=400, detail="provider must be phone, local, carddav, google or microsoft")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="vCard must be UTF-8")
    try:
        return await contacts_store.import_vcf(db, user["user_id"], text, provider=provider)
    except contacts_store.ContactsError as exc:
        _fail(exc)


@router.get("/reviews")
async def list_reviews(status: str = Query("open"), user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="read")
    if status not in ("open", "linked", "kept_separate", "dismissed"):
        raise HTTPException(status_code=400, detail="unknown status")
    return {"reviews": await contacts_store.list_reviews(db, user["user_id"], status)}


@router.post("/reviews/{review_id}")
async def resolve_review(review_id: str, body: dict, user: dict = Depends(_member), db=Depends(get_db)):
    await require_feature_access(db, user, feature="people", action="update")
    try:
        return await contacts_store.resolve_review(db, user["user_id"], review_id, str(body.get("action") or ""),
                                                   person_id=body.get("person_id"))
    except contacts_store.ContactsError as exc:
        _fail(exc)


def register(app: FastAPI) -> bool:
    """Mount the router iff ``ZOE_CONTACTS_API`` is on. Flag off = the routes are ABSENT (404), not merely refusing."""
    if not enabled():
        return False
    app.include_router(router)
    logger.info("Contacts router registered (ZOE_CONTACTS_API on)")
    return True
