"""Orbit — Who's in your orbit tonight?
FastAPI app: REST routes + WebSocket handlers.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import asyncio
import time

from database import get_db, init_db, parse_json_fields, row_to_dict
from icebreaker_engine import generate as make_icebreaker, get_connection_icebreaker
from match_engine import score_pair, top_matches

from models import (
    ChallengeAnswerCreate,
    ChallengeCreate,
    CheckInCreate,
    ConnectionCreate,
    ContactExchangeCreate,
    InteractionCreate,
    SafetyEventCreate,
    SessionCreate,
    SpeedDatingReact,
    SpeedDatingStart,
)
from session_manager import manager as ws_manager

log = logging.getLogger("orbit")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Orbit", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    await init_db()
    log.info("Orbit ready 🚀")


# ── Static pages ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "checkin.html")


@app.get("/checkin")
async def checkin_page():
    return FileResponse(STATIC_DIR / "checkin.html")


@app.get("/profile")
async def profile_page():
    return FileResponse(STATIC_DIR / "profile.html")


@app.get("/discover")
async def discover_page():
    return FileResponse(STATIC_DIR / "discover.html")


@app.get("/connect/{checkin_id}")
async def connect_page(checkin_id: str):
    return FileResponse(STATIC_DIR / "connect.html")


@app.get("/host")
async def host_page():
    return FileResponse(STATIC_DIR / "host.html")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "module": "orbit"}


# ── Sessions ──────────────────────────────────────────────────────────────────

def _gen_join_code() -> str:
    """Generate a memorable 4-character uppercase join code."""
    import random
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
    return "".join(random.choices(chars, k=4))


@app.post("/api/sessions")
async def create_session(body: SessionCreate):
    session_id = new_id()
    ts = now_iso()
    join_code = _gen_join_code()
    async with get_db() as db:
        # Ensure join code is unique among active sessions
        for _ in range(5):
            row = await db.execute("SELECT id FROM sessions WHERE join_code=? AND active=1", (join_code,))
            if not await row.fetchone():
                break
            join_code = _gen_join_code()
        await db.execute(
            "INSERT INTO sessions (id, venue_name, event_name, zone_names, join_code, created_at) VALUES (?,?,?,?,?,?)",
            (session_id, body.venue_name, body.event_name, "{}", join_code, ts),
        )
        await db.commit()
    return {"id": session_id, "venue_name": body.venue_name, "join_code": join_code, "created_at": ts}


@app.get("/api/sessions/by-code/{code}")
async def get_session_by_code(code: str):
    """Look up an active session by its short join code."""
    clean = code.upper().strip()
    async with get_db() as db:
        row = await db.execute(
            "SELECT * FROM sessions WHERE join_code=? AND active=1 ORDER BY created_at DESC LIMIT 1",
            (clean,)
        )
        r = await row.fetchone()
    if not r:
        raise HTTPException(404, "No active session found for that code")
    d = row_to_dict(r)
    d["zone_names"] = json.loads(d.get("zone_names") or "{}")
    return d


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    async with get_db() as db:
        row = await db.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
        r = await row.fetchone()
    if not r:
        raise HTTPException(404, "Session not found")
    d = row_to_dict(r)
    d["zone_names"] = json.loads(d.get("zone_names") or "{}")
    return d


@app.patch("/api/sessions/{session_id}/zones")
async def update_zones(session_id: str, zones: dict[str, str]):
    async with get_db() as db:
        await db.execute(
            "UPDATE sessions SET zone_names=? WHERE id=?",
            (json.dumps(zones), session_id),
        )
        await db.commit()
    await ws_manager.broadcast_session_players(session_id, {
        "type": "zones_updated",
        "data": {"zones": zones},
    })
    return {"ok": True}


@app.post("/api/sessions/{session_id}/end")
async def end_session(session_id: str):
    async with get_db() as db:
        await db.execute("UPDATE sessions SET active=0 WHERE id=?", (session_id,))
        await db.execute(
            "UPDATE checkins SET checked_out=1 WHERE session_id=? AND checked_out=0",
            (session_id,),
        )
        await db.commit()
    await ws_manager.broadcast_session_players(session_id, {"type": "session_ended", "data": {}})
    await ws_manager.broadcast_host(session_id, {"type": "session_ended", "data": {}})
    return {"ok": True}


@app.post("/api/sessions/{session_id}/last-orders")
async def last_orders(session_id: str):
    msg = {"type": "last_orders", "data": {"message": "Last orders — the night's wrapping up. Anyone you want to stay in touch with?"}}
    await ws_manager.broadcast_session_players(session_id, msg)
    await ws_manager.broadcast_host(session_id, {"type": "last_orders_triggered", "data": {}})
    return {"ok": True}


# ── Check-ins ─────────────────────────────────────────────────────────────────

@app.post("/api/checkins")
async def create_checkin(body: CheckInCreate):
    checkin_id = new_id()
    ts = now_iso()

    personality_json = (
        json.dumps(body.personality.model_dump()) if body.personality else None
    )

    async with get_db() as db:
        # Validate session exists and is active
        row = await (await db.execute(
            "SELECT id FROM sessions WHERE id=? AND active=1", (body.session_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Session not found or ended")

        # Derive legacy intent from intents list for backward compat
        primary_intent = body.intent or (body.intents[0] if body.intents else "social")
        await db.execute(
            """INSERT INTO checkins
               (id, session_id, display_name, intent, intents, desires, visibility,
                interests, interest_intensity, top_values, personality,
                activities, group_size, zone, created_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                checkin_id, body.session_id, body.display_name, primary_intent,
                json.dumps(body.intents),
                json.dumps(body.desires),
                body.visibility,
                json.dumps(body.interests),
                json.dumps(body.interest_intensity),
                json.dumps(body.values),
                personality_json,
                json.dumps(body.activities),
                body.group_size,
                body.zone,
                ts, ts,
            ),
        )
        await db.commit()

    await _broadcast_stats(body.session_id)
    await ws_manager.broadcast_host(body.session_id, {
        "type": "new_checkin",
        "data": {
            "checkin_id": checkin_id,
            "display_name": body.display_name,
            "intent": body.intent,
            "zone": body.zone,
            "group_size": body.group_size,
        },
    })
    return {"id": checkin_id, "created_at": ts}


@app.get("/api/checkins/{checkin_id}/matches")
async def get_matches(checkin_id: str):
    async with get_db() as db:
        me_row = await (await db.execute(
            "SELECT * FROM checkins WHERE id=? AND checked_out=0", (checkin_id,)
        )).fetchone()
        if not me_row:
            raise HTTPException(404, "Check-in not found or already checked out")
        me = _parse_checkin(row_to_dict(me_row))

        # Get all active checkins in same session
        rows = await (await db.execute(
            "SELECT * FROM checkins WHERE session_id=? AND checked_out=0",
            (me["session_id"],),
        )).fetchall()
        pool = [_parse_checkin(row_to_dict(r)) for r in rows]

        # Get block list
        blocked = await (await db.execute(
            """SELECT reported_id FROM safety_events
               WHERE reporter_id=? AND type='block'""",
            (checkin_id,),
        )).fetchall()
        blocked_ids = {r[0] for r in blocked}

        # Filter out blocked
        pool = [p for p in pool if p["id"] not in blocked_ids]
        me["blocked"] = list(blocked_ids)

        best = top_matches(me, pool, n=3)

    result = []
    for candidate, score in best:
        icebreaker = make_icebreaker(me, candidate)
        met_by_me = checkin_id in candidate.get("met_by", [])
        met_by_them = candidate["id"] in me.get("met_by", [])
        result.append({
            "match_id": f"{checkin_id}:{candidate['id']}",
            "person": {
                "id": candidate["id"],
                "display_name": candidate["display_name"],
                "intent": candidate["intent"],
                "interests": candidate["interests"],
                "interest_intensity": candidate["interest_intensity"],
                "values": candidate["values"],
                "activities": candidate["activities"],
                "group_size": candidate["group_size"],
                "zone": candidate["zone"],
            },
            "score": score,
            "icebreaker": icebreaker,
            "met_by_me": met_by_them,    # I pressed "we met" on them
            "met_by_them": met_by_me,    # They pressed "we met" on me
        })
    return {"matches": result}


@app.post("/api/checkins/{checkin_id}/checkout")
async def checkout(checkin_id: str):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT session_id FROM checkins WHERE id=?", (checkin_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Check-in not found")
        session_id = row[0]
        await db.execute(
            "UPDATE checkins SET checked_out=1 WHERE id=?", (checkin_id,)
        )
        await db.commit()

    ws_manager.unregister_player(checkin_id)
    await _broadcast_stats(session_id)
    return {"ok": True}


@app.post("/api/checkins/{checkin_id}/report")
async def report_user(checkin_id: str, body: SafetyEventCreate):
    event_id = new_id()
    ts = now_iso()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT session_id FROM checkins WHERE id=?", (checkin_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404)
        session_id = row[0]
        await db.execute(
            """INSERT INTO safety_events
               (id, session_id, reporter_id, reported_id, type, reason, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (event_id, session_id, checkin_id, body.reported_id, body.type, body.reason, ts),
        )
        await db.commit()
    if body.type == "report":
        await ws_manager.broadcast_host(session_id, {
            "type": "safety_report",
            "data": {"report_id": event_id, "reason": body.reason},
        })
    return {"ok": True}


# ── Matches (confirm met) ─────────────────────────────────────────────────────

@app.post("/api/matches/confirm-met")
async def confirm_met(checkin_id: str, other_id: str):
    """Record that checkin_id met other_id in real life."""
    async with get_db() as db:
        me_row = await (await db.execute(
            "SELECT met_by, session_id FROM checkins WHERE id=?", (checkin_id,)
        )).fetchone()
        if not me_row:
            raise HTTPException(404)
        session_id = me_row["session_id"]

        # Add other_id to my met_by list (means I confirmed I met them)
        met_by = json.loads(me_row["met_by"] or "[]")
        if other_id not in met_by:
            met_by.append(other_id)
        await db.execute(
            "UPDATE checkins SET met_by=? WHERE id=?",
            (json.dumps(met_by), checkin_id),
        )
        await db.commit()

        # Check if mutual
        other_row = await (await db.execute(
            "SELECT met_by FROM checkins WHERE id=?", (other_id,)
        )).fetchone()
        if other_row:
            other_met_by = json.loads(other_row["met_by"] or "[]")
            if checkin_id in other_met_by:
                # Mutual meet confirmed — notify both
                await ws_manager.send_player(checkin_id, {
                    "type": "mutual_met",
                    "data": {"other_id": other_id},
                })
                await ws_manager.send_player(other_id, {
                    "type": "mutual_met",
                    "data": {"other_id": checkin_id},
                })
                await ws_manager.broadcast_host(session_id, {
                    "type": "meet_confirmed",
                    "data": {"id_1": checkin_id, "id_2": other_id},
                })

    await _broadcast_stats(session_id)
    return {"ok": True}


# ── Interactions ──────────────────────────────────────────────────────────────

@app.post("/api/interactions")
async def send_interaction(body: InteractionCreate):
    interaction_id = new_id()
    ts = now_iso()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO interactions
               (id, session_id, sender_id, receiver_id, type, payload, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                interaction_id, body.session_id, body.sender_id, body.receiver_id,
                body.type, json.dumps(body.payload or {}), ts,
            ),
        )
        await db.commit()

    # Get sender's display name for notification
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT display_name, zone FROM checkins WHERE id=?", (body.sender_id,)
        )).fetchone()
    sender_name = row["display_name"] if row else "Someone"
    zone = row["zone"] if row else None

    type_labels = {
        "say_hey": "wants to say hey 👋",
        "join_drink": "wants you to join them for a drink 🍻",
        "join_activity": f"invited you to join them{(' — ' + body.payload.get('activity', '')) if body.payload and body.payload.get('activity') else ''} 🎯",
    }
    label = type_labels.get(body.type, "sent you a nudge")

    await ws_manager.send_player(body.receiver_id, {
        "type": "interaction_received",
        "data": {
            "interaction_id": interaction_id,
            "from_id": body.sender_id,
            "from_name": sender_name,
            "type": body.type,
            "label": label,
            "zone": zone,
        },
    })
    return {"id": interaction_id}


# ── QR Connections ────────────────────────────────────────────────────────────

@app.get("/api/connect/{checkin_id}")
async def get_connect_info(checkin_id: str, s: str, scanner_id: Optional[str] = None):
    """QR scan landing — returns scanned person's public profile + compatibility."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM checkins WHERE id=? AND session_id=? AND checked_out=0",
            (checkin_id, s),
        )).fetchone()
        if not row:
            raise HTTPException(404, "Person not found or no longer in orbit")
        them = _parse_checkin(row_to_dict(row))

        # Fetch scanner for compatibility + desires intersection
        scanner = None
        if scanner_id:
            sr = await (await db.execute(
                "SELECT * FROM checkins WHERE id=? AND checked_out=0",
                (scanner_id,),
            )).fetchone()
            if sr:
                scanner = _parse_checkin(row_to_dict(sr))

    # Compute compatibility
    compatibility = None
    if scanner:
        compatibility = _compute_compatibility(scanner, them)

    return {
        "id": them["id"],
        "display_name": them["display_name"],
        "intent": them["intent"],
        "intents": them.get("intents", []),
        "interests": them["interests"],
        "interest_intensity": them.get("interest_intensity", {}),
        "values": them.get("values") or them.get("top_values") or [],
        "activities": them["activities"],
        "zone": them["zone"],
        "group_size": them.get("group_size", 1),
        "compatibility": compatibility,
    }


def _compute_compatibility(me: dict, them: dict) -> dict:
    """Compute compatibility score and shared traits between two check-ins."""
    my_interests = set(i.lower() for i in (me.get("interests") or []))
    their_interests = set(i.lower() for i in (them.get("interests") or []))
    my_values = set(me.get("values") or [])
    their_values = set(them.get("values") or [])
    my_acts = set(a.lower() for a in (me.get("activities") or []))
    their_acts = set(a.lower() for a in (them.get("activities") or []))
    my_intents = set(me.get("intents") or [me.get("intent", "")])
    their_intents = set(them.get("intents") or [them.get("intent", "")])

    shared_interests = list(my_interests & their_interests)
    shared_values = list(my_values & their_values)
    shared_activities = list(my_acts & their_acts)
    intent_match = bool(my_intents & their_intents)

    # Weighted score (0-100)
    score = 0
    if intent_match:
        score += 35
    score += min(30, len(shared_interests) * 8)
    score += min(20, len(shared_values) * 7)
    score += min(10, len(shared_activities) * 5)

    # Personality bonus
    personality_note = None
    my_p = me.get("personality")
    their_p = them.get("personality")
    if isinstance(my_p, dict) and isinstance(their_p, dict):
        delta = sum(abs(my_p.get(k, 3) - their_p.get(k, 3)) for k in "OCEAN")
        if delta < 4:
            personality_note = "Very similar energy"
            score += 5
        elif delta < 8:
            personality_note = "Complementary vibes"
            score += 3

    # Desires intersection (private — only shared desires returned)
    my_desires = set(me.get("desires") or [])
    their_desires = set(them.get("desires") or [])
    shared_desires = list(my_desires & their_desires)

    # Build human-readable bullets
    bullets = []
    if intent_match and (my_intents & their_intents):
        shared_label = list(my_intents & their_intents)[0]
        bullets.append(f"Both here for: {shared_label}")
    for i in shared_interests[:2]:
        bullets.append(f"Both into {i}")
    for v in shared_values[:2]:
        bullets.append(f"Both value {v.capitalize()}")
    for a in shared_activities[:1]:
        bullets.append(f"Both up for {a}")

    return {
        "score": min(100, score),
        "bullets": bullets,
        "shared_interests": shared_interests,
        "shared_values": shared_values,
        "intent_match": intent_match,
        "personality_note": personality_note,
        "shared_desires": shared_desires,  # private — only the intersection
    }


@app.post("/api/connections")
async def create_connection(body: ConnectionCreate):
    """Scanner sends a connection request. Checks for mutual scan."""
    conn_id = new_id()
    ts = now_iso()

    async with get_db() as db:
        # Check for existing reverse connection (mutual scan detection)
        existing = await (await db.execute(
            """SELECT id FROM connections
               WHERE session_id=? AND scanner_id=? AND scanned_id=?
               AND status='pending'""",
            (body.session_id, body.scanned_id, body.scanner_id),
        )).fetchone()

        is_mutual = existing is not None

        if is_mutual:
            # Mark the reverse connection as mutual too
            await db.execute(
                "UPDATE connections SET status='mutual', is_mutual=1, responded_at=? WHERE id=?",
                (ts, existing["id"]),
            )
            status = "mutual"
        else:
            status = "pending"

        await db.execute(
            """INSERT INTO connections
               (id, session_id, scanner_id, scanned_id, status, is_mutual, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (conn_id, body.session_id, body.scanner_id, body.scanned_id,
             status, 1 if is_mutual else 0, ts),
        )
        await db.commit()

        # Get names for notifications
        scanner_row = await (await db.execute(
            "SELECT display_name, interests, top_values FROM checkins WHERE id=?",
            (body.scanner_id,),
        )).fetchone()
        scanned_row = await (await db.execute(
            "SELECT display_name, interests, top_values FROM checkins WHERE id=?",
            (body.scanned_id,),
        )).fetchone()

    if is_mutual and scanner_row and scanned_row:
        scanner = _parse_checkin(row_to_dict(scanner_row)) if scanner_row else {}
        scanned = _parse_checkin(row_to_dict(scanned_row)) if scanned_row else {}
        icebreaker = get_connection_icebreaker(scanner, scanned)

        await ws_manager.send_player(body.scanner_id, {
            "type": "mutual_scan",
            "data": {
                "connection_id": conn_id,
                "name": scanned_row["display_name"],
                "icebreaker": icebreaker,
            },
        })
        await ws_manager.send_player(body.scanned_id, {
            "type": "mutual_scan",
            "data": {
                "connection_id": existing["id"],
                "name": scanner_row["display_name"],
                "icebreaker": icebreaker,
            },
        })
    else:
        # Quiet badge — no popup
        await ws_manager.send_player(body.scanned_id, {
            "type": "connection_request",
            "data": {"connection_id": conn_id},
        })

    # Check if speed dating is active — inject timer info
    speed_dating_info = None
    async with get_db() as db:
        sd_row = await (await db.execute(
            "SELECT round_duration_seconds FROM speed_dating_sessions WHERE session_id=? AND active=1 LIMIT 1",
            (body.session_id,),
        )).fetchone()
        if sd_row:
            ends_at = time.time() + sd_row["round_duration_seconds"]
            # Get scanner name for the push to the scanned person
            scanner_name_row = await (await db.execute(
                "SELECT display_name FROM checkins WHERE id=?", (body.scanner_id,)
            )).fetchone()
            scanner_name = scanner_name_row["display_name"] if scanner_name_row else "Someone"
            speed_dating_info = {
                "duration_seconds": sd_row["round_duration_seconds"],
                "ends_at": ends_at,
                "partner_name": scanner_name,
            }
            # Push timer to the scanned person too
            await ws_manager.send_player(body.scanned_id, {
                "type": "speed_dating_paired",
                "data": {
                    "partner_name": scanner_name,
                    "ends_at": ends_at,
                    "duration_seconds": sd_row["round_duration_seconds"],
                },
            })

    return {"id": conn_id, "is_mutual": is_mutual, "speed_dating": speed_dating_info}


@app.get("/api/connections/{checkin_id}/pending")
async def get_pending_connections(checkin_id: str):
    """Get all pending connection requests for a user."""
    async with get_db() as db:
        rows = await (await db.execute(
            """SELECT c.*, ch.display_name, ch.intent, ch.interests, ch.top_values, ch.zone
               FROM connections c
               JOIN checkins ch ON ch.id = c.scanner_id
               WHERE c.scanned_id=? AND c.status='pending'
               ORDER BY c.created_at DESC""",
            (checkin_id,),
        )).fetchall()
    result = []
    for r in rows:
        d = row_to_dict(r)
        d = parse_json_fields(d, ["interests", "top_values"])
        result.append(d)
    return {"connections": result}


@app.patch("/api/connections/{conn_id}")
async def respond_to_connection(conn_id: str, checkin_id: str, action: str):
    """Accept or decline a pending connection request."""
    if action not in ("accept", "decline"):
        raise HTTPException(400, "action must be 'accept' or 'decline'")
    ts = now_iso()
    status = "accepted" if action == "accept" else "declined"

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM connections WHERE id=? AND scanned_id=?",
            (conn_id, checkin_id),
        )).fetchone()
        if not row:
            raise HTTPException(404)

        await db.execute(
            "UPDATE connections SET status=?, responded_at=? WHERE id=?",
            (status, ts, conn_id),
        )
        await db.commit()

        if action == "accept":
            scanner_row = await (await db.execute(
                "SELECT display_name, interests, values FROM checkins WHERE id=?",
                (row["scanner_id"],),
            )).fetchone()
            scanned_row = await (await db.execute(
                "SELECT display_name, interests, values FROM checkins WHERE id=?",
                (row["scanned_id"],),
            )).fetchone()

    if action == "accept" and scanner_row and scanned_row:
        scanner = parse_json_fields(row_to_dict(scanner_row), ["interests", "top_values"])
        scanned = parse_json_fields(row_to_dict(scanned_row), ["interests", "top_values"])
        icebreaker = get_connection_icebreaker(scanner, scanned)
        await ws_manager.send_player(row["scanner_id"], {
            "type": "connection_accepted",
            "data": {
                "connection_id": conn_id,
                "name": scanned_row["display_name"],
                "icebreaker": icebreaker,
            },
        })
    return {"ok": True}


# ── Contact Exchange ──────────────────────────────────────────────────────────

@app.post("/api/contact-exchange")
async def share_contact(body: ContactExchangeCreate):
    exchange_id = new_id()
    ts = now_iso()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO contact_exchanges
               (id, session_id, from_id, to_id, contact_data, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                exchange_id, body.session_id, body.from_id, body.to_id,
                json.dumps(body.contact_data.model_dump(exclude_none=True)), ts,
            ),
        )
        await db.commit()

    # Get sender name
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT display_name FROM checkins WHERE id=?", (body.from_id,)
        )).fetchone()
    sender_name = row["display_name"] if row else "Someone"

    await ws_manager.send_player(body.to_id, {
        "type": "contact_received",
        "data": {
            "exchange_id": exchange_id,
            "from_name": sender_name,
            "from_id": body.from_id,
        },
    })
    return {"id": exchange_id}


@app.get("/api/contact-exchange/{exchange_id}")
async def get_contact(exchange_id: str, checkin_id: str):
    """Recipient retrieves the contact details."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM contact_exchanges WHERE id=? AND to_id=?",
            (exchange_id, checkin_id),
        )).fetchone()
        if not row:
            raise HTTPException(404)
        d = row_to_dict(row)
        d["contact_data"] = json.loads(d["contact_data"])
        await db.execute(
            "UPDATE contact_exchanges SET status='viewed' WHERE id=?",
            (exchange_id,),
        )
        await db.commit()
    return d


# ── Host stats ────────────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/stats")
async def get_stats(session_id: str):
    return await _compute_stats(session_id)


@app.get("/api/sessions/{session_id}/attendees")
async def get_attendees(session_id: str):
    """Return all active check-ins for the solar system initial population."""
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, display_name, intent, zone FROM checkins "
            "WHERE session_id=? AND checked_out=0 ORDER BY created_at",
            (session_id,),
        )).fetchall()
    return {"attendees": [dict(r) for r in rows]}


async def _compute_stats(session_id: str) -> dict:
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT intent, zone, group_size FROM checkins WHERE session_id=? AND checked_out=0",
            (session_id,),
        )).fetchall()

        met_count = await (await db.execute(
            """SELECT COUNT(*) FROM checkins
               WHERE session_id=? AND met_by != '[]' AND met_by IS NOT NULL""",
            (session_id,),
        )).fetchone()

        connections_count = await (await db.execute(
            """SELECT COUNT(*) FROM connections
               WHERE session_id=? AND status IN ('accepted','mutual')""",
            (session_id,),
        )).fetchone()

        reports_count = await (await db.execute(
            "SELECT COUNT(*) FROM safety_events WHERE session_id=? AND type='report'",
            (session_id,),
        )).fetchone()

    total = len(rows)
    by_intent: dict[str, int] = {}
    by_zone: dict[str, int] = {}

    for r in rows:
        intent = r["intent"] or "unknown"
        zone = r["zone"] or "unknown"
        by_intent[intent] = by_intent.get(intent, 0) + 1
        by_zone[zone] = by_zone.get(zone, 0) + 1

    return {
        "total": total,
        "by_intent": by_intent,
        "by_zone": by_zone,
        "confirmed_meets": met_count[0] if met_count else 0,
        "connections": connections_count[0] if connections_count else 0,
        "safety_reports": reports_count[0] if reports_count else 0,
    }


async def _broadcast_stats(session_id: str) -> None:
    stats = await _compute_stats(session_id)
    await ws_manager.broadcast_host(session_id, {
        "type": "stats_update",
        "data": stats,
    })


# ── Scan Challenge ────────────────────────────────────────────────────────────

# In-memory store for quiz answers keyed by (challenge_id, scanner_id, scanned_id)
_quiz_answers: dict[tuple, int] = {}

DESIRE_LABELS = [
    "Something casual",
    "Something real",
    "Open to adventure",
    "A bit wild 🔥",
    "Let's see where it goes",
]

FAKE_POOLS = {
    "activities":  ["Quiz night", "Karaoke", "Darts", "Pool", "Board games", "Trivia", "Dancing", "Poker"],
    "values":      ["Adventure", "Loyalty", "Ambition", "Creativity", "Freedom", "Humour", "Honesty", "Security"],
    "interests":   ["Live music", "Travel", "Sport", "Food", "Dogs", "Gaming", "Festivals", "Hiking", "Film"],
    "intent":      ["Social", "Up for activities", "Looking for a date", "Here to chill"],
}


def _make_quiz_question(them: dict) -> dict | None:
    """Build a multiple-choice question from the scanned person's profile."""
    import random

    if them.get("activities"):
        correct = random.choice(them["activities"])
        pool = [x for x in FAKE_POOLS["activities"] if x.lower() != correct.lower()]
        options = [correct] + random.sample(pool, min(2, len(pool)))
        random.shuffle(options)
        return {
            "question": f"What is {them['display_name']} here to do tonight?",
            "options": options,
            "correct_index": options.index(correct),
        }

    vals = them.get("values") or them.get("top_values") or []
    if vals:
        correct = vals[0].capitalize()
        pool = [x for x in FAKE_POOLS["values"] if x.lower() != correct.lower()]
        options = [correct] + random.sample(pool, min(2, len(pool)))
        random.shuffle(options)
        return {
            "question": f"Which of these does {them['display_name']} value most?",
            "options": options,
            "correct_index": options.index(correct),
        }

    if them.get("interests"):
        correct = them["interests"][0].capitalize()
        pool = [x for x in FAKE_POOLS["interests"] if x.lower() != correct.lower()]
        options = [correct] + random.sample(pool, min(2, len(pool)))
        random.shuffle(options)
        return {
            "question": f"What is {them['display_name']} into?",
            "options": options,
            "correct_index": options.index(correct),
        }

    return None


@app.post("/api/challenges")
async def start_challenge(body: ChallengeCreate):
    challenge_id = new_id()
    ts = time.time()
    ends_at = ts + body.duration_seconds
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM sessions WHERE id=? AND active=1", (body.session_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        # Deactivate any existing challenge
        await db.execute(
            "UPDATE scan_challenges SET active=0 WHERE session_id=? AND active=1",
            (body.session_id,),
        )
        await db.execute(
            """INSERT INTO scan_challenges (id, session_id, duration_seconds, prize_text, started_at, active)
               VALUES (?,?,?,?,?,1)""",
            (challenge_id, body.session_id, body.duration_seconds, body.prize_text, ts),
        )
        await db.commit()

    await ws_manager.broadcast_session_players(body.session_id, {
        "type": "challenge_started",
        "data": {
            "challenge_id": challenge_id,
            "duration_seconds": body.duration_seconds,
            "ends_at": ends_at,
            "prize_text": body.prize_text,
        },
    })
    await ws_manager.broadcast_host(body.session_id, {
        "type": "challenge_started",
        "data": {
            "challenge_id": challenge_id,
            "duration_seconds": body.duration_seconds,
            "ends_at": ends_at,
            "prize_text": body.prize_text,
        },
    })

    asyncio.create_task(_auto_end_challenge(challenge_id, body.session_id, body.duration_seconds))
    return {"id": challenge_id, "ends_at": ends_at}


async def _auto_end_challenge(challenge_id: str, session_id: str, duration: int):
    await asyncio.sleep(duration)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT active FROM scan_challenges WHERE id=?", (challenge_id,)
        )).fetchone()
        if not row or not row["active"]:
            return
        await db.execute(
            "UPDATE scan_challenges SET active=0, ended_at=? WHERE id=?",
            (time.time(), challenge_id),
        )
        await db.commit()
        top = await (await db.execute(
            "SELECT display_name, points FROM challenge_scores WHERE challenge_id=? ORDER BY points DESC LIMIT 1",
            (challenge_id,),
        )).fetchone()
    winner = {"display_name": top["display_name"], "points": top["points"]} if top else None
    await ws_manager.broadcast_session_players(session_id, {
        "type": "challenge_ended",
        "data": {"winner": winner},
    })
    await ws_manager.broadcast_host(session_id, {
        "type": "challenge_ended",
        "data": {"winner": winner},
    })


@app.get("/api/challenges/{session_id}/current")
async def get_current_challenge(session_id: str):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM scan_challenges WHERE session_id=? AND active=1 ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        )).fetchone()
    if not row:
        return {"challenge": None}
    d = dict(row)
    d["ends_at"] = d["started_at"] + d["duration_seconds"]
    return {"challenge": d}


@app.get("/api/connect/{checkin_id}/quiz")
async def get_quiz_question(checkin_id: str, s: str, scanner_id: Optional[str] = None):
    """Return a quiz question about the scanned person."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM checkins WHERE id=? AND session_id=? AND checked_out=0",
            (checkin_id, s),
        )).fetchone()
        if not row:
            raise HTTPException(404)
        # Get active challenge
        ch_row = await (await db.execute(
            "SELECT id FROM scan_challenges WHERE session_id=? AND active=1 LIMIT 1", (s,)
        )).fetchone()
    if not ch_row:
        return {"question": None}
    them = _parse_checkin(dict(row))
    q = _make_quiz_question(them)
    if not q:
        return {"question": None}
    # Store correct answer in memory
    if scanner_id:
        key = (ch_row["id"], scanner_id, checkin_id)
        _quiz_answers[key] = q["correct_index"]
    return {
        "question": q["question"],
        "options": q["options"],
        "challenge_id": ch_row["id"],
    }


@app.post("/api/challenge-answers")
async def submit_challenge_answer(body: ChallengeAnswerCreate):
    key = (body.challenge_id, body.scanner_id, body.scanned_id)
    correct_index = _quiz_answers.get(key)
    is_correct = correct_index is not None and body.answer_index == correct_index
    points_earned = 1 if is_correct else 0

    # Clean up stored answer
    _quiz_answers.pop(key, None)

    async with get_db() as db:
        ch_row = await (await db.execute(
            "SELECT session_id FROM scan_challenges WHERE id=? AND active=1",
            (body.challenge_id,),
        )).fetchone()
        if not ch_row:
            return {"correct": False, "points": 0}
        session_id = ch_row["session_id"]

        # Get scanner's display name
        name_row = await (await db.execute(
            "SELECT display_name FROM checkins WHERE id=?", (body.scanner_id,)
        )).fetchone()
        display_name = name_row["display_name"] if name_row else "Unknown"

        # Upsert score
        existing = await (await db.execute(
            "SELECT id, scans, correct, points FROM challenge_scores WHERE challenge_id=? AND checkin_id=?",
            (body.challenge_id, body.scanner_id),
        )).fetchone()
        if existing:
            await db.execute(
                "UPDATE challenge_scores SET scans=scans+1, correct=correct+?, points=points+? WHERE id=?",
                (1 if is_correct else 0, points_earned, existing["id"]),
            )
        else:
            await db.execute(
                """INSERT INTO challenge_scores (id, challenge_id, checkin_id, display_name, scans, correct, points)
                   VALUES (?,?,?,?,1,?,?)""",
                (new_id(), body.challenge_id, body.scanner_id, display_name,
                 1 if is_correct else 0, points_earned),
            )
        await db.commit()

        top = await (await db.execute(
            """SELECT display_name, scans, points FROM challenge_scores
               WHERE challenge_id=? ORDER BY points DESC, scans DESC LIMIT 5""",
            (body.challenge_id,),
        )).fetchall()

    leaderboard = [dict(r) for r in top]
    await ws_manager.broadcast_host(session_id, {
        "type": "leaderboard_update",
        "data": {"top": leaderboard},
    })
    await ws_manager.broadcast_session_players(session_id, {
        "type": "leaderboard_update",
        "data": {"top": leaderboard},
    })
    return {"correct": is_correct, "points": points_earned, "leaderboard": leaderboard}


@app.get("/api/challenges/{challenge_id}/leaderboard")
async def get_leaderboard(challenge_id: str):
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT display_name, scans, correct, points FROM challenge_scores WHERE challenge_id=? ORDER BY points DESC, scans DESC LIMIT 10",
            (challenge_id,),
        )).fetchall()
    return {"leaderboard": [dict(r) for r in rows]}


# ── Speed Dating ──────────────────────────────────────────────────────────────

@app.post("/api/speed-dating/start")
async def start_speed_dating(body: SpeedDatingStart):
    sd_id = new_id()
    ts = time.time()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id FROM sessions WHERE id=? AND active=1", (body.session_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        await db.execute(
            "UPDATE speed_dating_sessions SET active=0 WHERE session_id=? AND active=1",
            (body.session_id,),
        )
        await db.execute(
            """INSERT INTO speed_dating_sessions (id, session_id, round_duration_seconds, started_at, active)
               VALUES (?,?,?,?,1)""",
            (sd_id, body.session_id, body.round_duration_seconds, ts),
        )
        await db.commit()
    await ws_manager.broadcast_session_players(body.session_id, {
        "type": "speed_dating_started",
        "data": {"duration_seconds": body.round_duration_seconds},
    })
    await ws_manager.broadcast_host(body.session_id, {
        "type": "speed_dating_started",
        "data": {"duration_seconds": body.round_duration_seconds, "sd_id": sd_id},
    })
    return {"id": sd_id}


@app.post("/api/speed-dating/end")
async def end_speed_dating(session_id: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE speed_dating_sessions SET active=0, ended_at=? WHERE session_id=? AND active=1",
            (time.time(), session_id),
        )
        await db.commit()
    await ws_manager.broadcast_session_players(session_id, {
        "type": "speed_dating_ended", "data": {},
    })
    await ws_manager.broadcast_host(session_id, {
        "type": "speed_dating_ended", "data": {},
    })
    return {"ok": True}


@app.get("/api/speed-dating/{session_id}/active")
async def get_active_speed_dating(session_id: str):
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM speed_dating_sessions WHERE session_id=? AND active=1 ORDER BY started_at DESC LIMIT 1",
            (session_id,),
        )).fetchone()
    if not row:
        return {"active": None}
    return {"active": dict(row)}


@app.post("/api/speed-dating/react")
async def speed_dating_react(body: SpeedDatingReact):
    """Record a reaction. Mutual thumbs-up → silent connection."""
    if not body.thumbs_up:
        return {"ok": True, "connection_made": False}

    # Check if the other person also gave thumbs up
    async with get_db() as db:
        # Look for reverse thumbs-up stored as a pending speed-dating connection
        existing = await (await db.execute(
            """SELECT id FROM connections
               WHERE session_id=? AND scanner_id=? AND scanned_id=? AND status='speed_dating_like'""",
            (body.session_id, body.to_id, body.from_id),
        )).fetchone()

        if existing:
            # Mutual! Create real connection
            conn_id = new_id()
            ts = now_iso()
            await db.execute(
                """INSERT INTO connections (id, session_id, scanner_id, scanned_id, status, is_mutual, created_at)
                   VALUES (?,?,?,?,?,1,?)""",
                (conn_id, body.session_id, body.from_id, body.to_id, "mutual", ts),
            )
            await db.execute(
                "UPDATE connections SET status='mutual', is_mutual=1 WHERE id=?",
                (existing["id"],),
            )
            await db.commit()
            # Notify both
            for pid, oid in [(body.from_id, body.to_id), (body.to_id, body.from_id)]:
                name_row = await (await db.execute(
                    "SELECT display_name FROM checkins WHERE id=?", (oid,)
                )).fetchone()
                await ws_manager.send_player(pid, {
                    "type": "speed_dating_match",
                    "data": {"name": name_row["display_name"] if name_row else "Someone"},
                })
            return {"ok": True, "connection_made": True}
        else:
            # Store as pending like
            conn_id = new_id()
            ts = now_iso()
            await db.execute(
                """INSERT INTO connections (id, session_id, scanner_id, scanned_id, status, is_mutual, created_at)
                   VALUES (?,?,?,?,?,0,?)""",
                (conn_id, body.session_id, body.from_id, body.to_id, "speed_dating_like", ts),
            )
            await db.commit()

    return {"ok": True, "connection_made": False}


# ── Host safety actions ───────────────────────────────────────────────────────

@app.post("/api/sessions/{session_id}/remove/{checkin_id}")
async def host_remove_user(session_id: str, checkin_id: str):
    async with get_db() as db:
        await db.execute(
            "UPDATE checkins SET checked_out=1 WHERE id=? AND session_id=?",
            (checkin_id, session_id),
        )
        await db.commit()
    await ws_manager.send_player(checkin_id, {
        "type": "removed_by_host",
        "data": {"message": "You have been removed from this session."},
    })
    ws_manager.unregister_player(checkin_id)
    await _broadcast_stats(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/reports")
async def get_reports(session_id: str):
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT * FROM safety_events WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        )).fetchall()
    return {"reports": [row_to_dict(r) for r in rows]}


# ── WebSocket — Player presence ───────────────────────────────────────────────

@app.websocket("/ws/presence/{session_id}/{checkin_id}")
async def ws_player(ws: WebSocket, session_id: str, checkin_id: str):
    await ws.accept()
    ws_manager.register_player(checkin_id, session_id, ws)

    # Send initial state
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT display_name FROM checkins WHERE id=?", (checkin_id,)
        )).fetchone()
    name = row["display_name"] if row else checkin_id

    await ws.send_text(json.dumps({
        "type": "connected",
        "data": {"checkin_id": checkin_id, "display_name": name, "session_id": session_id},
    }))

    try:
        while True:
            raw = await ws.receive_text()
            ws_manager.touch(checkin_id)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "auto_checkout":
                # Triggered by inactivity watchdog
                async with get_db() as db:
                    await db.execute(
                        "UPDATE checkins SET checked_out=1 WHERE id=?", (checkin_id,)
                    )
                    await db.commit()
                ws_manager.unregister_player(checkin_id)
                await _broadcast_stats(session_id)
                break

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.unregister_player(checkin_id)


# ── WebSocket — Host dashboard ────────────────────────────────────────────────

@app.websocket("/ws/host/{session_id}")
async def ws_host(ws: WebSocket, session_id: str):
    await ws.accept()
    ws_manager.register_host(session_id, ws)

    # Send initial stats
    stats = await _compute_stats(session_id)
    await ws.send_text(json.dumps({"type": "connected", "data": {"session_id": session_id}}))
    await ws.send_text(json.dumps({"type": "stats_update", "data": stats}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.unregister_host(session_id, ws)


# ── Helper ────────────────────────────────────────────────────────────────────

def _parse_checkin(d: dict) -> dict:
    return parse_json_fields(d, [
        "interests", "interest_intensity", "top_values",
        "activities", "met_by", "personality", "intents", "desires",
    ])


# ── Static mount (must be last) ───────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
