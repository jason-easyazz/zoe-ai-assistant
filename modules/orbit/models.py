from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


# ── Session ──────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    venue_name: str
    event_name: Optional[str] = None


class Session(BaseModel):
    id: str
    venue_name: str
    event_name: Optional[str]
    zone_names: dict[str, str] = Field(default_factory=dict)
    created_at: str
    active: bool = True


# ── Check-in ─────────────────────────────────────────────────────────────────

class Personality(BaseModel):
    O: float = 3.0  # Openness
    C: float = 3.0  # Conscientiousness
    E: float = 3.0  # Extraversion
    A: float = 3.0  # Agreeableness
    N: float = 3.0  # Neuroticism


class CheckInCreate(BaseModel):
    session_id: str
    display_name: str
    intent: str = "social"                             # legacy single intent (kept for compat)
    intents: list[str] = Field(default_factory=list)  # public multi-select (Tier 1)
    desires: list[str] = Field(default_factory=list)  # private multi-select (Tier 2, never public)
    visibility: str = "public"                        # public | low-key
    interests: list[str] = Field(default_factory=list)
    interest_intensity: dict[str, int] = Field(default_factory=dict)  # tag -> 1|2
    values: list[str] = Field(default_factory=list)   # top 3
    personality: Optional[Personality] = None
    activities: list[str] = Field(default_factory=list)
    group_size: int = 1
    zone: Optional[str] = None


class CheckIn(CheckInCreate):
    id: str
    checked_out: bool = False
    created_at: str
    last_seen: str


class CheckInPublic(BaseModel):
    """Subset of CheckIn safe to return to other users. NEVER includes desires."""
    id: str
    display_name: str
    intent: str
    intents: list[str] = Field(default_factory=list)
    interests: list[str]
    interest_intensity: dict[str, int]
    values: list[str]
    activities: list[str]
    group_size: int
    zone: Optional[str]


# ── Challenge / Speed Dating ──────────────────────────────────────────────────

class ChallengeCreate(BaseModel):
    session_id: str
    duration_seconds: int
    prize_text: Optional[str] = None


class ChallengeAnswerCreate(BaseModel):
    challenge_id: str
    scanner_id: str
    scanned_id: str
    answer_index: int


class SpeedDatingStart(BaseModel):
    session_id: str
    round_duration_seconds: int


class SpeedDatingReact(BaseModel):
    session_id: str
    from_id: str
    to_id: str
    thumbs_up: bool


# ── Match ─────────────────────────────────────────────────────────────────────

class Match(BaseModel):
    id: str
    session_id: str
    checkin_a: str
    checkin_b: str
    score: float
    icebreaker: str
    status: str = "pending"   # pending | met | ignored
    created_at: str


class MatchCard(BaseModel):
    """What a player sees on their discover screen."""
    match_id: str
    person: CheckInPublic
    score: float
    icebreaker: str
    met_by_me: bool = False
    met_by_them: bool = False


# ── Interaction ───────────────────────────────────────────────────────────────

class InteractionCreate(BaseModel):
    session_id: str
    sender_id: str
    receiver_id: str
    type: str          # say_hey | join_drink | join_activity
    payload: Optional[dict] = None


class Interaction(InteractionCreate):
    id: str
    status: str = "sent"   # sent | seen | accepted | declined
    created_at: str


# ── Connection (QR scan) ──────────────────────────────────────────────────────

class ConnectionCreate(BaseModel):
    session_id: str
    scanner_id: str
    scanned_id: str


class Connection(ConnectionCreate):
    id: str
    status: str = "pending"   # pending | accepted | declined | mutual
    is_mutual: bool = False
    created_at: str
    responded_at: Optional[str] = None


# ── Contact Exchange ──────────────────────────────────────────────────────────

class ContactData(BaseModel):
    phone: Optional[str] = None
    instagram: Optional[str] = None
    whatsapp: Optional[str] = None
    other: Optional[str] = None


class ContactExchangeCreate(BaseModel):
    session_id: str
    from_id: str
    to_id: str
    contact_data: ContactData


class ContactExchange(ContactExchangeCreate):
    id: str
    status: str = "pending"   # pending | viewed
    created_at: str


# ── Safety ────────────────────────────────────────────────────────────────────

class SafetyEventCreate(BaseModel):
    reporter_id: str
    reported_id: str
    type: str          # report | block
    reason: Optional[str] = None


class SafetyEvent(SafetyEventCreate):
    id: str
    session_id: str
    created_at: str


# ── WebSocket messages ────────────────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str
    data: Optional[dict] = None
