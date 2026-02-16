from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Depends
import os
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import sqlite3

from auth_integration import AuthenticatedSession, validate_session
from .notifications import send_notification, NotificationPriority

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


class ProactiveSettings(BaseModel):
    relationship_days_overdue: int = 14
    max_suggestions: int = 5


DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


def _fetch_people(conn, user_id: str) -> List[Dict]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, name, relationship, last_interaction, importance
            FROM people
            WHERE user_id = ?
            ORDER BY COALESCE(importance, 0) DESC, name ASC
            """,
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except Exception:
                return None


@router.post("/relationship-check")
async def relationship_check(
    background: BackgroundTasks,
    session: AuthenticatedSession = Depends(validate_session),
    days_overdue: int = Query(14, ge=1, le=180),
    max_suggestions: int = Query(5, ge=1, le=20),
):
    """Scan people for overdue contacts and emit suggestions as notifications."""
    try:
        conn = sqlite3.connect(DB_PATH)
        people = _fetch_people(conn, user_id)
        conn.close()
        if not people:
            return {"suggestions": 0}

        now = datetime.now()
        cutoff = now - timedelta(days=days_overdue)
        suggestions = []
        for p in people:
            last = _parse_date(p.get("last_interaction"))
            if last is None or last < cutoff:
                suggestions.append(p)
            if len(suggestions) >= max_suggestions:
                break

        # send notifications in background for each suggestion
        for p in suggestions:
            title = "Reconnect Opportunity"
            message = f"You haven't talked to {p.get('name','this person')} in {days_overdue}+ days. Add a reminder?"
            meta = {"suggestion_type": "relationship_check", "person_id": p.get("id"), "days_overdue": days_overdue}
            background.add_task(
                send_notification,
                title,
                message,
                NotificationPriority.IMPORTANT,
                None,
                True,
                meta,
            )

        return {"suggestions": len(suggestions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





